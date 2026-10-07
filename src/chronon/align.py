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

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from scipy import fft as sp_fft

from chronon import audio, devices

log = logging.getLogger(__name__)

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
SCREEN_UNCLEAR_MAX = 18  # finalists when no pair stands out (all of them for one track)
SCREEN_CLEAR = 12  # ... (all tracks, if no track reached this many agreeing windows) ...
SCREEN_WINDOWS_2 = 96
CHECK_WINDOWS = 192  # for check(): measuring an already corrected file
FFT_WORKERS = 1  # per FFT; windows already run in parallel threads
FINE_THREADS = min(6, os.cpu_count() or 1)
LINK_TRIES = 4  # bridges tried for a clip that does not overlap the reference
LEAD_TRIES = 6  # parallel tracks tried for the coarse position (loudest first)
COARSE_REF_TRIES = 4  # reference tracks tried for the coarse position of one file
# Progress inside one clip, from timing the musical (18 desk tracks, 7 files): the coarse
# position (with waiting for a video's decode) takes about a third, screening the
# (reference track, track) pairs half, measuring the finalists the rest.
STAGE_SCREEN = 0.35
STAGE_MEASURE = 0.85
# Decoding a reference track for the coarse search costs about 0.27 of analysing a file of
# the same length; it counts as work, so the first estimate of the time left is not inflated.
REF_READ_COST = 0.27
# A file libsndfile cannot read (video, AAC, MP3) is decoded whole by ffmpeg: about 1.7 times
# the work per second of audio of a PCM file (same timing).
STREAM_COST = 1.7
STREAM_DECODE_COST = STREAM_COST - 1.0  # of that, the decoding (in the background)
# Reading a file's length and level: about 0.1 s, as much as analysing ~30 s of audio.
READ_COST = 30.0
# A clip linked through a bridge: measured again, and the bridge read as a reference.
LINK_COST = 1.0 + REF_READ_COST
# Inside a clip without screening (one reference track, one track): the coarse position and
# the measurement take about as long as each other (synthetic 20-25 min clips).
STAGE_ALONE = 0.5
BORROW_SPAN_S = 600.0  # a clip measurable over less than this takes a sibling's drift
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
    good_lag: tuple[float, ...] = field(default=(), repr=False, compare=False)

    @property
    def reliable(self) -> bool:
        """Enough agreeing windows to trust offset and drift."""
        enough = min(RELIABLE_WINDOWS, max(3, self.windows_total // 2))
        return self.windows_used >= enough and self.confidence >= RELIABLE_CONFIDENCE

    def ref_time(self, t: float | np.ndarray) -> float | np.ndarray:
        """Reference file time of the other file's time ``t``."""
        return self.offset_s + t / (1 + self.drift_ppm * 1e-6)

    def file_time(self, ref_t: float | np.ndarray) -> float | np.ndarray:
        """The other file's time at reference file time ``ref_t`` (inverse of ``ref_time``)."""
        return (ref_t - self.offset_s) * (1 + self.drift_ppm * 1e-6)


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
    def from_file(
        cls, path: Path | str, rate: int, read: Callable[[float], None] | None = None
    ) -> Recording:
        """``read`` hears the seconds decoded so far."""
        try:
            source = audio.FileSource(path, rate)
        except (RuntimeError, sf.LibsndfileError):
            info = audio.probe(path)  # not PCM: stream once, keep the windows
            hint = int(info.frames * rate / info.sample_rate)
            blocks = _counted(audio.stream_mono(path, rate), rate, read)
            return cls.from_blocks(blocks, hint, rate)
        win = _window_length(source.length, rate)
        rec = cls._lazy(source, _positions(source.length, win, rate), win)
        hint = int(source.length * COARSE_RATE / rate) + 1
        rec.coarse = _collect(audio.stream_mono(path, COARSE_RATE), hint, read)
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


def _counted(blocks, rate: int, read: Callable[[float], None] | None):
    """The blocks, telling ``read`` the seconds passed so far."""
    n = 0
    for b in blocks:
        yield b
        n += len(b)
        if read is not None:
            read(n / rate)


def _collect(blocks, hint: int, read: Callable[[float], None] | None = None) -> np.ndarray:
    g = _Growing(hint)
    for b in blocks:
        g.add(b)
        if read is not None:
            read(g.n / COARSE_RATE)
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
        self.preferred: int | None = None  # the track whose coarse search worked last
        self._by_level: list[int] | None = None
        self.status: Callable[[str], None] = lambda text: None  # what is being read now
        # seconds of reference audio decoded for the coarse search, as it goes
        self.reading: Callable[[float], None] = lambda seconds: None

    @classmethod
    def from_arrays(cls, refs: Sequence[np.ndarray], rate: int) -> References:
        return cls([audio.ArraySource(r, rate) for r in refs], None)

    @classmethod
    def from_files(cls, paths: Sequence[Path | str], rate: int) -> References:
        return cls([audio.open_source(p, rate) for p in paths], paths)

    def __len__(self) -> int:
        return len(self.sources)

    def order(self) -> list[int]:
        """Tracks to try for the coarse search: the one that worked last, then the loudest
        (room and ambience microphones; a quiet channel rarely shares much)."""
        if self._by_level is None:
            level = []
            for src in self.sources:
                rec = Recording.sampled(src, 16) if src.length else None
                level.append(rec.mean_power if rec is not None else 0.0)
            self._by_level = sorted(range(len(self)), key=lambda i: -level[i])
        first = [self.preferred] if self.preferred is not None else []
        return first + [i for i in self._by_level if i != self.preferred]

    def release(self) -> None:
        """Forget the coarse copies of all tracks but the preferred one."""
        if len(self) == 1:
            return
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
                blocks = audio.stream_mono(self.paths[i], COARSE_RATE)
                self._coarse[i] = _collect(blocks, hint, self.reading)
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


@dataclass(frozen=True)
class FileResult:
    """Where one file goes, and how that was found."""

    reference: Path  # reference track it was measured against (itself for the reference)
    alignment: Alignment
    device: str
    is_reference: bool = False  # on the reference's clock and start: offset 0, no drift
    via: Path | None = None  # the parallel track its clip was measured through
    drift_from: Path | None = None  # the sibling clip whose drift it took
    linked_via: Path | None = None  # placed through this clip of another device (bridge)


@dataclass(frozen=True)
class Step:
    """A step of an analysis as the app's step list shows it."""

    id: str  # read, reference, compare:<k>, drift
    kind: str  # read | reference | compare | drift
    device: str | None = None


def _plan(devs: Sequence[devices.Device]) -> list[Step]:
    steps = [Step("read", "read"), Step("reference", "reference", devs[0].name)]
    for k, dev in enumerate(devs[1:], 1):
        steps.append(Step(f"compare:{k}", "compare", dev.name))
    return steps + [Step("drift", "drift")]


def _device_step(devs: Sequence[devices.Device], dev: devices.Device) -> str:
    return f"compare:{next(k for k, d in enumerate(devs) if d is dev)}"


class _Work:
    """Progress of an analysis in real work (seconds of audio analysed, see the *_COST
    constants), step by step; safe to use from several threads.

    ``progress`` hears (what, done, total) of the whole analysis; if it has a ``plan``
    method it first gets the steps, and then every report names its step (``task``), the
    share of that step done (``task_done``) and its device. The whole stays at 0 until
    every step's work is foreseen (``ready``), and never goes back: when a step turns out
    bigger than foreseen (``grow``), the bar holds still."""

    def __init__(self, steps: list[Step], progress: Callable[..., None] | None):
        self.steps = {s.id: s for s in steps}
        self.done = dict.fromkeys(self.steps, 0.0)
        self.total = dict.fromkeys(self.steps, 0.0)
        self.report = progress or (lambda what, done, total, **task: None)
        self.detailed = hasattr(progress, "plan")
        self.known = False
        self.shown = 0.0
        self.what = ""
        self.current = steps[0].id  # the step running in the foreground
        self.lock = threading.Lock()
        if self.detailed:
            progress.plan([asdict(s) for s in steps])  # type: ignore[union-attr]

    def foresee(self, step: str, units: float) -> None:
        with self.lock:
            self.total[step] += max(units, 0.0)

    def ready(self) -> None:
        self.known = True

    def grow(self, step: str, units: float) -> None:
        self.foresee(step, units)

    def start(self, step: str, what: str) -> None:
        with self.lock:
            self.current = step
        self._tell(what)

    def add(self, step: str, units: float, what: str | None = None) -> None:
        """Work done on ``step``; on another step than the current one (a clip decoded in
        the background) it counts for the whole, but the report stays on the current step."""
        with self.lock:
            self.done[step] = min(self.done[step] + max(units, 0.0), self.total[step])
        self._tell(what if step == self.current else None)

    def finish(self, step: str) -> None:
        with self.lock:
            self.done[step] = self.total[step]
            finished = step == self.current
        self._tell(None, finished)

    def _tell(self, what: str | None, finished: bool = False) -> None:
        with self.lock:
            step = self.current
            if what is not None:
                self.what = what
            total = sum(self.total.values())
            if self.known and total > 0:
                self.shown = max(self.shown, sum(self.done.values()) / total)
            scale = 1_000_000
            done, whole = round(self.shown * scale), scale
            share = self.done[step] / self.total[step] if self.total[step] else 0.0
            share = 1.0 if finished else share
            text = self.what
        if self.detailed:
            device = self.steps[step].device
            self.report(text, done, whole, task=step, task_done=share, device=device)
        else:
            self.report(text, done, whole)


def _stage(work: _Work, step: str, length: float, what: str) -> Callable[[float], None]:
    """Report the share of a clip's analysis done (0..1, never back) as work on ``step``."""
    last = {"frac": 0.0}

    def stage(frac: float) -> None:
        frac = max(frac, last["frac"])
        work.add(step, (frac - last["frac"]) * length, what)
        last["frac"] = frac

    return stage


def align_files(
    ref_paths: Sequence[Path | str],
    paths: Sequence[Path | str],
    rate: int = ANALYSIS_RATE,
    progress: Callable[[str, int, int], None] | None = None,
    separate: bool = False,
    layout: devices.Layout | None = None,
) -> list[FileResult]:
    """Align each file to the reference, device by device (see chronon.devices): parallel
    tracks are measured once through the one that matches best, clips too short or weak to
    show their own drift take it from a sibling clip. ``separate`` measures every file on
    its own. Results are in the order of ``paths``.

    Progress is reported in seconds of audio analysed, so time remaining can be
    estimated from it."""
    devs = _from_layout(layout) if layout is not None else devices.group(ref_paths, paths, separate)
    ref_tracks = devs[0].files
    clips = [(d, c) for d in devs[1:] for c in d.clips]
    work = _Work(_plan(devs), progress)
    refs = References.from_files(ref_tracks, rate)
    identity = Alignment(0.0, 0.0, 1.0, False, 1, 1)
    out: dict[Path, FileResult] = {
        t: FileResult(t, identity, devs[0].name, is_reference=True) for t in ref_tracks
    }
    errors: dict[Path, str] = {}

    # read: lengths and levels of every file
    work.foresee("read", READ_COST * (sum(len(c.tracks) for _, c in clips) + len(refs)))
    work.start("read", "reading files")
    durations, decode, analyse = [], [], []
    for _, c in clips:
        c.tracks = _by_level(c.tracks, rate)
        duration = audio.probe(c.tracks[0]).duration_s
        streamed = _streamed(c.tracks[0])
        durations.append(duration)
        decode.append(duration * (STREAM_DECODE_COST if streamed else REF_READ_COST))
        analyse.append(duration)
        work.add("read", READ_COST * len(c.tracks))
    refs.order()  # levels of the reference tracks
    for k, (dev, _) in enumerate(clips):
        work.foresee(_device_step(devs, dev), decode[k] + analyse[k])
    if clips and len(refs):
        work.foresee("reference", REF_READ_COST * refs.sources[0].length / refs.sources[0].rate)
    work.foresee("drift", 1.0)
    work.ready()
    work.finish("read")

    # reference: the coarse copy of the track the coarse search tries first
    current = {"step": "reference", "what": "reading the reference"}
    read_so_far = {"s": 0.0}

    def reading(seconds: float) -> None:
        if seconds < read_so_far["s"]:
            read_so_far["s"] = 0.0
        step = current["step"]
        if step != "reference":  # a further reference track: more work than foreseen
            work.grow(step, REF_READ_COST * (seconds - read_so_far["s"]))
        work.add(step, REF_READ_COST * (seconds - read_so_far["s"]), current["what"])
        read_so_far["s"] = seconds

    refs.reading = reading
    if clips and len(refs):
        work.start("reference", "reading the reference")
        refs.coarse(refs.order()[0])
    work.finish("reference")
    read_so_far["s"] = 0.0

    def loader(k: int) -> Recording:
        """Decode clip ``k`` (its coarse copy, or all of a streamed file), counting the work."""
        step = _device_step(devs, clips[k][0])
        credited = {"units": 0.0}

        def decoded(seconds: float) -> None:
            units = min(decode[k] * seconds / max(durations[k], 1e-9), decode[k])
            work.add(step, units - credited["units"])
            credited["units"] = units

        rec = Recording.from_file(clips[k][1].tracks[0], rate, decoded)
        work.add(step, decode[k] - credited["units"])
        return rec

    # the next clip is decoded in the background while one is analysed
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="chronon-load") as pool:
        upcoming = pool.submit(loader, 0) if clips else None
        for k, (dev, clip) in enumerate(clips):
            name = clip.tracks[0].name
            step = _device_step(devs, dev)
            current.update(step=step, what=f"analysing {name}")
            work.start(step, f"analysing {name}")
            assert upcoming is not None
            rec = upcoming.result()
            upcoming = pool.submit(loader, k + 1) if k + 1 < len(clips) else None
            stage = _stage(work, step, analyse[k], f"analysing {name}")

            began = time.monotonic()
            try:
                index, a, lead = _align_clip(refs, clip.tracks, rec, rate, stage)
            except ValueError as e:
                errors[clip.tracks[0]] = str(e)
                log.warning("%s: no direct match with the reference (%s)", name, e)
            else:
                log.info(
                    "%s: measured in %.1f s against %s via %s",
                    name,
                    time.monotonic() - began,
                    ref_tracks[index].name,
                    lead.name,
                )
                via = lead if len(clip.tracks) > 1 else None
                for t in clip.tracks:
                    out[t] = FileResult(ref_tracks[index], a, dev.name, via=via)
            stage(1.0)
            if k + 1 == len(clips) or _device_step(devs, clips[k + 1][0]) != step:
                work.finish(step)
            del rec
    current.update(step="drift", what="placing clips")
    work.start("drift", "placing clips")
    _link(clips, out, rate, work)
    for _, clip in clips:
        if clip.tracks[0] not in out:
            raise ValueError(f"{clip.tracks[0].name}: {errors[clip.tracks[0]]}")
    for dev in devs[1:]:
        _borrow_drift(dev, out)
    work.finish("drift")
    for p in paths:
        _log_result(Path(p), out[Path(p)])
    return [out[Path(p)] for p in paths]


def _log_result(path: Path, r: FileResult) -> None:
    a = r.alignment
    how = [
        f"{k} {v.name}"
        for k, v in (("via", r.via), ("drift from", r.drift_from), ("linked via", r.linked_via))
        if v is not None
    ]
    log.info(
        "%s (%s): offset %.6f s, drift %+.3f ppm, confidence %.2f (%d/%d windows), "
        "wander %.2f ms, %s%s",
        path.name,
        r.device,
        a.offset_s,
        a.drift_ppm,
        a.confidence,
        a.windows_used,
        a.windows_total,
        a.wander_ms,
        "reliable" if a.reliable or r.is_reference else "NOT RELIABLE",
        "".join(f", {h}" for h in how),
    )


def _from_layout(layout: devices.Layout) -> list[devices.Device]:
    """The user's devices, the reference first: the clip holding the chosen reference tracks
    (those first, its other parallel tracks after them); other clips of that device are
    measured like any device's."""
    ref = layout.devices[layout.reference]
    clip = next(c for c in ref.clips if set(layout.tracks) <= set(c.tracks))
    tracks = list(layout.tracks) + [t for t in clip.tracks if t not in layout.tracks]
    out = [devices.Device(ref.name, [devices.Clip(tracks)], is_reference=True)]
    if len(ref.clips) > 1:
        out.append(devices.Device(ref.name, [c for c in ref.clips if c is not clip]))
    out += [d for k, d in enumerate(layout.devices) if k != layout.reference]
    # copies: the analysis reorders tracks (loudest first); the user's order stays
    return [
        devices.Device(d.name, [devices.Clip(list(c.tracks)) for c in d.clips], d.is_reference)
        for d in out
    ]


def _streamed(path: Path) -> bool:
    """Whether a file is decoded whole (as ``Recording.from_file`` does) rather than read
    in excerpts."""
    try:
        sf.info(str(path))
    except (RuntimeError, sf.LibsndfileError):
        return True
    return False


def _link(
    clips: Sequence[tuple[devices.Device, devices.Clip]],
    out: dict[Path, FileResult],
    rate: int,
    work: _Work | None = None,
) -> None:
    """Place clips that found no reliable match with the reference through clips of other
    devices that did (a camera that started before the desk, but overlaps the Zoom). The
    two measurements are composed exactly; a newly placed clip can bridge the next one."""

    def placed(clip: devices.Clip) -> bool:
        r = out.get(clip.tracks[0])
        return r is not None and (r.is_reference or r.alignment.reliable)

    def priority(item: tuple[devices.Device, devices.Clip]) -> tuple[bool, float]:
        info = audio.probe(item[1].tracks[0])  # PCM bridges read fast; longer ones overlap more
        return (not info.codec.startswith("pcm_") or info.has_video, -info.duration_s)

    changed = True
    while changed:
        changed = False
        for dev, clip in clips:
            if placed(clip):
                continue
            bridges = sorted([(d, c) for d, c in clips if d is not dev and placed(c)], key=priority)
            rec: Recording | None = None
            best: tuple[Alignment, Alignment, Path, Path] | None = None
            for _, bridge in bridges[:LINK_TRIES]:
                rb = out[bridge.tracks[0]]
                track = rb.via or bridge.tracks[0]
                what = f"linking {clip.tracks[0].name} via {track.name}"
                stage: Callable[[float], None] = lambda frac: None  # noqa: E731
                if work is not None:
                    # each try is work nobody foresaw: the step grows, the bar holds still
                    length = LINK_COST * audio.probe(clip.tracks[0]).duration_s
                    work.grow("drift", length)
                    stage = _stage(work, "drift", length, what)
                rec = rec or Recording.from_file(clip.tracks[0], rate)
                try:
                    _, a, lead = _align_clip(
                        References.from_files([track], rate), clip.tracks, rec, rate, stage
                    )
                except ValueError:
                    continue
                finally:
                    stage(1.0)
                if a.reliable and (best is None or _rank(a) > _rank(best[0])):
                    best = (a, _compose(a, rb.alignment), track, lead)
            if best is None:
                continue
            a, composed, track, lead = best
            old = out.get(clip.tracks[0])
            if old is not None and _rank(old.alignment) >= _rank(a):
                continue
            for t in clip.tracks:
                out[t] = FileResult(
                    track,
                    composed,
                    dev.name,
                    via=lead if len(clip.tracks) > 1 else None,
                    linked_via=track,
                )
            changed = True


def _compose(xy: Alignment, y: Alignment) -> Alignment:
    """x aligned to y, and y to the reference: x aligned to the reference."""
    dy = 1 + y.drift_ppm * 1e-6
    composed = replace(
        xy,
        offset_s=y.offset_s + xy.offset_s / dy,
        drift_ppm=((1 + xy.drift_ppm * 1e-6) * dy - 1) * 1e6,
    )
    # the agreeing windows' lags, re-expressed against the reference (scatter kept)
    t = np.array(xy.good_s)
    if len(t):
        resid = np.array(xy.good_lag) - (xy.ref_time(t) - t)
        lag = composed.ref_time(t) - t + resid
        composed = replace(composed, good_lag=tuple(float(v) for v in lag))
    return composed


def loudest(tracks: Sequence[Path], count: int) -> list[Path]:
    """The ``count`` loudest of parallel tracks (sum and room channels rather than a
    rarely played instrument): the suggested reference tracks."""
    return _by_level(tracks, ANALYSIS_RATE)[:count]


def _by_level(tracks: Sequence[Path], rate: int) -> list[Path]:
    """Parallel tracks loudest first (from a few sampled windows): silent channels of a desk
    are not worth a full measurement."""
    if len(tracks) < 2:
        return list(tracks)
    level = {}
    for t in tracks:
        try:
            src = audio.open_source(t, rate)
        except (audio.AudioError, RuntimeError):
            level[t] = 0.0
            continue
        level[t] = Recording.sampled(src, 16).mean_power if src.length else 0.0
    return sorted(tracks, key=lambda t: -level[t])


def _align_clip(
    refs: References,
    tracks: Sequence[Path],
    first: Recording,
    rate: int,
    stage: Callable[[float], None],
) -> tuple[int, Alignment, Path]:
    """Measure a clip of one or more parallel tracks (loudest first; the first one loaded
    with its coarse copy); return (reference index, alignment, the track measured)."""
    if len(tracks) == 1:
        index, a = _align(refs, first, stage)
        return index, a, tracks[0]
    recs = [first] + [
        Recording._lazy(audio.open_source(t, rate), first.positions, first.win) for t in tracks[1:]
    ]
    index, a, j = _align(refs, recs, stage)
    return index, a, tracks[j]


def _borrow_drift(dev: devices.Device, out: dict[Path, FileResult]) -> None:
    """A clip whose own drift is not trustworthy (unreliable, or its measurable part too
    short) takes the drift of the nearest sibling clip that is, and its offset is refitted
    to its own windows with that drift. Long clips keep their own drift: a clock's rate can
    change over hours (the musical's Zoom: -8.2 then -11.2 ppm)."""
    results = [out[c.tracks[0]] for c in dev.clips]

    def strong(r: FileResult) -> bool:
        a = r.alignment
        return a.reliable and len(a.good_s) > 1 and np.ptp(a.good_s) >= BORROW_SPAN_S

    donors = [k for k, r in enumerate(results) if strong(r)]
    for k, (clip, r) in enumerate(zip(dev.clips, results, strict=True)):
        if strong(r) or not donors or not r.alignment.good_s:
            continue
        donor = min(donors, key=lambda d: abs(d - k))
        drift = results[donor].alignment.drift_ppm
        slope = 1 / (1 + drift * 1e-6) - 1
        t = np.array(r.alignment.good_s)
        lag = np.array(r.alignment.good_lag)
        offset = float(np.median(lag - slope * t))
        a = replace(r.alignment, offset_s=offset, drift_ppm=drift)
        for track in clip.tracks:
            out[track] = replace(out[track], alignment=a, drift_from=dev.clips[donor].tracks[0])


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
    refs: References,
    recs: Recording | Sequence[Recording],
    stage: Callable[[float], None] | None = None,
) -> tuple[int, Alignment] | tuple[int, Alignment, int]:
    """Align a recording, or parallel tracks of one clip (the first one with its coarse
    copy, the others reading their windows on the same grid), to the reference tracks.
    Returns (reference index, alignment), plus the track index for several tracks.
    ``stage`` hears the share of this clip's work that is done (0..1)."""
    single = isinstance(recs, Recording)
    tracks = [recs] if single else list(recs)
    stage = stage or (lambda frac: None)
    # the screening stage only exists for several (reference track, track) pairs
    alone = len(refs) * len(tracks) == 1
    share = STAGE_ALONE if alone else STAGE_SCREEN
    anchor = _anchor(
        refs, tracks[0], tries=1 if len(tracks) > 1 else 0, tick=lambda f: stage(share * f)
    )
    for k in range(1, min(len(tracks), LEAD_TRIES)):
        if anchor.support >= min(2, anchor.tried):
            break  # parallel tracks start together: one track's anchor holds for all
        # another channel against the reference track that worked so far is cheaper and
        # likelier to help than more reference tracks against a channel that hears nothing
        _with_coarse(tracks[k])
        other = _anchor(refs, tracks[k], tries=1)
        if other.support > anchor.support:
            anchor = other
    stage(share)
    narrow = anchor.slope is not None
    result = _align_with(refs, tracks, anchor, narrow, stage)
    if narrow and not result[1].reliable:
        # the coarse slope may have been misleading: search the full drift range
        wide = _align_with(refs, tracks, anchor, False, stage)
        if _rank(wide[1]) > _rank(result[1]):
            result = wide
    return result[:2] if single else result


