"""FCPXML timeline export for Final Cut Pro (and Logic Pro's FCPXML import).

All clips hang off one gap that spans the timeline: video above (lane 1, 2, ...), audio
below (lane -1, -2, ...), one lane per device (file name without its trailing number, so
ZOOM0003 and ZOOM0004 share a lane). Times are exact rationals.

Placement rules:
- Timeline offsets sit on frame boundaries (Final Cut requires it). Audio that belongs
  between frames starts at the next frame boundary and is trimmed by the remainder, which
  is sample-accurate. Padded corrected audio starts at 0 and needs no trim at all.
- Media that still carries clock drift (video; audio when not corrected) is placed so its
  middle is right, halving the worst error. The remaining error at the ends is reported.
- A video whose audio was corrected into its own file plays only its picture
  (``srcEnable="video"``); the corrected file plays below it.
- Asset ``start`` is the media's own time origin as Final Cut reads it: the camera
  timecode, or the BWF time stamp of a recorder file.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import quoteattr

from chronon import audio

VERSION = "1.11"
DEFAULT_FRAME = Fraction(1, 25)
AUDIO_RATES = {
    32000: "32k",
    44100: "44.1k",
    48000: "48k",
    88200: "88.2k",
    96000: "96k",
    176400: "176.4k",
    192000: "192k",
}


@dataclass(frozen=True)
class Item:
    """One media file on the timeline."""

    path: Path
    position_s: float  # timeline time of the media's first sample / frame
    drift_ppm: float = 0.0  # drift still in the media (0 for corrected audio)
    video_only: bool = False  # its audio is replaced by a corrected file


@dataclass(frozen=True)
class Media:
    duration: Fraction
    start: Fraction  # the media's own time origin (timecode / BWF)
    has_video: bool
    has_audio: bool
    channels: int
    rate: int
    frame: Fraction | None = None
    width: int = 0
    height: int = 0


@dataclass
class Placed:
    item: Item
    media: Media
    offset: Fraction
    start: Fraction
    duration: Fraction
    lane: int = 0
    error_ms: float = 0.0  # worst remaining misplacement over the clip


def write(items: list[Item], path: Path | str, name: str) -> list[Placed]:
    """Write the timeline; return the placements (for reporting)."""
    placed = place(items)
    Path(path).write_text(render(placed, name))
    return placed


def place(items: list[Item]) -> list[Placed]:
    medias = [probe(i.path) for i in items]
    frames = [m.frame for m in medias if m.has_video and m.frame]
    frame = frames[0] if frames else DEFAULT_FRAME
    placed = [_place(i, m, frame) for i, m in zip(items, medias, strict=True)]
    _assign_lanes(placed)
    return placed


def render(placed: list[Placed], name: str) -> str:
    frame = next(
        (p.media.frame for p in placed if p.media.has_video and p.media.frame), DEFAULT_FRAME
    )
    formats: dict[tuple, str] = {}
    res, clips = [], []

    def fmt_id(fd: Fraction, w: int, h: int) -> str:
        key = (fd, w, h)
        if key not in formats:
            formats[key] = f"r{len(formats) + 1}"
            res.append(
                f'    <format id="{formats[key]}" frameDuration="{_t(fd)}" '
                f'width="{w}" height="{h}"/>'
            )
        return formats[key]

    seq_video = next((p.media for p in placed if p.media.has_video), None)
    seq_fmt = fmt_id(
        frame, seq_video.width if seq_video else 1920, seq_video.height if seq_video else 1080
    )
    for k, p in enumerate(placed):
        m, aid = p.media, f"a{k + 1}"
        attrs = {
            "id": aid,
            "name": p.item.path.stem,
            "start": _t(m.start),
            "duration": _t(m.duration),
        }
        if m.has_video:
            attrs |= {
                "hasVideo": "1",
                "videoSources": "1",
                "format": fmt_id(m.frame or frame, m.width, m.height),
            }
        if m.has_audio:
            attrs |= {
                "hasAudio": "1",
                "audioSources": "1",
                "audioChannels": str(m.channels),
                "audioRate": str(m.rate),
            }
        res.append(
            f"    <asset {_attrs(attrs)}>\n"
            f'      <media-rep kind="original-media" '
            f"src={quoteattr(p.item.path.resolve().as_uri())}/>\n    </asset>"
        )
        clip = {
            "ref": aid,
            "lane": str(p.lane),
            "offset": _t(p.offset),
            "name": p.item.path.stem,
            "start": _t(p.start),
            "duration": _t(p.duration),
        }
        if p.item.video_only:
            clip["srcEnable"] = "video"
        clips.append(f"            <asset-clip {_attrs(clip)}/>")
    total = max((p.offset + p.duration for p in placed), default=frame)
    seq = {
        "format": seq_fmt,
        "duration": _t(total),
        "tcStart": "0s",
        "tcFormat": "NDF",
        "audioLayout": "stereo",
    }
    rate = next((p.media.rate for p in placed if p.media.has_audio), 48000)
    if rate in AUDIO_RATES:
        seq["audioRate"] = AUDIO_RATES[rate]
    body = "\n".join(clips)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="{VERSION}">
  <resources>
{chr(10).join(res)}
  </resources>
  <event name={quoteattr(name)}>
    <project name={quoteattr(name)}>
      <sequence {_attrs(seq)}>
        <spine>
          <gap name="Chronon" offset="0s" start="0s" duration="{_t(total)}">
{body}
          </gap>
        </spine>
      </sequence>
    </project>
  </event>
</fcpxml>
"""


