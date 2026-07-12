from __future__ import annotations

import calendar
import base64
import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
from functools import lru_cache
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
LOGGER = logging.getLogger(__name__)
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"
SHIFT_ALIAS_GROUP_TO_CODE = {
    "day": "D",
    "evening": "E",
    "night": "N",
    "s": "S",
    "annual": "Y",
    "off": "OFF",
}
DEFAULT_SHIFT_ALIAS_CONFIG = {
    "day": ["D", "DAY", "데이"],
    "evening": ["E", "EVE", "EVENING", "이브닝"],
    "night": ["N", "NN", "NIGHT", "나이트"],
    "s": ["S"],
    "annual": ["Y", "A", "ANNUAL", "연차"],
    "off": ["O", "OFF", "휴무"],
}
STATIC_ANTHROPIC_MODELS = (
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "claude-haiku-4-5",
)
FALLBACK_ANTHROPIC_MODELS = (
    DEFAULT_ANTHROPIC_MODEL,
    *STATIC_ANTHROPIC_MODELS,
)


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
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass  # extra data 등 → 아래 fallback으로

    start = text.find("{")
    if start == -1:
        raise ValueError("Claude 응답에서 JSON을 찾지 못했어요.")
    try:
        # 첫 번째 완전한 JSON 객체만 파싱 (extra data 무시)
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        pass

    end = text.rfind("}")
    if end <= start:
        raise ValueError("Claude 응답에서 JSON을 찾지 못했어요.")
    return json.loads(text[start : end + 1])


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def normalize_shift_alias_config(candidate: dict | None) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {
        key: list(values) for key, values in DEFAULT_SHIFT_ALIAS_CONFIG.items()
    }
    if not isinstance(candidate, dict):
        return normalized

    for shift_key in SHIFT_ALIAS_GROUP_TO_CODE:
        values = candidate.get(shift_key)
        if not isinstance(values, list):
            continue
        merged: list[str] = []
        seen: set[str] = set()
        for token in [*normalized.get(shift_key, []), *values]:
            raw = str(token or "").strip().upper()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            merged.append(raw)
        normalized[shift_key] = merged
    return normalized


def build_shift_lookup(shift_aliases: dict | None = None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    normalized = normalize_shift_alias_config(shift_aliases)
    for shift_key, output_code in SHIFT_ALIAS_GROUP_TO_CODE.items():
        canonical_output = "off" if output_code == "OFF" else output_code
        for token in normalized.get(shift_key, []):
            raw = str(token or "").strip().upper()
            if raw:
                lookup[raw] = canonical_output
    return lookup


@lru_cache(maxsize=1)
def default_shift_lookup() -> dict[str, str]:
    return build_shift_lookup(DEFAULT_SHIFT_ALIAS_CONFIG)


def render_shift_alias_guidance(shift_aliases: dict | None = None) -> str:
    normalized = normalize_shift_alias_config(shift_aliases)
    lines = []
    for shift_key, output_code in SHIFT_ALIAS_GROUP_TO_CODE.items():
        tokens = normalized.get(shift_key, [])
        if tokens:
            lines.append(f"- {output_code}: {', '.join(tokens)}")
    if not lines:
        return ""
    return "Shift strings to recognize for this roster:\n" + "\n".join(lines)


def normalize_shift_code(value: str | None, shift_lookup: dict[str, str] | None = None) -> tuple[str | None, str]:
    raw = str(value or "").strip().upper()
    token = "".join(ch for ch in raw if ch.isalpha() or ch.isdigit())
    lookup = shift_lookup or default_shift_lookup()
    canonical = lookup.get(token)
    if canonical is None and token.startswith("OFF"):
        canonical = "off"
    if canonical is None and token == "OF":
        canonical = "off"
    if canonical is None:
        return None, raw
    return canonical, canonical


def normalize_rows(
    payload: dict,
    row_index: int | None = None,
    *,
    column_count: int = 30,
    shift_lookup: dict[str, str] | None = None,
) -> list[dict]:
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

        trimmed = shifts[:column_count]
        if len(trimmed) < column_count:
            trimmed = trimmed + [None] * (column_count - len(trimmed))

        normalized_shifts = []
        raw_tokens = []
        recognized = 0
        for value in trimmed:
            shift, raw = normalize_shift_code(value, shift_lookup=shift_lookup)
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
                "shifts": [None] * column_count,
                "rawTokens": [None] * column_count,
                "cellBoxes": [],
            }
        normalized.append(item)

    if row_index is not None:
        normalized = [item for item in normalized if item["rowIndex"] == row_index]
    return normalized


