"""Synthetic multi-device recordings with known ground truth.

Every sync and drift algorithm in Chronon is tested against scenarios from this
module, because only here do we know the exact answer.

Model
-----
A "true" sound field is recorded by several devices. It holds one or more
independent sources (``signal`` plus ``extra_signals``); each device picks up each
source with its own gain and acoustic delay (``pickup``), so, as in a real room, the
lag between two devices depends on which source is dominant. Each device has its
own clock, which runs at ``1 + drift_ppm * 1e-6`` times real time.
A clip that starts at true time ``start_s`` holds, at file sample ``n`` (played
back at the nominal ``sample_rate``), the sound at true time::

    t_true(n) = start_s + n / (sample_rate * (1 + drift_ppm * 1e-6))

So positive drift means the device clock runs fast and the file comes out longer
than the real time span it covers. ``duration_s`` of a clip is that real time span.
``wander_ms`` bends the clock away from that straight line by up to that much in the
middle of each clip (a parabola, zero at both ends), like a clock whose rate drifts
with temperature.

Device colouration (reverb, filters, gain, noise, polarity) is applied after
sampling, at the device rate. The source is band-limited to 20 % of its sample
rate, so linear interpolation onto the device clock stays accurate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy import signal as sps
from scipy.io import wavfile

SIGNALS = ("speech", "music", "noise")
TRUTH_FILE = "truth.json"
TRUTH_VERSION = 2


@dataclass(frozen=True)
class Clip:
    """One recorded file: the real time span it covers."""

    start_s: float
    duration_s: float


@dataclass(frozen=True)
class Device:
    """A recording device: its clock, its colouration and the clips it recorded."""

    name: str
    clips: tuple[Clip, ...]
    sample_rate: int = 48_000
    drift_ppm: float = 0.0
    gain_db: float = 0.0
    snr_db: float | None = None
    highpass_hz: float | None = None
    lowpass_hz: float | None = None
    rt60_s: float | None = None
    invert: bool = False
    pickup: tuple[tuple[float, float], ...] = ()  # per source: (gain_db, delay_ms)
    wander_ms: float = 0.0

    def true_time(self, clip: Clip, n: np.ndarray | float) -> np.ndarray | float:
        """True time (s) of file sample ``n`` of ``clip``."""
        t = clip.start_s + n / (self.sample_rate * (1 + self.drift_ppm * 1e-6))
        if self.wander_ms:
            u = n / self.num_samples(clip)
            t = t + self.wander_ms * 1e-3 * 4 * u * (1 - u)
        return t

    def sources(self) -> tuple[tuple[float, float], ...]:
        """(gain_db, delay_ms) per source; by default only source 0, directly."""
        return self.pickup or ((0.0, 0.0),)

    def num_samples(self, clip: Clip) -> int:
        return round(clip.duration_s * self.sample_rate * (1 + self.drift_ppm * 1e-6))


@dataclass(frozen=True)
class Scenario:
    devices: tuple[Device, ...]
    signal: str = "speech"
    seed: int = 0
    source_rate: int = 48_000
    extra_signals: tuple[str, ...] = ()

    @property
    def duration_s(self) -> float:
        """Length of the source timeline: up to the end of the last clip."""
        return max(c.start_s + c.duration_s for d in self.devices for c in d.clips)


@dataclass
class RenderedClip:
    device: Device
    clip: Clip
    index: int
    audio: np.ndarray = field(repr=False)

    @property
    def filename(self) -> str:
        return f"{self.device.name}_{self.index + 1:02d}.wav"


# --- source signals --------------------------------------------------------


def make_source(kind: str, duration_s: float, rate: int, rng: np.random.Generator) -> np.ndarray:
    """A band-limited test signal of the given kind, normalised to -20 dBFS RMS."""
    n = int(np.ceil(duration_s * rate))
    if kind == "noise":
        x = _pink_noise(n, rng)
    elif kind == "speech":
        x = _speech_like(n, rate, rng)
    elif kind == "music":
        x = _music_like(n, rate, rng)
    else:
        raise ValueError(f"unknown signal {kind!r}, expected one of {SIGNALS}")
    x = sps.sosfilt(sps.butter(8, 0.2 * rate, fs=rate, output="sos"), x)
    return _normalise(x, -20.0).astype(np.float32)


def _pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spectrum), dtype=np.float64)
    f[0] = 1.0
    return np.fft.irfft(spectrum / np.sqrt(f), n)


def _speech_like(n: int, rate: int, rng: np.random.Generator) -> np.ndarray:
    """Band-passed noise gated into syllables, words and pauses."""
    carrier = sps.sosfilt(
        sps.butter(4, (150, 4000), "bandpass", fs=rate, output="sos"), rng.standard_normal(n)
    )
    ctrl_rate = 200
    env = np.zeros(int(np.ceil(n / rate * ctrl_rate)) + 1)
    pos = 0
    while pos < len(env):
        on = int(rng.uniform(0.08, 0.35) * ctrl_rate)
        off = int(rng.choice([rng.uniform(0.03, 0.12), rng.uniform(0.2, 0.9)]) * ctrl_rate)
        env[pos : pos + on] = rng.uniform(0.3, 1.0)
        pos += on + off
    env = np.convolve(env, np.hanning(5) / np.hanning(5).sum(), mode="same")
    return carrier * np.interp(np.arange(n) * ctrl_rate / rate, np.arange(len(env)), env)


def _music_like(n: int, rate: int, rng: np.random.Generator) -> np.ndarray:
    """Harmonic notes on a steady beat grid: strongly periodic, a hard case for sync."""
    x = np.zeros(n)
    beat = 0.25
    scale = np.array([0, 2, 4, 7, 9])
    for voice in range(2):
        t = 0.0
        while t * rate < n:
            length = beat * rng.integers(1, 4)
            k = rng.choice(scale) + 12 * rng.integers(0, 3) - 12 * voice
            freq = 220.0 * 2 ** (k / 12)
            start = int(t * rate)
            m = min(int(length * 2 * rate), n - start)
            tt = np.arange(m) / rate
            env = np.minimum(tt / 0.005, 1.0) * np.exp(-tt / (0.6 * length))
            note = sum(np.sin(2 * np.pi * h * freq * tt) / h for h in range(1, 6))
            x[start : start + m] += 0.5**voice * env * note
            t += length
    return x


def _normalise(x: np.ndarray, rms_db: float) -> np.ndarray:
    rms = np.sqrt(np.mean(np.square(x)))
    return x * (10 ** (rms_db / 20) / rms) if rms > 0 else x


# --- rendering -------------------------------------------------------------


def render(scenario: Scenario) -> list[RenderedClip]:
    """Render every clip of every device. Deterministic for a given seed."""
    n_dev = len(scenario.devices)
    # child seeds do not depend on the spawn count, so extra sources leave the rest unchanged
    seeds = np.random.SeedSequence(scenario.seed).spawn(1 + n_dev + len(scenario.extra_signals))
    source_seeds = [seeds[0], *seeds[1 + n_dev :]]
    sources = [
        make_source(kind, scenario.duration_s, scenario.source_rate, np.random.default_rng(sd))
        for kind, sd in zip((scenario.signal, *scenario.extra_signals), source_seeds, strict=True)
    ]
    out = []
    for device, seed in zip(scenario.devices, seeds[1 : 1 + n_dev], strict=True):
        if len(device.sources()) > len(sources):
            raise ValueError(f"device {device.name!r} picks up more sources than exist")
        rng = np.random.default_rng(seed)
        for i, clip in enumerate(device.clips):
            audio = _record(sources, scenario.source_rate, device, clip, rng)
            out.append(RenderedClip(device, clip, i, audio))
    return out


def _record(
    sources: list[np.ndarray],
    source_rate: int,
    device: Device,
    clip: Clip,
    rng: np.random.Generator,
) -> np.ndarray:
    fs = device.sample_rate
    ir = _reverb_ir(device.rt60_s, fs, rng) if device.rt60_s else None
    pre = len(ir) - 1 if ir is not None else 0  # pre-roll so the reverb tail is already there
    n = np.arange(-pre, device.num_samples(clip))
    t = device.true_time(clip, n)
    x = np.zeros(len(n))
    for source, (gain_db, delay_ms) in zip(sources, device.sources(), strict=False):
        pos = (t - delay_ms * 1e-3) * source_rate
        x += 10 ** (gain_db / 20) * np.interp(pos, np.arange(len(source)), source, 0.0, 0.0)
    if ir is not None:
        x = sps.fftconvolve(x, ir)[: len(x)]
    x = x[pre:]
    if device.highpass_hz:
        x = sps.sosfilt(sps.butter(2, device.highpass_hz, "highpass", fs=fs, output="sos"), x)
    if device.lowpass_hz:
        x = sps.sosfilt(sps.butter(2, device.lowpass_hz, fs=fs, output="sos"), x)
    x = x * 10 ** (device.gain_db / 20) * (-1 if device.invert else 1)
    if device.snr_db is not None:
        rms = np.sqrt(np.mean(np.square(x)))
        x = x + rng.standard_normal(len(x)) * rms / 10 ** (device.snr_db / 20)
    return x.astype(np.float32)


def _reverb_ir(rt60_s: float, rate: int, rng: np.random.Generator) -> np.ndarray:
    """Direct path plus an exponentially decaying noise tail of equal energy."""
    t = np.arange(1, int(rt60_s * rate)) / rate
    tail = rng.standard_normal(len(t)) * np.exp(-6.9 * t / rt60_s)
    return np.concatenate([[1.0], tail / np.linalg.norm(tail)])


def expected_alignment(
    ref_start_s: float, ref_drift_ppm: float, start_s: float, drift_ppm: float
) -> tuple[float, float]:
    """True (offset_s, drift_ppm) of a clip relative to a reference clip.

    Same model as :class:`chronon.align.Alignment`: the clip's file time ``t`` lies at
    reference file time ``offset_s + t / (1 + drift_ppm * 1e-6)``.
    """
    offset = (start_s - ref_start_s) * (1 + ref_drift_ppm * 1e-6)
    drift = ((1 + drift_ppm * 1e-6) / (1 + ref_drift_ppm * 1e-6) - 1) * 1e6
    return offset, drift


# --- files -----------------------------------------------------------------


def write(scenario: Scenario, outdir: Path | str) -> Path:
    """Write every clip as a float WAV plus ``truth.json``; return the truth path."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rendered = render(scenario)
    for rc in rendered:
        wavfile.write(outdir / rc.filename, rc.device.sample_rate, rc.audio)
    truth_path = outdir / TRUTH_FILE
    truth_path.write_text(json.dumps(truth(scenario, rendered), indent=2) + "\n")
    return truth_path


