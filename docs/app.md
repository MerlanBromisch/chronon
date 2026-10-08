# Desktop app

Decisions for Chronon's graphical app (Stage 3 in `ROADMAP.md`). Decided 2026-10-05.

## Scope and stance
- A personal project first. It is free and open source, but nobody is committed to regular
  releases, updates, signing or support. Those happen only if a real need shows up.
- One app for macOS, Windows and Linux, all equally supported by the code. "Supported" means
  it builds and runs from CI; it does not promise signed installers.
- Version 1 is **drop & export**: drop files, check devices and reference, sync, read the result
  table, listen to a match, export. Fixing a wrong match by hand happens in the editor (Final
  Cut, Logic …). A timeline editor inside the app comes later; the architecture must allow it.

## Toolkit: Python + PySide6 (Qt)
The core is Python (numpy, scipy, libsoxr, libsndfile, ffmpeg) and stays the only language.

| Option | Why not (for now) |
|---|---|
| Tauri + web UI | Three toolchains (Rust, Node, Python) plus a Python sidecar to bundle; on Linux the system WebView is WebKitGTK, the weakest platform for rendering and audio. Its main advantage, a small download, does not matter here. |
| Electron + web UI | Two runtimes (Node + Python) and a protocol between them for every feature, including audio playback. Reasonable plan B if Qt packaging fails. |
| Native per platform | Three UIs. |

Why PySide6:
- One process, one language, one bundle per platform; the GUI imports `chronon` directly.
- Qt is mature on all three platforms, including Linux.
- Listening uses `chronon.listen` (excerpts, below) and plays them with QtMultimedia — no
  audio over a protocol.
- Drag & drop yields real file paths (a browser never does).
- LGPL, fits any open-source license. A timeline view later fits `QGraphicsView`.

Look: Fusion style with a dark palette and a small stylesheet. Video and audio tools rarely look
native anyway.

## Architecture
```
chronon (core, unchanged)  ←  chronon.gui (PySide6, optional extra: uv sync --extra gui)
                                 │
  sync / correct jobs  ◄── child process: `chronon … --json` (JSON lines: progress, result)
  probe, listening     ◄── directly in the GUI process via audio.py
  project file         =   the existing report (JSON, with a schema version)
```
- **Jobs run in a child process**, never in a GUI thread: cancel = terminate the process, all its
  memory is returned, a crash in the core does not take the GUI down, and the GIL never makes
  the UI stutter. The same `--json` interface is useful for scripts.
- **Contract between GUI and core** = the `--json` event stream + the report schema. Both carry
  a version number, so the core can keep changing without breaking the GUI.
- The "never write next to the originals" rule stays in the core (`correct`), the GUI only
  repeats the check early to show a clear message.

### `--json` contract (version 2)
`chronon analyze|sync|correct --json …` writes one JSON object per line to stdout and
nothing else; tracebacks of unexpected errors go to stderr. Every object has `"v": 2` (bumped
when a field changes meaning or goes away; new fields may appear any time) and `"event"`:

