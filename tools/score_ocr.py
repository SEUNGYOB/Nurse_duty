#!/usr/bin/env python3
"""Run Claude OCR and Google Vision OCR on the sample image, compare against the answer key."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import openpyxl
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(ROOT / "credentials" / "google-service-account.json"))

from ocr.claude_parser import parse_duty_image_with_claude, crop_table_for_claude
from ocr.duty_parser import (
    ROW_NAMES,
    DEFAULT_TEMPLATE,
    detect_table_box,
    rectify_table,
    detect_schedule_line_bounds,
    build_schedule_boxes_from_bounds,
    get_schedule_geometry,
    normalize_shift,
)

SAMPLE_IMAGE = ROOT / "samples" / "주희_duty_june.jpeg"
ANSWER_KEY = ROOT / "samples" / "Correction_result.xlsx"

SHIFT_NORMALIZE = {
    "d": "D", "e": "E", "n": "N", "s": "S", "y": "Y",
    "D": "D", "E": "E", "N": "N", "S": "S", "Y": "Y",
    "off": "OFF", "OFF": "OFF", "of": "OFF",
    "oo": "OFF",
}

NAME_ALIASES: dict[str, str] = {
    "장혜진": "장해진",
    "유정수": "유정숙",
    "양임향": "양일향",
}


def load_answer_key() -> dict[str, list[str | None]]:
    wb = openpyxl.load_workbook(ANSWER_KEY)
    ws = wb.active
    answer: dict[str, list[str | None]] = {}
    for row in ws.iter_rows(values_only=True):
        if row[0] is None:
            continue
        name = str(row[1]).strip()
        shifts = []
        for value in row[2:32]:
            if value is None:
                shifts.append(None)
            else:
                raw = str(value).strip()
                shifts.append(SHIFT_NORMALIZE.get(raw, raw.upper()))
        answer[name] = shifts
    return answer


def normalize_ocr_shift(value: str | None) -> str | None:
    if value is None:
        return None
    return SHIFT_NORMALIZE.get(value, value.upper())


# ── Google Vision (full-table, single API call) ──────────────────────────────

def _google_vision_words(image: Image.Image) -> list[dict]:
    from google.cloud import vision as gv
    client = gv.ImageAnnotatorClient()
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=92)
    response = client.document_text_detection(image=gv.Image(content=buf.getvalue()))
    if response.error and response.error.message:
        raise RuntimeError(f"Google Vision error: {response.error.message}")
    words = []
    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                for word in para.words:
                    text = "".join(s.text for s in word.symbols).strip()
                    if not text:
                        continue
                    xs = [v.x or 0 for v in word.bounding_box.vertices]
                    ys = [v.y or 0 for v in word.bounding_box.vertices]
                    words.append({
                        "text": text,
                        "cx": (min(xs) + max(xs)) / 2,
                        "cy": (min(ys) + max(ys)) / 2,
                    })
    return words


def _build_column_centers(words: list[dict], img_height: int) -> dict[int, float]:
    """헤더 행의 날짜 숫자(1~30)를 찾아 각 컬럼 중심 x좌표를 반환."""
    # 헤더는 이미지 상단 15% 안에 있음
    header_max_y = img_height * 0.15
    # 이름 컬럼 제외: 표 너비의 5% 이상인 x만 허용
    min_x = img_height * 0.1  # 세로 기준으로 대략적인 여백

    candidates: dict[int, list[float]] = {}
    for w in words:
        if w["cy"] > header_max_y or w["cx"] < min_x:
            continue
        try:
            n = int(w["text"])
            if 1 <= n <= 30:
                candidates.setdefault(n, []).append(w["cx"])
        except ValueError:
            pass

    # 각 숫자별로 후보가 여럿이면 선형 트렌드에 가장 가까운 것 선택
    # 1단계: 후보가 1개인 것들로 초기 트렌드 피팅
    col_cx: dict[int, float] = {}
    for n, cxs in candidates.items():
        if len(cxs) == 1:
            col_cx[n] = cxs[0]

    if len(col_cx) >= 2:
        # 선형 회귀: cx = a * day + b
        days = sorted(col_cx)
        xs_fit = [col_cx[d] for d in days]
        n_fit = len(days)
        mean_d = sum(days) / n_fit
        mean_x = sum(xs_fit) / n_fit
        slope = sum((d - mean_d) * (x - mean_x) for d, x in zip(days, xs_fit)) / \
                sum((d - mean_d) ** 2 for d in days)
        intercept = mean_x - slope * mean_d

        # 중복 후보는 선형 예측에 가장 가까운 것 선택
        for n, cxs in candidates.items():
            if n not in col_cx:
                expected = slope * n + intercept
                col_cx[n] = min(cxs, key=lambda x: abs(x - expected))

    return col_cx


def _nearest_day(cx: float, col_cx: dict[int, float]) -> int | None:
    """cx에 가장 가까운 day 번호 반환 (컬럼 폭의 절반 이내만)."""
    if not col_cx:
        return None
    best_day = min(col_cx, key=lambda d: abs(col_cx[d] - cx))
    col_width = (max(col_cx.values()) - min(col_cx.values())) / (len(col_cx) - 1) if len(col_cx) > 1 else 100
    if abs(col_cx[best_day] - cx) > col_width * 0.6:
        return None
    return best_day


def _build_row_y_centers(words: list[dict], img_height: int) -> list[tuple[float, int]]:
    """각 간호사 행의 y중심과 rowIndex를 추정."""
    from ocr.duty_parser import ROW_NAMES
    header_max_y = img_height * 0.15

    name_hits: dict[int, list[float]] = {}
    for w in words:
        if w["cy"] <= header_max_y:
            continue
        for idx, name in enumerate(ROW_NAMES):
            if w["text"] == name:
                name_hits.setdefault(idx + 1, []).append(w["cy"])

    row_centers = []
    for row_idx, cys in sorted(name_hits.items()):
        row_centers.append((sum(cys) / len(cys), row_idx))
    return row_centers


def parse_duty_image_with_google_full(image_bytes: bytes) -> dict:
    """헤더 숫자로 컬럼 x좌표, grid line으로 행 y범위를 잡아 셀 매핑."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    table_box = detect_table_box(image)
    rectified = rectify_table(image, table_box, DEFAULT_TEMPLATE.table_size)

    words = _google_vision_words(rectified)
    img_w, img_h = rectified.size

    # 헤더에서 컬럼 중심 x 좌표 구축
    col_cx = _build_column_centers(words, img_h)

    # y_bounds는 grid line 감지로 (더 안정적)
    # y_bounds는 schedule 영역 내 상대 좌표 → rectified 절대 좌표로 변환
    _, y_bounds, _ = detect_schedule_line_bounds(rectified, DEFAULT_TEMPLATE)
    geometry = get_schedule_geometry(DEFAULT_TEMPLATE)
    y_offset = int(geometry["top"])
    slots = DEFAULT_TEMPLATE.row_slots
    row_y_bounds: dict[int, tuple[int, int]] = {}
    for row_idx, slot in enumerate(slots):
        row_y_bounds[row_idx + 1] = (y_offset + y_bounds[slot], y_offset + y_bounds[slot + 1])

    # shift 단어들을 (row_idx, day) 에 매핑
    shifts_by_row: dict[int, list[str | None]] = {i + 1: [None] * 30 for i in range(16)}
    raw_by_row:    dict[int, list[str]]        = {i + 1: [""] * 30    for i in range(16)}

    for w in words:
        normalized = normalize_shift(w["text"])
        if not normalized:
            continue
        cx, cy = w["cx"], w["cy"]

        # 어느 행?
        matched_row = None
        for row_idx, (y_top, y_bot) in row_y_bounds.items():
            if y_top <= cy <= y_bot:
                matched_row = row_idx
                break
        if matched_row is None:
            continue

        # 어느 컬럼?
        day = _nearest_day(cx, col_cx)
        if day is None:
            continue

        day_idx = day - 1
        if shifts_by_row[matched_row][day_idx] is None:
            shifts_by_row[matched_row][day_idx] = normalized
            raw_by_row[matched_row][day_idx] = w["text"]

    parsed_rows = []
    for i, name in enumerate(ROW_NAMES):
        ri = i + 1
        shifts = shifts_by_row[ri]
        recognized = sum(1 for s in shifts if s)
        parsed_rows.append({
            "rowIndex": ri,
            "name": name,
            "recognizedDays": recognized,
            "shifts": shifts,
            "rawTokens": raw_by_row[ri],
        })

    return {"rows": parsed_rows}


