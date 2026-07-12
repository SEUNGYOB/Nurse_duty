from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import tempfile
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from . import _morphology as ndimage


_NAMES_FILE = Path(__file__).resolve().parent.parent / "names.json"
_NAMES_EXAMPLE = Path(__file__).resolve().parent.parent / "names.example.json"

def _load_row_names() -> list[str]:
    # 우선순위: DUTY_ROW_NAMES 환경변수(JSON 배열) → names.json → names.example.json → 일반 이름
    # 실명은 저장소에 올리지 않으므로 배포 환경에는 파일이 없을 수 있다 — 임포트가 죽으면 안 됨.
    env_names = os.environ.get("DUTY_ROW_NAMES", "")
    if env_names:
        try:
            data = json.loads(env_names)
            if isinstance(data, list) and data:
                return [str(n) for n in data]
        except (ValueError, TypeError):
            pass
    for candidate in (_NAMES_FILE, _NAMES_EXAMPLE):
        if candidate.exists():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return [str(n) for n in data]
    return [f"간호사{i}" for i in range(1, 17)]

ROW_NAMES: list[str] = _load_row_names()

SHIFT_MAP = {
    "D": "D",
    "E": "E",
    "N": "N",
    "S": "S",
    "Y": "Y",
    "OFF": "off",
    "OF": "off",
    "OFFF": "off",
    "0FF": "off",
    "OOF": "off",
}

CELL_LABELS = ("off", "D", "E", "N", "S", "Y")
CELL_TEMPLATE_SIZE = (96, 72)
CELL_TEMPLATE_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    "/System/Library/Fonts/Supplemental/Trebuchet MS Bold.ttf",
)

TABLE_OUTPUT_SIZE = (3600, 2100)
DEBUG_IMAGE_SIZE = (1200, 700)


@dataclass(frozen=True)
class DutySheetTemplate:
    table_size: tuple[int, int]
    schedule_box: tuple[float, float, float, float]
    row_slots: tuple[int, ...]
    row_slot_count: int
    column_count: int = 30


DEFAULT_TEMPLATE = DutySheetTemplate(
    table_size=TABLE_OUTPUT_SIZE,
    schedule_box=(0.112, 0.069, 0.973, 0.845),
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


def pil_to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.uint8)


