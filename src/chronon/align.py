"""Offset and clock drift between two recordings of the same event.

Result model: the other file's time ``t`` lies at reference file time

    ref_time(t) = offset_s + t / (1 + drift_ppm * 1e-6)

so ``drift_ppm`` is how much faster the other device's clock runs than the
reference's. This matches the model in :mod:`chronon.synth`.

Method
------
1. Coarse: a few long windows of the other file are correlated against the whole
   reference at a low rate. The lag most windows agree on becomes the anchor.
2. Fine: short windows every ``FINE_STEP_S`` along the other file are correlated
   against a search range of the reference around the anchor, with sub-sample peak
   interpolation. Each window yields (time, lag).
3. Consensus: the straight line (lag over time) that most windows agree on to
   within ``FIT_TOLERANCE_S`` gives offset (intercept) and drift (slope). In a real
   room many windows are dominated by a source the two devices hear very
   differently and yield a random lag, so the line is found RANSAC-style rather
   than by fitting all windows and trimming.
4. Refine: the agreeing windows are measured again, resampled by the measured
   drift so the stretch does not smear the correlation peak, and fitted.

``confidence`` is the share of windows that agree with the line. It says whether
the files share enough signal for the result to be trusted; how strongly they
correlate matters little (crosstalk at |ncc| 0.05 can still be accurate).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
FINE_STEP_S = 10.0
MIN_FINE_WINDOWS = 64  # short files: overlapping windows, up to half a window apart
MAX_FINE_WINDOWS = 3000
COARSE_TOLERANCE_S = 0.05
REFINE_MARGIN_S = 0.005

FIT_TOLERANCE_S = 1e-3
RANSAC_TRIALS = 2000
RELIABLE_WINDOWS = 30
RELIABLE_CONFIDENCE = 0.1


@dataclass(frozen=True)
class Alignment:
    offset_s: float
    drift_ppm: float
    confidence: float  # share of windows agreeing with the line
    inverted: bool
    windows_used: int  # windows agreeing with the line
    windows_total: int  # windows with signal
    wander_ms: float = 0.0  # largest deviation of a quarter of the file from the line

    @property
    def reliable(self) -> bool:
        """Enough agreeing windows to trust offset and drift."""
        enough = min(RELIABLE_WINDOWS, max(3, self.windows_total // 2))
        return self.windows_used >= enough and self.confidence >= RELIABLE_CONFIDENCE

    def ref_time(self, t: float | np.ndarray) -> float | np.ndarray:
        """Reference file time of the other file's time ``t``."""
        return self.offset_s + t / (1 + self.drift_ppm * 1e-6)


@dataclass(frozen=True)
class _Match:
    pos: int  # window start in the other file (samples)
    t: float  # position in the other file (s) the lag holds at
    lag: float  # reference time minus other time (s)
    ncc: float  # signed normalised correlation at the peak


def align(ref: np.ndarray, other: np.ndarray, rate: int = ANALYSIS_RATE) -> Alignment:
    """Align ``other`` to ``ref``; both mono at ``rate`` Hz."""
    ref = np.asarray(ref, dtype=np.float64)
    other = np.asarray(other, dtype=np.float64)
    t0, lag0 = _coarse(
        audio.resample(ref, rate, COARSE_RATE),
        audio.resample(other, rate, COARSE_RATE),
        COARSE_RATE,
    )
    win = _window_length(len(other), rate)
    matches = _fine(
        ref,
        other,
        rate,
        _positions(len(other), win, rate),
        win,
        lag_at=lambda t: lag0,
        margin_at=lambda t: COARSE_TOLERANCE_S + MAX_DRIFT_PPM * 1e-6 * abs(t - t0),
    )
    if not matches:
        raise ValueError("the files do not seem to overlap")
    t, lag, ncc = _arrays(matches)
    slope, intercept, inlier = _consensus(t, lag, np.abs(ncc))
    wander = _wander(t, lag - (intercept + slope * t))
    first = _result(slope, intercept, ncc[inlier], len(matches), wander)

    # refine the agreeing windows with the drift undone inside each window
    stretch = 1 / (1 + first.drift_ppm * 1e-6)
    refined = _fine(
        ref,
        other,
        rate,
        np.array([m.pos for m, ok in zip(matches, inlier, strict=True) if ok]),
        win,
        lag_at=lambda t: first.ref_time(t) - t,
        margin_at=lambda t: REFINE_MARGIN_S,
        stretch=stretch,
    )
    if len(refined) < 2:
        return first
    t, lag, ncc = _arrays(refined)
    slope, intercept, _ = _consensus(t, lag, np.abs(ncc), initial=(slope, intercept))
    return _result(slope, intercept, ncc, len(matches), wander, used=first.windows_used)


