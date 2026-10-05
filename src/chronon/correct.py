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
import os
import shutil
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from chronon import align, audio, fcpxml

CAF_ABOVE_BYTES = 2**31  # WAV/AIFF: 4 GiB hard limit, some programs already fail at 2 GiB
# The check is a second, independent measurement on a different window grid. On acoustic
# pairs (a room recorder far from the stage) that alone moves the offset by up to ~0.35 ms,
# so a tighter bound would fail correct files. Correction bugs show up as drift.
VERIFY_OFFSET_TOL_S = 5e-4
VERIFY_DRIFT_TOL_PPM = 0.2
FORMATS = ("auto", "wav", "caf")
BLOCK_FRAMES = 1 << 16
WRITE_THREADS = min(4, os.cpu_count() or 1)

Progress = Callable[[str, int, int], None]  # (what, done, total) in real work units


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
    via: str = ""  # parallel track whose measurement this file shares
    check_at_s: list[float] = field(default_factory=list)  # output times to verify at
    verify_offset_ms: float | None = None
    verify_drift_ppm: float | None = None
    verified: bool | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def start_s(self) -> float:
        """Timeline time of the output's first sample."""
        return 0.0 if self.padded else self.position_s

    @property
    def bytes(self) -> int:
        return self.frames * self.channels * (4 if self.bits == 32 else self.bits // 8)


class CorrectError(ValueError):
    pass


Entry = tuple[Path, align.FileResult]


def analyse(
    refs: Sequence[Path],
    files: Sequence[Path],
    all_refs: bool = False,
    progress: Progress | None = None,
    separate: bool = False,
) -> list[Entry]:
    """Align the files; return entries for the reference tracks to export and the files.

    Of several reference tracks only those some file matched best are exported (plus the
    first), unless ``all_refs``; all of them are still used to find the best match."""
    report = progress or (lambda what, done, total: None)
    results = align.align_files(refs, files, progress=report, separate=separate)
    used = {Path(refs[0])} | {r.reference for r in results}
    identity = align.Alignment(0.0, 0.0, 1.0, False, 1, 1)
    entries: list[Entry] = [
        (Path(r), align.FileResult(Path(r), identity, "reference", is_reference=True))
        for r in refs
        if (all_refs or Path(r) in used) and Path(r) not in {Path(f) for f in files}
    ]
    entries += [(Path(f), r) for f, r in zip(files, results, strict=True)]
    return entries


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
) -> list[Output]:
    """Analyse, write, verify, write the timeline. Returns one Output per exported file."""
    refs, files, outdir = [Path(r) for r in refs], [Path(f) for f in files], Path(outdir)
    _check_outdir(outdir, refs + files)
    report = progress or (lambda what, done, total: None)
    entries = analyse(refs, files, all_refs, report, separate)
    outputs = plan(entries, outdir, rate, pad, fmt, overwrite)
    _check_space(outdir, outputs)

    outdir.mkdir(parents=True, exist_ok=True)
    write_all(outputs, report)

    verify(outputs, report)
    placed = _write_timeline(_corrected_items(outputs), outdir, name)
    for p in placed:
        if p.media.has_video:
            out = next(o for o in outputs if o.source == str(p.item.path))
            out.notes.append(f"video placed within ±{p.error_ms:.0f} ms")
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
    notes: list[str] = field(default_factory=list)


def sync(
    refs: Sequence[Path | str],
    files: Sequence[Path | str],
    outdir: Path | str,
    progress: Progress | None = None,
    all_refs: bool = False,
    name: str | None = None,
    separate: bool = False,
) -> list[Placement]:
    """Analyse and write a timeline of the original files (nothing corrected)."""
    refs, files, outdir = [Path(r) for r in refs], [Path(f) for f in files], Path(outdir)
    _check_outdir(outdir, refs + files)
    entries = analyse(refs, files, all_refs, progress, separate)
    zero = min(0.0, *(r.alignment.offset_s for _, r in entries))
    items = [
        fcpxml.Item(f, r.alignment.offset_s - zero, r.alignment.drift_ppm, group=r.device)
        for f, r in entries
    ]
    outdir.mkdir(parents=True, exist_ok=True)
    placed = _write_timeline(items, outdir, name)
    result = [
        Placement(
            str(f),
            r.alignment.offset_s - zero,
            r.alignment.drift_ppm,
            r.alignment.confidence,
            r.is_reference or r.alignment.reliable,
            p.error_ms,
            r.device,
            result_notes(r),
        )
        for (f, r), p in zip(entries, placed, strict=True)
    ]
    (outdir / "chronon-sync.json").write_text(
        json.dumps([asdict(r) for r in result], indent=2) + "\n"
    )
    return result


