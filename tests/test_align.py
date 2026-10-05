import shutil

import numpy as np
import pytest

from chronon import align, audio, synth
from chronon.cli import main
from chronon.synth import Clip, Device, Scenario

RATE = align.ANALYSIS_RATE
TIME_TOL_S = 1e-4  # 0.1 ms
DRIFT_TOL_PPM = 2.0
RELIABLE_BELOW = align.RELIABLE_CONFIDENCE

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _run(scenario: Scenario):
    """Align every clip to the first clip; yield (alignment, true offset, true drift, length)."""
    ref, *others = synth.render(scenario)
    ref_audio = audio.resample(ref.audio, ref.device.sample_rate, RATE)
    for rc in others:
        a = align.align(ref_audio, audio.resample(rc.audio, rc.device.sample_rate, RATE))
        offset, drift = synth.expected_alignment(
            ref.clip.start_s, ref.device.drift_ppm, rc.clip.start_s, rc.device.drift_ppm
        )
        yield a, offset, drift, len(rc.audio) / rc.device.sample_rate


def _assert_matches(scenario: Scenario):
    """Every point of every clip lands within 0.1 ms of where it truly belongs."""
    for a, offset, drift, length in _run(scenario):
        truth = align.Alignment(offset, drift, 1.0, False, 1, 1)
        t = np.array([0.0, length])
        assert np.max(np.abs(a.ref_time(t) - truth.ref_time(t))) < TIME_TOL_S
        assert a.drift_ppm == pytest.approx(drift, abs=DRIFT_TOL_PPM)
        assert a.confidence > 0.5
        assert a.reliable


@pytest.mark.parametrize("name", ["basic", "music"])
def test_presets(name):
    _assert_matches(synth.preset(name))


@pytest.mark.parametrize("signal", synth.SIGNALS)
def test_signals(signal):
    _assert_matches(
        Scenario(
            signal=signal,
            seed=7,
            devices=(
                Device("ref", (Clip(0.0, 40.0),)),
                Device(
                    "cam",
                    (Clip(5.5, 30.0),),
                    sample_rate=44_100,
                    drift_ppm=-60.0,
                    snr_db=20.0,
                    highpass_hz=150.0,
                    rt60_s=0.5,
                ),
            ),
        )
    )


def test_other_starts_before_reference_and_both_clocks_drift():
    _assert_matches(
        Scenario(
            seed=2,
            devices=(
                Device("ref", (Clip(10.0, 40.0),), drift_ppm=25.0),
                Device("cam", (Clip(0.0, 30.0),), drift_ppm=-30.0, snr_db=25.0),
                Device("late", (Clip(35.0, 30.0),), drift_ppm=80.0, snr_db=25.0),
            ),
        )
    )


def test_short_clip():
    _assert_matches(
        Scenario(
            seed=4,
            devices=(
                Device("ref", (Clip(0.0, 60.0),)),
                Device("cam", (Clip(21.3, 3.0),), snr_db=25.0),
            ),
        )
    )


def test_detects_inverted_polarity():
    scenario = Scenario(
        seed=5,
        devices=(
            Device("ref", (Clip(0.0, 30.0),)),
            Device("mic", (Clip(2.0, 20.0),), invert=True, snr_db=25.0),
        ),
    )
    (result,) = [a for a, *_ in _run(scenario)]
    assert result.inverted
    (result,) = [
        a
        for a, *_ in _run(
            Scenario(
                seed=5,
                devices=scenario.devices[:1] + (Device("mic", (Clip(2.0, 20.0),), snr_db=25.0),),
            )
        )
    ]
    assert not result.inverted


def test_unrelated_recordings_have_low_confidence():
    rng = np.random.default_rng(0)
    a = align.align(rng.standard_normal(30 * RATE), rng.standard_normal(20 * RATE))
    assert a.confidence < 0.1
    assert not a.reliable


def test_silent_file_is_rejected():
    with pytest.raises(ValueError):
        align.align(np.random.default_rng(0).standard_normal(10 * RATE), np.zeros(5 * RATE))


def test_expected_alignment_is_inverse_of_model():
    ref = Device("ref", (Clip(3.0, 10.0),), drift_ppm=40.0)
    cam = Device("cam", (Clip(5.0, 10.0),), drift_ppm=-25.0)
    offset, drift = synth.expected_alignment(3.0, 40.0, 5.0, -25.0)
    # a moment at true time T, seen from both files
    n_cam = 12_345.0
    true_t = cam.true_time(cam.clips[0], n_cam)
    ref_file_t = (true_t - 3.0) * (1 + 40e-6)
    a = align.Alignment(offset, drift, 1.0, False, 1, 1)
    assert a.ref_time(n_cam / cam.sample_rate) == pytest.approx(ref_file_t, abs=1e-12)
    assert ref.true_time(ref.clips[0], ref_file_t * ref.sample_rate) == pytest.approx(true_t)


# --- real-room behaviour (found on a live musical recording) ----------------

MUTE = -200.0  # dB: a source this device does not pick up


