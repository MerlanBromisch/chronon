"""Entry point of the app and, with ``--worker``, of its child processes.

``--worker ARGS`` runs ``chronon ARGS`` (the CLI, see docs/app.md), so the frozen app needs no
second executable. The worker never imports Qt: it starts fast and stays small."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if getattr(sys, "frozen", False):
        # bundled ffmpeg / ffprobe, for this process and the workers it starts
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "bin"
        os.environ["PATH"] = str(bundled) + os.pathsep + os.environ.get("PATH", "")
    if argv[:1] == ["--worker"]:
        from chronon.cli import main as cli

        return cli(argv[1:])
    from chronon.gui import app

    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
