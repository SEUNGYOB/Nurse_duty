from __future__ import annotations

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

from .duty_parser import (
    DEFAULT_TEMPLATE,
    ROW_NAMES,
    auto_orient_duty_image,
    build_schedule_boxes_from_bounds,
    build_schedule_boxes,
    detect_schedule_line_bounds,
    detect_table_box,
    guess_month_and_year,
    get_schedule_geometry,
    load_image_rgb,
    make_debug_overlay,
    rectify_table,
)


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

SHIFT_CANONICAL = {"D": "D", "E": "E", "N": "N", "S": "S", "Y": "Y", "OFF": "off"}
SHIFT_ALIASES = {
    "D": "D",
    "DAY": "D",
    "E": "E",
    "EVENING": "E",
    "N": "N",
    "NIGHT": "N",
    "S": "S",
    "SWING": "S",
    "Y": "Y",
    "TRAINING": "Y",
    "OFF": "OFF",
    "OF": "OFF",
    "0FF": "OFF",
    "OOF": "OFF",
}


def image_media_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def pil_to_base64(image: Image.Image, image_format: str = "JPEG") -> str:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format=image_format, quality=92)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def pil_to_pdf_base64(image: Image.Image) -> str:
    import io

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PDF", resolution=300.0)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def build_row_content_block(row_image: Image.Image, filename: str, source_format: str) -> dict:
    if source_format == "pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pil_to_pdf_base64(row_image),
            },
        }

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image_media_type(filename),
            "data": pil_to_base64(row_image, "JPEG"),
        },
    }


def extract_text_blocks(response_json: dict) -> str:
    blocks = response_json.get("content", [])
    texts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "\n".join(texts).strip()


def extract_json_payload(text: str) -> dict:
    fenced = re.search(r"```json\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Claude 응답에서 JSON을 찾지 못했어요.")
    return json.loads(text[start : end + 1])


def normalize_shift_code(value: str | None) -> tuple[str | None, str]:
    raw = str(value or "").strip().upper()
    token = "".join(ch for ch in raw if ch.isalpha() or ch.isdigit())
    canonical = SHIFT_ALIASES.get(token)
    if canonical is None:
        return None, raw
    return SHIFT_CANONICAL[canonical], canonical


def normalize_rows(payload: dict, row_index: int | None = None) -> list[dict]:
    """rowIndex를 유일한 식별자로 사용한다. name은 표시용 메타데이터."""
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("rows 필드가 배열이 아니에요.")

    parsed_by_index: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("rowIndex", 0))
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= 16) or idx in parsed_by_index:
            continue
        shifts = row.get("shifts", [])
        if not isinstance(shifts, list):
            continue

        trimmed = shifts[:30]
        if len(trimmed) < 30:
            trimmed = trimmed + [None] * (30 - len(trimmed))

        normalized_shifts = []
        raw_tokens = []
        recognized = 0
        for value in trimmed:
            shift, raw = normalize_shift_code(value)
            if shift:
                recognized += 1
            normalized_shifts.append(shift)
            raw_tokens.append(raw)

        # name은 이미지에서 Claude가 읽은 값으로 저장 (표시용)
        name = str(row.get("name", "")).strip()
        parsed_by_index[idx] = {
            "rowIndex": idx,
            "name": name,
            "recognizedDays": recognized,
            "shifts": normalized_shifts,
            "rawTokens": raw_tokens,
            "cellBoxes": [],
        }

    normalized = []
    for idx in range(1, 17):
        item = parsed_by_index.get(idx)
        if item is None:
            item = {
                "rowIndex": idx,
                "name": "",
                "recognizedDays": 0,
                "shifts": [None] * 30,
                "rawTokens": [None] * 30,
                "cellBoxes": [],
            }
        normalized.append(item)

    if row_index is not None:
        normalized = [item for item in normalized if item["rowIndex"] == row_index]
    return normalized


