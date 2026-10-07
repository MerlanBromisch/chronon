"""Analyse once, export later: a saved analysis replaces the measurement in sync / correct."""

import json
import os
import shutil
import subprocess

import pytest

from chronon import align, analysis, correct, synth
from chronon.cli import main
from chronon.synth import Clip, Device, Scenario

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

START = 2.5
DRIFT = 30.0


def _scene(tmp_path, video: bool = False):
    scenario = Scenario(
        signal="noise",
        seed=8,
        devices=(
            Device("rec", (Clip(0.0, 20.0),)),
            Device("cam", (Clip(START, 12.0),), drift_ppm=DRIFT),
        ),
    )
    media = tmp_path / "media"
    synth.write(scenario, media)
    cam = media / "cam_01.wav"
    if video:
        mov = media / "cam.mov"
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=25"]
        cmd += ["-i", str(cam), "-shortest", "-c:v", "mpeg4", "-c:a", "pcm_s16le", str(mov)]
        subprocess.run(cmd, check=True)
        cam = mov
    return media / "rec_01.wav", cam


def _no_measuring(monkeypatch):
    """From here on, fail if anything measures the files again."""
    monkeypatch.setattr(align, "align_files", lambda *a, **k: pytest.fail("measured again"))


def test_saved_analysis_round_trips(tmp_path):
    rec, cam = _scene(tmp_path)
    measured = analysis.measure([rec], [cam])
    loaded = analysis.Analysis.load(measured.save(tmp_path / "a.json"))
    assert loaded.refs == [rec] and loaded.files == [cam]
    assert loaded.results == measured.results
    assert loaded.results[0].alignment.good_s == measured.results[0].alignment.good_s
    assert loaded.placements == measured.placements
    assert loaded.frame_rate == measured.frame_rate == 25
    loaded.check_sources()


def test_sync_from_a_saved_analysis_matches_a_fresh_one(tmp_path, monkeypatch):
    rec, cam = _scene(tmp_path)
    assert main(["sync", str(rec), str(cam), "-o", str(tmp_path / "fresh")]) == 0
    assert main(["analyze", str(rec), str(cam), "--save", str(tmp_path / "a.json")]) == 0

    _no_measuring(monkeypatch)
    out = tmp_path / "saved"
    assert main(["sync", "--analysis", str(tmp_path / "a.json"), "-o", str(out)]) == 0
    fresh = correct.read_report(tmp_path / "fresh" / "chronon-sync.json")
    assert correct.read_report(out / "chronon-sync.json") == fresh
    xml = (out / "saved.fcpxml").read_text(encoding="utf-8")
    assert xml == (tmp_path / "fresh" / "fresh.fcpxml").read_text(encoding="utf-8").replace(
        "fresh", "saved"
    )


def test_correct_from_a_saved_analysis(tmp_path, capsys, monkeypatch):
    rec, cam = _scene(tmp_path)
    saved = analysis.measure([rec], [cam]).save(tmp_path / "a.json")
    _no_measuring(monkeypatch)
    out = tmp_path / "out"
    assert main(["correct", "--json", "--analysis", str(saved), "-o", str(out)]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["command"] == "correct" and result["failed"] == 0
    assert [r["verified"] for r in result["files"]] == [None, True]


def test_analyze_json_reports_the_saved_file_and_video_placement(tmp_path, capsys):
    rec, cam = _scene(tmp_path, video=True)
    saved = tmp_path / "a.json"
    assert main(["analyze", "--json", "--save", str(saved), str(rec), str(cam)]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["analysis"] == str(saved) and result["frame_rate"] == "25"
    (row,) = result["files"]
    p = row["placement"]
    assert p["has_video"] and p["video_error_ms"] is not None and p["video_error_ms"] < 20
    assert p["position_s"] == pytest.approx(START, abs=1e-3)
    assert p["duration_s"] == pytest.approx(12.0, abs=0.1)


def test_changed_or_missing_sources_refuse_the_export(tmp_path):
    rec, cam = _scene(tmp_path)
    saved = analysis.measure([rec], [cam]).save(tmp_path / "a.json")

    st = os.stat(cam)
    os.utime(cam, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    with pytest.raises(analysis.AnalysisError, match="changed since the analysis") as e:
        correct.sync([], [], tmp_path / "out", measured=analysis.Analysis.load(saved))
    assert e.value.code == "source_changed" and e.value.fields == {"file": str(cam)}

    cam.unlink()
    with pytest.raises(analysis.AnalysisError, match="missing") as e:
        correct.run([], [], tmp_path / "out", measured=analysis.Analysis.load(saved))
    assert e.value.code == "source_missing"


def test_files_and_analysis_do_not_mix(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    saved = analysis.measure([rec], [cam]).save(tmp_path / "a.json")
    args = ["sync", "--json", "--analysis", str(saved), str(cam), "-o", str(tmp_path / "out")]
    assert main(args) == 1
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert event["event"] == "error" and "either" in event["message"]

    (tmp_path / "x.json").write_text('{"kind": "report"}', encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="not a Chronon analysis"):
        analysis.Analysis.load(tmp_path / "x.json")
