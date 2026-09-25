# Chronon

Waveform-based sync for multi-device recordings with clock drift correction. Plan: `ROADMAP.md`.

## Commands
- `uv sync` — install (Python ≥ 3.11, numpy, scipy; ffmpeg must be on PATH)
- `uv run pytest` — tests
- `uv run ruff check . && uv run ruff format .` — lint and format (line length 100)
- `uv run chronon synth OUTDIR --preset basic|drift|multiclip|music` — synthetic test media + `truth.json`
- `uv run chronon eval OUTDIR` — run the aligner on a synth folder and print errors against truth
- `uv run chronon analyze REF FILE...` — offset/drift of files against a reference
- `uv run chronon analyze -r TRACK -r TRACK ... FILE...` — several sample-parallel reference tracks (one desk); each file uses its best match

## Layout
- `audio.py` — ffmpeg decoding to mono float32, resampling
- `align.py` — offset + drift between two signals (coarse → fine windows every 10 s → RANSAC line → drift-compensated refine)
- `synth.py` — synthetic scenarios with ground truth
- `cli.py` — `chronon` command

## Conventions
- Code, comments and docs in English.
- Source in `src/chronon/`, tests in `tests/`.
- Every sync/drift algorithm is tested against `chronon.synth` scenarios with known offset and drift.
  Drift convention: `t_true = start_s + n / (sample_rate * (1 + drift_ppm * 1e-6))` —
  positive ppm = device clock fast = file longer than the real time it covers.
  `align.Alignment` uses the same sign: `ref_time(t) = offset_s + t / (1 + drift_ppm * 1e-6)`.
- Accuracy target in tests: every point of a clip within 0.1 ms of truth.
- Lessons from real recordings (live musical, interview):
  - In a room, many windows yield random lags (another source dominates). Never fit all windows
    and trim afterwards — find the line most windows agree on (RANSAC), then refine.
  - `confidence` = share of windows agreeing with the line, not correlation strength
    (crosstalk at |ncc| 0.05 can be accurate to 0.1 ms).
  - Which track of a multitrack desk is the reference matters more than anything else.
  - Some clocks (battery recorders) wander by ±2 ms over hours — not a straight line.
  - A multi-source effect must first be reproduced in `chronon.synth` (see `test_room_*`).
- Never commit media files (see `.gitignore`); generate them with `chronon synth`.
