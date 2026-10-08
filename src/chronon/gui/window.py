"""The window: sidebar with the six steps, header, page, footer (docs/design/ui/README.md)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from chronon.gui import fmt, settings, texts, theme
from chronon.gui.audition import Overviews, Player
from chronon.gui.devices_page import DevicesPage
from chronon.gui.export_page import ExportPage
from chronon.gui.files_page import FilesPage
from chronon.gui.jobs import Job
from chronon.gui.listen_page import ListenPage
from chronon.gui.project import Project
from chronon.gui.result_page import ResultPage
from chronon.gui.settings_page import SettingsPage
from chronon.gui.sync_page import SyncPage
from chronon.gui.widgets import Icon

STEPS = ["Dateien", "Geräte & Referenz", "Sync", "Ergebnis", "Hören", "Export"]
NEXT = {k: f"Weiter: {STEPS[k + 1]}" for k in range(len(STEPS) - 1)}
SETTINGS = len(STEPS)  # page index of the settings


class Window(QMainWindow):
    def __init__(self, appearance: str | None = None):
        super().__init__()
        self.setWindowTitle("Chronon")
        self.setMinimumSize(1024, 700)
        self.resize(1200, 780)
        self.appearance = appearance or settings.appearance()
        self.before_settings = 0
        self.tokens = dict(theme.TOKENS[theme.resolve(self.appearance)])
        self.apply_theme()
        self.project = Project()
        self.step = 0

        # sidebar
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(216)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 20, 12, 20)
        side.setSpacing(4)
        name = QLabel("Chronon")
        name.setObjectName("appname")
        side.addWidget(name)
        self.step_buttons = QButtonGroup(self)
        for k, label in enumerate(STEPS):
            side.addWidget(self._side_button(str(k + 1), label, k))
        side.addStretch()
        side.addWidget(self._side_button("gear", "Einstellungen", SETTINGS))

        # header
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(60)
        self.step_label = QLabel()
        self.step_label.setObjectName("steplabel")
        self.title = QLabel()
        self.title.setObjectName("title")
        self.header_right = QLabel()
        self.header_right.setObjectName("headerright")
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.step_label)
        titles.addWidget(self.title)
        head = QHBoxLayout(header)
        head.setContentsMargins(28, 8, 28, 8)
        head.addLayout(titles)
        head.addStretch()
        head.addWidget(self.header_right)

        # pages
        self.files = FilesPage(self.project, self.tokens)
        self.files.changed.connect(self.update_chrome)
        self.player = Player(self)
        self.overviews = Overviews(self)
        self.devices = DevicesPage(self.project, self.tokens, self.overviews, self.player)
        self.devices.changed.connect(self.update_chrome)
        self.sync = SyncPage(self.project)
        self.sync.changed.connect(self.update_chrome)
        self.sync.finished.connect(lambda: self.show_step(3))
        self.sync.finished.connect(self._prefetch_waves)
        self.result = ResultPage(self.project, self.tokens, self.overviews)
        self.result.changed.connect(self.update_chrome)
        self.result.other_reference.connect(lambda: self.show_step(1))
        self.listen = ListenPage(self.project, self.tokens, self.player, self.overviews)
        self.listen.changed.connect(self.update_chrome)
        self.export = ExportPage(self.project, self.tokens)
        self.export.changed.connect(self.update_chrome)
        self.pages = QStackedWidget()
        for page in (self.files, self.devices, self.sync, self.result, self.listen, self.export):
            self.pages.addWidget(page)
        self.settings_page = SettingsPage(self.project)
        self.settings_page.appearance_changed.connect(self.set_appearance)
        self.pages.addWidget(self.settings_page)

        # footer
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(56)
        self.back = QPushButton()
        self.main = QPushButton()
        self.main.setProperty("role", "primary")
        self.main.setMinimumWidth(140)
        self.back.clicked.connect(self.go_back)
        self.main.clicked.connect(self.go_on)
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(28, 0, 28, 0)
        foot.addWidget(self.back)
        foot.addStretch()
        foot.addWidget(self.main)

        content = QWidget()
        content.setObjectName("content")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(header)
        column.addWidget(self.pages, 1)
        column.addWidget(footer)
        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(sidebar)
        row.addWidget(content, 1)
        self.setCentralWidget(central)
        self.show_step(0)

    def _side_button(self, mark: str, text: str, page: int) -> QPushButton:
        """A sidebar row: number box (accent when current) and the step's name."""
        b = QPushButton()
        box = QLabel(mark) if mark != "gear" else Icon("gear", 20, margin=3)
        box.setObjectName("stepnumber")
        box.setFixedSize(20, 20)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(text)
        name.setObjectName("stepname")
        for label in (box, name):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row = QHBoxLayout(b)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(12)
        row.addWidget(box)
        row.addWidget(name, 1)
        b.setProperty("role", "step")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: self.show_step(page))
        self.step_buttons.addButton(b, page)
        return b

    def closeEvent(self, event):  # noqa: N802 (Qt API)
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Stop playback and every child process before the window goes."""
        self.player.stop()
        self.overviews.stop()
        self.files.cancel()
        for job in self.findChildren(Job):  # sync / export, maybe still finishing
            job.cancel()
            job.wait(5000)

    def _prefetch_waves(self) -> None:
        """While the result is read: the waveforms listening needs, in the order it shows
        the files (reference tracks first), so they are there when it opens."""
        result, layout = self.project.result, self.project.layout
        if not result or layout is None:
            return
        rows = [r for r in result["files"] if not r.get("is_reference")]
        order = {"unsure": 0, "wanders": 1, "ok": 2}
        rows.sort(key=lambda r: order[texts.verdict(r)])
        self.overviews.prefetch(list(layout.tracks) + [Path(r["file"]) for r in rows])

    def set_appearance(self, appearance: str) -> None:
        """Change the theme live: every widget holds this one token dict."""
        self.appearance = appearance
        self.tokens.clear()
        self.tokens.update(theme.TOKENS[theme.resolve(appearance)])
        self.apply_theme()
        for page in (self.result, self.devices):
            if hasattr(page, "rebuild") and page.isVisible():
                page.rebuild()
        self.update()

    def apply_theme(self) -> None:
        app = QApplication.instance()
        style = app.style()
        if not isinstance(style, theme.Style) or style.tokens is not self.tokens:
            app.setStyle(theme.Style(self.tokens))
        app.setPalette(theme.palette(self.tokens))
        app.setStyleSheet(theme.stylesheet(self.tokens))

    # --- navigation -------------------------------------------------------------------
    def show_step(self, page: int) -> None:
        self.player.stop()
        if page == SETTINGS and self.step != SETTINGS:
            self.before_settings = self.step
        self.step = page
        self.pages.setCurrentIndex(page)
        self.step_buttons.button(page).setChecked(True)
        for b in self.step_buttons.buttons():
            current = "true" if b is self.step_buttons.button(page) else "false"
            for label in b.findChildren(QLabel):
                label.setProperty("current", current)
                label.style().unpolish(label)
                label.style().polish(label)
        enter = getattr(self.pages.widget(page), "enter", None)
        if enter is not None:
            enter()
        self.update_chrome()

    def go_back(self) -> None:
        if self.step == 0 and self.files.reading:
            self.files.cancel()
        elif self.step == 2 and self.sync.running:
            self.sync.cancel()
        elif self.step == 5 and self.export.running:
            self.export.cancel()
        elif self.step == 5 and self.export.state in ("done", "rejected"):
            self.export.state = "form"
            self.export.show_state()
        elif self.step == SETTINGS:
            self.show_step(self.before_settings)
        elif self.step > 0:
            self.show_step(self.step - 1)

    def go_on(self) -> None:
        if self.step == SETTINGS:
            self.show_step(self.before_settings)
        elif self.step == 1:  # "Sync starten": measure again only when the devices changed
            self.show_step(2)
            if not self.project.synced_now:
                self.sync.start()
        elif self.step == 2 and self.sync.state in ("cancelled", "failed", "idle"):
            self.sync.start()
        elif self.step == 5 and self.export.state == "done":
            self.new_project()
        elif self.step == 5:
            self.export.start()
        elif self.step + 1 < len(STEPS):
            self.show_step(self.step + 1)

    def new_project(self) -> None:
        """'Neues Projekt': back to an empty step 1 (the session folder stays)."""
        self.player.stop()
        self.project.reset()
        self.sync.state = "idle"
        self.export.state, self.export.loaded_for, self.export.name_edited = "form", "", False
        self.export.folder.clear()
        self.files.show_state()
        self.show_step(0)

    def footer(self) -> tuple[str | None, str, bool]:
        """(left button or None, main button, main enabled) for the current page."""
        step = self.step
        if step == SETTINGS:
            return None, "Fertig", True
        if step == 0:
            return ("Abbrechen" if self.files.reading else None), NEXT[0], self.files.ready
        if step == 1:
            return "Zurück", "Sync starten", self.devices.ready
        if step == 2:
            if self.sync.running:
                return "Abbrechen", NEXT[2], False
            if self.sync.state in ("cancelled", "failed"):
                return "Zurück", "Sync neu starten", self.devices.ready
            if self.sync.state == "idle":
                return "Zurück", "Sync starten", self.devices.ready
            return "Zurück", NEXT[2], self.project.synced_now
        if step + 1 < len(STEPS):
            return "Zurück", NEXT[step], self.project.synced_now
        if self.export.running:
            return "Abbrechen", "Exportieren", False
        if self.export.state == "done":
            return "Zurück", "Neues Projekt", True
        return "Zurück", "Exportieren", self.project.synced_now

    def update_chrome(self) -> None:
        """Header texts and footer buttons for the current page and its state."""
        in_settings = self.step == SETTINGS
        self.step_label.setText("" if in_settings else f"SCHRITT {self.step + 1} VON {len(STEPS)}")
        self.title.setText("Einstellungen" if in_settings else STEPS[self.step])
        entries, layout = self.project.entries, self.project.layout
        right = fmt.files(len(entries)) if entries and not self.files.reading else ""
        if right and self.step >= 1 and layout is not None and self.project.layout_current:
            count = len(layout.devices)
            right += f" · {count} Gerät" + ("" if count == 1 else "e")
        self.header_right.setText(right)
        back, main, enabled = self.footer()
        self.back.setVisible(back is not None)
        self.back.setText(back or "")
        self.main.setText(main.replace("&", "&&"))  # a single & marks a shortcut
        self.main.setEnabled(enabled)
