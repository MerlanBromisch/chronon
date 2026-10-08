"""Listening: waveforms, playback and the audition of step 2 (boards 05 and 10).

Excerpts come from ``audio.excerpt`` in this process (fast seeks; no child process needed);
overviews of whole tracks from ``chronon overview`` in a job, cached on disk."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from chronon import audio, listen
from chronon.gui import fmt, settings
from chronon.gui.jobs import Job

RATE = listen.LISTEN_RATE
FRAME_BYTES = 2 * 4  # stereo float32
FIRST_S = 1.0  # decoded before playback starts
CHUNK_S = 4.0  # then decoded ahead in pieces of this length
AHEAD_BYTES = int(12 * RATE) * FRAME_BYTES  # keep about this much decoded ahead
SINK_BUFFER_S = 0.5
log = logging.getLogger(__name__)

Source = Callable[[float, float], np.ndarray]  # (start_s, seconds) -> (frames, 2) float32


class Waveform(QWidget):
    """Bars of min/max peaks, a cursor; a click moves the position (``seek`` 0..1)."""

    seek = Signal(float)

    BAR, GAP = 9, 3

    def __init__(self, tokens: dict[str, str], height: int = 40):
        super().__init__()
        self.tokens = tokens
        self.peaks = np.zeros((0, 2), dtype=np.float32)
        self.cursor: float | None = None
        self.setMinimumHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_peaks(self, peaks: np.ndarray, cursor: float | None = None) -> None:
        """``peaks`` as (n, 2) floats in -1..1 (or int8 overviews, scaled here)."""
        if peaks.dtype == np.int8:
            peaks = peaks.astype(np.float32) / 127
        self.peaks = peaks
        self.cursor = cursor
        self.update()

    def set_samples(self, x: np.ndarray, cursor: float | None = None) -> None:
        n = max(len(x) // 400, 1)
        usable = x[: len(x) // n * n].reshape(-1, n) if len(x) >= n else np.zeros((1, 1))
        self.set_peaks(np.column_stack([usable.min(axis=1), usable.max(axis=1)]), cursor)

    def set_cursor(self, cursor: float | None) -> None:
        self.cursor = cursor
        self.update()

    def mousePressEvent(self, event):  # noqa: N802 (Qt API)
        if self.width() > 0:
            self.seek.emit(min(max(event.position().x() / self.width(), 0.0), 1.0))

    def paintEvent(self, event):  # noqa: N802
        t = self.tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QColor(t["card_border"]))
        p.setBrush(QColor(t["input"]))
        p.drawRoundedRect(rect, 4, 4)
        inner = rect.adjusted(6, 5, -6, -5)
        bars = max(int(inner.width() // (self.BAR + self.GAP)), 1)
        if len(self.peaks):
            level = _levels(self.peaks, bars)
            top = max(float(level.max()), 1e-3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["wave"]))
            step = inner.width() / len(level)
            for k, v in enumerate(level):
                h = max(2.0, inner.height() * float(v) / top)
                x = inner.left() + k * step
                p.drawRect(QRectF(x, inner.center().y() - h / 2, step - self.GAP, h))
        if self.cursor is not None:
            x = inner.left() + inner.width() * self.cursor
            p.setPen(QColor(t["accent"]))
            p.drawLine(int(x), int(rect.top()) + 1, int(x), int(rect.bottom()) - 1)
        p.end()


def _levels(peaks: np.ndarray, n: int) -> np.ndarray:
    """The loudest peak of each of ``n`` equal parts."""
    level = np.abs(peaks).max(axis=1)
    if len(level) <= n:
        return level
    starts = (np.arange(n) * len(level)) // n
    return np.maximum.reduceat(level, starts)


class _Stream(QIODevice):
    """Audio for the sink, decoded ahead in a thread: the sink pulls from one continuous
    stream (no restart, no gap every few seconds), and slow decoding (a video on a USB drive)
    or a busy window only cost a dropout when the buffer runs dry."""

    def __init__(self, source: Source, start_s: float, end_s: float, parent: QObject):
        super().__init__(parent)
        self.source, self.start_s, self.end_s = source, start_s, end_s
        self.data = bytearray()
        self.lock = threading.Lock()
        self.room = threading.Condition(self.lock)
        self.finished = False  # everything up to end_s is decoded
        self.starved = 0  # times decoding fell behind (each a moment of silence)
        self.stopped = False
        self.thread = threading.Thread(target=self._decode, daemon=True)

    def begin(self) -> None:
        # the first second synchronously: playback starts at once and with sound
        self._add(self.start_s, min(FIRST_S, self.end_s - self.start_s))
        self.open(QIODevice.OpenModeFlag.ReadOnly)
        self.thread.start()

    def stop(self) -> None:
        with self.lock:
            self.stopped = True
            self.room.notify_all()
        self.close()

    def _add(self, start: float, seconds: float) -> bool:
        if seconds <= 0:
            return False
        try:
            x = np.ascontiguousarray(self.source(start, seconds), dtype="<f4")
        except Exception:  # noqa: BLE001 (a file gone or unreadable: end the stream)
            log.exception("playback: decoding at %.1f s failed", start)
            return False
        with self.lock:
            self.data += x.tobytes()
        return True

    def _decode(self) -> None:
        t = self.start_s + FIRST_S
        while True:
            with self.lock:
                while len(self.data) > AHEAD_BYTES and not self.stopped:
                    self.room.wait()
                if self.stopped:
                    return
            seconds = min(CHUNK_S, self.end_s - t)
            if not self._add(t, seconds):
                break
            t += seconds
        with self.lock:
            self.finished = True

    def isSequential(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def bytesAvailable(self) -> int:  # noqa: N802
        with self.lock:
            return len(self.data) + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:  # noqa: N802
        with self.lock:
            n = min(maxlen, len(self.data)) // FRAME_BYTES * FRAME_BYTES
            out = bytes(self.data[:n])
            del self.data[:n]
            self.room.notify_all()
            if n == 0 and not self.finished and maxlen >= FRAME_BYTES:
                # decoding fell behind: a moment of silence keeps the stream going
                self.starved += 1
                n = min(maxlen, FRAME_BYTES * RATE // 50) // FRAME_BYTES * FRAME_BYTES
                return bytes(n)
        return out

    def writeData(self, data) -> int:  # noqa: N802
        return -1


class Player(QObject):
    """Plays stereo float audio from a ``Source`` from a start time on, as one stream.
    ``position`` reports the time playing now; without an audio output it stays silent."""

    position = Signal(float)
    playing_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.sink: QAudioSink | None = None
        self.stream: _Stream | None = None
        self.start_s = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.format = QAudioFormat()
        self.format.setSampleRate(RATE)
        self.format.setChannelCount(2)
        self.format.setSampleFormat(QAudioFormat.SampleFormat.Float)

    @property
    def playing(self) -> bool:
        return self.sink is not None

    @staticmethod
    def available() -> bool:
        return not QMediaDevices.defaultAudioOutput().isNull()

    def play(self, source: Source, start_s: float, end_s: float = float("inf")) -> bool:
        self.stop()
        if not self.available() or end_s - start_s <= 0:
            return False
        self.start_s = start_s
        self.stream = _Stream(source, start_s, end_s, self)
        self.stream.begin()
        self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), self.format, self)
        self.sink.setBufferSize(int(SINK_BUFFER_S * RATE) * FRAME_BYTES)  # rides out a busy GUI
        self.sink.stateChanged.connect(self._state)
        self.sink.start(self.stream)
        self.timer.start()
        self.playing_changed.emit(True)
        return True

    def stop(self) -> None:
        was = self.sink is not None
        if self.sink is not None:
            self.sink.stateChanged.disconnect(self._state)
            self.sink.stop()
            self.sink.deleteLater()
        if self.stream is not None:
            if self.stream.starved:
                log.warning("playback: decoding fell behind %d times", self.stream.starved)
            self.stream.stop()
            self.stream.deleteLater()
        self.sink, self.stream = None, None
        self.timer.stop()
        if was:
            self.playing_changed.emit(False)

    def _state(self, state) -> None:
        # idle with nothing left to decode: the end was reached
        if state == QAudio.State.IdleState and self.stream is not None and self.stream.finished:
            if not self.stream.bytesAvailable():
                self.stop()

    def _tick(self) -> None:
        if self.sink is not None:
            self.position.emit(self.start_s + self.sink.processedUSecs() / 1e6)


class Overviews(QObject):
    """Whole-track overviews: from the disk cache, else computed by ``chronon overview``
    in a job (a long track or a video takes a while)."""

    ready = Signal(object, object)  # path, peaks (int8 array)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.memory: dict[Path, np.ndarray] = {}
        self.jobs: dict[Path, Job] = {}

    def get(self, path: Path) -> np.ndarray | None:
        """The overview if known (or cached on disk); else start computing it."""
        if path in self.memory:
            return self.memory[path]
        cache = settings.cache_dir()
        try:
            cached = listen.overview_path(path, cache)
        except OSError:
            return None
        if cached.exists():
            try:
                self.memory[path] = np.load(cached)
                return self.memory[path]
            except (OSError, ValueError):
                pass
        if path not in self.jobs:
            job = Job(["overview", str(path), "--cache", str(cache)], parent=self)
            job.result.connect(lambda e, p=path: self._done(p, e))
            job.failed.connect(lambda e, p=path: self.jobs.pop(p, None))
            self.jobs[path] = job
            job.start()
        return None

    def _done(self, path: Path, event: dict) -> None:
        self.jobs.pop(path, None)
        row = event["files"][0]
        self.memory[path] = np.load(row["overview"])
        self.ready.emit(path, self.memory[path])

    def stop(self) -> None:
        for job in list(self.jobs.values()):
            job.cancel()
            job.wait(5000)
        self.jobs.clear()


def _button(text: str, role: str = "") -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    return b


class Audition(QWidget):
    """'Vorhören': one track's overview, play from a position, jump, slider (board 05)."""

    def __init__(self, tokens: dict[str, str], overviews: Overviews, player: Player):
        super().__init__()
        self.tokens, self.overviews, self.player = tokens, overviews, player
        self.tracks: list[tuple[Path, str]] = []
        self.index = 0
        self.pos = 0.0
        self.duration = 0.0
        self.title = QLabel("")
        self.title.setObjectName("section")
        self.prev, self.next = _button("‹", "small"), _button("›", "small")
        self.prev.clicked.connect(lambda: self.show_track(self.index - 1))
        self.next.clicked.connect(lambda: self.show_track(self.index + 1))
        self.wave = Waveform(tokens, 40)
        self.wave.seek.connect(lambda f: self.move_to(f * self.duration))
        self.play = _button("▶  Play", "primary")
        self.play.clicked.connect(self.toggle)
        self.time = QLabel("0:00:00")
        self.time.setObjectName("bigtime")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(lambda v: self.move_to(v / 1000 * self.duration))
        self.total = QLabel("")
        self.total.setObjectName("mono")
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(self.prev)
        head.addWidget(self.next)
        controls = QHBoxLayout()
        controls.addWidget(self.play)
        for text, delta in (("−1 min", -60), ("−10 s", -10)):
            b = _button(text)
            b.clicked.connect(lambda _c=False, d=delta: self.move_to(self.pos + d))
            controls.addWidget(b)
        controls.addWidget(self.time)
        for text, delta in (("+10 s", 10), ("+1 min", 60)):
            b = _button(text)
            b.clicked.connect(lambda _c=False, d=delta: self.move_to(self.pos + d))
            controls.addWidget(b)
        controls.addSpacing(12)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.total)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        box.addLayout(head)
        box.addWidget(self.wave)
        box.addLayout(controls)
        overviews.ready.connect(self._overview)
        player.position.connect(self._playing_at)
        player.playing_changed.connect(
            lambda on: self.play.setText("❚❚  Pause" if on else "▶  Play")
        )

    def set_tracks(self, tracks: list[tuple[Path, str]], durations: dict[Path, float]) -> None:
        """The tracks to step through (path, label), e.g. the reference device's."""
        current = self.tracks[self.index][0] if self.tracks else None
        self.tracks, self.durations = tracks, durations
        paths = [p for p, _ in tracks]
        self.show_track(paths.index(current) if current in paths else 0, keep_position=True)

    def show_track(self, k: int, keep_position: bool = False) -> None:
        if not self.tracks:
            return
        self.player.stop()
        self.index = k % len(self.tracks)
        path, label = self.tracks[self.index]
        name = f"SPUR {label}" if label.isdigit() else f"„{label.upper()}“"
        self.title.setText(
            f"VORHÖREN · {name} · {self.index + 1} VON {len(self.tracks)}"
            if len(self.tracks) > 1 else f"VORHÖREN · {label.upper()}"
        )  # fmt: skip
        self.prev.setVisible(len(self.tracks) > 1)
        self.next.setVisible(len(self.tracks) > 1)
        self.duration = self.durations.get(path, 0.0)
        self.total.setText(fmt.duration(self.duration))
        if not keep_position:
            self.pos = min(self.pos, self.duration)
        peaks = self.overviews.get(path)
        self.wave.set_peaks(peaks if peaks is not None else np.zeros((0, 2), np.float32))
        self._show_position()

    @property
    def path(self) -> Path | None:
        return self.tracks[self.index][0] if self.tracks else None

    def _overview(self, path: Path, peaks: np.ndarray) -> None:
        if path == self.path:
            self.wave.set_peaks(peaks, self._fraction())

    def _fraction(self) -> float | None:
        return self.pos / self.duration if self.duration else None

    def move_to(self, seconds: float) -> None:
        self.pos = min(max(seconds, 0.0), self.duration)
        self._show_position()
        if self.player.playing:
            self.start()

    def _show_position(self) -> None:
        self.time.setText(fmt.duration(self.pos))
        if not self.slider.isSliderDown():
            self.slider.setValue(round(1000 * (self._fraction() or 0)))
        self.wave.set_cursor(self._fraction())

    def toggle(self) -> None:
        if self.player.playing:
            self.player.stop()
        else:
            self.start()

    def start(self) -> None:
        path = self.path
        if path is None:
            return

        def source(start: float, seconds: float) -> np.ndarray:
            x = audio.excerpt(path, start, seconds, RATE)
            return np.column_stack([x, x])

        if not self.player.play(source, self.pos, self.duration):
            self.play.setText("Keine Audioausgabe")

    def _playing_at(self, seconds: float) -> None:
        if self.isVisible():
            self.pos = seconds
            self._show_position()
