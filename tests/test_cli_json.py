"""``--json``: the contract between the desktop app and the core (docs/app.md)."""

import json
import shutil

import pytest

from chronon import correct, messages, synth
from chronon.cli import _JsonOut, main
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
    """Every stdout line must be one JSON object of the current contract version."""
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events and all(e["v"] == _JsonOut.VERSION == 2 for e in events)
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
    assert event["code"] == "outdir_holds_input"
    assert event["folder"] == str(rec.parent) and event["file"] in {str(rec), str(cam)}

    missing = tmp_path / "missing.wav"
    assert main(["analyze", "--json", str(rec), str(missing)]) == 1
    event = _events(capsys)[-1]
    assert event["event"] == "error"
    assert event["code"] == "unreadable_file" and event["file"] == str(missing)

    assert main(["analyze", "--json", str(rec)]) == 1  # a usage error has no code
    event = _events(capsys)[-1]
    assert event["code"] is None and event["message"]


def test_notes_are_codes(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    out = tmp_path / "out"
    assert main(["correct", "--json", str(rec), str(cam), "-o", str(out)]) == 0
    rec_row, cam_row = _events(capsys)[-1]["files"]
    assert rec_row["notes"] == [{"code": "reference_clock"}]
    assert all(set(n) >= {"code"} and n["code"] in messages.TEXTS for n in cam_row["notes"])

    # the terminal builds its text from the same codes
    assert messages.text({"code": "measured_via", "file": str(cam)}) == f"measured via {cam.name}"
    assert messages.text({"code": "clock_wanders", "ms": 1.84}) == "clock wanders ±1.8 ms"


def test_every_note_code_has_a_text():
    for code, text in messages.TEXTS.items():
        n = {"code": code, "file": "/a/b.wav", "files": ["/a/b.wav"], "ms": 1.0}
        n |= {"reason": "r", "text": "t"}
        assert text(n)
    with pytest.raises(KeyError):
        messages.note("no_such_code")


def test_timeline_reads_old_unversioned_reports(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    out = tmp_path / "out"
    assert main(["sync", str(rec), str(cam), "-o", str(out)]) == 0
    report = out / "chronon-sync.json"
    rows = correct.read_report(report)
    rows[0]["notes"] = ["same clock and start as the reference"]  # notes were text before
    report.write_text(json.dumps(rows))  # schema 0: a bare list
    assert main(["timeline", str(out), "--name", "again"]) == 0
    assert (out / "again.fcpxml").exists()
    assert correct.read_report(report)[0]["notes"] == [
        {"code": "text", "text": "same clock and start as the reference"}
    ]

    report.write_text(json.dumps({"schema": correct.REPORT_SCHEMA + 1, "files": []}))
    with pytest.raises(correct.CorrectError, match="newer"):
        correct.read_report(report)


def test_log_file_holds_the_run(tmp_path, capsys):
    rec, cam = _scene(tmp_path)
    log = tmp_path / "logs" / "run.log"
    assert main(["analyze", "--json", "--log", str(log), str(rec), str(cam)]) == 0
    text = log.read_text(encoding="utf-8")
    assert "chronon " in text and "ffmpeg" in text and "command: chronon analyze" in text
    assert f"{cam.name} (" in text and "drift +40." in text and "analysing done" in text
    capsys.readouterr()

    missing = tmp_path / "missing.wav"
    assert main(["analyze", "--json", "--log", str(log), str(rec), str(missing)]) == 1
    text = log.read_text(encoding="utf-8")  # a new protocol per run
    assert "command: chronon analyze" in text and f"{cam.name} (" not in text
    assert "ERROR" in text and "missing.wav" in text and "Traceback" in text
