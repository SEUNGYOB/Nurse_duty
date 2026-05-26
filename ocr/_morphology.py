"""Pure-numpy drop-in for the scipy.ndimage operations used in duty_parser."""
from __future__ import annotations

import numpy as np


# ── internal helpers ──────────────────────────────────────────────────────────

def _cumsum_window(arr: np.ndarray, size: int, axis: int, pad_mode: str) -> np.ndarray:
    """Return the rolling sum of *arr* along *axis* with window *size*."""
    n = arr.shape[axis]
    pad_l = (size - 1) // 2
    pad_r = size - 1 - pad_l
    pw = [(0, 0)] * arr.ndim
    pw[axis] = (pad_l, pad_r)
    padded = np.pad(arr.astype(np.int32), pw, mode=pad_mode)
    cs = np.cumsum(padded, axis=axis)
    z_shape = list(arr.shape)
    z_shape[axis] = 1
    cs = np.concatenate([np.zeros(z_shape, dtype=np.int32), cs], axis=axis)
    sl_end = [slice(None)] * arr.ndim
    sl_start = [slice(None)] * arr.ndim
    sl_end[axis] = slice(size, size + n)
    sl_start[axis] = slice(0, n)
    return cs[tuple(sl_end)] - cs[tuple(sl_start)]


def _erode_axis(arr: np.ndarray, size: int, axis: int) -> np.ndarray:
    if size <= 1:
        return arr.astype(bool).copy()
    return _cumsum_window(arr, size, axis, "constant") == size


def _dilate_axis(arr: np.ndarray, size: int, axis: int) -> np.ndarray:
    if size <= 1:
        return arr.astype(bool).copy()
    return _cumsum_window(arr, size, axis, "constant") > 0


def _structure_axes(structure: np.ndarray) -> tuple[int, int]:
    """Return (h, w) of a 2-D structuring element."""
    if structure.ndim == 2:
        return int(structure.shape[0]), int(structure.shape[1])
    if structure.ndim == 1:
        return 1, int(structure.shape[0])
    raise ValueError(f"Unsupported structure ndim={structure.ndim}")


# ── public API (matches scipy.ndimage signatures used in duty_parser) ─────────

def binary_opening(
    arr: np.ndarray,
    structure: np.ndarray,
    *,
    iterations: int = 1,
) -> np.ndarray:
    sh, sw = _structure_axes(structure)
    result = arr.astype(bool)
    for _ in range(iterations):
        if sh > 1:
            result = _erode_axis(result, sh, 0)
        if sw > 1:
            result = _erode_axis(result, sw, 1)
        if sh > 1:
            result = _dilate_axis(result, sh, 0)
        if sw > 1:
            result = _dilate_axis(result, sw, 1)
    return result


def binary_dilation(
    arr: np.ndarray,
    structure: np.ndarray,
    *,
    iterations: int = 1,
) -> np.ndarray:
    sh, sw = _structure_axes(structure)
    result = arr.astype(bool)
    for _ in range(iterations):
        if sh > 1:
            result = _dilate_axis(result, sh, 0)
        if sw > 1:
            result = _dilate_axis(result, sw, 1)
    return result


def uniform_filter1d(
    arr: np.ndarray,
    size: int,
    axis: int = 0,
    mode: str = "reflect",
) -> np.ndarray:
    n = arr.shape[axis]
    pad_l = (size - 1) // 2
    pad_r = size - 1 - pad_l
    pw = [(0, 0)] * arr.ndim
    pw[axis] = (pad_l, pad_r)
    # scipy mode='nearest' == numpy mode='edge'
    np_mode = "edge" if mode == "nearest" else mode
    padded = np.pad(arr.astype(float), pw, mode=np_mode)
    cs = np.cumsum(padded, axis=axis)
    z_shape = list(arr.shape)
    z_shape[axis] = 1
    cs = np.concatenate([np.zeros(z_shape), cs], axis=axis)
    sl_end = [slice(None)] * arr.ndim
    sl_start = [slice(None)] * arr.ndim
    sl_end[axis] = slice(size, size + n)
    sl_start[axis] = slice(0, n)
    return (cs[tuple(sl_end)] - cs[tuple(sl_start)]) / size


def _conv1d(x: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    """1-D convolution with symmetric padding (matches scipy.ndimage mode='reflect')."""
    pad = len(kernel) // 2
    pw = [(0, 0)] * x.ndim
    pw[axis] = (pad, pad)
    # scipy ndimage mode='reflect' pads including the edge pixel = numpy 'symmetric'
    p = np.pad(x, pw, mode="symmetric")
    out = np.zeros_like(x, dtype=float)
    for i, k in enumerate(kernel):
        sl = [slice(None)] * x.ndim
        sl[axis] = slice(i, i + x.shape[axis])
        out += k * p[tuple(sl)]
    return out


def sobel(arr: np.ndarray, axis: int = 0) -> np.ndarray:
    """Sobel edge filter (separable, matches scipy.ndimage.sobel default mode='reflect')."""
    a = arr.astype(float)
    smooth = np.array([1.0, 2.0, 1.0])
    # scipy correlate1d uses kernel [-1,0,1] (forward difference)
    diff = np.array([-1.0, 0.0, 1.0])
    if axis == 0:
        return _conv1d(_conv1d(a, smooth, axis=1), diff, axis=0)
    return _conv1d(_conv1d(a, smooth, axis=0), diff, axis=1)
