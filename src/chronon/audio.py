"""Reading audio from any audio/video file via ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
from scipy import signal as sps


class AudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class Info:
    """The first audio stream of a file."""

    sample_rate: int
    channels: int
    bits: int  # PCM bit depth; 24 for compressed audio (AAC, MP3, ...)
    frames: int  # samples per channel (estimated from the duration if not stored)
    has_video: bool

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate


def probe(path: Path | str) -> Info:
    cmd = [
        _tool("ffprobe"),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AudioError(f"cannot read {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise AudioError(f"{path} has no audio stream")
    a = audio_streams[0]
    rate = int(a["sample_rate"])
    bits = int(a.get("bits_per_raw_sample") or a.get("bits_per_sample") or 0)
    if not a.get("codec_name", "").startswith("pcm_") or bits not in (16, 24, 32):
        bits = 24
    if a.get("duration_ts") and a.get("time_base") == f"1/{rate}":
        frames = int(a["duration_ts"])
    else:
        duration = float(a.get("duration") or data.get("format", {}).get("duration") or 0)
        frames = round(duration * rate)
    has_video = any(
        s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        for s in streams
    )
    return Info(rate, int(a["channels"]), bits, frames, has_video)


def stream(path: Path | str, channels: int, block_frames: int = 1 << 16) -> Iterator[np.ndarray]:
    """First audio stream of ``path`` at its native rate, as float32 blocks of shape
    (frames, channels). Only one block is in memory at a time."""
    cmd = [
        _tool("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-f",
        "f32le",
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    block_bytes = block_frames * channels * 4
    try:
        while True:
            buf = proc.stdout.read(block_bytes)
            if not buf:
                break
            usable = len(buf) - len(buf) % (channels * 4)
            yield np.frombuffer(buf[:usable], dtype="<f4").reshape(-1, channels)
        err = proc.stderr.read().decode(errors="replace").strip()
        if proc.wait() != 0:
            raise AudioError(f"cannot decode {path}: {err}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _tool(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        raise AudioError(f"{name} not found; install ffmpeg (e.g. 'brew install ffmpeg')")
    return exe


def load(path: Path | str, rate: int) -> np.ndarray:
    """First audio stream of ``path`` as mono float32 at ``rate`` Hz.

    Time is file time: sample ``n`` is at ``n / rate`` seconds of the file as its
    nominal sample rate plays it back. Channels are averaged.
    """
    cmd = [
        _tool("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(rate),
        "-f",
        "f32le",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise AudioError(f"cannot decode {path}: {result.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def resample(x: np.ndarray, rate_in: int, rate_out: int) -> np.ndarray:
    """Band-limited polyphase resampling between integer rates."""
    if rate_in == rate_out:
        return np.asarray(x, dtype=np.float32)
    g = gcd(rate_in, rate_out)
    return sps.resample_poly(x, rate_out // g, rate_in // g).astype(np.float32)
