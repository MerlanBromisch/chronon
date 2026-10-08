"""Listening: waveforms, playback and the audition of step 2 (boards 05 and 10).

Excerpts come from ``audio.excerpt`` in this process (fast seeks; no child process needed);
overviews of whole tracks from ``chronon overview`` in a job, cached on disk."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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
    """A whole track's waveform the way editors draw it: one column per pixel, the peaks as a
    lighter outline around a solid core (the average level), a centre line and a cursor. A
    click moves the position (``seek`` 0..1). While the overview is computed it says so."""

    seek = Signal(float)

    def __init__(self, tokens: dict[str, str], height: int = 48):
        super().__init__()
        self.tokens = tokens
        self.peaks = np.zeros((0, 2), dtype=np.float32)
        self.cursor: float | None = None
        self.loading: float | None = None  # 0..1 while the overview is computed
        self._columns: tuple[int, np.ndarray, np.ndarray, np.ndarray] | None = None
        self.setMinimumHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_peaks(self, peaks: np.ndarray, cursor: float | None = None) -> None:
        """``peaks`` as (n, 2) min / max in -1..1 (or int8 overviews, scaled here)."""
        if peaks.dtype == np.int8:
            peaks = peaks.astype(np.float32) / 127
        self.peaks = peaks
        self.loading = None
        self._columns = None
        self.cursor = cursor
        self.update()

    def set_loading(self, fraction: float = 0.0) -> None:
        self.peaks = np.zeros((0, 2), dtype=np.float32)
        self.loading = fraction
        self._columns = None
        self.update()

    def set_cursor(self, cursor: float | None) -> None:
        self.cursor = cursor
        self.update()

    def mousePressEvent(self, event):  # noqa: N802 (Qt API)
        if self.width() > 0 and len(self.peaks):
            self.seek.emit(min(max(event.position().x() / self.width(), 0.0), 1.0))

    def mouseMoveEvent(self, event):  # noqa: N802 (dragging scrubs)
        self.mousePressEvent(event)

    def _columns_for(self, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per column: lowest and highest peak, and the average level (the core)."""
        if self._columns is None or self._columns[0] != n:
            lo, hi, core = columns(self.peaks, n)
            self._columns = (n, lo, hi, core)
        return self._columns[1], self._columns[2], self._columns[3]

    def paintEvent(self, event):  # noqa: N802
        t = self.tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QColor(t["card_border"]))
        p.setBrush(QColor(t["input"]))
        p.drawRoundedRect(rect, 4, 4)
        inner = rect.adjusted(1, 4, -1, -4)
        mid = inner.center().y()
        p.setPen(QPen(QColor(t["divider"]), 1))
        p.drawLine(QPointF(inner.left(), mid), QPointF(inner.right(), mid))
        if self.loading is not None:
            p.setPen(QColor(t["text2"]))
            share = f" {round(100 * self.loading)} %" if self.loading > 0 else ""
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"Wellenform wird berechnet …{share}")
            if self.loading > 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t["accent"]))
                p.drawRect(QRectF(rect.left() + 1, rect.bottom() - 3,
                                  (rect.width() - 2) * self.loading, 2))  # fmt: skip
        elif len(self.peaks):
            n = max(int(inner.width()), 1)
            lo, hi, core = self._columns_for(n)
            half = inner.height() / 2
            x = inner.left() + np.arange(n) + 0.5
            outline = QPolygonF(
                [QPointF(a, mid - half * b) for a, b in zip(x, hi, strict=True)]
                + [QPointF(a, mid - half * b) for a, b in zip(x[::-1], lo[::-1], strict=True)]
            )
            wave = QColor(t["wave"])
            light = QColor(wave)
            light.setAlphaF(0.45)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(light)
            p.drawPolygon(outline)
            body = QPolygonF(
                [QPointF(a, mid - half * c) for a, c in zip(x, core, strict=True)]
                + [QPointF(a, mid + half * c) for a, c in zip(x[::-1], core[::-1], strict=True)]
            )
            p.setBrush(wave)
            p.drawPolygon(body)
        if self.cursor is not None and self.loading is None:
            cx = inner.left() + inner.width() * self.cursor
            p.setPen(QPen(QColor(t["accent"]), 1.5))
            p.drawLine(QPointF(cx, rect.top() + 1), QPointF(cx, rect.bottom() - 1))
        p.end()


DB_RANGE = 40.0  # the waveform's height covers this many dB below the loudest