def build_prompt(year: int, month: int, with_row_guides: bool = False) -> str:
    names_blob = "\n".join(f"{i+1}. {name}" for i, name in enumerate(ROW_NAMES))
    image_note = (
        "Two images are attached:\n"
        "  Image 1: original photo (use this to read the title for year/month).\n"
        "  Image 2: same table rectified, with blue horizontal lines drawn at the detected row boundaries.\n"
        "  Use the blue boundary lines in Image 2 to precisely identify which cells belong to each nurse row.\n"
        "  Each band between two consecutive blue lines = one nurse row."
        if with_row_guides else
        "One image is attached: the original duty roster photo."
    )
    return f"""
You are reading a Korean hospital nurse duty roster table photographed with a phone camera.

{image_note}

Context hint (override with what you actually see in the image title):
- Hint year: {year}, Hint month: {month}
- The table header row shows day numbers (1~30) and weekday abbreviations (월화수목금토일).
- Below the header are nurse rows. Each nurse row has: row number | name | 성별 | 30 day cells.
- The duty cells contain exactly one of: D, E, N, S, Y, OFF
- "off" written in lowercase in the original image should be returned as OFF.

Your task:
1. If Image 1 shows a title like "2026년 6월 근무표", read the actual year and month from it.
   Otherwise use the hint values above.
2. Read every cell for ALL 16 nurses listed below.
3. Use the header row's day numbers to align each cell to the correct day (1~30).
4. For each nurse row, trace the 30 day cells from left to right as one continuous spatial sequence.
5. Use neighboring cells only to infer cell boundaries and positions in space, not to infer the duty value.
6. If a cell is illegible, return null for that specific cell only (do not null out the whole row).
7. Do not guess or infer from neighboring cells or scheduling patterns.

Nurses (in this exact order):
{names_blob}

Return ONLY valid JSON with this exact shape (no commentary, no markdown):
{{
  "year": {year},
  "month": {month},
  "rows": [
    {{
      "rowIndex": 1,
      "name": "윤미영",
      "shifts": ["S", "S", "OFF", "S", ... exactly 30 items]
    }},
    ...16 rows total
  ]
}}

Rules:
- year and month must reflect what is actually visible in the image title (not the hint) if readable.
- shifts array must have exactly 30 items (days 1 through 30).
- Valid shift values: D, E, N, S, Y, OFF — always uppercase, never null unless truly illegible.
- rowIndex is 1-based matching the order above.
- Do not skip any nurse row.
- "Continuous" means spatial continuity of adjacent cell positions and borders, not continuity of the work schedule contents.
- Do not shift values left or right to make a row look more regular.
- Do not copy values from the row above or below.
- Internally verify each row by checking that day 1 through day 30 are assigned to 30 distinct adjacent cells in left-to-right order.
    """.strip()


def build_row_refine_prompt(year: int, month: int, row_index: int) -> str:
    return f"""
You are re-reading ONE specific nurse row from a Korean hospital duty roster.

Context:
- Hint year: {year}, Hint month: {month}
- The first image shows the whole table with the target row highlighted in red.
- The second image is a focused crop that includes the day header and the target row.
- Read ONLY the highlighted row (rowIndex {row_index}, counted top-to-bottom from the first data row).

Task:
1. Use the day header to align day 1 through day 30.
2. Read the highlighted row only, from left to right, as one continuous spatial sequence.
3. Use neighboring visual context only to identify the correct row and cell boundaries.
4. Do not infer from scheduling patterns, neighboring rows, or neighboring days.
5. If a cell is illegible, return null for that cell only.

Return ONLY valid JSON:
{{
  "rowIndex": {row_index},
  "shifts": ["D", "E", "OFF", ... exactly 30 items]
}}

Rules:
- shifts must contain exactly 30 items.
- Valid values are D, E, N, S, Y, OFF, or null.
- "Continuous" means spatial continuity of adjacent cells, not continuity of the schedule contents.
- Do not copy values from rows above or below.
- Do not shift values left or right to make the row look more regular.
""".strip()


def crop_row_strip(rectified: Image.Image, row: dict, *, include_header: bool = False) -> Image.Image:
    left = 0
    right = rectified.width
    row_top = min(box[1] for box in row["cellBoxes"])
    row_bottom = max(box[3] for box in row["cellBoxes"])

    if include_header:
        schedule_top = get_schedule_geometry(DEFAULT_TEMPLATE)["top"]
        top = max(0, schedule_top - 12)
    else:
        top = max(0, row_top - 12)

    bottom = min(rectified.height, row_bottom + 12)
    return rectified.crop((left, top, right, bottom))


