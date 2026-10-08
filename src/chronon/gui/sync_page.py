"""Step 3, Sync: analyse in a child process, with the plan's step list (boards 06, 07).

``chronon analyze --devices <layout> --save <analysis> --json --log <protocol>``: the layout of
step 2 goes in, the saved analysis comes out (steps 4–6 read it; the export does not measure
again)."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from chronon.gui import texts
from chronon.gui.jobs import Job
from chronon.gui.project import Project
from chronon.gui.widgets import StepList, banner, card, label, percent


def _clock(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _left(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return " · noch wenige Sekunden"
    return f" · noch ca. {round(seconds / 60)} Min."


class SyncPage(QWidget):
    changed = Signal()
    finished = Signal()  # a result is there

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.job: Job | None = None
        self.state = "idle"  # idle, running, cancelled, failed, done
        self.percent = 0
        self.steps: list[dict] = []
        self.task = ""
        self.task_started = 0.0
        self.runs = 0

        top, box = card((28, 22, 28, 22))
        head = QHBoxLayout()
        self.title = label("", "headline")
        self.number = label("", "percent")
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(self.number)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.detail = label("", "hint")
        box.addLayout(head)
        box.addWidget(self.bar)
        box.addWidget(self.detail)
        self.banner_box = QVBoxLayout()
        self.list = StepList()
        column = QVBoxLayout(self)
        column.setContentsMargins(28, 22, 28, 22)
        column.setSpacing(18)
        column.addWidget(top)
        column.addLayout(self.banner_box)
        column.addWidget(self.list)
        column.addStretch()
        self.show_state()

    @property
    def running(self) -> bool:
        return self.state == "running"

    def start(self) -> None:
        """Analyse the current layout (step 2's "Sync starten")."""
        if self.running or self.project.layout is None:
            return
        folder = self.project.folder()
        self.runs += 1
        layout_path = folder / "devices.json"
        self.project.layout.save(layout_path)
        analysis = folder / "analysis.json"
        log = folder / "logs" / f"sync-{self.runs}.log"
        self.project.result = None
        self.project.synced = self.project.layout_json
        self.project.analysis_path = analysis
        args = ["analyze", "--devices", str(layout_path), "--save", str(analysis)]
        self.job = Job(args, log=str(log), parent=self)
        self.job.plan.connect(self._plan)
        self.job.progress.connect(self._progress)
        self.job.result.connect(self._result)
        self.job.failed.connect(self._failed)
        self.state, self.percent, self.task = "running", 0, ""
        self.error = ""
        self.list.set_plan([])
        self.show_state()
        self.job.start()

    def cancel(self) -> None:
        if self.job is not None and self.running:
            self.job.cancel()

    def _plan(self, steps: list[dict]) -> None:
        self.steps = steps
        self.list.set_plan(steps)
        self.list.setVisible(True)

    def _progress(self, e: dict) -> None:
        task = e.get("task") or e.get("step", "")
        now = time.monotonic()
        if task != self.task:
            ids = [s["id"] for s in self.steps]
            if self.task in ids:
                self.list.set_state(self.task, "done", _clock(now - self.task_started))
            # steps skipped over (nothing to do) count as done
            if task in ids and self.task in ids:
                for skipped in ids[ids.index(self.task) + 1 : ids.index(task)]:
                    self.list.set_state(skipped, "done", "0:00")
            self.task, self.task_started = task, now
            self.list.set_state(task, "running")
        self.percent = percent(e.get("done", 0.0))
        name = next((texts.step(s) for s in self.steps if s["id"] == task), "")
        self.bar.setValue(10 * self.percent)
        self.number.setText(f"{self.percent} %")
        self.detail.setText(name + _left(e.get("left_s")))

    def _result(self, e: dict) -> None:
        for s in self.steps:
            if s["id"] == self.task:
                self.list.set_state(s["id"], "done", _clock(time.monotonic() - self.task_started))
            elif self.list.rows.get(s["id"]) and not self.list.rows[s["id"]][2].text():
                self.list.set_state(s["id"], "done")
        self.project.result = e
        self.state = "done"
        self.job = None
        self.show_state()
        self.finished.emit()

    def _failed(self, e: dict) -> None:
        self.job = None
        if e.get("code") == "cancelled":
            self.state = "cancelled"
            self.list.set_state(self.task, "cancelled")
        else:
            self.state, self.error = "failed", texts.error(e)
        self.project.result = None
        self.show_state()

    def show_state(self) -> None:
        while self.banner_box.count():
            item = self.banner_box.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        self.bar.setProperty("stopped", "true" if self.state in ("cancelled", "failed") else "")
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)
        if self.state == "running":
            self.title.setText("Sync läuft …")
            self.number.setText(f"{self.percent} %")
        elif self.state == "cancelled":
            self.title.setText("Sync angehalten")
            self.detail.setText(f"Bei {self.percent} % abgebrochen.")
            self.banner_box.addWidget(banner(
                "warn", "Sync abgebrochen.",
                "Es wurde nichts verändert. Deine Dateien bleiben unberührt."))  # fmt: skip
        elif self.state == "failed":
            self.title.setText("Sync fehlgeschlagen")
            self.detail.setText("")
            self.banner_box.addWidget(banner("error", "Fehler:", self.error))
        elif self.state == "done":
            self.title.setText("Sync fertig")
            self.number.setText("100 %")
            self.bar.setValue(1000)
            self.detail.setText("Alle Schritte erledigt.")
        else:
            self.title.setText("Sync noch nicht gestartet")
            self.number.setText("")
            self.bar.setValue(0)
            self.detail.setText("In Schritt 2 „Sync starten“ wählen.")
        self.list.setVisible(bool(self.list.rows))
        self.number.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.changed.emit()
