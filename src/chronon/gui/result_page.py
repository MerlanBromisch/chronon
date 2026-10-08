"""Step 4, Ergebnis: how it went, a view-only timeline, measurement details (boards 08, 09)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from chronon.gui import texts, theme
from chronon.gui.project import Project
from chronon.gui.widgets import badge, button, card, label, status_square

LEFT_MIDDLE = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def ppm(value: float) -> str:
    return f"{value:+.2f} ppm".replace("-", "−").replace(".", ",")


def clock_ms(seconds: float) -> str:
    """``0:00:03.120`` (timeline position)."""
    sign = "−" if seconds < 0 else ""
    ms = round(abs(seconds) * 1000)
    s, ms = divmod(ms, 1000)
    return f"{sign}{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}.{ms:03d}"


class Lane:
    def __init__(self, name: str, bars: list[tuple[float, float]], reference: bool):
        self.name, self.bars, self.reference = name, bars, reference


def lanes(project: Project) -> tuple[list[Lane], float]:
    """Each device's clips on the timeline of the originals (from the analysis result), and
    the timeline's length. The reference device starts where the result's zero puts it."""
    layout, result = project.layout, project.result or {}
    rows = {Path(r["file"]): r for r in result.get("files", [])}
    zero = min([0.0, *(r["offset_s"] for r in rows.values())])
    infos = project.infos
    out, end = [], 0.0
    for k, d in enumerate(layout.devices):
        bars = []
        for clip in d.clips:
            f = clip.tracks[0]
            r = rows.get(f)
            if r is not None:
                start, length = r["placement"]["position_s"], r["placement"]["duration_s"]
            elif f in infos:  # a reference track: on the reference clock
                start, length = -zero, infos[f].duration_s
            else:
                continue
            bars.append((start, length))
            end = max(end, start + length)
        out.append(Lane(d.name, bars, k == layout.reference))
    return out, end


class Timeline(QWidget):
    """Lanes per device, view only (board 08)."""

    LANE = 35
    LEFT = 200

    def __init__(self, tokens: dict[str, str]):
        super().__init__()
        self.tokens = tokens
        self.lanes: list[Lane] = []
        self.total = 0.0

    def set_lanes(self, lanes: list[Lane], total: float) -> None:
        self.lanes, self.total = lanes, total
        self.setFixedHeight(30 + self.LANE * len(lanes))
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt API)
        t = self.tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width() - self.LEFT
        if self.total <= 0 or width <= 0:
            return
        mono = theme.mono_font(11)
        p.setFont(mono)
        p.setPen(QColor(t["text2"]))
        step = _tick(self.total)
        x = 0.0
        while x <= self.total + 1e-6:
            px = self.LEFT + width * x / self.total
            text = _axis(x, self.total)
            align = (
                Qt.AlignmentFlag.AlignRight if x > self.total * 0.95 else Qt.AlignmentFlag.AlignLeft
            )
            p.drawText(QRectF(px - (60 if x > self.total * 0.95 else 0), 0, 60, 20),
                       align | Qt.AlignmentFlag.AlignVCenter, text)  # fmt: skip
            x += step
        name_font = QFont(self.font())
        for k, lane in enumerate(self.lanes):
            top = 30 + k * self.LANE
            p.setPen(QColor(t["divider"]))
            p.drawLine(0, top, self.width(), top)
            name_font.setBold(lane.reference)
            p.setFont(name_font)
            p.setPen(QColor(t["text"]))
            p.drawText(QRectF(0, top, self.LEFT - 10, self.LANE),
                       Qt.AlignmentFlag.AlignVCenter, lane.name)  # fmt: skip
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["input"]))
            p.drawRect(QRectF(self.LEFT, top + 9, width, self.LANE - 18))
            p.setBrush(QColor(t["accent"] if lane.reference else t["wave"]))
            for start, length in lane.bars:
                x0 = self.LEFT + width * max(start, 0) / self.total
                w = max(width * length / self.total, 2)
                p.drawRoundedRect(QRectF(x0, top + 11, w, self.LANE - 22), 2, 2)
        p.end()


def _axis(x: float, total: float) -> str:
    """Hours and minutes on long timelines, minutes and seconds on short ones."""
    s = round(x)
    if total >= 3600:
        return f"{s // 3600}:{s // 60 % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _tick(total: float) -> float:
    for step in (10, 30, 60, 300, 600, 1800, 3600, 7200):
        if total / step <= 6:
            return step
    return 14400


class ResultPage(QWidget):
    changed = Signal()
    other_reference = Signal()

    def __init__(self, project: Project, tokens: dict[str, str]):
        super().__init__()
        self.project, self.tokens = project, tokens
        self.details_open = False
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

    def enter(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        while self.column.count():
            item = self.column.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
            elif item.layout() is not None:
                _clear(item.layout())
        project = self.project
        if not project.synced_now:
            self.column.addWidget(label("Noch kein Ergebnis: zuerst in Schritt 3 synchronisieren.",
                                        "placeholder"))  # fmt: skip
            self.column.addStretch()
            self.changed.emit()
            return
        layout = project.layout
        rows = project.result["files"]
        by_device: dict[str, list[dict]] = {}
        for r in rows:
            by_device.setdefault(r["device"], []).append(r)
        unsure = [d.name for d in layout.devices
                  if any(not r["reliable"] for r in by_device.get(d.name, []))]  # fmt: skip
        count = len(layout.devices)

        head = QHBoxLayout()
        head.addWidget(status_square(not unsure))
        head.addSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        if not unsure:
            title = f"Alle {count} Geräte sind synchronisiert"
        else:
            title = f"{count - len(unsure)} von {count} Geräten sicher synchronisiert"
        titles.addWidget(label(title, "headline"))
        titles.addWidget(label(self._reference_text(), "hint"))
        head.addLayout(titles)
        head.addStretch()
        other = button("Andere Referenz wählen …")
        other.clicked.connect(self.other_reference.emit)
        head.addWidget(other, 0, Qt.AlignmentFlag.AlignTop)
        self.column.addLayout(head)
        if unsure:
            self.column.addWidget(label(
                "Ohne sicheren Treffer: " + ", ".join(unsure)
                + ". Im nächsten Schritt anhören; Position und Drift können falsch sein.",
                "hint", wrap=True))  # fmt: skip

        frame, box = card()
        line = QHBoxLayout()
        line.addWidget(label("TIMELINE · NUR ANSEHEN", "section"))
        line.addStretch()
        box.addLayout(line)
        timeline = Timeline(self.tokens)
        all_lanes, total = lanes(project)
        timeline.set_lanes(all_lanes, total)
        box.addWidget(timeline)
        self.column.addWidget(frame)

        toggle = button("Messdetails ausblenden" if self.details_open else "Messdetails anzeigen")
        toggle.clicked.connect(self.toggle_details)
        self.column.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        if self.details_open:
            self.column.addWidget(self._details(by_device))
        self.column.addStretch()
        self.changed.emit()

    def _reference_text(self) -> str:
        layout = self.project.layout
        ref = layout.devices[layout.reference]
        multi = any(len(c.tracks) > 1 for c in ref.clips)
        if multi:
            names = ", ".join(ref.track_label(t) for t in layout.tracks)
            return f"Referenz: {ref.name} · Spuren {names}"
        return f"Referenz: {ref.name} · {layout.tracks[0].name}"

    def toggle_details(self) -> None:
        self.details_open = not self.details_open
        self.rebuild()

    def _details(self, by_device: dict[str, list[dict]]) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 10, 16, 10)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        heads = ["GERÄT / DATEI", "START", "DRIFT", "URTEIL", "HINWEIS"]
        for col, text in enumerate(heads):
            grid.addWidget(label(text, "colhead"), 0, col)
        grid.setColumnStretch(4, 1)
        layout = self.project.layout
        ref = layout.devices[layout.reference]
        row = 1
        for k, d in enumerate(layout.devices):
            if k == layout.reference:
                grid.addWidget(label(f"<b>{d.name}</b>"), row, 0)
                grid.addWidget(label("–", "mono"), row, 1)
                grid.addWidget(label("Referenz", "mono"), row, 2)
                grid.addWidget(badge("ref", "Referenz"), row, 3, LEFT_MIDDLE)
                grid.addWidget(label(self._reference_text().split(" · ", 1)[1], wrap=True), row, 4)
                row += 1
            for r in by_device.get(d.name, []):
                if d is ref and Path(r["file"]) in layout.tracks:
                    continue
                grid.addWidget(label(f"<b>{d.name}</b> · {Path(r['file']).name}"), row, 0)
                grid.addWidget(label(clock_ms(r["placement"]["position_s"]), "mono"), row, 1)
                grid.addWidget(label(ppm(r["drift_ppm"]), "mono"), row, 2)
                grid.addWidget(badge(texts.verdict(r)), row, 3, LEFT_MIDDLE)
                grid.addWidget(label(hint(r), wrap=True), row, 4)
                row += 1
        return frame

    @property
    def ready(self) -> bool:
        return self.project.synced_now


def hint(row: dict, links: bool = True) -> str:
    """The notes of a result row in German, plus how exactly a video lands; without the clip
    it was measured through when ``links`` is false (listening: the badge says enough)."""
    # the badge says "kein sicherer Treffer"; which desk track matched is detail for the log
    skip = ("no_reliable_match", "matched_track") + (() if links else ("linked_via",))
    parts = [texts.note(n) for n in row.get("notes", [])
             if isinstance(n, str) or n.get("code") not in skip]  # fmt: skip
    video = row.get("placement", {}).get("video_error_ms")
    if video is not None:
        parts.append(f"Video ±{video:.0f} ms")
    return " · ".join(parts)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().setParent(None)
        elif item.layout() is not None:
            _clear(item.layout())