def build_row_highlight_image(rectified: Image.Image, row: dict) -> Image.Image:
    image = rectified.convert("RGB").copy()
    draw = ImageDraw.Draw(image, "RGBA")
    left = min(box[0] for box in row["cellBoxes"]) - 6
    top = min(box[1] for box in row["cellBoxes"]) - 6
    right = max(box[2] for box in row["cellBoxes"]) + 6
    bottom = max(box[3] for box in row["cellBoxes"]) + 6
    draw.rounded_rectangle((left, top, right, bottom), radius=12, outline=(220, 38, 38, 255), width=8)
    draw.rounded_rectangle((left, top, right, bottom), radius=12, fill=(220, 38, 38, 28))
    return image


def build_ssl_context() -> ssl.SSLContext:
    custom_bundle = os.environ.get("SSL_CERT_FILE")
    if custom_bundle:
        return ssl.create_default_context(cafile=custom_bundle)

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()

    return ssl.create_default_context(cafile=certifi.where())


def crop_table_for_claude(image: Image.Image, table_box: tuple[int, int, int, int]) -> Image.Image:
    """Resize the full image for Claude so the title row ("2026년 6월 근무표") is visible."""
    max_width = 2400
    if image.width > max_width:
        scale = max_width / image.width
        new_size = (max_width, int(image.height * scale))
        return image.resize(new_size, Image.Resampling.LANCZOS)
    return image


def annotate_row_boundaries(
    rectified: Image.Image,
    y_bounds: list[int],
    template: DutySheetTemplate = DEFAULT_TEMPLATE,
    max_width: int = 2400,
) -> Image.Image:
    """감지된 행 경계를 파란 선으로 그린 rectified 이미지를 반환한다.

    좌우 끝은 종이 곡률로 실제 격자선과 어긋날 수 있으므로
    스케줄 영역 x 범위 안쪽 5%만 그린다.
    """
    annotated = rectified.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)
    geometry = get_schedule_geometry(template)
    y_offset = int(geometry["top"])
    margin = int((geometry["right"] - geometry["left"]) * 0.07)
    x_start = int(geometry["left"]) + margin
    x_end = int(geometry["right"]) - margin

    for bound_y in y_bounds:
        y = y_offset + int(bound_y)
        if 0 <= y < annotated.height:
            draw.line([(x_start, y), (x_end, y)], fill=(30, 120, 255), width=3)

    if annotated.width > max_width:
        scale = max_width / annotated.width
        annotated = annotated.resize(
            (max_width, int(annotated.height * scale)), Image.Resampling.LANCZOS
        )
    return annotated


def send_claude_request(payload: dict, ssl_context: ssl.SSLContext) -> dict:
    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180, context=ssl_context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Claude API request failed: {detail}") from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise RuntimeError(
                "Claude API SSL verification failed. Install `certifi` in this project environment "
                "or run the macOS Python certificate installer, then try again."
            ) from error
        raise RuntimeError(f"Claude API connection failed: {error}") from error


def request_row_refinement(
    rectified: Image.Image,
    row_box: dict,
    *,
    year: int,
    month: int,
    filename: str,
    source_format: str,
    ssl_context: ssl.SSLContext,
) -> dict | None:
    prompt = build_row_refine_prompt(year, month, row_box["rowIndex"])
    focus_crop = crop_row_strip(rectified, row_box, include_header=True)
    highlight_image = build_row_highlight_image(rectified, row_box)
    payload = {
        "model": DEFAULT_ANTHROPIC_MODEL,
        "max_tokens": 2048,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    build_row_content_block(highlight_image, filename, source_format),
                    build_row_content_block(focus_crop, filename, source_format),
                ],
            }
        ],
    }
    response_json = send_claude_request(payload, ssl_context)
    payload_json = extract_json_payload(extract_text_blocks(response_json))
    normalized = normalize_rows({"rows": [payload_json]}, row_index=row_box["rowIndex"])
    return (normalized[0] if normalized else None), response_json