def load_image_rgb(image_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def fallback_table_box(image: Image.Image) -> tuple[int, int, int, int]:
    width, height = image.size
    return (
        int(width * 0.01),
        int(height * 0.14),
        int(width * 0.99),
        int(height * 0.86),
    )


def contiguous_runs(indices: np.ndarray) -> list[tuple[int, int]]:
    if len(indices) == 0:
        return []
    runs = []
    start = int(indices[0])
    prev = int(indices[0])
    for raw in indices[1:]:
        current = int(raw)
        if current == prev + 1:
            prev = current
            continue
        runs.append((start, prev))
        start = prev = current
    runs.append((start, prev))
    return runs


def normalize_profile(values: np.ndarray) -> np.ndarray:
    values = values.astype(float)
    if values.size == 0:
        return values
    peak = float(values.max())
    if peak <= 0:
        return values
    return values / peak


def pick_bounds_from_profile(scores: np.ndarray, threshold_ratio: float, minimum_span: int) -> tuple[int, int] | None:
    if scores.size == 0 or float(scores.max()) <= 0:
        return None
    indices = np.where(scores > float(scores.max()) * threshold_ratio)[0]
    runs = contiguous_runs(indices)
    runs = [run for run in runs if run[1] - run[0] + 1 >= minimum_span]
    if not runs:
        return None
    return runs[0][0], runs[-1][1]


def detect_table_box(image: Image.Image) -> tuple[int, int, int, int]:
    gray = pil_to_array(image)
    height, width = gray.shape

    x_margin = int(width * 0.04)
    y_margin = int(height * 0.08)
    center = gray[y_margin : height - y_margin, x_margin : width - x_margin]

    binary = center < 185
    horizontal = ndimage.binary_opening(
        binary,
        structure=np.ones((1, max(80, center.shape[1] // 30)), dtype=bool),
    )
    vertical = ndimage.binary_opening(
        binary,
        structure=np.ones((max(60, center.shape[0] // 28), 1), dtype=bool),
    )

    row_scores = ndimage.uniform_filter1d(horizontal.mean(axis=1), size=31)
    col_scores = ndimage.uniform_filter1d(vertical.mean(axis=0), size=31)

    row_bounds = pick_bounds_from_profile(row_scores, threshold_ratio=0.18, minimum_span=12)
    col_bounds = pick_bounds_from_profile(col_scores, threshold_ratio=0.10, minimum_span=12)

    if row_bounds is None or col_bounds is None:
        return fallback_table_box(image)

    left = max(0, x_margin + col_bounds[0] - int(width * 0.01))
    top = max(0, y_margin + row_bounds[0] - int(height * 0.01))
    bottom = min(height, y_margin + row_bounds[1] + int(height * 0.01))

    # 감지된 우측 끝에서 한 셀 폭만큼 오른쪽으로 탐색해 실제 마지막 세로선을 찾는다
    detected_right = x_margin + col_bounds[1]
    search_end = min(width, detected_right + int(width * 0.06))
    right_strip = gray[y_margin : height - y_margin, detected_right:search_end]
    strip_dark = right_strip < 185
    strip_vertical = ndimage.binary_opening(
        strip_dark,
        structure=np.ones((max(60, right_strip.shape[0] // 28), 1), dtype=bool),
    )
    strip_col_scores = strip_vertical.mean(axis=0)
    if strip_col_scores.size > 0 and float(strip_col_scores.max()) > 0.05:
        candidates = np.where(strip_col_scores > float(strip_col_scores.max()) * 0.3)[0]
        if candidates.size:
            right = min(width, detected_right + int(candidates[-1]) + 4)
        else:
            right = min(width, detected_right + int(width * 0.01))
    else:
        right = min(width, detected_right + int(width * 0.01))

    if right - left < width * 0.55 or bottom - top < height * 0.45:
        return fallback_table_box(image)

    return left, top, right, bottom


def score_table_orientation(image: Image.Image) -> tuple[float, tuple[int, int, int, int]]:
    table_box = detect_table_box(image)
    left, top, right, bottom = table_box
    width = max(1, right - left)
    height = max(1, bottom - top)

    coverage = (width * height) / max(1, image.width * image.height)
    aspect_bonus = min(3.0, width / height)
    landscape_bonus = 0.4 if width >= height else 0.0
    centered_x = 1.0 - min(1.0, abs(((left + right) / 2) - (image.width / 2)) / max(1.0, image.width / 2))
    centered_y = 1.0 - min(1.0, abs(((top + bottom) / 2) - (image.height / 2)) / max(1.0, image.height / 2))
    center_bonus = 0.5 * (centered_x + centered_y)
    score = (coverage * 3.0) + aspect_bonus + landscape_bonus + center_bonus
    return score, table_box


def score_left_label_layout(image: Image.Image, table_box: tuple[int, int, int, int]) -> float:
    rectified = rectify_table(image, table_box, TABLE_OUTPUT_SIZE)
    gray = pil_to_array(rectified)
    dark = gray < 190
    vertical = ndimage.binary_opening(
        dark,
        structure=np.ones((max(48, rectified.height // 22), 1), dtype=bool),
    )

    edge_width = max(
        1,
        int(
            round(
                rectified.width
                * max(DEFAULT_TEMPLATE.schedule_box[0], 1.0 - DEFAULT_TEMPLATE.schedule_box[2])
            )
        ),
    )
    left_band = vertical[:, :edge_width]
    right_band = vertical[:, rectified.width - edge_width :]
    left_density = float(left_band.mean()) if left_band.size else 0.0
    right_density = float(right_band.mean()) if right_band.size else 0.0

    # 정방향에서는 왼쪽 라벨 열보다 오른쪽 듀티 그리드의 세로선 밀도가 높다.
    return right_density - left_density


def auto_orient_duty_image(image: Image.Image) -> tuple[Image.Image, int]:
    best_image = image
    best_rotation = 0
    best_score, _ = score_table_orientation(image)

    for rotation in (90, 270):
        rotated = image.rotate(rotation, expand=True)
        score, _ = score_table_orientation(rotated)
        if score > best_score + 0.05:
            best_image = rotated
            best_rotation = rotation
            best_score = score

    best_table_box = detect_table_box(best_image)
    upright_layout = score_left_label_layout(best_image, best_table_box)
    flipped_image = best_image.rotate(180, expand=True)
    flipped_table_box = detect_table_box(flipped_image)
    flipped_layout = score_left_label_layout(flipped_image, flipped_table_box)
    if flipped_layout > upright_layout + 0.01:
        best_image = flipped_image
        best_rotation = (best_rotation + 180) % 360

    return best_image, best_rotation


def rectify_table(image: Image.Image, table_box: tuple[int, int, int, int], output_size: tuple[int, int]) -> Image.Image:
    cropped = image.crop(table_box)
    return cropped.resize(output_size, Image.Resampling.BICUBIC)


def build_schedule_boxes(template: DutySheetTemplate) -> list[dict]:
    table_width, table_height = template.table_size
    schedule_left_ratio, schedule_top_ratio, schedule_right_ratio, schedule_bottom_ratio = template.schedule_box

    schedule_left = int(round(schedule_left_ratio * table_width))
    schedule_top = int(round(schedule_top_ratio * table_height))
    schedule_right = int(round(schedule_right_ratio * table_width))
    schedule_bottom = int(round(schedule_bottom_ratio * table_height))

    cell_width = (schedule_right - schedule_left) / template.column_count
    slot_height = (schedule_bottom - schedule_top) / template.row_slot_count

    rows = []
    for row_index, slot_index in enumerate(template.row_slots):
        row_top = schedule_top + (slot_index * slot_height)
        row_bottom = schedule_top + ((slot_index + 1) * slot_height)
        cell_boxes = []

        for day_index in range(template.column_count):
            cell_left = schedule_left + (day_index * cell_width)
            cell_right = schedule_left + ((day_index + 1) * cell_width)
            cell_boxes.append(
                (
                    int(round(cell_left + (cell_width * 0.12))),
                    int(round(row_top + (slot_height * 0.10))),
                    int(round(cell_right - (cell_width * 0.12))),
                    int(round(row_bottom - (slot_height * 0.08))),
                )
            )

        rows.append({"rowIndex": row_index + 1, "cellBoxes": cell_boxes})

    return rows


def correct_bounds_outliers(bounds: list[int], sigma_threshold: float = 2.0) -> list[int]:
    """선형 피팅 잔차 기반으로 이상치 경계점을 교정한다.

    전체 감지된 점들에 1차 선형 모델(a + i*b)을 피팅한 뒤,
    잔차가 sigma_threshold × std 를 넘는 내부 점을 피팅값으로 교체한다.
    첫 번째와 마지막 점은 고정한다.
    """
    if len(bounds) < 4:
        return bounds
    indices = np.arange(len(bounds), dtype=float)
    raw = np.array(bounds, dtype=float)
    coeffs = np.polyfit(indices, raw, 1)
    fitted = np.polyval(coeffs, indices)
    residuals = raw - fitted
    std = float(residuals.std())
    if std < 1e-6:
        return bounds
    corrected = list(bounds)
    for i in range(1, len(bounds) - 1):
        if abs(residuals[i]) > sigma_threshold * std:
            corrected[i] = int(round(float(fitted[i])))
    return corrected


def detect_profile_boundaries(
    scores: np.ndarray,
    expected_positions: list[int],
    radius: int = 18,
    min_ratio: float = 0.35,
) -> list[int]:
    if scores.size == 0:
        return expected_positions

    smoothed = ndimage.uniform_filter1d(scores.astype(float), size=max(5, radius // 2), mode="nearest")
    max_score = float(smoothed.max()) if smoothed.size else 0.0
    if max_score <= 0:
        return expected_positions

    bounds = [int(expected_positions[0])]
    last = bounds[0]

    for index in range(1, len(expected_positions) - 1):
        expected = int(expected_positions[index])
        next_expected = int(expected_positions[index + 1])
        start = max(last + 1, expected - radius)
        end = min(len(smoothed) - 1, next_expected - 1, expected + radius)

        candidate = expected
        if start <= end:
            window = smoothed[start : end + 1]
            if window.size and float(window.max()) >= max_score * min_ratio:
                candidate = start + int(window.argmax())

        candidate = max(last + 1, min(candidate, next_expected - 1))
        bounds.append(int(candidate))
        last = int(candidate)

    bounds.append(int(expected_positions[-1]))
    return bounds


def detect_schedule_line_bounds(
    rectified: Image.Image,
    template: DutySheetTemplate = DEFAULT_TEMPLATE,
) -> tuple[Image.Image, list[int], list[int]]:
    geometry = get_schedule_geometry(template)
    schedule = rectified.crop((geometry["left"], geometry["top"], geometry["right"], geometry["bottom"]))

    gray = np.asarray(ImageOps.autocontrast(schedule.convert("L")), dtype=float)
    dark = gray < 190
    inverted = 255.0 - gray

    # Long line kernels catch the obvious grid lines, while shorter kernels
    # keep faint or slightly broken lines from disappearing.
    horizontal_masks = []
    for kernel_width in {max(24, schedule.width // 120), max(42, schedule.width // 70), max(60, schedule.width // 25)}:
        horizontal_masks.append(
            ndimage.binary_opening(
                dark,
                structure=np.ones((1, kernel_width), dtype=bool),
            )
        )
    vertical_masks = []
    for kernel_height in {max(24, schedule.height // 120), max(42, schedule.height // 70), max(60, schedule.height // 20)}:
        vertical_masks.append(
            ndimage.binary_opening(
                dark,
                structure=np.ones((kernel_height, 1), dtype=bool),
            )
        )

    horizontal_mask = np.zeros_like(dark, dtype=bool)
    for mask in horizontal_masks:
        horizontal_mask |= ndimage.binary_dilation(mask, structure=np.ones((1, 3), dtype=bool))

    vertical_mask = np.zeros_like(dark, dtype=bool)
    for mask in vertical_masks:
        vertical_mask |= ndimage.binary_dilation(mask, structure=np.ones((3, 1), dtype=bool))

    # Edge response helps when a line is broken but still visually obvious.
    horizontal_edge = np.abs(ndimage.sobel(inverted, axis=0))
    vertical_edge = np.abs(ndimage.sobel(inverted, axis=1))

    horiz_scores = normalize_profile(ndimage.uniform_filter1d(horizontal_mask.mean(axis=1), size=9, mode="nearest"))
    horiz_scores = 0.75 * horiz_scores + 0.25 * normalize_profile(ndimage.uniform_filter1d(horizontal_edge.mean(axis=1), size=9, mode="nearest"))
    vert_scores = normalize_profile(ndimage.uniform_filter1d(vertical_mask.mean(axis=0), size=9, mode="nearest"))
    vert_scores = 0.75 * vert_scores + 0.25 * normalize_profile(ndimage.uniform_filter1d(vertical_edge.mean(axis=0), size=9, mode="nearest"))

    expected_y = [int(round(i * schedule.height / template.row_slot_count)) for i in range(template.row_slot_count + 1)]
    expected_x = [int(round(i * schedule.width / template.column_count)) for i in range(template.column_count + 1)]

    y_bounds = detect_profile_boundaries(horiz_scores, expected_y, radius=22, min_ratio=0.18)
    x_bounds = detect_profile_boundaries(vert_scores, expected_x, radius=18, min_ratio=0.15)
    x_bounds = correct_bounds_outliers(x_bounds)
    return schedule, y_bounds, x_bounds


def build_schedule_boxes_from_bounds(
    template: DutySheetTemplate,
    y_bounds: list[int],
    x_bounds: list[int],
) -> list[dict]:
    if len(y_bounds) != template.row_slot_count + 1 or len(x_bounds) != template.column_count + 1:
        return build_schedule_boxes(template)

    schedule_left_ratio, schedule_top_ratio, _, _ = template.schedule_box
    table_width, table_height = template.table_size
    schedule_left = int(round(schedule_left_ratio * table_width))
    schedule_top = int(round(schedule_top_ratio * table_height))

    rows = []
    for row_index, slot_index in enumerate(template.row_slots):
        row_top = schedule_top + y_bounds[slot_index]
        row_bottom = schedule_top + y_bounds[slot_index + 1]
        cell_boxes = []

        for day_index in range(template.column_count):
            cell_left = schedule_left + x_bounds[day_index]
            cell_right = schedule_left + x_bounds[day_index + 1]
            cell_width = max(1, cell_right - cell_left)
            cell_height = max(1, row_bottom - row_top)
            cell_boxes.append(
                (
                    int(round(cell_left + (cell_width * 0.12))),
                    int(round(row_top + (cell_height * 0.10))),
                    int(round(cell_right - (cell_width * 0.12))),
                    int(round(row_bottom - (cell_height * 0.08))),
                )
            )

        rows.append({"rowIndex": row_index + 1, "cellBoxes": cell_boxes})

    return rows


def get_schedule_geometry(template: DutySheetTemplate) -> dict[str, float]:
    table_width, table_height = template.table_size
    left = int(round(template.schedule_box[0] * table_width))
    top = int(round(template.schedule_box[1] * table_height))
    right = int(round(template.schedule_box[2] * table_width))
    bottom = int(round(template.schedule_box[3] * table_height))
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "cell_width": (right - left) / template.column_count,
        "slot_height": (bottom - top) / template.row_slot_count,
    }


def preprocess_shift_cell(image: Image.Image) -> Image.Image:
    prepared = ImageOps.autocontrast(image.convert("L"))
    prepared = prepared.filter(ImageFilter.MedianFilter(size=3))
    array = np.asarray(prepared, dtype=np.uint8)
    binary = np.where(array > 172, 255, 0).astype(np.uint8)
    inverted = 255 - binary

    horizontal = ndimage.binary_opening(
        inverted > 0,
        structure=np.ones((1, max(6, binary.shape[1] // 6)), dtype=bool),
    )
    vertical = ndimage.binary_opening(
        inverted > 0,
        structure=np.ones((max(6, binary.shape[0] // 4), 1), dtype=bool),
    )
    line_mask = ndimage.binary_dilation(horizontal, structure=np.ones((1, 2), dtype=bool))
    line_mask |= ndimage.binary_dilation(vertical, structure=np.ones((2, 1), dtype=bool))

    cleaned = inverted.copy()
    cleaned[line_mask] = 0
    ink = np.argwhere(cleaned > 0)
    if ink.size:
        ys = ink[:, 0]
        xs = ink[:, 1]
        pad_x = max(2, int(round(cleaned.shape[1] * 0.08)))
        pad_y = max(2, int(round(cleaned.shape[0] * 0.08)))
        left = max(0, int(xs.min()) - pad_x)
        right = min(cleaned.shape[1], int(xs.max()) + pad_x + 1)
        top = max(0, int(ys.min()) - pad_y)
        bottom = min(cleaned.shape[0], int(ys.max()) + pad_y + 1)
        cleaned = cleaned[top:bottom, left:right]
    restored = 255 - cleaned
    return Image.fromarray(restored).resize((max(1, prepared.width * 5), max(1, prepared.height * 5)))


def crop_center_focus(image: Image.Image, margin_x_ratio: float = 0.18, margin_y_ratio: float = 0.16) -> Image.Image:
    width, height = image.size
    left = int(round(width * margin_x_ratio))
    top = int(round(height * margin_y_ratio))
    right = int(round(width * (1 - margin_x_ratio)))
    bottom = int(round(height * (1 - margin_y_ratio)))
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def crop_ink_focus(image: Image.Image, pad_ratio_x: float = 0.10, pad_ratio_y: float = 0.10) -> Image.Image:
    prepared = ImageOps.autocontrast(image.convert("L"))
    prepared = prepared.filter(ImageFilter.MedianFilter(size=3))
    array = np.asarray(prepared, dtype=np.uint8)
    binary = np.where(array > 180, 255, 0).astype(np.uint8)
    inverted = 255 - binary

    # Keep only the long grid-like components out of the way.
    horizontal = ndimage.binary_opening(
        inverted > 0,
        structure=np.ones((1, max(5, binary.shape[1] // 4)), dtype=bool),
    )
    vertical = ndimage.binary_opening(
        inverted > 0,
        structure=np.ones((max(5, binary.shape[0] // 3), 1), dtype=bool),
    )
    line_mask = ndimage.binary_dilation(horizontal, structure=np.ones((1, 2), dtype=bool))
    line_mask |= ndimage.binary_dilation(vertical, structure=np.ones((2, 1), dtype=bool))

    cleaned = inverted.copy()
    cleaned[line_mask] = 0
    ink = np.argwhere(cleaned > 0)
    if not ink.size:
        return image

    ys = ink[:, 0]
    xs = ink[:, 1]
    pad_x = max(2, int(round(cleaned.shape[1] * pad_ratio_x)))
    pad_y = max(2, int(round(cleaned.shape[0] * pad_ratio_y)))
    left = max(0, int(xs.min()) - pad_x)
    right = min(cleaned.shape[1], int(xs.max()) + pad_x + 1)
    top = max(0, int(ys.min()) - pad_y)
    bottom = min(cleaned.shape[0], int(ys.max()) + pad_y + 1)
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def prepare_text_variants(image: Image.Image) -> list[Image.Image]:
    base = crop_ink_focus(image, pad_ratio_x=0.12, pad_ratio_y=0.12)
    wide = crop_ink_focus(image, pad_ratio_x=0.18, pad_ratio_y=0.18)
    loose = crop_ink_focus(image, pad_ratio_x=0.24, pad_ratio_y=0.24)
    variants = []
    for item in (base, wide, loose):
        if item.size not in {(0, 0)}:
            variants.append(item)
    return variants or [image]


def preprocess_classifier_cell(image: Image.Image) -> Image.Image:
    focused = crop_ink_focus(image, pad_ratio_x=0.10, pad_ratio_y=0.10)
    prepared = ImageOps.autocontrast(focused.convert("L"))
    prepared = prepared.filter(ImageFilter.MedianFilter(size=3))
    array = np.asarray(prepared, dtype=np.uint8)
    binary = np.where(array > 180, 255, 0).astype(np.uint8)
    inverted = 255 - binary

    horizontal = ndimage.binary_opening(
        inverted > 0,
        structure=np.ones((1, max(5, binary.shape[1] // 5)), dtype=bool),
    )
    vertical = ndimage.binary_opening(
        inverted > 0,
        structure=np.ones((max(5, binary.shape[0] // 4), 1), dtype=bool),
    )
    line_mask = ndimage.binary_dilation(horizontal, structure=np.ones((1, 2), dtype=bool))
    line_mask |= ndimage.binary_dilation(vertical, structure=np.ones((2, 1), dtype=bool))

    cleaned = inverted.copy()
    cleaned[line_mask] = 0
    ink = np.argwhere(cleaned > 0)
    if ink.size:
        ys = ink[:, 0]
        xs = ink[:, 1]
        pad_x = max(2, int(round(cleaned.shape[1] * 0.06)))
        pad_y = max(2, int(round(cleaned.shape[0] * 0.06)))
        left = max(0, int(xs.min()) - pad_x)
        right = min(cleaned.shape[1], int(xs.max()) + pad_x + 1)
        top = max(0, int(ys.min()) - pad_y)
        bottom = min(cleaned.shape[0], int(ys.max()) + pad_y + 1)
        cleaned = cleaned[top:bottom, left:right]

    restored = 255 - cleaned
    return Image.fromarray(restored).resize(CELL_TEMPLATE_SIZE, Image.Resampling.BICUBIC)


def _load_font(font_path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(font_path, size=size)
    except Exception:
        return ImageFont.load_default()


@lru_cache(maxsize=1)
def _template_fonts() -> tuple[str, ...]:
    return tuple(path for path in CELL_TEMPLATE_FONT_CANDIDATES if Path(path).exists())


@lru_cache(maxsize=128)
def render_cell_template(label: str, font_path: str, font_size: int) -> Image.Image:
    canvas = Image.new("L", CELL_TEMPLATE_SIZE, 255)
    draw = ImageDraw.Draw(canvas)
    font = _load_font(font_path, font_size)
    text = label
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (CELL_TEMPLATE_SIZE[0] - text_w) // 2 - bbox[0]
    y = (CELL_TEMPLATE_SIZE[1] - text_h) // 2 - bbox[1]
    draw.text((x, y), text, fill=0, font=font)
    return preprocess_classifier_cell(canvas)


def template_distance(cell_image: Image.Image, template_image: Image.Image) -> float:
    cell = np.asarray(preprocess_classifier_cell(cell_image), dtype=float) / 255.0
    template = np.asarray(template_image, dtype=float) / 255.0
    diff = np.mean(np.abs(cell - template))
    cell_ink = 1.0 - float(cell.mean())
    template_ink = 1.0 - float(template.mean())
    return diff + abs(cell_ink - template_ink) * 0.3


def score_cell_candidates(cell_image: Image.Image) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    fonts = _template_fonts()
    if not fonts:
        return []

    variants = prepare_text_variants(cell_image)

    size_map = {
        "off": (24, 28, 32),
        "D": (34, 38, 42),
        "E": (34, 38, 42),
        "N": (34, 38, 42),
        "S": (34, 38, 42),
        "Y": (34, 38, 42),
    }

    for label in CELL_LABELS:
        best = None
        for font_path in fonts:
            for size in size_map[label]:
                template = render_cell_template(label, font_path, size)
                for variant in variants:
                    score = template_distance(variant, template)
                    if best is None or score < best:
                        best = score
        if best is not None:
            candidates.append((label, float(best)))

    candidates.sort(key=lambda item: item[1])
    return candidates


def classify_shift_cell(image: Image.Image) -> tuple[str | None, str, float, float]:
    focused = crop_ink_focus(image, pad_ratio_x=0.10, pad_ratio_y=0.10)
    prepared = preprocess_classifier_cell(focused)
    ink_ratio = 1.0 - float(np.asarray(prepared).mean() / 255.0)
    if ink_ratio < 0.015:
        return None, "", 0.0, 0.0

    scores = score_cell_candidates(focused)
    if not scores:
        return None, "", 0.0, 0.0

    best_label, best_score = scores[0]
    second_score = scores[1][1] if len(scores) > 1 else best_score + 1.0
    margin = second_score - best_score

    if best_label == "off":
        confident = best_score < 0.34 and margin > 0.025
    else:
        confident = best_score < 0.36 and margin > 0.02

    if not confident:
        return None, best_label, best_score, margin
    return best_label, best_label, best_score, margin


def preprocess_row_strip(image: Image.Image) -> Image.Image:
    prepared = ImageOps.autocontrast(image.convert("L"))
    prepared = prepared.filter(ImageFilter.MedianFilter(size=3))
    prepared = prepared.point(lambda value: 255 if value > 178 else 0)
    array = np.asarray(prepared).copy()

    # Remove strong border/grid lines so row text survives as isolated tokens.
    dark = array < 200
    row_ratio = dark.mean(axis=1)
    col_ratio = dark.mean(axis=0)
    array[row_ratio > 0.45, :] = 255
    array[:, col_ratio > 0.45] = 255

    trimmed = Image.fromarray(array).resize((prepared.width * 4, prepared.height * 4))
    return trimmed


def find_tesseract_executable() -> str | None:
    candidates = [
        shutil.which("tesseract"),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def run_tesseract(image: Image.Image, psm: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        image.save(temp_path)
        executable = find_tesseract_executable()
        if not executable:
            raise FileNotFoundError("tesseract executable not found. Install tesseract or add it to PATH.")
        result = subprocess.run(
            [
                executable,
                str(temp_path),
                "stdout",
                "--psm",
                str(psm),
                "-l",
                "eng",
                "-c",
                "tessedit_char_whitelist=DENSYoff",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return " ".join(result.stdout.decode("utf-8", "ignore").split()).strip()
    finally:
        temp_path.unlink(missing_ok=True)


def run_tesseract_tsv(image: Image.Image, psm: int) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        image.save(temp_path)
        executable = find_tesseract_executable()
        if not executable:
            raise FileNotFoundError("tesseract executable not found. Install tesseract or add it to PATH.")
        result = subprocess.run(
            [
                executable,
                str(temp_path),
                "stdout",
                "--psm",
                str(psm),
                "-l",
                "eng",
                "-c",
                "tessedit_char_whitelist=DENSYoff",
                "tsv",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        lines = result.stdout.decode("utf-8", "ignore").splitlines()
        if not lines:
            return []
        headers = lines[0].split("\t")
        rows = []
        for line in lines[1:]:
            values = line.split("\t")
            if len(values) != len(headers):
                continue
            item = dict(zip(headers, values))
            text = " ".join(item.get("text", "").split()).strip()
            if not text:
                continue
            rows.append(item)
        return rows
    finally:
        temp_path.unlink(missing_ok=True)


def ocr_shift_cell(image: Image.Image) -> tuple[str | None, str]:
    focused = crop_ink_focus(image, pad_ratio_x=0.12, pad_ratio_y=0.12)
    prepared = preprocess_shift_cell(focused)
    width, height = prepared.size
    ink_ratio = (np.asarray(prepared) < 200).mean()
    if ink_ratio < 0.015:
        return None, ""

    tall_crop = prepared.crop((0, 0, width, height))
    single_raw = run_tesseract(tall_crop, psm=10)
    word_raw = run_tesseract(tall_crop, psm=7)

    candidates = [single_raw, word_raw]
    for candidate in candidates:
        normalized = normalize_shift(candidate)
        if normalized:
            return normalized, candidate

    merged = max(candidates, key=len, default="")
    return normalize_shift(merged), merged


def parse_row_strip(row_image: Image.Image, column_count: int) -> tuple[list[str | None], list[str]]:
    prepared = preprocess_row_strip(row_image)
    tsv_rows = run_tesseract_tsv(prepared, psm=6)
    shifts: list[str | None] = [None] * column_count
    raw_tokens: list[str] = [""] * column_count

    if not tsv_rows:
        return shifts, raw_tokens

    scale_x = row_image.width / prepared.width
    cell_width = row_image.width / column_count

    for item in tsv_rows:
        raw = " ".join(item.get("text", "").split()).strip()
        normalized = normalize_shift(raw)
        if not normalized:
            continue

        try:
            left = float(item.get("left", "0")) * scale_x
            width = float(item.get("width", "0")) * scale_x
        except ValueError:
            continue
        center_x = left + (width / 2)
        day_index = int(center_x // cell_width)
        day_index = max(0, min(column_count - 1, day_index))

        if shifts[day_index] is None:
            shifts[day_index] = normalized
            raw_tokens[day_index] = raw

    return shifts, raw_tokens


def make_debug_overlay(
    original: Image.Image,
    rectified: Image.Image,
    table_box: tuple[int, int, int, int],
    rows: list[dict],
    parsed_rows: list[dict],
) -> str:
    canvas = Image.new("RGB", (DEBUG_IMAGE_SIZE[0] * 2, DEBUG_IMAGE_SIZE[1]), "white")
    original_preview = ImageOps.contain(original.copy(), DEBUG_IMAGE_SIZE, Image.Resampling.BICUBIC)
    rectified_preview = ImageOps.contain(rectified.copy(), DEBUG_IMAGE_SIZE, Image.Resampling.BICUBIC)

    left_offset_y = (DEBUG_IMAGE_SIZE[1] - original_preview.height) // 2
    right_offset_y = (DEBUG_IMAGE_SIZE[1] - rectified_preview.height) // 2
    canvas.paste(original_preview, (0, left_offset_y))
    canvas.paste(rectified_preview, (DEBUG_IMAGE_SIZE[0], right_offset_y))

    draw = ImageDraw.Draw(canvas)

    sx_left = original_preview.width / original.width
    sy_left = original_preview.height / original.height
    left, top, right, bottom = table_box
    draw.rectangle(
        (
            left * sx_left,
            top * sy_left + left_offset_y,
            right * sx_left,
            bottom * sy_left + left_offset_y,
        ),
        outline=(220, 70, 70),
        width=4,
    )

    sx_right = rectified_preview.width / rectified.width
    sy_right = rectified_preview.height / rectified.height

    row_by_id = {row["rowIndex"]: row for row in parsed_rows}
    for row in rows:
        parsed = row_by_id.get(row["rowIndex"])
        recognized = set()
        if parsed:
            recognized = {index for index, shift in enumerate(parsed["shifts"]) if shift}
        for index, box in enumerate(row["cellBoxes"]):
            x1, y1, x2, y2 = box
            color = (28, 124, 122) if index in recognized else (210, 140, 40)
            draw.rectangle(
                (
                    DEBUG_IMAGE_SIZE[0] + (x1 * sx_right),
                    right_offset_y + (y1 * sy_right),
                    DEBUG_IMAGE_SIZE[0] + (x2 * sx_right),
                    right_offset_y + (y2 * sy_right),
                ),
                outline=color,
                width=1,
            )

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def parse_duty_image_bytes(
    image_bytes: bytes,
    filename: str,
    row_index: int | None = None,
    include_debug: bool = False,
) -> dict:
    image = load_image_rgb(image_bytes)
    image, rotation_applied = auto_orient_duty_image(image)
    year, month = guess_month_and_year(filename)

    table_box = detect_table_box(image)
    rectified = rectify_table(image, table_box, DEFAULT_TEMPLATE.table_size)
    rows = build_schedule_boxes(DEFAULT_TEMPLATE)
    target_rows = [item for item in rows if row_index is None or item["rowIndex"] == row_index]

    parsed_rows = []
    for row in target_rows:
        shifts: list[str | None] = [None] * DEFAULT_TEMPLATE.column_count
        raw_tokens: list[str] = [""] * DEFAULT_TEMPLATE.column_count

        # First pass: classify each cell from its center.
        for index, box in enumerate(row["cellBoxes"]):
            cell = rectified.crop(box)
            shift, raw, score, margin = classify_shift_cell(cell)
            if shift is None:
                continue
            shifts[index] = shift
            raw_tokens[index] = raw or shift

        # Second pass: OCR only the ambiguous cells.
        for index, box in enumerate(row["cellBoxes"]):
            if shifts[index] is not None:
                continue
            cell = rectified.crop(box)
            shift, raw = ocr_shift_cell(cell)
            if not shift:
                continue
            shifts[index] = shift
            raw_tokens[index] = raw

        # Third pass: use row-strip OCR only when there are still blanks.
        if any(shift is None for shift in shifts):
            row_top = min(box[1] for box in row["cellBoxes"])
            row_bottom = max(box[3] for box in row["cellBoxes"])
            row_left = min(box[0] for box in row["cellBoxes"])
            row_right = max(box[2] for box in row["cellBoxes"])
            row_strip = rectified.crop((row_left, row_top, row_right, row_bottom))
            strip_shifts, strip_tokens = parse_row_strip(row_strip, DEFAULT_TEMPLATE.column_count)
            for index in range(DEFAULT_TEMPLATE.column_count):
                if shifts[index] is not None:
                    continue
                if strip_shifts[index] is None:
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
            "detectedScheduleBox": [get_schedule_geometry(DEFAULT_TEMPLATE)[key] for key in ("left", "top", "right", "bottom")],
            "rotationApplied": rotation_applied,
        },
    }
    if include_debug:
        result["debugImage"] = make_debug_overlay(image, rectified, table_box, rows, parsed_rows)

    return result
