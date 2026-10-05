import shutil
from pathlib import Path

import numpy as np
import pytest

from chronon import align, audio, devices, synth
from chronon.synth import Clip, Device, Scenario

MUTE = -200.0


def _info(frames=1000, rate=48_000, ref=None, codec="pcm_s24le", recorder="ZOOM H5", ch=2):
    return audio.Info(rate, ch, 24, frames, False, codec, ref, recorder)


def _group(refs, files, infos, separate=False):
    table = {Path(k): v for k, v in infos.items()}
    return devices.group(refs, files, separate, probe=lambda p: table[Path(p)])


def test_parallel_tracks_form_one_clip():
    infos = {
        f"/x/Ohne Namen_{i}#01.aif": _info(622_468, 44_100, codec="pcm_s24be", recorder="")
        for i in (1, 2, 3)
    }
    infos["/desk/ch1.wav"] = _info(500)
    ds = _group(["/desk/ch1.wav"], list(infos)[:3], infos)
    assert len(ds) == 2
    assert len(ds[1].clips) == 1 and len(ds[1].clips[0].tracks) == 3
    assert ds[1].name == "Ohne Namen"


def test_tracks_parallel_to_the_reference_join_it():
    zoom = {
        f"/z/ZOOM0001_{t}.WAV": _info(78_867_008, ref=137_760_000, ch=1) for t in ("Tr1", "Tr2")
    }
    infos = {
        "/z/ZOOM0001_TrLR.WAV": _info(78_867_008, ref=137_760_000),
        **zoom,
        "/l/Merlan #01.wav": _info(78_626_458, ref=172_800_000, recorder="Logic Pro", ch=1),
    }
    ds = _group(["/z/ZOOM0001_TrLR.WAV"], [*zoom, "/l/Merlan #01.wav"], infos)
    assert ds[0].is_reference and len(ds[0].files) == 3
    assert [d.files for d in ds[1:]] == [[Path("/l/Merlan #01.wav")]]


def test_numbered_files_of_one_recorder_are_clips_of_one_device():
    infos = {
        "/t/ZOOM0004.WAV": _info(314_614_976, ref=2),
        "/t/ZOOM0003.WAV": _info(357_892_096, ref=1),
        "/t/C2378.MP4": _info(9, codec="pcm_s16be", recorder="XAVC"),
        "/t/C2379.MP4": _info(8, codec="pcm_s16be", recorder="XAVC"),
        "/t/R62_0040.MP4": _info(7, codec="aac", recorder="mp42"),
        "/other/ZOOM0005.WAV": _info(5, ref=3),
        "/ref.wav": _info(1),
    }
    files = [f for f in infos if f != "/ref.wav"]
    ds = {d.name: d for d in _group(["/ref.wav"], files, infos)[1:]}
    assert [c.tracks[0].name for c in ds["ZOOM"].clips] == ["ZOOM0003.WAV", "ZOOM0004.WAV"]
    assert len(ds["C2378"].clips) == 2  # a one-letter prefix is named after its first clip
    assert ds["R62"].files == [Path("/t/R62_0040.MP4")]
    assert ds["ZOOM (other)"].files == [Path("/other/ZOOM0005.WAV")]  # another folder


def test_separate_keeps_every_file_apart():
    infos = {f"/t/ZOOM000{i}.WAV": _info(10 + i) for i in (3, 4)} | {"/ref.wav": _info(1)}
    ds = _group(["/ref.wav"], ["/t/ZOOM0003.WAV", "/t/ZOOM0004.WAV"], infos, separate=True)
    assert [len(d.clips) for d in ds] == [1, 1, 1]


# --- end to end on synthetic recordings ---------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@needs_ffmpeg
def test_parallel_tracks_are_measured_through_the_best_one(tmp_path):
    # two channels of one recorder: the first hears nothing the reference hears
    channel = dict(clips=(Clip(5.0, 60.0),), drift_ppm=25.0, snr_db=25.0)
    scenario = Scenario(
        signal="speech",
        extra_signals=("speech",),
        seed=41,
        devices=(
            Device("ref", (Clip(0.0, 80.0),), pickup=((0.0, 0.0), (MUTE, 0.0))),
            Device("chA", pickup=((MUTE, 0.0), (0.0, 0.0)), **channel),
            Device("chB", pickup=((0.0, 0.0), (-6.0, 0.0)), **channel),
        ),
    )
    synth.write(scenario, tmp_path)
    a_path, b_path = tmp_path / "chA_01.wav", tmp_path / "chB_01.wav"
    ra, rb = align.align_files([tmp_path / "ref_01.wav"], [a_path, b_path])
    assert ra.via == rb.via == b_path
    assert ra.alignment == rb.alignment
    assert rb.alignment.drift_ppm == pytest.approx(25.0, abs=1.0)
    assert rb.alignment.offset_s == pytest.approx(5.0, abs=1e-4)


