"""What the app keeps between runs (QSettings) and where it writes its own files."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

APPEARANCES = {"system": "System", "light": "Hell", "dark": "Dunkel"}


def store() -> QSettings:
    return QSettings("Chronon", "Chronon")


def default_cache() -> Path:
    """Per platform, as docs/design/ui/README.md says."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Chronon"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Chronon" / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "chronon"


def cache_dir() -> Path:
    """Waveform overviews; changeable in the settings."""
    return Path(str(store().value("cache", str(default_cache()))))


def set_cache_dir(path: Path) -> None:
    store().setValue("cache", str(path))


def appearance() -> str:
    value = str(store().value("appearance", "system"))
    return value if value in APPEARANCES else "system"


def set_appearance(value: str) -> None:
    store().setValue("appearance", value)


def data_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(location or Path.home() / ".chronon")


def new_work_dir() -> Path:
    """A folder for one session's own files: the device layout, the analysis, protocols."""
    path = data_dir() / "sessions" / time.strftime("%Y-%m-%d_%H-%M-%S")
    n = 2
    while path.exists():
        path = path.with_name(f"{path.name}_{n}")
        n += 1
    path.mkdir(parents=True)
    return path
