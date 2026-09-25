"""Reading audio from any audio/video file via ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from math import gcd
from pathlib import Path

import numpy as np
from scipy import signal as sps


class AudioError(RuntimeError):
    pass


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
