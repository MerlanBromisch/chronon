"""What the user has put together so far: files and what reading them found."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from chronon import align, audio, devices

# What a folder contributes: audio and video, no sidecar files (XML, THM, LRF, JSON ...)
MEDIA = {
    ".wav", ".bwf", ".aif", ".aiff", ".aifc", ".caf", ".flac", ".mp3", ".m4a", ".aac",
    ".ogg", ".opus", ".wma", ".mp4", ".mov", ".m4v", ".mxf", ".mts", ".m2ts", ".avi",
    ".mkv", ".webm", ".3gp", ".mpg", ".mpeg", ".dv",
}  # fmt: skip
READ_THREADS = 4


def media_files(paths: list[str | Path]) -> list[Path]:
    """Files as given, folders searched (with subfolders) for audio and video; hidden
    files and duplicates left out, in the given order (folders sorted)."""
    out: dict[Path, None] = {}
    for p in map(Path, paths):
        if p.is_dir():
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(names):
                    f = Path(root) / name
                    if not name.startswith(".") and f.suffix.lower() in MEDIA:
                        out.setdefault(f, None)
        elif p.is_file():
            out.setdefault(p, None)
    return list(out)


@dataclass
class Entry:
    path: Path
    info: audio.Info | None = None
    error: str | None = None  # ffmpeg's message for an unreadable file
    skipped: bool = False  # unreadable, kept in the list but left out ("ohne diese Datei")


@dataclass
class Project:
    entries: list[Entry] = field(default_factory=list)
    layout: devices.Layout | None = None  # step 2: the devices as the user arranged them
    suggested_tracks: list[Path] = field(default_factory=list)  # Chronon's reference tracks
    work_dir: Path | None = None  # this session's own files (layout, analysis, protocols)
    synced: str = ""  # the layout (as JSON) the analysis was made with
    analysis_path: Path | None = None  # step 3's saved analysis (analyze --save)
    result: dict | None = None  # step 3's result event (rows per file)

    @property
    def layout_json(self) -> str:
        return json.dumps(self.layout.to_dict(), sort_keys=True) if self.layout else ""

    @property
    def synced_now(self) -> bool:
        """Whether step 3's result still belongs to the devices of step 2."""
        return self.result is not None and self.synced == self.layout_json

    def folder(self) -> Path:
        if self.work_dir is None:
            from chronon.gui import settings

            self.work_dir = settings.new_work_dir()
        return self.work_dir

    def add(self, paths: list[Path]) -> list[Entry]:
        known = {e.path for e in self.entries}
        new = [Entry(p) for p in paths if p not in known]
        self.entries += new
        return new

    def remove(self, path: Path) -> None:
        self.entries = [e for e in self.entries if e.path != path]

    @property
    def readable(self) -> list[Entry]:
        return [e for e in self.entries if e.info is not None]

    @property
    def infos(self) -> dict[Path, audio.Info]:
        """The files that take part, with their metadata."""
        return {e.path: e.info for e in self.entries if e.info is not None}

    @property
    def layout_current(self) -> bool:
        """Whether the devices still hold exactly the files of step 1."""
        return self.layout is not None and set(self.layout.files) == set(self.infos)


class Detector(QObject):
    """Groups the files into devices and suggests a reference (``devices.detect``) on a
    thread: reading the levels of a desk's tracks takes a moment."""

    finished = Signal(object)  # devices.Layout, or an Exception

    def __init__(self, infos: dict[Path, audio.Info], parent: QObject | None = None):
        super().__init__(parent)
        self.infos = infos

    def start(self) -> None:
        QThreadPool.globalInstance().start(self._run)

    def _run(self) -> None:
        try:
            layout = devices.detect(
                list(self.infos), probe=self.infos.__getitem__, loudest=align.loudest
            )
        except Exception as e:  # shown on the page; the window must not die
            self.finished.emit(e)
        else:
            self.finished.emit(layout)


class Reader(QObject):
    """Reads files' metadata (one ffprobe each) on a few threads; the window stays live."""

    read = Signal(object)  # Entry, after each file
    finished = Signal()

    def __init__(self, entries: list[Entry], parent: QObject | None = None):
        super().__init__(parent)
        self.entries = entries
        self.left = len(entries)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(READ_THREADS)

    def start(self) -> None:
        if not self.entries:
            self.finished.emit()
        for e in self.entries:
            self.pool.start(_Probe(self, e))

    def cancel(self) -> None:
        self.stop.set()

    def _done(self, entry: Entry) -> None:
        self.read.emit(entry)
        with self.lock:
            self.left -= 1
            last = self.left == 0
        if last:
            self.finished.emit()


class _Probe(QRunnable):
    def __init__(self, reader: Reader, entry: Entry):
        super().__init__()
        self.reader, self.entry = reader, entry

    def run(self) -> None:
        if not self.reader.stop.is_set():
            try:
                self.entry.info = audio.probe(self.entry.path)
            except audio.AudioError as e:
                detail = e.fields.get("detail") or str(e)
                self.entry.error = detail.splitlines()[-1] if detail else str(e)
        self.reader._done(self.entry)