def truth(scenario: Scenario, rendered: list[RenderedClip]) -> dict:
    """Ground truth as plain JSON-serialisable data."""
    devices = []
    for device in scenario.devices:
        d = asdict(device)
        d["clips"] = [
            {
                "file": rc.filename,
                "start_s": rc.clip.start_s,
                "duration_s": rc.clip.duration_s,
                "num_samples": len(rc.audio),
            }
            for rc in rendered
            if rc.device is device
        ]
        devices.append(d)
    return {
        "version": TRUTH_VERSION,
        "convention": "t_true = start_s + n / (sample_rate * (1 + drift_ppm * 1e-6))",
        "signal": scenario.signal,
        "extra_signals": list(scenario.extra_signals),
        "seed": scenario.seed,
        "source_rate": scenario.source_rate,
        "devices": devices,
    }


# --- presets ---------------------------------------------------------------


def _basic() -> Scenario:
    return Scenario(
        devices=(
            Device("recorder", (Clip(0.0, 60.0),)),
            Device(
                "camera",
                (Clip(3.217, 50.0),),
                drift_ppm=12.0,
                snr_db=25.0,
                highpass_hz=120.0,
                lowpass_hz=8000.0,
                rt60_s=0.4,
            ),
        )
    )


def _drift() -> Scenario:
    return Scenario(
        devices=(
            Device("recorder", (Clip(0.0, 1200.0),)),
            Device(
                "cam_a",
                (Clip(1.5, 1190.0),),
                sample_rate=44_100,
                drift_ppm=35.0,
                snr_db=25.0,
                lowpass_hz=10_000.0,
                rt60_s=0.3,
            ),
            Device(
                "cam_b",
                (Clip(7.25, 1180.0),),
                drift_ppm=-22.0,
                snr_db=18.0,
                highpass_hz=200.0,
                rt60_s=0.7,
            ),
        )
    )


def _multiclip() -> Scenario:
    return Scenario(
        devices=(
            Device("recorder", (Clip(0.0, 600.0),)),
            Device(
                "camera",
                (Clip(5.0, 120.0), Clip(140.5, 200.0), Clip(360.0, 230.0)),
                drift_ppm=18.0,
                snr_db=22.0,
                lowpass_hz=9000.0,
                rt60_s=0.5,
            ),
            Device(
                "phone",
                (Clip(200.0, 90.0),),
                sample_rate=44_100,
                drift_ppm=-40.0,
                snr_db=15.0,
                highpass_hz=300.0,
                rt60_s=0.8,
            ),
        )
    )


def _music() -> Scenario:
    return Scenario(
        signal="music",
        devices=(
            Device("desk", (Clip(0.0, 180.0),)),
            Device("camera", (Clip(12.4, 160.0),), drift_ppm=-15.0, snr_db=20.0, rt60_s=1.2),
        ),
    )


PRESETS = {"basic": _basic, "drift": _drift, "multiclip": _multiclip, "music": _music}


def preset(name: str) -> Scenario:
    try:
        return PRESETS[name]()
    except KeyError:
        raise ValueError(f"unknown preset {name!r}, expected one of {tuple(PRESETS)}") from None
