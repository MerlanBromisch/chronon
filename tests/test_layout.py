"""Devices before a reference exists, edited by the user, and how their names travel."""

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from chronon import analysis, audio, correct, devices, synth
from chronon.cli import main
from chronon.synth import Clip, Device, Scenario


def _info(frames, rate=48_000, codec="pcm_s24le", recorder="", ch=1, ref=None):
    return audio.Info(rate, ch, 24, frames, False, codec, ref, recorder)


def test_detect_suggests_the_device_covering_the_most_time():
    infos = {Path(f"/x/Ohne Namen_{i}#01.aif"): _info(900_000) for i in (10, 2, 1)}
    infos[Path("/z/ZOOM0003.WAV")] = _info(500_000, ch=2, recorder="ZOOM")
    infos[Path("/z/ZOOM0004.WAV")] = _info(300_000, ch=2, recorder="ZOOM")
    picked = []

    def loudest(tracks, n):
        picked.append(list(tracks))
        return list(tracks)[-n:]

    layout = devices.detect(list(infos), probe=lambda p: infos[Path(p)], loudest=loudest)
    desk, zoom = layout.devices
    assert desk.name == "Ohne Namen" and zoom.name == "ZOOM"
    # tracks as people count them: _1, _2, _10
    assert [t.name for t in desk.clips[0].tracks] == [
        "Ohne Namen_1#01.aif",
        "Ohne Namen_2#01.aif",
        "Ohne Namen_10#01.aif",
    ]
    assert layout.suggested == layout.reference == 0  # 900 000 frames beat 800 000
    assert layout.tracks == desk.clips[0].tracks[-2:] and picked
    assert desk.is_reference and not zoom.is_reference


def test_set_names_end_on_a_whole_word():
    clip = devices.Clip([Path(f"/a/Musical 23.6.26_{i} #01.wav") for i in (1, 2)])
    assert devices._set_name(clip) == "Musical 23.6.26"
    clip = devices.Clip([Path(f"/a/ZOOM0001_{t}.WAV") for t in ("Tr1", "Tr2", "TrLR")])
    assert devices._set_name(clip) == "ZOOM0001"


def _layout() -> devices.Layout:
    desk = devices.Device("Pult", [devices.Clip([Path("/a/1.wav"), Path("/a/2.wav")])])
    cam = devices.Device("Kamera", [devices.Clip([Path("/b/C1.MP4")])])
    return devices.Layout([desk, cam], 0, [Path("/a/2.wav")])