def build_prompt(
    year: int,
    month: int,
    *,
    column_count: int = 30,
    with_row_guides: bool = False,
    shift_aliases: dict | None = None,
    with_curve_hint: bool = False,
    day_offset: int = 0,
) -> str:
    names_blob = "\n".join(f"{i+1}. {name}" for i, name in enumerate(ROW_NAMES))
    alias_blob = render_shift_alias_guidance(shift_aliases)
    image_note = (
        "Two images are attached:\n"
        "  Image 1: original photo (use this to read the title for year/month).\n"
        "  Image 2: same table rectified, with blue horizontal lines drawn at the detected row boundaries.\n"
        "  Use the blue boundary lines in Image 2 to precisely identify which cells belong to each nurse row.\n"
        "  Each band between two consecutive blue lines = one nurse row."
        if with_row_guides else
        "One image is attached: the original duty roster photo."
    )
    curve_note = (
        "\nIMPORTANT: The paper is physically curved/bent. "
        "This causes the top portion of the table to appear warped and column spacing to look uneven. "
        "Mentally compensate for this curvature when mapping each cell to its correct day column. "
        "Do not let the visual distortion cause column misalignment."
        if with_curve_hint else ""
    )
    day_start = day_offset + 1
    day_end   = day_offset + column_count
    day_range_hint = (
        f"This image shows only days {day_start}~{day_end} of the month (a vertical stripe)."
        if day_offset > 0 else ""
    )
    return f"""
You are reading a Korean hospital nurse duty roster table photographed with a phone camera.
{curve_note}
{image_note}
{day_range_hint}

{alias_blob if alias_blob else ""}

Context hint (override with what you actually see in the image title):
- Hint year: {year}, Hint month: {month}
- The table header row shows day numbers ({day_start}~{day_end}) and weekday abbreviations (월화수목금토일).
- Below the header are nurse rows. Each nurse row has: row number | name | 성별 | {column_count} day cells.
- The duty cells contain exactly one of: D, E, N, S, Y, OFF
- "off" written in lowercase in the original image should be returned as OFF.
- IMPORTANT: Annual leave cells (Y) are visually distinct — they have a GREY or DARK SHADED background rectangle, with the letter Y written inside. When you see a cell that looks darker/greyer than surrounding white cells, it is a Y cell. Count these shaded cells carefully in sequence; do not skip them or return null. Even if the Y letter is faint against the grey background, the grey shading itself is the signal — read it as Y.

Your task:
1. If Image 1 shows a title like "2026년 6월 근무표", read the actual year and month from it.
   Otherwise use the hint values above.
2. Read every cell for ALL 16 nurses listed below.
3. Use the header row's day numbers to align each cell to the correct day ({day_start}~{day_end}).
4. For each nurse row, trace the {column_count} day cells from left to right as one continuous spatial sequence.
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
      "name": "홍길동",
      "shifts": ["S", "S", "OFF", "S", ... exactly {column_count} items]
    }},
    ...16 rows total
  ]
}}

Rules:
- year and month must reflect what is actually visible in the image title (not the hint) if readable.
- shifts array must have exactly {column_count} items (days 1 through {column_count}).
- Valid shift values: D, E, N, S, Y, OFF — always uppercase, never null unless truly illegible.
- rowIndex is 1-based matching the order above.
- Do not skip any nurse row.
- "Continuous" means spatial continuity of adjacent cell positions and borders, not continuity of the work schedule contents.
- Do not shift values left or right to make a row look more regular.
- Do not copy values from the row above or below.
- Internally verify each row by checking that day 1 through day {column_count} are assigned to {column_count} distinct adjacent cells in left-to-right order.
    """.strip()


