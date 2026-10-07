"""The desktop app, headless: step 1 (Dateien) and the child-process jobs."""

import os
import shutil
import time
from fractions import Fraction
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="the app's extra (uv sync --extra gui) is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from chronon import audio, devices, synth  # noqa: E402
from chronon.gui import devices_page, fmt  # noqa: E402
from chronon.gui.files_page import UnreadableDialog  # noqa: E402
from chronon.gui.jobs import Job  # noqa: E402
from chronon.gui.project import media_files  # noqa: E402
from chronon.gui.window import Window  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(app, condition, seconds: float = 60.0) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "timed out"
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)


def _info(**kw) -> audio.Info:
    return audio.Info(**({"sample_rate": 48_000, "channels": 2, "bits": 24, "frames": 0} | kw))


def test_numbers_look_german():
    assert fmt.duration(3 * 3600 + 58 * 60 + 41.4) == "3:58:41"
    assert fmt.duration(50) == "0:00:50"
    assert fmt.rate(48_000) == "48 kHz" and fmt.rate(44_100) == "44,1 kHz"
    assert fmt.files(1) == "1 Datei" and fmt.files(56) == "56 Dateien"
    video = _info(has_video=True, timecode="10:42:13:08", frame=Fraction(1, 25))
    assert fmt.start(video) == "10:42:13:08"
    bwf = _info(has_video=False, time_reference=(3 * 25 + 12) * 48_000 // 25)  # 3 s 12 frames
    assert fmt.start(bwf) == "00:00:03:12"
    assert fmt.start(_info(has_video=False)) == "–"


def test_folders_bring_their_media(tmp_path):
    (tmp_path / "zoom" / "sub").mkdir(parents=True)
    for name in ["zoom/ZOOM0001.WAV", "zoom/sub/b.mp4", "zoom/.hidden.wav", "zoom/ZOOM.XML"]:
        (tmp_path / name).write_bytes(b"")
    loose = tmp_path / "loose.m4a"
    loose.write_bytes(b"")
    found = media_files([tmp_path / "zoom", loose, tmp_path / "zoom" / "ZOOM0001.WAV"])
    names = [p.relative_to(tmp_path).as_posix() for p in found]
    assert names == ["zoom/ZOOM0001.WAV", "zoom/sub/b.mp4", "loose.m4a"]


@needs_ffmpeg
def test_files_step_reads_lists_and_asks_about_unreadable_files(app, tmp_path):
    synth.write(synth.preset("basic"), tmp_path / "media")
    (tmp_path / "media" / "truth.json").unlink()
    broken = tmp_path / "R62_0041.MP4"
    broken.write_bytes(b"\0" * 5000)
    win = Window(appearance="light")
    assert not win.main.isEnabled() and not win.back.isVisible()

    win.files.add([tmp_path / "media", broken])
    assert win.files.reading and win.back.text() == "Abbrechen"
    _wait(app, lambda: not win.files.reading and win.files.dialog is not None)

    rows = {e.path.name: e for e in win.project.entries}
    assert set(rows) == {"camera_01.wav", "recorder_01.wav", "R62_0041.MP4"}
    model = win.files.model
    texts = [[model.text(e, c) for c in range(6)] for e in win.project.entries]
    assert ["recorder_01.wav", "0:01:00", "48 kHz", "1", "–", ""] in texts
    assert texts[-1][0] == "R62_0041.MP4" and texts[-1][5] == "nicht lesbar"

    dialog = win.files.dialog
    assert dialog.entry.path == broken and "ffmpeg:" in dialog.detail.text()
    assert str(broken) not in dialog.detail.text()  # the path is not repeated
    dialog.done(UnreadableDialog.REMOVE)
    assert [e.path.name for e in win.project.entries] == ["camera_01.wav", "recorder_01.wav"]
    assert win.header_right.text() == "2 Dateien"
    assert win.main.isEnabled() and win.main.text() == "Weiter: Geräte && Referenz"
    win.go_on()
    assert win.step == 1 and win.title.text() == "Geräte & Referenz"


@needs_ffmpeg
def test_cancel_drops_what_was_not_read(app, tmp_path):
    synth.write(synth.preset("multiclip"), tmp_path)
    win = Window(appearance="dark")
    win.files.add([tmp_path])
    win.go_back()  # "Abbrechen" while reading
    _wait(app, lambda: not win.files.reading)
    assert all(e.info is not None or e.error for e in win.project.entries)


@needs_ffmpeg
def test_jobs_run_chronon_in_a_child_process(app, tmp_path):
    synth.write(synth.preset("basic"), tmp_path)
    files = [str(tmp_path / "recorder_01.wav"), str(tmp_path / "camera_01.wav")]
    seen: dict[str, object] = {}
    job = Job(["devices", *files])
    job.result.connect(lambda e: seen.setdefault("result", e))
    job.failed.connect(lambda e: seen.setdefault("failed", e))
    job.start()
    _wait(app, lambda: seen)
    assert "result" in seen, seen
    assert [Path(r["file"]).name for r in seen["result"]["files"]] == [
        "recorder_01.wav",
        "camera_01.wav",
    ]

    seen.clear()
    plan: list = []
    job = Job(["analyze", files[0], str(tmp_path / "missing.wav")])
    job.plan.connect(plan.append)
    job.failed.connect(lambda e: seen.setdefault("failed", e))
    job.start()
    _wait(app, lambda: seen)
    assert seen["failed"]["code"] == "unreadable_file"
    job.wait()


def _device(name, *clips):
    return devices.Device(name, [devices.Clip([Path(t) for t in c]) for c in clips])


def test_device_texts():
    desk = _device("Pult", [f"/a/{n}.wav" for n in range(18)])
    zoom = _device("ZOOM", ["/z/3.WAV"], ["/z/4.WAV"])
    cam = _device("Kamera", ["/c/1.MP4"], ["/c/2.MP4"])
    infos = {f: _info(has_video=False, channels=1) for f in desk.files}
    infos |= {f: _info(has_video=False) for f in zoom.files}
    infos |= {f: _info(has_video=True) for f in cam.files}
    assert devices_page.kind(desk, infos) == "18 Spuren parallel"
    assert devices_page.kind(zoom, infos) == "2 Clips, Stereo"
    assert devices_page.kind(cam, infos) == "2 Videoclips"
    assert devices_page.rate(desk, infos) == "48 kHz"

    layout = devices.Layout([desk, zoom, cam], 0, desk.files[:2])
    assert devices_page.name_problem("zoom", layout) == "Ein Gerät heißt schon „ZOOM“."
    assert devices_page.name_problem("zoom", layout, keep=zoom) == ""
    assert "Zeichen" in devices_page.name_problem("A/B", layout)
    assert devices_page.name_problem(" ", layout) == "Der Name fehlt."


def _scene_with_clips(folder: Path) -> None:
    scenario = synth.Scenario(
        signal="speech",
        seed=7,
        devices=(
            synth.Device("rec", (synth.Clip(0.0, 60.0),)),
            synth.Device("cam", (synth.Clip(5.0, 15.0), synth.Clip(25.0, 20.0)), drift_ppm=20),
            synth.Device("phone", (synth.Clip(10.0, 30.0),), sample_rate=44_100),
        ),
    )
    synth.write(scenario, folder)
    (folder / "truth.json").unlink()


@needs_ffmpeg
def test_devices_step_edits_the_layout(app, tmp_path):
    _scene_with_clips(tmp_path)
    win = Window(appearance="light")
    win.files.add([tmp_path])
    _wait(app, lambda: not win.files.reading)
    win.go_on()
    page = win.devices
    assert win.step == 1 and page.detecting and not win.main.isEnabled()
    _wait(app, lambda: not page.detecting)
    layout = win.project.layout
    assert [d.name for d in layout.devices] == ["cam", "phone", "rec"]
    assert layout.devices[layout.reference].name == "rec" and layout.suggested == 2
    assert win.main.text() == "Sync starten" and win.main.isEnabled()
    assert win.header_right.text() == "4 Dateien · 3 Geräte"

    # rename with a description; a taken name is refused
    page.start_rename(layout.devices[0])
    assert page.editing is layout.devices[0]
    layout.devices[0].name, layout.devices[0].description = "Kamera", "Sony"
    page.editing = None
    page.rebuild()

    # the camera's second clip becomes a device of its own, then joins the phone
    second = layout.devices[0].files[1]
    page.split_off([second])
    assert [d.name for d in layout.devices] == ["Kamera", "cam_02", "phone", "rec"]
    page.move_to_device([second])
    dialog = page.dialog
    assert dialog.target == 0  # the first device that can take it
    dialog.group.button(dialog.targets.index(2)).setChecked(True)
    dialog.accept()
    assert [d.name for d in layout.devices] == ["Kamera", "phone", "rec"]
    assert second in layout.devices[1].files

    # merge the phone into the camera, under a new name
    page.merge(1)
    page.dialog.new_name.setText("Kameras")
    assert page.dialog.ok.isEnabled()
    page.dialog.accept()
    assert [d.name for d in layout.devices] == ["Kameras", "rec"]
    assert len(layout.devices[0].clips) == 3

    # order and reference
    page.move_device(1, 0)
    assert [d.name for d in layout.devices] == ["rec", "Kameras"] and layout.reference == 0
    page.set_reference(1)
    assert layout.devices[1].is_reference and len(layout.tracks) == 1
    clip = layout.devices[1].clips[1].tracks[0]
    page.toggle_track(clip)
    assert layout.tracks == [clip]
    layout.check()
    assert win.main.isEnabled()