def timeline(outdir: Path | str, name: str | None = None) -> Path:
    """Rebuild the .fcpxml of an earlier ``correct`` or ``sync`` run from its report,
    without analysing or writing audio again."""
    outdir = Path(outdir)
    if (outdir / "chronon-report.json").exists():
        rows = json.loads((outdir / "chronon-report.json").read_text())
        fields = {f for f in Output.__dataclass_fields__}
        items = _corrected_items(
            [Output(**{k: v for k, v in r.items() if k in fields}) for r in rows]
        )
    elif (outdir / "chronon-sync.json").exists():
        rows = json.loads((outdir / "chronon-sync.json").read_text())
        items = [
            fcpxml.Item(
                Path(r["source"]), r["position_s"], r["drift_ppm"], group=r.get("device", "")
            )
            for r in rows
        ]
    else:
        raise CorrectError(f"{outdir} holds no chronon-report.json or chronon-sync.json")
    _write_timeline(items, outdir, name)
    return outdir / f"{name or outdir.name}.fcpxml"


def _corrected_items(outputs: Sequence[Output]) -> list[fcpxml.Item]:
    """Video originals (picture only) plus every corrected audio file."""
    items = []
    for o in outputs:
        if o.has_video:
            items.append(
                fcpxml.Item(Path(o.source), o.position_s, o.drift_ppm, True, group=o.device)
            )
        items.append(fcpxml.Item(Path(o.path), o.start_s, group=o.device))
    return items


def _write_timeline(items: list[fcpxml.Item], outdir: Path, name: str | None):
    name = name or outdir.name
    return fcpxml.write(items, outdir / f"{name}.fcpxml", name)


def result_notes(r: align.FileResult) -> list[str]:
    """How a file's placement was found, for reports."""
    notes = []
    if r.is_reference:
        notes.append("same clock and start as the reference")
    if r.via is not None:
        notes.append(f"measured via {r.via.name}")
    if r.drift_from is not None:
        notes.append(f"drift from {r.drift_from.name}")
    return notes


def plan(
    entries: Sequence[Entry],
    outdir: Path,
    rate: int,
    pad: bool,
    fmt: str,
    overwrite: bool,
) -> list[Output]:
    """Where every file goes on the timeline and what gets written."""
    if fmt not in FORMATS:
        raise CorrectError(f"unknown format {fmt!r}, expected one of {FORMATS}")
    zero = min(0.0, *(r.alignment.offset_s for _, r in entries))
    names = _output_names([e[0] for e in entries])
    outputs = []
    for (src, r), name in zip(entries, names, strict=True):
        a, is_ref = r.alignment, r.is_reference
        info = audio.probe(src)
        position = a.offset_s - zero
        pad_frames = round(position * rate) if pad else 0
        real_rate = info.sample_rate * (1 + a.drift_ppm * 1e-6)
        frames = pad_frames + round(info.frames * rate / real_rate)
        out = Output(
            source=str(src),
            path="",
            reference=str(r.reference),
            is_reference=is_ref,
            position_s=position,
            offset_s=a.offset_s,
            drift_ppm=a.drift_ppm,
            confidence=a.confidence,
            reliable=a.reliable,
            in_rate=info.sample_rate,
            out_rate=rate,
            channels=info.channels,
            bits=info.bits,
            padded=pad,
            pad_frames=pad_frames,
            frames=frames,
            format="wav",
        )
        out.has_video = info.has_video
        out.device = r.device
        out.via = str(r.via) if r.via is not None else ""
        out.notes.extend(result_notes(r))
        # where the analysis could measure this file, in output file time
        lead = out.position_s - out.start_s
        out.check_at_s = [lead + t / (1 + a.drift_ppm * 1e-6) for t in a.good_s]
        out.format = fmt if fmt != "auto" else ("caf" if out.bytes > CAF_ABOVE_BYTES else "wav")
        if fmt == "auto" and out.format == "caf":
            out.notes.append("written as CAF: over 2 GiB")
        if fmt == "wav" and out.bytes >= 2**32:
            raise CorrectError(f"{src.name}: {out.bytes / 2**30:.1f} GiB is too large for WAV")
        if not is_ref and not a.reliable:
            out.notes.append("NO RELIABLE MATCH: position and drift may be wrong")
        if info.has_video:
            out.notes.append("audio of a video file; the video itself is not changed")
        path = outdir / f"{name}.{out.format}"
        if path.exists() and not overwrite:
            raise CorrectError(f"{path} exists (use --overwrite)")
        out.path = str(path)
        outputs.append(out)
    return outputs