def test_layout_round_trip(tmp_path):
    layout = _layout()
    layout.devices[0].description = "Mischpult"
    layout.devices[0].track_names = {"/a/2.wav": "Summe"}
    loaded = devices.Layout.load(layout.save(tmp_path / "d.json"))
    assert loaded.to_dict() == layout.to_dict()
    assert loaded.devices[0].track_label(Path("/a/2.wav")) == "Summe"
    assert loaded.devices[0].track_label(Path("/a/1.wav")) == "1"


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda lay: lay.devices[1].clips[0].tracks.append(Path("/a/1.wav")), "two devices"),
        (lambda lay: setattr(lay.devices[1], "name", "PULT"), "two devices are called"),
        (lambda lay: setattr(lay.devices[1], "name", "Cam/1"), "file name"),
        (lambda lay: setattr(lay, "tracks", [Path("/b/C1.MP4")]), "reference tracks"),
        (lambda lay: setattr(lay.devices[1], "clips", []), "no files"),
    ],
)
def test_layout_refuses_what_cannot_be_exported(edit, message):
    layout = _layout()
    edit(layout)
    with pytest.raises(devices.LayoutError, match=message):
        layout.check()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_user_names_and_order_reach_files_and_timeline(tmp_path, capsys):
    scenario = Scenario(
        signal="noise",
        seed=12,
        devices=(
            Device("desk", (Clip(0.0, 20.0),)),
            Device("cam", (Clip(3.0, 12.0),), drift_ppm=25.0),
        ),
    )
    media = tmp_path / "media"
    synth.write(scenario, media)
    # two parallel desk tracks: the same recording on two channels
    (media / "desk_01.wav").rename(media / "Desk_1.wav")
    shutil.copy(media / "Desk_1.wav", media / "Desk_2.wav")
    files = [media / "Desk_1.wav", media / "Desk_2.wav", media / "cam_01.wav"]

    assert main(["devices", "--json", *map(str, files), "--save", str(tmp_path / "d.json")]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert [d["kind"] for d in result["devices"]] == ["tracks", "file"]

    layout = devices.Layout.load(tmp_path / "d.json")
    desk, cam = layout.devices
    desk.name, cam.name = "Pult", "Kamera"
    desk.track_names = {str(media / "Desk_1.wav"): "Summe"}
    layout.devices = [cam, desk]  # the camera's lane first
    layout.reference, layout.tracks = 1, [media / "Desk_1.wav"]

    measured = analysis.measure([], [], layout=layout)
    (cam_result,) = [
        r for f, r in zip(measured.files, measured.results, strict=True) if f.name == "cam_01.wav"
    ]
    assert cam_result.device == "Kamera" and cam_result.alignment.reliable
    assert cam_result.alignment.offset_s == pytest.approx(3.0, abs=1e-3)

    out = tmp_path / "out"
    outputs = correct.run(
        [], [], out, measured=analysis.Analysis.load(measured.save(tmp_path / "a"))
    )
    names = sorted(Path(o.path).name for o in outputs)
    assert names == ["Kamera_korrigiert.wav", "Pult_2_korrigiert.wav", "Pult_Summe_korrigiert.wav"]
    lanes = {c.get("name"): c.get("lane") for c in ET.parse(out / "out.fcpxml").iter("asset-clip")}
    assert lanes["Kamera_korrigiert"] == "-1"  # the user's first device gets the first lane
    assert {lanes["Pult_Summe_korrigiert"], lanes["Pult_2_korrigiert"]} == {"-2", "-3"}


def _clips_layout() -> devices.Layout:
    """A desk (parallel tracks, the reference), a Zoom split in two, a camera with three
    clips (one of them from another camera)."""
    desk = devices.Device("Pult", [devices.Clip([Path("/a/1.wav"), Path("/a/2.wav")])])
    zoom3 = devices.Device("ZOOM0003", [devices.Clip([Path("/z/ZOOM0003.WAV")])])
    zoom4 = devices.Device("ZOOM0004", [devices.Clip([Path("/z/ZOOM0004.WAV")])])
    cam = devices.Device(
        "Kamera", [devices.Clip([Path(f"/c/{n}.MP4")]) for n in ("C2378", "C2379", "R62_0041")]
    )
    return devices.Layout([desk, zoom3, zoom4, cam], 0, [Path("/a/2.wav")], suggested=0)


def test_layout_edits_keep_reference_and_suggestion():
    layout = _clips_layout()
    layout.move_device(0, 2)
    assert [d.name for d in layout.devices] == ["ZOOM0003", "ZOOM0004", "Pult", "Kamera"]
    assert layout.reference == layout.suggested == 2 and layout.devices[2].is_reference

    # a file of the camera becomes a device of its own, right after the camera
    layout.move_files([Path("/c/R62_0041.MP4")], None, "Kamera R62")
    assert [d.name for d in layout.devices][-2:] == ["Kamera", "Kamera R62"]
    assert len(layout.devices[3].clips) == 2

    # the Zoom's halves together again, in start order
    starts = {"/z/ZOOM0003.WAV": 0.0, "/z/ZOOM0004.WAV": -1.0}
    layout.merge(0, 1, "ZOOM", start=lambda p: starts[p.as_posix()])
    assert [d.name for d in layout.devices] == ["ZOOM", "Pult", "Kamera", "Kamera R62"]
    assert [c.tracks[0].name for c in layout.devices[0].clips] == ["ZOOM0004.WAV", "ZOOM0003.WAV"]
    assert layout.reference == layout.suggested == 1
    layout.check()

    # moving the last file away removes its device
    layout.move_files([Path("/c/R62_0041.MP4")], 2)
    assert [d.name for d in layout.devices] == ["ZOOM", "Pult", "Kamera"]
    assert len(layout.devices[2].clips) == 3


def test_parallel_tracks_stay_together():
    layout = _clips_layout()
    with pytest.raises(devices.LayoutError, match="stay together"):
        layout.move_files([Path("/a/1.wav")], 1)
    with pytest.raises(devices.LayoutError, match="parallel tracks"):
        layout.move_files([Path("/z/ZOOM0003.WAV")], 0)
    with pytest.raises(devices.LayoutError, match="parallel tracks"):
        layout.merge(1, 0, "x")


def test_merging_into_the_reference_keeps_it():
    layout = _clips_layout()
    layout.reference, layout.tracks = 1, [Path("/z/ZOOM0003.WAV")]
    layout.merge(2, 1, "ZOOM")  # default order: by file name
    assert layout.devices[layout.reference].name == "ZOOM"
    assert layout.suggested == 0
    layout.merge(1, 2, "Alles")  # the reference device itself goes into another one
    assert layout.devices[layout.reference].name == "Alles"
    layout.check()