# ── Scoring ──────────────────────────────────────────────────────────────────

def score(label: str, ocr_result: dict, answer: dict[str, list[str | None]]) -> None:
    raw_rows = {row["name"]: row["shifts"] for row in ocr_result["rows"]}
    rows: dict[str, list[str | None]] = dict(raw_rows)
    for answer_name, ocr_name in NAME_ALIASES.items():
        if ocr_name in raw_rows:
            rows[answer_name] = raw_rows[ocr_name]

    total_cells = correct_cells = null_cells = 0

    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(f"{'Name':<10} {'Correct':>7} {'Wrong':>6} {'Null':>5} {'Total':>6} {'Acc':>7}")
    print("-" * 50)

    for name, expected in answer.items():
        predicted = rows.get(name, [None] * 30)
        row_correct = row_wrong = row_null = 0
        wrong_days = []

        for day_idx, (exp, pred) in enumerate(zip(expected, predicted), start=1):
            if exp is None:
                continue
            total_cells += 1
            pred_norm = normalize_ocr_shift(pred)
            if pred_norm is None:
                null_cells += 1
                row_null += 1
            elif pred_norm == exp:
                correct_cells += 1
                row_correct += 1
            else:
                row_wrong += 1
                wrong_days.append(f"day{day_idx}:{pred_norm}(exp:{exp})")

        row_total = row_correct + row_wrong + row_null
        acc = row_correct / row_total * 100 if row_total else 0
        flag = "" if row_wrong == 0 and row_null == 0 else " ✗"
        print(f"{name:<10} {row_correct:>7} {row_wrong:>6} {row_null:>5} {row_total:>6} {acc:>6.1f}%{flag}")
        if wrong_days:
            print(f"  오답: {', '.join(wrong_days[:8])}{'...' if len(wrong_days) > 8 else ''}")

    print("-" * 50)
    acc_total = correct_cells / total_cells * 100 if total_cells else 0
    null_pct  = null_cells / total_cells * 100 if total_cells else 0
    print(f"{'TOTAL':<10} {correct_cells:>7} {total_cells-correct_cells-null_cells:>6} {null_cells:>5} {total_cells:>6} {acc_total:>6.1f}%")
    print(f"\n  정답률: {acc_total:.1f}%  |  미인식: {null_pct:.1f}%  ({null_cells}/{total_cells})")


def main() -> None:
    image_bytes = SAMPLE_IMAGE.read_bytes()
    answer = load_answer_key()

    # ── Claude (baseline) ──
    print("\n[1/3] Claude Vision (기본) 실행 중...")
    claude_result = parse_duty_image_with_claude(image_bytes, SAMPLE_IMAGE.name)
    score("Claude Vision — 기본 (재판독 없음)", claude_result, answer)

    # ── Claude (refine row 4) ──
    print("\n[2/3] Claude Vision (rowIndex=4 재판독) 실행 중...")
    claude_refined = parse_duty_image_with_claude(
        image_bytes, SAMPLE_IMAGE.name, refine_row_indices=[4]
    )
    score("Claude Vision — 재판독 rowIndex=4 (장해진)", claude_refined, answer)

    # 저장
    out_dir = ROOT / "scratch" / "ocr-debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result_claude.json").write_text(json.dumps(claude_result, ensure_ascii=False, indent=2))
    (out_dir / "result_claude_refined.json").write_text(json.dumps(claude_refined, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: result_claude.json, result_claude_refined.json")


if __name__ == "__main__":
    main()
