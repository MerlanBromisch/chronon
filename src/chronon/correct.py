"""Drift- and rate-corrected audio on one common timeline (see docs/export.md).

The reference clock defines the timeline. Timeline zero is the earliest start of any file,
so nothing is cut off. Every file (the reference tracks too) is written at the target rate
with its drift removed:

    output rate / input rate = out_rate / (in_rate * (1 + drift_ppm * 1e-6))

i.e. the input is resampled from its *real* rate to the target rate in one step (libsoxr,
fractional rates are exact). With padding, silence is put in front so every file starts at
timeline zero. Files are streamed block by block, never held in memory whole.

Originals are only read. Outputs go into their own folder, which must not hold any input.
After writing, every corrected file is measured again against its reference output;
anything but zero offset and zero drift fails the export.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from chronon import __version__, align, analysis, audio, devices, fcpxml, messages
from chronon.messages import note

log = logging.getLogger(__name__)

WAV_MAX_BYTES = 2**32 - 2**20  # RIFF sizes are 32-bit; leave room for the header and chunks
CAF_ABOVE_BYTES = 2**31  # WAV/AIFF: 4 GiB hard limit, some programs already fail at 2 GiB
# The check is a second, independent measurement on a different window grid. On acoustic
# pairs (a room recorder far from the stage) that alone moves the offset by up to ~0.35 ms,
# so a tighter bound would fail correct files. Correction bugs show up as drift.
VERIFY_OFFSET_TOL_S = 5e-4
VERIFY_DRIFT_TOL_PPM = 0.2
FORMATS = ("auto", "wav", "caf")
TIMELINES = ("fcpxml",)  # Final Cut Pro / Logic; Premiere (#7) and Resolve (#8) later
# Report files (chronon-report.json, chronon-sync.json): {"schema", "chronon", "kind", "files"}.
# Bump the schema when a field changes meaning or goes away; adding fields does not need it.
# Schema 0 = a bare list of file rows (before 2026-10-05), still readable.
REPORT_SCHEMA = 2  # 2: notes are codes (chronon.messages)
BLOCK_FRAMES = 1 << 16
# BWF time stamps count from 01:00:00:00, where a Logic project starts by default: timeline
# zero lands on the project start ("move region to recorded position")
BWF_ORIGIN_S = 3600.0
SUFFIX = "_korrigiert"  # corrected outputs: <device or file>_korrigiert.<ext>
MIN_BITS = 24  # resampled audio is not requantised to 16 bit (no dither needed)
BWF_FORMATS = ("wav",)
WRITE_THREADS = min(4, os.cpu_count() or 1)

Progress = Callable[[str, int, int], None]  # (what, done, total) in real work units


@dataclass
class Segment:
    """One source in an output file: resampled from its real rate, starting at
    ``start_frame`` of the output (several segments when the clips of a device are joined)."""

    source: str
    position_s: float  # timeline time of the source's first sample
    drift_ppm: float
    in_rate: int
    in_frames: int
    start_frame: int
    out_frames: int
    has_video: bool = False


@dataclass
class Output:
    source: str
    path: str
    reference: str  # reference track this file was aligned to (itself for reference tracks)
    is_reference: bool
    position_s: float  # timeline time of the source's first sample
    offset_s: float  # measured, on the reference track's file time
    drift_ppm: float  # measured; removed in the output
    confidence: float
    reliable: bool
    in_rate: int
    out_rate: int
    channels: int
    bits: int
    padded: bool
    pad_frames: int
    frames: int  # output frames including padding
    format: str
    has_video: bool = False
    device: str = ""
    order: int = 0  # lane position of its device (the user's device order)
    via: str = ""  # parallel track whose measurement this file shares
    check_at_s: list[float] = field(default_factory=list)  # output times to verify at
    segments: list[Segment] = field(default_factory=list)
    verify_offset_ms: float | None = None
    verify_drift_ppm: float | None = None
    verified: bool | None = None
    notes: list[messages.Note] = field(default_factory=list)

    @property
    def start_s(self) -> float:
        """Timeline time of the output's first sample."""
        return 0.0 if self.padded else self.position_s

    @property
    def bytes(self) -> int:
        return self.frames * self.channels * (4 if self.bits == 32 else self.bits // 8)


class CorrectError(messages.UserError, ValueError):
    pass


Entry = tuple[Path, align.FileResult]


def _measured(
    refs: Sequence[Path | str],
    files: Sequence[Path | str],
    done: analysis.Analysis | None,
    progress: Progress | None,
    separate: bool,
) -> analysis.Analysis:
    """The given analysis (its files unchanged since), or a new one."""
    if done is None:
        return analysis.measure(refs, files, progress, separate)
    done.check_sources()
    return done


def run(
    refs: Sequence[Path | str],
    files: Sequence[Path | str],
    outdir: Path | str,
    rate: int = 48_000,
    pad: bool = True,
    fmt: str = "auto",
    overwrite: bool = False,
    progress: Progress | None = None,
    all_refs: bool = False,
    name: str | None = None,
    separate: bool = False,
    join: bool = False,
    measured: analysis.Analysis | None = None,
    timeline: str | None = "fcpxml",
    frame: Fraction | None = None,
) -> list[Output]:
    """Analyse (unless ``measured`` is a saved analysis), write, verify, write the timeline
    in the format ``timeline`` (one of TIMELINES, or None for audio only; ``frame`` = its
    frame duration, default from its videos).
    Returns one Output per exported file (per device for joined clips)."""
    if timeline is not None and timeline not in TIMELINES:
        raise CorrectError(f"unknown timeline format {timeline!r}")
    if measured is not None:
        refs, files = measured.refs, measured.files
    refs, files, outdir = [Path(r) for r in refs], [Path(f) for f in files], Path(outdir)
    _check_outdir(outdir, refs + files)
    report = progress or (lambda what, done, total: None)
    done = _measured(refs, files, measured, report, separate)
    entries = done.entries(all_refs)
    outputs = plan(entries, outdir, rate, pad, fmt, overwrite, join, done.layout)
    _check_space(outdir, outputs)

    outdir.mkdir(parents=True, exist_ok=True)
    write_all(outputs, report)

    verify(outputs, report)
    for o in outputs:
        o.order = _order(done.layout, o.device)
    items = _corrected_items(outputs)
    placed = _write_timeline(items, outdir, name, frame) if timeline else []
    for p in placed:
        if p.media.has_video:
            out = next(o for o in outputs if str(p.item.path) in {g.source for g in o.segments})
            clip = {"file": str(p.item.path)} if len(out.segments) > 1 else {}
            out.notes.append(note("video_placed", ms=p.error_ms, **clip))
            if p.media.variable_rate:
                out.notes.append(note("variable_frame_rate", **clip))
    write_report(outputs, outdir)
    return outputs


@dataclass
class Placement:
    source: str
    position_s: float
    drift_ppm: float
    confidence: float
    reliable: bool
    error_ms: float  # worst misplacement over the clip from remaining drift / frame rounding
    device: str = ""
    notes: list[messages.Note] = field(default_factory=list)
    order: int = 0  # lane position of its device (the user's device order)


def sync(
    refs: Sequence[Path | str],
    files: Sequence[Path | str],
    outdir: Path | str,
    progress: Progress | None = None,
    all_refs: bool = False,
    name: str | None = None,
    separate: bool = False,
    measured: analysis.Analysis | None = None,
    frame: Fraction | None = None,
) -> list[Placement]:
    """Analyse (unless ``measured`` is a saved analysis) and write a timeline of the
    original files (nothing corrected)."""
    if measured is not None:
        refs, files = measured.refs, measured.files
    refs, files, outdir = [Path(r) for r in refs], [Path(f) for f in files], Path(outdir)
    _check_outdir(outdir, refs + files)
    done = _measured(refs, files, measured, progress, separate)
    entries = done.entries(all_refs)
    zero = min(0.0, *(r.alignment.offset_s for _, r in entries))
    items = [
        fcpxml.Item(
            f,
            r.alignment.offset_s - zero,
            r.alignment.drift_ppm,
            group=r.device,
            order=_order(done.layout, r.device),
        )
        for f, r in entries
    ]
    outdir.mkdir(parents=True, exist_ok=True)
    placed = _write_timeline(items, outdir, name, frame)
    result = [
        Placement(
            str(f),
            r.alignment.offset_s - zero,
            r.alignment.drift_ppm,
            r.alignment.confidence,
            r.is_reference or r.alignment.reliable,
            p.error_ms,
            r.device,
            result_notes(r) + ([note("variable_frame_rate")] if p.media.variable_rate else []),
            p.item.order,
        )
        for (f, r), p in zip(entries, placed, strict=True)
    ]
    _write_json(outdir / "chronon-sync.json", "sync", [asdict(r) for r in result])
    return result


def timeline(outdir: Path | str, name: str | None = None) -> Path:
    """Rebuild the .fcpxml of an earlier ``correct`` or ``sync`` run from its report,
    without analysing or writing audio again."""
    outdir = Path(outdir)
    if (outdir / "chronon-report.json").exists():
        rows = read_report(outdir / "chronon-report.json")
        items = _corrected_items([_output_from_row(r) for r in rows])
    elif (outdir / "chronon-sync.json").exists():
        rows = read_report(outdir / "chronon-sync.json")
        items = [
            fcpxml.Item(
                Path(r["source"]),
                r["position_s"],
                r["drift_ppm"],
                group=r.get("device", ""),
                order=r.get("order", 0),
            )
            for r in rows
        ]
    else:
        raise CorrectError(f"{outdir} holds no chronon-report.json or chronon-sync.json")
    _write_timeline(items, outdir, name)
    return outdir / f"{name or outdir.name}.fcpxml"


def read_report(path: Path | str) -> list[dict]:
    """The file rows of a chronon-report.json or chronon-sync.json, any schema so far."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"files": data}
    if data.get("schema", 0) > REPORT_SCHEMA:
        raise CorrectError(f"{path} was written by a newer Chronon ({data.get('chronon')})")
    rows = data["files"]
    for row in rows:
        if "notes" in row:
            row["notes"] = [messages.from_text(n) for n in row["notes"]]
    return rows


def report_rows(outputs: Sequence[Output]) -> list[dict]:
    """Report rows of corrected outputs, as written to chronon-report.json."""
    return [
        {k: v for k, v in asdict(o).items() if k != "check_at_s"} | {"start_s": o.start_s}
        for o in outputs
    ]


def _write_json(path: Path, kind: str, rows: list[dict]) -> None:
    data = {"schema": REPORT_SCHEMA, "chronon": __version__, "kind": kind, "files": rows}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _output_from_row(row: dict) -> Output:
    fields = set(Output.__dataclass_fields__)
    out = Output(**{k: v for k, v in row.items() if k in fields and k != "segments"})
    out.segments = [Segment(**seg) for seg in row.get("segments", [])]
    return out


def _corrected_items(outputs: Sequence[Output]) -> list[fcpxml.Item]:
    """Video originals (picture only) plus every corrected audio file."""
    items = []
    for o in outputs:
        order = o.order
        for seg in o.segments or [_single_segment(o)]:
            if seg.has_video:
                items.append(
                    fcpxml.Item(
                        Path(seg.source), seg.position_s, seg.drift_ppm, True, o.device, order
                    )
                )
        items.append(fcpxml.Item(Path(o.path), o.start_s, group=o.device, order=order))
    return items


def _order(layout: devices.Layout | None, device: str) -> int:
    return layout.order(device) if layout is not None else 0


def _write_timeline(
    items: list[fcpxml.Item], outdir: Path, name: str | None, frame: Fraction | None = None
):
    name = name or outdir.name
    return fcpxml.write(items, outdir / f"{name}.fcpxml", name, frame)


def result_notes(r: align.FileResult) -> list[messages.Note]:
    """How a file's placement was found, for reports."""
    notes = []
    if r.is_reference:
        notes.append(note("reference_clock"))
    if r.via is not None:
        notes.append(note("measured_via", file=str(r.via)))
    if r.drift_from is not None:
        notes.append(note("drift_from", file=str(r.drift_from)))
    if r.linked_via is not None:
        notes.append(note("linked_via", file=str(r.linked_via)))
    return notes


def plan(
    entries: Sequence[Entry],
    outdir: Path,
    rate: int,
    pad: bool,
    fmt: str,
    overwrite: bool,
    join: bool = False,
    layout: devices.Layout | None = None,
) -> list[Output]:
    """Where every file goes on the timeline and what gets written. With ``join`` the clips
    of a device (not parallel tracks) become one file, gaps filled with silence. Output
    names come from the devices in ``layout`` (see ``_device_names``)."""
    if fmt not in FORMATS:
        raise CorrectError(f"unknown format {fmt!r}, expected one of {FORMATS}")
    zero = min(0.0, *(r.alignment.offset_s for _, r in entries))
    groups: list[list[Entry]] = []
    by_device: dict[str, list[Entry]] = {}
    for e in entries:
        r = e[1]
        joinable = join and not r.is_reference and r.via is None
        if joinable and r.device in by_device:
            by_device[r.device].append(e)
            continue
        groups.append([e])
        if joinable:
            by_device[r.device] = groups[-1]
    if layout is not None:
        names = _device_names(groups, layout)
    else:
        names = _output_names([g[0][0] for g in groups])
        names = _unique(
            [g[0][1].device if len(g) > 1 else n for g, n in zip(groups, names, strict=True)]
        )
    outputs = []
    for group, name in zip(groups, names, strict=True):
        outputs.append(_plan_output(group, name, zero, outdir, rate, pad, fmt, overwrite))
    return outputs


def _plan_output(
    group: list[Entry],
    name: str,
    zero: float,
    outdir: Path,
    rate: int,
    pad: bool,
    fmt: str,
    overwrite: bool,
) -> Output:
    group = sorted(group, key=lambda e: e[1].alignment.offset_s)
    src, r = group[0]
    a, is_ref = r.alignment, r.is_reference
    base = a.offset_s - zero
    pad_frames = round(base * rate) if pad else 0
    segments, infos, check_at = [], [], []
    for path, res in group:
        info = audio.probe(path)
        infos.append(info)
        position = res.alignment.offset_s - zero
        real_rate = info.sample_rate * (1 + res.alignment.drift_ppm * 1e-6)
        start = pad_frames + round((position - base) * rate)
        seg = Segment(
            str(path),
            position,
            res.alignment.drift_ppm,
            info.sample_rate,
            info.frames,
            start,
            round(info.frames * rate / real_rate),
            info.has_video,
        )
        segments.append(seg)
        # where the analysis could measure this clip, in output file time
        lead = start / rate
        scale = 1 + res.alignment.drift_ppm * 1e-6
        check_at += [lead + t / scale for t in res.alignment.good_s]
    info = infos[0]
    out = Output(
        source=str(src),
        path="",
        reference=str(r.reference),
        is_reference=is_ref,
        position_s=base,
        offset_s=a.offset_s,
        drift_ppm=a.drift_ppm,
        confidence=a.confidence,
        reliable=a.reliable,
        in_rate=info.sample_rate,
        out_rate=rate,
        channels=info.channels,
        bits=max(info.bits, MIN_BITS),
        padded=pad,
        pad_frames=pad_frames,
        frames=max(seg.start_frame + seg.out_frames for seg in segments),
        format="wav",
        segments=segments,
        check_at_s=check_at,
    )
    out.has_video = any(i.has_video for i in infos)
    out.device = r.device
    out.via = str(r.via) if r.via is not None else ""
    for _, res in group:
        out.notes.extend(n for n in result_notes(res) if n not in out.notes)
    if len(group) > 1:
        out.notes.append(note("joined", files=[str(p) for p, _ in group]))
    if fmt != "auto":
        out.format = fmt
    elif out.bytes <= CAF_ABOVE_BYTES:
        out.format = "wav"
    elif not pad and out.bytes <= WAV_MAX_BYTES:
        # an unpadded file needs its BWF time stamp, which CAF cannot hold; WAV reaches 4 GiB.
        # (libsndfile's RF64 left the data size at 0xFFFFFFFF, so the chunk Logic appends to
        # a file was read as audio by other programs: not used.)
        out.format = "wav"
        out.notes.append(note("wav_over_2gib"))
    else:
        out.format = "caf"
        out.notes.append(note("caf_over_2gib"))
    if out.format not in BWF_FORMATS and not pad:
        out.notes.append(note("caf_no_time_stamp"))
    if fmt == "wav" and out.bytes > WAV_MAX_BYTES:
        raise CorrectError(
            f"{src.name}: {out.bytes / 2**30:.1f} GiB is too large for WAV",
            "too_large_for_wav",
            file=str(src),
            bytes=out.bytes,
        )
    if not is_ref and not all(res.alignment.reliable for _, res in group):
        out.notes.append(note("no_reliable_match"))
    if out.has_video:
        out.notes.append(note("video_unchanged"))
    path = outdir / f"{name}{SUFFIX}.{out.format}"
    if path.exists() and not overwrite:
        raise CorrectError(f"{path} exists (use --overwrite)", "output_exists", file=str(path))
    out.path = str(path)
    return out


def write(out: Output, progress: Callable[[int, int], None] | None = None) -> None:
    """Stream each segment's source through the resampler into the output file, silence
    before and between segments. WAV / RF64 get a BWF time stamp: the output's timeline
    position counted from BWF_ORIGIN_S."""
    report = progress or (lambda done, total: None)
    subtype = {16: "PCM_16", 24: "PCM_24", 32: "FLOAT"}[out.bits]
    segments = out.segments or [_single_segment(out)]
    done = 0
    with sf.SoundFile(
        out.path, "w", out.out_rate, out.channels, subtype, format=out.format.upper()
    ) as f:
        if out.format in BWF_FORMATS:
            audio.set_bwf(f, round((BWF_ORIGIN_S + out.start_s) * out.out_rate), out.out_rate)
        silence = np.zeros((BLOCK_FRAMES, out.channels), dtype=np.float32)
        for seg in segments:
            while done < seg.start_frame:
                n = min(BLOCK_FRAMES, seg.start_frame - done)
                f.write(silence[:n])
                done += n
                report(done, out.frames)
            skip = done - seg.start_frame  # overlap with the previous segment: keep that one
            real_rate = seg.in_rate * (1 + seg.drift_ppm * 1e-6)
            resampler = None
            if not (seg.in_rate == out.out_rate and seg.drift_ppm == 0.0):
                resampler = soxr.ResampleStream(
                    real_rate, out.out_rate, out.channels, dtype="float32", quality="VHQ"
                )
            for data, last in _with_last(audio.stream(seg.source, out.channels, BLOCK_FRAMES)):
                if resampler is not None:
                    data = resampler.resample_chunk(data, last=last)
                if skip:
                    cut = min(skip, len(data))
                    data, skip = data[cut:], skip - cut
                if len(data):
                    f.write(np.clip(data, -1.0, 1.0))
                    done += len(data)
                    report(min(done, out.frames), out.frames)
    out.frames = done


def _single_segment(out: Output) -> Segment:
    """Reports written before segments existed describe one source."""
    real_rate = out.in_rate * (1 + out.drift_ppm * 1e-6)
    frames = round((out.frames - out.pad_frames) * real_rate / out.out_rate)
    return Segment(
        out.source,
        out.position_s,
        out.drift_ppm,
        out.in_rate,
        frames,
        out.pad_frames,
        out.frames - out.pad_frames,
        out.has_video,
    )


def write_all(outputs: Sequence[Output], progress: Progress | None = None) -> None:
    """Write several files at once (resampling is the bottleneck and runs on one core per
    file). Progress counts output frames over all files."""
    report = progress or (lambda what, done, total: None)
    total = sum(o.frames for o in outputs)
    done = dict.fromkeys(range(len(outputs)), 0)
    lock = threading.Lock()
    report("writing", 0, total)

    def one(k: int) -> None:
        def step(n: int, _total: int) -> None:
            with lock:
                done[k] = n
                current = sum(done.values())
            report("writing", min(current, total), total)

        write(outputs[k], step)

    for o in outputs:
        log.info("writing %s (%d frames at %d Hz, %s)", o.path, o.frames, o.out_rate, o.format)
    with ThreadPoolExecutor(max_workers=WRITE_THREADS, thread_name_prefix="chronon-write") as ex:
        list(ex.map(one, range(len(outputs))))  # re-raises the first error
    report("writing", total, total)


def verify(outputs: Sequence[Output], progress: Progress | None = None) -> None:
    """Measure every written file against its reference track's output again, at
    CHECK_WINDOWS windows spread over the file (excerpts only, nothing loaded whole)."""
    report = progress or (lambda what, done, total: None)
    # every source an output holds (joined outputs hold several), so a clip placed through a
    # bridge is checked against the bridge's output
    by_source = {seg.source: o for o in outputs for seg in o.segments} | {
        o.source: o for o in outputs
    }
    # parallel tracks share one correction: check the track it was measured through. A file
    # without a reliable match is not checked: the check only searches near where the file
    # was put, so it would confirm any placement (verified stays None)
    todo = [
        o for o in outputs if not o.is_reference and o.reliable and (not o.via or o.via == o.source)
    ]
    for k, out in enumerate(todo):
        report("verifying", k, len(todo))
        ref = by_source[out.reference]
        expected = out.start_s - ref.start_s
        try:
            a = align.check(
                audio.open_source(ref.path, align.ANALYSIS_RATE),
                audio.open_source(out.path, align.ANALYSIS_RATE),
                expected,
                audio_from_s=out.pad_frames / out.out_rate,
                at_s=out.check_at_s,
            )
        except ValueError as e:
            out.verified = False
            out.notes.append(note("not_verifiable", reason=str(e)))
            log.warning("cannot verify %s: %s", Path(out.path).name, e)
            continue
        # the error in the middle of the measured part, not extrapolated into padding
        mid = (min(a.good_s) + max(a.good_s)) / 2 if a.good_s else 0.0
        error = float(a.ref_time(mid) - mid) - expected
        out.verify_offset_ms = error * 1e3
        out.verify_drift_ppm = a.drift_ppm
        out.verified = (
            abs(error) <= VERIFY_OFFSET_TOL_S and abs(a.drift_ppm) <= VERIFY_DRIFT_TOL_PPM
        )
        log.info(
            "verified %s: %+.3f ms, %+.3f ppm, %s",
            Path(out.path).name,
            out.verify_offset_ms,
            out.verify_drift_ppm,
            "ok" if out.verified else "FAILED",
        )
        if not out.verified:
            out.notes.append(note("verification_failed"))
    for o in outputs:
        lead = by_source.get(o.via)
        if o.via and o.via != o.source and lead is not None:
            o.verified = lead.verified
            o.verify_offset_ms, o.verify_drift_ppm = lead.verify_offset_ms, lead.verify_drift_ppm
            if lead.verified is False:
                o.notes.append(note("verification_failed_via", file=o.via))
    report("verifying", len(todo), len(todo))


def write_report(outputs: Sequence[Output], outdir: Path) -> None:
    _write_json(outdir / "chronon-report.json", "correct", report_rows(outputs))
    (outdir / "chronon-report.txt").write_text(format_report(outputs) + "\n", encoding="utf-8")


def format_report(outputs: Sequence[Output]) -> str:
    lines = [
        f"{'output':<28} {'timeline s':>12} {'drift ppm':>10} {'rate':>12} "
        f"{'check offset / drift':>22}  notes"
    ]
    for o in outputs:
        check = (
            "reference"
            if o.is_reference
            else f"{o.verify_offset_ms:+.3f} ms {o.verify_drift_ppm:+.2f} ppm"
            if o.verify_offset_ms is not None
            else "-"
        )
        lines.append(
            f"{Path(o.path).name:<28} {o.position_s:>12.6f} {o.drift_ppm:>+10.2f} "
            f"{o.in_rate:>6}>{o.out_rate:<5} {check:>22}  {messages.texts(o.notes)}"
        )
    return "\n".join(lines)


# --- helpers ---------------------------------------------------------------


def _with_last(blocks):
    """Yield (block, is_last) pairs."""
    prev = None
    for b in blocks:
        if prev is not None:
            yield prev, False
        prev = b
    if prev is not None:
        yield prev, True


def _device_names(groups: Sequence[Sequence[Entry]], layout: devices.Layout) -> list[str]:
    """Output names from device names: the device for a single file or joined clips,
    ``<device>_<track>`` for parallel tracks (track name, else its number), and
    ``<device>_<file>`` for clips not joined (just the file name when it already starts
    with the device's, as ZOOM0003 for ZOOM)."""
    names = []
    for group in groups:
        path = group[0][0]
        dev = layout.device_of(path)
        if dev is None:
            names.append(path.stem)
        elif len(group) > 1 or len(dev.files) == 1:
            names.append(dev.name)
        elif any(len(c.tracks) > 1 and path in c.tracks for c in dev.clips):
            names.append(f"{dev.name}_{dev.track_label(path)}")
        elif path.stem.casefold().startswith(dev.name.casefold()):
            names.append(path.stem)
        else:
            names.append(f"{dev.name}_{path.stem}")
    return _unique(names)


def _output_names(sources: Sequence[Path]) -> list[str]:
    """File stems, made unique with the parent folder name where needed."""
    keys = [_name_key(s.stem) for s in sources]
    return _unique(
        [
            s.stem if keys.count(k) == 1 else f"{s.parent.name}_{s.stem}"
            for s, k in zip(sources, keys, strict=True)
        ]
    )


def _unique(names: Sequence[str]) -> list[str]:
    """Names numbered where two would be the same file on a case-insensitive file system."""
    out, taken = [], set()
    for base in names:
        name, n = base, 2
        while _name_key(name) in taken:
            name, n = f"{base}_{n}", n + 1
        taken.add(_name_key(name))
        out.append(name)
    return out


def _name_key(name: str) -> str:
    """Names macOS and Windows treat as the same file: case and Unicode form ignored."""
    return unicodedata.normalize("NFC", name).casefold()


def _check_outdir(outdir: Path, inputs: Sequence[Path]) -> None:
    # Compare folders on disk, not path strings: case-insensitive file systems (macOS,
    # Windows), Unicode normalisation (macOS), symlinks, short names, drive mappings.
    if not outdir.exists():
        return  # a new folder holds no input
    for p in inputs:
        if p.parent.exists() and os.path.samefile(p.parent, outdir):
            raise CorrectError(
                f"output folder {outdir} holds the input {p.name}; choose a separate folder "
                "(originals are never written next to)",
                "outdir_holds_input",
                folder=str(outdir),
                file=str(p),
            )


def _check_space(outdir: Path, outputs: Sequence[Output]) -> None:
    need = sum(o.bytes for o in outputs)
    probe_dir = outdir
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    free = shutil.disk_usage(probe_dir).free
    if need > 0.95 * free:
        raise CorrectError(
            f"needs {need / 2**30:.1f} GiB but only {free / 2**30:.1f} GiB are free on {probe_dir}",
            "not_enough_space",
            folder=str(probe_dir),
            need_bytes=need,
            free_bytes=free,
        )
