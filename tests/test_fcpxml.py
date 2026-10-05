import shutil
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

from chronon import correct, fcpxml, synth
from chronon.fcpxml import Item, Media
from chronon.synth import Clip, Device, Scenario

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

DTD = Path(
    "/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework"
    "/Versions/A/Resources/FCPXMLv1_11.dtd"
)
FRAME = Fraction(1, 25)


def _audio(seconds=100, start=0):
    return Media(Fraction(seconds), Fraction(start), False, True, 2, 48_000)


def _video(seconds=100, start=0):
    return Media(Fraction(seconds), Fraction(start), True, True, 2, 48_000, FRAME, 1920, 1080)


def test_audio_starts_on_a_frame_and_is_trimmed_to_the_sample():
    p = fcpxml._place(Item(Path("a.wav"), 3.5), _audio(start=10), FRAME)
    assert p.offset == Fraction(88, 25)  # next frame boundary after 3.5 s
    assert p.start == 10 + Fraction(960, 48_000)  # 20 ms of audio skipped
    assert p.offset % FRAME == 0 and p.duration % FRAME == 0
    assert p.error_ms == 0


def test_padded_audio_needs_no_trim():
    p = fcpxml._place(Item(Path("a.wav"), 0.0), _audio(), FRAME)
    assert (p.offset, p.start, p.duration) == (0, 0, 100)


def test_drifting_video_is_centred_on_the_nearest_frame():
    # a clock 100 ppm fast: the file's 1000 s cover 999.9 real seconds
    p = fcpxml._place(Item(Path("v.mp4"), 50.0, drift_ppm=100.0), _video(1000), FRAME)
    centre = 50.0 + 500 / (1 + 1e-4) - 500
    assert p.offset % FRAME == 0
    assert abs(float(p.offset) - centre) <= float(FRAME) / 2
    assert p.error_ms == pytest.approx(50.0, abs=21.0)  # ~50 ms at the ends + rounding


def test_video_before_timeline_zero_is_trimmed():
    p = fcpxml._place(Item(Path("v.mp4"), -0.09), _video(start=5), FRAME)
    assert p.offset == 0
    assert p.start == 5 + Fraction(2, 25)


def test_lanes_group_devices():
    placed = [
        fcpxml.Placed(Item(Path(n), 0.0), m, Fraction(o), Fraction(0), Fraction(d))
        for n, m, o, d in [
            ("ZOOM0003.WAV", _audio(), 0, 10),
            ("ZOOM0004.WAV", _audio(), 10, 10),
            ("desk_17.wav", _audio(), 0, 30),
            ("C2378.MP4", _video(), 0, 5),
            ("C2379.MP4", _video(), 20, 5),
            ("R62_0040.MP4", _video(), 0, 30),
        ]
    ]
    fcpxml._assign_lanes(placed)
    lane = {p.item.path.name: p.lane for p in placed}
    assert lane["ZOOM0003.WAV"] == lane["ZOOM0004.WAV"] < 0
    assert lane["desk_17.wav"] < 0 and lane["desk_17.wav"] != lane["ZOOM0003.WAV"]
    assert lane["C2378.MP4"] == lane["C2379.MP4"] > 0
    assert lane["R62_0040.MP4"] > 0 and lane["R62_0040.MP4"] != lane["C2378.MP4"]


def _scene(tmp_path):
    """A recorder and a camera: the camera's audio as a video file with timecode."""
    scenario = Scenario(
        signal="noise",
        seed=31,
        devices=(
            Device("rec", (Clip(0.0, 30.0),)),
            Device("cam", (Clip(4.0, 20.0),), drift_ppm=30.0, snr_db=25.0),
        ),
    )
    media = tmp_path / "media"
    synth.write(scenario, media)
    video = media / "cam.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=320x180:r=25",
            "-i",
            str(media / "cam_01.wav"),
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "pcm_s24le",
            "-timecode",
            "01:00:00:00",
            str(video),
        ],
        check=True,
    )
    return media / "rec_01.wav", video


def _clips(path: Path) -> dict[str, ET.Element]:
    root = ET.parse(path).getroot()
    return {c.get("name"): c for c in root.iter("asset-clip")}


