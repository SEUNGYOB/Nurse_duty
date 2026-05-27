from __future__ import annotations

import io
from bisect import bisect_right

from PIL import Image

from .duty_parser import (
    DEFAULT_TEMPLATE,
    auto_orient_duty_image,
    build_schedule_boxes_from_bounds,
    detect_schedule_line_bounds,
    detect_table_box,
    guess_month_and_year,
    make_debug_overlay,
    load_image_rgb,
    normalize_shift,
    ocr_shift_cell,
    rectify_table,
    classify_shift_cell,
    crop_ink_focus,
)


def _load_vision_client():
    try:
        from google.cloud import vision
    except ImportError as error:
        raise RuntimeError(
            "google-cloud-vision is not installed. Run `pip install -r requirements.txt`."
        ) from error

    return vision.ImageAnnotatorClient(), vision


def _image_to_bytes(image: Image.Image, image_format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format=image_format)
    return buffer.getvalue()


def _extract_words(image: Image.Image) -> list[dict]:
    client, vision = _load_vision_client()
    request_image = vision.Image(content=_image_to_bytes(image))
    response = client.document_text_detection(image=request_image)

    if response.error and response.error.message:
        raise RuntimeError(f"Google Vision request failed: {response.error.message}")

    document = response.full_text_annotation
    words: list[dict] = []
    for page in document.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    text = "".join(symbol.text for symbol in word.symbols).strip()
                    if not text:
                        continue
                    xs = [vertex.x or 0 for vertex in word.bounding_box.vertices]
                    ys = [vertex.y or 0 for vertex in word.bounding_box.vertices]
                    words.append(
                        {
                            "text": text,
                            "left": min(xs),
                            "right": max(xs),
                            "top": min(ys),
                            "bottom": max(ys),
                        }
                    )
    return words


def _column_index_from_x(center_x: float, x_bounds: list[int]) -> int:
    if len(x_bounds) < 2:
        return 0
    index = bisect_right(x_bounds, center_x) - 1
    return max(0, min(len(x_bounds) - 2, index))


def _crop_row_strip(rectified: Image.Image, row: dict) -> Image.Image:
    left = 0
    right = rectified.width
    top = max(0, min(box[1] for box in row["cellBoxes"]) - 6)
    bottom = min(rectified.height, max(box[3] for box in row["cellBoxes"]) + 6)
    return rectified.crop((left, top, right, bottom))


def _parse_cell_with_google(cell_image: Image.Image) -> tuple[str | None, str]:
    words = _extract_words(crop_ink_focus(cell_image, pad_ratio_x=0.12, pad_ratio_y=0.12))
    if not words:
        return None, ""

    candidates = [item["text"] for item in words]
    for candidate in candidates:
        normalized = normalize_shift(candidate)
        if normalized:
            return normalized, candidate

    merged = " ".join(candidates).strip()
    return normalize_shift(merged), merged


def _parse_row_strip_with_google(
    row_image: Image.Image,
    x_bounds: list[int],
    column_count: int,
) -> tuple[list[str | None], list[str]]:
    shifts: list[str | None] = [None] * column_count
    raw_tokens: list[str] = [""] * column_count

    words = _extract_words(row_image)
    if not words:
        return shifts, raw_tokens

    for item in words:
        normalized = normalize_shift(item["text"])
        if not normalized:
            continue

        center_x = (float(item["left"]) + float(item["right"])) / 2
        day_index = _column_index_from_x(center_x, x_bounds)

        if shifts[day_index] is None:
            shifts[day_index] = normalized
            raw_tokens[day_index] = item["text"]

    return shifts, raw_tokens


def parse_duty_image_with_google(
    image_bytes: bytes,
    filename: str,
    row_index: int | None = None,
    include_debug: bool = False,
) -> dict:
    import io

    image = load_image_rgb(image_bytes)
    image, rotation_applied = auto_orient_duty_image(image)
    year, month = guess_month_and_year(filename)

    table_box = detect_table_box(image)
    rectified = rectify_table(image, table_box, DEFAULT_TEMPLATE.table_size)
    _, y_bounds, x_bounds = detect_schedule_line_bounds(rectified, DEFAULT_TEMPLATE)
    rows = build_schedule_boxes_from_bounds(DEFAULT_TEMPLATE, y_bounds, x_bounds)
    if row_index is not None:
        rows = [row for row in rows if row["rowIndex"] == row_index]

    parsed_rows = []
    for row in rows:
        shifts: list[str | None] = [None] * DEFAULT_TEMPLATE.column_count
        raw_tokens: list[str] = [""] * DEFAULT_TEMPLATE.column_count

        # First pass: classify each cell independently from its center.
        for index, box in enumerate(row["cellBoxes"]):
            cell = rectified.crop(box)
            shift, raw, score, margin = classify_shift_cell(cell)
            if not shift:
                continue
            shifts[index] = shift
            raw_tokens[index] = raw or shift

        # Second pass: use Google Vision on ambiguous cells only.
        for index, box in enumerate(row["cellBoxes"]):
            if shifts[index] is not None:
                continue
            cell = rectified.crop(box)
            shift, raw = _parse_cell_with_google(cell)
            if not shift:
                continue
            shifts[index] = shift
            raw_tokens[index] = raw

        row_strip = _crop_row_strip(rectified, row)
        strip_shifts, strip_tokens = _parse_row_strip_with_google(row_strip, x_bounds, DEFAULT_TEMPLATE.column_count)

        # Third pass: fill blanks from row-strip OCR when the cell crop was too weak.
        for index in range(DEFAULT_TEMPLATE.column_count):
            if shifts[index] is not None or strip_shifts[index] is None:
                continue
            shifts[index] = strip_shifts[index]
            raw_tokens[index] = strip_tokens[index]

        recognized = sum(1 for shift in shifts if shift)

        parsed_rows.append(
            {
                "rowIndex": row["rowIndex"],
                "name": row["name"],
                "recognizedDays": recognized,
                "shifts": shifts,
                "rawTokens": raw_tokens,
                "cellBoxes": row["cellBoxes"],
            }
        )

    result = {
        "year": year,
        "month": month,
        "rows": parsed_rows,
        "template": {
            "tableSize": list(DEFAULT_TEMPLATE.table_size),
            "rowSlotCount": DEFAULT_TEMPLATE.row_slot_count,
            "columnCount": DEFAULT_TEMPLATE.column_count,
            "detectedTableBox": list(table_box),
            "parser": "google",
            "rotationApplied": rotation_applied,
        },
    }
    if include_debug:
        result["debugImage"] = make_debug_overlay(image, rectified, table_box, rows, parsed_rows)
    return result
