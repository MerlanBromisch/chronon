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

The app shows the detected devices before a reference exists (``detect``) and lets the user
rename, describe, reorder and regroup them and name tracks. That edited ``Layout`` is a
property of the project, never of the files; the analysis takes it instead of grouping again.
Device names become output file names and the timeline's lanes, in the layout's order.
"""

from __future__ import annotations

import json
import re
import unicodedata
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
    description: str = ""
    track_names: dict[str, str] = field(default_factory=dict)  # str(path) -> name

    @property
    def files(self) -> list[Path]:
        return [t for c in self.clips for t in c.tracks]

    @property
    def multitrack(self) -> bool:
        return any(len(c.tracks) > 1 for c in self.clips)

    def track_label(self, path: Path) -> str:
        """A parallel track's name, or its number within its clip (1 = first)."""
        name = self.track_names.get(str(path), "").strip()
        if name:
            return name
        clip = next(c for c in self.clips if path in c.tracks)
        return str(clip.tracks.index(path) + 1)

    def describe(self) -> str:
        tracks = {len(c.tracks) for c in self.clips}
        parts = [f"{len(self.clips)} clip{'s' if len(self.clips) != 1 else ''}"]
        if tracks != {1}:
            parts.append(f"{max(tracks)} parallel tracks")
        return f"{self.name}: {', '.join(parts)}" + (" (reference)" if self.is_reference else "")


LAYOUT_SCHEMA = 1


class LayoutError(ValueError):
    pass


@dataclass
class Layout:
    """Devices as the user arranged them, in timeline order, and the reference choice."""

    devices: list[Device]
    reference: int  # index into ``devices``
    tracks: list[Path]  # the reference device's tracks used as reference
    suggested: int | None = None  # the device Chronon proposed as reference

    @property
    def files(self) -> list[Path]:
        return [f for d in self.devices for f in d.files]

    def device_of(self, path: Path) -> Device | None:
        return next((d for d in self.devices if Path(path) in d.files), None)

    def order(self, name: str) -> int:
        """Lane position of the device called ``name`` (the user's order)."""
        return next((k for k, d in enumerate(self.devices) if d.name == name), len(self.devices))

    def check(self) -> None:
        """Every file in one device, names usable as file names and distinct, reference
        tracks within the reference device."""
        seen: set[Path] = set()
        names: set[str] = set()
        for d in self.devices:
            if not d.name.strip() or re.search(r'[/\\:*?"<>|]', d.name):
                raise LayoutError(f"device name {d.name!r} cannot be a file name")
            key = unicodedata.normalize("NFC", d.name.strip()).casefold()
            if key in names:
                raise LayoutError(f"two devices are called {d.name!r}")
            names.add(key)
            if not d.clips or not all(c.tracks for c in d.clips):
                raise LayoutError(f"device {d.name!r} has no files")
            for f in d.files:
                if f in seen:
                    raise LayoutError(f"{f.name} is in two devices")
                seen.add(f)
        if not 0 <= self.reference < len(self.devices):
            raise LayoutError("no reference device")
        ref = self.devices[self.reference]
        if not self.tracks or any(t not in ref.files for t in self.tracks):
            raise LayoutError(f"reference tracks must be tracks of {ref.name!r}")
        if not any(set(self.tracks) <= set(c.tracks) for c in ref.clips):
            raise LayoutError("reference tracks must be parallel tracks of one clip")

    def save(self, path: Path | str) -> Path:
        self.check()
        path = Path(path)
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str) -> Layout:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise LayoutError(f"cannot read the device layout {path}: {e}") from None
        if not isinstance(data, dict) or data.get("kind") != "devices":
            raise LayoutError(f"{path} is not a Chronon device layout")
        if data.get("schema", 0) > LAYOUT_SCHEMA:
            raise LayoutError(f"{path} was written by a newer Chronon")
        layout = cls.from_dict(data)
        layout.check()
        return layout

    @classmethod
    def from_dict(cls, data: dict) -> Layout:
        devices = [
            Device(
                d["name"],
                [Clip([Path(t) for t in c]) for c in d["clips"]],
                description=d.get("description", ""),
                track_names=dict(d.get("track_names", {})),
            )
            for d in data["devices"]
        ]
        for k, d in enumerate(devices):
            d.is_reference = k == data["reference"]
        return cls(
            devices,
            data["reference"],
            [Path(t) for t in data["reference_tracks"]],
            data.get("suggested"),
        )

    def to_dict(self) -> dict:
        return {
            "schema": LAYOUT_SCHEMA,
            "kind": "devices",
            "reference": self.reference,
            "reference_tracks": [str(t) for t in self.tracks],
            "suggested": self.suggested,
            "devices": [
                {
                    "name": d.name,
                    "description": d.description,
                    "clips": [[str(t) for t in c.tracks] for c in d.clips],
                    "track_names": d.track_names,
                }
                for d in self.devices
            ],
        }


