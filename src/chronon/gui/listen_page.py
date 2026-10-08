"""Step 5, Hören: every file with its verdict; the whole file's waveform under the reference's
for the same time, played side by side or mixed from a position (board 10, with the whole file
instead of ±5 s and no slider). Files that need checking come first."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chronon import analysis, audio, listen
from chronon.gui import fmt, texts
from chronon.gui.audition import RATE, Overviews, Player, Waveform
from chronon.gui.project import Project
from chronon.gui.result_page import hint
from chronon.gui.widgets import Segmented, badge, button, card, label


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

    def __init__(
        self, project: Project, tokens: dict[str, str], player: Player, overviews: Overviews
    ):
        super().__init__()
        self.project, self.tokens, self.player = project, tokens, player
        self.overviews = overviews
        self.span = (0.0, 0.0)  # the current file in project time
        self.timeline: listen.Timeline | None = None
        self.rows: list[dict] = []
        self.current: dict | None = None
        self.pos = 0.0  # project (timeline) time
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
        line.addWidget(label("WELLENFORM · GANZE DATEI", "section"))
        line.addStretch()
        self.span_label = label("", "muted")
        line.addWidget(self.span_label)
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
            w.seek.connect(lambda f: self.move_to(self.span[0] + f * (self.span[1] - self.span[0])))
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
        waves_box.addLayout(controls)
        overviews.ready.connect(lambda _path, _peaks: self._show_waves())
        overviews.progress.connect(self._loading)

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
        for w in (top, waves, playback):
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
        first = next(k for k in range(self.list.count())
                     if self.list.item(k).data(Qt.ItemDataRole.UserRole))  # fmt: skip
        self.list.setCurrentRow(first)

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
        self.span = (start - zero, end - zero)
        self.span_label.setText(f"{fmt.duration(self.span[0])} – {fmt.duration(self.span[1])}")
        if not self.span[0] <= self.pos <= self.span[1]:
            self.pos = self.timeline.suggest(path, 0.0) - zero
        self._show_waves()
        self.move_to(self.pos)

    # --- position ---------------------------------------------------------------------
    def move_to(self, seconds: float) -> None:
        """A project time within the current file."""
        self.pos = min(max(seconds, self.span[0]), self.span[1])
        self.time.setText(fmt.duration(self.pos))
        self._show_cursor()
        if self.player.playing:
            self.play()

    def _show_cursor(self) -> None:
        length = self.span[1] - self.span[0]
        cursor = (self.pos - self.span[0]) / length if length > 0 else None
        self.ref_wave.set_cursor(cursor)
        self.file_wave.set_cursor(cursor)

    def _pair(self, start: float, seconds: float, rate: int = RATE) -> np.ndarray:
        """Reference (left) and the file (right) from project time ``start``."""
        assert self.timeline is not None and self.current is not None
        return self.timeline.pair(self.current["file"], start + self.timeline.zero, seconds, rate)

    def _waves(self) -> tuple[Path, Path] | None:
        if self.current is None or self.timeline is None:
            return None
        path = Path(self.current["file"])
        return path, self.timeline.result(path).reference

    def _show_waves(self) -> None:
        """The file's whole overview, and the reference's for the same time (silent where the
        reference has no audio); 'being computed' until an overview is there."""
        paths = self._waves()
        if paths is None:
            return
        path, ref = paths
        assert self.timeline is not None
        zero = self.timeline.zero
        file_peaks, ref_peaks = self.overviews.get(path), self.overviews.get(ref)
        if file_peaks is None:
            self.file_wave.set_loading()
        else:
            self.file_wave.set_peaks(file_peaks)
        if ref_peaks is None:
            self.ref_wave.set_loading()
        else:
            ref_t = [self.timeline.file_time(ref, t + zero) for t in self.span]
            self.ref_wave.set_peaks(listen.window(ref_peaks, *ref_t))
        self._show_cursor()

    def _loading(self, path: Path, share: float) -> None:
        paths = self._waves()
        if paths is not None and path == paths[0]:
            self.file_wave.set_loading(share)
        if paths is not None and path == paths[1]:
            self.ref_wave.set_loading(share)

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

        if not self.player.play(source, self.pos, self.span[1]):
            self.play_button.setText("Keine Audioausgabe")

    def _playing_at(self, seconds: float) -> None:
        if self.isVisible():
            self.pos = seconds
            self.time.setText(fmt.duration(seconds))
            self._show_cursor()
