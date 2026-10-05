"""Command line interface."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from chronon import __version__, align, audio, correct, synth


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

    p = commands.add_parser("analyze", help="measure offset and drift of files against a reference")
    _ref_args(p, export=False)
    p.add_argument("files", nargs="+", help="recordings to align to the reference")

    p = commands.add_parser(
        "sync", help="write a Final Cut Pro timeline (FCPXML) of the original files"
    )
    _ref_args(p)
    p.add_argument("files", nargs="+", help="recordings to place")
    p.add_argument("-o", "--out", required=True, help="output folder for the .fcpxml")
    p.add_argument("--name", help="project name (default: the output folder's name)")

    p = commands.add_parser(
        "correct", help="write drift- and rate-corrected audio on one timeline, then verify it"
    )
    _ref_args(p)
    p.add_argument("files", nargs="+", help="recordings to correct")
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
    p.add_argument("--name", help="project name (default: the output folder's name)")

    p = commands.add_parser(
        "timeline", help="rebuild the .fcpxml of an earlier sync/correct run from its report"
    )
    p.add_argument("outdir", help="output folder of the earlier run")
    p.add_argument("--name", help="project name (default: the folder's name)")

    p = commands.add_parser("eval", help="run analyze on a 'chronon synth' folder and compare")
    p.add_argument("scene", help="folder written by 'chronon synth'")

    args = parser.parse_args(argv)
    try:
        if args.command == "synth":
            return _synth(args)
        if args.command == "analyze":
            return _analyze(args)
        if args.command == "sync":
            return _sync(args)
        if args.command == "correct":
            return _correct(args)
        if args.command == "timeline":
            print(f"timeline: {correct.timeline(args.outdir, args.name)}")
            return 0
        if args.command == "eval":
            return _eval(args)
    except (audio.AudioError, ValueError) as e:
        parser.exit(1, f"chronon: error: {e}\n")
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
    if export:
        p.add_argument(
            "--all-refs",
            action="store_true",
            help="export every reference track, not only those a file matched best",
        )


def _refs_and_files(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    refs, files = (args.ref, args.files) if args.ref else (args.files[:1], args.files[1:])
    if not files:
        raise ValueError("give a reference and at least one more file")
    return refs, files


def _synth(args: argparse.Namespace) -> int:
    scenario = dataclasses.replace(synth.preset(args.preset), seed=args.seed)
    if args.signal:
        scenario = dataclasses.replace(scenario, signal=args.signal)
    truth_path = synth.write(scenario, args.outdir)
    truth = json.loads(truth_path.read_text())
    for device in truth["devices"]:
        for clip in device["clips"]:
            print(
                f"{clip['file']:<16} start {clip['start_s']:>9.3f} s  "
                f"drift {device['drift_ppm']:+7.1f} ppm  {device['sample_rate']} Hz"
            )
    print(f"truth: {truth_path}")
    return 0


def _analyze(args: argparse.Namespace) -> int:
    refs, files = _refs_and_files(args)
    results = align.align_files(refs, files)
    print(f"reference: {', '.join(Path(r).name for r in refs)}")
    print(f"{'file':<24} {'offset s':>13} {'drift ppm':>10} {'confidence':>10}  windows  notes")
    for path, (ref_index, a) in zip(files, results, strict=True):
        print(
            f"{Path(path).name:<24} {a.offset_s:>13.6f} {a.drift_ppm:>+10.2f} "
            f"{a.confidence:>10.2f}  {a.windows_used:>4}/{a.windows_total:<4} "
            + ", ".join(_notes(a, Path(refs[ref_index]).name if len(refs) > 1 else None))
        )
    return 0


def _correct(args: argparse.Namespace) -> int:
    refs, files = _refs_and_files(args)
    outputs = correct.run(
        refs,
        files,
        args.out,
        args.rate,
        args.pad,
        args.format,
        args.overwrite,
        progress=_progress,
        all_refs=args.all_refs,
        name=args.name,
    )
    print(correct.format_report(outputs))
    print(f"report: {Path(args.out) / 'chronon-report.txt'}")
    failed = [o for o in outputs if o.verified is False]
    if failed:
        print(f"{len(failed)} file(s) failed verification", file=sys.stderr)
        return 1
    return 0


def _sync(args: argparse.Namespace) -> int:
    refs, files = _refs_and_files(args)
    result = correct.sync(refs, files, args.out, _progress, args.all_refs, args.name)
    print(f"{'file':<28} {'timeline s':>12} {'drift ppm':>10} {'off at ends':>12}  notes")
    for r in result:
        note = "" if r.reliable else "NO RELIABLE MATCH"
        print(
            f"{Path(r.source).name:<28} {r.position_s:>12.6f} {r.drift_ppm:>+10.2f} "
            f"{r.error_ms:>9.1f} ms  {note}"
        )
    print(f"timeline: {Path(args.out) / ((args.name or Path(args.out).name) + '.fcpxml')}")
    return 0


def _progress(what: str, done: int, total: int) -> None:
    if not sys.stderr.isatty():
        return
    pct = 100 * done / total if total else 100
    end = "\n" if done >= total else ""
    print(f"\r{what}: {pct:5.1f} %", end=end, file=sys.stderr, flush=True)


def _notes(a: align.Alignment, via: str | None) -> list[str]:
    notes = []
    if not a.reliable:
        notes.append("NO RELIABLE MATCH")
    if a.inverted:
        notes.append("inverted")
    if a.wander_ms > 1.0:
        notes.append(f"clock wanders ±{a.wander_ms:.1f} ms")
    if via:
        notes.append(f"via {via}")
    return notes


def _eval(args: argparse.Namespace) -> int:
    scene = Path(args.scene)
    truth = json.loads((scene / synth.TRUTH_FILE).read_text())
    ref_dev = truth["devices"][0]
    ref_clip = ref_dev["clips"][0]
    others = [(d, c) for d in truth["devices"] for c in d["clips"] if c is not ref_clip]
    results = align.align_files([scene / ref_clip["file"]], [scene / c["file"] for _, c in others])
    print(f"reference: {ref_clip['file']}")
    print(
        f"{'file':<16} {'offset err ms':>13} {'drift ppm':>10} {'true ppm':>9} {'confidence':>10}"
    )
    for (dev, clip), (_, a) in zip(others, results, strict=True):
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
