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
  - Listening: `audio.excerpt`, `listen.Timeline` (reference time ↔ file time, clips of a
    device), cached overviews (`listen.overview`, `chronon overview --cache DIR`); see
    `docs/app.md` "Listening and waveforms".
  - `devices --json` lists every file with its start (`tmcd` timecode or BWF, `audio.Info.start`);
    `--log FILE` writes a protocol per run.
  - Progress for the step list (board 06): a `plan` event, then `task` / `task_done` / `device`
    in every progress event; counted in real work, decoding included (no gap at the start).
  - Progress is weighted by real work (reference decoding counts, decoded files weigh 1.7×).
  - Export: `<device>[_<track>|_<file>]_korrigiert.<wav|caf>`, bit depth ≥ 24, `--timeline
    fcpxml|none`, `--fps`, FCPXML lanes in the user's device order.
- **CI** runs the full suite on Ubuntu, macOS and Windows (LGPL ffmpeg on Windows: no libx264,
  test videos use `mpeg4`). Windows has already caught two path bugs; expect more.
  The repo is public (since 2026-10-08), so Actions minutes are free; before that the private
  repo ran out (GitHub Pro: 3000 a month, macOS counts 10×). Runs stay lean anyway: a PR runs
  Linux only, once per push; main pushes run all three systems. Before calling a PR done, start
  CI once by hand on its branch ("Run workflow", `workflow_dispatch`) for all three systems, and
  the app build too when the app or packaging changed. Jobs time out after 20 min (app build
  30 min): a hung `apt-get` once held a runner for 6 h, another took 18 min; so Linux takes
  ffmpeg as a static build from GitHub (like Windows) and runs apt only for a missing Qt library.
- **Desktop app** (`src/chronon/gui/`, see `docs/app.md` "The app in the repo"): all six steps
  and the settings are built, following the boards and their corrections. `app.walk()` drives the
  whole flow; the app build (on demand and for `v*` tags) runs it as a self test on all three
  platforms.
- **Design**: finished. Boards in `docs/design/ui/png/{dark,light}`, spec in `docs/design/ui/README.md`
  (read its "Corrections to the boards" section: the timeline row is a format choice, multitrack
  devices are disabled targets in the move / merge dialogs, parallel-track output names).
  Decisions with reasons: `docs/design/gaps.md` ("Decided 2026-10-07").

## Next steps (in order)
1. **The user tests the app** on real material (the musical, the interview) and reports what is
   wrong or awkward; fix that first. Playback could not be heard in the cloud (no audio device).
2. Later, only with a reason: a project file to reopen a session; bundle IBM Plex; issues #5,
   #7 (Premiere), #8 (Resolve), #11, #15.

## How the user works
- Replies in German; code, comments, docs in English. Short answers, a recommendation rather
  than a list of options. Ask before deciding things that change what the user sees.
- Personal open-source project (MIT): no commitment to signing, releases or updates.
- One branch per task, a PR with a test plan, CI green on all three systems before saying done
  (the all-systems run is started by hand once per PR, see CI above).
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
