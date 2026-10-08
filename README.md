# Chronon

**Automatic waveform-based sync for multi-device recordings — with clock drift correction.**

> Status: beta 1.0.0-beta.1. The desktop app and the command line measure, place and
> correct recordings and write a Final Cut Pro / Logic timeline; tested on a 4 h musical (56
> files from two desks, a Zoom recorder and two cameras) and an interview.

## Why

When you record with more than one device (camera + recorder, recorder + DAW, several cameras),
the files never line up perfectly: every device has its own clock, and "48 kHz" on one is never
exactly 48 kHz on another. Over a long take this adds up to audible drift — often 100+ ms after
half an hour.

PluralEyes used to solve this. It is discontinued, and the sync features built into editors are
slow, often imprecise, and don't correct drift at all. Chronon fills that gap: drop in your
media, get back a timeline for your editor and, if you want, drift-corrected audio.

## Features

- Syncs any number of audio and video files by their sound — a 4 h show with 56 files in one
  to two minutes
- Measures every device's clock drift (ppm) and can write drift-corrected audio
- Groups files into devices: a desk's parallel tracks, a recorder's or camera's numbered clips,
  a long take split into several files
- Places clips that never overlap the reference through other devices that do
- Says "kein sicherer Treffer" (no reliable match) instead of guessing, and lets you listen to
  every file against the reference before you export
- Exports a timeline of the original files for Final Cut Pro (FCPXML, also opens in Logic Pro),
  or drift-corrected WAV / CAF files with a timeline

## Install

