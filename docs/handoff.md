# Handoff (2026-10-07)

For the next Claude session (cloud). State of the work, how the user works, and what comes next.
Read `CLAUDE.md`, `ROADMAP.md`, `docs/app.md` and `docs/design/gaps.md` first. Update or delete
this file when it is out of date.

## Where things stand
- `main` (`babc174`) holds everything merged; no open PRs. Remote branches other than `main` and
  `spike/packaging` are merged leftovers and can be deleted.
- **Core** (Python, `src/chronon/`):
  - `chronon devices` detects devices before a reference exists and suggests one; the result is
    an editable layout JSON (`devices.Layout`).
  - `chronon analyze --devices D.json --save A.json` measures once.
  - `chronon sync|correct --analysis A.json` export without measuring again; a changed or
    missing source is refused.
  - `--json` on devices/analyze/sync/correct: JSON lines for the desktop app. The contract is in
    `docs/app.md`; change it only with a version bump. Version 2: `notes` and user-fixable
    errors are codes (`chronon.messages`), the app translates them; reports are schema 2.
  - Progress is weighted by real work (reference decoding counts, decoded files weigh 1.7×).
  - Export: `<device>[_<track>|_<file>]_korrigiert.<wav|caf>`, bit depth ≥ 24, `--timeline
    fcpxml|none`, `--fps`, FCPXML lanes in the user's device order.
- **CI** runs the full suite on Ubuntu, macOS and Windows (LGPL ffmpeg on Windows: no libx264,
  test videos use `mpeg4`). Windows has already caught two path bugs; expect more.
- **Desktop app**: decided, not built. Python + PySide6, analysis in a child process
  (`chronon … --json`). The packaging spike on `spike/packaging` passed on all three platforms
  (PyInstaller, bundled ffmpeg, headless self test in `.github/workflows/app.yml`). It still uses
  its own worker; switch it to `chronon … --json` (contract v2) when the app starts.
- **Design**: finished. Boards in `docs/design/ui/png/{dark,light}`, spec in `docs/design/ui/README.md`
  (read its "Corrections to the boards" section: the timeline row is a format choice, multitrack
  devices are disabled targets in the move / merge dialogs, parallel-track output names).
  Decisions with reasons: `docs/design/gaps.md` ("Decided 2026-10-07").

## Next steps (in order)
1. **Listening and waveforms** (gaps.md 1.6): `audio.excerpt(path, start_s, seconds, rate)` with
   a fast seek; reference time → file time per row (joined clips, devices); min/max overview of
   a whole track, cached on disk (cache folder from settings).
2. **Smaller** (gaps.md 1.7): MP4 `tmcd` timecode into `audio.probe` (only `fcpxml.py` parses it
   now) for the start column; a log file per run for "Protokoll öffnen".
3. **Build the app** from `spike/packaging` (rebase onto `main`), screen by screen following the
   boards: Dateien → Geräte & Referenz → Sync → Ergebnis → Hören → Export, plus settings.
4. Later, only with a reason: progress has one 6–7 s gap while the first video decodes in the
   background; issues #5, #7, #8, #11, #15.

## How the user works
- Replies in German; code, comments, docs in English. Short answers, a recommendation rather
  than a list of options. Ask before deciding things that change what the user sees.
- Personal open-source project (MIT): no commitment to signing, releases or updates.
- One branch per task, a PR with a test plan, CI green on all three systems before saying done.
  **The user merges PRs** (merging is blocked for Claude) and then runs `git pull` locally.
- The user designs in a Claude Desktop session that writes straight into their local
  `docs/design/`. Never switch branches in their main checkout; commit design files from a
  separate worktree.
- Files for the user to open go to `~/Documents/Chronon Test/` on their Mac (not reachable from
  the cloud). Never commit media; generate test media with `chronon synth`.

## Real material (only on the user's Mac, not in the cloud)
Algorithm changes need a check on real recordings: ask the user to run it locally, or describe
the run for them. Known truths to compare against:
- **Musical** (4 h, external drive T7, `scripts/bench.py`): reference = Presonus desk tracks
  17/18 (18 is the best room track). x32 desk +15.207 s / −12.01 ppm (electrical copy of the
  band leader on x32 24, 0.07 ms RMS); ZOOM0003 +97.021 s / −8.23 ppm, ZOOM0004 −11.21 ppm (the
  clock changes rate between the halves of one take); Sony C2378/C2379 −6.37 / −5.69 ppm;
  R62_0040/0041 −3.18 / −2.74 ppm. Full analysis 46–80 s depending on the drive's cache,
  `sync --analysis` 1.5 s, peak memory ~0.5–0.7 GB.
- **Interview** (local Logic project): Zoom H5 (3 parallel tracks) vs Logic recording
  `Merlan #01.wav`: +3.333 s, −153.38 ppm, confidence 0.88.
- Lessons that shaped the aligner are in `CLAUDE.md` (RANSAC over trimming, confidence = window
  agreement, reference choice matters most, some clocks wander).
