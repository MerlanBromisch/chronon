# Design revision 1: briefing

For the design session that revises the boards in `ui/`. Read `ui/README.md` (current design)
and `briefing.md` (product) first. Feedback and discussion may be in German; the UI text is
German. Decided 2026-10-07 after checking the design against the core (`gaps.md`).

Deliver: the changed and new boards in dark and light, numbered in flow order like the
existing ones, plus an updated `ui/README.md`. Unchanged boards stay as they are.

## Changes to existing boards

### Step 2 — Geräte & Referenz (board 05): devices can be edited
Chronon groups files into devices automatically, and sometimes it gets it wrong or uses unhelpful
names ("Musical 23.6.26 Presonus"). In step 2 the user can now:
- **Rename** a device (inline, e.g. "Presonus", optional description "Mischpult der Band").
- **Reorder** devices. The order is the lane order in the timeline and the export.
- **Name tracks** of a multitrack device (track 17 → "Summe L"). The track grid shows names
  instead of bare numbers once named.
- **Regroup:** move a file to another device, split a device, merge two devices, or make a new
  device from files. This is for when the automatic grouping is wrong (two recorders mixed up,
  a clip landed in the wrong device).

Names are used later: output file names (`Presonus_korrigiert.wav`) and lane / clip names in the
timeline file. They stay a property of the project, not of the original files (originals are
never touched).

**Suggested reference:** the device that covers the most time is preselected and marked
"Vorschlag" (replaces the "Kandidat" role). Within it, the loudest tracks are preselected. The
user can change both.

Needed: board 05 with editable names and drag handles, plus at least one board for regrouping
(a file's menu "In Gerät verschieben …", or drag and drop between devices; split / merge). Design
the interaction so that it stays clear with 56 files and 5 devices.

### Step 5 — Hören (board 10): every file, with its verdict
The file list also holds files without a reliable match, with their verdict badge. Files that
need checking ("kein sicherer Treffer", "Uhr wandert") come first. Listening matters most for them.

### Step 6 — Export (boards 11, 12): more options
- **Audioformat:** segmented control **Automatisch / WAV / CAF**, default *Automatisch*. Hint for
  Automatisch: "WAV, ab 2 GiB CAF". Only for "Korrigiert".
- **Auffüllen bis Projektstart:** new checkbox, default on, only for "Korrigiert". On: every file
  starts at 0:00:00 of the project, so it can be placed at the start in any program. Off: files
  stay as long as their original; their position comes from the time stamp or the timeline file.
  If off and the format can be CAF, a hint: "CAF hat keinen Zeitstempel: Position nur über die
  Timeline-Datei".
- **Samplerate:** dropdown 44,1 / 48 / 88,2 / 96 kHz, default 48 kHz. Only for "Korrigiert".
- **Projektname:** text field, default = name of the output folder. Names the timeline and the
  `.fcpxml`. Both modes.
- Order the form so that both modes still look tidy (Sync has only project name, FCPXML, frame
  rate, output folder).

### Export done (boards 13, 14)
- Corrected files are named `<Gerät>_korrigiert.<ext>`. A CAF output ends in `.caf` (board 14 shows
  `x32_korrigiert.wav` with the note "Als CAF geschrieben", which is wrong).
- Only one action for the files: **"Im Finder zeigen"** (Windows: "Im Explorer zeigen", Linux:
  "Im Dateimanager zeigen"). Remove "In Final Cut Pro öffnen".

### Settings (board 15): appearance
New row **Darstellung: System / Hell / Dunkel** (default System). The cache folder default is
per platform (macOS `~/Library/Caches/Chronon`, Windows `%LOCALAPPDATA%\Chronon\Cache`, Linux
`~/.cache/chronon`). Show the macOS one on the boards.

## Unchanged, for reference
- Flow, sidebar, header, footer, tokens, fonts, badges.
- Step 3 step list: the steps come from the core (one "Spuren vergleichen" per device), the
  board's structure stays.
- Constraints from `briefing.md` (rebuilt in Qt: standard widgets, custom drawing only for
  waveforms, timeline, badges, step list).
