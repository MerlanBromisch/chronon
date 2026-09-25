# Chronon

**Automatic waveform-based sync for multi-device recordings — with clock drift correction.**

> Status: early development. No sync yet — only the test signal generator.

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

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
uv run chronon synth /tmp/scene --preset drift   # synthetic recordings with known offset and drift
```

`chronon synth` writes one WAV per clip plus a `truth.json` holding each device's true start times
and clock drift. Presets: `basic`, `drift`, `multiclip`, `music`.

## Name

A *chronon* is the hypothetical smallest possible unit of time in physics.

## License

[MIT](LICENSE)
