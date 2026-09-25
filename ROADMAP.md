# Roadmap

## Stage 1 — Core (CLI / drop app)
- [x] Project setup (uv, pytest, ruff, CI on Linux + macOS)
- [x] Synthetic test scenarios with known offset / drift (`chronon synth`)
- [x] Decode audio from any audio/video file (ffmpeg)
- [x] Coarse offset search on downsampled audio, sample-accurate refinement
- [x] Drift measurement (windowed cross-correlation + consensus line fit) — `chronon analyze`
- [x] Pick the best of several sample-parallel reference tracks (`--ref` repeated)
- [ ] Drift correction
- [ ] Write corrected audio files aligned to a reference
- [ ] FCPXML export (Final Cut Pro, Logic Pro)

## Stage 2 — Robustness
- [ ] Many clips per source; chain clips that only overlap with other sources
- [x] Confidence from window agreement; flag files without a reliable match
- [ ] Resolve conflicting matches between files
- [x] Rooms with several sources at different distances (RANSAC line fit)
- [ ] Robust features for music, silence, reverb, very different mics
- [ ] Non-linear clock drift: detected (`wander_ms`), not yet corrected (piecewise fit)
- [ ] Stream long files instead of holding them in memory (4 h reference ≈ 10 GB now)
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
