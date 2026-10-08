"""Listening and waveforms: excerpts, reference time to file time, cached overviews."""

import json
import os
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf
from scipy import signal as sps

from chronon import analysis, audio, listen, synth
from chronon.cli import main
from chronon.synth import Clip, Device, Scenario

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

RATE = 48_000


def _scene(tmp_path):
    """A recorder, a camera at 44.1 kHz with a fast clock, and a phone with two clips."""
    scenario = Scenario(
        signal="noise",
        seed=5,
        devices=(
            Device("rec", (Clip(0.0, 40.0),)),
            Device("cam", (Clip(4.0, 20.0),), sample_rate=44_100, drift_ppm=60.0),
            Device("phone", (Clip(2.0, 12.0), Clip(20.0, 15.0)), drift_ppm=-25.0),
        ),
    )
    media = tmp_path / "media"
    synth.write(scenario, media)
    return scenario, media


def _aac(src, dst):
    cmd = ["ffmpeg", "-v", "error", "-i", str(src), "-c:a", "aac", "-b:a", "192k", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def _lag(a: np.ndarray, b: np.ndarray) -> int:
    """Samples ``b`` lags behind ``a``."""
    c = sps.correlate(b, a, mode="full", method="fft")
    return int(np.argmax(np.abs(c))) - (len(a) - 1)


def test_excerpt_is_exact_and_fast_paths_agree(tmp_path):
    _, media = _scene(tmp_path)
    cam = media / "cam_01.wav"  # 44.1 kHz: resampled
    whole = audio.load(cam, RATE)
    x = audio.excerpt(cam, 5.25, 1.0, RATE)
    assert x.dtype == np.float32 and len(x) == RATE
    assert _lag(whole[round(5.25 * RATE) : round(6.25 * RATE)], x) == 0
    # ffmpeg (for video and compressed audio) cuts at the same place
    y = audio._excerpt_ffmpeg(cam, 5.25, 1.0, RATE, RATE)
    assert _lag(x, y) == 0 and np.corrcoef(x, y)[0, 1] > 0.99
    # a compressed copy, decoded by ffmpeg from shortly before (near the start too, where
    # newer ffmpeg seeks wrong)
    m4a = _aac(cam, tmp_path / "cam.m4a")
    for start in (0.0, 0.5, 2.7, 5.25, 17.0):
        x = audio.excerpt(cam, start, 1.0, RATE)
        z = audio.excerpt(m4a, start, 1.0, RATE)
        assert _lag(x, z) == 0 and np.corrcoef(x, z)[0, 1] > 0.9, start


@pytest.mark.parametrize("compressed", [False, True])
def test_excerpt_pads_outside_the_recording(tmp_path, compressed):
    _, media = _scene(tmp_path)
    rec = media / "rec_01.wav"
    if compressed:
        rec = _aac(rec, tmp_path / "rec.m4a")
    before = audio.excerpt(rec, -0.5, 1.0, RATE)
    assert len(before) == RATE and not before[: RATE // 2 - 200].any()
    assert np.abs(before[RATE // 2 + 200 :]).mean() > 0.01
    after = audio.excerpt(rec, 39.5, 1.0, RATE)
    assert len(after) == RATE and not after[RATE // 2 + 200 :].any()
    assert not audio.excerpt(rec, -5.0, 1.0, RATE).any()
    assert not audio.excerpt(rec, 50.0, 1.0, RATE).any()


def test_timeline_maps_reference_time_to_every_file(tmp_path):
    scenario, media = _scene(tmp_path)
    rec, cam = media / "rec_01.wav", media / "cam_01.wav"
    phone1, phone2 = media / "phone_01.wav", media / "phone_02.wav"
    measured = analysis.measure([rec], [cam, phone1, phone2])
    t = listen.Timeline(measured)
    assert t.zero == 0.0

    # the camera at reference time 10 s: 6 s after its start, on its fast clock
    _, drift = synth.expected_alignment(0.0, 0.0, 4.0, 60.0)
    assert t.file_time(cam, 10.0) == pytest.approx(6.0 * (1 + drift * 1e-6), abs=1e-4)
    assert t.span(cam)[0] == pytest.approx(4.0, abs=1e-4)

    # a device's clips: which one plays when
    assert [s.path for s in t.at(5.0, "phone")] == [phone1]
    assert [s.path for s in t.at(17.0, "phone")] == []
    (spot,) = t.at(25.0, "phone")
    assert spot.path == phone2 and spot.time_s == pytest.approx(5.0 * (1 - 25e-6), abs=1e-4)
    assert {s.path for s in t.at(10.0)} == {rec, cam, phone1}

    # reference left, file right: in sync where they start (the file plays at its own
    # rate, so by the end of 2 s a 60 ppm clock is 0.12 ms ahead)
    start = t.suggest(cam, 2.0)
    assert t.span(cam)[0] <= start and start + 2.0 <= t.span(cam)[1]
    stereo = t.pair(cam, start, 2.0)
    assert stereo.shape == (2 * RATE, 2)
    head = stereo[: RATE // 5]
    assert abs(_lag(head[:, 0], head[:, 1])) <= 1
    late = t.pair(phone2, 30.0, 0.2)
    assert abs(_lag(late[:, 0], late[:, 1])) <= 1

    with pytest.raises(KeyError, match="not part"):
        t.file_time(media / "other.wav", 1.0)


def test_overview_is_cached_and_follows_the_file(tmp_path, monkeypatch):
    _, media = _scene(tmp_path)
    cam = media / "cam_01.wav"
    cache = tmp_path / "cache"
    peaks = listen.overview(cam, cache)
    x, rate = sf.read(cam, dtype="float32", always_2d=True)
    bins = -(-len(x) * listen.PEAKS_PER_S // rate)  # the last one partly filled
    assert peaks.dtype == np.int8 and peaks.shape == (bins, 2)
    bin_ = rate // listen.PEAKS_PER_S
    first = x[: 3 * bin_, 0].reshape(3, bin_)
    expect = np.round(np.column_stack([first.min(axis=1), first.max(axis=1)]) * 127)
    assert np.array_equal(peaks[:3], expect.astype(np.int8))
    assert (peaks[:, 0] <= peaks[:, 1]).all()

    def boom(*args):
        raise AssertionError("computed again")

    monkeypatch.setattr(listen, "_peaks", boom)
    assert np.array_equal(listen.overview(cam, cache), peaks)  # from the cache
    old = listen.overview_path(cam, cache)
    assert old.exists()
    st = os.stat(cam)
    os.utime(cam, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert listen.overview_path(cam, cache) != old  # a changed file gets a new overview

    narrow = listen.reduce(peaks, 100)
    assert narrow.shape == (100, 2) and narrow.dtype == np.float32
    assert narrow[:, 0].min() == pytest.approx(peaks[:, 0].min() / 127)
    assert narrow[:, 1].max() == pytest.approx(peaks[:, 1].max() / 127)


def test_overview_of_compressed_audio(tmp_path):
    _, media = _scene(tmp_path)
    m4a = _aac(media / "rec_01.wav", tmp_path / "rec.m4a")
    peaks = listen.overview(m4a, tmp_path / "cache")
    assert abs(len(peaks) - 40 * listen.PEAKS_PER_S) <= 3
    assert np.abs(peaks).max() > 10


def test_overview_cli_json(tmp_path, capsys):
    _, media = _scene(tmp_path)
    rec = media / "rec_01.wav"
    cache = tmp_path / "cache"
    assert main(["overview", "--json", str(rec), "--cache", str(cache)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]["event"] == "plan" and [s["id"] for s in events[0]["steps"]] == ["overview"]
    assert {e["event"] for e in events[1:-1]} == {"progress"}
    (row,) = events[-1]["files"]
    assert row["file"] == str(rec) and row["peaks"] == 40 * listen.PEAKS_PER_S
    assert np.load(row["overview"]).shape == (row["peaks"], 2)


def test_without_a_reliable_match_listen_where_both_play(tmp_path):
    scenario = Scenario(
        signal="speech",
        extra_signals=("speech",),
        seed=5,
        devices=(
            Device("rec", (Clip(0.0, 120.0),), pickup=((0.0, 0.0), (-200.0, 0.0))),
            Device("other", (Clip(20.0, 60.0),), pickup=((-200.0, 0.0), (0.0, 0.0))),
        ),
    )
    synth.write(scenario, tmp_path)
    # a file that matches nothing is no error: it gets a row without a reliable match
    measured = analysis.measure([tmp_path / "rec_01.wav"], [tmp_path / "other_01.wav"])
    assert not measured.results[0].alignment.reliable
    t = listen.Timeline(measured)
    other = tmp_path / "other_01.wav"
    start, end = t.span(other)
    lo, hi = max(start, 0.0), min(end, 120.0)
    assert t.suggest(other, 2.0) == pytest.approx((lo + hi) / 2 - 1.0)


def test_window_of_an_overview_is_silent_outside_the_file():
    peaks = np.ones((500, 2), dtype=np.int8)  # 5 s
    part = listen.window(peaks, -1.0, 2.0)
    assert len(part) == 300
    assert not part[:100].any() and part[100:].all()
    assert (
        len(listen.window(peaks, 4.0, 6.0)) == 200
        and not listen.window(peaks, 4.0, 6.0)[100:].any()
    )
