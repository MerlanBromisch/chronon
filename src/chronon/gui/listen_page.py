"""Step 5, Hören: every file with its verdict; reference and file around a position, played
side by side or mixed (board 10). Files that need checking come first."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from chronon import analysis, audio, listen
from chronon.gui import fmt, texts
from chronon.gui.audition import RATE, Player, Waveform
from chronon.gui.project import Project
from chronon.gui.result_page import hint
from chronon.gui.widgets import Segmented, badge, button, card, label

AROUND_S = 5.0  # the waveforms show this much on both sides of the position
VIEW_RATE = 8_000  # excerpts for the waveforms


class FileItem(QWidget):
    def __init__(self, row: dict):
        super().__init__()
        line = QHBoxLayout(self)
        line.setContentsMargins(14, 6, 14, 6)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label(Path(row["file"]).name, "monobold"))
        names.addWidget(label(row["device"], "muted"))
        line.addLayout(names, 1)
        line.addWidget(badge(texts.verdict(row)))


class ListenPage(QWidget):
    changed = Signal()

    def __init__(self, project: Project, tokens: dict[str, str], player: Player):
        super().__init__()
        self.project, self.tokens, self.player = project, tokens, player
        self.timeline: listen.Timeline | None = None
        self.rows: list[dict] = []
        self.current: dict | None = None
        self.pos = 0.0  # project (timeline) time
        self.total = 0.0
        self.loaded_for = ""

        # left: the files
        left, left_box = card((0, 0, 0, 0))
        left.setFixedWidth(340)
        head = QHBoxLayout()
        head.setContentsMargins(14, 10, 14, 10)
        head.addWidget(label("DATEI PRÜFEN", "section"))
        head.addStretch()
        self.count = label("", "section")
        head.addWidget(self.count)
        left_box.addLayout(head)
        self.list = QListWidget()
        self.list.setObjectName("filelist")
        self.list.currentRowChanged.connect(self._picked)
        left_box.addWidget(self.list)

        # right: file, waveforms, position, playback
        top, top_box = card()
        line = QHBoxLayout()
        self.name = label("", "monotitle")
        self.badge_box = QHBoxLayout()
        self.device = label("", "muted")
        line.addWidget(self.name)
        line.addLayout(self.badge_box)
        line.addStretch()
        line.addWidget(self.device)
        self.hint = label("", "hint", wrap=True)
        top_box.addLayout(line)
        top_box.addWidget(self.hint)

        waves, waves_box = card()
        line = QHBoxLayout()
        self.around = label("", "section")
        line.addWidget(self.around)
        line.addStretch()
        line.addWidget(label("±5 s um die Position", "muted"))
        waves_box.addLayout(line)
        grid = QGridLayout()
        grid.setColumnMinimumWidth(0, 100)
        grid.setColumnStretch(1, 1)
        self.ref_wave = Waveform(tokens, 56)
        self.file_wave = Waveform(tokens, 56)
        self.file_label = label("", "monobold")
        grid.addWidget(label("Referenz"), 0, 0)
        grid.addWidget(self.ref_wave, 0, 1)
        grid.addWidget(self.file_label, 1, 0)
        grid.addWidget(self.file_wave, 1, 1)
        waves_box.addLayout(grid)
        for w in (self.ref_wave, self.file_wave):
            w.seek.connect(lambda f: self.move_to(self.pos + (f - 0.5) * 2 * AROUND_S))

        position, pos_box = card()
        pos_box.addWidget(label("POSITION IM PROJEKT", "section"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(lambda v: self.move_to(v / 1000 * self.total, later=True))
        pos_box.addWidget(self.slider)
        self.ticks = QHBoxLayout()
        pos_box.addLayout(self.ticks)
        controls = QHBoxLayout()
        for text, delta in (("−1 min", -60), ("−10 s", -10)):
            b = button(text)
            b.clicked.connect(lambda _c=False, d=delta: self.move_to(self.pos + d))
            controls.addWidget(b)
        self.time = label("0:00:00", "bigclock")
        controls.addWidget(self.time)
        for text, delta in (("+10 s", 10), ("+1 min", 60)):
            b = button(text)
            b.clicked.connect(lambda _c=False, d=delta: self.move_to(self.pos + d))
            controls.addWidget(b)
        controls.addStretch()
        pos_box.addLayout(controls)

        playback, play_box = card()
        play_box.addWidget(label("WIEDERGABE", "section"))
        line = QHBoxLayout()
        self.mode = Segmented(["Referenz links · Datei rechts", "Gemischt"])
        self.mode.changed.connect(lambda _k: self.player.playing and self.play())
        self.play_button = button("▶  Play", "primary")
        self.play_button.setMinimumWidth(110)
        self.play_button.clicked.connect(self.toggle)
        line.addWidget(self.mode)
        line.addStretch()
        line.addWidget(self.play_button)
        play_box.addLayout(line)

        right = QVBoxLayout()
        right.setSpacing(14)
        for w in (top, waves, position, playback):
            right.addWidget(w)
        right.addWidget(label(
            "Klingt es wie ein einziger Ton, liegt die Datei richtig. "
            "Ein Echo deutet auf Versatz.", "hint", wrap=True))  # fmt: skip
        right.addStretch()
        self.empty = label("Noch kein Ergebnis: zuerst in Schritt 3 synchronisieren.",
                           "placeholder")  # fmt: skip
        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(left)
        body.addLayout(right, 1)
        self.body = QWidget()
        self.body.setLayout(body)
        column = QVBoxLayout(self)
        column.setContentsMargins(28, 22, 28, 22)
        column.addWidget(self.empty)
        column.addWidget(self.body)

        self.refresh = QTimer(self)
        self.refresh.setSingleShot(True)
        self.refresh.setInterval(120)
        self.refresh.timeout.connect(self._show_waves)
        player.position.connect(self._playing_at)
        player.playing_changed.connect(
            lambda on: self.play_button.setText("❚❚  Pause" if on else "▶  Play")
        )

    # --- data -----------------------------------------------------------------------------
    def enter(self) -> None:
        ok = self.project.synced_now and self.project.analysis_path is not None
        self.empty.setVisible(not ok)
        self.body.setVisible(ok)
        if not ok:
            self.changed.emit()
            return
        key = self.project.synced + str(id(self.project.result))
        if key != self.loaded_for:
            self.loaded_for = key
            self._load()
        self.changed.emit()

    def _load(self) -> None:
        measured = analysis.Analysis.load(self.project.analysis_path)
        infos = self.project.infos
        self.timeline = listen.Timeline(
            measured, probe=lambda p: infos.get(p) or audio.probe(p)
        )  # fmt: skip
        rows = [r for r in self.project.result["files"] if not r.get("is_reference")]
        order = {"unsure": 0, "wanders": 1, "ok": 2}
        self.rows = sorted(rows, key=lambda r: order[texts.verdict(r)])
        self.total = max(
            (self.timeline.span(p)[1] - self.timeline.zero for p in self.timeline.entries),
            default=0.0,
        )
        self.list.clear()
        check = [r for r in self.rows if texts.verdict(r) != "ok"]
        self.count.setText(fmt.files(len(self.rows)).upper())
        for title, part in (("ZU PRÜFEN", check), ("ZUVERLÄSSIG", self.rows[len(check) :])):
            if not part:
                continue
            header = QListWidgetItem(f"{title}    {len(part)}")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setData(Qt.ItemDataRole.UserRole, None)
            self.list.addItem(header)
            for r in part:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, r)
                widget = FileItem(r)
                item.setSizeHint(widget.sizeHint())
                self.list.addItem(item)
                self.list.setItemWidget(item, widget)
        self._ticks()
        first = next(k for k in range(self.list.count())
                     if self.list.item(k).data(Qt.ItemDataRole.UserRole))  # fmt: skip
        self.list.setCurrentRow(first)

    def _ticks(self) -> None:
        while self.ticks.count():
            item = self.ticks.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        for k in range(5):
            if k:
                self.ticks.addStretch()
            self.ticks.addWidget(label(fmt.duration(self.total * k / 4), "mono"))

    def _picked(self, k: int) -> None:
        item = self.list.item(k)
        row = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if row is None or self.timeline is None:
            return
        self.player.stop()
        self.current = row
        path = Path(row["file"])
        self.name.setText(path.name)
        while self.badge_box.count():
            old = self.badge_box.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        self.badge_box.addWidget(badge(texts.verdict(row)))
        self.device.setText(f"Gerät {row['device']}")
        self.hint.setText(hint(row, links=False))
        self.hint.setVisible(bool(self.hint.text()))
        self.file_label.setText(path.stem)
        start, end = self.timeline.span(path)
        zero = self.timeline.zero
        if not start - zero <= self.pos <= end - zero:
            self.pos = self.timeline.suggest(path, 0.0) - zero
        self.move_to(self.pos)

    # --- position ---------------------------------------------------------------------
    def move_to(self, seconds: float, later: bool = False) -> None:
        self.pos = min(max(seconds, 0.0), self.total)
        self.time.setText(fmt.duration(self.pos))
        self.around.setText(f"WELLENFORM UM {fmt.duration(self.pos)}")
        if not self.slider.isSliderDown() and self.total:
            self.slider.setValue(round(1000 * self.pos / self.total))
        if self.player.playing and not later:
            self.play()
        if later:
            self.refresh.start()
        else:
            self._show_waves()

    def _pair(self, start: float, seconds: float, rate: int = RATE) -> np.ndarray:
        """Reference (left) and the file (right) from project time ``start``."""
        assert self.timeline is not None and self.current is not None
        return self.timeline.pair(self.current["file"], start + self.timeline.zero, seconds, rate)

    def _show_waves(self) -> None:
        if self.current is None:
            return
        pair = self._pair(self.pos - AROUND_S, 2 * AROUND_S, VIEW_RATE)
        self.ref_wave.set_samples(pair[:, 0], 0.5)
        self.file_wave.set_samples(pair[:, 1], 0.5)

    # --- playback ---------------------------------------------------------------------
    def toggle(self) -> None:
        if self.player.playing:
            self.player.stop()
        else:
            self.play()

    def play(self) -> None:
        if self.current is None:
            return
        mixed = self.mode.current == 1

        def source(start: float, seconds: float) -> np.ndarray:
            pair = self._pair(start, seconds)
            if mixed:
                mono = pair.mean(axis=1)
                return np.column_stack([mono, mono])
            return pair

        if not self.player.play(source, self.pos, self.total):
            self.play_button.setText("Keine Audioausgabe")

    def _playing_at(self, seconds: float) -> None:
        if self.isVisible():
            self.pos = seconds
            self.time.setText(fmt.duration(seconds))
            self.around.setText(f"WELLENFORM UM {fmt.duration(seconds)}")
            if self.total:
                self.slider.setValue(round(1000 * seconds / self.total))
            self.refresh.start()
