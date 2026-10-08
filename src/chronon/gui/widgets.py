"""Building blocks of several steps: labels, cards, badges, banners, the step list."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QTransform,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chronon.gui import texts


def percent(fraction: float) -> int:
    """What the bar says while a job runs: never 100 % before its result is in."""
    return min(int(100 * max(fraction, 0.0)), 99)


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text: str, role: str = "") -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    return b


def card(margins: tuple[int, int, int, int] = (18, 16, 18, 16)) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(*margins)
    box.setSpacing(10)
    return frame, box


def rule() -> QFrame:
    line = QFrame()
    line.setObjectName("rule")
    line.setFixedHeight(1)
    return line


class Icon(QLabel):
    """A small drawn symbol in the widget's text colour (glyphs like ✓ depend on the font and
    looked like a root sign): check, warn, circle, bang, dot."""

    def __init__(self, shape: str, size: int = 13, margin: int = 0):
        super().__init__()
        self.shape, self.margin = shape, margin
        self.setFixedSize(size, size)

    def set_shape(self, shape: str) -> None:
        self.shape = shape
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt API)
        super().paintEvent(event)  # a frame from the style sheet, if any
        m = self.margin
        paint_icon(self, self.shape, QRectF(self.rect()).adjusted(m, m, -m, -m))


def paint_icon(widget: QWidget, shape: str, box: QRectF) -> None:
    if not shape:
        return
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = widget.palette().color(QPalette.ColorRole.WindowText)
    s = min(box.width(), box.height())
    box = QRectF(box.center().x() - s / 2, box.center().y() - s / 2, s, s)
    pen = QPen(color, max(1.4, s / 8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)  # fmt: skip
    p.setPen(pen)

    def at(x: float, y: float) -> QPointF:
        return QPointF(box.left() + x * s, box.top() + y * s)

    if shape == "check":
        path = QPainterPath(at(0.16, 0.54))
        path.lineTo(at(0.40, 0.76))
        path.lineTo(at(0.86, 0.26))
        p.drawPath(path)
    elif shape == "warn":
        path = QPainterPath(at(0.5, 0.10))
        path.lineTo(at(0.94, 0.88))
        path.lineTo(at(0.06, 0.88))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(at(0.5, 0.40), at(0.5, 0.60))
        p.drawPoint(at(0.5, 0.74))
    elif shape == "circle":
        p.drawEllipse(at(0.5, 0.5), 0.36 * s, 0.36 * s)
    elif shape == "bang":
        p.drawLine(at(0.5, 0.16), at(0.5, 0.60))
        p.drawPoint(at(0.5, 0.82))
    elif shape == "gear":  # eight teeth on a ring, a hole in the middle, filled
        gear = QPainterPath()
        gear.addEllipse(at(0.5, 0.5), 0.33 * s, 0.33 * s)
        for k in range(8):
            tooth = QPainterPath()
            tooth.addRect(QRectF(-0.085 * s, -0.48 * s, 0.17 * s, 0.2 * s))
            turn = QTransform().translate(box.center().x(), box.center().y()).rotate(45 * k)
            gear = gear.united(turn.map(tooth))
        hole = QPainterPath()
        hole.addEllipse(at(0.5, 0.5), 0.13 * s, 0.13 * s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(gear.subtracted(hole))
    elif shape == "dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(at(0.5, 0.5), 0.2 * s, 0.2 * s)
    p.end()


BADGE_ICONS = {"ok": "check", "wanders": "warn", "unsure": "circle", "ref": "check", "bad": "bang"}
# badges of one kind of list share one size: the verdicts, and the export's outcome
BADGE_TEXTS = {
    "verdict": [*texts.VERDICTS.values(), "Referenz"],
    "export": [*texts.VERDICTS.values(), "Referenz", "geprüft", "platziert", "nicht geprüft",
               "Prüfung fehlgeschlagen"],
}  # fmt: skip


class Badge(QFrame):
    """A verdict badge (README "Badge"): ok / wanders / unsure / ref / bad, all of a group
    the same size whatever their text."""

    HEIGHT = 24

    def __init__(self, kind: str, text: str, group: str = "verdict"):
        super().__init__()
        self.setObjectName(f"badge_{kind}")
        self.text = QLabel(text)
        line = QHBoxLayout(self)
        line.setContentsMargins(8, 0, 8, 0)
        line.setSpacing(6)
        line.addStretch()
        line.addWidget(Icon(BADGE_ICONS.get(kind, "")), 0, Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(self.text, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addStretch()
        bold = QFont(self.font())
        bold.setPixelSize(12)
        bold.setWeight(QFont.Weight.DemiBold)
        widest = max(QFontMetrics(bold).horizontalAdvance(t) for t in BADGE_TEXTS[group] + [text])
        self.setFixedSize(widest + 13 + 6 + 2 * 8 + 4, self.HEIGHT)


def badge(kind: str, text: str | None = None, group: str = "verdict") -> Badge:
    """A verdict badge; ``text`` defaults to the verdict's word."""
    return Badge(kind, text if text is not None else texts.VERDICTS.get(kind, kind), group)


