"""Step 6, Export: the form (Sync or Korrigiert), the run, the outcome (boards 11–14).

``chronon sync|correct --analysis <step 3's analysis>``: nothing is measured again, and a
changed source is refused. The output folder must not hold any original; the page checks
that before it starts (board 12), the core checks it again."""

from __future__ import annotations

import os
import sys
import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from chronon.gui import texts
from chronon.gui.jobs import Job
from chronon.gui.project import Project
from chronon.gui.widgets import (
    Segmented,
    StepList,
    badge,
    banner,
    button,
    card,
    label,
    rule,
    status_square,
)

FRAME_RATES = ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"]
SAMPLE_RATES = [(44_100, "44,1 kHz"), (48_000, "48 kHz"), (88_200, "88,2 kHz"), (96_000, "96 kHz")]
FORMATS = ["auto", "wav", "caf"]
TIMELINES = [("fcpxml", "Final Cut Pro / Logic"), ("none", "Keine (nur Audio)")]


def file_manager() -> str:
    if sys.platform == "darwin":
        return "Im Finder zeigen"
    if sys.platform == "win32":
        return "Im Explorer zeigen"
    return "Im Dateimanager zeigen"


def default_root() -> Path:
    movies = Path.home() / ("Movies" if sys.platform == "darwin" else "Videos")
    return (movies if movies.is_dir() else Path.home()) / "Chronon"


def holds_original(folder: Path, files: list[Path]) -> Path | None:
    """An original in ``folder`` (compared as folders on disk, as the core does)."""
    if not folder.exists():
        return None
    for f in files:
        try:
            if f.parent.exists() and os.path.samefile(f.parent, folder):
                return f
        except OSError:
            continue
    return None


def fps_text(rate: str) -> str:
    """'30000/1001' -> '29.97' (the dropdown's spelling)."""
    value = Fraction(rate)
    for text in FRAME_RATES:
        exact = Fraction({"23.976": 24, "29.97": 30, "59.94": 60}.get(text, 0) * 1000, 1001)
        if (exact and value == exact) or value == Fraction(text):
            return text
    return f"{float(value):g}"


