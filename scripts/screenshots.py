"""README screenshots of the app on real material, light and dark, at twice the pixels.

usage: QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 uv run python scripts/screenshots.py FILE...
Writes docs/screenshots/{light,dark}-{devices,result,listen,export}.png. The media are the
user's (the musical, CHRONON_MUSICAL), not part of the repo; overviews come from a cache in a
temporary folder (computed once per run)."""

import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from chronon.gui import settings
from chronon.gui.window import Window

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SIZE = (1240, 800)


def pump(app: QApplication, cond, timeout: float = 1800.0) -> None:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)


def wait(app: QApplication, seconds: float) -> None:
    t = time.monotonic()
    pump(app, lambda: time.monotonic() - t > seconds)


def shoot(app: QApplication, look: str, paths: list[str]) -> None:
    """One theme: walk the steps and save four pictures."""
    win = Window(appearance=look)
    win.menuBar().hide()  # offscreen draws the macOS menu bar into the window
    win.resize(*SIZE)
    win.show()

    def shot(name: str) -> None:
        wait(app, 0.5)
        win.grab().save(str(OUT / f"{look}-{name}.png"))

    win.files.add(paths)
    pump(app, lambda: not win.files.reading)
    win.go_on()
    pump(app, lambda: not win.devices.detecting)
    pump(app, lambda: len(win.devices.audition.wave.peaks) > 0)
    shot("devices")
    win.go_on()
    pump(app, lambda: win.sync.state != "running")
    pump(app, lambda: win.step == 3)
    overviews = win.overviews
    pump(app, lambda: not overviews.jobs and not overviews.ahead)  # the timeline's waveforms
    win.result.rebuild()  # with the waveforms now computed
    shot("result")
    win.go_on()
    lp = win.listen
    pump(app, lambda: len(lp.ref_wave.peaks) > 0 and len(lp.file_wave.peaks) > 0)
    shot("listen")
    win.go_on()
    win.export.folder.setText("~/Movies/Chronon/Musical")
    shot("export")
    win.close()
    wait(app, 0.5)


def main(paths: list[str]) -> None:
    app = QApplication(sys.argv)
    cache = Path(tempfile.gettempdir()) / "chronon-screenshots-cache"
    settings.cache_dir = lambda: cache
    OUT.mkdir(parents=True, exist_ok=True)
    for look in ("light", "dark"):
        shoot(app, look, paths)


if __name__ == "__main__":
    main(sys.argv[1:])