# --- placement -------------------------------------------------------------


def _place(item: Item, m: Media, frame: Fraction) -> Placed:
    d = item.drift_ppm * 1e-6
    half = float(m.duration) / 2
    # centre the media: its middle lands where it belongs, the ends are off by +-half*d
    pos = item.position_s + half / (1 + d) - half
    error_ms = abs(half * d / (1 + d)) * 1e3
    if m.has_video:
        # video: nearest frame; the rounding adds up to half a frame
        rounded = Fraction(round(Fraction(pos) / frame)) * frame
        error_ms += abs(float(rounded) - pos) * 1e3
        offset, trim = max(rounded, Fraction(0)), max(-rounded, Fraction(0))
    else:
        # audio: next frame boundary, trimmed by the remainder (sample-accurate)
        offset = _ceil(Fraction(pos), frame) if pos > 0 else Fraction(0)
        trim = Fraction(round((float(offset) - pos) * m.rate), m.rate)
    usable = m.duration - trim
    duration = (usable // frame) * frame
    return Placed(item, m, offset, m.start + trim, duration, error_ms=error_ms)


def _assign_lanes(placed: list[Placed]) -> None:
    """One lane per device group; clips of a group share it when they do not overlap."""
    for video in (True, False):
        lanes: list[tuple[str, list[tuple[Fraction, Fraction]]]] = []
        for p in sorted(
            (p for p in placed if p.media.has_video == video),
            key=lambda p: (_group(p.item.path), p.offset),
        ):
            span = (p.offset, p.offset + p.duration)
            for k, (group, spans) in enumerate(lanes):
                if group == _group(p.item.path) and all(
                    span[0] >= e or span[1] <= s for s, e in spans
                ):
                    spans.append(span)
                    p.lane = (k + 1) if video else -(k + 1)
                    break
            else:
                lanes.append((_group(p.item.path), [span]))
                p.lane = len(lanes) if video else -len(lanes)


def _group(path: Path) -> str:
    return re.sub(r"\d+$", "", path.stem) or path.stem


# --- media -----------------------------------------------------------------


def probe(path: Path | str) -> Media:
    info = audio.probe(path)
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    data = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    streams, fmt_tags = data["streams"], data.get("format", {}).get("tags", {})
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    frame = None
    start = Fraction(0)
    if video:
        num, den = (int(x) for x in video.get("r_frame_rate", "25/1").split("/"))
        frame = Fraction(den, num)
        tc = fmt_tags.get("timecode") or next(
            (
                s.get("tags", {}).get("timecode")
                for s in streams
                if s.get("tags", {}).get("timecode")
            ),
            None,
        )
        if tc:
            start = _timecode(tc, frame)
        nb = int(video.get("nb_frames") or 0)
        duration = nb * frame if nb else Fraction(info.frames, info.sample_rate)
    else:
        ref = fmt_tags.get("time_reference")
        if ref:
            start = Fraction(int(ref), info.sample_rate)
        duration = Fraction(info.frames, info.sample_rate)
    return Media(
        duration=duration,
        start=start,
        has_video=video is not None,
        has_audio=True,
        channels=info.channels,
        rate=info.sample_rate,
        frame=frame,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
    )


def _timecode(tc: str, frame: Fraction) -> Fraction:
    h, m, s, f = (int(x) for x in re.split(r"[:;.]", tc))
    fps = round(1 / frame)
    return ((h * 3600 + m * 60 + s) * fps + f) * frame


# --- formatting ------------------------------------------------------------


def _ceil(x: Fraction, step: Fraction) -> Fraction:
    return -((-x) // step) * step


def _t(x: Fraction) -> str:
    x = Fraction(x)
    return f"{x.numerator}s" if x.denominator == 1 else f"{x.numerator}/{x.denominator}s"


def _attrs(d: dict[str, str]) -> str:
    return " ".join(f"{k}={quoteattr(v)}" for k, v in d.items())
