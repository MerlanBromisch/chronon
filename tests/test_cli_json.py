"""``--json``: the contract between the desktop app and the core (docs/app.md)."""

import json
import shutil

import pytest

from chronon import correct, synth
from chronon.cli import main
from chronon.synth import Clip, Device, Scenario

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

START = 3.5
DRIFT = 40.0


def _scene(tmp_path):
    scenario = Scenario(
        signal="noise",
        seed=21,
        devices=(
            Device("rec", (Clip(0.0, 30.0),)),
            Device("cam", (Clip(START, 20.0),), drift_ppm=DRIFT),
        ),
    )
    synth.write(scenario, tmp_path / "media")
    return tmp_path / "media" / "rec_01.wav", tmp_path / "media" / "cam_01.wav"


def _events(capsys) -> list[dict]:
    """Every stdout line must be one JSON object of contract version 1."""
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events and all(e["v"] == 1 for e in events)
    return events


def test_analyze_json(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    assert main(["analyze", "--json", str(rec), str(cam)]) == 0
    events = _events(capsys)
    assert {e["event"] for e in events[:-1]} == {"progress"}
    progress = [e["done"] for e in events[:-1]]
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert all(e["step"] == "analysing" and e["steps"] == 1 for e in events[:-1])

    result = events[-1]
    assert result["event"] == "result" and result["command"] == "analyze"
    assert result["refs"] == [str(rec)]
    (row,) = result["files"]
    assert row["file"] == str(cam) and row["reliable"]
    assert row["offset_s"] == pytest.approx(START, abs=1e-4)
    assert row["drift_ppm"] == pytest.approx(DRIFT, abs=0.5)
    assert row["via"] is None and isinstance(row["notes"], list)


def test_sync_json_and_versioned_report(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    out = tmp_path / "out"
    assert main(["sync", "--json", str(rec), str(cam), "-o", str(out)]) == 0
    result = _events(capsys)[-1]
    assert result["command"] == "sync"
    assert result["timeline"] == str(out / "out.fcpxml")
    assert result["report"] == str(out / "chronon-sync.json")
    assert [r["source"] for r in result["files"]] == [str(rec), str(cam)]

    report = json.loads((out / "chronon-sync.json").read_text())
    assert report["schema"] == correct.REPORT_SCHEMA and report["kind"] == "sync"
    assert report["files"] == result["files"]


def test_correct_json_steps(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    out = tmp_path / "out"
    assert main(["correct", "--json", str(rec), str(cam), "-o", str(out)]) == 0
    events = _events(capsys)
    steps = [e["step"] for e in events if e["event"] == "progress"]
    assert list(dict.fromkeys(steps)) == ["analysing", "writing", "verifying"]
    result = events[-1]
    assert result["command"] == "correct" and result["failed"] == 0
    assert [r["verified"] for r in result["files"]] == [None, True]
    assert correct.read_report(result["report"]) == result["files"]


def test_errors_are_events(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    assert main(["correct", "--json", str(rec), str(cam), "-o", str(rec.parent)]) == 1
    (event,) = _events(capsys)
    assert event["event"] == "error" and "original" in event["message"].lower()

    assert main(["analyze", "--json", str(rec), str(tmp_path / "missing.wav")]) == 1
    assert _events(capsys)[-1]["event"] == "error"


def test_timeline_reads_old_unversioned_reports(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    out = tmp_path / "out"
    assert main(["sync", str(rec), str(cam), "-o", str(out)]) == 0
    report = out / "chronon-sync.json"
    report.write_text(json.dumps(correct.read_report(report)))  # schema 0: a bare list
    assert main(["timeline", str(out), "--name", "again"]) == 0
    assert (out / "again.fcpxml").exists()

    report.write_text(json.dumps({"schema": correct.REPORT_SCHEMA + 1, "files": []}))
    with pytest.raises(correct.CorrectError, match="newer"):
        correct.read_report(report)
