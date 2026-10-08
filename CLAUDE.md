# Chronon

Waveform-based sync for multi-device recordings with clock drift correction. Plan: `ROADMAP.md`.

## Commands
- `uv sync` — install (Python ≥ 3.11, numpy, scipy; ffmpeg must be on PATH)
- `uv run pytest` — tests
- `uv sync --extra gui`, `uv run chronon-app` — the desktop app (PySide6); its tests need `QT_QPA_PLATFORM=offscreen` without a screen
- `uv run ruff check . && uv run ruff format .` — lint and format (line length 100)
- `uv run chronon synth OUTDIR --preset basic|drift|multiclip|music` — synthetic test media + `truth.json`
- `uv run chronon eval OUTDIR` — run the aligner on a synth folder and print errors against truth
- `uv run chronon analyze REF FILE...` — offset/drift of files against a reference
- `uv run chronon sync [-r TRACK]... FILE... -o OUTDIR` — FCPXML of the originals (nothing corrected)
- `uv run chronon timeline OUTDIR` — rebuild the .fcpxml of an earlier sync/correct run from its report (seconds)
- `uv run chronon correct [-r TRACK]... FILE... -o OUTDIR` — write drift/rate-corrected, padded audio + report, then verify
- `uv run chronon analyze -r TRACK -r TRACK ... FILE...` — several sample-parallel reference tracks (one desk); each file uses its best match
- `--json` on analyze/sync/correct — JSON lines for the desktop app (contract in `docs/app.md`; change it only with a version bump)
- `uv run chronon devices FILE... --save D.json` — devices before a reference exists + suggested reference; edit D.json (names, order, grouping, track names, reference), then `chronon analyze --devices D.json`
- `uv run chronon analyze ... --save A.json`, then `chronon sync|correct --analysis A.json -o OUTDIR` — export without analysing again (refused if a source changed)
- `uv run chronon overview FILE... --cache DIR` — waveform overviews (min/max peaks) for the app, cached
- `--log FILE` on devices/analyze/sync/correct/overview — protocol of the run (for "Protokoll öffnen")
- `correct --timeline fcpxml|none`, `sync|correct --fps 29.97` — timeline format (none = audio only; Premiere/Resolve later) / frame rate

## Layout
- `audio.py` — ffmpeg decoding, `Source`s for excerpt access (PCM via libsndfile seek, libsoxr resampling)
- `align.py` — offset + drift: coarse (one desk track at 2 kHz) → screen desk tracks → fine windows every 10 s → RANSAC line → drift-compensated refine. Never holds whole recordings at 16 kHz.
- `scripts/bench.py` — benchmark on the musical (time per phase, peak memory); results in `scripts/bench-results.jsonl`
- `devices.py` — groups files into devices: parallel tracks (same recording, several channels) and numbered clips of one recorder
- `correct.py` — corrected audio export (libsoxr at the real rate, libsndfile WAV/CAF, streamed) + verification + report
- `fcpxml.py` — FCPXML 1.11 timeline (frame-aligned offsets, sample-accurate trims, asset start = timecode/BWF)
- `synth.py` — synthetic scenarios with ground truth
- `listen.py` — listening and waveforms: reference time ↔ file time (`Timeline`), excerpt pairs, cached peak overviews
- `messages.py` — notes and user-fixable errors as codes (the app translates; English texts here)
- `cli.py` — `chronon` command
- `gui/` — desktop app: `window.py` (shell), `theme.py` (design tokens), one `*_page.py` per step, `audition.py` (waveforms, playback), `jobs.py` (`chronon … --json` in a child process), `app.py` (`walk()` = the whole flow, `--selftest`); see `docs/app.md`

## Conventions
- Code, comments and docs in English.
- Source in `src/chronon/`, tests in `tests/`.
- Every sync/drift algorithm is tested against `chronon.synth` scenarios with known offset and drift.
  Drift convention: `t_true = start_s + n / (sample_rate * (1 + drift_ppm * 1e-6))` —
  positive ppm = device clock fast = file longer than the real time it covers.
  `align.Alignment` uses the same sign: `ref_time(t) = offset_s + t / (1 + drift_ppm * 1e-6)`.
- Accuracy target in tests: every point of a clip within 0.1 ms of truth. On real acoustic pairs
  the measurement itself only repeats to ~0.05–0.35 ms (depends on the window grid); electrical
  pairs repeat to ~0.001 ms.
- Lessons from real recordings (live musical, interview):
  - In a room, many windows yield random lags (another source dominates). Never fit all windows
    and trim afterwards — find the line most windows agree on (RANSAC), then refine.
  - `confidence` = share of windows agreeing with the line, not correlation strength
    (crosstalk at |ncc| 0.05 can be accurate to 0.1 ms).
  - Which track of a multitrack desk is the reference matters more than anything else.
  - Some clocks (battery recorders) wander by ±2 ms over hours — not a straight line.
  - A multi-source effect must first be reproduced in `chronon.synth` (see `test_room_*`).
  - One recorder records one thing at a time: its clips never overlap. A match that puts a
    clip over its own sibling (the musical's Zoom: the end of ZOOM0003 against the start of
    ZOOM0004, 513 s off, "reliable") is a false match and is dropped. A take split into files
    (2 / 4 GiB) continues exactly where the last file ends: proven by BWF time-of-day stamps or
    a file ending at the size limit, never by camera timecode (record-run counts only while
    recording).
- Final Cut import (tested 2026-10-05): asset `start` must equal what FCP reads as media start.
  Sony XAVC S MP4 keeps its timecode only in an `rtmd` track that FCP ignores (media start 0);
  only `tmcd` timecode counts. Formats need FCP's name (`FFVideoFormat3840x2160p25`).
- Files for the user to open go to `~/Documents/Chronon Test/`, never to `/private/tmp`.
- Never commit media files (see `.gitignore`); generate them with `chronon synth`.
- Never modify the user's original media; outputs go to a separate folder (enforced in `correct`).
- Export design decisions: `docs/export.md`. Desktop app decisions: `docs/app.md`.
- Devices: parallel tracks share one measurement (screened as (reference track, track) pairs; an electrical copy
  of a desk channel beats any acoustic match); clips keep their own drift (clocks change rate over hours),
  only clips too short/weak borrow a sibling's. Clips without a reliable match to the reference are linked
  through placed clips of other devices (`_link`, composed exactly; verified against the bridge's output). Coarse search tries loud tracks first (quiet desk channels rarely share).
