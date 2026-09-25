"""Offset and clock drift between two recordings of the same event.

Result model: the other file's time ``t`` lies at reference file time

    ref_time(t) = offset_s + t / (1 + drift_ppm * 1e-6)

so ``drift_ppm`` is how much faster the other device's clock runs than the
reference's. This matches the model in :mod:`chronon.synth`.

Method
------
1. Coarse: a few long windows of the other file are correlated against the whole
   reference at a low rate. The lag most windows agree on becomes the anchor.
2. Fine: many short windows along the other file are correlated against a narrow
   search range of the reference around the anchor, with sub-sample peak
   interpolation. Each window yields (time, lag).
3. Fit: a robust straight line through lag over time gives offset (intercept)
   and drift (slope).
4. Refine: steps 2 and 3 again, with every window resampled by the measured drift
   so the stretch does not smear the correlation peak.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import signal as sps

from chronon import audio

ANALYSIS_RATE = 16_000
COARSE_RATE = 2_000
MAX_DRIFT_PPM = 500.0

COARSE_WINDOW_S = 30.0
COARSE_WINDOWS = 5
FINE_WINDOW_S = 4.0
FINE_WINDOWS = 64
COARSE_TOLERANCE_S = 0.05
REFINE_MARGIN_S = 0.005


@dataclass(frozen=True)
class Alignment:
    offset_s: float
    drift_ppm: float
    confidence: float
    inverted: bool
    windows_used: int
    windows_total: int

    def ref_time(self, t: float | np.ndarray) -> float | np.ndarray:
        """Reference file time of the other file's time ``t``."""
        return self.offset_s + t / (1 + self.drift_ppm * 1e-6)


@dataclass(frozen=True)
class _Match:
    t: float  # position in the other file (s) the lag holds at
    lag: float  # reference time minus other time (s)
    ncc: float  # signed normalised correlation at the peak


def align(ref: np.ndarray, other: np.ndarray, rate: int = ANALYSIS_RATE) -> Alignment:
    """Align ``other`` to ``ref``; both mono at ``rate`` Hz."""
    ref = np.asarray(ref, dtype=np.float64)
    other = np.asarray(other, dtype=np.float64)
    anchor = _coarse(
        audio.resample(ref, rate, COARSE_RATE),
        audio.resample(other, rate, COARSE_RATE),
        COARSE_RATE,
    )
    t0, lag0 = anchor
    matches, total = _fine(
        ref,
        other,
        rate,
        lag_at=lambda t: lag0,
        margin_at=lambda t: COARSE_TOLERANCE_S + MAX_DRIFT_PPM * 1e-6 * abs(t - t0),
    )
    first = _fit(matches, total)
    # second pass: undo the measured drift inside each window and search narrowly
    stretch = 1 / (1 + first.drift_ppm * 1e-6)
    matches, total = _fine(
        ref,
        other,
        rate,
        lag_at=lambda t: first.ref_time(t) - t,
        margin_at=lambda t: REFINE_MARGIN_S,
        stretch=stretch,
    )
    return _fit(matches, total) if matches else first


def align_files(
    ref_path: Path | str, paths: list[Path | str], rate: int = ANALYSIS_RATE
) -> list[Alignment]:
    ref = audio.load(ref_path, rate)
    return [align(ref, audio.load(p, rate), rate) for p in paths]


# --- correlation -----------------------------------------------------------


def _ncc_valid(ref: np.ndarray, win: np.ndarray) -> np.ndarray:
    """Normalised correlation of ``win`` at every start position inside ``ref``."""
    c = sps.fftconvolve(ref, win[::-1], mode="valid")
    cs = np.concatenate([[0.0], np.cumsum(np.square(ref))])
    local = cs[len(win) :] - cs[: len(cs) - len(win)]
    e_win = float(np.dot(win, win))
    floor = 1e-4 * cs[-1] / max(len(ref), 1) * len(win)  # ignore near-silent stretches
    return c / np.sqrt(e_win * np.maximum(local, floor) + 1e-30)


def _parabolic(y: np.ndarray, k: int) -> float:
    if k <= 0 or k >= len(y) - 1:
        return 0.0
    a, b, c = y[k - 1], y[k], y[k + 1]
    denom = a - 2 * b + c
    return 0.0 if denom == 0 else 0.5 * (a - c) / denom


def _energy_ok(win: np.ndarray, mean_power: float) -> bool:
    return float(np.mean(np.square(win))) > 1e-3 * mean_power


# --- stages ----------------------------------------------------------------