def _with_coarse(rec: Recording) -> None:
    if rec.coarse.size == 0 and isinstance(rec.source, audio.FileSource):
        hint = int(rec.length * COARSE_RATE / rec.rate) + 1
        rec.coarse = _collect(audio.stream_mono(rec.source.path, COARSE_RATE), hint)


def _align_with(
    refs: References,
    tracks: Sequence[Recording],
    anchor: _Anchor,
    narrow: bool,
    stage: Callable[[float], None],
) -> tuple[int, Alignment, int]:
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

    pairs = [(i, j) for j in range(len(tracks)) for i in range(len(refs))]
    candidates = pairs[:1]
    if len(pairs) > 1:
        # screening: few windows on every (reference track, track) pair, more on the
        # finalists. A file with weak shared sound has only ~15 % of windows on the line,
        # so one small sample would pick almost at random among similar tracks. Many pairs
        # (a desk against a desk) start with fewer windows each.
        lead = tracks[0]
        n = len(lead.positions)
        per_pair = SCREEN_WINDOWS if len(pairs) <= 18 else max(8, 864 // len(pairs))
        first = np.unique(np.linspace(0, n - 1, min(per_pair, n)).astype(int))
        both = np.unique(np.linspace(0, n - 1, min(per_pair + SCREEN_WINDOWS_2, n)).astype(int))
        more = np.setdiff1d(both, first)

        def look(pair, idx):
            i, j = pair
            return _fine(refs.sources[i], tracks[j], lead.positions[idx], lag_at, margin_at)

        found = {}
        for k, pair in enumerate(pairs):
            stage(STAGE_SCREEN + (STAGE_MEASURE - STAGE_SCREEN) * 0.8 * k / len(pairs))
            found[pair] = look(pair, first)
        ranked = sorted(found, key=lambda q: _screen_score(found[q]), reverse=True)
        clear = _screen_score(found[ranked[0]])[0] >= min(SCREEN_CLEAR, len(first) // 4 + 1)
        finalists = ranked[:SCREEN_FINALISTS] if clear else ranked[:SCREEN_UNCLEAR_MAX]
        for pair in finalists:
            found[pair] = found[pair] + look(pair, more)
        ranked = sorted(finalists, key=lambda q: _screen_score(found[q]), reverse=True)
        candidates = ranked[:SCREEN_KEEP]
    begin = STAGE_ALONE if len(pairs) == 1 else STAGE_MEASURE
    stage(begin)
    best: tuple[int, Alignment, int] | None = None
    for k, (i, j) in enumerate(candidates):

        def part(frac: float, k: int = k) -> None:
            stage(begin + (1 - begin) * (k + frac) / len(candidates))

        try:
            a = _measure(refs.sources[i], tracks[j], tracks[j].positions, lag_at, margin_at, part)
        except ValueError:
            continue
        if best is None or _rank(a) > _rank(best[1]):
            best = (i, a, j)
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


def _anchor(
    refs: References, rec: Recording, tries: int = 0, tick: Callable[[float], None] | None = None
) -> _Anchor:
    """Coarse position: try reference tracks (the one that worked last first, at most
    ``tries`` or COARSE_REF_TRIES; each costs decoding a whole track) until one gives a
    clear answer. ``tick`` hears the share of the first try done (usually the only one)."""
    fallback = None
    for n, i in enumerate(refs.order()[: tries or COARSE_REF_TRIES]):
        found = _coarse(refs.coarse(i), rec.coarse, COARSE_RATE, tick if n == 0 else None)
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
    part: Callable[[float], None] | None = None,
) -> Alignment:
    """Fine windows, consensus line, then the agreeing windows again with drift undone.
    ``part`` hears the share done (0..1): the first pass counts two thirds."""
    part = part or (lambda frac: None)
    matches = _fine(ref, rec, positions, lag_at, margin_at, tick=lambda f: part(f * 2 / 3))
    if not matches:
        raise ValueError("the files do not seem to overlap")
    t, lag, ncc = _arrays(matches)
    slope, intercept, inlier = _consensus(t, lag, np.abs(ncc))
    wander = _wander(t, lag - (intercept + slope * t))
    first = _result(
        slope, intercept, ncc[inlier], len(matches), wander, good_s=t[inlier], good_lag=lag[inlier]
    )

    stretch = 1 / (1 + first.drift_ppm * 1e-6)
    refined = _fine(
        ref,
        rec,
        np.array([m.pos for m, ok in zip(matches, inlier, strict=True) if ok]),
        lag_at=lambda t: first.ref_time(t) - t,
        margin_at=lambda t: REFINE_MARGIN_S,
        stretch=stretch,
        tick=lambda f: part(2 / 3 + f / 3),
    )
    if len(refined) < 2:
        return first
    t, lag, ncc = _arrays(refined)
    slope, intercept, _ = _consensus(t, lag, np.abs(ncc), initial=(slope, intercept))
    return _result(
        slope,
        intercept,
        ncc,
        len(matches),
        wander,
        used=first.windows_used,
        good_s=first.good_s,
        good_lag=first.good_lag,
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


def _coarse(
    ref: np.ndarray, other: np.ndarray, rate: int, tick: Callable[[float], None] | None = None
) -> _Anchor | None:
    """The best-supported coarse window's position and lag, with the slope through the
    windows agreeing with it when they lie far enough apart; None without usable audio."""
    win = min(len(other), int(COARSE_WINDOW_S * rate))
    # the reference is padded by one window on both sides so the other file may start
    # before or end after it
    power = _mean_power(ref) * len(ref) / max(len(ref) + 2 * win, 1)
    count = min(COARSE_WINDOWS, 1 + (len(other) - win) // max(win // 2, 1))
    mean_power = float(np.mean(np.square(other))) if len(other) else 0.0
    cands = []
    starts = np.linspace(0, len(other) - win, count).astype(int)
    for n, p in enumerate(starts, 1):
        seg = np.asarray(other[p : p + win], dtype=np.float32)
        if tick is not None:
            tick(n / len(starts))
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
    tick: Callable[[float], None] | None = None,
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

    results = map(one, positions) if len(positions) < 4 else _pool().map(one, positions)
    found = []
    step = max(len(positions) // 20, 1)  # the share done, in about 20 steps
    for k, m in enumerate(results, 1):
        found.append(m)
        if tick is not None and (k % step == 0 or k == len(positions)):
            tick(k / len(positions))
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
    good_lag: Sequence[float] = (),
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
        good_lag=tuple(float(x) for x in good_lag),
    )