def align_best(
    refs: Sequence[np.ndarray], other: np.ndarray, rate: int = ANALYSIS_RATE
) -> tuple[int, Alignment]:
    """Align ``other`` to whichever of several sample-parallel reference tracks
    (e.g. the channels of one desk) it agrees with best; return (index, alignment)."""
    best: tuple[int, Alignment] | None = None
    for i, ref in enumerate(refs):
        try:
            a = align(ref, other, rate)
        except ValueError:
            continue
        if best is None or _rank(a) > _rank(best[1]):
            best = (i, a)
    if best is None:
        raise ValueError("no reference track overlaps the file")
    return best


def align_files(
    ref_paths: Sequence[Path | str], paths: Sequence[Path | str], rate: int = ANALYSIS_RATE
) -> list[tuple[int, Alignment]]:
    """Align each file to the best of the reference tracks; one reference track is
    decoded at a time, the files to align stay in memory."""
    others = [audio.load(p, rate) for p in paths]
    best: list[tuple[int, Alignment] | None] = [None] * len(others)
    errors: list[str] = [""] * len(others)
    for i, ref_path in enumerate(ref_paths):
        ref = audio.load(ref_path, rate)
        for k, other in enumerate(others):
            try:
                a = align(ref, other, rate)
            except ValueError as e:
                errors[k] = str(e)
                continue
            if best[k] is None or _rank(a) > _rank(best[k][1]):
                best[k] = (i, a)
    for k, b in enumerate(best):
        if b is None:
            raise ValueError(f"{Path(paths[k]).name}: {errors[k]}")
    return best  # type: ignore[return-value]


def _rank(a: Alignment) -> tuple[bool, int, float]:
    return (a.reliable, a.windows_used, a.confidence)


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


