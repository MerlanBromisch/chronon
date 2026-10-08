"""Step 4, Ergebnis: how it went, a view-only timeline, measurement details (boards 08, 09)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from chronon.gui import fmt, texts, theme
from chronon.gui.audition import Overviews, columns
from chronon.gui.project import Project
from chronon.gui.widgets import badge, button, card, label, status_square

LEFT_MIDDLE = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
STRIP = 18.0  # a timeline region's name strip


def ppm(value: float) -> str:
    return f"{value:+.2f} ppm".replace("-", "−").replace(".", ",")


def clock_ms(seconds: float) -> str:
    """``0:00:03.120`` (timeline position)."""
    sign = "−" if seconds < 0 else ""
    ms = round(abs(seconds) * 1000)
    s, ms = divmod(ms, 1000)
    return f"{sign}{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}.{ms:03d}"


@dataclass
class Region:
    """A clip on the timeline: where it sits, its file (the track its waveform shows), its
    name, its verdict."""

    start: float
    length: float
    path: Path
    verdict: str  # ref, ok, wanders, unsure
    drift_ppm: float | None = None
    name: str = ""


@dataclass
class Lane:
    name: str
    regions: list[Region]
    reference: bool


def lanes(project: Project) -> tuple[list[Lane], float]:
    """Each device's clips on the timeline of the originals (from the analysis result), and
    the timeline's length. The reference device starts where the result's zero puts it."""
    layout, result = project.layout, project.result or {}
    rows = {Path(r["file"]): r for r in result.get("files", [])}
    zero = min([0.0, *(r["offset_s"] for r in rows.values())])
    infos = project.infos
    out, end = [], 0.0
    for k, d in enumerate(layout.devices):
        regions = []
        for clip in d.clips:
            f = clip.tracks[0]
            r = rows.get(f)
            # parallel tracks: named by their count, drawn with the track that was measured
            # (the reference: its first chosen track)
            many = len(clip.tracks) > 1
            name = f"{len(clip.tracks)} Spuren" if many else f.name
            chosen = [t for t in clip.tracks if t in layout.tracks]
            if r is not None:
                start, length = r["placement"]["position_s"], r["placement"]["duration_s"]
                verdict = "ref" if r.get("is_reference") else texts.verdict(r)
                if k == layout.reference and verdict == "ok":
                    verdict = "ref"  # the whole device is the reference, each clip for its time
                shown = Path(chosen[0] if chosen else r.get("via") or f)
                regions.append(Region(start, length, shown, verdict, r.get("drift_ppm"), name))
            elif f in infos:  # a reference track: on the reference clock
                start, length = -zero, infos[f].duration_s
                regions.append(Region(start, length, chosen[0] if chosen else f, "ref", None, name))
            else:
                continue
            end = max(end, start + length)
        out.append(Lane(d.name, regions, k == layout.reference))
    return out, end


class Timeline(QWidget):
    """The devices' clips the way an editor shows them (board 08, redrawn): a ruler with
    major and minor ticks, a lane per device, each clip a region with its file name in a
    header strip and its waveform inside (once the overview is computed). View only; a
    tooltip tells a region's file, span and drift."""

    RULER = 26
    LANE = 58
    LEFT = 150

    def __init__(self, tokens: dict[str, str], overviews: Overviews | None = None):
        super().__init__()
        self.tokens = tokens
        self.overviews = overviews
        self.lanes: list[Lane] = []
        self.total = 0.0
        self.waves: dict[tuple[Path, int], np.ndarray] = {}  # (file, width) -> columns
        self.setMouseTracking(True)
        if overviews is not None:
            overviews.stored.connect(self._stored)

    def set_lanes(self, lanes: list[Lane], total: float) -> None:
        self.lanes, self.total = lanes, total
        self.setFixedHeight(self.RULER + self.LANE * len(lanes) + 1)
        self.update()

    def _stored(self, path: Path) -> None:
        if any(r.path == path for lane in self.lanes for r in lane.regions):
            self.update()

    # --- geometry -----------------------------------------------------------------------
    def _x(self, seconds: float) -> float:
        width = self.width() - self.LEFT
        return self.LEFT + width * seconds / self.total if self.total else self.LEFT

    def _region_rect(self, k: int, r: Region) -> QRectF:
        top = self.RULER + k * self.LANE
        x0 = self._x(max(r.start, 0.0))
        x1 = max(self._x(r.start + r.length), x0 + 3)
        return QRectF(x0 + 0.5, top + 5, x1 - x0 - 1, self.LANE - 10)

    def _region_at(self, x: float, y: float) -> Region | None:
        for k, lane in enumerate(self.lanes):
            for r in lane.regions:
                if self._region_rect(k, r).contains(x, y):
                    return r
        return None

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt API)
        r = self._region_at(event.position().x(), event.position().y())
        if r is None:
            QToolTip.hideText()
            return
        title = f"{r.name} · {r.path.name}" if r.name and r.name != r.path.name else r.path.name
        tip = f"{title}\n{fmt.duration(max(r.start, 0))} – {fmt.duration(r.start + r.length)}"
        if r.verdict != "ref":
            tip += f"\n{texts.VERDICTS.get(r.verdict, '')}"
            if r.drift_ppm is not None:
                tip += f" · Drift {ppm(r.drift_ppm)}"
        else:
            tip += "\nReferenz"
        QToolTip.showText(event.globalPosition().toPoint(), tip, self)

    # --- drawing ------------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        t = self.tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.total <= 0 or self.width() <= self.LEFT:
            p.end()
            return
        self._ruler(p)
        name_font = QFont(self.font())
        name_font.setPixelSize(13)
        for k, lane in enumerate(self.lanes):
            top = self.RULER + k * self.LANE
            if k % 2:
                p.fillRect(QRectF(0, top, self.width(), self.LANE), QColor(t["table_header"]))
            p.setPen(QColor(t["divider"]))
            p.drawLine(QPointF(0, top + self.LANE), QPointF(self.width(), top + self.LANE))
            name_font.setBold(lane.reference)
            p.setFont(name_font)
            p.setPen(QColor(t["text"]))
            p.drawText(QRectF(10, top, self.LEFT - 20, self.LANE),
                       Qt.AlignmentFlag.AlignVCenter, lane.name)  # fmt: skip
            for r in lane.regions:
                self._region(p, self._region_rect(k, r), r)
        p.setPen(QColor(t["divider"]))
        p.drawLine(QPointF(self.LEFT, 0), QPointF(self.LEFT, self.height()))
        p.end()

    def _ruler(self, p: QPainter) -> None:
        t = self.tokens
        p.fillRect(QRectF(self.LEFT, 0, self.width() - self.LEFT, self.RULER),
                   QColor(t["table_header"]))  # fmt: skip
        p.setPen(QColor(t["divider"]))
        p.drawLine(QPointF(0, self.RULER), QPointF(self.width(), self.RULER))
        width = self.width() - self.LEFT
        major, minor = ruler_steps(self.total, width)
        p.setFont(theme.mono_font(10))
        count = int(self.total // minor) + 1
        for n in range(count):
            x_s = n * minor
            px = self._x(x_s)
            is_major = n % round(major / minor) == 0
            p.setPen(QColor(t["text3"] if not is_major else t["text2"]))
            h = 9 if is_major else 4
            p.drawLine(QPointF(px, self.RULER - h), QPointF(px, self.RULER))
            if is_major and px + 60 < self.width():
                p.setPen(QColor(t["text2"]))
                p.drawText(QRectF(px + 3, 2, 80, self.RULER - 10),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           ruler_label(x_s, self.total))  # fmt: skip

    def _region(self, p: QPainter, rect: QRectF, r: Region) -> None:
        t = self.tokens
        color = QColor(t["accent"] if r.verdict == "ref" else t["region"])
        body = QColor(color)
        body.setAlphaF(0.22)
        p.setPen(
            QPen(color, 1, Qt.PenStyle.DashLine if r.verdict == "unsure" else Qt.PenStyle.SolidLine)
        )
        p.setBrush(body)
        p.drawRoundedRect(rect, 3, 3)
        strip = QRectF(rect.left(), rect.top(), rect.width(), min(STRIP, rect.height()))
        if rect.width() > 6:
            # the strip: rounded at the top, square below (both shapes filled, not cancelled)
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.WindingFill)
            path.addRoundedRect(strip, 3, 3)
            path.addRect(strip.adjusted(0, 4, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPath(path)
            label_font = QFont(self.font())
            label_font.setPixelSize(11)
            label_font.setWeight(QFont.Weight.DemiBold)
            p.setFont(label_font)
            p.setPen(QColor(t["on_accent"]))
            mark = {"wanders": "⚠ ", "unsure": "? "}.get(r.verdict, "")
            text = p.fontMetrics().elidedText(mark + (r.name or r.path.name),
                                               Qt.TextElideMode.ElideRight,
                                               int(strip.width() - 12))  # fmt: skip
            p.drawText(strip.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, text)
        wave_box = QRectF(rect.left() + 1, strip.bottom() + 2, rect.width() - 2,
                          rect.bottom() - strip.bottom() - 4)  # fmt: skip
        n = int(wave_box.width())
        if n < 4 or wave_box.height() < 6 or self.overviews is None:
            return
        cols = self._wave(r.path, n)
        if cols is None:
            return
        lo, hi = cols
        mid, half = wave_box.center().y(), wave_box.height() / 2
        xs = wave_box.left() + np.arange(n) + 0.5
        shape = QPolygonF(
            [QPointF(x, mid - half * v) for x, v in zip(xs, hi, strict=True)]
            + [QPointF(x, mid - half * v) for x, v in zip(xs[::-1], lo[::-1], strict=True)]
        )
        p.setPen(Qt.PenStyle.NoPen)
        wave = QColor(color)
        wave.setAlphaF(0.75)
        p.setBrush(wave)
        p.drawPolygon(shape)

    def _wave(self, path: Path, n: int):
        key = (path, n)
        if key not in self.waves:
            assert self.overviews is not None
            peaks = self.overviews.peek(path)
            if peaks is None:
                return None
            self.waves[key] = columns(np.asarray(peaks, dtype=np.float32) / 127, n)
        return self.waves[key]


def ruler_steps(total: float, width: float) -> tuple[float, float]:
    """A labelled (major) and a tick (minor) step in seconds, labels at least ~90 px apart."""
    steps = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)
    minors = {1: 0.2, 2: 0.5, 5: 1, 10: 2, 15: 5, 30: 5, 60: 10, 120: 30, 300: 60, 600: 120,
              900: 300, 1800: 300, 3600: 600, 7200: 1800}  # fmt: skip
    for step in steps:
        if width * step / total >= 90:
            return step, minors[step]
    return 14400, 3600


def ruler_label(seconds: float, total: float) -> str:
    """``1:20:00`` on a long timeline, ``20:00`` / ``0:20`` on short ones, like an editor."""
    s = round(seconds)
    if total >= 3600:
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


class ResultPage(QWidget):
    changed = Signal()
    other_reference = Signal()
    compare_tracks = Signal()

    def __init__(
        self, project: Project, tokens: dict[str, str], overviews: Overviews | None = None
    ):
        super().__init__()
        self.project, self.tokens, self.overviews = project, tokens, overviews
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
        head.addWidget(status_square(not unsure), 0, Qt.AlignmentFlag.AlignTop)
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
        head.setAlignment(titles, Qt.AlignmentFlag.AlignTop)  # together, beside taller buttons
        head.addStretch()
        actions = QVBoxLayout()
        actions.setSpacing(6)
        ref = layout.devices[layout.reference]
        if unsure and ref.multitrack:  # what to try first: other tracks of the desk
            tracks = button("Andere Vergleichsspuren wählen …")
            tracks.clicked.connect(self.compare_tracks.emit)
            actions.addWidget(tracks)
        other = button("Andere Referenz wählen …")
        other.clicked.connect(self.other_reference.emit)
        actions.addWidget(other)
        head.addLayout(actions)
        head.setAlignment(actions, Qt.AlignmentFlag.AlignTop)
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
        timeline = Timeline(self.tokens, self.overviews)
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
            return f"Referenz: {ref.name} · Vergleichsspuren {names}"
        if len(ref.clips) > 1:
            return f"Referenz: {ref.name} · {len(ref.clips)} Aufnahmen"
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
                verdict = texts.verdict(r)
                tag = badge("ref", "Referenz") if d is ref and verdict == "ok" else badge(verdict)
                grid.addWidget(tag, row, 3, LEFT_MIDDLE)  # the whole device is the reference
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
