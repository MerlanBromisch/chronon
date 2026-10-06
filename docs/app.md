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
- Listening reuses the excerpt `Source`s of `audio.py` and plays them with QtMultimedia — no
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

### `--json` contract (version 1)
`chronon analyze|sync|correct --json …` writes one JSON object per line to stdout and
nothing else; tracebacks of unexpected errors go to stderr. Every object has `"v": 1` (bumped
when a field changes meaning or goes away; new fields may appear any time) and `"event"`:

```json
{"v": 1, "event": "progress", "step": "writing", "step_number": 2, "steps": 3,
 "what": "writing", "done": 0.42, "left_s": 18.5}
{"v": 1, "event": "result", "command": "correct", "report": "…/chronon-report.json",
 "timeline": "…/Show.fcpxml", "failed": 0, "files": [ … ]}
{"v": 1, "event": "error", "message": "…"}
```
- `progress`: `step` is one of the command's steps (`analysing`; `correct`: `analysing`,
  `writing`, `verifying`), `what` the detail (e.g. `analysing ZOOM0003.WAV`), `done` 0…1 within
  the step, `left_s` the time left in the step or `null` while it cannot be estimated yet. Measured
  in real work (seconds analysed, samples written, files checked), at most ten events a second.
- `result` (exactly one, last, on success): `files` are the same rows as the report file —
  `analyze`: offset, drift, confidence, `reliable`, windows, `via` / `drift_from` /
  `linked_via`, `notes`; `sync`: the `chronon-sync.json` rows; `correct`: the
  `chronon-report.json` rows. `correct` exits 1 when `failed` > 0.
- `error`: the run stopped (exit code 1); `message` is meant for the user.
- Files without a reliable match are not errors: their row says `"reliable": false`.

Report files (`chronon-report.json`, `chronon-sync.json`) are
`{"schema": 1, "chronon": "<version>", "kind": "correct"|"sync", "files": [rows]}`. Reports
from before 2026-10-05 are a bare list of rows (schema 0) and stay readable
(`correct.read_report`).

## Version 1 screens
1. **Drop** — files and folders; per file: duration, rate, channels, timecode / BWF start.
2. **Devices and reference** — detected devices (parallel tracks, numbered clips) and the
   reference track(s) (`-r`). The reference choice is prominent, not hidden in settings: it
   matters more than anything else.
3. **Sync** — step, percentage, time left; cancel.
4. **Result** — one row per file / device: offset, drift (ppm), confidence, `wander_ms`, and a
   clear "no reliable match" state. Re-run with another reference.
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
- [ ] Small API for listening: a mono/stereo excerpt of a file at reference time *t*, using
      the measured alignment

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

## Later
- Timeline view with waveforms (needs peak overviews from the core) and manual correction
- Premiere Pro export (#7), DaVinci Resolve check (#8)
- Signing, update hint — only on demand
