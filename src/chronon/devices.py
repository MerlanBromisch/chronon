"""Grouping files into devices (see docs/export.md, "Devices").

A *clip* is one recording: one file, or several parallel tracks that start on the same sample
(the channels of a desk, a Zoom's XLR inputs and its XY mic). A *device* is the clips that
share one clock (the halves of a Zoom take split at 2 GiB, the files a camera wrote).

Grouping only uses metadata and names; it never places anything (cameras number their
timecode only while recording, so metadata cannot show the pauses between clips).

- Parallel tracks: same folder, sample rate, codec, exact length, and the same BWF time
  stamp where there is one. Only PCM files have an exact length.
- Clips of a device: same folder, file name without its trailing number, sample rate,
  channel count, codec and recorder tag.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from chronon import audio


@dataclass
class Clip:
    tracks: list[Path]

    @property
    def name(self) -> str:
        return self.tracks[0].stem if len(self.tracks) == 1 else f"{self.tracks[0].stem} …"


@dataclass
class Device:
    name: str
    clips: list[Clip] = field(default_factory=list)
    is_reference: bool = False

    @property
    def files(self) -> list[Path]:
        return [t for c in self.clips for t in c.tracks]

    def describe(self) -> str:
        tracks = {len(c.tracks) for c in self.clips}
        parts = [f"{len(self.clips)} clip{'s' if len(self.clips) != 1 else ''}"]
        if tracks != {1}:
            parts.append(f"{max(tracks)} parallel tracks")
        return f"{self.name}: {', '.join(parts)}" + (" (reference)" if self.is_reference else "")


def prefix(path: Path) -> str:
    """File name without its trailing number: ZOOM0003 -> ZOOM, R62_0041 -> R62_."""
    return re.sub(r"\d+$", "", path.stem) or path.stem


def group(
    refs: Sequence[Path | str],
    files: Sequence[Path | str],
    separate: bool = False,
    probe: Callable[[Path], audio.Info] = audio.probe,
) -> list[Device]:
    """Devices for the reference tracks and the files, in input order; the reference
    tracks form the first device. With ``separate`` every file is a device of its own."""
    refs, files = [Path(r) for r in refs], [Path(f) for f in files]
    infos = {p: probe(p) for p in dict.fromkeys(refs + files)}
    reference = Device("reference", [Clip(list(refs))], is_reference=True)
    rest = [f for f in files if f not in refs]
    if separate:
        return [reference] + [Device(f.stem, [Clip([f])]) for f in rest]

    # parallel tracks first: files that are the same recording on several channels
    clips: list[Clip] = []
    keys: list[tuple] = []
    for f in rest:
        key = _parallel_key(f, infos[f])
        if key is not None and key in keys:
            clips[keys.index(key)].tracks.append(f)
        else:
            clips.append(Clip([f]))
            keys.append(key if key is not None else ("single", f))
    # tracks parallel to the reference belong to it
    ref_key = _parallel_key(refs[0], infos[refs[0]]) if refs else None
    for clip in list(clips):
        if ref_key is not None and _parallel_key(clip.tracks[0], infos[clip.tracks[0]]) == ref_key:
            reference.clips[0].tracks.extend(clip.tracks)
            clips.remove(clip)

    devices: list[Device] = [reference]
    by_key: dict[tuple, Device] = {}
    for clip in clips:
        first = clip.tracks[0]
        if len(clip.tracks) > 1:
            devices.append(Device(_set_name(clip), [clip]))
            continue
        key = _device_key(first, infos[first])
        if key in by_key:
            by_key[key].clips.append(clip)
        else:
            name = prefix(first).rstrip("_-# ")
            by_key[key] = Device(name if len(name) >= 2 else first.stem, [clip])
            devices.append(by_key[key])
    seen: set[str] = set()
    for d in devices[1:]:
        d.clips.sort(key=lambda c: _number(c.tracks[0]))
        if d.name in seen:  # e.g. two Zoom recorders: tell them apart by folder
            d.name = f"{d.name} ({d.clips[0].tracks[0].parent.name})"
        seen.add(d.name)
    return devices


def _parallel_key(path: Path, info: audio.Info) -> tuple | None:
    if not info.codec.startswith("pcm_") or info.has_video:
        return None  # only PCM files have an exact length
    return (path.parent, info.sample_rate, info.frames, info.time_reference)


def _device_key(path: Path, info: audio.Info) -> tuple:
    return (path.parent, prefix(path), info.sample_rate, info.channels, info.codec, info.recorder)


def _number(path: Path) -> int:
    m = re.search(r"(\d+)$", path.stem)
    return int(m.group(1)) if m else 0


def _set_name(clip: Clip) -> str:
    """A name for parallel tracks: their common file name start, or their folder."""
    stems = [t.stem for t in clip.tracks]
    common = stems[0]
    for s in stems[1:]:
        while not s.startswith(common):
            common = common[:-1]
    common = common.rstrip("_-# 0123456789")
    if len(common) >= 3:
        return common
    folder = clip.tracks[0].parent
    return (folder.parent.stem if folder.name.lower() == "audio files" else folder.name) or "tracks"