```json
{"v": 2, "event": "progress", "step": "writing", "step_number": 2, "steps": 3,
 "what": "writing", "done": 0.42, "left_s": 18.5}
{"v": 2, "event": "result", "command": "correct", "report": "…/chronon-report.json",
 "timeline": "…/Show.fcpxml", "failed": 0, "files": [ … ]}
{"v": 2, "event": "error", "code": "not_enough_space", "message": "needs 3.1 GiB but …",
 "folder": "…", "need_bytes": 3328599654, "free_bytes": 1073741824}
```
Version 2 (2026-10-07): `notes` are objects with a code, errors carry a code (below).
Version 1 had English sentences in both.
- `plan` (first, once): the step list of board 06. `steps` = objects with `step` (the
  command's step it belongs to), `id`, `kind` and `device` (or null). The analysis brings
  `read` (kind `read`: lengths and levels of the files), `reference` (kind `reference`, the
  reference device: its waveform for the coarse search), one `compare:<k>` per device (kind
  `compare`, in the user's device order) and `drift` (kind `drift`: placing clips through
  others, borrowed drift); `correct` adds `writing` / `verifying`, `overview` its `overview`.
  Without an analysis (`--analysis`) only the command's other steps are listed.
- `progress`: `step` is one of the command's steps (`analysing`; `correct`: `analysing`,
  `writing`, `verifying`), `what` the detail (e.g. `analysing ZOOM0003.WAV`, English), `done`
  0…1 within the step, `left_s` the time left in the step or `null` while it cannot be
  estimated yet (before 3 % is done). `task` is the plan step running now, `task_done` 0…1
  within it, `device` its device or null. Steps run in the plan's order; a step is done when
  the next one starts. Measured in real work (seconds analysed, samples written, files
  checked): the files' decoding counts (the next clip decodes in the background while one is
  analysed; its work is credited to its own step, which can start at more than 0), the coarse
  search and the measurement count window by window. `done` stays 0 until the files' lengths
  are read, and never goes back: work nobody foresaw (a further reference track, a clip
  linked through another) slows it down (the rest of the bar stands for the rest of the work).
  The app shows at most 99 % until the `result` arrives. At most ten events a second, but the first
  event of each task always goes out.
- `result` (exactly one, last, on success): `files` are the same rows as the report file —
  `analyze`: offset, drift, confidence, `reliable`, windows, `via` / `drift_from` /
  `linked_via`, `notes`, and `placement` (where the file lands on a timeline of the originals:
  `position_s`, `duration_s`, `has_video`, `video_error_ms`, `variable_rate`); plus
  `frame_rate` of the timeline and `analysis` (the file of `--save`, else null). `sync`: the
  `chronon-sync.json` rows; `correct`: the `chronon-report.json` rows, `timeline` null with
  `--timeline none`. `correct` exits 1 when `failed` > 0.
- **Devices first:** `chronon devices --json FILE...` → `result` with `layout` (the
  editable device layout: `devices` in timeline order with `name`, `clips`
  (lists of parallel tracks), `track_names`; `reference`, `reference_tracks`, `suggested`) and
  `devices` (per device `kind` = tracks / clips / video_clips / file / video, `clips`,
  `tracks`, `files`, `sample_rate`, `channels`, `duration_s`, `has_video`) and `files` (the
  file list of step 1, per file `file`, `duration_s`, `sample_rate`, `channels`, `has_video`,
  `codec`, `start_s` = the media's own start as editors read it: video timecode from a `tmcd`
  track, else the BWF time stamp, else null; `timecode` as written, `frame_rate`). The app edits
  the layout, saves it and runs `analyze --devices FILE`. A layout is refused when a file is in two
  devices, two names collide (ignoring case), a name cannot be a file name, or the reference
  tracks are not parallel tracks of the reference device.
- **Analyse once, export later:** `analyze --save A.json` keeps the analysis;
  `sync` / `correct --analysis A.json` export from it without measuring again (the app's Sync
  step, then its Export step). The file stores each source's size and modification time; a
  changed or missing source is an `error`.
