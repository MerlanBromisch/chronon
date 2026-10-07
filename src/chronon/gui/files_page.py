"""Step 1, Dateien: drop or choose files and folders, read them, list them (boards 01–04)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from chronon.gui import fmt, theme
from chronon.gui.project import Entry, Project, Reader, media_files

COLUMNS = ["DATEI", "DAUER", "SAMPLERATE", "KANÄLE", "STARTZEIT", ""]


class FileModel(QAbstractTableModel):
    def __init__(self, project: Project, tokens: dict[str, str]):
        super().__init__()
        self.project = project
        self.tokens = tokens

    def rowCount(self, parent=None):  # noqa: N802 (Qt API)
        return 0 if parent is not None and parent.isValid() else len(self.project.entries)

    def columnCount(self, parent=None):  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        e = self.project.entries[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.text(e, col)
        if role == Qt.ItemDataRole.FontRole and col in (0, 1, 4):
            return theme.mono_font(12)
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(e.path) if col == 0 else e.error if col == 5 else None
        return None

    @staticmethod
    def text(e: Entry, col: int) -> str:
        if col == 0:
            return e.path.name
        if col == 5:
            return "nicht lesbar" if e.error else ""
        i = e.info
        if i is None:
            return ""
        return [fmt.duration(i.duration_s), fmt.rate(i.sample_rate), str(i.channels), fmt.start(i)][
            col - 1
        ]

    def refresh(self) -> None:
        self.beginResetModel()
        self.endResetModel()


class BadgeDelegate(QStyledItemDelegate):
    """A status badge: status colour, ~12 % fill, 1 px border, 22 high (README, Components)."""

    def __init__(self, tokens: dict[str, str], colour: str = "red"):
        super().__init__()
        self.text, self.border = QColor(tokens[colour]), QColor(tokens[f"{colour}_border"])

    def paint(self, painter, option, index):
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        text, cell.text = cell.text, ""
        style = cell.widget.style() if cell.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, cell, painter, cell.widget)
        if not text:
            return
        label = f"!  {text}"
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = option.fontMetrics.horizontalAdvance(label) + 18
        rect = QRectF(option.rect.left() + 6, option.rect.center().y() - 11, width, 22)
        fill = QColor(self.text)
        fill.setAlphaF(0.12)
        painter.setPen(QPen(self.border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.setPen(self.text)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()


class DropZone(QFrame):
    """The empty page: a dashed area to drop on, two buttons."""

    def __init__(self, choose_files, choose_folder):
        super().__init__()
        self.setObjectName("dropzone")
        icon = QLabel("⇪")
        icon.setObjectName("dropicon")
        icon.setFixedSize(64, 64)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Aufnahmen hierher ziehen")
        title.setObjectName("headline")
        hint = QLabel(
            "Audio- und Videodateien oder ganze Ordner von allen Geräten desselben\n"
            "Ereignisses. Chronon gruppiert sie danach automatisch zu Geräten."
        )
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        files = QPushButton("Dateien wählen …")
        files.setProperty("role", "primary")
        folder = QPushButton("Ordner wählen …")
        files.clicked.connect(choose_files)
        folder.clicked.connect(choose_folder)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(files)
        buttons.addWidget(folder)
        buttons.addStretch()
        box = QVBoxLayout(self)
        box.addStretch()
        box.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addSpacing(14)
        box.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addSpacing(14)
        box.addLayout(buttons)
        box.addStretch()


class ReadingCard(QFrame):
    """'Dateien werden gelesen …  23 / 56' with a bar and the current file (board 02)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        title = QLabel("Dateien werden gelesen …")
        title.setObjectName("headline")
        self.counter = QLabel("")
        self.counter.setObjectName("counter")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.current = QLabel("")
        self.current.setObjectName("current")
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.counter)
        box = QVBoxLayout(self)
        box.setContentsMargins(28, 22, 28, 22)
        box.addLayout(top)
        box.addWidget(self.bar)
        box.addWidget(self.current)

    def show_count(self, done: int, total: int, name: str) -> None:
        self.counter.setText(f"{done} / {total}")
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(done)
        self.current.setText(name)


class UnreadableDialog(QDialog):
    """'Datei nicht lesbar' with ffmpeg's message (board 04)."""

    REMOVE, SKIP = 2, 3

    def __init__(self, entry: Entry, parent: QWidget):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle("Datei nicht lesbar")
        self.setModal(True)
        self.setFixedWidth(520)
        icon = QLabel("!")
        icon.setObjectName("dialogicon")
        icon.setFixedSize(34, 34)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Datei nicht lesbar")
        title.setObjectName("dialogtitle")
        head = QHBoxLayout()
        head.addWidget(icon)
        head.addSpacing(8)
        head.addWidget(title)
        head.addStretch()
        text = QLabel(
            f"Chronon kann <span style='font-family: monospace'>{entry.path.name}</span> nicht "
            "öffnen. Die Datei ist vermutlich beschädigt oder unvollständig kopiert."
        )
        text.setWordWrap(True)
        self.detail = QLabel(f"ffmpeg: {self.message(entry)}")
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy = QPushButton("Details kopieren")
        remove = QPushButton("Datei entfernen")
        skip = QPushButton("Ohne diese Datei weiter")
        skip.setProperty("role", "primary")
        skip.setDefault(True)
        copy.clicked.connect(self.copy)
        remove.clicked.connect(lambda: self.done(self.REMOVE))
        skip.clicked.connect(lambda: self.done(self.SKIP))
        buttons = QHBoxLayout()
        buttons.addWidget(copy)
        buttons.addStretch()
        buttons.addWidget(remove)
        buttons.addWidget(skip)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 22)
        box.setSpacing(14)
        box.addLayout(head)
        box.addWidget(text)
        box.addWidget(self.detail)
        box.addLayout(buttons)

    @staticmethod
    def message(entry: Entry) -> str:
        """ffprobe's message without the path it starts with."""
        error = entry.error or ""
        for prefix in (f"{entry.path}: ", f"{entry.path.name}: "):
            if error.startswith(prefix):
                return error[len(prefix) :]
        return error

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(f"{self.entry.path}\n{self.detail.text()}")


