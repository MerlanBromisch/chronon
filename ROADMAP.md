# Roadmap

## Goals
These apply to every stage and decide trade-offs.

- **Fast.** Sync should feel instant, as PluralEyes did — no waiting minutes on a crawling bar.
  Target: the 4 h musical (18 desk tracks, Zoom, 4 camera files) in under a minute on an
  Apple-silicon Mac. Baseline 2026-10-05: 1242 s; now 46 s (`scripts/bench.py sync`). ✓
- **Honest progress.** Show how long the sync will still take (time remaining), based on the
  work actually left, not a bar that just moves. ✓ in the CLI: step, percentage and time left,
  measured in seconds of audio analysed / samples written / files checked; on the musical the
  prediction was within 1–2 s of the actual time left.
- **Small memory footprint.** Under 1 GB peak, independent of recording length. Baseline:
  18.5 GB; now ~690 MB ✓ — references and PCM files are read as excerpts, only video files
  are streamed once with their measurement windows kept (half precision).
- **Accurate and honest results.** Every file within 0.1 ms where the material allows it, and
  a clear "no reliable match" instead of a made-up number.

## Stage 1 — Core (CLI / drop app)
- [x] Project setup (uv, pytest, ruff, CI on Linux + macOS)
- [x] Synthetic test scenarios with known offset / drift (`chronon synth`)
- [x] Decode audio from any audio/video file (ffmpeg)
- [x] Coarse offset search on downsampled audio, sample-accurate refinement
- [x] Drift measurement (windowed cross-correlation + consensus line fit) — `chronon analyze`
- [x] Pick the best of several sample-parallel reference tracks (`--ref` repeated)
- [x] Drift correction (concept: [docs/export.md](docs/export.md))
- [x] Write corrected audio files: drift + sample rate in one step, padded to timeline zero, verified — `chronon correct`
- [x] Separate camera audio and correct it like any other source (video never re-encoded)
- [x] BWF time stamp in every WAV / RF64 output (timeline zero = 01:00:00:00); join clips per device (`--join`)
- [x] Selectable time reference (`-r`)
- [ ] RF64 (unpadded files over 2 GiB) in Logic Pro and Final Cut Pro — to verify
- [x] FCPXML export — `chronon sync` (originals) and `chronon correct` (corrected audio); DTD-valid
- [x] FCPXML imports in Final Cut Pro without warnings, picture and sound in sync (musical: 2 cameras,
  Zoom, 2 desks; confirmed 2026-10-05)
- [x] FCPXML in Logic Pro: audio tracks and video as picture reference (confirmed 2026-10-05)

## Stage 2 — Robustness
- [x] Devices: parallel tracks measured once; numbered clips grouped; short clips borrow drift — automatic, `--separate` to switch off
- [ ] Devices: manual grouping (CLI / app)
- [ ] Many clips per source; chain clips that only overlap with other sources
- [x] Confidence from window agreement; flag files without a reliable match
- [ ] Resolve conflicting matches between files
- [x] Rooms with several sources at different distances (RANSAC line fit)
- [ ] Robust features for music, silence, reverb, very different mics
- [ ] Non-linear clock drift: detected (`wander_ms`), not yet corrected (piecewise fit)
- [x] Stream long files instead of holding them in memory (see Goals)
- [ ] Video drift via retiming in the export (no video re-encode)
- [ ] Variable frame rate (phone) footage
- [ ] Premiere Pro XML and DaVinci Resolve export

## Stage 3 — Mac app
- [ ] Timeline view with confidence per clip
- [ ] Manual correction of wrong matches
- [ ] Signed & notarized build

## Ideas
- Sub-sample phase / polarity alignment of mics recording the same source
- Clap / slate detection as extra anchor
- Mixed timecode + waveform sync
- Device drift profiles
- Transcript markers (Whisper)
- Speaker-based multicam pre-cut for interviews
- Loudness matching, best-channel selection
- Watch folder / card ingest
