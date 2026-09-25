# Chronon

Waveform-based sync for multi-device recordings with clock drift correction. Plan: `ROADMAP.md`.

## Commands
- `uv sync` — install (Python ≥ 3.11, numpy, scipy)
- `uv run pytest` — tests
- `uv run ruff check . && uv run ruff format .` — lint and format (line length 100)
- `uv run chronon synth OUTDIR --preset basic|drift|multiclip|music` — synthetic test media + `truth.json`

## Conventions
- Code, comments and docs in English.
- Source in `src/chronon/`, tests in `tests/`.
- Every sync/drift algorithm is tested against `chronon.synth` scenarios with known offset and drift.
  Drift convention: `t_true = start_s + n / (sample_rate * (1 + drift_ppm * 1e-6))` —
  positive ppm = device clock fast = file longer than the real time it covers.
- Never commit media files (see `.gitignore`); generate them with `chronon synth`.