def _coarse(ref: np.ndarray, other: np.ndarray, rate: int) -> tuple[float, float]:
    """(position in other, lag) in seconds for the best-supported coarse window."""
    win = min(len(other), int(COARSE_WINDOW_S * rate))
    pad = np.zeros(win)
    padded = np.concatenate([pad, ref, pad])  # lets the other file start before/after ref
    count = min(COARSE_WINDOWS, 1 + (len(other) - win) // max(win // 2, 1))
    mean_power = float(np.mean(np.square(other))) if len(other) else 0.0
    cands = []
    for p in np.linspace(0, len(other) - win, count).astype(int):
        seg = other[p : p + win]
        if not _energy_ok(seg, mean_power):
            continue
        ncc = _ncc_valid(padded, seg)
        k = int(np.argmax(np.abs(ncc)))
        cands.append((p / rate, (k - win - p) / rate, abs(ncc[k])))
    if not cands:
        raise ValueError("no usable audio in the file to align")

    def support(c):
        return sum(
            s
            for t, lag, s in cands
            if abs(lag - c[1]) <= COARSE_TOLERANCE_S + MAX_DRIFT_PPM * 1e-6 * abs(t - c[0])
        )

    best = max(cands, key=lambda c: (support(c), c[2]))
    return best[0], best[1]


def _fine(
    ref: np.ndarray,
    other: np.ndarray,
    rate: int,
    lag_at: Callable[[float], float],
    margin_at: Callable[[float], float],
    stretch: float = 1.0,
) -> tuple[list[_Match], int]:
    """Correlate short windows of ``other`` against ``ref`` near the predicted lag.

    ``stretch`` is the expected reference time per unit of other time (``1 + slope``);
    each window is resampled by it so drift does not smear the correlation peak.
    """
    win = min(int((len(other) - 2) * stretch), int(FINE_WINDOW_S * rate))
    span = int(np.ceil(win / stretch)) + 1  # other samples a window covers
    count = min(FINE_WINDOWS, 1 + (len(other) - span) // max(span // 2, 1))
    mean_power = float(np.mean(np.square(other)))
    grid = np.arange(win) / stretch
    matches = []
    for p in np.linspace(0, len(other) - span, count).astype(int):
        seg = np.interp(p + grid, np.arange(p, p + span), other[p : p + span])
        if not _energy_ok(seg, mean_power):
            continue
        t, margin = p / rate, margin_at(p / rate)
        lo = max(0, int(np.floor((t + lag_at(t) - margin) * rate)))
        hi = min(len(ref), int(np.ceil((t + lag_at(t) + margin) * rate)) + win)
        if hi - lo < win:
            continue  # window lies outside the reference
        ncc = _ncc_valid(ref[lo:hi], seg)
        k = int(np.argmax(np.abs(ncc)))
        sign = 1.0 if ncc[k] >= 0 else -1.0
        start = lo + k + _parabolic(sign * ncc, k)  # reference sample where seg starts
        # report the lag at the window's energy centroid, where any residual
        # stretch cancels out
        power = np.square(seg)
        c = float(np.dot(np.arange(win), power) / power.sum())
        t_c = p + c / stretch
        matches.append(_Match(t_c / rate, (start + c - t_c) / rate, float(ncc[k])))
    return matches, count


def _fit(matches: list[_Match], total: int) -> Alignment:
    if not matches:
        raise ValueError("the files do not seem to overlap")
    t = np.array([m.t for m in matches])
    lag = np.array([m.lag for m in matches])
    w = np.abs([m.ncc for m in matches])
    inlier = np.ones(len(t), dtype=bool)
    slope, intercept = 0.0, float(np.median(lag))
    for _ in range(5):
        if inlier.sum() >= 2 and np.ptp(t[inlier]) > 0:
            slope, intercept = np.polyfit(t[inlier], lag[inlier], 1, w=w[inlier])
        else:
            slope, intercept = 0.0, float(np.median(lag[inlier]))
        resid = np.abs(lag - (intercept + slope * t))
        mad = float(np.median(resid[inlier]))
        new = resid <= max(4 * 1.4826 * mad, 2e-4)  # never tighter than 0.2 ms
        if np.array_equal(new, inlier) or new.sum() == 0:
            break
        inlier = new
    ncc = np.array([m.ncc for m in matches])[inlier]
    return Alignment(
        offset_s=float(intercept),
        drift_ppm=float((1 / (1 + slope) - 1) * 1e6),
        confidence=float(np.median(np.abs(ncc)) * inlier.sum() / len(matches)),
        inverted=bool(np.sum(ncc < 0) > len(ncc) / 2),
        windows_used=int(inlier.sum()),
        windows_total=total,
    )