def _room(seed: int) -> Scenario:
    """A hall: the desk channel hears the band leader's close mic (plus a little band);
    the room recorder hears him faintly and late, the PA-amplified band early and an
    audience only it picks up. Which lag a window yields depends on who is loudest
    then. The fit used before RANSAC reported -12.7 and +7.7 ppm here instead of -8."""
    return Scenario(
        signal="speech",
        extra_signals=("music", "speech"),
        seed=seed,
        devices=(
            Device("desk", (Clip(0.0, 640.0),), pickup=((0.0, 0.0), (-20.0, 0.0), (MUTE, 0.0))),
            Device(
                "zoom",
                (Clip(20.0, 600.0),),
                drift_ppm=-8.0,
                snr_db=10.0,
                rt60_s=1.5,
                pickup=((-18.0, 25.0), (0.0, 8.0), (0.0, 3.0)),
            ),
        ),
    )


@pytest.mark.parametrize("seed", [1, 2])
def test_room_with_several_sources(seed):
    ((a, offset, drift, _),) = _run(_room(seed))
    assert a.reliable
    assert a.drift_ppm == pytest.approx(drift, abs=0.1)
    # the result follows one acoustic path (a delayed sound sits earlier on the
    # reference), not a blend of them
    paths = [offset - d * 1e-3 for d in (25.0, 8.0)]
    assert min(abs(a.offset_s - p) for p in paths) < 1e-4


def _two_tracks() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(unrelated track, matching track, other file) at the analysis rate."""
    scenario = Scenario(
        signal="speech",
        extra_signals=("speech",),
        seed=12,
        devices=(
            Device("desk", (Clip(0.0, 120.0),), pickup=((MUTE, 0.0), (0.0, 0.0))),
            Device("desk", (Clip(0.0, 120.0),), pickup=((0.0, 0.0), (MUTE, 0.0))),
            Device("cam", (Clip(10.0, 90.0),), drift_ppm=20.0, snr_db=20.0, rt60_s=0.5),
        ),
    )
    return tuple(
        audio.resample(rc.audio, rc.device.sample_rate, RATE) for rc in synth.render(scenario)
    )


def test_no_shared_signal_is_not_reliable():
    unrelated, _, cam = _two_tracks()
    try:
        a = align.align(unrelated, cam)
    except ValueError:
        return  # also fine: nothing plausible found at all
    assert not a.reliable
    assert a.confidence < RELIABLE_BELOW


def test_best_reference_track_is_chosen():
    unrelated, matching, cam = _two_tracks()
    index, a = align.align_best([unrelated, matching], cam)
    assert index == 1
    assert a.reliable
    assert a.offset_s == pytest.approx(10.0, abs=TIME_TOL_S)
    assert a.drift_ppm == pytest.approx(20.0, abs=DRIFT_TOL_PPM)


@pytest.mark.parametrize("wander_ms", [0.0, 4.0])
def test_wandering_clock_is_reported(wander_ms):
    scenario = Scenario(
        signal="noise",
        seed=13,
        devices=(
            Device("ref", (Clip(0.0, 620.0),)),
            Device("rec", (Clip(10.0, 600.0),), drift_ppm=-10.0, snr_db=20.0, wander_ms=wander_ms),
        ),
    )
    ((a, *_),) = _run(scenario)
    if wander_ms:
        assert a.wander_ms > 1.0
    else:
        assert a.wander_ms < 0.2
        assert a.drift_ppm == pytest.approx(-10.0, abs=DRIFT_TOL_PPM)


@needs_ffmpeg
def test_files_end_to_end(tmp_path, capsys):
    scenario = Scenario(
        seed=1,
        devices=(
            Device("rec", (Clip(0.0, 30.0),)),
            Device("cam", (Clip(4.2, 20.0),), sample_rate=44_100, drift_ppm=45.0, snr_db=25.0),
        ),
    )
    synth.write(scenario, tmp_path)
    loaded = audio.load(tmp_path / "cam_01.wav", RATE)
    assert len(loaded) == pytest.approx(20.0 * (1 + 45e-6) * RATE, abs=2)

    ((_, a),) = align.align_files([tmp_path / "rec_01.wav"], [tmp_path / "cam_01.wav"])
    assert a.offset_s == pytest.approx(4.2, abs=TIME_TOL_S)
    assert a.drift_ppm == pytest.approx(45.0, abs=DRIFT_TOL_PPM)

    assert main(["eval", str(tmp_path)]) == 0
    assert "cam_01.wav" in capsys.readouterr().out
    assert main(["analyze", str(tmp_path / "rec_01.wav"), str(tmp_path / "cam_01.wav")]) == 0
    row = next(r for r in capsys.readouterr().out.splitlines() if r.startswith("cam_01.wav"))
    assert float(row.split()[1]) == pytest.approx(4.2, abs=TIME_TOL_S)


@needs_ffmpeg
def test_missing_file_is_a_clean_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["analyze", str(tmp_path / "a.wav"), str(tmp_path / "b.wav")])
    assert e.value.code == 1