def columns(peaks: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Peaks (float, -1..1) squeezed or stretched into ``n`` columns: lowest and highest peak
    per column and the core (the mean level within it), on a dB scale like editors draw it
    (quiet passages stay visible; a few loud spikes do not flatten the rest)."""
    if not len(peaks):
        z = np.zeros(n, dtype=np.float32)
        return z, z, z
    level = np.maximum(-peaks[:, 0], peaks[:, 1])
    if len(peaks) >= n:
        starts = (np.arange(n) * len(peaks)) // n
        lo = np.minimum.reduceat(peaks[:, 0], starts)
        hi = np.maximum.reduceat(peaks[:, 1], starts)
        core = np.add.reduceat(level, starts) / np.diff(np.append(starts, len(peaks)))
    else:  # fewer peaks than pixels: each covers several columns
        pick = (np.arange(n) * len(peaks)) // n
        lo, hi, core = peaks[pick, 0], peaks[pick, 1], level[pick]
    top = max(float(np.percentile(level, 99.9)), 1e-4)

    def scale(x: np.ndarray) -> np.ndarray:
        db = 20 * np.log10(np.maximum(np.abs(x) / top, 1e-9))
        return np.sign(x) * np.clip(1 + db / DB_RANGE, 0.0, 1.0)

    lo, hi, core = scale(lo), scale(hi), scale(core)
    return lo, hi, np.minimum(core, np.minimum(hi, -lo))


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
    in a job (a long track or a video takes a while). At most ``RUNNING`` jobs at once: what
    is shown now first, files computed ahead (``prefetch``) when nothing else waits; those go
    to the disk cache only, not into memory."""

    ready = Signal(object, object)  # path, peaks (int8 array)
    progress = Signal(object, float)  # path, share computed

    RUNNING = 2

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.memory: dict[Path, np.ndarray] = {}
        self.jobs: dict[Path, Job] = {}
        self.wanted: list[Path] = []  # waiting to be computed for the screen, newest first
        self.ahead: list[Path] = []  # waiting to be computed ahead
        self.asked: set[Path] = set()  # shown once computed

    def _cached(self, path: Path) -> Path | None:
        try:
            return listen.overview_path(path, settings.cache_dir())
        except OSError:
            return None

    def get(self, path: Path) -> np.ndarray | None:
        """The overview if known (or cached on disk); else compute it, before anything else."""
        if path in self.memory:
            return self.memory[path]
        cached = self._cached(path)
        if cached is None:
            return None
        if cached.exists():
            try:
                self.memory[path] = np.load(cached)
                return self.memory[path]
            except (OSError, ValueError):
                pass
        self.asked.add(path)
        if path in self.ahead:
            self.ahead.remove(path)
        if path not in self.jobs:
            if path in self.wanted:
                self.wanted.remove(path)
            self.wanted.insert(0, path)
            self._next()
        return None

    def prefetch(self, paths: list[Path]) -> None:
        """Compute these when nothing else waits, so they show at once later."""
        for path in paths:
            cached = self._cached(path)
            known = path in self.memory or path in self.ahead or path in self.jobs
            if cached is not None and not cached.exists() and not known:
                self.ahead.append(path)
        self._next()

    def _next(self) -> None:
        while len(self.jobs) < self.RUNNING and (self.wanted or self.ahead):
            path = (self.wanted or self.ahead).pop(0)
            if path in self.jobs:
                continue
            job = Job(["overview", str(path), "--cache", str(settings.cache_dir())], parent=self)
            job.result.connect(lambda e, p=path: self._done(p, e))
            job.progress.connect(lambda e, p=path: self.progress.emit(p, e.get("done", 0.0)))
            job.failed.connect(lambda e, p=path: self._failed(p))
            self.jobs[path] = job
            job.start()

    def _done(self, path: Path, event: dict) -> None:
        self.jobs.pop(path, None)
        if path in self.asked:  # computed ahead only: it waits in the disk cache
            self.asked.discard(path)
            self.memory[path] = np.load(event["files"][0]["overview"])
            self.ready.emit(path, self.memory[path])
        self._next()

    def _failed(self, path: Path) -> None:
        self.jobs.pop(path, None)
        self.asked.discard(path)
        self._next()

    def stop(self) -> None:
        self.wanted.clear()
        self.ahead.clear()
        self.asked.clear()
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
    """'Vorhören': one track's whole waveform (click to move), play from a position, jump
    (board 05; no slider: the waveform is the overview)."""

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
        self.wave = Waveform(tokens, 56)
        self.wave.seek.connect(lambda f: self.move_to(f * self.duration))
        self.play = _button("▶  Play", "primary")
        self.play.clicked.connect(self.toggle)
        self.time = QLabel("0:00:00")
        self.time.setObjectName("bigtime")
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
        controls.addStretch()
        controls.addWidget(self.total)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        box.addLayout(head)
        box.addWidget(self.wave)
        box.addLayout(controls)
        overviews.ready.connect(self._overview)
        overviews.progress.connect(
            lambda path, share: path == self.path and self.wave.set_loading(share)
        )
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
        if peaks is None:
            self.wave.set_loading()
        else:
            self.wave.set_peaks(peaks)
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