- `notes` (rows of every command and of the report files): a list of objects
  `{"code": …, …fields}`, translated by the app; the terminal builds its English text from the
  same codes (`chronon.messages.TEXTS`). Paths are full paths; the texts show the file name.

  | code | fields | meaning |
  |---|---|---|
  | `no_reliable_match` | | position and drift may be wrong |
  | `inverted` | | polarity inverted against the reference |
  | `clock_wanders` | `ms` | the clock wanders ±`ms` around the straight line |
  | `matched_track` | `file` | best match among several reference tracks |
  | `reference_clock` | | a reference track: same clock and start as the reference |
  | `measured_via` | `file` | parallel track whose measurement this file shares |
  | `drift_from` | `file` | clip too short / weak: drift borrowed from this sibling clip |
  | `linked_via` | `file` | no reliable overlap with the reference; placed through this file |
  | `joined` | `files` | `--join`: these clips in one output |
  | `wav_over_2gib` | | WAV over 2 GiB: a few programs may not read it |
  | `caf_over_2gib` | | written as CAF because it is over 2 GiB |
  | `caf_no_time_stamp` | | CAF holds no time stamp: place it from the timeline file |
  | `video_unchanged` | | audio of a video file; the video itself is not changed |
  | `video_placed` | `ms`, `file`? | video placed within ±`ms` (`file`: which clip of a joined output) |
  | `variable_frame_rate` | `file`? | variable frame rate: check picture against sound at the end |
  | `not_verifiable` | `reason` | verification could not measure (`reason`: English text) |
  | `verification_failed` | | output is not in sync with the reference |
  | `verification_failed_via` | `file` | its parallel track `file` failed verification |
  | `text` | `text` | a note of a report written before codes (schema < 2) |
- **Protocol:** `--log FILE` on devices / analyze / sync / correct / overview writes a
  protocol of the run (versions, ffmpeg, command, per file what was measured, written and
  verified, step durations, errors with traceback), a new file per run; for "Protokoll öffnen".
  The worker's stderr holds only tracebacks of unexpected errors, which the protocol has too.
