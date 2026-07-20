"""곡면 근무표에서 간호사 행을 곡선 격자선 기준으로 펴서(dewarp) 추출한다.

재판독(refine) 경로 전용. 프로덕션 baseline은 건드리지 않는다.
scipy 없이 numpy + _morphology shim 만으로 동작해야 서버리스 번들에서 안전하다.

핵심 아이디어:
- 흐리고 휜 가로 격자선을 1D 투영으로 잡으면 뭉개진다.
- x 구간(band)별로 국소 피크를 찾고, 곡선으로 링킹해 2차 다항식으로 피팅하면
  어느 x에서든 잡히는 선을 복원할 수 있다(라인 병합/누락 방지).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import _morphology as ndimage
from .duty_parser import DutySheetTemplate, DEFAULT_TEMPLATE


def _uniform2d(arr: np.ndarray, size: int) -> np.ndarray:
    out = ndimage.uniform_filter1d(arr, size, axis=0)
    return ndimage.uniform_filter1d(out, size, axis=1)


def _find_peaks_1d(col: np.ndarray, height: float, distance: int) -> list[int]:
    """단순 1D 피크: height 이상 국소 최대값, 최소 간격 distance."""
    n = col.size
    cand = [i for i in range(1, n - 1)
            if col[i] >= height and col[i] >= col[i - 1] and col[i] >= col[i + 1]]
    cand.sort(key=lambda i: col[i], reverse=True)
    kept: list[int] = []
    for i in cand:
        if all(abs(i - j) >= distance for j in kept):
            kept.append(i)
    return sorted(kept)


def detect_horizontal_curves(rectified: Image.Image) -> list[np.poly1d]:
    """rectified 이미지 전체에서 가로 격자선을 2차 다항식 곡선 리스트로 반환.

    스케줄 crop이 아닌 전체 이미지에서 검출한다(우측 끝 edge effect 회피).
    """
    arr = np.asarray(rectified.convert("L"), dtype=float)
    H, W = arr.shape

    # 얇은 어두운 구조(격자선/글자) 강조: 넓은 blur 대비 어두운 정도
    bg = _uniform2d(arr, 41)
    dark = np.clip(bg - arr, 0.0, None)
    # 수평 방향 평균으로 글자(폭 ~40px)는 죽이고 전폭 선만 살림
    line_resp = ndimage.uniform_filter1d(dark, 151, axis=1)

    x0, x1 = int(0.10 * W), int(0.95 * W)
    nb = 40
    edges = np.linspace(x0, x1, nb + 1).astype(int)
    xc = [(edges[i] + edges[i + 1]) // 2 for i in range(nb)]

    band_peaks: list[list[int]] = []
    for i in range(nb):
        col = line_resp[:, edges[i]:edges[i + 1]].mean(axis=1)
        thr = col.mean() + 0.8 * col.std()
        band_peaks.append(_find_peaks_1d(col, thr, distance=40))

    # 센터 밴드 시드 → 좌우로 최근접 링킹(기울기 제약)
    c = nb // 2
    lines: dict[int, dict[int, int]] = {k: {c: y} for k, y in enumerate(band_peaks[c])}

    def extend(rng) -> None:
        prev = {k: v[c] for k, v in lines.items()}
        for i in rng:
            used: set[int] = set()
            for k in list(prev):
                cand = [(abs(p - prev[k]), p) for p in band_peaks[i]
                        if p not in used and abs(p - prev[k]) < 22]
                if cand:
                    _, p = min(cand)
                    lines[k][i] = p
                    used.add(p)
                    prev[k] = p

    extend(range(c - 1, -1, -1))
    extend(range(c + 1, nb))

    polys: list[np.poly1d] = []
    for v in lines.values():
        if len(v) < 10:  # 근거 부족한 트랙 버림
            continue
        xs = np.array([xc[i] for i in v], dtype=float)
        ys = np.array([v[i] for i in v], dtype=float)
        polys.append(np.poly1d(np.polyfit(xs, ys, 2)))

    polys.sort(key=lambda p: float(p(W / 2)))
    return polys


def select_nurse_grid(
    polys: list[np.poly1d],
    template: DutySheetTemplate = DEFAULT_TEMPLATE,
    *,
    width: int,
    height: int,
) -> list[np.poly1d]:
    """검출된 곡선들 중 간호사 데이터 영역 격자선을 고른다.

    스케줄 top 위의 범례/날짜/요일 헤더 선은 건너뛰고, 1행 상단부터 아래로
    넉넉히(빈 분리행 포함) 반환한다. 빈 행 skip은 build_nurse_bands에서 처리.
    """
    schedule_top = template.schedule_box[1] * height
    ys = [float(p(width / 2)) for p in polys]
    start = next((i for i, y in enumerate(ys) if y > schedule_top + 110), 0)
    return polys[start:]

def _bilinear_sample(arr: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """numpy 바이리니어 샘플링 (scipy.map_coordinates 대체)."""
    H, W = arr.shape
    y0 = np.clip(np.floor(ys).astype(int), 0, H - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, W - 1)
    y1 = np.clip(y0 + 1, 0, H - 1)
    x1 = np.clip(x0 + 1, 0, W - 1)
    wy = np.clip(ys - y0, 0.0, 1.0)
    wx = np.clip(xs - x0, 0.0, 1.0)
    top = arr[y0, x0] * (1 - wx) + arr[y0, x1] * wx
    bot = arr[y1, x0] * (1 - wx) + arr[y1, x1] * wx
    return top * (1 - wy) + bot * wy


def dewarp_row(
    rectified: Image.Image,
    top_poly: np.poly1d,
    bottom_poly: np.poly1d,
    *,
    out_h: int = 90,
    out_w: int = 1600,
    pad: float = 0.12,
    x_left_px: float | None = None,
    x_right_px: float | None = None,
    x_left_ratio: float = 0.112,
    x_right_ratio: float = 0.973,
) -> Image.Image:
    """한 행(위/아래 곡선 사이)을 평평한 스트립으로 편다.

    x_left_px / x_right_px 를 주면 그 절대 픽셀 범위(날짜셀 좌우 경계)를 쓴다.
    안 주면 ratio 기본값(6월/7월 시트의 날짜셀 범위)을 쓴다.
    """
    arr = np.asarray(rectified.convert("L"), dtype=float)
    H, W = arr.shape
    oy, ox = np.mgrid[0:out_h, 0:out_w]
    fx = ox / (out_w - 1)
    fy = oy / (out_h - 1)
    x0 = x_left_px if x_left_px is not None else x_left_ratio * W
    x1 = x_right_px if x_right_px is not None else x_right_ratio * W
    src_x = x0 + fx * (x1 - x0)
    t = top_poly(src_x)
    b = bottom_poly(src_x)
    ext = (b - t) * pad
    src_y = (t - ext) + fy * ((b + ext) - (t - ext))
    strip = _bilinear_sample(arr, src_y, src_x)
    return Image.fromarray(strip.astype("uint8"))

def day_number_band(
    polys: list[np.poly1d],
    template: DutySheetTemplate = DEFAULT_TEMPLATE,
    *,
    width: int,
    height: int,
) -> tuple[np.poly1d, np.poly1d] | None:
    """날짜번호 헤더행(1..N)의 위/아래 곡선.

    날짜번호행은 항상 1행 위 헤더 영역의 **최상단 밴드**다(6월·7월 공통). 헤더행
    개수가 시트마다 달라도(6월 2개, 7월 3개) 최상단 = 날짜번호로 안정적.
    (제목·범례는 전폭 선이 아니라 검출되지 않으므로 polys[0]가 날짜번호 상단.)
    """
    schedule_top = template.schedule_box[1] * height
    ys = [float(p(width / 2)) for p in polys]
    start = next((i for i, y in enumerate(ys) if y > schedule_top + 110), None)
    if start is None or start < 2:
        return None
    return polys[0], polys[1]


def stack_with_header(header: Image.Image, row: Image.Image) -> Image.Image:
    """헤더행 위 + 대상행 아래로 세로 결합(같은 폭)."""
    w = max(header.width, row.width)
    out = Image.new("L", (w, header.height + row.height + 4), 255)
    out.paste(header.convert("L"), (0, 0))
    out.paste(row.convert("L"), (0, header.height + 4))
    return out

def _day_span(strip: Image.Image, column_count: int, pitch: float,
              day_left_out: float, day_right_out: float) -> tuple[int, int] | None:
    """스트립에서 날짜 토큰 영역 [day1 왼쪽, dayN 오른쪽]을 찾는다(이름칼럼 제외).

    1) 예상 날짜 영역 창(window) 안의 토큰만 first~last 로 잡는다(6월은 이름이
       창 밖이라 여기서 제외됨 → 안정).
    2) 그래도 폭이 칸수보다 크게 넓으면 이름이 창 안까지 들어온 것(7월) → dayN
       오른쪽 끝 기준으로 (칸수×pitch)만큼만 남기고 왼쪽(이름)을 잘라낸다.
    """
    a = np.asarray(strip.convert("L"), dtype=float)
    H = a.shape[0]
    mid = a[int(H * 0.22):int(H * 0.78), :]
    ink = ndimage.uniform_filter1d((mid < (mid.mean() - 0.6 * mid.std())).sum(axis=0).astype(float), 9)
    has = ink > 0.12 * ink.max()
    runs = []
    i = 0
    while i < has.size:
        if has[i]:
            j = i
            while j < has.size and has[j]:
                j += 1
            if j - i > pitch * 0.15:
                runs.append((i, j))
            i = j
        else:
            i += 1
    day_runs = [r for r in runs
                if day_left_out - 1.3 * pitch <= (r[0] + r[1]) / 2 <= day_right_out + 0.3 * pitch]
    if not day_runs:
        return None
    # 창(window) 안 first~last. 이름 잔편이 좌측에 조금 남아도 헤더(날짜번호)를
    # 함께 스택하므로 Claude가 번호로 정렬해 무해. (좌측 트림은 digit 분할로
    # 폭 오판→day1 잘림 위험이 커서 쓰지 않는다.)
    return int(day_runs[0][0]), int(day_runs[-1][1])


def build_header_stacked_strips(
    rectified: Image.Image,
    polys: list[np.poly1d],
    grid: list[np.poly1d],
    *,
    column_count: int,
    day_left_px: float,
    day_right_px: float,
    out_h: int = 90,
    out_w: int = 1800,
    n_rows: int = 16,
    template: DutySheetTemplate = DEFAULT_TEMPLATE,
) -> list[Image.Image] | None:
    """각 행을 dewarp → 자신의 날짜 토큰 범위로 크롭 → 날짜번호 헤더를 위에 스택.

    핵심: 헤더와 각 행을 **각자의 day1~dayN 범위**로 크롭하므로, 곡률로 열 x가
    행마다 달라도 둘 다 day1부터 시작해 정렬된다(고정 x-범위의 한계 극복).
    모든 칸엔 내용이 있어 k번째 토큰 = day k → Claude가 순서대로 읽는다.
    """
    hdr = day_number_band(polys, template, width=rectified.width, height=rectified.height)
    if hdr is None:
        return None
    cw = (day_right_px - day_left_px) / column_count
    # 곡률로 아래 행 day1이 왼쪽으로 밀리므로 왼쪽 마진 넉넉히
    xL = day_left_px - 1.6 * cw
    xR = day_right_px + 0.6 * cw
    cap_w = int((xR - xL) / cw * 60)          # dewarp 캔버스(셀당 ~60px)
    pit = cw / (xR - xL) * cap_w
    d_lo = (day_left_px - xL) / (xR - xL) * cap_w
    d_ro = (day_right_px - xL) / (xR - xL) * cap_w

    pad = int(pit * 0.12)

    def span_crop(strip: Image.Image, target_width: float | None = None) -> Image.Image:
        sp = _day_span(strip, column_count, pit, d_lo, d_ro)
        if sp is None:
            return strip
        l, r = sp
        # 이름이 섞여 폭이 헤더폭+1칸 넘게 크면 오른쪽 기준으로 이름을 잘라낸다.
        # (헤더 실측폭 사용 → nominal pitch 오차로 인한 day1 잘림 회피. 6월 잔편은
        #  임계 이하라 트림 안 됨.)
        if target_width is not None and (r - l) > target_width + pit:
            l = r - target_width
        return strip.crop((max(0, int(l) - pad), 0, min(strip.width, int(r) + pad), strip.height))

    header_full = dewarp_row(rectified, hdr[0], hdr[1], out_h=60, out_w=cap_w, x_left_px=xL, x_right_px=xR, pad=0.05)
    hspan = _day_span(header_full, column_count, pit, d_lo, d_ro)
    header_width = (hspan[1] - hspan[0]) if hspan else column_count * pit
    hs = span_crop(header_full).resize((out_w, 60))

    out = []
    for row_index in range(1, n_rows + 1):
        slot = template.row_slots[row_index - 1]
        if slot + 1 >= len(grid):
            return None
        rs = span_crop(
            dewarp_row(rectified, grid[slot], grid[slot + 1], out_h=out_h, out_w=cap_w, x_left_px=xL, x_right_px=xR),
            target_width=header_width,
        ).resize((out_w, out_h))
        out.append(stack_with_header(hs, rs))
    return out

def day_x_range(rectified: Image.Image, x_bounds, template: DutySheetTemplate = DEFAULT_TEMPLATE):
    """detect_schedule_line_bounds의 x_bounds → 날짜셀 좌/우 절대 픽셀."""
    W, _ = rectified.size
    schedule_left = int(round(template.schedule_box[0] * template.table_size[0]))
    return schedule_left + x_bounds[0], schedule_left + x_bounds[-1]
