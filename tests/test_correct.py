import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy import signal as sps

from chronon import correct, synth
from chronon.cli import main
from chronon.synth import Clip, Device, Scenario

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

START = 3.5
DRIFT = 40.0


def _scene(tmp_path, **cam) -> tuple:
    """A clean recorder (the reference) and a clean 44.1 kHz recorder with a fast clock,
    so the corrected output can be compared sample by sample with the source."""
    scenario = Scenario(
        signal="noise",
        seed=21,
        devices=(
            Device("rec", (Clip(0.0, 30.0),)),
            Device("cam", (Clip(START, 20.0),), sample_rate=44_100, drift_ppm=DRIFT, **cam),
        ),
    )
    src = tmp_path / "media"
    synth.write(scenario, src)
    seed = np.random.SeedSequence(scenario.seed).spawn(3)[0]
    source = synth.make_source("noise", scenario.duration_s, 48_000, np.random.default_rng(seed))
    return src / "rec_01.wav", src / "cam_01.wav", source


def _lowpass(x):
    # the synthetic 44.1 kHz device is sampled by linear interpolation, exact only well below
    # the source's 9.6 kHz band limit
    return sps.sosfiltfilt(sps.butter(8, 4000, fs=48_000, output="sos"), x)


def test_drift_and_rate_are_removed(tmp_path):
    rec, cam, source = _scene(tmp_path)
    outputs = correct.run([rec], [cam], tmp_path / "out")
    ref_out, cam_out = outputs
    assert (cam_out.in_rate, cam_out.out_rate) == (44_100, 48_000)
    assert cam_out.drift_ppm == pytest.approx(DRIFT, abs=0.5)
    assert cam_out.position_s == pytest.approx(START, abs=1e-4)
    assert cam_out.verified

    audio, rate = sf.read(cam_out.path, dtype="float64")
    assert rate == 48_000
    pad = round(START * 48_000)
    assert not audio[: pad - 10].any()
    # after padding, the output is the source itself on the reference clock
    n = min(len(audio), len(source)) - pad - 48_000
    got = _lowpass(audio[pad : pad + n])[4800:-4800]
    want = _lowpass(source[pad : pad + n])[4800:-4800]
    assert np.corrcoef(got, want)[0, 1] > 0.999

    ref_audio, _ = sf.read(ref_out.path, dtype="float32")
    np.testing.assert_allclose(ref_audio, sf.read(rec, dtype="float32")[0], atol=2e-7)


def test_unpadded_files_start_with_their_own_audio(tmp_path):
    rec, cam, _ = _scene(tmp_path)
    _, cam_out = correct.run([rec], [cam], tmp_path / "out", pad=False)
    assert cam_out.pad_frames == 0
    assert cam_out.start_s == pytest.approx(START, abs=1e-4)
    assert sf.info(cam_out.path).duration == pytest.approx(20.0, abs=1e-3)
    assert cam_out.verified


def test_report_and_cli(tmp_path, capsys):
    rec, cam, _ = _scene(tmp_path)
    out = tmp_path / "out"
    assert main(["correct", str(rec), str(cam), "-o", str(out)]) == 0
    assert "cam_01.wav" in capsys.readouterr().out
    report = json.loads((out / "chronon-report.json").read_text())
    assert [Path(r["path"]).name for r in report] == ["rec_01.wav", "cam_01.wav"]
    assert report[1]["verified"] is True
    assert (out / "chronon-report.txt").exists()


def test_never_writes_next_to_the_originals(tmp_path):
    rec, cam, _ = _scene(tmp_path)
    with pytest.raises(correct.CorrectError):
        correct.run([rec], [cam], rec.parent)


def test_existing_output_is_not_overwritten(tmp_path):
    rec, cam, _ = _scene(tmp_path)
    correct.run([rec], [cam], tmp_path / "out")
    with pytest.raises(correct.CorrectError):
        correct.run([rec], [cam], tmp_path / "out")
    correct.run([rec], [cam], tmp_path / "out", overwrite=True)


