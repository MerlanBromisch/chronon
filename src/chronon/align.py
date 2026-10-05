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

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from scipy import fft as sp_fft

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
SCREEN_WINDOWS = 48  # per reference track when choosing among several ...
SCREEN_FINALISTS = 4  # ... then this many tracks get SCREEN_WINDOWS_2 more windows ...
SCREEN_CLEAR = 12  # ... (all tracks, if no track reached this many agreeing windows) ...
SCREEN_WINDOWS_2 = 96
CHECK_WINDOWS = 192  # for check(): measuring an already corrected file
FFT_WORKERS = 1  # per FFT; windows already run in parallel threads
FINE_THREADS = min(6, os.cpu_count() or 1)
COARSE_CHUNK = 1 << 20  # coarse search in chunks: bounded memory on long references
SCREEN_KEEP = 2  # ... and this many are measured in full
# When the coarse windows already show the drift, fine windows search only this far from it
# (coarse lags scatter by tens of ms between acoustic paths, hence the generous bound).
NARROW_TOLERANCE_S = 0.08
NARROW_DRIFT_PPM = 50.0
NARROW_MIN_SPAN_S = 600.0  # coarse windows at least this far apart to trust their slope

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
    # times (s, in the other file) of the windows on the line: where this pair can be measured
    good_s: tuple[float, ...] = field(default=(), repr=False, compare=False)

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