class Choice(QFrame):
    """One of the two export kinds, as a card with a radio (board 11)."""

    def __init__(self, title: str, text: str):
        super().__init__()
        self.setObjectName("choicecard")
        self.radio = QRadioButton()
        box = QHBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label(f"<b>{title}</b>"))
        words.addWidget(label(text, "muted", wrap=True))
        box.addLayout(words, 1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):  # noqa: N802 (Qt API)
        self.radio.setChecked(True)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class ExportPage(QWidget):
    changed = Signal()
    new_project = Signal()

    def __init__(self, project: Project, tokens: dict[str, str]):
        super().__init__()
        self.project, self.tokens = project, tokens
        self.state = "form"  # form, running, done, failed
        self.job: Job | None = None
        self.error = ""
        self.outcome: dict | None = None
        self.name_edited = False
        self.loaded_for = ""
        self.runs = 0

        inner = QWidget()
        inner.setObjectName("content")
        self.column = QVBoxLayout(inner)
        self.column.setContentsMargins(28, 22, 28, 22)
        self.column.setSpacing(18)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.banner_box = QVBoxLayout()
        self.column.addLayout(self.banner_box)

        # the form
        self.form = QWidget()
        form = QVBoxLayout(self.form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(18)
        kinds = QHBoxLayout()
        self.sync_choice = Choice(
            "Sync", "Clips bleiben unverändert, nur die Position auf der Timeline wird korrigiert."
        )
        self.correct_choice = Choice(
            "Korrigiert",
            "Zusätzlich wird der Drift ausgeglichen. Neue Audiodateien werden geschrieben.",
        )
        for c in (self.sync_choice, self.correct_choice):
            kinds.addWidget(c)
            c.radio.toggled.connect(self._kind_changed)
        form.addLayout(kinds)

        common, common_box = card((18, 6, 18, 6))
        self.name = QLineEdit()
        self.name.textEdited.connect(self._name_edited)
        self.timeline = QComboBox()
        self.fps = QComboBox()
        self.fps.addItems([f"{r} fps" for r in FRAME_RATES])
        self.fps_hint = label("", "muted")
        self.folder = QLineEdit()
        self.folder.setObjectName("pathfield")
        self.folder.textEdited.connect(self._folder_edited)
        choose = button("Wählen …")
        choose.clicked.connect(self.choose_folder)
        rows = [
            ("Projektname", [self.name, label("Standard: Name des Ausgabeordners", "muted")]),
            ("Timeline für", [self.timeline]),
            ("Timeline-Bildrate", [self.fps, self.fps_hint]),
            ("Ausgabeordner", [self.folder, choose]),
        ]
        self._rows(common_box, rows)
        form.addWidget(common)

        self.corrected, corrected_box = card((18, 10, 18, 6))
        corrected_box.addWidget(label("KORRIGIERTE AUDIODATEIEN", "section"))
        self.format = Segmented(["Automatisch", "WAV", "CAF"])
        self.format.changed.connect(lambda _k: self._caf_hint())
        self.rate = QComboBox()
        for _hz, text in SAMPLE_RATES:
            self.rate.addItem(text)
        self.rate.setCurrentIndex(1)
        self.pad = QCheckBox("Jede Datei beginnt bei 0:00:00 des Projekts")
        self.pad.setChecked(True)
        self.pad.toggled.connect(lambda _on: self._caf_hint())
        self.pad_hint = label("", "warnhint")
        self.join = QCheckBox("Zusammengehörige Dateien eines Geräts zu einer Spur")
        self.join.setChecked(True)
        self._rows(corrected_box, [
            ("Audioformat", [self.format, label("WAV, ab 2 GiB CAF", "muted")]),
            ("Samplerate", [self.rate]),
            ("Auffüllen bis Projektstart", [self.pad, self.pad_hint]),
            ("Clips zusammenfügen", [self.join]),
        ])  # fmt: skip
        form.addWidget(self.corrected)
        self.column.addWidget(self.form)

        # running
        self.progress, progress_box = card((28, 22, 28, 22))
        head = QHBoxLayout()
        self.run_title = label("Export läuft …", "headline")
        self.percent = label("", "percent")
        head.addWidget(self.run_title)
        head.addStretch()
        head.addWidget(self.percent)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.detail = label("", "hint")
        progress_box.addLayout(head)
        progress_box.addWidget(self.bar)
        progress_box.addWidget(self.detail)
        self.steps = StepList()
        self.column.addWidget(self.progress)
        self.column.addWidget(self.steps)

        # done
        self.outcome_box = QVBoxLayout()
        self.column.addLayout(self.outcome_box)
        self.column.addStretch()

        self.correct_choice.radio.setChecked(True)
        self.show_state()

    @staticmethod
    def _rows(box: QVBoxLayout, rows: list[tuple[str, list[QWidget]]]) -> None:
        for k, (title, widgets) in enumerate(rows):
            if k:
                box.addWidget(rule())
            line = QHBoxLayout()
            line.setContentsMargins(0, 6, 0, 6)
            name = label(title)
            name.setFixedWidth(220)
            line.addWidget(name)
            for w in widgets:
                line.addWidget(w, 1 if isinstance(w, QLineEdit) else 0)
            line.addStretch()
            box.addLayout(line)

    # --- form ---------------------------------------------------------------------------
    def enter(self) -> None:
        """Defaults from the project, once per sync result."""
        if not self.project.synced_now:
            self.changed.emit()
            return
        key = self.project.synced + str(id(self.project.result))
        if key != self.loaded_for:
            self.loaded_for = key
            files = list(self.project.infos)
            base = files[0].parent.name if files else "Projekt"
            if not self.name_edited:
                self.name.setText(base)
            if not self.folder.text():
                self.folder.setText(str(default_root() / base))
            rate = self.project.result.get("frame_rate") or "25"
            self.fps.setCurrentText(f"{fps_text(rate)} fps")
            video = any(i.has_video for i in self.project.infos.values())
            self.fps_hint.setText("aus den Videos erkannt" if video else "ohne Video: 25 fps")
            if self.state != "running":
                self.state = "form"
        self.show_state()

    def _kind_changed(self) -> None:
        corrected = self.correct_choice.radio.isChecked()
        self.sync_choice.set_selected(not corrected)
        self.correct_choice.set_selected(corrected)
        self.corrected.setVisible(corrected)
        current = self.timeline.currentData()
        self.timeline.clear()
        for value, text in TIMELINES if corrected else TIMELINES[:1]:  # sync: only a timeline
            self.timeline.addItem(text, value)
        index = self.timeline.findData(current)
        self.timeline.setCurrentIndex(max(index, 0))
        self._caf_hint()

    def _caf_hint(self) -> None:
        can_be_caf = FORMATS[self.format.current] in ("auto", "caf")
        self.pad_hint.setText(
            "CAF hat keinen Zeitstempel: Position nur über die Timeline-Datei"
            if not self.pad.isChecked() and can_be_caf else ""
        )  # fmt: skip

    def _name_edited(self) -> None:
        self.name_edited = True

    def _folder_edited(self, text: str) -> None:
        if not self.name_edited and text.strip():
            self.name.setText(Path(text.strip()).name)

    def choose_folder(self) -> None:
        start = self.folder.text() or str(default_root())
        folder = QFileDialog.getExistingDirectory(self, "Ausgabeordner wählen", start)
        if folder:
            self.folder.setText(folder)
            self._folder_edited(folder)

    # --- running ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.state == "running"

    def start(self, overwrite: bool = False) -> None:
        if self.running or not self.project.synced_now:
            return
        folder = Path(self.folder.text().strip()).expanduser()
        if not self.folder.text().strip():
            self._reject("Kein Ausgabeordner gewählt.")
            return
        original = holds_original(folder, list(self.project.infos))
        if original is not None:
            self._reject(
                "Der Ausgabeordner enthält die Originaldateien. Wähle einen anderen Ordner."
            )
            return
        corrected = self.correct_choice.radio.isChecked()
        args = [
            "correct" if corrected else "sync",
            "--analysis", str(self.project.analysis_path),
            "-o", str(folder),
            "--name", self.name.text().strip() or folder.name,
            "--fps", self.fps.currentText().split()[0],
        ]  # fmt: skip
        if corrected:
            args += ["--timeline", self.timeline.currentData() or "fcpxml"]
            args += ["--format", FORMATS[self.format.current]]
            args += ["--rate", str(SAMPLE_RATES[self.rate.currentIndex()][0])]
            if not self.pad.isChecked():
                args.append("--no-pad")
            if self.join.isChecked():
                args.append("--join")
            if overwrite:
                args.append("--overwrite")
        self.runs += 1
        log = self.project.folder() / "logs" / f"export-{self.runs}.log"
        self.job = Job(args, log=str(log), parent=self)
        self.job.plan.connect(self._plan)
        self.job.progress.connect(self._progress)
        self.job.result.connect(self._result)
        self.job.failed.connect(self._failed)
        self.state, self.error, self.outcome = "running", "", None
        self.started = time.monotonic()
        self.task = ""
        self.bar.setValue(0)
        self.percent.setText("0 %")
        self.detail.setText("")
        self.steps.set_plan([])
        self.show_state()
        self.job.start()

    def cancel(self) -> None:
        if self.job is not None and self.running:
            self.job.cancel()

    def _reject(self, text: str, code: str = "") -> None:
        self.state, self.error, self.error_code = "rejected", text, code
        self.show_state()

    def _plan(self, steps: list[dict]) -> None:
        self.steps.set_plan(steps)
        self.plan = steps

    def _progress(self, e: dict) -> None:
        task = e.get("task") or e.get("step", "")
        if task != self.task:
            if self.task:
                self.steps.set_state(self.task, "done")
            self.task = task
            self.steps.set_state(task, "running")
        self.bar.setValue(round(1000 * e.get("done", 0.0)))
        self.percent.setText(f"{round(100 * e.get('done', 0.0))} %")
        name = {"writing": "Dateien schreiben", "verifying": "Ergebnis prüfen"}.get(task, task)
        left = e.get("left_s")
        self.detail.setText(name + (f" · noch ca. {max(round(left / 60), 1)} Min." if left else ""))

    def _result(self, e: dict) -> None:
        self.job = None
        self.state, self.outcome = "done", e
        self.show_state()

    def _failed(self, e: dict) -> None:
        self.job = None
        if e.get("code") == "cancelled":
            self.state = "form"
            self.show_state()
            self.banner_box.addWidget(banner(
                "warn", "Export abgebrochen.",
                "Bereits geschriebene Dateien bleiben im Ausgabeordner."))  # fmt: skip
            return
        self._reject(texts.error(e), e.get("code") or "")

    # --- states -------------------------------------------------------------------------
    def show_state(self) -> None:
        while self.banner_box.count():
            item = self.banner_box.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        _clear(self.outcome_box)
        synced = self.project.synced_now
        if not synced:
            self.banner_box.addWidget(
                label("Noch kein Ergebnis: zuerst in Schritt 3 synchronisieren.", "placeholder")
            )
        form = synced and self.state in ("form", "rejected")
        self.form.setVisible(form)
        self.corrected.setVisible(form and self.correct_choice.radio.isChecked())
        self.progress.setVisible(synced and self.state == "running")
        self.steps.setVisible(synced and self.state == "running" and bool(self.steps.rows))
        if synced and self.state == "rejected":
            self.banner_box.addWidget(banner("error", "Export abgelehnt.", self.error))
            if getattr(self, "error_code", "") == "output_exists":
                replace = button("Vorhandene Dateien ersetzen")
                replace.clicked.connect(lambda: self.start(overwrite=True))
                self.banner_box.addWidget(replace, 0, Qt.AlignmentFlag.AlignLeft)
        if synced and self.state == "done":
            self._outcome()
        self.changed.emit()

    def _outcome(self) -> None:
        e = self.outcome or {}
        rows = e.get("files", [])
        corrected = e.get("command") == "correct"
        failed = [r for r in rows if r.get("verified") is False]
        if failed:
            n = len(failed)
            who = "1 Datei hat" if n == 1 else f"{n} Dateien haben"
            title = f"{who} die Prüfung nicht bestanden."
            self.outcome_box.addWidget(
                banner(
                    "error",
                    title,
                    "Ausgabe ist nicht synchron zur Referenz. "
                    "Prüfe die Datei, bevor du sie benutzt.",
                )
            )
        else:
            head = QHBoxLayout()
            head.addWidget(status_square(True))
            head.addSpacing(12)
            words = QVBoxLayout()
            words.setSpacing(2)
            words.addWidget(label("Export fertig", "headline"))
            timeline = e.get("timeline")
            where = f"Timeline: {Path(timeline).name}" if timeline else "Nur Audiodateien"
            words.addWidget(label(f"{where} · {Path(self.folder.text()).expanduser()}", "hint"))
            head.addLayout(words)
            head.addStretch()
            self.outcome_box.addLayout(head)

        frame = QFrame()
        frame.setObjectName("card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 10, 16, 10)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for col, text in enumerate(["DATEI", "ERGEBNIS", "HINWEIS"]):
            grid.addWidget(label(text, "colhead"), 0, col)
        grid.setColumnStretch(2, 1)
        for k, r in enumerate(rows, 1):
            if corrected:
                name = Path(r["path"]).name
                state = r.get("verified")
                tag = (badge("ref", "Referenz") if r.get("is_reference")
                       else badge("unsure") if not r.get("reliable", True)
                       else badge("ok", "geprüft") if state
                       else badge("bad", "Prüfung fehlgeschlagen") if state is False
                       else badge("unsure", "nicht geprüft"))  # fmt: skip
            else:
                name = Path(r["source"]).name
                tag = badge("ok", "platziert") if r.get("reliable") else badge("unsure")
            skip = ("reference_clock", "matched_track", "verification_failed",
                    "verification_failed_via")  # fmt: skip
            grid.addWidget(label(name, "mono"), k, 0)
            grid.addWidget(tag, k, 1, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(label(texts.notes(r.get("notes", []), skip), "muted", wrap=True), k, 2)
        self.outcome_box.addWidget(frame)
        show = button(file_manager())
        show.clicked.connect(self.show_folder)
        self.outcome_box.addWidget(show, 0, Qt.AlignmentFlag.AlignLeft)

    def show_folder(self) -> None:
        folder = Path(self.folder.text()).expanduser()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().setParent(None)
        elif item.layout() is not None:
            _clear(item.layout())
