"""The generator is our ground truth, so it gets checked against the source directly."""

import json

import numpy as np
import pytest
from scipy import signal as sps
from scipy.io import wavfile

from chronon import synth
from chronon.cli import main
from chronon.synth import Clip, Device, Scenario


def _source(scenario: Scenario) -> np.ndarray:
    seed = np.random.SeedSequence(scenario.seed).spawn(1 + len(scenario.devices))[0]
    return synth.make_source(
        scenario.signal, scenario.duration_s, scenario.source_rate, np.random.default_rng(seed)
    )


def _lag(ref: np.ndarray, x: np.ndarray) -> int:
    """Samples by which ``x`` starts later than ``ref`` (peak of |cross-correlation|)."""
    c = sps.correlate(ref, x, mode="full", method="fft")
    return int(np.argmax(np.abs(c))) - (len(x) - 1)


def test_clean_device_reproduces_source_exactly():
    scenario = Scenario(devices=(Device("a", (Clip(2.0, 5.0),)),), signal="noise")
    (rc,) = synth.render(scenario)
    source = _source(scenario)
    start = 2 * scenario.source_rate
    np.testing.assert_allclose(rc.audio, source[start : start + len(rc.audio)], atol=1e-6)


@pytest.mark.parametrize("ppm", [0.0, 50.0, -120.0])
def test_file_length_follows_drift(ppm):
    device = Device("a", (Clip(0.0, 100.0),), drift_ppm=ppm)
    assert device.num_samples(device.clips[0]) == round(100.0 * 48_000 * (1 + ppm * 1e-6))


def test_true_time_convention():
    device = Device("a", (Clip(1.5, 10.0),), sample_rate=44_100, drift_ppm=100.0)
    assert device.true_time(device.clips[0], 0) == 1.5
    assert device.true_time(device.clips[0], 44_100 * 1.0001) == pytest.approx(2.5)


@pytest.mark.parametrize("signal", synth.SIGNALS)
def test_offset_recoverable_from_coloured_device(signal):
    scenario = Scenario(
        signal=signal,
        seed=3,
        devices=(
            Device("ref", (Clip(0.0, 20.0),)),
            Device(
                "cam",
                (Clip(3.25, 12.0),),
                snr_db=20.0,
                highpass_hz=120.0,
                lowpass_hz=8000.0,
                rt60_s=0.4,
                gain_db=-6.0,
            ),
        ),
    )
    ref, cam = synth.render(scenario)
    assert abs(_lag(ref.audio, cam.audio) - 3.25 * 48_000) <= 1


def test_drift_visible_across_clip():
    ppm, rate = 200.0, 48_000
    scenario = Scenario(
        signal="noise",
        seed=1,
        devices=(
            Device("ref", (Clip(0.0, 60.0),)),
            Device("cam", (Clip(1.0, 55.0),), drift_ppm=ppm, snr_db=30.0),
        ),
    )
    ref, cam = synth.render(scenario)
    win = 2 * rate
    d = ppm * 1e-6
    for pos in (0, len(cam.audio) // 2, len(cam.audio) - win):
        # a window's lag is that of its centre; the fast clock pulls later windows earlier
        expected = rate - (pos + win / 2) * d / (1 + d)
        assert abs(_lag(ref.audio, cam.audio[pos : pos + win]) - pos - expected) <= 1


def test_invert_flips_polarity():
    base = Device("a", (Clip(0.0, 1.0),))
    scenario = Scenario(signal="noise", devices=(base, Device("b", base.clips, invert=True)))
    a, b = synth.render(scenario)
    np.testing.assert_array_equal(a.audio, -b.audio)


def test_render_is_deterministic():
    a = synth.render(synth.preset("basic"))
    b = synth.render(synth.preset("basic"))
    for x, y in zip(a, b, strict=True):
        np.testing.assert_array_equal(x.audio, y.audio)


def test_write_roundtrip(tmp_path):
    scenario = Scenario(
        devices=(
            Device("rec", (Clip(0.0, 3.0),)),
            Device("cam", (Clip(0.5, 1.0), Clip(1.8, 1.0)), sample_rate=44_100, drift_ppm=30.0),
        )
    )
    truth = json.loads(synth.write(scenario, tmp_path).read_text())
    assert truth["version"] == synth.TRUTH_VERSION
    cam = truth["devices"][1]
    assert [c["file"] for c in cam["clips"]] == ["cam_01.wav", "cam_02.wav"]
    assert cam["drift_ppm"] == 30.0
    for clip in cam["clips"]:
        rate, audio = wavfile.read(tmp_path / clip["file"])
        assert rate == 44_100
        assert audio.dtype == np.float32
        assert len(audio) == clip["num_samples"]


def test_presets_are_valid():
    for name in synth.PRESETS:
        scenario = synth.preset(name)
        assert scenario.signal in synth.SIGNALS
        assert len({d.name for d in scenario.devices}) == len(scenario.devices)
    with pytest.raises(ValueError):
        synth.preset("nope")


def test_cli_synth(tmp_path, capsys):
    assert main(["synth", str(tmp_path), "--preset", "basic", "--signal", "noise"]) == 0
    assert (tmp_path / "recorder_01.wav").exists()
    assert (tmp_path / "camera_01.wav").exists()
    truth = json.loads((tmp_path / "truth.json").read_text())
    assert truth["signal"] == "noise"
    assert "camera_01.wav" in capsys.readouterr().out