def test_large_files_become_caf(tmp_path, monkeypatch):
    monkeypatch.setattr(correct, "CAF_ABOVE_BYTES", 1_000_000)
    rec, cam, _ = _scene(tmp_path)
    outputs = correct.run([rec], [cam], tmp_path / "out")
    assert all(o.path.endswith(".caf") for o in outputs)
    assert sf.info(outputs[1].path).samplerate == 48_000
    assert outputs[1].verified


def _bwf(path) -> int | None:
    from chronon import audio

    return audio.probe(path).time_reference


def test_outputs_carry_a_bwf_time_stamp(tmp_path):
    rec, cam, _ = _scene(tmp_path)
    ref_out, cam_out = correct.run([rec], [cam], tmp_path / "padded")
    assert _bwf(ref_out.path) == _bwf(cam_out.path) == 3600 * 48_000  # all start at zero
    _, cam_out = correct.run([rec], [cam], tmp_path / "unpadded", pad=False)
    assert _bwf(cam_out.path) == pytest.approx((3600 + START) * 48_000, abs=5)


def test_clips_of_a_device_can_be_joined(tmp_path):
    scenario = Scenario(
        signal="noise",
        seed=22,
        devices=(
            Device("rec", (Clip(0.0, 40.0),)),
            # different lengths: equally long files without time stamps count as parallel tracks
            Device("cam", (Clip(3.5, 12.0), Clip(22.0, 13.0)), sample_rate=44_100, drift_ppm=DRIFT),
        ),
    )
    media = tmp_path / "media"
    synth.write(scenario, media)
    seed = np.random.SeedSequence(scenario.seed).spawn(3)[0]
    source = synth.make_source("noise", scenario.duration_s, 48_000, np.random.default_rng(seed))
    clips = [media / "cam_01.wav", media / "cam_02.wav"]
    outputs = correct.run([media / "rec_01.wav"], clips, tmp_path / "out", join=True, name="t")
    assert [Path(o.path).name for o in outputs] == ["rec_01.wav", "cam.wav"]
    joined = outputs[1]
    assert joined.verified
    assert [s.source for s in joined.segments] == [str(c) for c in clips]
    audio, _ = sf.read(joined.path, dtype="float64")
    for start, length in ((3.5, 12.0), (22.0, 13.0)):
        a, b = round(start * 48_000) + 4800, round((start + length) * 48_000) - 4800
        got, want = _lowpass(audio[a:b]), _lowpass(source[a:b])
        assert np.corrcoef(got[4800:-4800], want[4800:-4800])[0, 1] > 0.999
    gap = audio[round(15.6 * 48_000) : round(21.9 * 48_000)]
    assert not gap.any()
    timeline = (tmp_path / "out" / "t.fcpxml").read_text()
    assert timeline.count("<asset-clip") == 2  # reference + one joined camera file


def test_large_unpadded_files_stay_wav_with_time_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(correct, "CAF_ABOVE_BYTES", 1_000_000)
    rec, cam, _ = _scene(tmp_path)
    _, cam_out = correct.run([rec], [cam], tmp_path / "out", pad=False)
    assert cam_out.format == "wav" and sf.info(cam_out.path).format == "WAV"
    assert _bwf(cam_out.path) == pytest.approx((3600 + START) * 48_000, abs=5)
    assert cam_out.verified
    monkeypatch.setattr(correct, "WAV_MAX_BYTES", 1_000_000)  # beyond what WAV can hold
    _, cam_out = correct.run([rec], [cam], tmp_path / "caf", pad=False)
    assert cam_out.format == "caf" and any("no time stamp" in n for n in cam_out.notes)


def _data_size_is_exact(path) -> bool:
    import struct

    with open(path, "rb") as f:
        f.read(12)
        while True:
            cid, size = struct.unpack("<4sI", f.read(8))
            if cid == b"data":
                return size != 0xFFFFFFFF and f.tell() + size <= Path(path).stat().st_size
            f.seek(size + (size & 1), 1)


def test_wav_outputs_state_their_data_size(tmp_path):
    rec, cam, _ = _scene(tmp_path)
    for o in correct.run([rec], [cam], tmp_path / "out", pad=False):
        assert _data_size_is_exact(o.path)