- `error`: the run stopped (exit code 1); `message` is English text. `code` is set for errors
  the user can fix, with its fields next to it; `null` otherwise (show `message`).

  | code | fields |
  |---|---|
  | `ffmpeg_missing` | `tool` |
  | `unreadable_file` | `file`, `detail` (ffmpeg's message) |
  | `no_audio` | `file` |
  | `outdir_holds_input` | `folder`, `file` |
  | `not_enough_space` | `folder`, `need_bytes`, `free_bytes` |
  | `output_exists` | `file` |
  | `too_large_for_wav` | `file`, `bytes` |
  | `source_missing` | `file` (a saved analysis' source is gone) |
  | `source_changed` | `file` (changed since the analysis) |
- Files without a reliable match are not errors: their row says `"reliable": false`.

Report files (`chronon-report.json`, `chronon-sync.json`) are
`{"schema": 2, "chronon": "<version>", "kind": "correct"|"sync", "files": [rows]}`. Reports
from before 2026-10-05 are a bare list of rows (schema 0) and stay readable
(`correct.read_report`). Schema 2 (2026-10-07): notes are codes; `read_report` turns the text
notes of older reports into `{"code": "text", "text": …}`.

## Version 1 screens
1. **Drop** — files and folders; per file: duration, rate, channels, timecode / BWF start.
2. **Devices and reference** — detected devices (parallel tracks, numbered clips) and the
   reference track(s) (`-r`). The reference choice is prominent, not hidden in settings: it
   matters more than anything else.
3. **Sync** — step, percentage, time left; cancel.
4. **Result** — one row per file / device: offset, drift (ppm), `wander_ms`, and a clear "no
   reliable match" state (the confidence stays in the protocol: a low share of agreeing windows
   is normal in a room and read as a failure). Re-run with another reference.
5. **Listen** — play a short excerpt around a chosen point: reference and file together
   (e.g. reference left, file right, or mixed). Latency does not matter.
6. **Export** — sync (originals) or correct (corrected audio), WAV / CAF, `--join`, output
   folder (never the originals' folder). Timeline formats: FCPXML now; Premiere (#7) and
   Resolve (#8) later.

## Packaging
- PyInstaller, built by a GitHub Actions matrix (macOS arm64, Windows x64, Linux x64):
  `.dmg` / `.zip`, a Windows `.zip` or simple installer, an AppImage.
- ffmpeg: ship an LGPL build (we only decode).
- **Unsigned.** The README explains how to open an unsigned app (macOS: right-click → Open,
  or `xattr -dr com.apple.quarantine`; Windows: SmartScreen "More info → Run anyway").
  If signing is ever needed: Apple Developer account for notarization; SignPath.io offers free
  code signing for open-source projects on Windows.
- No auto-updater. At most a "new version available" hint from GitHub Releases, later.

## Prerequisites in the core
- [x] `--json` mode for `analyze`, `sync`, `correct` (contract above) and a versioned report schema
- [x] Windows in CI; path robustness (drive letters, long paths, Unicode normalisation, case-
      insensitive file systems), including the originals check (compare real paths / same file)
- [x] Small API for listening (`chronon.listen`, below)

## Listening and waveforms (`chronon.listen`)
The GUI imports these directly (no child process; an excerpt takes well under a second):
- `audio.excerpt(path, start_s, seconds, rate)`: mono float32, zeros outside the recording,
  always `round(seconds * rate)` samples. PCM seeks to the sample; video and compressed audio
  are decoded by ffmpeg from the nearest point before (`-ss` before `-i`).
- `listen.Timeline(analysis)`: reference time ↔ file time for every file, reference tracks
  included. `at(ref_t, device)` = the files (clips, parallel tracks) playing at `ref_t`;
  `span(path)`, `file_time(path, ref_t)`, `suggest(path)` = a start worth listening at (the
  middle window the measurement agreed on); `pair(path, ref_t)` = reference track left, file
  right. Timeline time = `ref_t - Timeline.zero`. The file plays at its own rate: a 60 ppm
  clock runs 0.6 ms ahead after 10 s of listening, inaudible.
- Overviews: `listen.overview(path, cache_dir)` = min/max peaks, 100 pairs a second over all
  channels, int8, kept in `cache_dir` (keyed by real path, size, modification time);
  `listen.reduce(peaks, width)` squeezes them into a widget's columns. A 4 h desk track is a
  2.9 MB file. Computing one reads the whole file (a video takes long), so the app runs
  `chronon overview --json FILE... --cache DIR` in a child process: `progress` (step
  `overview`), then `result` with `files` = `file`, `overview` (the cache file, a `.npy`),
  `peaks_per_s`, `peaks`.

## First step: packaging spike
A minimal PySide6 window — drop files, run `analyze` in a child process, progress bar, result
table — built by CI for all three platforms. It answers early: does PyInstaller bundle
PySide6 + numpy + scipy + soxr + soundfile + ffmpeg, how big is it, how fast does it start.
If it fails, Electron with the same `--json` contract is plan B; nothing in the core is lost.

### Spike result (2026-10-05, branch `spike/packaging`)
Passed on all three platforms. `.github/workflows/app.yml` builds with PyInstaller, then runs
the frozen worker on synthetic media (one file AAC, decoded by the bundled ffmpeg, no system
ffmpeg on PATH) and a headless GUI self test (offscreen Qt: drop → worker → result table →
listening excerpt).

| | Build | Worker run | GUI self test | Unpacked | Zip |
|---|---|---|---|---|---|
| macOS 14 arm64 | 40 s | 1 s | 3 s | 302 MB | ~128 MB |
| Windows | 105 s | 7 s | 10 s | 513 MB | 206 MB |
| Ubuntu 22.04 | 50 s | 2 s | 3 s | 622 MB | 251 MB |

Findings:
- The windowed Windows build can talk JSON over stdout to its parent (worker falls back to fd 1
  when `sys.stdout` is None).
- QtMultimedia lives in `pyside6-addons`, not `pyside6-essentials`; both are needed.
- About 130 MB per platform are the static `ffmpeg` + `ffprobe` executables. QtMultimedia
  already ships FFmpeg 7.1 as LGPL shared libraries. Decoding through libraries (e.g. PyAV, whose
  wheels bundle LGPL FFmpeg) instead of the executables would remove that size and the licence
  question below. Not urgent.
- No LGPL `ffmpeg` download exists for macOS arm64; the spike uses martin-riedl.de's GPL build.
  Shipping a GPL executable next to an MIT app is aggregation (allowed, with a pointer to its
  source), but an LGPL route is cleaner — see the point above.
- Size not trimmed yet (unused Qt modules, Linux pulls in many libraries). Download size does
  not matter for now.

## The app in the repo (`src/chronon/gui/`, since 2026-10-07)
Install with `uv sync --extra gui`, start with `uv run chronon-app` (or `python -m chronon.gui`).
- `window.py`: sidebar (six steps, settings), header, pages, footer; `theme.py`: the tokens of
  `docs/design/ui/README.md` as palette and style sheet (dark / light / system).
- `files_page.py` + `project.py`: step 1. Files and folders (searched with subfolders for audio
  and video), read in the GUI process on a few threads (`audio.probe`, one ffprobe each), so the
  list shows "23 / 56" while it reads. Unreadable files ask one by one (board 04).
- `devices_page.py`: step 2 (boards 05–05c). `devices.detect` runs on a thread with the infos
  of step 1 (it only reads levels for the suggestion); the page edits the project's
  `devices.Layout` through its methods (`move_device`, `move_files`, `merge`), which keep the
  reference and the suggestion on their devices. Rename in place, reorder
  by the grip or the row menu, regroup clips (multitrack devices are disabled targets),
  reference device by its radio, reference tracks as chips (double click names one), and the
  audition of a reference track.
- `audition.py`: `Waveform` (a whole overview, one column per pixel on a dB scale, cursor, click
  to seek, "being computed" with its share), `Player` (QtMultimedia pulling one continuous
  stereo float stream that a thread decodes ~12 s ahead from a source function), `Overviews`
  (`chronon overview` in jobs, two at a time, what is shown first; after a sync the files to
  listen to are computed ahead into the settings' cache folder), `Audition` (step 2's player).
- `sync_page.py`: step 3. Saves the layout into the session folder and runs
  `analyze --devices devices.json --save analysis.json --log logs/sync-N.log`; the plan event
  becomes the step list (board 06), cancel kills the job (board 07). Going back to step 2 and on
  without changes shows the result again without measuring.
- `result_page.py`: step 4. Verdict, a view-only timeline from the result's placements drawn
  like an editor's (ruler, lanes, regions with name and waveform), the details table with
  German notes (`texts.py`) and verdict badges (README "Verdict badges").
- `listen_page.py`: step 5. Files to check first, then reliable ones; the file's whole waveform
  under the reference's for the same time (`listen.window`), played from a position side by side
  or mixed (`listen.Timeline.pair`).
- `export_page.py`: step 6. Sync or Korrigiert from the saved analysis (`sync|correct
  --analysis`), with the corrections to the boards (timeline as a format choice); the folder
  of the originals is refused before the run; the outcome lists each file with its check.
- `settings_page.py` + `settings.py`: language (Deutsch only), appearance switched live, the
  cache folder (choose, clear), the session's protocols, about. Each start of the app gets a
  session folder (`<app data>/sessions/<time>/`) for its layout, analysis and protocols.
- `texts.py` (the core's codes in German), `widgets.py` (badges, banners, step list, cards).
- `jobs.py`: a `Job` runs `chronon <command> --json` in a child process (`QProcess`) and turns
  its events into signals; cancel = kill. The frozen app runs itself with `--worker <command>`.
- `app.py`: `walk()` drives every step like a user (read, devices, sync, result, listen,
  export); `--selftest FOLDER` runs it without a screen, and the app build
  (`.github/workflows/app.yml`) runs that on all three platforms. The build runs on demand
  ("Run workflow" on a branch) and for `v*` tags, not on every push.
- Tests: `tests/test_gui.py` (offscreen Qt; CI installs the `gui` extra).
- Not yet: IBM Plex (not bundled; the system's sans and mono fonts are used), other languages,
  a project file to reopen a session later.

## Later
- Timeline view with waveforms (needs peak overviews from the core) and manual correction
- Premiere Pro export (#7), DaVinci Resolve check (#8)
- Signing, update hint — only on demand
