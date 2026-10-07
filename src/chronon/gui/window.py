"""The window: sidebar with the six steps, header, page, footer (docs/design/ui/README.md)."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
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

from chronon.gui import fmt, theme
from chronon.gui.files_page import FilesPage
from chronon.gui.project import Project

STEPS = ["Dateien", "Geräte & Referenz", "Sync", "Ergebnis", "Hören", "Export"]
# "&&": a single & marks a keyboard shortcut on a button
NEXT = {k: f"Weiter: {STEPS[k + 1]}".replace("&", "&&") for k in range(len(STEPS) - 1)}
SETTINGS = len(STEPS)  # page index of the settings


class Placeholder(QWidget):
    """A step that is not built yet."""

    def __init__(self, text: str):
        super().__init__()
        label = QLabel(text)
        label.setObjectName("placeholder")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        QVBoxLayout(self).addWidget(label)


class Window(QMainWindow):
    def __init__(self, appearance: str | None = None):
        super().__init__()
        self.setWindowTitle("Chronon")
        self.setMinimumSize(1024, 700)
        self.resize(1200, 780)
        self.settings = QSettings("Chronon", "Chronon")
        self.appearance = appearance or str(self.settings.value("appearance", "system"))
        self.tokens = theme.TOKENS[theme.resolve(self.appearance)]
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
        side.addWidget(self._side_button("⚙", "Einstellungen", SETTINGS))

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
        self.pages = QStackedWidget()
        self.pages.addWidget(self.files)
        for label in STEPS[1:]:
            self.pages.addWidget(Placeholder(f"{label}: kommt in einem der nächsten Schritte."))
        self.pages.addWidget(Placeholder("Einstellungen: kommen in einem der nächsten Schritte."))

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
        box = QLabel(mark)
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

    def apply_theme(self) -> None:
        app = QApplication.instance()
        app.setStyle("Fusion")
        app.setPalette(theme.palette(self.tokens))
        app.setStyleSheet(theme.stylesheet(self.tokens))

    # --- navigation -------------------------------------------------------------------
    def show_step(self, page: int) -> None:
        self.step = page
        self.pages.setCurrentIndex(page)
        self.step_buttons.button(page).setChecked(True)
        for b in self.step_buttons.buttons():
            current = "true" if b is self.step_buttons.button(page) else "false"
            for label in b.findChildren(QLabel):
                label.setProperty("current", current)
                label.style().unpolish(label)
                label.style().polish(label)
        self.update_chrome()

    def go_back(self) -> None:
        if self.step == 0 and self.files.reading:
            self.files.cancel()
        elif self.step == SETTINGS or self.step > 0:
            self.show_step(0 if self.step == SETTINGS else self.step - 1)

    def go_on(self) -> None:
        if self.step == SETTINGS:
            self.show_step(0)
        elif self.step + 1 < len(STEPS):
            self.show_step(self.step + 1)

    def update_chrome(self) -> None:
        """Header texts and footer buttons for the current page and its state."""
        settings = self.step == SETTINGS
        self.step_label.setText("" if settings else f"SCHRITT {self.step + 1} VON {len(STEPS)}")
        self.title.setText("Einstellungen" if settings else STEPS[self.step])
        entries = self.project.entries
        self.header_right.setText(
            fmt.files(len(entries)) if entries and not self.files.reading else ""
        )
        reading = self.step == 0 and self.files.reading
        self.back.setText("Abbrechen" if reading else "Zurück")
        self.back.setVisible(reading or self.step > 0)
        if settings:
            self.main.setText("Fertig")
            self.main.setEnabled(True)
        elif self.step + 1 < len(STEPS):
            self.main.setText(NEXT[self.step])
            self.main.setEnabled(self.files.ready if self.step == 0 else False)
        else:
            self.main.setText("Exportieren")
            self.main.setEnabled(False)