def _window_length(n_other: int, rate: int) -> int:
    """FINE_WINDOW_S, shorter for short files so that they still get several windows."""
    return max(2, min(int(FINE_WINDOW_S * rate), max(n_other // 4, int(0.5 * rate)), n_other - 2))


def _positions(n_other: int, win: int, rate: int) -> np.ndarray:
    """Window starts: one every FINE_STEP_S, overlapping on short files."""
    usable = n_other - win - 2
    if usable <= 0:
        return np.array([0])
    by_step = usable // int(FINE_STEP_S * rate) + 1
    by_overlap = usable // max(win // 2, 1) + 1
    count = min(MAX_FINE_WINDOWS, max(by_step, min(MIN_FINE_WINDOWS, by_overlap)))
    return np.linspace(0, usable, count).astype(int)


def _fine(
    ref: np.ndarray,
    other: np.ndarray,
    rate: int,
    positions: np.ndarray,
    win: int,
    lag_at: Callable[[float], float],
    margin_at: Callable[[float], float],
    stretch: float = 1.0,
) -> list[_Match]:
    """Correlate windows of ``other`` starting at ``positions`` against ``ref`` near the
    predicted lag.

    ``stretch`` is the expected reference time per unit of other time (``1 + slope``);
    each window is resampled by it so drift does not smear the correlation peak.
    """
    span = int(np.ceil(win / stretch)) + 1  # other samples a window covers
    mean_power = float(np.mean(np.square(other)))
    grid = np.arange(win) / stretch
    matches = []
    for p in positions:
        p = int(min(p, len(other) - span))
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
        matches.append(_Match(p, t_c / rate, (start + c - t_c) / rate, float(ncc[k])))
    return matches


def _arrays(matches: list[_Match]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.array([m.t for m in matches]),
        np.array([m.lag for m in matches]),
        np.array([m.ncc for m in matches]),
    )


def _consensus(
    t: np.ndarray,
    lag: np.ndarray,
    w: np.ndarray,
    initial: tuple[float, float] | None = None,
) -> tuple[float, float, np.ndarray]:
    """(slope, intercept, inliers) of the line most windows agree with.

    Candidate lines go through pairs of windows (all pairs when there are few,
    random pairs otherwise); the one with the most windows within FIT_TOLERANCE_S
    wins and is then refined by weighted least squares on its inliers.
    """
    n = len(t)
    if initial is not None:
        slope, intercept = initial
    elif n == 1:
        slope, intercept = 0.0, float(lag[0])
    else:
        slope, intercept = _ransac(t, lag, w)
    inlier = np.abs(lag - (intercept + slope * t)) <= FIT_TOLERANCE_S
    for _ in range(5):
        if inlier.sum() >= 2 and np.ptp(t[inlier]) > 0:
            slope, intercept = np.polyfit(t[inlier], lag[inlier], 1, w=w[inlier] + 1e-3)
        elif inlier.any():
            intercept = float(np.median(lag[inlier] - slope * t[inlier]))
        new = np.abs(lag - (intercept + slope * t)) <= FIT_TOLERANCE_S
        if np.array_equal(new, inlier) or not new.any():
            break
        inlier = new
    return float(slope), float(intercept), inlier


def _ransac(t: np.ndarray, lag: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    n = len(t)
    if n * (n - 1) // 2 <= RANSAC_TRIALS:
        i, j = np.triu_indices(n, k=1)
    else:
        rng = np.random.default_rng(0)
        i, j = rng.integers(0, n, RANSAC_TRIALS), rng.integers(0, n, RANSAC_TRIALS)
    dt = t[j] - t[i]
    ok = np.abs(dt) >= 0.1 * np.ptp(t)  # too close together for a stable slope
    if not ok.any():
        return 0.0, float(np.median(lag))
    i, j, dt = i[ok], j[ok], dt[ok]
    slopes = (lag[j] - lag[i]) / dt
    ok = np.abs(slopes) <= MAX_DRIFT_PPM * 1e-6
    if not ok.any():
        return 0.0, float(np.median(lag))
    slopes, intercepts = slopes[ok], lag[i[ok]] - slopes[ok] * t[i[ok]]
    best, best_score = 0, (-1, -1.0)
    for k in range(len(slopes)):
        inl = np.abs(lag - (intercepts[k] + slopes[k] * t)) <= FIT_TOLERANCE_S
        score = (int(inl.sum()), float(w[inl].sum()))
        if score > best_score:
            best, best_score = k, score
    return float(slopes[best]), float(intercepts[best])


def _wander(t: np.ndarray, resid: np.ndarray) -> float:
    """Largest offset (ms) of a quarter of the file from the line.

    Within each quarter, the densest 2 * FIT_TOLERANCE_S wide cluster of residuals
    (at least 5 windows) marks where that part of the file really sits. A clock
    whose rate changes over time bends away from any straight line.
    """
    worst = 0.0
    edges = np.linspace(t.min(), t.max() + 1e-9, 5)
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        r = np.sort(resid[(t >= lo) & (t < hi) & (np.abs(resid) < 0.05)])
        if len(r) < 5:
            continue
        ends = np.searchsorted(r, r + 2 * FIT_TOLERANCE_S, side="right")
        k = int(np.argmax(ends - np.arange(len(r))))
        if ends[k] - k >= 5:
            centre = float(np.median(r[k : ends[k]]))
            worst = max(worst, abs(centre))
    return worst * 1e3


def _result(
    slope: float,
    intercept: float,
    inlier_ncc: np.ndarray,
    total: int,
    wander_ms: float,
    used: int | None = None,
) -> Alignment:
    used = len(inlier_ncc) if used is None else used
    return Alignment(
        offset_s=float(intercept),
        drift_ppm=float((1 / (1 + slope) - 1) * 1e6),
        confidence=max(0, used - 2) / max(1, total - 2) if total > 1 else float(used),
        inverted=bool(np.sum(inlier_ncc < 0) > len(inlier_ncc) / 2),
        windows_used=used,
        windows_total=total,
        wander_ms=wander_ms,
    )
