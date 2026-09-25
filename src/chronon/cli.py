"""Command line interface."""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

from chronon import __version__, align, audio, synth


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
    p.add_argument("reference", help="reference recording (usually the best audio)")
    p.add_argument("files", nargs="+", help="recordings to align to the reference")

    p = commands.add_parser("eval", help="run analyze on a 'chronon synth' folder and compare")
    p.add_argument("scene", help="folder written by 'chronon synth'")

    args = parser.parse_args(argv)
    try:
        if args.command == "synth":
            return _synth(args)
        if args.command == "analyze":
            return _analyze(args)
        if args.command == "eval":
            return _eval(args)
    except (audio.AudioError, ValueError) as e:
        parser.exit(1, f"chronon: error: {e}\n")
    return 2


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
    results = align.align_files(args.reference, args.files)
    print(f"reference: {args.reference}")
    print(f"{'file':<24} {'offset s':>12} {'drift ppm':>10} {'confidence':>10}  windows")
    for path, a in zip(args.files, results, strict=True):
        flag = "  inverted" if a.inverted else ""
        print(
            f"{Path(path).name:<24} {a.offset_s:>12.6f} {a.drift_ppm:>+10.2f} "
            f"{a.confidence:>10.2f}  {a.windows_used}/{a.windows_total}{flag}"
        )
    return 0


def _eval(args: argparse.Namespace) -> int:
    scene = Path(args.scene)
    truth = json.loads((scene / synth.TRUTH_FILE).read_text())
    ref_dev = truth["devices"][0]
    ref_clip = ref_dev["clips"][0]
    others = [(d, c) for d in truth["devices"] for c in d["clips"] if c is not ref_clip]
    results = align.align_files(scene / ref_clip["file"], [scene / c["file"] for _, c in others])
    print(f"reference: {ref_clip['file']}")
    print(
        f"{'file':<16} {'offset err ms':>13} {'drift ppm':>10} {'true ppm':>9} {'confidence':>10}"
    )
    for (dev, clip), a in zip(others, results, strict=True):
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
