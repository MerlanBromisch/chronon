"""Start the window; ``--selftest FOLDER`` runs the whole app without a screen."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QLibraryInfo, QTranslator
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from chronon.gui import menus
from chronon.gui.window import Window

STEP_TIMEOUT_S = 300.0


class SelftestError(RuntimeError):
    pass


def walk(win: Window, folder: str | Path, out: Path, timeout: float = STEP_TIMEOUT_S) -> dict:
    """Drive every step as a user would: read ``folder``, take the suggested devices and
    reference, sync, look at the result, listen, export corrected audio to ``out``.
    Returns what each step showed; raises SelftestError when a step fails."""
    app = QApplication.instance()

    def wait(condition, what: str) -> None:
        end = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > end:
                raise SelftestError(f"timed out: {what}")
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)

    seen: dict = {}
    win.files.add([folder])
    wait(lambda: not win.files.reading, "reading the files")
    entries = win.project.entries
    seen["files"] = len(entries)
    unreadable = [e.path.name for e in entries if e.info is None]
    if unreadable or len(entries) < 2:
        raise SelftestError(f"files: {len(entries)}, unreadable: {unreadable}")

    win.go_on()
    wait(lambda: not win.devices.detecting, "detecting devices")
    layout = win.project.layout
    if layout is None or not win.devices.ready:
        raise SelftestError(f"devices: {win.devices.error or 'no layout'}")
    seen["devices"] = [d.name for d in layout.devices]

    win.go_on()  # Sync starten
    wait(lambda: win.sync.state != "running", "sync")
    if win.sync.state != "done":
        raise SelftestError(f"sync: {win.sync.state} {getattr(win.sync, 'error', '')}")
    rows = win.project.result["files"]
    seen["unreliable"] = [Path(r["file"]).name for r in rows if not r["reliable"]]
    wait(lambda: win.step == 3, "the result page")

    win.go_on()  # Hören
    if win.listen.current is None:
        raise SelftestError("listen: no file")
    waves = (win.listen.ref_wave, win.listen.file_wave)
    wait(lambda: all(len(w.peaks) for w in waves), "the waveforms")  # overviews, from jobs
    seen["listen"] = win.listen.name.text()

    win.go_on()  # Export
    win.export.correct_choice.radio.setChecked(True)
    win.export.folder.setText(str(out))
    win.go_on()  # Exportieren
    wait(lambda: win.export.state != "running", "export")
    if win.export.state != "done":
        raise SelftestError(f"export: {win.export.state} {win.export.error}")
    outcome = win.export.outcome["files"]
    seen["exported"] = sorted(Path(r["path"]).name for r in outcome)
    seen["failed"] = [Path(r["path"]).name for r in outcome if r.get("verified") is False]
    return seen


def selftest(folder: str) -> int:
    """For CI and a frozen build: the whole flow on ``folder``; exit 0 when every file was
    readable, reliable and its corrected output passed the check."""
    app = QApplication.instance() or QApplication(sys.argv)
    win = Window(appearance="light")
    win.show()
    with tempfile.TemporaryDirectory(prefix="chronon-selftest-") as tmp:
        try:
            seen = walk(win, folder, Path(tmp) / "export")
        except SelftestError as e:
            print(json.dumps({"error": str(e)}))
            seen = None
        finally:
            win.close()
    ok = bool(seen) and not seen["unreliable"] and not seen["failed"]
    if seen:
        print(json.dumps(seen))
    print("selftest", "ok" if ok else "FAILED")
    app.processEvents()
    return 0 if ok else 1


def run(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        return selftest(argv[1])
    menus.name_the_app("Chronon")  # before the app starts: macOS reads it once
    app = QApplication(sys.argv)
    app.setApplicationName("Chronon")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.png"))))  # Windows, Linux
    german = QTranslator(app)  # Qt's own words (menu roles, dialogs) in German
    if german.load("qtbase_de", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(german)
    win = Window()
    if argv:
        win.files.add(argv)
    win.show()
    return app.exec()
