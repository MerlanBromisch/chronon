"""Start the window; ``--selftest FOLDER`` checks a (frozen) build without a screen."""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from chronon.gui.jobs import Job
from chronon.gui.window import Window

SELFTEST_TIMEOUT_MS = 300_000


def selftest(folder: str) -> int:
    """Read every file of ``folder`` in the window, then analyse them in a worker (first file
    as reference); exit 0 when all were readable and every result is reliable."""
    app = QApplication.instance() or QApplication(sys.argv)
    win = Window(appearance="light")
    outcome = {"code": 1}
    jobs: list[Job] = []

    def analyse() -> None:
        if jobs or win.files.reading or not win.project.entries:
            return
        entries = win.project.entries
        unreadable = [str(e.path) for e in entries if e.info is None]
        if unreadable or len(entries) < 2:
            print(json.dumps({"unreadable": unreadable, "files": len(entries)}))
            app.quit()
            return
        job = Job(["analyze", *(str(e.path) for e in entries)], parent=win)
        jobs.append(job)
        job.result.connect(done)
        job.failed.connect(lambda e: (print(json.dumps(e)), app.quit()))
        job.start()

    def done(result: dict) -> None:
        rows = result["files"]
        bad = [r["file"] for r in rows if not r["reliable"]]
        print(json.dumps({"files": len(rows) + 1, "unreliable": bad}))
        outcome["code"] = 0 if not bad else 1
        app.quit()

    win.files.changed.connect(lambda: QTimer.singleShot(0, analyse))
    win.files.add([folder])
    QTimer.singleShot(SELFTEST_TIMEOUT_MS, app.quit)
    app.exec()
    for job in jobs:
        job.wait()
    print("selftest", "ok" if outcome["code"] == 0 else "FAILED")
    return outcome["code"]


def run(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        return selftest(argv[1])
    app = QApplication(sys.argv)
    app.setApplicationName("Chronon")
    win = Window()
    if argv:
        win.files.add(argv)
    win.show()
    return app.exec()
