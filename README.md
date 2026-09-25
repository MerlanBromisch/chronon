# Chronon

**Automatic waveform-based sync for multi-device recordings — with clock drift correction.**

> Status: early development. Measurement, corrected audio and Final Cut Pro timeline export work.

## Why

When you record with more than one device (camera + recorder, recorder + DAW, several cameras), the files never line up perfectly: every device has its own clock, and "48 kHz" on one is never exactly 48 kHz on another. Over a long take this adds up to audible drift — often 100+ ms after half an hour.

PluralEyes used to solve this. It is discontinued, and the sync features built into editors are slow, often imprecise, and don't correct drift at all.

Chronon aims to fill that gap: drop in a folder of media, get back perfectly aligned files and a timeline for your editor.

## Planned features

- Sync any number of audio/video sources by waveform — fast
- Measure and correct clock drift (per device, in ppm)
- Handle many clips per camera (start/stop recording, split files)
- Export to Final Cut Pro (FCPXML — also importable in Logic Pro), Premiere Pro and DaVinci Resolve
- Confidence score for every match

Ideas for later: sub-sample phase alignment of mics, clap/slate detection, device drift profiles, transcript markers, speaker-based multicam pre-cut.

See [ROADMAP.md](ROADMAP.md).

## Development

Requires Python ≥ 3.11, [uv](https://docs.astral.sh/uv/) and ffmpeg (`brew install ffmpeg`).

```sh
uv sync
uv run pytest
uv run chronon analyze recorder.wav camera.mov phone.mp4   # offset + drift against the first file
uv run chronon synth /tmp/scene --preset drift            # synthetic recordings with known truth
uv run chronon eval /tmp/scene                            # analyze a synth folder, compare with truth
```

`chronon analyze` prints, for each file, where it starts in the reference (seconds), how much
faster its clock runs (ppm) and a confidence between 0 and 1 (the share of measurement windows
that agree). Files that share too little sound with the reference are flagged
`NO RELIABLE MATCH` instead of getting a made-up number.

A multitrack desk can be given as several reference tracks; each file is then aligned to the
track it matches best (often a room or ambience mic):

```sh
uv run chronon analyze -r desk/ch01.wav -r desk/ch02.wav ... zoom.wav camera.mp4
```

`chronon correct` writes new audio files on one common timeline: clock drift removed and sample
rate converted in one step (default 48 kHz), padded with silence so every file starts at timeline
zero (`--no-pad` to skip), camera audio taken from the video files. Originals are only read; the
output folder must be a separate one. Every written file is measured again against the reference
and the export fails if it is not in sync. A report (`chronon-report.txt` / `.json`) lists what
was done.

```sh
uv run chronon correct -r desk/ch18.wav zoom.wav camera.mp4 -o ~/Desktop/synced
```

Both `correct` and `sync` write a Final Cut Pro timeline (`.fcpxml`, also importable in Logic
Pro): video above, one audio lane per device below. `correct` uses the corrected audio and
mutes the cameras' own sound; `sync` places the original files only (drift stays in, each
clip is centred so the error at its ends is halved):

```sh
uv run chronon sync -r desk/ch18.wav zoom.wav camera.mp4 -o ~/Desktop/timeline
```

With several `-r` tracks only those some file matched best are exported (`--all-refs` for all).

`chronon synth` writes one WAV per clip plus a `truth.json` holding each device's true start times
and clock drift. Presets: `basic`, `drift`, `multiclip`, `music`.

## Name

A *chronon* is the hypothetical smallest possible unit of time in physics.

## License

[MIT](LICENSE)
