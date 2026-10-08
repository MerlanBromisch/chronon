"""Settings (board 15): language, appearance, cache folder, protocol, about."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from chronon import __version__
from chronon.gui import settings
from chronon.gui.project import Project
from chronon.gui.widgets import Segmented, button, card, label, rule

APPEARANCE_ORDER = list(settings.APPEARANCES)


class SettingsPage(QWidget):
    appearance_changed = Signal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        main, box = card((18, 8, 18, 8))
        self.language = QComboBox()
        self.language.addItem("Deutsch")
        self.language.setFixedWidth(200)
        self.appearance = Segmented(list(settings.APPEARANCES.values()),
                                    APPEARANCE_ORDER.index(settings.appearance()))  # fmt: skip
        self.appearance.changed.connect(self._appearance)
        self.cache = QLineEdit(str(settings.cache_dir()))
        self.cache.setObjectName("pathfield")
        self.cache.editingFinished.connect(
            lambda: settings.set_cache_dir(Path(self.cache.text().strip()).expanduser())
        )
        choose, clear = button("Wählen …"), button("Leeren")
        choose.clicked.connect(self.choose_cache)
        clear.clicked.connect(self.clear_cache)
        self.cleared = label("", "muted")
        log = button("Protokoll öffnen …")
        log.clicked.connect(self.open_log)
        rows = [
            ("Sprache", "Gilt nach einem Neustart.", [self.language]),
            ("Darstellung", "System folgt der Einstellung des Betriebssystems.",
             [self.appearance]),
            ("Temp-/Cache-Ordner",
             "Wellenformen und Zwischendateien. Kann gefahrlos geleert werden.",
             [self.cache, choose, clear]),
            ("Protokoll", "Hilfreich bei Fehlern und Support-Anfragen.", [log, self.cleared]),
        ]  # fmt: skip
        for k, (title, hint, widgets) in enumerate(rows):
            if k:
                box.addWidget(rule())
            box.addLayout(self._row(title, hint, widgets))
        about, about_box = card((18, 8, 18, 8))
        about_box.addLayout(self._row(
            "Über Chronon", "Version und Hinweise zur Messgenauigkeit.",
            [label(f"Version {__version__}", "mono")]))  # fmt: skip
        about_box.addWidget(label(
            "Chronon misst Versatz und Drift aus dem Ton. Elektrische Kopien (Pult-Kanäle) "
            "stimmen auf etwa 0,001 ms, Raummikrofone auf etwa 0,05–0,35 ms. Manche Uhren "
            "(Akku-Rekorder) wandern über Stunden um ±2 ms; das Ergebnis zeigt das als "
            "„Uhr wandert“.", "muted", wrap=True))  # fmt: skip
        column = QVBoxLayout(self)
        column.setContentsMargins(28, 22, 28, 22)
        column.setSpacing(18)
        column.addWidget(main)
        column.addWidget(about)
        column.addStretch()

    @staticmethod
    def _row(title: str, hint: str, widgets: list[QWidget]) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setContentsMargins(0, 8, 0, 8)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label(title))
        words.addWidget(label(hint, "muted", wrap=True))
        holder = QWidget()
        holder.setLayout(words)
        holder.setFixedWidth(276)
        line.addWidget(holder)
        for w in widgets:
            line.addWidget(w, 1 if isinstance(w, QLineEdit) else 0)
        if not any(isinstance(w, QLineEdit) for w in widgets):
            line.addStretch()
        return line

    def _appearance(self, k: int) -> None:
        value = APPEARANCE_ORDER[k]
        settings.set_appearance(value)
        self.appearance_changed.emit(value)

    def choose_cache(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Cache-Ordner wählen", self.cache.text())
        if folder:
            self.cache.setText(folder)
            settings.set_cache_dir(Path(folder))

    def clear_cache(self) -> int:
        """Delete the waveform overviews (only Chronon's own files)."""
        folder = settings.cache_dir()
        removed = 0
        for f in folder.glob("*.peaks.npy") if folder.is_dir() else []:
            try:
                f.unlink()
                removed += 1
            except OSError:
                continue
        self.cleared.setText(f"{removed} Wellenformen gelöscht." if removed else "Cache ist leer.")
        return removed

    def open_log(self) -> None:
        """The protocols of this session (the folder, or the newest protocol in it)."""
        logs = self.project.folder() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        newest = max(logs.glob("*.log"), key=lambda p: p.stat().st_mtime, default=None)
        target = newest or logs
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
