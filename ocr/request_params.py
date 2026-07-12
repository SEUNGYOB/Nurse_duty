from __future__ import annotations

import json


def parse_optional_int(raw: object, *, min_value: int, max_value: int) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(min_value, min(max_value, value))


def load_shift_aliases(form) -> dict | None:
    raw = form.get("shiftAliases")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_ocr_request_context(form) -> dict[str, object]:
    return {
        "row_index": parse_optional_int(form.get("rowIndex"), min_value=1, max_value=16),
        "year": parse_optional_int(form.get("year"), min_value=2020, max_value=2099),
        "month": parse_optional_int(form.get("month"), min_value=1, max_value=12),
        "shift_aliases": load_shift_aliases(form),
    }
