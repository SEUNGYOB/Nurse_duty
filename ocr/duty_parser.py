from __future__ import annotations

import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


ROW_NAMES = [
    "윤미영",
    "구명임",
    "이현진",
    "장해진",
    "이영미",
    "이경순",
    "김향수",
    "최은영",
    "유정숙",
    "이은우",
    "김영애",
    "양일향",
    "김우섭",
    "김주희",
    "김성남",
    "김우진",
]

SHIFT_MAP = {
    "D": "day",
    "E": "evening",
    "N": "night",
    "S": "swing",
    "Y": "training",
    "OFF": "off",
    "OF": "off",
    "OFFF": "off",
    "0FF": "off",
    "OOF": "off",
}


@dataclass(frozen=True)
class DutySheetTemplate:
    table_box: tuple[float, float, float, float]
    schedule_box: tuple[float, float, float, float]
    row_slots: tuple[int, ...]
    row_slot_count: int
    column_count: int = 30


DEFAULT_TEMPLATE = DutySheetTemplate(
    table_box=(0.006, 0.172, 0.989, 0.878),
    schedule_box=(0.111, 0.068, 0.973, 0.922),
    row_slots=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16),
    row_slot_count=17,
)


def normalize_shift(raw: str) -> str | None:
    token = "".join(ch for ch in raw.upper() if ch.isalpha())
    if not token:
        return None
    if token.startswith("OFF") or token == "OF":
        return "off"
    return SHIFT_MAP.get(token)


def guess_month_and_year(filename: str) -> tuple[int, int]:
    lower = filename.lower()
    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    month = next((value for key, value in month_map.items() if key in lower), 1)
    year = 2026
    return year, month


def to_pixels(image: Image.Image, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    width, height = image.size
    left = int(round(box[0] * width))
    top = int(round(box[1] * height))
    right = int(round(box[2] * width))
    bottom = int(round(box[3] * height))
    return left, top, right, bottom


def ocr_shift_cell(image: Image.Image) -> tuple[str | None, str]:
    prepared = ImageOps.autocontrast(image.convert("L"))
    prepared = prepared.point(lambda value: 255 if value > 178 else 0)
    prepared = prepared.resize((prepared.width * 4, prepared.height * 4))

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        prepared.save(temp_path)
        result = subprocess.run(
            [
                "tesseract",
                str(temp_path),
                "stdout",
                "--psm",
                "10",
                "-l",
                "eng",
                "-c",
                "tessedit_char_whitelist=DENSYoff",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        raw = " ".join(result.stdout.decode("utf-8", "ignore").split()).strip()
        return normalize_shift(raw), raw
    finally:
        temp_path.unlink(missing_ok=True)


def build_schedule_boxes(image: Image.Image, template: DutySheetTemplate) -> list[dict]:
    table_left, table_top, table_right, table_bottom = to_pixels(image, template.table_box)
    schedule_left_ratio, schedule_top_ratio, schedule_right_ratio, schedule_bottom_ratio = template.schedule_box
    table_width = table_right - table_left
    table_height = table_bottom - table_top

    schedule_left = int(round(table_left + (schedule_left_ratio * table_width)))
    schedule_top = int(round(table_top + (schedule_top_ratio * table_height)))
    schedule_right = int(round(table_left + (schedule_right_ratio * table_width)))
    schedule_bottom = int(round(table_top + (schedule_bottom_ratio * table_height)))

    cell_width = (schedule_right - schedule_left) / template.column_count
    slot_height = (schedule_bottom - schedule_top) / template.row_slot_count

    rows = []
    for row_index, slot_index in enumerate(template.row_slots):
        row_top = schedule_top + (slot_index * slot_height)
        row_bottom = schedule_top + ((slot_index + 1) * slot_height)
        shifts = []

        for day_index in range(template.column_count):
            cell_left = schedule_left + (day_index * cell_width)
            cell_right = schedule_left + ((day_index + 1) * cell_width)
            shifts.append(
                (
                    int(round(cell_left + (cell_width * 0.16))),
                    int(round(row_top + (slot_height * 0.14))),
                    int(round(cell_right - (cell_width * 0.16))),
                    int(round(row_bottom - (slot_height * 0.14))),
                )
            )

        rows.append(
            {
                "rowIndex": row_index + 1,
                "name": ROW_NAMES[row_index],
                "cellBoxes": shifts,
            }
        )

    return rows


def parse_duty_image_bytes(image_bytes: bytes, filename: str, row_index: int | None = None) -> dict:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    year, month = guess_month_and_year(filename)
    rows = build_schedule_boxes(image, DEFAULT_TEMPLATE)
    target_rows = [item for item in rows if row_index is None or item["rowIndex"] == row_index]

    parsed_rows = []
    for row in target_rows:
        recognized = 0
        shifts = []
        raw_tokens = []

        for box in row["cellBoxes"]:
            cell = image.crop(box)
            shift, raw = ocr_shift_cell(cell)
            if shift:
                recognized += 1
            shifts.append(shift)
            raw_tokens.append(raw)

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

    table_left, table_top, table_right, table_bottom = to_pixels(image, DEFAULT_TEMPLATE.table_box)
    return {
        "year": year,
        "month": month,
        "rows": parsed_rows,
        "template": {
            "tableBox": [table_left, table_top, table_right, table_bottom],
            "rowSlotCount": DEFAULT_TEMPLATE.row_slot_count,
            "columnCount": DEFAULT_TEMPLATE.column_count,
        },
    }
