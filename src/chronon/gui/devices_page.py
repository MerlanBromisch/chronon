"""Step 2, Geräte & Referenz: check the devices, name and order them, regroup clips, pick the
reference device and its tracks (boards 05–05c). The audition follows in the next step.

Everything here edits ``Project.layout`` (``devices.Layout``): a property of the project,
never of the files."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from chronon import audio, devices
from chronon.gui import fmt
from chronon.gui.audition import Audition, Overviews, Player
from chronon.gui.project import Detector, Project

DRAG_MIME = "application/x-chronon-device"
COLUMNS = (34, 30, 0, 160, 96, 180, 112, 36)  # handle, radio, name, kind, rate, role, files, …


# --- texts -------------------------------------------------------------------------------


def kind(device: devices.Device, infos: dict[Path, audio.Info]) -> str:
    """'18 Spuren parallel', '2 Clips, Stereo', '3 Videoclips', '1 Datei' (board 05)."""
    tracks = max(len(c.tracks) for c in device.clips)
    clips = len(device.clips)
    first = [infos[c.tracks[0]] for c in device.clips if c.tracks[0] in infos]
    video = any(i.has_video for i in first)
    stereo = not video and tracks == 1 and all(i.channels == 2 for i in first)
    if tracks > 1:
        text = f"{tracks} Spuren parallel" + (f", {clips} Aufnahmen" if clips > 1 else "")
    elif video:
        text = "1 Videoclip" if clips == 1 else f"{clips} Videoclips"
    else:
        text = "1 Datei" if clips == 1 else f"{clips} Clips"
    return text + (", Stereo" if stereo else "")


def rate(device: devices.Device, infos: dict[Path, audio.Info]) -> str:
    rates = {infos[f].sample_rate for f in device.files if f in infos}
    return fmt.rate(rates.pop()) if len(rates) == 1 else "–"


def name_problem(name: str, layout: devices.Layout, keep: devices.Device | None = None) -> str:
    """Why a device name cannot be used ('' if it can): it names files and lanes."""
    if not name.strip():
        return "Der Name fehlt."
    if re.search(r'[/\\:*?"<>|]', name):
        return 'Ein Name darf keines dieser Zeichen enthalten: / \\ : * ? " < > |'
    key = unicodedata.normalize("NFC", name.strip()).casefold()
    for d in layout.devices:
        if d is not keep and unicodedata.normalize("NFC", d.name.strip()).casefold() == key:
            return f"Ein Gerät heißt schon „{d.name}“."
    return ""


def default_tracks(
    device: devices.Device, infos: dict[Path, audio.Info], suggested: list[Path]
) -> list[Path]:
    """Reference tracks when the user picks a device: Chronon's suggestion where it applies,
    else the longest clip (its first two tracks)."""
    if suggested and all(t in device.files for t in suggested):
        return list(suggested)
    clip = max(
        device.clips, key=lambda c: infos[c.tracks[0]].duration_s if c.tracks[0] in infos else 0
    )
    return list(clip.tracks[:2])


# --- small widgets ------------------------------------------------------------------------


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


def _small_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("role", "small")
    return b


def _more_button() -> QPushButton:
    """The row menu button '…'."""
    b = QPushButton("···")
    b.setProperty("role", "more")
    b.setFixedSize(32, 26)
    return b


class Handle(QLabel):
    """The six-dot grip: drag a row to another position."""

    def __init__(self, index: int):
        super().__init__("⠿")
        self.index = index
        self.setObjectName("handle")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, event):  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            drag = QDrag(self)
            data = QMimeData()
            data.setData(DRAG_MIME, str(self.index).encode())
            drag.setMimeData(data)
            drag.setPixmap(self.parentWidget().grab())
            drag.exec(Qt.DropAction.MoveAction)


class Chip(QPushButton):
    """A reference track: number and name, toggled by a click, renamed by a double click."""

    rename = Signal()

    def mouseDoubleClickEvent(self, event):  # noqa: N802 (Qt API)
        self.rename.emit()


# --- dialogs -----------------------------------------------------------------------------


class ChooseDevice(QDialog):
    """'Datei verschieben' (05b) and 'Geräte zusammenführen' (05c): pick a device; devices
    with parallel tracks cannot take clips and are shown disabled."""

    def __init__(self, parent, layout: devices.Layout, infos, title: str, intro: str,
                 current: devices.Device, merge: bool):  # fmt: skip
        super().__init__(parent)
        self.layout, self.current, self.merge = layout, current, merge
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(520)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 22)
        box.setSpacing(12)
        box.addWidget(_label(title, "dialogtitle"))
        intro_label = _label(intro)
        intro_label.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(intro_label)

        frame = QFrame()
        frame.setObjectName("choices")
        rows = QVBoxLayout(frame)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        self.group = QButtonGroup(self)
        self.targets: list[int | None] = []
        for k, d in enumerate(layout.devices):
            if merge and d is current:
                continue
            here = d is current
            text = f"<b>{d.name}</b>" + (" · aktuell" if here else "")
            radio = QRadioButton()
            row = self._choice(radio, _label(text), _label(kind(d, infos), "muted"))
            disabled = here or d.multitrack
            radio.setEnabled(not disabled)
            row.setEnabled(not disabled)
            self.group.addButton(radio, len(self.targets))
            self.targets.append(k)
            rows.addWidget(row)
        self.new_name = QLineEdit()
        if not merge:
            self.new_name.setPlaceholderText("Name des neuen Geräts")
            radio = QRadioButton()
            row = self._choice(radio, _label("<b>Neues Gerät</b>"), self.new_name)
            self.group.addButton(radio, len(self.targets))
            self.targets.append(None)
            rows.addWidget(row)
            self.new_name.textEdited.connect(lambda: radio.setChecked(True))
        box.addWidget(frame)

        if merge:
            line = QHBoxLayout()
            line.addWidget(_label("Name danach"))
            line.addWidget(self.new_name, 1)
            box.addLayout(line)
        self.problem = _label("", "problem")
        box.addWidget(self.problem)
        box.addWidget(_label(
            "Die Clips werden nach Startzeit geordnet. Das gilt nur in diesem Projekt."
            if merge else
            "Die Zuordnung gilt nur in diesem Projekt. Die Originaldatei bleibt unverändert.",
            "muted"))  # fmt: skip
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Abbrechen")
        self.ok = QPushButton("Zusammenführen" if merge else "Verschieben")
        self.ok.setProperty("role", "primary")
        cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        box.addLayout(buttons)
        first = next((b for b in self.group.buttons() if b.isEnabled()), None)
        if first is not None:
            first.setChecked(True)
            if merge:
                self.new_name.setText(layout.devices[self.target].name)
        self.group.idToggled.connect(self.check)
        self.new_name.textChanged.connect(self.check)
        self.check()

    @staticmethod
    def _choice(radio: QRadioButton, text: QWidget, right: QWidget) -> QFrame:
        row = QFrame()
        row.setObjectName("choice")
        line = QHBoxLayout(row)
        line.setContentsMargins(14, 8, 14, 8)
        line.addWidget(radio)
        line.addWidget(text, 1)
        line.addWidget(right)
        return row

    @property
    def target(self) -> int | None:
        k = self.group.checkedId()
        return self.targets[k] if k >= 0 else None

    def check(self, *_args) -> None:
        if self.merge and self.group.checkedId() >= 0 and self.sender() is self.group:
            self.new_name.setText(self.layout.devices[self.target].name)
        problem = ""
        if self.group.checkedId() < 0:
            problem = "Kein Gerät gewählt."
        elif self.merge or self.target is None:
            keep = self.layout.devices[self.target] if self.merge else None
            problem = name_problem(self.new_name.text(), self.layout, keep)
            if self.merge and problem and self.new_name.text().strip() == self.current.name:
                problem = ""  # the merged device may keep the name of the one that goes
        self.problem.setText(problem)
        self.ok.setEnabled(not problem)


# --- the page ----------------------------------------------------------------------------


class DevicesPage(QWidget):
    changed = Signal()

    def __init__(
        self, project: Project, tokens: dict[str, str], overviews: Overviews, player: Player
    ):
        super().__init__()
        self.project = project
        self.tokens = tokens
        self.detector: Detector | None = None
        self.open: devices.Device | None = None  # the device whose files are shown
        self.editing: devices.Device | None = None  # the device being renamed
        self.shown: devices.Device | None = None  # the device section 2 shows (its radio)
        self.selected: set[Path] = set()
        self.error = ""
        self.setAcceptDrops(True)

        self.status = _label("", "hint")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_card = QFrame()
        self.list_card.setObjectName("card")
        self.list_box = QVBoxLayout(self.list_card)
        self.list_box.setContentsMargins(1, 1, 1, 1)  # rows inside the card's border
        self.list_box.setSpacing(0)
        self.tracks_card = QFrame()
        self.tracks_card.setObjectName("card")
        card = QVBoxLayout(self.tracks_card)
        card.setContentsMargins(18, 16, 18, 16)
        card.setSpacing(12)
        self.tracks_box = QVBoxLayout()
        card.addLayout(self.tracks_box)
        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFixedHeight(1)
        card.addWidget(rule)
        self.audition = Audition(tokens, overviews, player)
        card.addWidget(self.audition)

        heading = QHBoxLayout()
        heading.addWidget(_label("1 · GERÄTE PRÜFEN, REFERENZ WÄHLEN", "section"))
        heading.addStretch()
        heading.addWidget(_label(
            "Namen und Reihenfolge gelten für Dateinamen und Timeline. Zeilen am Griff ziehen.",
            "muted"))  # fmt: skip
        inner = QWidget()
        inner.setObjectName("content")
        column = QVBoxLayout(inner)
        column.setContentsMargins(28, 22, 28, 22)
        column.setSpacing(10)
        column.addLayout(heading)
        column.addWidget(self.status)
        column.addWidget(self.list_card)
        column.addSpacing(8)
        column.addWidget(self.tracks_card)
        column.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # --- state ------------------------------------------------------------------------
    @property
    def layout_(self) -> devices.Layout | None:
        return self.project.layout

    @property
    def detecting(self) -> bool:
        return self.detector is not None

    @property
    def ready(self) -> bool:
        if self.layout_ is None or self.detecting:
            return False
        try:
            self.layout_.check()
        except devices.LayoutError:
            return False
        return True

    def enter(self) -> None:
        """Called when the step is shown: (re)detect devices when the files changed."""
        if self.project.layout_current or self.detecting:
            self.rebuild()
            return
        infos = self.project.infos
        if len(infos) < 2:
            self.project.layout = None
            self.rebuild()
            return
        self.project.layout = None
        self.detector = Detector(infos, self)
        self.detector.finished.connect(self._detected)
        self.rebuild()
        self.detector.start()

    def _detected(self, result) -> None:
        self.detector = None
        if isinstance(result, Exception):
            self.error = f"Die Geräte konnten nicht erkannt werden: {result}"
        else:
            self.error = ""
            self.project.layout = result
            self.project.suggested_tracks = list(result.tracks)
        self.open = self.editing = None
        self.selected.clear()
        self.rebuild()

    # --- building ---------------------------------------------------------------------
    def rebuild(self) -> None:
        for box in (self.list_box, self.tracks_box):
            _clear(box)
        layout = self.layout_
        if self.detecting:
            self.status.setText("Geräte werden erkannt …")
        elif self.error:
            self.status.setText(self.error)
        elif layout is None:
            self.status.setText("Zuerst in Schritt 1 mindestens zwei lesbare Dateien hinzufügen.")
        self.status.setVisible(self.detecting or bool(self.error) or layout is None)
        self.list_card.setVisible(layout is not None and not self.detecting)
        self.tracks_card.setVisible(layout is not None and not self.detecting)
        if layout is None or self.detecting:
            self.changed.emit()
            return
        self.list_box.addWidget(self._header())
        self.radios = QButtonGroup(self)
        for k, d in enumerate(layout.devices):
            self.list_box.addWidget(self._row(k, d))
            if d is self.open:
                self.list_box.addWidget(self._files(d))
        self._tracks()
        self.changed.emit()

    def _grid(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(10, 0, 14, 0)
        grid.setHorizontalSpacing(12)
        for col, width in enumerate(COLUMNS):
            if width:
                grid.setColumnMinimumWidth(col, width)
        grid.setColumnStretch(2, 1)
        return grid

    @staticmethod
    def _cell(grid: QGridLayout, content: QWidget | QHBoxLayout, col: int) -> None:
        """Put ``content`` in a column of exactly its width, so the columns of the header and
        of every row line up whatever a row holds."""
        box = QWidget()
        box.setFixedWidth(COLUMNS[col])
        line = QHBoxLayout(box)
        line.setContentsMargins(0, 0, 0, 0)
        if isinstance(content, QWidget):
            line.addWidget(content)
            line.addStretch()
        else:
            line.addLayout(content)
        grid.addWidget(box, 0, col)

    def _header(self) -> QFrame:
        row = QFrame()
        row.setObjectName("tablehead")
        row.setFixedHeight(44)
        grid = self._grid()
        row.setLayout(grid)
        grid.addWidget(_label("GERÄT", "colhead"), 0, 2)
        for col, text in ((3, "ART"), (4, "SAMPLERATE"), (5, "ROLLE")):
            self._cell(grid, _label(text, "colhead"), col)
        for col in (6, 7):
            self._cell(grid, QWidget(), col)
        return row

    def _row(self, k: int, d: devices.Device) -> QFrame:
        layout, infos = self.layout_, self.project.infos
        row = QFrame()
        row.setObjectName("devicerow")
        row.setProperty("selected", "true" if d is self._shown() else "false")
        row.setFixedHeight(44)
        grid = self._grid()
        row.setLayout(grid)
        grid.addWidget(Handle(k), 0, 0)
        radio = QRadioButton()
        radio.setChecked(d is self._shown())
        radio.setToolTip("Anzeigen und vorhören")
        radio.clicked.connect(lambda: self.show_device(d))
        self.radios.addButton(radio, k)
        grid.addWidget(radio, 0, 1)
        if d is self.editing:
            grid.addWidget(self._rename(d), 0, 2)
        else:
            name = _label(f"<b>{d.name}</b>")
            name.setCursor(Qt.CursorShape.IBeamCursor)
            name.setToolTip("Klicken zum Umbenennen")
            name.mousePressEvent = lambda _e, d=d: self.start_rename(d)
            grid.addWidget(name, 0, 2)
        self._cell(grid, _label(kind(d, infos)), 3)
        self._cell(grid, _label(rate(d, infos), "mono"), 4)
        roles = QHBoxLayout()
        roles.setSpacing(6)
        if k == layout.reference:
            roles.addWidget(_label("Referenz", "badgeref"))
        if k == layout.suggested:
            roles.addWidget(_label("Vorschlag", "badgesuggest"))
        roles.addStretch()
        self._cell(grid, roles, 5)
        files = _small_button(f"{fmt.files(len(d.files))} {'▴' if d is self.open else '▾'}")
        files.setProperty("open", "true" if d is self.open else "false")
        files.clicked.connect(lambda: self.toggle_files(d))
        self._cell(grid, files, 6)
        more = _more_button()
        more.clicked.connect(lambda: self._device_menu(k, d, more))
        self._cell(grid, more, 7)
        return row

    def _rename(self, d: devices.Device) -> QWidget:
        box = QWidget()
        line = QHBoxLayout(box)
        line.setContentsMargins(0, 0, 0, 0)
        name = QLineEdit(d.name)
        name.setMinimumWidth(180)
        ok = _small_button("OK")
        problem = _label("", "problem")
        line.addWidget(name, 1)
        line.addWidget(ok)
        line.addWidget(problem)

        def check() -> None:
            text = name_problem(name.text(), self.layout_, keep=d)
            problem.setText(text)
            ok.setEnabled(not text)

        def done() -> None:
            if not name_problem(name.text(), self.layout_, keep=d):
                d.name = name.text().strip()
                self.editing = None
                self.rebuild()

        name.textChanged.connect(check)
        name.returnPressed.connect(done)
        ok.clicked.connect(done)
        name.setFocus()
        name.selectAll()
        return box

    def _files(self, d: devices.Device) -> QFrame:
        """The device's files (05a); clips can be selected and regrouped."""
        infos = self.project.infos
        regroup = not d.multitrack
        panel = QFrame()
        panel.setObjectName("filespanel")
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        head = QGridLayout()
        head.setContentsMargins(58, 6, 14, 6)
        head.setColumnStretch(1, 1)
        head.setColumnMinimumWidth(2, 100)
        head.setColumnMinimumWidth(3, 130)
        head.setColumnMinimumWidth(4, 36)
        if regroup:
            every = QCheckBox()
            chosen = [f for f in d.files if f in self.selected]
            every.setTristate(True)
            every.setCheckState(
                Qt.CheckState.Checked if len(chosen) == len(d.files)
                else Qt.CheckState.PartiallyChecked if chosen else Qt.CheckState.Unchecked
            )  # fmt: skip
            every.clicked.connect(lambda: self.select(d.files, len(chosen) < len(d.files)))
            head.addWidget(every, 0, 0)
        for col, text in ((1, "DATEI"), (2, "DAUER"), (3, "STARTZEIT")):
            head.addWidget(_label(text, "colhead"), 0, col)
        box.addLayout(head)
        for f in d.files:
            line = QFrame()
            line.setObjectName("filerow")
            grid = QGridLayout(line)
            grid.setContentsMargins(58, 4, 14, 4)
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(2, 100)
            grid.setColumnMinimumWidth(3, 130)
            grid.setColumnMinimumWidth(4, 36)
            if regroup:
                check = QCheckBox()
                check.setChecked(f in self.selected)
                check.clicked.connect(lambda on, f=f: self.select([f], on))
                grid.addWidget(check, 0, 0)
            named = d.track_names.get(str(f), "")
            grid.addWidget(_label(f.name + (f"  ·  {named}" if named else ""), "mono"), 0, 1)
            info = infos.get(f)
            grid.addWidget(_label(fmt.duration(info.duration_s) if info else "", "mono"), 0, 2)
            grid.addWidget(_label(fmt.start(info) if info else "", "mono"), 0, 3)
            if regroup:
                more = _more_button()
                more.clicked.connect(lambda _c=False, f=f, b=more: self._file_menu([f], b))
                grid.addWidget(more, 0, 4)
            box.addWidget(line)
        chosen = [f for f in d.files if f in self.selected]
        if chosen:
            bar = QFrame()
            bar.setObjectName("selectionbar")
            line = QHBoxLayout(bar)
            line.setContentsMargins(14, 8, 14, 8)
            count = (
                "1 Datei ausgewählt" if len(chosen) == 1 else f"{len(chosen)} Dateien ausgewählt"
            )
            line.addWidget(_label(f"<b>{count}</b>"))
            move = QPushButton("In Gerät verschieben …")
            split = QPushButton("Als neues Gerät abtrennen")
            clear = QPushButton("Auswahl aufheben")
            move.clicked.connect(lambda: self.move_to_device(chosen))
            split.clicked.connect(lambda: self.split_off(chosen))
            clear.clicked.connect(lambda: self.select(chosen, False))
            for b in (move, split, clear):
                line.addWidget(b)
            line.addStretch()
            box.addWidget(bar)
        return panel

    def _shown(self) -> devices.Device:
        """The device whose tracks section 2 shows: the one picked by its radio, else the
        reference."""
        layout = self.layout_
        if self.shown is None or not any(d is self.shown for d in layout.devices):
            self.shown = layout.devices[layout.reference]
        return self.shown

    def _tracks(self) -> None:
        """Section 2 for the device picked by its radio. The reference: '2 · Referenz-Spuren
        von <Gerät>' (the tracks of the clip holding the reference tracks, several can be
        chosen, or the clips of a clip device, one is chosen), then the audition. Any other
        device: only the audition of its files."""
        layout = self.layout_
        ref = layout.devices[layout.reference]
        shown = self._shown()
        infos = self.project.infos
        if shown is not ref:
            head = QHBoxLayout()
            head.addWidget(_label(f"2 · {shown.name}", "cardtitle"))
            head.addStretch()
            head.addWidget(_label(
                f"Referenz ist {ref.name}; ändern im Menü „…“ eines Geräts.", "muted"))  # fmt: skip
            self.tracks_box.addLayout(head)
            files = shown.files
            self.audition.set_tracks(
                [(f, shown.track_label(f) if shown.multitrack else f.name) for f in files],
                {f: infos[f].duration_s for f in files if f in infos},
            )
            return
        clip = next((c for c in ref.clips if set(layout.tracks) <= set(c.tracks)), ref.clips[0])
        multi = len(clip.tracks) > 1
        head = QHBoxLayout()
        title = f"2 · Referenz-{'Spuren' if multi else 'Aufnahme'} von {ref.name}"
        head.addWidget(_label(title, "cardtitle"))
        head.addStretch()
        if multi:
            hint = "Doppelklick benennt eine Spur."
            if (
                layout.reference == layout.suggested
                and layout.tracks == self.project.suggested_tracks
            ):
                hint = f"Vorschlag: die lautesten Spuren, {len(layout.tracks)} gewählt. " + hint
            head.addWidget(_label(hint, "muted"))
        self.tracks_box.addLayout(head)
        chips = QWidget()
        flow = _Flow(chips)
        choices = clip.tracks if multi else [c.tracks[0] for c in ref.clips]
        self.chips: dict[Path, Chip] = {}
        for n, t in enumerate(choices, 1):
            label = ref.track_label(t) if multi else t.name
            text = f"{n}  {label}" if multi and label != str(n) else str(n) if multi else label
            chip = Chip(text)
            chip.setProperty("role", "chip")
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            chip.setFixedWidth(chip.fontMetrics().horizontalAdvance(text) + 30)
            chip.setCheckable(True)
            chip.setChecked(t in layout.tracks)
            chip.clicked.connect(lambda _c=False, t=t: self.toggle_track(t))
            if multi:
                chip.rename.connect(lambda t=t, chip=chip: self.rename_track(t, chip))
            flow.add(chip)
            self.chips[t] = chip
        flow.finish()
        self.tracks_box.addWidget(chips)
        self.chip_area = chips
        listen_to = [t for t in choices if t in layout.tracks] + [
            t for t in choices if t not in layout.tracks
        ]
        self.audition.set_tracks(
            [(t, ref.track_label(t) if multi else t.name) for t in listen_to],
            {t: infos[t].duration_s for t in choices if t in infos},
        )

    # --- actions ----------------------------------------------------------------------
    def show_device(self, d: devices.Device) -> None:
        self.shown = d
        self.rebuild()

    def set_reference(self, k: int) -> None:
        layout = self.layout_
        self.shown = layout.devices[k]  # section 2 shows the new reference's tracks
        if k != layout.reference:
            layout.reference = k
            for j, d in enumerate(layout.devices):
                d.is_reference = j == k
            layout.tracks = default_tracks(
                layout.devices[k], self.project.infos, self.project.suggested_tracks
            )
        self.rebuild()

    def toggle_track(self, track: Path) -> None:
        layout = self.layout_
        ref = layout.devices[layout.reference]
        clip = next(c for c in ref.clips if track in c.tracks)
        if len(clip.tracks) == 1:  # a clip device: one clip is the reference
            layout.tracks = [track]
        elif track in layout.tracks:
            if len(layout.tracks) > 1:  # at least one track stays
                layout.tracks = [t for t in layout.tracks if t != track]
        else:
            in_clip = [t for t in layout.tracks if t in clip.tracks]
            layout.tracks = [t for t in clip.tracks if t in in_clip or t == track]
        self.rebuild()

    def rename_track(self, track: Path, chip: Chip) -> None:
        ref = self.layout_.devices[self.layout_.reference]
        edit = QLineEdit(ref.track_names.get(str(track), ""), self.chip_area)
        edit.setPlaceholderText("Name")
        edit.setGeometry(chip.geometry().adjusted(0, 0, 60, 0))
        edit.show()
        edit.setFocus()

        def done() -> None:
            name = edit.text().strip()
            if name:
                ref.track_names[str(track)] = name
            else:
                ref.track_names.pop(str(track), None)
            edit.deleteLater()
            self.rebuild()

        edit.editingFinished.connect(done)

    def start_rename(self, d: devices.Device) -> None:
        self.editing = d
        self.rebuild()

    def toggle_files(self, d: devices.Device) -> None:
        self.open = None if self.open is d else d
        self.selected.clear()
        self.rebuild()

    def select(self, files: list[Path], on: bool) -> None:
        if on:
            self.selected.update(files)
        else:
            self.selected.difference_update(files)
        self.rebuild()

    def move_device(self, k: int, to: int) -> None:
        if 0 <= to < len(self.layout_.devices) and to != k:
            self.layout_.move_device(k, to)
            self.rebuild()

    def _device_menu(self, k: int, d: devices.Device, anchor: QWidget) -> None:
        menu = QMenu(self)
        reference = menu.addAction("Als Referenz verwenden", lambda: self.set_reference(k))
        reference.setEnabled(k != self.layout_.reference)
        menu.addSeparator()
        menu.addAction("Umbenennen …", lambda: self.start_rename(d))
        up = menu.addAction("Nach oben", lambda: self.move_device(k, k - 1))
        down = menu.addAction("Nach unten", lambda: self.move_device(k, k + 1))
        up.setEnabled(k > 0)
        down.setEnabled(k + 1 < len(self.layout_.devices))
        merge = menu.addAction("Zusammenführen mit …", lambda: self.merge(k))
        merge.setEnabled(not d.multitrack and len(self.layout_.devices) > 1)
        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height())))
        self.menu = menu

    def _file_menu(self, files: list[Path], anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction("In Gerät verschieben …", lambda: self.move_to_device(files))
        menu.addAction("Als neues Gerät abtrennen", lambda: self.split_off(files))
        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height())))
        self.menu = menu

    def move_to_device(self, files: list[Path]) -> None:
        layout = self.layout_
        current = layout.device_of(files[0])
        names = files[0].name if len(files) == 1 else f"{len(files)} Dateien"
        verb = "gehört" if len(files) == 1 else "gehören"
        self.dialog = ChooseDevice(
            self, layout, self.project.infos, "Datei verschieben" if len(files) == 1
            else "Dateien verschieben", f"<span style='font-family: monospace'>{names}</span> "
            f"{verb} zu:", current, merge=False)  # fmt: skip
        self.dialog.accepted.connect(
            lambda: self._regroup(files, self.dialog.target, self.dialog.new_name.text().strip())
        )
        self.dialog.open()

    def split_off(self, files: list[Path]) -> None:
        layout = self.layout_
        base = files[0].stem
        name, n = base, 2
        while name_problem(name, layout):
            name, n = f"{base} {n}", n + 1
        self._regroup(files, None, name)

    def _regroup(self, files: list[Path], to: int | None, name: str) -> None:
        self.layout_.move_files(files, to, name)
        self.selected.difference_update(files)
        if self.open is not None and self.open not in self.layout_.devices:
            self.open = None
        self.rebuild()

    def merge(self, k: int) -> None:
        layout = self.layout_
        d = layout.devices[k]
        clips = "1 Clip" if len(d.clips) == 1 else f"{len(d.clips)} Clips"
        self.dialog = ChooseDevice(
            self, layout, self.project.infos, "Geräte zusammenführen",
            f"<b>{d.name}</b> ({clips}) wird zusammengeführt mit:", d, merge=True)  # fmt: skip
        infos = self.project.infos

        def start(path: Path) -> float:
            info = infos.get(path)
            return float(info.start) if info is not None and info.start is not None else 0.0

        def accepted() -> None:
            into = self.dialog.target
            layout.merge(k, into, self.dialog.new_name.text().strip(), start=start)
            self.open = None
            self.rebuild()

        self.dialog.accepted.connect(accepted)
        self.dialog.open()

    # --- drag and drop of rows ------------------------------------------------------------
    def dragEnterEvent(self, event):  # noqa: N802 (Qt API)
        if event.mimeData().hasFormat(DRAG_MIME):
            event.acceptProposedAction()

    dragMoveEvent = dragEnterEvent  # noqa: N815

    def dropEvent(self, event):  # noqa: N802
        k = int(bytes(event.mimeData().data(DRAG_MIME)).decode())
        y = self.list_card.mapFrom(self, event.position().toPoint()).y()
        rows = [
            w for w in self.list_card.findChildren(QFrame, "devicerow")
            if w.parentWidget() is self.list_card
        ]  # fmt: skip
        to = sum(1 for w in rows if w.geometry().center().y() < y)
        to = to - 1 if to > k else to
        event.acceptProposedAction()
        self.move_device(k, max(0, min(to, len(rows) - 1)))


class _Flow:
    """Chips in rows that wrap (a simple flow layout on a fixed grid of rows)."""

    PER_ROW = 16

    def __init__(self, parent: QWidget):
        self.box = QVBoxLayout(parent)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(6)
        self.line: QHBoxLayout | None = None
        self.count = 0

    def add(self, w: QWidget) -> None:
        if self.line is None or self.count % self.PER_ROW == 0:
            if self.line is not None:
                self.line.addStretch()
            self.line = QHBoxLayout()
            self.line.setSpacing(6)
            self.box.addLayout(self.line)
        self.line.addWidget(w)
        self.count += 1
        if self.count % self.PER_ROW == 0:
            self.line.addStretch()

    def finish(self) -> None:
        if self.line is not None and self.count % self.PER_ROW:
            self.line.addStretch()


def _clear(layout) -> None:
    """Empty a layout; its widgets disappear at once (deleted later, as Qt requires)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _clear(item.layout())
