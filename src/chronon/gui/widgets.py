"""Building blocks of several steps: labels, cards, badges, banners, the step list."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
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


BADGE_ICONS = {"ok": "✓", "wanders": "⚠", "unsure": "○", "ref": "✓", "bad": "!"}


def badge(kind: str, text: str | None = None) -> QLabel:
    """A verdict badge (README "Badge"): ok / wanders / unsure / ref / bad."""
    text = text if text is not None else texts.VERDICTS.get(kind, kind)
    w = QLabel(f"{BADGE_ICONS.get(kind, '')}  {text}".strip())
    w.setObjectName(f"badge_{kind}")
    w.setSizePolicy(w.sizePolicy().horizontalPolicy(), w.sizePolicy().verticalPolicy())
    return w


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


def status_square(ok: bool) -> QLabel:
    w = QLabel("✓" if ok else "!")
    w.setObjectName("square_ok" if ok else "square_bad")
    w.setFixedSize(40, 40)
    w.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return w


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
            mark, name, state = label("", "stepmark"), label(texts.step(s)), label("", "mono")
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
        mark.setText("✓" if state == "done" else "")
        mark.setProperty("state", state)
        name.setProperty("state", state)
        for w in (mark, name):
            w.style().unpolish(w)
            w.style().polish(w)
        right.setText(text or {"running": "läuft", "cancelled": "abgebrochen"}.get(state, ""))
