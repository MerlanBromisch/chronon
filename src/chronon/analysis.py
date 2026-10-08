"""A finished analysis, kept so the export does not measure again.

The app analyses once (and shows the result), then exports later in either mode: ``sync``
and ``correct`` take a saved analysis instead of measuring the files again. The analysis
file also holds what the result screen shows before any export: where each file lands on the
timeline, how exactly video can be placed, the timeline's frame rate.

An analysis is only valid for the files it measured: each source's size and modification
time are stored, and a changed or missing file refuses the export.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, fields
from fractions import Fraction
from pathlib import Path

from chronon import __version__, align, devices, fcpxml, messages

SCHEMA = 1  # bump when a field changes meaning or goes away
Progress = Callable[[str, int, int], None]


class AnalysisError(messages.UserError, ValueError):
    pass


@dataclass
class Analysis:
    refs: list[Path]
    files: list[Path]
    results: list[align.FileResult]  # one per entry of ``files``
    separate: bool = False
    stamps: dict[str, list[int]] = field(default_factory=dict)  # path -> [size, mtime ns]
    frame_rate: Fraction = Fraction(25)  # of the timeline: its videos' rate, else 25 fps
    placements: list[dict] = field(default_factory=list)  # per entry, see ``_preview``
    layout: devices.Layout | None = None  # the devices (names, order) the analysis used

    @property
    def timeline_zero(self) -> float:
        """Reference time of the timeline's start: the earliest start of any file."""
        return min(0.0, *(r.alignment.offset_s for _, r in self.entries()))

    @property
    def reference_name(self) -> str:
        return self.layout.devices[self.layout.reference].name if self.layout else "reference"

    def entries(self, all_refs: bool = False) -> list[tuple[Path, align.FileResult]]:
        """Reference tracks to export, then the files. Of several reference tracks only
        those some file matched best are exported (plus the first), unless ``all_refs`` or
        the reference device's other tracks are exported anyway (then all of its tracks)."""
        all_refs = all_refs or any(r.is_reference for r in self.results)
        used = {self.refs[0]} | {r.reference for r in self.results}
        identity = align.Alignment(0.0, 0.0, 1.0, False, 1, 1)
        files = set(self.files)
        entries = [
            (r, align.FileResult(r, identity, self.reference_name, is_reference=True))
            for r in self.refs
            if (all_refs or r in used) and r not in files
        ]
        return entries + list(zip(self.files, self.results, strict=True))

    def check_sources(self) -> None:
        """Refuse an analysis whose files changed (or vanished) since it was made."""
        for path in dict.fromkeys(self.refs + self.files):
            if not path.exists():
                raise AnalysisError(
                    f"{path} is missing; it was part of the analysis",
                    "source_missing",
                    file=str(path),
                )
            if _stamp(path) != self.stamps.get(str(path)):
                raise AnalysisError(
                    f"{path.name} changed since the analysis; analyse again",
                    "source_changed",
                    file=str(path),
                )

    def save(self, path: Path | str) -> Path:
        data = {
            "schema": SCHEMA,
            "chronon": __version__,
            "kind": "analysis",
            "refs": [str(r) for r in self.refs],
            "separate": self.separate,
            "frame_rate": str(self.frame_rate),
            "stamps": self.stamps,
            "layout": self.layout.to_dict() if self.layout else None,
            "files": [
                _result_row(f, r) | {"placement": p}
                for f, r, p in zip(self.files, self.results, self.placements, strict=True)
            ],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str) -> Analysis:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise AnalysisError(f"cannot read the analysis {path}: {e}") from None
        if data.get("kind") != "analysis":
            raise AnalysisError(f"{path} is not a Chronon analysis")
        if data.get("schema", 0) > SCHEMA:
            raise AnalysisError(f"{path} was written by a newer Chronon ({data.get('chronon')})")
        rows = data["files"]
        return cls(
            refs=[Path(r) for r in data["refs"]],
            files=[Path(r["file"]) for r in rows],
            results=[_result_from_row(r) for r in rows],
            separate=data.get("separate", False),
            stamps=data["stamps"],
            frame_rate=Fraction(data["frame_rate"]),
            placements=[r["placement"] for r in rows],
            layout=devices.Layout.from_dict(data["layout"]) if data.get("layout") else None,
        )


def measure(
    refs: Sequence[Path | str],
    files: Sequence[Path | str],
    progress: Progress | None = None,
    separate: bool = False,
    layout: devices.Layout | None = None,
) -> Analysis:
    """Align the files to the reference (see ``align.align_files``): the devices of
    ``layout`` (the user's: names, order, grouping, reference choice), else the files
    grouped automatically with ``refs`` as the reference."""
    if layout is not None:
        layout.check()
        refs = list(layout.tracks)
        files = [f for f in layout.files if f not in refs]
    refs, files = [Path(r) for r in refs], [Path(f) for f in files]
    if layout is None:
        found = devices.group(refs, files, separate)
        layout = devices.Layout(found, 0, refs)
    results = align.align_files(refs, files, progress=progress, layout=layout)
    stamps = {str(p): _stamp(p) for p in dict.fromkeys(refs + files)}
    a = Analysis(refs, files, results, separate, stamps, layout=layout)
    a.frame_rate, a.placements = _preview(a)
    return a


def _preview(a: Analysis) -> tuple[Fraction, list[dict]]:
    """Where each file lands on a timeline of the originals (as ``sync`` writes it)."""
    entries = a.entries()
    zero = a.timeline_zero
    items = [
        fcpxml.Item(
            f,
            r.alignment.offset_s - zero,
            r.alignment.drift_ppm,
            group=r.device,
            order=a.layout.order(r.device) if a.layout else 0,
        )
        for f, r in entries
    ]
    placed = fcpxml.place(items)
    frame = fcpxml.timeline_frame([p.media for p in placed])
    by_file = {p.item.path: p for p in placed}
    rows = []
    for f in a.files:
        p = by_file[f]
        rows.append(
            {
                "position_s": p.item.position_s,
                "duration_s": float(p.media.duration),
                "has_video": p.media.has_video,
                "video_error_ms": p.error_ms if p.media.has_video else None,
                "variable_rate": p.media.variable_rate,
            }
        )
    return 1 / frame, rows


def _stamp(path: Path) -> list[int]:
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def _result_row(path: Path, r: align.FileResult) -> dict:
    return {
        "file": str(path),
        "reference": str(r.reference),
        "device": r.device,
        "is_reference": r.is_reference,
        "via": _str(r.via),
        "drift_from": _str(r.drift_from),
        "linked_via": _str(r.linked_via),
        "continues": _str(r.continues),
        "alignment": asdict(r.alignment),
    }


def _result_from_row(row: dict) -> align.FileResult:
    names = {f.name for f in fields(align.Alignment)}
    a = {k: v for k, v in row["alignment"].items() if k in names}
    a["good_s"] = tuple(a.get("good_s", ()))
    a["good_lag"] = tuple(a.get("good_lag", ()))
    return align.FileResult(
        Path(row["reference"]),
        align.Alignment(**a),
        row["device"],
        row["is_reference"],
        _path(row["via"]),
        _path(row["drift_from"]),
        _path(row["linked_via"]),
        _path(row.get("continues")),  # written since 2026-10-08
    )


def _str(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _path(text: str | None) -> Path | None:
    return None if text is None else Path(text)
