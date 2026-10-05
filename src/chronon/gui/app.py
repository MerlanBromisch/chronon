"""Packaging spike: the smallest window that exercises what the real app needs.

Drop files, tick the reference track(s), analyse in a child process (JSON lines), show the
result, listen to reference (left) and file (right) at the middle of their overlap.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QProcess, Qt, QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chronon import audio

LISTEN_RATE = 48000
LISTEN_S = 5.0
COLUMNS = ["File", "Device", "Offset s", "Drift ppm", "Confidence", "Reliable"]


def worker_command(args: list[str]) -> tuple[str, list[str]]:
    """Program and arguments that run the analysis worker, frozen or from source."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--worker", *args]
    return sys.executable, ["-m", "chronon.gui", "--worker", *args]


class DropList(QListWidget):
    """Files dropped here; a ticked file is a reference track."""

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event):  # noqa: N802 (Qt API)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    dragMoveEvent = dragEnterEvent  # noqa: N815

    def dropEvent(self, event):  # noqa: N802
        self.add([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def add(self, paths: list[str]) -> None:
        known = {self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())}
        for p in paths:
            if Path(p).is_dir():
                self.add(sorted(str(f) for f in Path(p).iterdir() if f.is_file()))
                continue
            if p in known or Path(p).suffix.lower() == ".json":
                continue
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            first = self.count() == 0
            item.setCheckState(Qt.CheckState.Checked if first else Qt.CheckState.Unchecked)
            self.addItem(item)

    def paths(self) -> tuple[list[str], list[str]]:
        refs, files = [], []
        for i in range(self.count()):
            item = self.item(i)
            p = item.data(Qt.ItemDataRole.UserRole)
            (refs if item.checkState() == Qt.CheckState.Checked else files).append(p)
        return refs, files


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chronon (packaging spike)")
        self.resize(900, 600)
        self.proc: QProcess | None = None
        self.rows: list[dict] = []
        self.sink: QAudioSink | None = None
        self.buffer: QBuffer | None = None
        self.on_result = None  # self test hook

        self.files = DropList()
        self.analyse = QPushButton("Analyse")
        self.cancel = QPushButton("Cancel")
        self.cancel.setEnabled(False)
        self.listen = QPushButton("Listen (ref left, file right)")
        self.listen.setEnabled(False)
        self.bar = QProgressBar()
        self.status = QLabel("Drop files; tick the reference track(s).")
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        buttons = QHBoxLayout()
        for w in (self.analyse, self.cancel, self.listen):
            buttons.addWidget(w)
        buttons.addStretch()
        layout = QVBoxLayout()
        layout.addWidget(self.files, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 2)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.analyse.clicked.connect(self.start)
        self.cancel.clicked.connect(self.stop)
        self.listen.clicked.connect(self.play)
        self.table.itemSelectionChanged.connect(
            lambda: self.listen.setEnabled(bool(self.table.selectedItems()))
        )

    # analysis -----------------------------------------------------------------------
    def start(self) -> None:
        refs, files = self.files.paths()
        if not refs or not files:
            self.status.setText("Tick at least one reference and leave at least one file.")
            return
        args = [a for r in refs for a in ("-r", r)] + files
        program, arguments = worker_command(args)
        self.proc = QProcess(self)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(self._log)
        self.proc.finished.connect(self._finished)
        self.proc.start(program, arguments)
        self.analyse.setEnabled(False)
        self.cancel.setEnabled(True)
        self.bar.setValue(0)
        self.status.setText("Starting…")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.status.setText("Cancelled.")

    def _log(self) -> None:
        if self.proc is not None:
            sys.stderr.write(bytes(self.proc.readAllStandardError()).decode(errors="replace"))

    def _read(self) -> None:
        while self.proc is not None and self.proc.canReadLine():
            line = bytes(self.proc.readLine()).decode(errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._event(event)

    def _event(self, e: dict) -> None:
        if e["event"] == "progress":
            self.bar.setValue(round(100 * e["done"]))
            left = f" — about {e['eta_s']:.0f} s left" if e.get("eta_s") else ""
            self.status.setText(f"{e['step']}{left}")
        elif e["event"] == "error":
            self.status.setText(f"Error: {e['message']}")
        elif e["event"] == "result":
            self.rows = e["files"]
            self._fill()
            self.status.setText("Done.")
            if self.on_result:
                self.on_result(self.rows)

    def _finished(self, code: int, _status) -> None:
        self.analyse.setEnabled(True)
        self.cancel.setEnabled(False)
        if code != 0 and not self.status.text().startswith(("Error", "Cancelled")):
            self.status.setText(f"Worker stopped (exit code {code}).")
        if code != 0 and self.on_result:
            self.on_result(None)

    def _fill(self) -> None:
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            values = [
                Path(r["file"]).name,
                r["device"],
                f"{r['offset_s']:.6f}",
                f"{r['drift_ppm']:+.2f}",
                f"{100 * r['confidence']:.0f} %",
                "yes" if r["reliable"] else "NO",
            ]
            for j, v in enumerate(values):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()

    # listening ----------------------------------------------------------------------
    def play(self) -> None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return
        try:
            stereo = listen_excerpt(self.rows[min(rows)])
        except audio.AudioError as err:
            self.status.setText(f"Cannot listen: {err}")
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(LISTEN_RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self.status.setText("No audio output.")
            return
        if self.sink is not None:
            self.sink.stop()
        self.buffer = QBuffer()
        self.buffer.setData(QByteArray(stereo.astype("<f4").tobytes()))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.sink = QAudioSink(device, fmt, self)
        self.sink.start(self.buffer)
        self.status.setText("Playing: reference left, file right.")


def listen_excerpt(row: dict) -> np.ndarray:
    """LISTEN_S seconds of reference (left) and file (right), interleaved, at the middle
    of the part of the file the reference also covers."""
    ref, path = row["reference"], row["file"]
    offset, ppm = row["offset_s"], row["drift_ppm"]
    ref_len = audio.probe(ref).duration_s
    file_len = audio.probe(path).duration_s
    lo = max(0.0, offset)
    hi = min(ref_len, offset + file_len / (1 + ppm * 1e-6))
    ref_t = max(0.0, (lo + hi) / 2 - LISTEN_S / 2)
    file_t = (ref_t - offset) * (1 + ppm * 1e-6)
    left = _excerpt(ref, ref_t)
    right = _excerpt(path, file_t)
    n = min(len(left), len(right))
    return np.column_stack([left[:n], right[:n]]).ravel()


def _excerpt(path: str, start_s: float) -> np.ndarray:
    cmd = [
        audio._tool("ffmpeg"),
        "-v",
        "error",
        "-ss",
        f"{max(start_s, 0.0):.6f}",
        "-t",
        f"{LISTEN_S}",
        "-i",
        path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(LISTEN_RATE),
        "-f",
        "f32le",
        "-",
    ]
    done = subprocess.run(cmd, capture_output=True, check=False)
    if done.returncode != 0:
        raise audio.AudioError(done.stderr.decode(errors="replace").strip())
    return np.frombuffer(done.stdout, dtype="<f4")


def selftest(folder: str) -> int:
    """Headless check for CI: analyse every file in ``folder`` (first = reference),
    expect all reliable, cut a listening excerpt, exit 0 on success."""
    app = QApplication.instance() or QApplication(sys.argv)
    win = Window()
    win.files.add([folder])
    outcome = {"code": 1}

    def done(rows):
        if rows:
            bad = [r["file"] for r in rows if not r["reliable"]]
            stereo = listen_excerpt(rows[0])
            print(json.dumps({"rows": rows, "excerpt_samples": len(stereo), "unreliable": bad}))
            outcome["code"] = 0 if not bad and len(stereo) >= LISTEN_RATE * 2 * 4 else 1
        if win.proc is not None:
            win.proc.waitForFinished(10_000)
        app.quit()

    win.on_result = done
    QTimer.singleShot(0, win.start)
    QTimer.singleShot(300_000, app.quit)
    app.exec()
    print("selftest", "ok" if outcome["code"] == 0 else "FAILED")
    return outcome["code"]


def run(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        return selftest(argv[1])
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = Window()
    win.files.add(argv)
    win.show()
    return app.exec()