def write(out: Output, progress: Callable[[int, int], None] | None = None) -> None:
    """Stream the source through the resampler into the output file."""
    report = progress or (lambda done, total: None)
    subtype = {16: "PCM_16", 24: "PCM_24", 32: "FLOAT"}[out.bits]
    real_rate = out.in_rate * (1 + out.drift_ppm * 1e-6)
    same = out.in_rate == out.out_rate and out.drift_ppm == 0.0
    resampler = (
        None
        if same
        else soxr.ResampleStream(
            real_rate, out.out_rate, out.channels, dtype="float32", quality="VHQ"
        )
    )
    done = 0
    with sf.SoundFile(
        out.path, "w", out.out_rate, out.channels, subtype, format=out.format.upper()
    ) as f:
        silence = np.zeros((BLOCK_FRAMES, out.channels), dtype=np.float32)
        for start in range(0, out.pad_frames, BLOCK_FRAMES):
            n = min(BLOCK_FRAMES, out.pad_frames - start)
            f.write(silence[:n])
            done += n
            report(done, out.frames)
        blocks = audio.stream(out.source, out.channels, BLOCK_FRAMES)
        for block in _with_last(blocks):
            data, last = block
            if resampler is not None:
                data = resampler.resample_chunk(data, last=last)
            if len(data):
                f.write(np.clip(data, -1.0, 1.0))
                done += len(data)
                report(min(done, out.frames), out.frames)
    out.frames = done


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

    with ThreadPoolExecutor(max_workers=WRITE_THREADS, thread_name_prefix="chronon-write") as ex:
        list(ex.map(one, range(len(outputs))))  # re-raises the first error
    report("writing", total, total)


def verify(outputs: Sequence[Output], progress: Progress | None = None) -> None:
    """Measure every written file against its reference track's output again, at
    CHECK_WINDOWS windows spread over the file (excerpts only, nothing loaded whole)."""
    report = progress or (lambda what, done, total: None)
    by_source = {o.source: o for o in outputs}
    # parallel tracks share one correction: check the track it was measured through
    todo = [o for o in outputs if not o.is_reference and (not o.via or o.via == o.source)]
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
            out.notes.append(f"verification failed: {e}")
            continue
        # the error in the middle of the measured part, not extrapolated into padding
        mid = (min(a.good_s) + max(a.good_s)) / 2 if a.good_s else 0.0
        error = float(a.ref_time(mid) - mid) - expected
        out.verify_offset_ms = error * 1e3
        out.verify_drift_ppm = a.drift_ppm
        out.verified = (
            abs(error) <= VERIFY_OFFSET_TOL_S and abs(a.drift_ppm) <= VERIFY_DRIFT_TOL_PPM
        )
        if not out.verified:
            out.notes.append("VERIFICATION FAILED: output is not in sync with the reference")
    for o in outputs:
        lead = by_source.get(o.via)
        if o.via and o.via != o.source and lead is not None:
            o.verified = lead.verified
            o.verify_offset_ms, o.verify_drift_ppm = lead.verify_offset_ms, lead.verify_drift_ppm
            if lead.verified is False:
                o.notes.append("VERIFICATION FAILED (via its parallel track)")
    report("verifying", len(todo), len(todo))


def write_report(outputs: Sequence[Output], outdir: Path) -> None:
    data = [
        {k: v for k, v in asdict(o).items() if k != "check_at_s"} | {"start_s": o.start_s}
        for o in outputs
    ]
    (outdir / "chronon-report.json").write_text(json.dumps(data, indent=2) + "\n")
    (outdir / "chronon-report.txt").write_text(format_report(outputs) + "\n")


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
            f"{o.in_rate:>6}>{o.out_rate:<5} {check:>22}  {', '.join(o.notes)}"
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


def _output_names(sources: Sequence[Path]) -> list[str]:
    """File stems, made unique with the parent folder name where needed."""
    stems = [s.stem for s in sources]
    names = []
    for s, stem in zip(sources, stems, strict=True):
        name = stem if stems.count(stem) == 1 else f"{s.parent.name}_{stem}"
        base, n = name, 2
        while name in names:
            name, n = f"{base}_{n}", n + 1
        names.append(name)
    return names


def _check_outdir(outdir: Path, inputs: Sequence[Path]) -> None:
    out = outdir.resolve()
    for p in inputs:
        if p.resolve().parent == out:
            raise CorrectError(
                f"output folder {outdir} holds the input {p.name}; choose a separate folder "
                "(originals are never written next to)"
            )


def _check_space(outdir: Path, outputs: Sequence[Output]) -> None:
    need = sum(o.bytes for o in outputs)
    probe_dir = outdir
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    free = shutil.disk_usage(probe_dir).free
    if need > 0.95 * free:
        raise CorrectError(
            f"needs {need / 2**30:.1f} GiB but only {free / 2**30:.1f} GiB are free on {probe_dir}"
        )
