"""Analysis in a child process of the GUI, speaking JSON lines on stdout.

Spike version of the planned ``--json`` contract (docs/app.md): progress events while it
works, one result event at the end. The GUI cancels by killing the process."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence

from chronon import align

VERSION = 1


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="chronon-worker")
    parser.add_argument("-r", "--ref", action="append", default=[], metavar="TRACK")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    refs, files = (args.ref, args.files) if args.ref else (args.files[:1], args.files[1:])
    out = _stdout()
    try:
        results = align.align_files(refs, files, progress=_Progress(out))
    except Exception as err:  # the GUI shows it; a traceback would only reach stderr
        _emit(out, "error", message=f"{type(err).__name__}: {err}")
        return 1
    rows = []
    for path, r in zip(files, results, strict=True):
        a = r.alignment
        rows.append(
            {
                "file": str(path),
                "device": r.device,
                "reference": str(r.reference),
                "is_reference": r.is_reference,
                "offset_s": a.offset_s,
                "drift_ppm": a.drift_ppm,
                "confidence": a.confidence,
                "reliable": a.reliable,
                "wander_ms": a.wander_ms,
            }
        )
    _emit(out, "result", refs=[str(r) for r in refs], files=rows)
    return 0


def _stdout():
    # A windowed (no console) build on Windows can start with sys.stdout = None even when
    # the parent passed a pipe; fd 1 is still that pipe.
    if sys.stdout is not None:
        return sys.stdout
    return open(1, "w", encoding="utf-8", closefd=False)  # noqa: SIM115


def _emit(out, event: str, **fields) -> None:
    out.write(json.dumps({"v": VERSION, "event": event, **fields}) + "\n")
    out.flush()


class _Progress:
    """Progress events, at most ten per second; time left from the real work done."""

    def __init__(self, out):
        self.out = out
        self.step = ""
        self.started = 0.0
        self.last = 0.0

    def __call__(self, what: str, done: int, total: int) -> None:
        now = time.monotonic()
        step = what.split(" ")[0]
        if step != self.step:
            self.step, self.started = step, now
        frac = done / total if total else 1.0
        if now - self.last < 0.1 and frac < 1:
            return
        self.last = now
        elapsed = now - self.started
        eta = elapsed * (1 - frac) / frac if 0 < frac < 1 and elapsed > 1.5 else None
        _emit(self.out, "progress", step=what, done=frac, eta_s=eta)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