Download the zip for your system from the
[Releases](https://github.com/MerlanBromisch/chronon/releases) page and unpack it.

| System | Requirements |
|---|---|
| macOS | Apple silicon (M1 or newer), macOS 14 or newer. No build for Intel Macs. |
| Windows | Windows 10 or 11, 64-bit |
| Linux | x86-64, glibc 2.35 or newer (Ubuntu 22.04 and later) |

ffmpeg is included; nothing else needs to be installed.

**The apps are not signed** (no paid Apple or Microsoft certificate), so the system warns the
first time:

- **macOS:** move `Chronon.app` to Programme (Applications) and open it. When macOS refuses,
  open System Settings → Privacy & Security, scroll down to "Chronon was blocked" and click
  "Open Anyway". On macOS 14 and older a right-click on the app → Open also works.
- **Windows:** SmartScreen says "Windows protected your PC": click "More info" → "Run anyway".
- **Linux:** run `Chronon/Chronon` from the unpacked folder.

## Using the app

The app is in German. Six steps, one after the other:

1. **Dateien** — drop files or whole folders (a Logic project works as is).
2. **Geräte & Referenz** — check how Chronon grouped the files into devices; rename, reorder
   (this is the order of the lanes in the timeline) and regroup them. The reference is the
   device everything is measured against: Chronon suggests one; change it in a device's "…"
   menu. A desk's "Vergleichsspuren" (the tracks the others are compared with) are chosen
   automatically; change them only if a file finds no reliable match.
3. **Sync** — measures every file, a few minutes at most.
4. **Ergebnis** — the timeline and, under "Messdetails", start, drift and verdict per file.
5. **Hören** — files that need checking come first. Play the reference and a file together:
   one sound means in sync, an echo means an offset.
6. **Export** — "Sync" writes a timeline of the original files (nothing else is written);
   "Korrigiert" writes drift-corrected audio plus a timeline. The output folder can never be
   the originals' folder; originals are only read.

Open the `.fcpxml` in Final Cut Pro (File → Import → XML) or Logic Pro (File → Import →
Final Cut Pro XML). Tips:

- The best reference is a device that hears what everyone else hears: a recorder in the room
  or a desk with a room mic beats a camera.
- "Uhr wandert" means a clock that is not steady (or two microphones far apart in a hall);
  listen to such files.
- "Hilfe → Protokoll öffnen" shows what was measured and written — attach it to
  an issue if something looks wrong.

## Limits

- Video drift is not corrected (a camera's picture keeps its own clock; the sound is placed
  exactly, the picture within a frame) — [#5](https://github.com/MerlanBromisch/chronon/issues/5).
- Timelines for Premiere Pro ([#7](https://github.com/MerlanBromisch/chronon/issues/7)) and
  DaVinci Resolve ([#8](https://github.com/MerlanBromisch/chronon/issues/8)) come later.
- iPhone footage with variable frame rate is handled but not yet checked against real material
  ([#11](https://github.com/MerlanBromisch/chronon/issues/11)).
- A project cannot be saved and opened again yet
  ([#34](https://github.com/MerlanBromisch/chronon/issues/34)).
- The interface is German only.

## Command line

Everything the app does is also a `chronon` command (see [Development](#development) to run
it from the source):

```sh
uv run chronon analyze recorder.wav camera.mov phone.mp4   # offset + drift against the first file
uv run chronon sync -r desk/ch18.wav zoom.wav camera.mp4 -o ~/Desktop/timeline
uv run chronon correct -r desk/ch18.wav zoom.wav camera.mp4 -o ~/Desktop/synced
```

`chronon analyze` prints, for each file, where it starts in the reference (seconds), how much
faster its clock runs (ppm) and a confidence between 0 and 1 (the share of measurement windows
that agree). Files that share too little sound with the reference are flagged
`NO RELIABLE MATCH` instead of getting a made-up number.

A multitrack desk can be given as several reference tracks (`-r` repeated); each file is then
aligned to the track it matches best (often a room or ambience mic).

`chronon correct` writes new audio files on one common timeline: clock drift removed and sample
rate converted in one step (default 48 kHz), padded with silence so every file starts at timeline
zero (`--no-pad` to skip), camera audio taken from the video files. Originals are only read; the
output folder must be a separate one. Every written file is measured again against the reference
and the export fails if it is not in sync. A report (`chronon-report.txt` / `.json`) lists what
was done.

Both `correct` and `sync` write a Final Cut Pro timeline (`.fcpxml`, also importable in Logic
Pro): video above, one audio lane per device below. `correct` uses the corrected audio and
mutes the cameras' own sound; `sync` places the original files only (drift stays in, each
clip is centred so the error at its ends is halved).

`--no-pad` writes files that start with their own audio; every WAV carries a BWF time stamp
(timeline zero = 01:00:00:00) so Logic can move it to its recorded position. `--join` writes one
file per device (e.g. both halves of a Zoom take), gaps filled with silence.

With several `-r` tracks only those some file matched best are exported (`--all-refs` for all).
Files are grouped into devices automatically (`--separate` to switch off): parallel tracks
of one recording (desk channels, a recorder's inputs) are measured once and corrected alike, so
e.g. all 32 channels of a desk can be passed at once; numbered clips of one recorder
(ZOOM0003/ZOOM0004) share a timeline lane, and a clip too short to show its own drift takes it
from its sibling. A recorder's clips never overlap, and a take split into files (2 / 4 GiB)
continues exactly where the last file ends. Tracks recorded with the reference (same clock and
start) are not measured. A clip that never overlaps the reference (a camera that started before
the desk) is placed through a clip of another device it does overlap, e.g. the Zoom; the two
measurements are composed exactly.

`chronon devices FILE... --save D.json` writes the detected devices for editing (names, order,
grouping, track names, reference); `chronon analyze --devices D.json --save A.json` measures
once, and `chronon sync|correct --analysis A.json` exports from that measurement.
`chronon timeline OUTDIR` rebuilds the `.fcpxml` of an earlier run from its report. In a
terminal, all commands show the current step, its progress and the time left.

## Development

Requires Python ≥ 3.11, [uv](https://docs.astral.sh/uv/) and ffmpeg (`brew install ffmpeg`).

```sh
uv sync --extra gui
uv run pytest
uv run chronon-app                                        # the desktop app from the source
uv run chronon synth /tmp/scene --preset drift            # synthetic recordings with known truth
uv run chronon eval /tmp/scene                            # analyze a synth folder, compare with truth
```

`chronon synth` writes one WAV per clip plus a `truth.json` holding each device's true start times
and clock drift. Presets: `basic`, `drift`, `multiclip`, `music`. Design and decisions:
[ROADMAP.md](ROADMAP.md), [docs/app.md](docs/app.md), [docs/export.md](docs/export.md).

**Releases:** the "App build" workflow builds and self-tests the apps for all three systems
(Actions → App build → Run workflow: download them under the run's artifacts). Pushing a tag
`vX.Y.Z` (or `vX.Y.Z-beta.N`, a pre-release) also publishes them as a GitHub Release.

## Name

A *chronon* is the hypothetical smallest possible unit of time in physics.

## License

[MIT](LICENSE). The apps bundle ffmpeg (on macOS a GPL build from
[martin-riedl.de](https://ffmpeg.martin-riedl.de), on Windows and Linux an LGPL build from
[BtbN](https://github.com/BtbN/FFmpeg-Builds), each with its source there) and Qt / PySide6
(LGPL).
