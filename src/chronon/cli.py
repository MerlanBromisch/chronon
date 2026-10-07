"""Command line interface."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import traceback
from collections.abc import Sequence
from pathlib import Path

from chronon import (
    __version__,
    align,
    analysis,
    audio,
    correct,
    devices,
    fcpxml,
    listen,
    messages,
    synth,
)
from chronon.messages import note


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chronon", description="Waveform-based sync for multi-device recordings."
    )
    parser.add_argument("--version", action="version", version=f"chronon {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("synth", help="write a synthetic test scenario with known truth")
    p.add_argument("outdir", help="directory for the WAV files and truth.json")
    p.add_argument("--preset", choices=tuple(synth.PRESETS), default="basic")
    p.add_argument("--signal", choices=synth.SIGNALS, help="override the preset's signal")
    p.add_argument("--seed", type=int, default=0)

    p = commands.add_parser(
        "devices", help="group files into devices and suggest a reference (before analysing)"
    )
    p.add_argument("files", nargs="+", help="all recordings of the project")
    p.add_argument("--separate", action="store_true", help="every file a device of its own")
    p.add_argument(
        "--save",
        metavar="FILE",
        help="keep the devices in FILE (edit names, order, grouping, reference), "
        "then 'analyze --devices FILE'",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output (docs/app.md)")

    p = commands.add_parser("analyze", help="measure offset and drift of files against a reference")
    _ref_args(p, export=False)
    p.add_argument("files", nargs="*", help="recordings to align to the reference")
    p.add_argument(
        "--devices",
        metavar="FILE",
        help="the devices saved by 'chronon devices --save' (possibly edited) instead of files",
    )
    p.add_argument(
        "--save",
        metavar="FILE",
        help="keep the analysis in FILE for 'sync' / 'correct --analysis' (no second analysis)",
    )

    p = commands.add_parser(
        "sync", help="write a Final Cut Pro timeline (FCPXML) of the original files"
    )
    _ref_args(p)
    p.add_argument("files", nargs="*", help="recordings to place (or --analysis)")
    p.add_argument("-o", "--out", required=True, help="output folder for the .fcpxml")
    p.add_argument("--name", help="project name (default: the output folder's name)")

    p = commands.add_parser(
        "correct", help="write drift- and rate-corrected audio on one timeline, then verify it"
    )
    _ref_args(p)
    p.add_argument("files", nargs="*", help="recordings to correct (or --analysis)")
    p.add_argument(
        "-o", "--out", required=True, help="output folder (must not be a folder holding any input)"
    )
    p.add_argument("--rate", type=int, default=48_000, help="output sample rate (default 48000)")
    p.add_argument(
        "--no-pad",
        dest="pad",
        action="store_false",
        help="do not pad with silence to timeline zero",
    )
    p.add_argument(
        "--format",
        choices=correct.FORMATS,
        default="auto",
        help="auto = WAV, or CAF for files over 2 GiB",
    )
    p.add_argument("--overwrite", action="store_true", help="replace existing output files")
    p.add_argument(
        "--timeline",
        choices=(*correct.TIMELINES, "none"),
        default="fcpxml",
        help="timeline for: fcpxml = Final Cut Pro / Logic (default), none = audio only",
    )
    p.add_argument("--name", help="project name (default: the output folder's name)")
    p.add_argument(
        "--join",
        action="store_true",
        help="one file per device: its clips joined, gaps filled with silence",
    )

    p = commands.add_parser(
        "timeline", help="rebuild the .fcpxml of an earlier sync/correct run from its report"
    )
    p.add_argument("outdir", help="output folder of the earlier run")
    p.add_argument("--name", help="project name (default: the folder's name)")

    p = commands.add_parser(
        "overview", help="waveform overviews (min/max peaks) of whole files, cached on disk"
    )
    p.add_argument("files", nargs="+", help="recordings")
    p.add_argument("--cache", required=True, metavar="DIR", help="cache folder for the overviews")
    p.add_argument("--json", action="store_true", help="machine-readable output (docs/app.md)")

    p = commands.add_parser("eval", help="run analyze on a 'chronon synth' folder and compare")
    p.add_argument("scene", help="folder written by 'chronon synth'")

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        # file names a Windows console code page cannot show must not stop a run
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    out = _JsonOut() if getattr(args, "json", False) else None
    try:
        if args.command == "synth":
            return _synth(args)
        if args.command == "devices":
            return _devices(args, out)
        if args.command == "analyze":
            return _analyze(args, out)
        if args.command == "sync":
            return _sync(args, out)
        if args.command == "correct":
            return _correct(args, out)
        if args.command == "timeline":
            print(f"timeline: {correct.timeline(args.outdir, args.name)}")
            return 0
        if args.command == "overview":
            return _overview(args, out)
        if args.command == "eval":
            return _eval(args)
    except (audio.AudioError, ValueError) as e:
        if out is not None:
            out.error(e)
            return 1
        parser.exit(1, f"chronon: error: {e}\n")
    except Exception as e:
        if out is None:
            raise
        traceback.print_exc()
        out.emit("error", code=None, message=f"{type(e).__name__}: {e}")
        return 1
    return 2


def _ref_args(p: argparse.ArgumentParser, export: bool = True) -> None:
    p.add_argument(
        "-r",
        "--ref",
        action="append",
        default=[],
        metavar="TRACK",
        help="reference track; repeat for several sample-parallel tracks of one device "
        "(e.g. all channels of a desk) and each file uses the one it matches best. "
        "Default: the first file",
    )
    p.add_argument(
        "--separate",
        action="store_true",
        help="measure every file on its own instead of grouping them into devices",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output for apps and scripts: one JSON object per line on "
        "stdout (progress, result, error), see docs/app.md",
    )
    if export:
        p.add_argument(
            "--fps",
            type=fcpxml.frame_rate,
            metavar="RATE",
            help="timeline frame rate, e.g. 25, 29.97, 23.976 (default: the videos', else 25)",
        )
        p.add_argument(
            "--analysis",
            metavar="FILE",
            help="use an analysis saved by 'analyze --save' instead of analysing the files again",
        )
        p.add_argument(
            "--all-refs",
            action="store_true",
            help="export every reference track, not only those a file matched best",
        )


def _refs_and_files(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    if getattr(args, "analysis", None):
        if args.files or args.ref:
            raise ValueError("give either files or --analysis, not both")
        return [], []
    refs, files = (args.ref, args.files) if args.ref else (args.files[:1], args.files[1:])
    if not files:
        raise ValueError("give a reference and at least one more file")
    return refs, files


def _synth(args: argparse.Namespace) -> int:
    scenario = dataclasses.replace(synth.preset(args.preset), seed=args.seed)
    if args.signal:
        scenario = dataclasses.replace(scenario, signal=args.signal)
    truth_path = synth.write(scenario, args.outdir)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    for device in truth["devices"]:
        for clip in device["clips"]:
            print(
                f"{clip['file']:<16} start {clip['start_s']:>9.3f} s  "
                f"drift {device['drift_ppm']:+7.1f} ppm  {device['sample_rate']} Hz"
            )
    print(f"truth: {truth_path}")
    return 0


def _devices(args: argparse.Namespace, out: _JsonOut | None) -> int:
    infos: dict[Path, audio.Info] = {}

    def probe(path: Path) -> audio.Info:
        if path not in infos:
            infos[path] = audio.probe(path)
        return infos[path]

    layout = devices.detect(args.files, args.separate, probe, align.loudest)
    saved = layout.save(args.save) if args.save else None
    rows = [_device_row(d, infos) for d in layout.devices]
    if out is not None:
        out.emit(
            "result",
            command="devices",
            layout=layout.to_dict(),
            devices=rows,
            saved=None if saved is None else str(saved),
        )
        return 0
    print(f"{'':2}{'device':<24} {'kind':<26} {'rate':>8}  {'duration':>9}")
    for k, (d, row) in enumerate(zip(layout.devices, rows, strict=True)):
        mark = "*" if k == layout.suggested else " "
        print(
            f"{mark} {d.name[:24]:<24} {row['kind_text']:<26} {row['sample_rate'] or '-':>8}  "
            f"{_duration(row['duration_s']):>9}"
        )
    tracks = ", ".join(t.name for t in layout.tracks)
    print(f"* suggested reference: {layout.devices[layout.suggested].name} ({tracks})")
    if saved is not None:
        print(f"devices: {saved}")
    return 0


def _device_row(d: devices.Device, infos: dict[Path, audio.Info]) -> dict:
    """What the devices step shows: kind, rate, channels, covered time."""
    first = [infos[c.tracks[0]] for c in d.clips]
    tracks = max(len(c.tracks) for c in d.clips)
    video = any(i.has_video for i in first)
    rates = {i.sample_rate for i in first}
    if tracks > 1:
        kind, text = "tracks", f"{tracks} parallel tracks"
    elif len(d.clips) > 1:
        kind = "video_clips" if video else "clips"
        text = f"{len(d.clips)} {'video clips' if video else 'clips'}"
    else:
        kind, text = ("video" if video else "file"), ("video" if video else "1 file")
    return {
        "name": d.name,
        "kind": kind,
        "kind_text": text,
        "clips": len(d.clips),
        "tracks": tracks,
        "files": len(d.files),
        "has_video": video,
        "sample_rate": rates.pop() if len(rates) == 1 else None,
        "channels": max(i.channels for i in first),
        "duration_s": sum(i.duration_s for i in first),
    }


def _analyze(args: argparse.Namespace, out: _JsonOut | None) -> int:
    layout = devices.Layout.load(args.devices) if args.devices else None
    if layout is not None:
        if args.files or args.ref:
            raise ValueError("give either files or --devices, not both")
        refs, files = list(layout.tracks), [f for f in layout.files if f not in layout.tracks]
    else:
        refs, files = _refs_and_files(args)
    progress = _Progress(["analysing"], out)
    measured = analysis.measure(refs, files, progress, args.separate, layout)
    results = measured.results
    progress.finish()
    saved = measured.save(args.save) if args.save else None
    if out is not None:
        rows = [
            _analysis_row(path, r, len(refs) > 1) | {"placement": p}
            for path, r, p in zip(files, results, measured.placements, strict=True)
        ]
        out.emit(
            "result",
            command="analyze",
            refs=[str(r) for r in refs],
            analysis=None if saved is None else str(saved),
            frame_rate=str(measured.frame_rate),
            files=rows,
        )
        return 0
    print(f"reference: {', '.join(Path(r).name for r in refs)}")
    print(
        f"{'file':<24} {'device':<12} {'offset s':>13} {'drift ppm':>10} {'confidence':>10}"
        "  windows  notes"
    )
    for path, r in zip(files, results, strict=True):
        a = r.alignment
        via = r.reference if len(refs) > 1 and not r.is_reference else None
        print(
            f"{Path(path).name:<24} {r.device[:12]:<12} {a.offset_s:>13.6f} {a.drift_ppm:>+10.2f} "
            f"{a.confidence:>10.2f}  {a.windows_used:>4}/{a.windows_total:<4} "
            + messages.texts(([] if r.is_reference else _notes(a, via)) + correct.result_notes(r))
        )
    if saved is not None:
        print(f"analysis: {saved}")
    return 0


def _analysis_row(path: str, r: align.FileResult, several_refs: bool) -> dict:
    a = r.alignment
    via = r.reference if several_refs and not r.is_reference else None
    return {
        "file": str(path),
        "device": r.device,
        "reference": str(r.reference),
        "is_reference": r.is_reference,
        "offset_s": a.offset_s,
        "drift_ppm": a.drift_ppm,
        "confidence": a.confidence,
        "reliable": r.is_reference or a.reliable,
        "inverted": a.inverted,
        "wander_ms": a.wander_ms,
        "windows_used": a.windows_used,
        "windows_total": a.windows_total,
        "via": _str(r.via),
        "drift_from": _str(r.drift_from),
        "linked_via": _str(r.linked_via),
        "notes": ([] if r.is_reference else _notes(a, via)) + correct.result_notes(r),
    }


def _saved(args: argparse.Namespace) -> analysis.Analysis | None:
    return analysis.Analysis.load(args.analysis) if args.analysis else None


def _str(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _timeline_path(args: argparse.Namespace) -> Path:
    return Path(args.out) / ((args.name or Path(args.out).name) + ".fcpxml")


def _correct(args: argparse.Namespace, out: _JsonOut | None) -> int:
    refs, files = _refs_and_files(args)
    progress = _Progress(["analysing", "writing", "verifying"], out)
    outputs = correct.run(
        refs,
        files,
        args.out,
        args.rate,
        args.pad,
        args.format,
        args.overwrite,
        progress=progress,
        all_refs=args.all_refs,
        name=args.name,
        separate=args.separate,
        join=args.join,
        measured=_saved(args),
        timeline=None if args.timeline == "none" else args.timeline,
        frame=args.fps,
    )
    progress.finish()
    failed = [o for o in outputs if o.verified is False]
    if out is not None:
        out.emit(
            "result",
            command="correct",
            report=str(Path(args.out) / "chronon-report.json"),
            timeline=None if args.timeline == "none" else str(_timeline_path(args)),
            failed=len(failed),
            files=correct.report_rows(outputs),
        )
        return 1 if failed else 0
    print(correct.format_report(outputs))
    print(f"report: {Path(args.out) / 'chronon-report.txt'}")
    if failed:
        print(f"{len(failed)} file(s) failed verification", file=sys.stderr)
        return 1
    return 0


def _sync(args: argparse.Namespace, out: _JsonOut | None) -> int:
    refs, files = _refs_and_files(args)
    progress = _Progress(["analysing"], out)
    result = correct.sync(
        refs,
        files,
        args.out,
        progress,
        args.all_refs,
        args.name,
        separate=args.separate,
        measured=_saved(args),
        frame=args.fps,
    )
    progress.finish()
    if out is not None:
        out.emit(
            "result",
            command="sync",
            report=str(Path(args.out) / "chronon-sync.json"),
            timeline=str(_timeline_path(args)),
            files=[dataclasses.asdict(r) for r in result],
        )
        return 0
    print(f"{'file':<28} {'timeline s':>12} {'drift ppm':>10} {'off at ends':>12}  notes")
    for r in result:
        notes = ([] if r.reliable else [note("no_reliable_match")]) + r.notes
        print(
            f"{Path(r.source).name:<28} {r.position_s:>12.6f} {r.drift_ppm:>+10.2f} "
            f"{r.error_ms:>9.1f} ms  {messages.texts(notes)}"
        )
    print(f"timeline: {_timeline_path(args)}")
    return 0


def _overview(args: argparse.Namespace, out: _JsonOut | None) -> int:
    progress = _Progress(["overview"], out)
    rows = []
    for f in args.files:
        peaks = listen.overview(f, args.cache, progress)
        rows.append(
            {
                "file": str(f),
                "overview": str(listen.overview_path(f, args.cache)),
                "peaks_per_s": listen.PEAKS_PER_S,
                "peaks": len(peaks),
            }
        )
    progress.finish()
    if out is not None:
        out.emit("result", command="overview", files=rows)
        return 0
    for row in rows:
        print(f"{Path(row['file']).name}: {row['overview']}")
    return 0


class _JsonOut:
    """``--json``: one JSON object per line on stdout, flushed at once (see docs/app.md)."""

    VERSION = 2  # 2: notes and errors carry codes (chronon.messages)

    def __init__(self):
        # A windowed (no console) app on Windows can start its child with sys.stdout = None
        # even though it passed a pipe; fd 1 is still that pipe.
        self.stream = sys.stdout
        if self.stream is None:
            self.stream = open(1, "w", encoding="utf-8", closefd=False)  # noqa: SIM115

    def emit(self, event: str, **fields) -> None:
        self.stream.write(json.dumps({"v": self.VERSION, "event": event, **fields}) + "\n")
        self.stream.flush()

    def error(self, e: Exception) -> None:
        """An ``error`` event: the code and its fields when the user can fix it, else
        ``code`` null and only the message."""
        if isinstance(e, messages.UserError) and e.code is not None:
            self.emit("error", code=e.code, message=str(e), **e.fields)
        else:
            self.emit("error", code=None, message=str(e))


class _Progress:
    """Step, percentage and time left in this step: one status line on a terminal, or
    progress events (at most ten a second) with ``--json``.

    The time left is extrapolated from the rate of real work done so far (seconds of
    audio analysed, samples written, files checked), not from a guess."""

    def __init__(self, steps: Sequence[str], out: _JsonOut | None = None):
        self.steps = list(steps)
        self.step = ""
        self.started = 0.0
        self.sent = 0.0
        self.out = out
        self.tty = out is None and sys.stderr.isatty()

    def __call__(self, what: str, done: int, total: int) -> None:
        if not self.tty and self.out is None:
            return
        step = what.split(" ")[0]
        now = time.monotonic()
        if step != self.step:
            if self.step and self.tty:
                print(file=sys.stderr)
            self.step, self.started, self.sent = step, now, 0.0
        elapsed = now - self.started
        frac = done / total if total else 1.0
        # no estimate before a little real work is done: it would be noise
        left = elapsed * (1 - frac) / frac if 0.03 <= frac < 1 and elapsed > 1.5 else None
        number = self.steps.index(step) + 1 if step in self.steps else 0
        if self.out is not None:
            if frac < 1 and self.sent and now - self.sent < 0.1:
                return
            self.sent = now
            self.out.emit(
                "progress",
                step=step,
                step_number=number,
                steps=len(self.steps),
                what=what,
                done=frac,
                left_s=left,
            )
            return
        prefix = f"[{number}/{len(self.steps)}] " if number else ""
        line = f"{prefix}{what}: {100 * frac:3.0f} %"
        if left is not None:
            line += f"  about {_duration(left)} left"
        print(f"\r{line:<78}", end="", file=sys.stderr, flush=True)

    def finish(self) -> None:
        if self.tty and self.step:
            print(file=sys.stderr)


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{max(seconds, 1)} s"
    return f"{seconds // 60} min {seconds % 60:02d} s"


def _notes(a: align.Alignment, via: Path | None) -> list[messages.Note]:
    notes = []
    if not a.reliable:
        notes.append(note("no_reliable_match"))
    if a.inverted:
        notes.append(note("inverted"))
    if a.wander_ms > 1.0:
        notes.append(note("clock_wanders", ms=a.wander_ms))
    if via:
        notes.append(note("matched_track", file=str(via)))
    return notes


def _eval(args: argparse.Namespace) -> int:
    scene = Path(args.scene)
    truth = json.loads((scene / synth.TRUTH_FILE).read_text(encoding="utf-8"))
    ref_dev = truth["devices"][0]
    ref_clip = ref_dev["clips"][0]
    others = [(d, c) for d in truth["devices"] for c in d["clips"] if c is not ref_clip]
    results = align.align_files(
        [scene / ref_clip["file"]], [scene / c["file"] for _, c in others], separate=True
    )
    print(f"reference: {ref_clip['file']}")
    print(
        f"{'file':<16} {'offset err ms':>13} {'drift ppm':>10} {'true ppm':>9} {'confidence':>10}"
    )
    for (dev, clip), result in zip(others, results, strict=True):
        a = result.alignment
        offset, drift = synth.expected_alignment(
            ref_clip["start_s"], ref_dev["drift_ppm"], clip["start_s"], dev["drift_ppm"]
        )
        print(
            f"{clip['file']:<16} {(a.offset_s - offset) * 1e3:>+13.3f} {a.drift_ppm:>+10.2f} "
            f"{drift:>+9.2f} {a.confidence:>10.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