@needs_ffmpeg
def test_a_short_weak_clip_takes_its_siblings_drift(tmp_path):
    drift = 30.0
    scenario = Scenario(
        signal="speech",
        seed=42,
        devices=(
            Device("ref", (Clip(0.0, 900.0),)),
            Device(
                "cam",
                (Clip(10.0, 700.0), Clip(800.0, 25.0)),
                drift_ppm=drift,
                snr_db=0.0,
                rt60_s=0.8,
            ),
        ),
    )
    synth.write(scenario, tmp_path)
    ref = tmp_path / "ref_01.wav"
    files = [tmp_path / "cam_01.wav", tmp_path / "cam_02.wav"]
    long, short = align.align_files([ref], files)
    alone = align.align_files([ref], files, separate=True)[1]
    assert short.drift_from == files[0] and long.drift_from is None
    assert short.alignment.drift_ppm == long.alignment.drift_ppm
    assert abs(short.alignment.drift_ppm - drift) < abs(alone.alignment.drift_ppm - drift)
    # every point of the short clip lands within 0.1 ms of where it belongs
    truth = align.Alignment(*synth.expected_alignment(0.0, 0.0, 800.0, drift), 1.0, False, 1, 1)
    t = np.array([0.0, 25.0])
    assert np.max(np.abs(short.alignment.ref_time(t) - truth.ref_time(t))) < 1e-4


@needs_ffmpeg
def test_a_clip_off_the_reference_is_linked_through_another_device(tmp_path):
    # desk 0-100 s, Zoom 50-300 s, camera 200-260 s: the camera never meets the desk
    scenario = Scenario(
        signal="speech",
        seed=43,
        devices=(
            Device("desk", (Clip(0.0, 100.0),)),
            Device("zoom", (Clip(50.0, 250.0),), drift_ppm=-20.0, snr_db=25.0, rt60_s=0.4),
            Device("cam", (Clip(200.0, 60.0),), sample_rate=44_100, drift_ppm=35.0, snr_db=20.0),
        ),
    )
    synth.write(scenario, tmp_path)
    zoom, cam = tmp_path / "zoom_01.wav", tmp_path / "cam_01.wav"
    rz, rc = align.align_files([tmp_path / "desk_01.wav"], [zoom, cam])
    assert rz.linked_via is None and rc.linked_via == zoom
    offset, drift = synth.expected_alignment(0.0, 0.0, 200.0, 35.0)
    truth = align.Alignment(offset, drift, 1.0, False, 1, 1)
    t = np.array([0.0, 60.0])
    assert np.max(np.abs(rc.alignment.ref_time(t) - truth.ref_time(t))) < 1e-4
    assert rc.alignment.drift_ppm == pytest.approx(35.0, abs=1.0)


@needs_ffmpeg
def test_linked_clips_are_corrected_and_verified(tmp_path):
    from chronon import correct

    scenario = Scenario(
        signal="speech",
        seed=44,
        devices=(
            Device("desk", (Clip(0.0, 100.0),)),
            Device("zoom", (Clip(50.0, 250.0),), drift_ppm=-20.0, snr_db=25.0),
            Device("cam", (Clip(200.0, 60.0),), drift_ppm=35.0, snr_db=20.0),
        ),
    )
    media = tmp_path / "media"
    synth.write(scenario, media)
    outputs = correct.run(
        [media / "desk_01.wav"], [media / "zoom_01.wav", media / "cam_01.wav"], tmp_path / "out"
    )
    cam = next(o for o in outputs if o.source.endswith("cam_01.wav"))
    assert cam.reference.endswith("zoom_01.wav")
    assert cam.verified
    assert cam.position_s == pytest.approx(200.0, abs=1e-3)
