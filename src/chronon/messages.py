"""Notes and user-fixable errors as codes, so the app can translate them (docs/app.md).

A note is a JSON object ``{"code": "measured_via", "file": "/path/C2379.MP4"}``: a code plus
the values its text needs. Paths are full paths; texts show the file name. The terminal
builds its English text from the same codes (``text``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

Note = dict


def note(code: str, **fields) -> Note:
    """A note; ``fields`` must be JSON values (paths as strings)."""
    if code not in TEXTS:
        raise KeyError(f"unknown note code {code!r}")
    return {"code": code, **fields}


def _name(path: str) -> str:
    return Path(path).name


def _clip(n: Note) -> str:
    """Prefix naming the clip a note is about, inside an output of joined clips."""
    return f"{_name(n['file'])}: " if n.get("file") else ""


TEXTS: dict[str, Callable[[Note], str]] = {
    # how a file was placed
    "no_reliable_match": lambda n: "NO RELIABLE MATCH",
    "inverted": lambda n: "inverted",
    "clock_wanders": lambda n: f"clock wanders ±{n['ms']:.1f} ms",
    "matched_track": lambda n: f"via {_name(n['file'])}",
    "reference_clock": lambda n: "same clock and start as the reference",
    "measured_via": lambda n: f"measured via {_name(n['file'])}",
    "drift_from": lambda n: f"drift from {_name(n['file'])}",
    "linked_via": lambda n: (
        f"linked via {_name(n['file'])} (no reliable overlap with the reference)"
    ),
    "continues": lambda n: f"continues {_name(n['file'])} (one take split into files)",
    # the written file
    "joined": lambda n: "joined: " + ", ".join(_name(f) for f in n["files"]),
    "wav_over_2gib": lambda n: "WAV over 2 GiB: a few programs may not read it",
    "caf_over_2gib": lambda n: "written as CAF: over 2 GiB",
    "caf_no_time_stamp": lambda n: "no time stamp in CAF: place it from the timeline file",
    "video_unchanged": lambda n: "audio of a video file; the video itself is not changed",
    "video_placed": lambda n: f"{_clip(n)}video placed within ±{n['ms']:.0f} ms",
    "variable_frame_rate": lambda n: (
        f"{_clip(n)}variable frame rate: check picture against sound at the clip's end"
    ),
    # verification
    "not_verifiable": lambda n: f"verification failed: {n['reason']}",
    "verification_failed": lambda n: (
        "VERIFICATION FAILED: output is not in sync with the reference"
    ),
    "verification_failed_via": lambda n: "VERIFICATION FAILED (via its parallel track)",
    # a note of a report written before codes existed
    "text": lambda n: n["text"],
}


def text(n: Note | str) -> str:
    """English text of a note (old reports hold plain strings)."""
    if isinstance(n, str):
        return n
    return TEXTS[n["code"]](n)


def texts(notes: Iterable[Note | str]) -> str:
    return ", ".join(text(n) for n in notes)


def from_text(n: Note | str) -> Note:
    """A note of any report: plain strings (before codes existed) become ``text`` notes."""
    return note("text", text=n) if isinstance(n, str) else n


class UserError(Exception):
    """An error the user can fix. ``code`` and ``fields`` let the app say it in its own
    language; ``str(error)`` is the English message."""

    def __init__(self, message: str, code: str | None = None, **fields):
        super().__init__(message)
        if code is not None and code not in ERRORS:
            raise KeyError(f"unknown error code {code!r}")
        self.code = code
        self.fields = fields


# error codes and their fields (paths as strings)
ERRORS = {
    "ffmpeg_missing": ("tool",),
    "unreadable_file": ("file", "detail"),
    "no_audio": ("file",),
    "outdir_holds_input": ("folder", "file"),
    "not_enough_space": ("folder", "need_bytes", "free_bytes"),
    "output_exists": ("file",),
    "too_large_for_wav": ("file", "bytes"),
    "source_missing": ("file",),
    "source_changed": ("file",),
}
