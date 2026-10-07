"""Reading audio from any audio/video file via ffmpeg."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from scipy import signal as sps

from chronon import messages

RESAMPLE_QUALITY = "HQ"
EXCERPT_PAD_S = 0.05  # extra audio read on both sides of an excerpt for the resampler
SEEK_MARGIN_S = 2.0  # ffmpeg excerpts: decoded from this long before the start


class AudioError(messages.UserError, RuntimeError):
    pass


@dataclass(frozen=True)
class Info:
    """The first audio stream of a file."""

    sample_rate: int
    channels: int
    bits: int  # PCM bit depth; 24 for compressed audio (AAC, MP3, ...)
    frames: int  # samples per channel (estimated from the duration if not stored)
    has_video: bool
    codec: str = ""
    time_reference: int | None = None  # BWF time stamp (samples since midnight)
    recorder: str = ""  # whatever names the device: BWF encoder, MP4 brand / encoder tag

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
    # ffprobe writes UTF-8; text=True would decode with the locale (cp1252 on Windows)
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AudioError(
            f"cannot read {path}: {result.stderr.strip()}",
            "unreadable_file",
            file=str(path),
            detail=result.stderr.strip(),
        )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise AudioError(f"{path} has no audio stream", "no_audio", file=str(path))
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
    tags = {k.lower(): v for k, v in data.get("format", {}).get("tags", {}).items()}
    ref = tags.get("time_reference")
    recorder = " ".join(
        str(tags[k]) for k in ("encoded_by", "originator", "major_brand", "encoder") if tags.get(k)
    )
    return Info(
        rate,
        int(a["channels"]),
        bits,
        frames,
        has_video,
        codec=str(a.get("codec_name", "")),
        time_reference=int(ref) if ref and str(ref).isdigit() else None,
        recorder=recorder,
    )


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
            raise AudioError(
                f"cannot decode {path}: {err}", "unreadable_file", file=str(path), detail=err
            )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _tool(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        raise AudioError(
            f"{name} not found; install ffmpeg (e.g. 'brew install ffmpeg')",
            "ffmpeg_missing",
            tool=name,
        )
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
        err = result.stderr.decode(errors="replace").strip()
        raise AudioError(
            f"cannot decode {path}: {err}", "unreadable_file", file=str(path), detail=err
        )
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def excerpt(path: Path | str, start_s: float, seconds: float, rate: int) -> np.ndarray:
    """``seconds`` of ``path`` from file time ``start_s`` on, mono float32 at ``rate`` Hz,
    zeros before and after the recording; always ``round(seconds * rate)`` samples.

    Fast anywhere in a long file: PCM files (and whatever else libsndfile reads) seek to the
    sample, everything else is decoded by ffmpeg from shortly before ``start_s``."""
    n = round(seconds * rate)
    try:
        f = sf.SoundFile(str(path))
    except (RuntimeError, sf.LibsndfileError):
        return _excerpt_ffmpeg(path, start_s, seconds, rate, n)
    with f:
        native = f.samplerate
        pad = int(EXCERPT_PAD_S * native) if native != rate else 0
        first = round(start_s * native) - pad
        count = round(seconds * native) + 2 * pad
        data = np.zeros(count, dtype=np.float32)
        a, b = max(first, 0), min(first + count, f.frames)
        if b > a:
            f.seek(a)
            block = f.read(b - a, dtype="float32", always_2d=True)
            data[a - first : a - first + len(block)] = block.mean(axis=1)
    if native != rate:
        data = soxr.resample(data, native, rate, quality=RESAMPLE_QUALITY)
    return _fit(data[round(pad * rate / native) :], n)


def _excerpt_ffmpeg(path: Path | str, start_s: float, seconds: float, rate: int, n: int):
    lead = round(max(-start_s, 0.0) * rate)  # silence before the recording starts
    take = seconds - lead / rate
    if take <= 0:
        return np.zeros(n, dtype=np.float32)
    # Seek coarsely before the excerpt (fast), then cut it exactly from the decoded audio.
    # Seeking straight to the start lands up to ~1100 samples off on AAC in newer ffmpeg
    # (the encoder delay is lost), and so does any seek near the file's start.
    start = max(start_s, 0.0)
    seek = start - SEEK_MARGIN_S if start >= 2 * SEEK_MARGIN_S else 0.0
    cmd = [
        _tool("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        *(["-ss", f"{seek:.6f}"] if seek else []),
        "-i",
        str(path),
        "-ss",
        f"{start - seek:.6f}",
        "-t",
        f"{take:.6f}",
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
        err = result.stderr.decode(errors="replace").strip()
        raise AudioError(
            f"cannot decode {path}: {err}", "unreadable_file", file=str(path), detail=err
        )
    data = np.frombuffer(result.stdout, dtype="<f4")
    return _fit(np.concatenate([np.zeros(lead, dtype=np.float32), data]), n)


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    """``x`` cut or padded with zeros to ``n`` samples."""
    out = np.zeros(n, dtype=np.float32)
    out[: min(n, len(x))] = x[:n]
    return out


def resample(x: np.ndarray, rate_in: int, rate_out: int) -> np.ndarray:
    """Band-limited polyphase resampling between integer rates."""
    if rate_in == rate_out:
        return np.asarray(x, dtype=np.float32)
    g = gcd(rate_in, rate_out)
    return sps.resample_poly(x, rate_out // g, rate_in // g).astype(np.float32)


# --- analysis-rate access ----------------------------------------------------
#
# Analysis works on mono audio at one rate. A file being aligned is decoded once, as a
# stream; reference tracks are read as short excerpts wherever a window needs them, so
# no whole recording is ever held in memory. Both paths resample with libsoxr, so their
# time bases agree exactly.


def stream_mono(path: Path | str, rate: int, block_frames: int = 1 << 16) -> Iterator[np.ndarray]:
    """The first audio stream of ``path`` as mono float32 blocks at ``rate`` Hz."""
    info = probe(path)
    rs = None
    if info.sample_rate != rate:
        rs = soxr.ResampleStream(
            info.sample_rate, rate, 1, dtype="float32", quality=RESAMPLE_QUALITY
        )
    prev = None
    for block in stream(path, info.channels, block_frames):
        mono = block.mean(axis=1) if info.channels > 1 else block[:, 0]
        if rs is None:
            yield np.ascontiguousarray(mono, dtype=np.float32)
            continue
        if prev is not None:
            yield rs.resample_chunk(prev)
        prev = mono
    if rs is not None:
        yield rs.resample_chunk(prev if prev is not None else np.zeros(0, np.float32), last=True)


class Source:
    """Random access to a recording at the analysis rate."""

    rate: int
    length: int  # samples at ``rate``

    def read(self, start: int, n: int) -> tuple[np.ndarray, float]:
        """About ``n`` samples from analysis sample ``start`` on, zeros outside the
        recording, and the exact analysis-sample position of the first returned sample
        (it can be fractional when the native rate is not a multiple of the analysis rate)."""
        raise NotImplementedError


class ArraySource(Source):
    def __init__(self, x: np.ndarray, rate: int):
        self.x = np.asarray(x, dtype=np.float32)
        self.rate = rate
        self.length = len(self.x)

    def read(self, start: int, n: int) -> tuple[np.ndarray, float]:
        out = np.zeros(n, dtype=np.float32)
        lo, hi = max(start, 0), min(start + n, self.length)
        if hi > lo:
            out[lo - start : hi - start] = self.x[lo:hi]
        return out, float(start)


class FileSource(Source):
    """Excerpts read straight from a PCM file (WAV, AIFF, CAF, ...) by seeking.
    Safe to read from several threads: each thread gets its own file handle."""

    def __init__(self, path: Path | str, rate: int):
        self.path = Path(path)
        with sf.SoundFile(str(path)) as f:
            self.native, self.frames, self.channels = f.samplerate, f.frames, f.channels
        self.rate = rate
        self.length = int(self.frames * rate / self.native)
        self._local = threading.local()

    @property
    def file(self) -> sf.SoundFile:
        f = getattr(self._local, "file", None)
        if f is None:
            f = self._local.file = sf.SoundFile(str(self.path))
        return f

    def read(self, start: int, n: int) -> tuple[np.ndarray, float]:
        ratio = self.native / self.rate
        pad = int(EXCERPT_PAD_S * self.native)
        first = int(np.floor(start * ratio))  # native sample at or before ``start``
        lo = first - pad
        count = int(np.ceil(n * ratio)) + 2 * pad + 2
        data = np.zeros(count, dtype=np.float32)
        a, b = max(lo, 0), min(lo + count, self.frames)
        if b > a:
            f = self.file
            f.seek(a)
            block = f.read(b - a, dtype="float32", always_2d=True)
            mono = block[:, 0] if self.channels == 1 else block.mean(axis=1)
            data[a - lo : a - lo + len(block)] = mono
        if self.native != self.rate:
            data = soxr.resample(data, self.native, self.rate, quality=RESAMPLE_QUALITY)
        skip = int(round(pad / ratio))
        exact = (lo + skip * ratio) / ratio  # analysis position of data[skip]
        return np.ascontiguousarray(data[skip : skip + n]), exact

    def close(self) -> None:
        f = getattr(self._local, "file", None)
        if f is not None:
            f.close()


_BWF_FORMAT = "<256s32s32s10s8s2xIIh64s5h180sI256s"  # libsndfile SF_BROADCAST_INFO (864 bytes)
_SFC_SET_BROADCAST_INFO = 0x10F1


def set_bwf(f: sf.SoundFile, time_reference: int, rate: int, originator: str = "Chronon") -> None:
    """Give a WAV / RF64 file being written a BWF ``bext`` chunk with ``time_reference``
    (samples since midnight). Must be called before any audio is written."""
    now = time.localtime()
    history = f"A=PCM,F={rate},T={originator}\r\n".encode()
    info = struct.pack(
        _BWF_FORMAT,
        b"",
        originator.encode()[:32],
        b"",
        time.strftime("%Y-%m-%d", now).encode(),
        time.strftime("%H:%M:%S", now).encode(),
        time_reference & 0xFFFFFFFF,
        time_reference >> 32,
        1,
        b"",
        0,
        0,
        0,
        0,
        0,
        b"",
        len(history),
        history,
    )
    ffi = sf._ffi
    if (
        sf._snd.sf_command(f._file, _SFC_SET_BROADCAST_INFO, ffi.new("char[]", info), len(info))
        != 1
    ):
        raise AudioError(f"cannot write a BWF time stamp into {f.name}")


def open_source(path: Path | str, rate: int) -> Source:
    """Excerpt access for PCM files, else the whole file decoded into memory."""
    try:
        return FileSource(path, rate)
    except (RuntimeError, sf.LibsndfileError):
        return ArraySource(np.concatenate(list(stream_mono(path, rate)) or [np.zeros(0)]), rate)
