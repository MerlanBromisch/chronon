"""Benchmark on the live musical (the scenario named in ROADMAP.md Goals).

    uv run python scripts/bench.py sync            # 18 desk tracks as reference, 7 files
    uv run python scripts/bench.py correct --refs 2 -o ~/Documents/Chronon\\ Test/bench

Prints wall time per phase and peak memory (this process plus ffmpeg children). The media
is not in the repository; set CHRONON_MUSICAL to the folder (default: the T7 drive).
Results are appended to bench-results.jsonl next to this script.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

from chronon import __version__, correct

ROOT = Path(os.environ.get("CHRONON_MUSICAL", "/Volumes/T7/Musical Dienstag"))
DESK = ROOT / "Musical 23.6.26 Presonus.logicx/Media/Audio Files"
X32 = ROOT / "Musical 23.6.26 x32.logicx/Media/Audio Files"
FILES = [
    X32 / "Ohne Namen_24#01.aif",
    ROOT / "ZOOM0003.WAV",
    ROOT / "ZOOM0004.WAV",
    ROOT / "C2378.MP4",
    ROOT / "C2379.MP4",
    ROOT / "R62_0040.MP4",
    ROOT / "R62_0041.MP4",
]
# with --refs 2: the two tracks the files actually match (17 = band leader, 18 = room)
BEST = [17, 18]


def _peak_mb() -> tuple[float, float]:
    scale = 1 if sys.platform == "darwin" else 1024  # macOS reports bytes, Linux KiB
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale / 2**20
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale / 2**20
    return own, kids


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("sync", "correct"))
    p.add_argument("--refs", type=int, default=18, help="18 = all desk tracks, 2 = the best two")
    p.add_argument("-o", "--out", help="output folder (default: a temporary one, removed)")
    args = p.parse_args()

    refs = [
        DESK / f"Musical 23.6.26_{i} #01.wav" for i in (BEST if args.refs == 2 else range(1, 19))
    ]
    missing = [f for f in refs + FILES if not f.exists()]
    if missing:
        print(f"missing media, e.g. {missing[0]}", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="chronon-bench-"))
    phases: dict[str, float] = {}
    t0 = last = time.perf_counter()
    current = ""

    def progress(what: str, done: int, total: int) -> None:
        nonlocal last, current
        phase = what.split(" ")[0]
        if phase != current:
            now = time.perf_counter()
            if current:
                phases[current] = phases.get(current, 0.0) + now - last
            current, last = phase, now

    if args.mode == "sync":
        correct.sync(refs, FILES, out, progress, name="bench")
    else:
        correct.run(refs, FILES, out, progress=progress, overwrite=True, name="bench")
    end = time.perf_counter()
    if current:
        phases[current] = phases.get(current, 0.0) + end - last
    own, kids = _peak_mb()
    result = {
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "version": __version__,
        "mode": args.mode,
        "refs": len(refs),
        "files": len(FILES),
        "total_s": round(end - t0, 1),
        "phases_s": {k: round(v, 1) for k, v in phases.items()},
        "peak_mb": round(own),
        "peak_ffmpeg_mb": round(kids),
    }
    print(json.dumps(result, indent=2))
    with open(Path(__file__).with_name("bench-results.jsonl"), "a") as f:
        f.write(json.dumps(result) + "\n")
    if not args.out:
        shutil.rmtree(out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