def parse_duty_image_with_claude(
    image_bytes: bytes,
    filename: str,
    row_index: int | None = None,
    source_format: str = "image",
    include_debug: bool = False,
    year: int | None = None,
    month: int | None = None,
    refine_row_indices: list[int] | None = None,
    use_row_guides: bool = True,
    guide_image_width: int = 1200,
    on_progress: "Callable[[str], None] | None" = None,
) -> dict:
    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    import io

    _progress("이미지 전처리 중...")
    image = load_image_rgb(image_bytes)
    image, rotation_applied = auto_orient_duty_image(image)
    guessed_year, guessed_month = guess_month_and_year(filename)
    if year is None:
        year = guessed_year
    if month is None:
        month = guessed_month
    table_box = detect_table_box(image)
    rectified = rectify_table(image, table_box, DEFAULT_TEMPLATE.table_size)
    _, y_bounds, x_bounds = detect_schedule_line_bounds(rectified, DEFAULT_TEMPLATE)
    rows_for_overlay = build_schedule_boxes_from_bounds(DEFAULT_TEMPLATE, y_bounds, x_bounds)
    if not rows_for_overlay or not rows_for_overlay[0].get("cellBoxes"):
        rows_for_overlay = build_schedule_boxes(DEFAULT_TEMPLATE)

    table_crop = crop_table_for_claude(image, table_box)
    ssl_context = build_ssl_context()

    content: list[dict] = [{"type": "text", "text": build_prompt(year, month, with_row_guides=use_row_guides)}]
    content.append({
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": pil_to_base64(table_crop, "JPEG")},
    })
    if use_row_guides:
        annotated = annotate_row_boundaries(rectified, y_bounds, DEFAULT_TEMPLATE, max_width=guide_image_width)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": pil_to_base64(annotated, "JPEG")},
        })

    payload = {
        "model": DEFAULT_ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "temperature": 0,
        "messages": [{"role": "user", "content": content}],
    }

    _progress("Claude Vision 분석 중... (약 30초)")
    response_json = send_claude_request(payload, ssl_context)
    usage = response_json.get("usage", {})
    total_input_tokens  = int(usage.get("input_tokens", 0))
    total_output_tokens = int(usage.get("output_tokens", 0))
    response_text = extract_text_blocks(response_json)
    parsed_payload = extract_json_payload(response_text)

    # Claude가 이미지 제목에서 연월을 직접 읽었으면 그 값을 사용
    try:
        detected_year = int(parsed_payload.get("year", year))
        if 2020 <= detected_year <= 2099:
            year = detected_year
    except (TypeError, ValueError):
        pass
    try:
        detected_month = int(parsed_payload.get("month", month))
        if 1 <= detected_month <= 12:
            month = detected_month
    except (TypeError, ValueError):
        pass

    rows = normalize_rows(parsed_payload)

    refine_debug: list[dict] = []
    if refine_row_indices:
        by_index = {row["rowIndex"]: row for row in rows}
        overlay_by_index = {row["rowIndex"]: row for row in rows_for_overlay}
        for target_index in sorted(set(idx for idx in refine_row_indices if 1 <= idx <= 16)):
            row_box = overlay_by_index.get(target_index)
            if not row_box:
                continue
            _progress(f"행 {target_index} 재판독 중...")
            refined, refine_response = request_row_refinement(
                rectified,
                row_box,
                year=year,
                month=month,
                filename=filename,
                source_format=source_format,
                ssl_context=ssl_context,
            )
            if refined is None:
                continue
            refine_usage = refine_response.get("usage", {}) if refine_response else {}
            total_input_tokens  += int(refine_usage.get("input_tokens", 0))
            total_output_tokens += int(refine_usage.get("output_tokens", 0))
            by_index[target_index] = refined
            refine_debug.append(
                {
                    "rowIndex": target_index,
                    "name": refined["name"],
                    "recognizedDays": refined["recognizedDays"],
                }
            )
        rows = [by_index[index] for index in sorted(by_index)]

    _progress("결과 정리 중...")

    if row_index is not None:
        rows = [row for row in rows if row["rowIndex"] == row_index]

    result = {
        "year": year,
        "month": month,
        "rows": rows,
        "template": {
            "tableSize": list(DEFAULT_TEMPLATE.table_size),
            "rowSlotCount": DEFAULT_TEMPLATE.row_slot_count,
            "columnCount": DEFAULT_TEMPLATE.column_count,
            "detectedTableBox": list(table_box),
            "parser": "claude",
            "model": DEFAULT_ANTHROPIC_MODEL,
            "sourceFormat": source_format,
            "rotationApplied": rotation_applied,
            "refinedRows": refine_debug,
            "inputTokens": total_input_tokens,
            "outputTokens": total_output_tokens,
        },
    }
    if include_debug:
        result["debugImage"] = make_debug_overlay(image, rectified, table_box, rows_for_overlay, rows)
    return result