class FilesPage(QWidget):
    """Empty → reading → list. ``changed`` tells the window to update header and footer."""

    changed = Signal()

    def __init__(self, project: Project, tokens: dict[str, str]):
        super().__init__()
        self.project = project
        self.reader: Reader | None = None
        self.total = 0
        self.read_count = 0
        self.unreadable: list[Entry] = []
        self.dialog: UnreadableDialog | None = None
        self.setAcceptDrops(True)

        self.model = FileModel(project, tokens)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.badges = BadgeDelegate(tokens)
        self.table.setItemDelegateForColumn(5, self.badges)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in zip(range(1, 6), (112, 112, 82, 130, 130), strict=True):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, width)

        self.drop = DropZone(self.choose_files, self.choose_folder)
        self.card = ReadingCard()
        self.count = QLabel("")
        self.count.setObjectName("headline")
        hint = QLabel(
            "Im nächsten Schritt ordnet Chronon sie Geräten zu und du wählst die Referenz."
        )
        hint.setObjectName("hint")
        more_files = QPushButton("Dateien hinzufügen …")
        more_folder = QPushButton("Ordner hinzufügen …")
        more_files.clicked.connect(self.choose_files)
        more_folder.clicked.connect(self.choose_folder)
        title = QVBoxLayout()
        title.setSpacing(2)
        title.addWidget(self.count)
        title.addWidget(hint)
        self.top = QWidget()
        top = QHBoxLayout(self.top)
        top.setContentsMargins(0, 0, 0, 0)
        top.addLayout(title)
        top.addStretch()
        top.addWidget(more_files)
        top.addWidget(more_folder)

        listing = QWidget()
        box = QVBoxLayout(listing)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(18)
        box.addWidget(self.card)
        box.addWidget(self.top)
        box.addWidget(self.table, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.drop)
        self.stack.addWidget(listing)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.addWidget(self.stack)
        self.show_state()

    # --- state ------------------------------------------------------------------------
    @property
    def reading(self) -> bool:
        return self.reader is not None

    @property
    def ready(self) -> bool:
        """Enough readable files for the next step: a reference and one more."""
        return not self.reading and len(self.project.readable) >= 2

    def show_state(self) -> None:
        empty = not self.project.entries
        self.stack.setCurrentIndex(0 if empty else 1)
        self.card.setVisible(self.reading)
        self.top.setVisible(not self.reading)
        self.count.setText(fmt.files(len(self.project.entries)))
        self.model.refresh()
        self.changed.emit()

    # --- adding -----------------------------------------------------------------------
    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Dateien wählen")
        if paths:
            self.add(paths)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Ordner wählen")
        if folder:
            self.add([folder])

    def dragEnterEvent(self, event):  # noqa: N802 (Qt API)
        if event.mimeData().hasUrls() and not self.reading:
            event.acceptProposedAction()
            self.drop.setProperty("hover", "true")
            self.drop.style().polish(self.drop)

    def dragLeaveEvent(self, event):  # noqa: N802
        self.drop.setProperty("hover", "false")
        self.drop.style().polish(self.drop)

    def dropEvent(self, event):  # noqa: N802
        self.dragLeaveEvent(event)
        self.add([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def add(self, paths: list[str | Path]) -> None:
        if self.reading:
            return
        new = self.project.add(media_files(paths))
        if not new:
            self.show_state()
            return
        self.total, self.read_count = len(new), 0
        self.reader = Reader(new, self)
        self.reader.read.connect(self._read)
        self.reader.finished.connect(self._finished)
        self.card.show_count(0, self.total, "")
        self.show_state()
        self.reader.start()

    def cancel(self) -> None:
        """Stop reading: files not read yet leave the list again."""
        if self.reader is not None:
            self.reader.cancel()

    def _read(self, entry: Entry) -> None:
        self.read_count += 1
        self.card.show_count(self.read_count, self.total, entry.path.name)
        if entry.error:
            self.unreadable.append(entry)
        self.model.refresh()

    def _finished(self) -> None:
        if self.reader is not None and self.reader.stop.is_set():
            self.project.entries = [e for e in self.project.entries if e.info or e.error]
        self.reader = None
        self.show_state()
        self._ask_next()

    # --- unreadable files ---------------------------------------------------------------
    def _ask_next(self) -> None:
        """One dialog per unreadable file, one after the other."""
        if self.dialog is not None or not self.unreadable:
            return
        entry = self.unreadable.pop(0)
        self.dialog = UnreadableDialog(entry, self)
        self.dialog.finished.connect(lambda code: self._answered(entry, code))
        self.dialog.open()

    def _answered(self, entry: Entry, code: int) -> None:
        if code == UnreadableDialog.REMOVE:
            self.project.remove(entry.path)
        else:
            entry.skipped = True
        self.dialog = None
        self.show_state()
        self._ask_next()