def banner(kind: str, title: str, text: str) -> QFrame:
    """A message across the page: amber (warn) or red (error) with an icon."""
    frame = QFrame()
    frame.setObjectName(f"banner_{kind}")
    line = QHBoxLayout(frame)
    line.setContentsMargins(16, 12, 16, 12)
    icon = label("⚠" if kind == "warn" else "!", f"bannericon_{kind}")
    body = label(f"<b>{title}</b> {text}", wrap=True)
    line.addWidget(icon)
    line.addWidget(body, 1)
    return frame


class _Square(QLabel):
    def __init__(self, ok: bool):
        super().__init__()
        self.shape = "check" if ok else "bang"

    def paintEvent(self, event):  # noqa: N802 (Qt API)
        super().paintEvent(event)  # the frame from the style sheet
        paint_icon(self, self.shape, QRectF(self.rect()).adjusted(11, 11, -11, -11))


def status_square(ok: bool) -> QLabel:
    w = _Square(ok)
    w.setObjectName("square_ok" if ok else "square_bad")
    w.setFixedSize(40, 40)
    return w


class _StepMark(QLabel):
    """The box in front of a step: dashed (waiting), a dot (running), a check (done)."""

    def paintEvent(self, event):  # noqa: N802 (Qt API)
        super().paintEvent(event)
        shape = {"done": "check", "running": "dot"}.get(self.property("state") or "", "")
        paint_icon(self, shape, QRectF(self.rect()).adjusted(5, 5, -5, -5))


class _Segment(QPushButton):
    """A segment wide enough for its text in bold, so checking it never clips the text."""

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        extra = QFontMetrics(bold).horizontalAdvance(
            self.text()
        ) - self.fontMetrics().horizontalAdvance(self.text())
        return QSize(hint.width() + max(extra, 0), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()


class Segmented(QWidget):
    """A segmented control (30 high, selected = accent)."""

    changed = Signal(int)

    def __init__(self, options: list[str], current: int = 0):
        super().__init__()
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(0)
        self.group = QButtonGroup(self)
        for k, text in enumerate(options):
            b = _Segment(text)
            b.setCheckable(True)
            b.setProperty("role", "segment")
            b.setProperty("edge", "first" if k == 0 else "last" if k == len(options) - 1 else "")
            self.group.addButton(b, k)
            line.addWidget(b)
        self.group.button(current).setChecked(True)
        self.group.idClicked.connect(self.changed.emit)

    @property
    def current(self) -> int:
        return self.group.checkedId()

    def set_current(self, k: int) -> None:
        self.group.button(k).setChecked(True)


class StepList(QFrame):
    """'ABLAUF': the plan's steps, each waiting, running, done (with its duration) or
    cancelled (board 06 / 07)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(18, 14, 18, 8)
        self.box.setSpacing(0)
        self.rows: dict[str, tuple[QLabel, QLabel, QLabel]] = {}

    def set_plan(self, steps: list[dict]) -> None:
        while self.box.count():
            item = self.box.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        self.rows.clear()
        self.box.addWidget(label("ABLAUF", "section"))
        for s in steps:
            row = QFrame()
            row.setObjectName("steprow")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 7, 0, 7)
            mark, name, state = _StepMark(), label(texts.step(s)), label("", "mono")
            mark.setObjectName("stepmark")
            mark.setFixedSize(24, 24)
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line.addWidget(mark)
            line.addSpacing(8)
            line.addWidget(name, 1)
            line.addWidget(state)
            self.box.addWidget(row)
            self.rows[s["id"]] = (mark, name, state)
            self.set_state(s["id"], "waiting")

    def set_state(self, step: str, state: str, text: str = "") -> None:
        if step not in self.rows:
            return
        mark, name, right = self.rows[step]
        mark.setProperty("state", state)
        name.setProperty("state", state)
        for w in (mark, name):
            w.style().unpolish(w)
            w.style().polish(w)
        right.setText(text or {"running": "läuft", "cancelled": "abgebrochen"}.get(state, ""))
