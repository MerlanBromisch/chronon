"""Listening and waveforms for the app: where a file plays at a reference time, excerpts of a
file and its reference track side by side, and peak overviews of whole tracks.

Times: ``ref_t`` is reference file time (what ``Alignment.offset_s`` counts in); the timeline
starts at ``Timeline.zero`` in reference time, so timeline time = ``ref_t - zero``.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from chronon import align, analysis, audio

LISTEN_RATE = 48_000
LISTEN_S = 10.0
PEAKS_PER_S = 100  # overview resolution: one min/max pair per 10 ms
OVERVIEW_VERSION = 1  # part of the cache key: bump when the overview's content changes
BLOCK_FRAMES = 1 << 16

Progress = Callable[[str, int, int], None]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spot:
    """A file playing at some reference time."""

    path: Path
    device: str
    time_s: float  # its file time


class Timeline:
    """Reference time to file time for every file of an analysis (reference tracks
    included), clips and parallel tracks of a device alike."""

    def __init__(
        self, measured: analysis.Analysis, probe: Callable[[Path], audio.Info] = audio.probe
    ):
        self.entries = dict(measured.entries(all_refs=True))
        self.zero = measured.timeline_zero
        self._probe = probe
        self._durations: dict[Path, float] = {}

    def result(self, path: Path | str) -> align.FileResult:
        try:
            return self.entries[Path(path)]
        except KeyError:
            raise KeyError(f"{path} is not part of the analysis") from None

    def duration_s(self, path: Path | str) -> float:
        path = Path(path)
        if path not in self._durations:
            self._durations[path] = self._probe(path).duration_s
        return self._durations[path]

    def file_time(self, path: Path | str, ref_t: float) -> float:
        return float(self.result(path).alignment.file_time(ref_t))

    def span(self, path: Path | str) -> tuple[float, float]:
        """Reference times of the file's first and last sample."""
        a = self.result(path).alignment
        return a.offset_s, float(a.ref_time(self.duration_s(path)))

    def at(self, ref_t: float, device: str | None = None) -> list[Spot]:
        """Every file (of ``device``) that plays at ``ref_t``, in analysis order."""
        spots = []
        for path, r in self.entries.items():
            if device is not None and r.device != device:
                continue
            start, end = self.span(path)
            if start <= ref_t < end:
                spots.append(Spot(path, r.device, self.file_time(path, ref_t)))
        return spots

    def suggest(self, path: Path | str, seconds: float = LISTEN_S) -> float:
        """A reference time worth listening at: around the middle window the measurement
        agreed on, else (no reliable match) the middle of the part the file shares with its
        reference track."""
        path = Path(path)
        r = self.result(path)
        # the measured windows only mean something when they agree; else they are noise
        good = sorted(r.alignment.good_s) if r.alignment.reliable else []
        if good:
            middle = float(r.alignment.ref_time(good[len(good) // 2]))
        else:
            start, end = self.span(path)
            ref_start, ref_end = self.span(r.reference)
            lo, hi = max(start, ref_start), min(end, ref_end)
            middle = (lo + hi) / 2 if hi > lo else (start + end) / 2
        return middle - seconds / 2

    def pair(
        self,
        path: Path | str,
        ref_t: float,
        seconds: float = LISTEN_S,
        rate: int = LISTEN_RATE,
    ) -> np.ndarray:
        """``seconds`` from reference time ``ref_t``: the file's reference track (left) and
        the file (right), shape (frames, 2), float32, zeros where either is silent."""
        r = self.result(path)
        ref_start = self.result(r.reference).alignment.file_time(ref_t)
        left = audio.excerpt(r.reference, float(ref_start), seconds, rate)
        right = audio.excerpt(path, self.file_time(path, ref_t), seconds, rate)
        return np.column_stack([left, right])


# --- overviews ---------------------------------------------------------------


def overview_path(path: Path | str, cache_dir: Path | str) -> Path:
    """Cache file of a file's overview: keyed by its real path, size and modification time,
    so a changed file gets a new overview."""
    real = os.path.realpath(path)
    st = os.stat(real)
    key = f"{OVERVIEW_VERSION}|{os.path.normcase(real)}|{st.st_size}|{st.st_mtime_ns}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return Path(cache_dir) / f"{Path(path).stem[:40]}-{digest}.peaks.npy"


def overview(
    path: Path | str, cache_dir: Path | str, progress: Progress | None = None
) -> np.ndarray:
    """Min / max peaks of a whole file, PEAKS_PER_S pairs a second over all channels, shape
    (n, 2), int8 (−127…127 = full scale). Computed once, then read from ``cache_dir``."""
    cached = overview_path(path, cache_dir)
    if cached.exists():
        try:
            return np.load(cached)
        except (OSError, ValueError):
            pass  # a damaged cache file is computed again
    began = time.monotonic()
    peaks = _peaks(Path(path), progress)
    log.info("overview of %s: %d peaks in %.1f s", path, len(peaks), time.monotonic() - began)
    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_name(cached.name + f".{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        np.save(f, peaks)
    os.replace(tmp, cached)
    return peaks


def _peaks(path: Path, progress: Progress | None) -> np.ndarray:
    report = progress or (lambda what, done, total: None)
    info = audio.probe(path)
    with contextlib.ExitStack() as stack:
        try:
            f = stack.enter_context(sf.SoundFile(str(path)))
        except (RuntimeError, sf.LibsndfileError):
            blocks = audio.stream(path, info.channels, BLOCK_FRAMES)
        else:
            blocks = f.blocks(BLOCK_FRAMES, dtype="float32", always_2d=True)
        return _bin(blocks, info.sample_rate, max(info.frames, 1), report, f"overview {path.name}")


def _bin(blocks, rate: int, total: int, report: Progress, what: str) -> np.ndarray:
    lows: list[np.ndarray] = []
    highs: list[np.ndarray] = []
    bins: list[np.ndarray] = []
    pos = 0
    report(what, 0, total)
    for block in blocks:
        n = len(block)
        if not n:
            continue
        idx = (np.arange(pos, pos + n, dtype=np.int64) * PEAKS_PER_S) // rate
        starts = np.concatenate([[0], np.flatnonzero(np.diff(idx)) + 1])
        lows.append(np.minimum.reduceat(block.min(axis=1), starts))
        highs.append(np.maximum.reduceat(block.max(axis=1), starts))
        bins.append(idx[starts])
        pos += n
        report(what, min(pos, total), total)
    report(what, total, total)
    if not bins:
        return np.zeros((0, 2), dtype=np.int8)
    ids, low, high = np.concatenate(bins), np.concatenate(lows), np.concatenate(highs)
    # a bin split between two blocks appears twice: merge
    first = np.concatenate([[0], np.flatnonzero(np.diff(ids)) + 1])
    low, high = np.minimum.reduceat(low, first), np.maximum.reduceat(high, first)
    out = np.zeros((int(ids[-1]) + 1, 2), dtype=np.float32)
    out[ids[first], 0], out[ids[first], 1] = low, high
    return np.clip(np.round(out * 127), -127, 127).astype(np.int8)


def reduce(peaks: np.ndarray, width: int) -> np.ndarray:
    """An overview squeezed into ``width`` columns (min of mins, max of maxes), as float32
    in −1…1, shape (width, 2). Shorter overviews keep their length."""
    if len(peaks) <= width:
        return peaks.astype(np.float32) / 127
    starts = (np.arange(width) * len(peaks)) // width
    low = np.minimum.reduceat(peaks[:, 0], starts)
    high = np.maximum.reduceat(peaks[:, 1], starts)
    return np.column_stack([low, high]).astype(np.float32) / 127
