from __future__ import annotations

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from .duty_parser import (
    DEFAULT_TEMPLATE,
    ROW_NAMES,
    build_schedule_boxes,
    detect_table_box,
    guess_month_and_year,
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
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("rows 필드가 배열이 아니에요.")

    parsed_by_name: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        if name not in ROW_NAMES or name in parsed_by_name:
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

        parsed_by_name[name] = {
            "rowIndex": ROW_NAMES.index(name) + 1,
            "name": name,
            "recognizedDays": recognized,
            "shifts": normalized_shifts,
            "rawTokens": raw_tokens,
            "cellBoxes": [],
        }

    normalized = []
    for index, name in enumerate(ROW_NAMES, start=1):
        item = parsed_by_name.get(name)
        if item is None:
            item = {
                "rowIndex": index,
                "name": name,
                "recognizedDays": 0,
                "shifts": [None] * 30,
                "rawTokens": [None] * 30,
                "cellBoxes": [],
            }
        normalized.append(item)

    if row_index is not None:
        normalized = [item for item in normalized if item["rowIndex"] == row_index]
    return normalized


def build_prompt(year: int, month: int) -> str:
    names_blob = "\n".join(f"{i+1}. {name}" for i, name in enumerate(ROW_NAMES))
    return f"""
You are reading a Korean hospital nurse duty roster table photographed with a phone camera.

Context:
- Year: {year}, Month: {month}
- The table header row shows day numbers (1~30) and weekday abbreviations (월화수목금토일).
- Below the header are nurse rows. Each nurse row has: row number | name | 성별 | 30 day cells.
- The duty cells contain exactly one of: D, E, N, S, Y, OFF
- "off" written in lowercase in the original image should be returned as OFF.

Your task:
- Read every cell for ALL 16 nurses listed below.
- Use the header row's day numbers to align each cell to the correct day (1~30).
- If a cell is illegible, return null for that specific cell only (do not null out the whole row).
- Do not guess or infer from neighboring cells or scheduling patterns.

Nurses (in this exact order):
{names_blob}

Return ONLY valid JSON with this exact shape (no commentary, no markdown):
{{
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
- shifts array must have exactly 30 items (days 1 through 30).
- Valid shift values: D, E, N, S, Y, OFF — always uppercase, never null unless truly illegible.
- rowIndex is 1-based matching the order above.
- Do not skip any nurse row.
""".strip()


def crop_row_strip(rectified: Image.Image, row: dict) -> Image.Image:
    left = 0
    right = rectified.width
    top = max(0, min(box[1] for box in row["cellBoxes"]) - 12)
    bottom = min(rectified.height, max(box[3] for box in row["cellBoxes"]) + 12)
    return rectified.crop((left, top, right, bottom))


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
    """Crop and resize the full table to a reasonable size for Claude."""
    cropped = image.crop(table_box)
    max_width = 2400
    if cropped.width > max_width:
        scale = max_width / cropped.width
        new_size = (max_width, int(cropped.height * scale))
        cropped = cropped.resize(new_size, Image.Resampling.LANCZOS)
    return cropped


def parse_duty_image_with_claude(
    image_bytes: bytes,
    filename: str,
    row_index: int | None = None,
    source_format: str = "image",
    include_debug: bool = False,
) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    import io

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    year, month = guess_month_and_year(filename)
    table_box = detect_table_box(image)
    rectified = rectify_table(image, table_box, DEFAULT_TEMPLATE.table_size)

    table_crop = crop_table_for_claude(image, table_box)

    ssl_context = build_ssl_context()

    payload = {
        "model": DEFAULT_ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_prompt(year, month)},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": pil_to_base64(table_crop, "JPEG"),
                        },
                    },
                ],
            }
        ],
    }

    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180, context=ssl_context) as response:
            response_json = json.loads(response.read().decode("utf-8"))
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

    response_text = extract_text_blocks(response_json)
    parsed_payload = extract_json_payload(response_text)
    rows = normalize_rows(parsed_payload)

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
        },
    }
    if include_debug:
        rows_for_overlay = build_schedule_boxes(DEFAULT_TEMPLATE)
        result["debugImage"] = make_debug_overlay(image, rectified, table_box, rows_for_overlay, rows)
    return result