class Recording:
    """The file being aligned, as the analysis needs it: its fine windows and a low-rate
    copy for the coarse search. The whole recording is never held at the analysis rate.

    PCM files (WAV, AIFF, CAF) are read window by window where needed (``source``); other
    files (video, compressed audio), where seeking is slow, are decoded once as a stream and
    their windows (about 40 % of the audio) are kept in memory (``segs``)."""

    def __init__(self, rate: int, length: int, positions: np.ndarray, win: int):
        self.rate = rate
        self.length = length
        self.win = win
        self.positions = positions
        self.span = win + win // 1000 + 4  # room for resampling by up to MAX_DRIFT_PPM
        self.segs: dict[int, np.ndarray] = {}
        self.source: audio.Source | None = None
        self.coarse = np.zeros(0, dtype=np.float32)
        self.mean_power = 0.0

    def window(self, p: int) -> tuple[np.ndarray, float]:
        """The ``span`` samples of the window at position ``p`` and the exact analysis
        position of its first sample (fractional for excerpts of e.g. 44.1 kHz files)."""
        if self.source is None:
            return self.segs[int(p)].astype(np.float32), float(p)
        return self.source.read(int(p), self.span)

    @classmethod
    def from_blocks(cls, blocks, length_hint: int, rate: int) -> Recording:
        win = _window_length(length_hint, rate)
        rec = cls(rate, length_hint, _positions(length_hint, win, rate), win)
        # half precision: these are streamed (video / compressed) files; rounding sits
        # ~66 dB below the signal, far below what the correlation can resolve
        bufs = {int(p): np.zeros(rec.span, dtype=np.float16) for p in rec.positions}
        starts = np.array(sorted(bufs))
        lowrate = soxr.ResampleStream(
            rate, COARSE_RATE, 1, dtype="float32", quality=audio.RESAMPLE_QUALITY
        )
        coarse = _Growing(int(length_hint * COARSE_RATE / rate) + 1)
        energy, pos = 0.0, 0
        for block in blocks:
            block = np.asarray(block, dtype=np.float32)
            end = pos + len(block)
            first = np.searchsorted(starts, pos - rec.span, side="right")
            last = np.searchsorted(starts, end, side="left")
            for p in starts[first:last]:
                a, b = max(p, pos), min(p + rec.span, end)
                if b > a:
                    bufs[int(p)][a - p : b - p] = block[a - pos : b - pos]
            coarse.add(lowrate.resample_chunk(block))
            energy += float(np.dot(block, block))
            pos = end
        coarse.add(lowrate.resample_chunk(np.zeros(0, np.float32), last=True))
        rec.length = pos
        rec.positions = np.array([p for p in rec.positions if p + rec.span <= pos] or [0])
        rec.segs = {int(p): bufs[int(p)] for p in rec.positions if int(p) in bufs}
        rec.coarse = coarse.array()
        rec.mean_power = energy / max(pos, 1)
        return rec

    @classmethod
    def from_array(cls, x: np.ndarray, rate: int) -> Recording:
        x = np.asarray(x, dtype=np.float32)
        return cls.from_blocks([x], len(x), rate)

    @classmethod
    def from_file(cls, path: Path | str, rate: int) -> Recording:
        try:
            source = audio.FileSource(path, rate)
        except (RuntimeError, sf.LibsndfileError):
            info = audio.probe(path)  # not PCM: stream once, keep the windows
            hint = int(info.frames * rate / info.sample_rate)
            return cls.from_blocks(audio.stream_mono(path, rate), hint, rate)
        win = _window_length(source.length, rate)
        rec = cls._lazy(source, _positions(source.length, win, rate), win)
        hint = int(source.length * COARSE_RATE / rate) + 1
        rec.coarse = _collect(audio.stream_mono(path, COARSE_RATE), hint)
        return rec

    @classmethod
    def sampled(
        cls, source: audio.Source, count: int, start: int = 0, at_s: Sequence[float] = ()
    ) -> Recording:
        """``count`` windows read from ``source`` as needed, no coarse copy: at the times
        ``at_s`` (evenly thinned out) or else spread from sample ``start`` on."""
        win = _window_length(source.length - start, source.rate)
        usable = max(source.length - win - win // 1000 - 6, start)
        if len(at_s):
            times = np.sort(np.asarray(at_s, dtype=float))
            times = times[np.unique(np.linspace(0, len(times) - 1, count).astype(int))]
            positions = np.unique(np.clip((times * source.rate).astype(int) - win // 2, 0, usable))
        else:
            positions = np.unique(np.linspace(start, usable, count).astype(int))
        return cls._lazy(source, positions, win)

    @classmethod
    def _lazy(cls, source: audio.Source, positions: np.ndarray, win: int) -> Recording:
        rec = cls(source.rate, source.length, positions, win)
        rec.positions = np.array([p for p in positions if p + rec.span <= source.length] or [0])
        rec.source = source
        # mean power from a sample of windows (only used as a silence threshold)
        sample = rec.positions[np.unique(np.linspace(0, len(rec.positions) - 1, 64).astype(int))]
        energy = sum(float(np.dot(w, w)) for w in (rec.window(p)[0] for p in sample))
        rec.mean_power = energy / max(len(sample) * rec.span, 1)
        return rec


class _Growing:
    """A buffer filled block by block, without a list-then-concatenate copy. Coarse copies
    use half precision: they only locate a file to tens of milliseconds."""

    def __init__(self, hint: int, dtype=np.float16):
        self.buf = np.zeros(max(hint, 1), dtype=dtype)
        self.n = 0

    def add(self, x: np.ndarray) -> None:
        if self.n + len(x) > len(self.buf):
            self.buf = np.resize(self.buf, max(self.n + len(x), int(len(self.buf) * 1.25)))
        self.buf[self.n : self.n + len(x)] = x
        self.n += len(x)

    def array(self) -> np.ndarray:
        return self.buf[: self.n]


def _collect(blocks, hint: int) -> np.ndarray:
    g = _Growing(hint)
    for b in blocks:
        g.add(b)
    return g.array()


class References:
    """One or more sample-parallel reference tracks (e.g. the channels of a desk).

    Fine windows read short excerpts of a track. The coarse search needs a whole track
    at COARSE_RATE; it is decoded for one track at a time and kept while it keeps working.
    """

    def __init__(self, sources: Sequence[audio.Source], paths: Sequence[Path | str] | None):
        self.sources = list(sources)
        self.paths = list(paths) if paths is not None else None
        self._coarse: dict[int, np.ndarray] = {}
        self.preferred = 0
        self.status: Callable[[str], None] = lambda text: None  # what is being read now

    @classmethod
    def from_arrays(cls, refs: Sequence[np.ndarray], rate: int) -> References:
        return cls([audio.ArraySource(r, rate) for r in refs], None)

    @classmethod
    def from_files(cls, paths: Sequence[Path | str], rate: int) -> References:
        return cls([audio.open_source(p, rate) for p in paths], paths)

    def __len__(self) -> int:
        return len(self.sources)

    def order(self) -> list[int]:
        return [self.preferred] + [i for i in range(len(self)) if i != self.preferred]

    def release(self) -> None:
        """Forget the coarse copies of all tracks but the preferred one."""
        for k in [k for k in self._coarse if k != self.preferred]:
            del self._coarse[k]

    def coarse(self, i: int) -> np.ndarray:
        if i not in self._coarse:
            self.release()  # bound memory: at most the preferred track and this one
            src = self.sources[i]
            if isinstance(src, audio.ArraySource):
                self._coarse[i] = audio.resample(src.x, src.rate, COARSE_RATE)
            else:
                assert self.paths is not None
                self.status(f"reading reference track {i + 1} of {len(self)}")
                hint = int(src.length * COARSE_RATE / src.rate) + 1
                self._coarse[i] = _collect(audio.stream_mono(self.paths[i], COARSE_RATE), hint)
        return self._coarse[i]


def align(ref: np.ndarray, other: np.ndarray, rate: int = ANALYSIS_RATE) -> Alignment:
    """Align ``other`` to ``ref``; both mono at ``rate`` Hz."""
    return _align(References.from_arrays([ref], rate), Recording.from_array(other, rate))[1]


def align_best(
    refs: Sequence[np.ndarray], other: np.ndarray, rate: int = ANALYSIS_RATE
) -> tuple[int, Alignment]:
    """Align ``other`` to whichever of several sample-parallel reference tracks
    (e.g. the channels of one desk) it agrees with best; return (index, alignment)."""
    return _align(References.from_arrays(refs, rate), Recording.from_array(other, rate))


def align_files(
    ref_paths: Sequence[Path | str],
    paths: Sequence[Path | str],
    rate: int = ANALYSIS_RATE,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[tuple[int, Alignment]]:
    """Align each file to the best of the reference tracks.

    Progress is reported in seconds of audio analysed, so time remaining can be
    estimated from it."""
    report = progress or (lambda what, done, total: None)
    durations = [audio.probe(p).duration_s for p in paths]
    total, done = round(sum(durations)), 0.0
    refs = References.from_files(ref_paths, rate)
    results = []
    current = {"name": "", "done": 0.0}
    refs.status = lambda text: report(
        f"analysing {current['name']} ({text})", round(current["done"]), total
    )
    # the next file is decoded in the background while this one is analysed
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="chronon-load") as loader:
        upcoming = loader.submit(Recording.from_file, paths[0], rate) if paths else None
        for k, (path, duration) in enumerate(zip(paths, durations, strict=True)):
            current.update(name=Path(path).name, done=done)
            report(f"analysing {Path(path).name}", round(done), total)
            assert upcoming is not None
            rec = upcoming.result()
            upcoming = (
                loader.submit(Recording.from_file, paths[k + 1], rate)
                if k + 1 < len(paths)
                else None
            )

            def stage(
                frac: float,
                base: float = done,
                length: float = duration,
                name: str = Path(path).name,
            ) -> None:
                report(f"analysing {name}", round(base + frac * length), total)

            try:
                results.append(_align(refs, rec, stage))
            except ValueError as e:
                raise ValueError(f"{Path(path).name}: {e}") from None
            del rec
            done += duration
    report("analysing", total, total)
    return results


def check(
    ref: audio.Source,
    other: audio.Source,
    expected_offset_s: float,
    count: int = CHECK_WINDOWS,
    audio_from_s: float = 0.0,
    at_s: Sequence[float] = (),
) -> Alignment:
    """Test a corrected file against the expectation "offset as computed, no drift".

    ``count`` windows are spread over the part of ``other`` that holds audio (from
    ``audio_from_s``, i.e. after any padding). The line is not searched from scratch, where
    a second acoustic path a few ms away could win on a small sample: it starts at the
    expected line and is then refitted to the windows that agree with it. A wrong
    correction pulls the refitted line away from the expectation, which the caller
    compares against its tolerances."""
    rec = Recording.sampled(other, count, start=int(audio_from_s * other.rate), at_s=at_s)
    matches = _fine(
        ref,
        rec,
        rec.positions,
        lag_at=lambda t: expected_offset_s,
        margin_at=lambda t: COARSE_TOLERANCE_S + 50e-6 * t,
    )
    if not matches:
        raise ValueError("the files do not seem to overlap")
    t, lag, ncc = _arrays(matches)
    slope, intercept, inlier = _consensus(t, lag, np.abs(ncc), initial=(0.0, expected_offset_s))
    return _result(
        slope, intercept, ncc[inlier], len(matches), _wander(t, lag - intercept), good_s=t[inlier]
    )


@dataclass(frozen=True)
class _Anchor:
    t0: float  # position in the other file (s)
    lag0: float  # reference minus other time there (s)
    slope: float | None  # lag change per second, if the coarse windows show it
    support: int  # coarse windows agreeing
    tried: int  # coarse windows with signal


def _align(
    refs: References, rec: Recording, stage: Callable[[float], None] | None = None
) -> tuple[int, Alignment]:
    """``stage`` hears the share of this file's work that is done (0..1)."""
    stage = stage or (lambda frac: None)
    anchor = _anchor(refs, rec)
    stage(0.15)
    narrow = anchor.slope is not None
    result = _align_with(refs, rec, anchor, narrow, stage)
    if narrow and not result[1].reliable:
        # the coarse slope may have been misleading: search the full drift range
        wide = _align_with(refs, rec, anchor, False, stage)
        if _rank(wide[1]) > _rank(result[1]):
            result = wide
    return result


def _align_with(
    refs: References,
    rec: Recording,
    anchor: _Anchor,
    narrow: bool,
    stage: Callable[[float], None],
) -> tuple[int, Alignment]:
    t0, lag0 = anchor.t0, anchor.lag0
    if narrow:
        slope = anchor.slope or 0.0

        def lag_at(t):
            return lag0 + slope * (t - t0)

        def margin_at(t):
            return NARROW_TOLERANCE_S + NARROW_DRIFT_PPM * 1e-6 * abs(t - t0)
    else:

        def lag_at(t):
            return lag0

        def margin_at(t):
            return COARSE_TOLERANCE_S + MAX_DRIFT_PPM * 1e-6 * abs(t - t0)

    candidates = [0]
    if len(refs) > 1:
        # screening: few windows on every track, more on the finalists. A file with weak
        # shared sound has only ~15 % of windows on the line, so one small sample would
        # pick almost at random among similar tracks.
        n = len(rec.positions)
        first = np.unique(np.linspace(0, n - 1, min(SCREEN_WINDOWS, n)).astype(int))
        both = np.unique(
            np.linspace(0, n - 1, min(SCREEN_WINDOWS + SCREEN_WINDOWS_2, n)).astype(int)
        )
        more = np.setdiff1d(both, first)
        found = {
            i: _fine(src, rec, rec.positions[first], lag_at, margin_at)
            for i, src in enumerate(refs.sources)
        }
        ranked = sorted(found, key=lambda i: _screen_score(found[i]), reverse=True)
        clear = _screen_score(found[ranked[0]])[0] >= SCREEN_CLEAR
        finalists = ranked[:SCREEN_FINALISTS] if clear else ranked
        for i in finalists:
            found[i] = found[i] + _fine(
                refs.sources[i], rec, rec.positions[more], lag_at, margin_at
            )
        ranked = sorted(finalists, key=lambda i: _screen_score(found[i]), reverse=True)
        candidates = ranked[:SCREEN_KEEP]
    stage(0.4)
    best: tuple[int, Alignment] | None = None
    for k, i in enumerate(candidates):
        stage(0.4 + 0.6 * k / len(candidates))
        try:
            a = _measure(refs.sources[i], rec, rec.positions, lag_at, margin_at)
        except ValueError:
            continue
        if best is None or _rank(a) > _rank(best[1]):
            best = (i, a)
    if best is None:
        raise ValueError("the files do not seem to overlap")
    return best


def _screen_score(matches: list[_Match]) -> tuple[int, float]:
    if len(matches) < 2:
        return (0, 0.0)
    t, lag, ncc = _arrays(matches)
    _, _, inl = _consensus(t, lag, np.abs(ncc))
    return (int(inl.sum()), float(np.abs(ncc[inl]).sum()))


def _rank(a: Alignment) -> tuple[bool, int, float]:
    return (a.reliable, a.windows_used, a.confidence)


def _anchor(refs: References, rec: Recording) -> _Anchor:
    """Coarse position: try reference tracks until one gives a clear answer."""
    fallback = None
    for i in refs.order():
        found = _coarse(refs.coarse(i), rec.coarse, COARSE_RATE)
        if found is None:
            continue
        if found.support >= min(2, found.tried):
            refs.preferred = i
            refs.release()
            return found
        if fallback is None or found.support > fallback.support:
            fallback = found
    if fallback is None:
        raise ValueError("no usable audio in the file to align")
    return fallback


def _measure(
    ref: audio.Source,
    rec: Recording,
    positions: np.ndarray,
    lag_at: Callable[[float], float],
    margin_at: Callable[[float], float],
) -> Alignment:
    """Fine windows, consensus line, then the agreeing windows again with drift undone."""
    matches = _fine(ref, rec, positions, lag_at, margin_at)
    if not matches:
        raise ValueError("the files do not seem to overlap")
    t, lag, ncc = _arrays(matches)
    slope, intercept, inlier = _consensus(t, lag, np.abs(ncc))
    wander = _wander(t, lag - (intercept + slope * t))
    first = _result(slope, intercept, ncc[inlier], len(matches), wander, good_s=t[inlier])

    stretch = 1 / (1 + first.drift_ppm * 1e-6)
    refined = _fine(
        ref,
        rec,
        np.array([m.pos for m, ok in zip(matches, inlier, strict=True) if ok]),
        lag_at=lambda t: first.ref_time(t) - t,
        margin_at=lambda t: REFINE_MARGIN_S,
        stretch=stretch,
    )
    if len(refined) < 2:
        return first
    t, lag, ncc = _arrays(refined)
    slope, intercept, _ = _consensus(t, lag, np.abs(ncc), initial=(slope, intercept))
    return _result(
        slope, intercept, ncc, len(matches), wander, used=first.windows_used, good_s=first.good_s
    )


# --- correlation -----------------------------------------------------------


def _ncc_valid(ref: np.ndarray, win: np.ndarray, mean_power: float | None = None) -> np.ndarray:
    """Normalised correlation of ``win`` at every start position inside ``ref``.
    ``mean_power`` of the whole reference sets the silence floor (default: of ``ref``)."""
    ref = np.asarray(ref, dtype=np.float64)
    win = np.asarray(win, dtype=np.float64)
    n = sp_fft.next_fast_len(len(ref), real=True)
    c = sp_fft.irfft(
        sp_fft.rfft(ref, n, workers=FFT_WORKERS)
        * np.conj(sp_fft.rfft(win, n, workers=FFT_WORKERS)),
        n,
        workers=FFT_WORKERS,
    )[: len(ref) - len(win) + 1]
    cs = np.concatenate([[0.0], np.cumsum(np.square(ref))])
    local = cs[len(win) :] - cs[: len(cs) - len(win)]
    e_win = float(np.dot(win, win))
    if mean_power is None:
        mean_power = cs[-1] / max(len(ref), 1)
    floor = 1e-4 * mean_power * len(win)  # ignore near-silent stretches
    return c / np.sqrt(e_win * np.maximum(local, floor) + 1e-30)


def _best_match(ref: np.ndarray, win: np.ndarray, pad: int, mean_power: float) -> tuple[int, float]:
    """(start, signed ncc) of the strongest match of ``win`` in ``ref`` with ``pad`` zeros
    on both sides (so ``win`` may hang over either end). Works in chunks, so no copy of
    ``ref`` is made and memory stays bounded however long it is."""
    total = len(ref) + 2 * pad
    best_k, best_v = 0, 0.0
    step = max(COARSE_CHUNK, 4 * len(win))
    for lo in range(0, max(total - len(win) + 1, 1), step):
        chunk = _padded_slice(ref, pad, lo, min(lo + step + len(win) - 1, total))
        if len(chunk) < len(win):
            break
        ncc = _ncc_valid(chunk, win, mean_power)
        k = int(np.argmax(np.abs(ncc)))
        if abs(ncc[k]) > abs(best_v):
            best_k, best_v = lo + k, float(ncc[k])
    return best_k, best_v


def _padded_slice(x: np.ndarray, pad: int, lo: int, hi: int) -> np.ndarray:
    """``[zeros(pad), x, zeros(pad)][lo:hi]`` without building the padded array."""
    out = np.zeros(hi - lo, dtype=np.float32)
    a, b = max(lo - pad, 0), min(hi - pad, len(x))
    if b > a:
        out[a + pad - lo : b + pad - lo] = x[a:b]
    return out


def _mean_power(x: np.ndarray, block: int = 1 << 22) -> float:
    total = 0.0
    for lo in range(0, len(x), block):
        part = np.asarray(x[lo : lo + block], dtype=np.float32)
        total += float(np.dot(part, part))
    return total / max(len(x), 1)


def _parabolic(y: np.ndarray, k: int) -> float:
    if k <= 0 or k >= len(y) - 1:
        return 0.0
    a, b, c = y[k - 1], y[k], y[k + 1]
    denom = a - 2 * b + c
    return 0.0 if denom == 0 else 0.5 * (a - c) / denom


def _energy_ok(win: np.ndarray, mean_power: float) -> bool:
    return float(np.dot(win, win)) / max(len(win), 1) > 1e-3 * mean_power


# --- stages ----------------------------------------------------------------


def _coarse(ref: np.ndarray, other: np.ndarray, rate: int) -> _Anchor | None:
    """The best-supported coarse window's position and lag, with the slope through the
    windows agreeing with it when they lie far enough apart; None without usable audio."""
    win = min(len(other), int(COARSE_WINDOW_S * rate))
    # the reference is padded by one window on both sides so the other file may start
    # before or end after it
    power = _mean_power(ref) * len(ref) / max(len(ref) + 2 * win, 1)
    count = min(COARSE_WINDOWS, 1 + (len(other) - win) // max(win // 2, 1))
    mean_power = float(np.mean(np.square(other))) if len(other) else 0.0
    cands = []
    for p in np.linspace(0, len(other) - win, count).astype(int):
        seg = np.asarray(other[p : p + win], dtype=np.float32)
        if not _energy_ok(seg, mean_power):
            continue
        k, v = _best_match(ref, seg, win, power)
        cands.append((p / rate, (k - win - p) / rate, abs(v)))
    if not cands:
        return None

    def agree(c):
        return [
            (t, lag, s)
            for t, lag, s in cands
            if abs(lag - c[1]) <= COARSE_TOLERANCE_S + MAX_DRIFT_PPM * 1e-6 * abs(t - c[0])
        ]

    best = max(cands, key=lambda c: (sum(s for *_, s in agree(c)), c[2]))
    agreeing = agree(best)
    slope = None
    ts = np.array([t for t, _, _ in agreeing])
    if len(agreeing) >= 2 and np.ptp(ts) >= NARROW_MIN_SPAN_S:
        slope = float(np.polyfit(ts, [lag for _, lag, _ in agreeing], 1)[0])
        if abs(slope) > MAX_DRIFT_PPM * 1e-6:
            slope = None
    return _Anchor(best[0], best[1], slope, len(agreeing), len(cands))


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
    ref: audio.Source,
    rec: Recording,
    positions: np.ndarray,
    lag_at: Callable[[float], float],
    margin_at: Callable[[float], float],
    stretch: float = 1.0,
) -> list[_Match]:
    """Correlate the windows of ``rec`` starting at ``positions`` against excerpts of
    ``ref`` near the predicted lag. Windows are measured in parallel threads (reading,
    resampling and FFTs release the GIL); the result keeps the order of ``positions``.

    ``stretch`` is the expected reference time per unit of other time (``1 + slope``);
    each window is resampled by it so drift does not smear the correlation peak.
    """
    rate, win = rec.rate, rec.win
    span = min(int(np.ceil(win / stretch)) + 1, rec.span)  # other samples a window covers
    grid = np.arange(win) / stretch

    def one(p: int) -> _Match | None:
        stored, x0 = rec.window(int(p))
        seg = stored[:win] if stretch == 1.0 else np.interp(grid, np.arange(span), stored[:span])
        if not _energy_ok(seg, rec.mean_power):
            return None
        t, margin = x0 / rate, margin_at(x0 / rate)
        lo = max(0, int(np.floor((t + lag_at(t) - margin) * rate)))
        hi = min(ref.length, int(np.ceil((t + lag_at(t) + margin) * rate)) + win)
        if hi - lo < win:
            return None  # window lies outside the reference
        excerpt, r0 = ref.read(lo, hi - lo)
        ncc = _ncc_valid(excerpt, seg)
        k = int(np.argmax(np.abs(ncc)))
        sign = 1.0 if ncc[k] >= 0 else -1.0
        start = r0 + k + _parabolic(sign * ncc, k)  # reference sample where seg starts
        # report the lag at the window's energy centroid, where any residual
        # stretch cancels out
        power = np.square(seg, dtype=np.float64)
        c = float(np.dot(np.arange(win), power) / power.sum())
        t_c = x0 + c / stretch
        return _Match(int(p), t_c / rate, (start + c - t_c) / rate, float(ncc[k]))

    if len(positions) < 4:
        found = [one(p) for p in positions]
    else:
        found = list(_pool().map(one, positions))
    return [m for m in found if m is not None]


_POOL: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=FINE_THREADS, thread_name_prefix="chronon")
    return _POOL


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
    good_s: Sequence[float] = (),
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
        good_s=tuple(float(x) for x in good_s),
    )
