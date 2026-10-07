# Design vs. core: what is missing

The screens in `ui/` checked against the core as of 2026-10-06 (`main` after #13). Part 1 is work
in the core, in the order the app needs it. Part 2 lists open questions in the design. Part 3 lists
notes for building it in Qt.

## 1. Core work

### 1.1 Analyse once, export later (biggest gap)
The app analyses in step 3 (Sync), shows the result in step 4 and exports in step 6. The core
runs the analysis inside every command instead: `sync` and `correct` each analyse again, which
takes ~46 s on the musical, a second time just to export. Needed:
- `chronon analyze --json --save analysis.json`, plus `sync` / `correct --analysis analysis.json`
  that skip the analysis. `correct` still verifies its output.
- The analysis must also carry what the result screen shows before any export: per video clip
  "video placed within ±x ms" (now computed only while the FCPXML is written) and the timeline
  frame rate detected from the videos (see 1.5).

### 1.2 Devices before a reference exists (done: `chronon devices`, `analyze --devices`)
Step 2 lists every device first and the user then picks the reference device and its tracks.
`devices.group` needs the reference tracks as input and names that device `"reference"`. Needed:
- Group without a reference: `chronon devices FILE... --json` gives per device its name, kind
  (parallel tracks / clips / video clips), tracks, clips, rate and channels.
- A suggested reference (the "Kandidat" role): for example the device with the most parallel
  tracks that covers the longest time.
- The reference device keeps its own name ("Presonus"), so `device` in the rows says which
  device it is.

### 1.3 Notes as codes, not English sentences (done: `chronon.messages`, `--json` v2)
Rows carry `notes` as English text ("measured via …", "drift from …", "clock wanders ±1.8 ms",
"written as CAF: over 2 GiB"). The UI is German, and the language setting allows other
languages. Needed: structured notes such as `{"code": "measured_via", "file": "…"}` and
`{"code": "clock_wanders", "ms": 1.8}`, translated by the app. The terminal output keeps English
text built from the same codes. Errors (`error` events) need the same treatment for the cases a
user can fix: unreadable file, output folder holds the originals, not enough space.

### 1.4 Progress the step list can show
Board 06 shows a plan: "Dateien einlesen", "Wellenformen berechnen", "Spuren vergleichen:
<Gerät>" for each device, and "Drift messen und prüfen", each with its own duration. Progress
events today have `step` = `analysing` and a free-text `what`. Needed:
- A `plan` event at the start that lists the steps, with device names.
- `progress` events with the step's id and device. The weights must come from real work (see the
  open progress work: fixed 15 % / 40 % jumps inside a clip, and setup time counted as 0 %).

### 1.5 Export options from the export form
- **Timeline format:** `correct --timeline fcpxml|none` (done; Premiere / Resolve join the list later).
- **Timeline frame rate:** fixed to the videos' rate, or 25 fps without video. Needs `--fps` and
  the detected value in the analysis (1.1), so the dropdown can say "aus den Videos erkannt".
- **Audio format:** see the open question in 2.

### 1.6 Listening and waveforms (done: `chronon.listen`, `chronon overview`)
- `audio.excerpt(path, start_s, seconds, rate)`: a few seconds from any position, with a fast
  seek (excerpt reads for PCM, `ffmpeg -ss` for everything else). Step 2 audition and step 5.
- Reference time to file time for a row, including joined clips and devices. This belongs in the
  core next to `Alignment.ref_time`.
- Overview of a whole track (min/max peaks) for the step 2 audition bar, cached on disk. The
  cache folder is set in settings. A 4 h desk track takes seconds to scan, a video file much
  longer, so the overview must be computed once and kept.

### 1.7 Smaller items
- **Start column in step 1:** BWF time stamps are in `audio.Info`, but MP4 `tmcd` timecode is
  only parsed in `fcpxml.py`. Move it to `probe`, so the file list can show every start time.
- **Protocol:** the core logs nothing. Write a `logging` file per run (the worker's stderr plus
  core messages) for "Protokoll öffnen".
- **Version:** `chronon.__version__` exists, nothing more needed.

## 2. Open questions in the design

**Decided 2026-10-07** (design changes in `revision-1.md`):
1. Corrected files are named `<device>_korrigiert.<ext>` (core change: suffix).
2. CAF outputs end in `.caf`; board 14 gets fixed.
3. Audio format: Automatisch / WAV / CAF, default Automatisch. RF64 later (#15).
4. Version 1 includes renaming and reordering devices, naming tracks, and regrouping files
   (move, split, merge). Core change: a device description the analysis accepts instead of
   automatic grouping, with names and order that reach file names and the FCPXML.
5. Suggested reference = the device covering the most time; its loudest tracks preselected.
6. Only "Im Finder zeigen" (Explorer / Dateimanager on Windows / Linux), no "open in Final Cut".
7. Padding is a checkbox in the export form (default on). Also in the form: sample rate
   (default 48 kHz) and project name (default output folder name). No bit depth setting:
   output = the source's bit depth, at least 24 bit (16-bit camera audio becomes 24 bit, so the
   resampled signal is not requantised to 16 bit and needs no dither). Peaks the resampling
   pushes above 0 dBFS (a fraction of a dB, only on material already at a limiter's ceiling)
   are clipped as today, without a note; no limiter, no float rewrite (it would cost a second
   write pass).
8. Step 5 lists every file, with its verdict badge; files that need checking first.
9. Appearance: follow the system, plus a setting System / Hell / Dunkel.

Original questions, for reference:


1. **Output file names:** the boards show `ZOOM_korrigiert.wav`; the core writes `ZOOM.wav`
   (device or file name). Pick one; a suffix is easy to add.
2. **Board 14:** `x32_korrigiert.wav` with the note "Als CAF geschrieben" is a contradiction. A CAF
   output ends in `.caf`.
3. **Audio format:** the core default is *auto* (WAV, CAF above 2 GiB). With "WAV" forced, files
   above 4 GiB fail (a 4 h stereo camera track is close to that). Suggest three options:
   Automatisch / WAV / CAF, with Automatisch as the default.
4. **Manual grouping and renaming of devices** (roadmap Stage 2) is not designed. Board 05 shows
   names and descriptions ("Presonus · Mischpult der Band") that the core cannot know. Are they
   editable in step 2?
5. **"Kandidat":** what exactly makes a device a reference candidate? See 1.2.
6. **Platform words:** "Im Finder zeigen" becomes "Im Explorer zeigen" on Windows and "Im
   Dateimanager zeigen" on Linux. "In Final Cut Pro öffnen" exists only on macOS; elsewhere it
   becomes "Timeline öffnen" (default app), later Resolve. The cache path in settings is a
   per-platform default.
7. **Padding** (`--no-pad`) is not in the form. Fine as a fixed default (padded) for version 1?
8. **Step 5 list:** does it also hold files without a reliable match (where listening matters
   most), and should it show their verdict badge?
9. **Theme switching** is noted as not designed. Following the system is the default in Qt
   (6.5+); a setting can come later.

## 3. Notes for Qt
- IBM Plex Sans / Mono are under the OFL. Bundle them and load them with `QFontDatabase`.
- Colours: build a `QPalette` per theme from the tokens, plus one stylesheet for borders, corners
  and the badges. The dashed border of "kein sicherer Treffer" needs custom painting or a
  stylesheet border. Both are fine.
- Custom widgets: waveform (step 2, step 5), read-only device timeline (step 4), step list with
  status icons (step 3). Everything else is standard widgets: tables, segmented control as a
  button group, combo box, slider.
- Native sliders and combo boxes will look slightly different from the boards, as the README
  says.