def build_row_refine_prompt(
    year: int,
    month: int,
    row_index: int,
    *,
    column_count: int = 30,
    shift_aliases: dict | None = None,
) -> str:
    alias_blob = render_shift_alias_guidance(shift_aliases)
    return f"""
You are re-reading ONE specific nurse row from a Korean hospital duty roster.

Context:
- Hint year: {year}, Hint month: {month}
- The first image shows the whole table with the target row highlighted in red.
- The second image is a focused crop that includes the day header and the target row.
- Read ONLY the highlighted row (rowIndex {row_index}, counted top-to-bottom from the first data row).

{alias_blob if alias_blob else ""}

Task:
1. Use the day header to align day 1 through day {column_count}.
2. Read the highlighted row only, from left to right, as one continuous spatial sequence.
3. Use neighboring visual context only to identify the correct row and cell boundaries.
4. Do not infer from scheduling patterns, neighboring rows, or neighboring days.
5. If a cell is illegible, return null for that cell only.

Return ONLY valid JSON:
{{
  "rowIndex": {row_index},
  "shifts": ["D", "E", "OFF", ... exactly {column_count} items]
}}

Rules:
- shifts must contain exactly {column_count} items.
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
    x_bounds: list[int] | None = None,
) -> Image.Image:
    """감지된 행·열 경계를 선으로 그린 rectified 이미지를 반환한다.

    수평선(파랑): 행 경계. 좌우 끝은 곡률 오차가 있으므로 안쪽 5%부터 그린다.
    수직선(주황, x_bounds 제공 시): 컬럼 경계. 5일 간격(day 5, 10, ...)만 굵게 강조.
    """
    annotated = rectified.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)
    geometry = get_schedule_geometry(template)
    y_offset = int(geometry["top"])
    x_offset = int(geometry["left"])
    margin = int((geometry["right"] - geometry["left"]) * 0.07)
    x_start = int(geometry["left"]) + margin
    x_end = int(geometry["right"]) - margin
    y_start = int(geometry["top"])
    y_end = int(geometry["bottom"])

    for bound_y in y_bounds:
        y = y_offset + int(bound_y)
        if 0 <= y < annotated.height:
            draw.line([(x_start, y), (x_end, y)], fill=(30, 120, 255), width=3)

    if x_bounds:
        for i, bound_x in enumerate(x_bounds):
            x = x_offset + int(bound_x)
            if 0 <= x < annotated.width:
                # 5일 간격(경계선 0, 5, 10, ...)은 굵은 주황, 나머지는 가는 선
                is_major = (i % 5 == 0)
                color = (255, 140, 0) if is_major else (255, 200, 100)
                width = 3 if is_major else 1
                draw.line([(x, y_start), (x, y_end)], fill=color, width=width)

    if annotated.width > max_width:
        scale = max_width / annotated.width
        annotated = annotated.resize(
            (max_width, int(annotated.height * scale)), Image.Resampling.LANCZOS
        )
    return annotated


def mask_identifying_info(
    rectified: Image.Image,
    template: DutySheetTemplate = DEFAULT_TEMPLATE,
) -> Image.Image:
    """이름 컬럼·제목·여백을 검은 박스로 가려 익명 처리한 이미지를 반환한다."""
    masked = rectified.convert("RGB").copy()
    draw = ImageDraw.Draw(masked)
    w, h = masked.size
    g = get_schedule_geometry(template)
    fill = (20, 20, 20)
    draw.rectangle([0,              0, int(g["left"]) - 1, h          ], fill=fill)  # 이름 컬럼
    draw.rectangle([0,              0, w,                  int(g["top"]) - 1], fill=fill)  # 제목
    draw.rectangle([int(g["right"]) + 1, 0, w,             h          ], fill=fill)  # 우측 여백
    draw.rectangle([0, int(g["bottom"]) + 1, w,             h         ], fill=fill)  # 하단 여백
    return masked


def normalize_model_id(value: object) -> str:
    return str(value or "").strip()


def is_previewish_model(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("preview", "beta", "experimental", "exp", "rc"))


def select_latest_stable_sonnet_model(models: list[object]) -> str | None:
    ranked: list[tuple[float, str]] = []
    for model in models:
        model_id = normalize_model_id(getattr(model, "id", ""))
        display_name = normalize_model_id(getattr(model, "display_name", ""))
        combined = f"{model_id} {display_name}".strip().lower()
        if not model_id or "sonnet" not in combined:
            continue
        if is_previewish_model(combined):
            continue
        created_at = getattr(model, "created_at", None)
        created_ts = created_at.timestamp() if created_at is not None else 0.0
        ranked.append((created_ts, model_id))

    if not ranked:
        return None

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][1]


def build_anthropic_model_candidates(
    override_model: str | None,
    discovered_model: str | None,
    fallback_models: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    candidates: list[str] = []
    seen: set[str] = set()

    for candidate in (override_model, discovered_model, *fallback_models):
        model = normalize_model_id(candidate)
        if not model or model in seen:
            continue
        seen.add(model)
        candidates.append(model)

    return tuple(candidates)


def discover_latest_sonnet_model() -> str | None:
    api_key = normalize_model_id(os.environ.get("ANTHROPIC_API_KEY"))
    if not api_key:
        return None

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        models = list(client.models.list(limit=1000))
    except Exception as error:
        LOGGER.info("Anthropic model discovery failed: %s", error)
        return None

    return select_latest_stable_sonnet_model(models)


@lru_cache(maxsize=1)
def resolve_anthropic_model_candidates() -> tuple[str, ...]:
    discovered_model = discover_latest_sonnet_model()
    candidates = build_anthropic_model_candidates(
        DEFAULT_ANTHROPIC_MODEL,
        discovered_model,
        STATIC_ANTHROPIC_MODELS,
    )
    if discovered_model:
        LOGGER.warning(
            "Anthropic model selection resolved: override=%s discovered=%s candidates=%s",
            DEFAULT_ANTHROPIC_MODEL,
            discovered_model,
            " -> ".join(candidates),
        )
    else:
        LOGGER.warning(
            "Anthropic model discovery unavailable; using fallback order=%s",
            " -> ".join(candidates),
        )
    return candidates


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


def is_model_not_found_error(detail: str) -> bool:
    lowered = detail.lower()
    try:
        parsed = json.loads(detail)
    except Exception:
        return (
            "not_found_error" in lowered
            or ("model" in lowered and "not found" in lowered)
            or ("model:" in lowered and "not found" in lowered)
        )
    error = parsed.get("error") if isinstance(parsed, dict) else None
    return isinstance(error, dict) and error.get("type") == "not_found_error"


def send_claude_request_with_model_fallbacks(payload: dict, ssl_context: ssl.SSLContext) -> tuple[dict, str]:
    last_error: RuntimeError | None = None
    candidates = resolve_anthropic_model_candidates()
    for model in candidates:
        attempt_payload = {**payload, "model": model}
        try:
            response_json = send_claude_request(attempt_payload, ssl_context)
            LOGGER.warning("Anthropic OCR selected model=%s", model)
            return response_json, model
        except RuntimeError as error:
            message = str(error)
            if "Claude API request failed:" not in message:
                raise
            detail = message.split("Claude API request failed:", 1)[1].strip()
            if not is_model_not_found_error(detail):
                raise
            last_error = error
    if last_error is not None:
        raise RuntimeError(
            "Claude 모델을 찾지 못했어요. `ANTHROPIC_MODEL` 환경변수에 현재 사용 가능한 모델명을 설정해 주세요. "
            "예: claude-sonnet-4-6, claude-opus-4-8, claude-haiku-4-5."
        ) from last_error
    raise RuntimeError("Claude 모델 후보가 없습니다.")


def request_row_refinement(
    rectified: Image.Image,
    row_box: dict,
    *,
    year: int,
    month: int,
    column_count: int,
    filename: str,
    source_format: str,
    ssl_context: ssl.SSLContext,
    shift_lookup: dict[str, str] | None = None,
    shift_aliases: dict | None = None,
) -> dict | None:
    prompt = build_row_refine_prompt(
        year,
        month,
        row_box["rowIndex"],
        column_count=column_count,
        shift_aliases=shift_aliases,
    )
    focus_crop = crop_row_strip(rectified, row_box, include_header=True)
    highlight_image = build_row_highlight_image(rectified, row_box)
    payload = {
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
    response_json, _used_model = send_claude_request_with_model_fallbacks(payload, ssl_context)
    payload_json = extract_json_payload(extract_text_blocks(response_json))
    normalized = normalize_rows(
        {"rows": [payload_json]},
        row_index=row_box["rowIndex"],
        column_count=column_count,
        shift_lookup=shift_lookup,
    )
    return (normalized[0] if normalized else None), response_json


def parse_duty_image_with_claude(
    image_bytes: bytes,
    filename: str,
    row_index: int | None = None,
    source_format: str = "image",
    include_debug: bool = False,
    year: int | None = None,
    month: int | None = None,
    shift_aliases: dict | None = None,
    refine_row_indices: list[int] | None = None,
    use_row_guides: bool = True,
    guide_image_width: int = 1200,
    on_progress: "Callable[[str], None] | None" = None,
    on_training_data: "Callable[[Image.Image, dict], None] | None" = None,
) -> dict:
    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    import io

    _progress("근무표 읽는 중...")
    image = load_image_rgb(image_bytes)
    image, rotation_applied = auto_orient_duty_image(image)
    guessed_year, guessed_month = guess_month_and_year(filename)
    if year is None:
        year = guessed_year
    if month is None:
        month = guessed_month
    column_count = days_in_month(year, month)
    shift_lookup = build_shift_lookup(shift_aliases)
    table_box = detect_table_box(image)
    rectified = rectify_table(image, table_box, DEFAULT_TEMPLATE.table_size)
    _, y_bounds, x_bounds = detect_schedule_line_bounds(rectified, DEFAULT_TEMPLATE)
    rows_for_overlay = build_schedule_boxes_from_bounds(DEFAULT_TEMPLATE, y_bounds, x_bounds)
    if not rows_for_overlay or not rows_for_overlay[0].get("cellBoxes"):
        rows_for_overlay = build_schedule_boxes(DEFAULT_TEMPLATE)

    table_crop = crop_table_for_claude(image, table_box)
    ssl_context = build_ssl_context()

    content: list[dict] = [{
        "type": "text",
        "text": build_prompt(
            year,
            month,
            column_count=column_count,
            with_row_guides=use_row_guides,
            shift_aliases=shift_aliases,
        ),
    }]
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
        "max_tokens": 4096,
        "temperature": 0,
        "messages": [{"role": "user", "content": content}],
    }

    _progress("AI가 듀티 보는 중... 잠깐만요")
    response_json, used_model = send_claude_request_with_model_fallbacks(payload, ssl_context)
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
    column_count = days_in_month(year, month)

    rows = normalize_rows(
        parsed_payload,
        column_count=column_count,
        shift_lookup=shift_lookup,
    )

    refine_debug: list[dict] = []
    if refine_row_indices:
        by_index = {row["rowIndex"]: row for row in rows}
        overlay_by_index = {row["rowIndex"]: row for row in rows_for_overlay}
        for target_index in sorted(set(idx for idx in refine_row_indices if 1 <= idx <= 16)):
            row_box = overlay_by_index.get(target_index)
            if not row_box:
                continue
            _progress(f"{target_index}번 행 꼼꼼히 확인 중...")
            refined, refine_response = request_row_refinement(
                rectified,
                row_box,
                year=year,
                month=month,
                column_count=column_count,
                filename=filename,
                source_format=source_format,
                ssl_context=ssl_context,
                shift_lookup=shift_lookup,
                shift_aliases=shift_aliases,
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

    _progress("거의 다 됐어요!")

    if row_index is not None:
        rows = [row for row in rows if row["rowIndex"] == row_index]

    result = {
        "year": year,
        "month": month,
        "rows": rows,
        "template": {
            "tableSize": list(DEFAULT_TEMPLATE.table_size),
            "rowSlotCount": DEFAULT_TEMPLATE.row_slot_count,
            "columnCount": column_count,
            "detectedTableBox": list(table_box),
            "parser": "claude",
            "model": used_model,
            "sourceFormat": source_format,
            "rotationApplied": rotation_applied,
            "refinedRows": refine_debug,
            "inputTokens": total_input_tokens,
            "outputTokens": total_output_tokens,
        },
    }
    if include_debug:
        result["debugImage"] = make_debug_overlay(image, rectified, table_box, rows_for_overlay, rows)
    if on_training_data:
        try:
            masked = mask_identifying_info(rectified, DEFAULT_TEMPLATE)
            on_training_data(masked, result)
        except Exception:
            pass  # 학습 데이터 저장 실패는 OCR 결과에 영향 없음
    return result