def _validate(path: Path):
    if not DTD.exists() or shutil.which("xmllint") is None:
        return
    local = path.parent / "fcpxml.dtd"  # xmllint cannot open a DTD path with spaces
    shutil.copy(DTD, local)
    subprocess.run(["xmllint", "--noout", "--dtdvalid", str(local), str(path)], check=True)


def test_sync_places_originals(tmp_path):
    rec, video = _scene(tmp_path)
    result = correct.sync([rec], [video], tmp_path / "out", name="t")
    assert result[1].position_s == pytest.approx(4.0, abs=1e-3)
    path = tmp_path / "out" / "t.fcpxml"
    _validate(path)
    clips = _clips(path)
    assert clips["cam"].get("srcEnable") is None  # keeps its own (drifting) audio
    assert Fraction(clips["cam"].get("start").rstrip("s")) == 3600  # camera timecode
    assert Fraction(clips["cam"].get("offset").rstrip("s")) == 4


def test_correct_mutes_camera_audio_and_uses_the_corrected_file(tmp_path):
    rec, video = _scene(tmp_path)
    outputs = correct.run([rec], [video], tmp_path / "out", name="t")
    assert all(o.verified is not False for o in outputs)
    path = tmp_path / "out" / "t.fcpxml"
    _validate(path)
    root = ET.parse(path).getroot()
    clips = list(root.iter("asset-clip"))
    video_clip = next(c for c in clips if c.get("srcEnable") == "video")
    assert int(video_clip.get("lane")) > 0
    audio_clips = [c for c in clips if c.get("srcEnable") is None]
    assert {c.get("offset") for c in audio_clips} == {"0s"}  # padded: all start at zero
    srcs = {r.get("src") for r in root.iter("media-rep")}
    assert (tmp_path / "out" / "cam.wav").resolve().as_uri() in srcs
    assert video.resolve().as_uri() in srcs


def test_format_names_match_final_cut():
    assert fcpxml.format_name(FRAME, 3840, 2160) == "FFVideoFormat3840x2160p25"
    assert fcpxml.format_name(FRAME, 1920, 1080) == "FFVideoFormat1080p25"
    assert fcpxml.format_name(Fraction(1001, 30000), 1920, 1080) == "FFVideoFormat1080p2997"


def test_only_a_standard_timecode_track_counts():
    # Sony XAVC S: timecode only in its 'rtmd' metadata track, which Final Cut ignores
    sony = [
        {"codec_tag_string": "avc1"},
        {"codec_tag_string": "rtmd", "tags": {"timecode": "14:49:07:04"}},
    ]
    assert fcpxml._timecode_tag(sony, {}) is None
    other = [
        {"codec_tag_string": "hvc1"},
        {"codec_tag_string": "tmcd", "tags": {"timecode": "07:39:37:10"}},
    ]
    assert fcpxml._timecode_tag(other, {"timecode": "07:39:37:10"}) == "07:39:37:10"


def test_corrected_audio_is_named_apart_from_its_video(tmp_path):
    rec, video = _scene(tmp_path)
    correct.run([rec], [video], tmp_path / "out", name="t")
    names = set(_clips(tmp_path / "out" / "t.fcpxml"))
    assert {"cam", "cam audio", "rec_01"} <= names


def test_timeline_is_rebuilt_from_the_report(tmp_path):
    rec, video = _scene(tmp_path)
    correct.run([rec], [video], tmp_path / "out", name="t")
    first = (tmp_path / "out" / "t.fcpxml").read_text()
    (tmp_path / "out" / "t.fcpxml").unlink()
    assert correct.timeline(tmp_path / "out", "t") == tmp_path / "out" / "t.fcpxml"
    assert (tmp_path / "out" / "t.fcpxml").read_text() == first

    correct.sync([rec], [video], tmp_path / "sync", name="s")
    first = (tmp_path / "sync" / "s.fcpxml").read_text()
    correct.timeline(tmp_path / "sync", "s")
    assert (tmp_path / "sync" / "s.fcpxml").read_text() == first