def detect(
    files: Sequence[Path | str],
    separate: bool = False,
    probe: Callable[[Path], audio.Info] = audio.probe,
    loudest: Callable[[Sequence[Path], int], list[Path]] | None = None,
) -> Layout:
    """The files' devices before any reference is chosen, with a suggestion: the device
    covering the most time, and its loudest tracks (``loudest(tracks, n)``; default: the
    first two)."""
    files = [Path(f) for f in files]
    infos = {p: probe(p) for p in dict.fromkeys(files)}
    devices = group([], files, separate, probe=lambda p: infos[p])[1:]

    def coverage(d: Device) -> tuple[float, int]:
        return (sum(infos[c.tracks[0]].duration_s for c in d.clips), len(d.files))

    best = max(range(len(devices)), key=lambda k: coverage(devices[k])) if devices else 0
    clip = (
        max(devices[best].clips, key=lambda c: infos[c.tracks[0]].duration_s) if devices else None
    )
    tracks = list(clip.tracks) if clip else []
    if len(tracks) > 2:
        tracks = loudest(tracks, 2) if loudest else tracks[:2]
    for k, d in enumerate(devices):
        d.is_reference = k == best
    return Layout(devices, best, tracks, best)


def _natural(path: Path) -> list:
    """Sort key: 'track 2' before 'track 10'."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", path.name)]


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
            by_key[key] = Device(_clip_device_name(clip), [clip])
            devices.append(by_key[key])
    if refs:  # the reference device is named like any other
        clip = reference.clips[0]
        reference.name = _set_name(clip) if len(clip.tracks) > 1 else _clip_device_name(clip)
    for d in devices:
        for c in d.clips:
            c.tracks.sort(key=_natural)  # track numbers as people count them
    seen: set[str] = {reference.name} if refs else set()
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


def _clip_device_name(clip: Clip) -> str:
    """A recorder's name from its file names: ZOOM0003 -> ZOOM, R62_0041 -> R62."""
    first = clip.tracks[0]
    name = prefix(first).rstrip("_-# ")
    return name if len(name) >= 2 else first.stem


def _set_name(clip: Clip) -> str:
    """A name for parallel tracks: their common file name start, or their folder."""
    stems = [t.stem for t in clip.tracks]
    common = stems[0]
    for s in stems[1:]:
        while not s.startswith(common):
            common = common[:-1]
    # cut back to a whole word: "Musical 23.6.26_" -> "Musical 23.6.26", "ZOOM0001_Tr" -> "ZOOM0001"
    cut = max(common.rfind(sep) for sep in "_- #")
    if cut >= 3:
        common = common[:cut]
    common = common.rstrip("_-# ")
    if len(common) >= 3:
        return common
    folder = clip.tracks[0].parent
    return (folder.parent.stem if folder.name.lower() == "audio files" else folder.name) or "tracks"
