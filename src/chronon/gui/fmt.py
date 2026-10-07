"""How numbers look in the UI (German): durations, rates, start times."""

from __future__ import annotations

from fractions import Fraction

from chronon import audio

AUDIO_FRAME = Fraction(1, 25)  # frames of a start time without video (the timeline default)


def duration(seconds: float) -> str:
    """``3:58:41`` (hours always shown, as the file list on board 03)."""
    s = round(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def rate(hz: int) -> str:
    """``48 kHz``, ``44,1 kHz``."""
    khz = hz / 1000
    text = f"{khz:.1f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} kHz"


def start(info: audio.Info) -> str:
    """The media's own start as ``HH:MM:SS:FF`` (video: its frame rate, audio: 25 fps),
    ``–`` without timecode or time stamp. Starts past midnight wrap as editors show them."""
    t = info.start
    if t is None:
        return "–"
    frame = info.frame or AUDIO_FRAME
    fps = round(1 / frame)
    frames = int(t / frame) % (24 * 3600 * fps)
    s, f = divmod(frames, fps)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def files(n: int) -> str:
    return "1 Datei" if n == 1 else f"{n} Dateien"
