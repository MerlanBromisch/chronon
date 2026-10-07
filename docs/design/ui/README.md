# Chronon desktop UI: design handoff

Visual design for the desktop app described in `../briefing.md` and `../../app.md`.
`png/dark/` and `png/light/` hold every screen in both themes. File names are numbered in flow order.
The UI text is German (language setting: Deutsch); code, comments and docs stay English.

The interactive prototype lives in a private Claude Design artifact and is not part of the repo.
These PNGs are the reference.

## Flow

Dateien → Geräte & Referenz → Sync → Ergebnis → Hören → Export. Settings is a separate entry at the
bottom of the sidebar. Every step can be opened from the sidebar; steps that need earlier results
load them first (the prototype uses sample data).

| PNG | Screen |
|---|---|
| 00-style | Colors, badges, type scale, building blocks |
| 01-files-empty | Drop zone, buttons "Dateien wählen …" / "Ordner wählen …" |
| 02-files-reading | Intermediate state while the files are probed ("23 / 56") |
| 03-files-loaded | Flat file list: name, duration, sample rate, channels, start time |
| 04-files-unreadable-dialog | Modal "Datei nicht lesbar" with the ffmpeg message |
| 05-devices-reference | Device list (rename inline with optional description, reorder by the drag handle, pick the reference; "Vorschlag" marks the suggestion), then reference tracks (nameable), then audition |
| 05a-devices-regroup | A device's files opened: select files, then "In Gerät verschieben …" or "Als neues Gerät abtrennen" (the file menu shows the same two) |
| 05b-devices-move-dialog | Dialog "Datei verschieben": pick a device or "Neues Gerät" with a name |
| 05c-devices-merge-dialog | Dialog "Geräte zusammenführen" (row menu "…"), with the name after merging |
| 06-sync-running / 07-sync-cancelled | Progress, step list, cancel in the footer |
| 08-result | "Alle 5 Geräte sind synchronisiert", read-only timeline, details collapsed |
| 09-result-details-special-cases | Details table with all verdict kinds |
| 10-listen | Every file except the reference with its verdict badge; files to check first ("Zu prüfen"), then "Zuverlässig". Waveform of reference and file around a position, play, split/mixed |
| 11-export / 11a-export-sync / 11b-export-no-padding | Export form: "Korrigiert" (format, sample rate, padding, join clips), "Sync" (project name, FCPXML, frame rate, output folder only), padding off with the CAF hint |
| 12-export-rejected / 13-export-done / 14-export-done-with-notes | Rejected output folder, outcome list (`<Gerät>_korrigiert.<ext>`), outcome with notes. The only file action is "Im Finder zeigen" |
| 15-settings | Language, appearance (System / Hell / Dunkel), cache folder (macOS path shown), log, about |

The PNG numbers of earlier boards stay as they were; new boards of a step get a letter (05a, 11a).

## Where the data comes from

- **Files step:** `audio.probe` per file (metadata only, one ffprobe call each). Unreadable files
  show up here (`AudioError: cannot read …`).
- **Devices step:** `devices.group` from that metadata and the file names. The reference is a
  device plus its tracks (`-r`). Grouping must not appear in the Files step. The user may then
  rename, reorder, regroup (move a file, split, merge) and name tracks. All of that is a property
  of the project, never of the original files. Device names become `<Gerät>_korrigiert.<ext>`
  and lane / clip names in the timeline file.
- **Reference suggestion:** the device covering the most time is preselected and marked
  "Vorschlag", its loudest tracks are preselected. Both can be changed.
- **Result rows:** per file `offset`, `drift_ppm`, `confidence`, `reliable`, `wander_ms`, `via`,
  `drift_from`, `linked_via`, `notes` (see `docs/app.md`).
- **Verdict badges** (only what the core reports):
  - `zuverlässig`: `reliable` is true. Low confidence (16 %) is normal, never styled as a failure.
  - `Uhr wandert`: reliable, but `wander_ms` > 1 ms (note "clock wanders ±x ms").
  - `kein sicherer Treffer`: `reliable` is false. Hints: "drift from <file>", "linked via <file>".
  - `Referenz`: the reference device.
  - Other hints: `inverted`, "video placed within ±x ms", "measured via <file>".
- **Listen step:** the list holds every file with a verdict (`reliable`, `wander_ms`), not only
  the measured ones. Files that are not reliable or wander come first.
- **Export notes:** from `correct.py` (CAF instead of WAV above 2 GiB, no time stamp in CAF,
  audio of a video file, `VERIFICATION FAILED`). Board 14 shows how they appear.
- Placeholders in the boards (`[Start]`, `[ppm]`, `±[x] ms`, `[x.y.z]`) mean "real value goes
  here". Durations and start times in the file list are examples.

## Layout (px)

- Window minimum 1024 × 700. Boards are drawn at 1200 × 780.
- Sidebar 216 wide, padding 20 / 12. Step row 36 high, gap 4, number box 20 × 20.
- Header 60 high, padding 28 sides: label "Schritt N von 6" (11 px caps), title 18/600,
  optional right text "56 Dateien · 5 Geräte" (12 px).
- Content padding 22 × 28, gap 18 between blocks.
- Footer 56 high, padding 28 sides, line on top. Back ("Zurück") left, main action right
  (accent, min width 140). While syncing or reading the left button is "Abbrechen" and the right
  button is disabled. In settings the right button is "Fertig".
- Corners 4 everywhere, small bars 2. Only radio buttons are round.
- Controls 32 high, buttons 14 side padding, 1 border.

## Tokens

| Name | Dark | Light |
|---|---|---|
| Background | `#151618` | `#F3F4F6` |
| Sidebar | `#1B1C1F` | `#E9EBEE` |
| Panel | `#202124` | `#FFFFFF` |
| Raised (buttons, active step) | `#2B2D31` | `#FFFFFF` |
| Table header | `#1D1E21` | `#F0F1F4` |
| Input / waveform background | `#1A1B1E` | `#F6F7F9` |
| Hover / selected row | `#26282C` | `#E8EAEE` |
| Card border | `#34363B` | `#D5D8DD` |
| Divider | `#2C2E33` | `#E1E3E7` |
| Button border | `#4A4D54` | `#BCC0C7` |
| Control border (radio, check) | `#5A5D64` | `#A5A9B1` |
| Text | `#E6E7E9` | `#1B1C1F` |
| Text secondary | `#9A9DA4` | `#5E626A` |
| Text tertiary (hints) | `#7E8188` | `#7B7F87` |
| Waveform grey | `#6E727A` | `#8E939B` |
| Accent (default) | `#6AA7F5` | `#2F7DE1` |
| Text on accent | `#0E1114` | `#FFFFFF` |
| Green / border | `#8FD6A9` / `#3F7A58` | `#1F7A47` / `#86C3A0` |
| Amber / border | `#F0C06A` / `#8A6A2A` | `#8A5A00` / `#D4AE5E` |
| Red / border | `#F0958E` / `#8A403B` | `#B3342B` / `#E2A29C` |

Badge fills are the status color at about 10–14 % alpha. Accent alternatives: dark `#4DB8C4`
(teal), `#A592F2` (violet); light `#1E94A0`, `#7B63D6`. Blue is the default.

Fonts: IBM Plex Sans (UI) and IBM Plex Mono (numbers, times, paths). Sizes: 22/600 headline,
18/600 step title, 13 body and buttons, 12 hints and tables, 11 caps labels (+0.08 em).

## Components

- Button 32 high; primary = accent fill, 600; small table button 26 high.
- Input 32 high, 10 px padding. Checkbox and radio 18 × 18; radio dot 8. Drag handle: 6 dots, 10 × 16.
- Selection bar below a device's files (one line, buttons 32 high); popup menu with 2 items.
- Segmented control 30 high, selected = accent fill.
- Badge 22 high, 9 side padding, 12/500, 11 px icon, 1 border. "kein sicherer Treffer" has a
  dashed border; "Referenz" is an accent fill.
- Table row min 34, columns gap 12, header 30. Card: panel fill, card border, padding 14–18.
- Dialog 520 wide over a dimmed window. Progress bar 8 high with a 2 px radius.
- Large status square 40 × 40 (green check, red "!"), small "!" icon in banners.

## Behaviour notes

- The timeline in the result is view-only. Message details are collapsed by default; a button
  shows them. Most users only need "synced".
- Reference choice is prominent (step 2): device first, then its tracks, with an audition
  (waveform, play/pause, −1 min / −10 s / +10 s / +1 min, slider).
- Devices: names and order are edited in place. Click a name to rename (name + optional
  description, "OK"); the grip moves a row up (drag and drop in Qt). The order is the lane order
  in the timeline and export. Files are shown per device on demand ("Dateien"); with 56 files and
  5 devices all devices stay collapsed except the one being edited. Only devices of clips can be
  regrouped or merged; multitrack devices keep their tracks together.
- Reference: the "Vorschlag" badge marks the device covering the most time. "Referenz" is the
  user's choice. Tracks in the grid show their name once named (naming happens in the audition).
- Listen: the file list scrolls; badge colors as in the result. Files to check come first.
- Export: "Sync" (originals stay, timeline only) or "Korrigiert" (drift-corrected audio).
  Common rows: project name (default = output folder name, names the timeline and `.fcpxml`),
  FCPXML, frame rate, output folder. Only for "Korrigiert": audio format (Automatisch / WAV /
  CAF, default Automatisch = "WAV, ab 2 GiB CAF"), sample rate (44,1 / 48 / 88,2 / 96 kHz,
  default 48), "Auffüllen bis Projektstart" (default on; off shows "CAF hat keinen Zeitstempel:
  Position nur über die Timeline-Datei" when the format can be CAF) and join clips. Frame rate
  appears for audio-only projects (shown as "aus den Videos erkannt"). The output folder must
  not be the originals' folder; the GUI repeats that check early (board 12).
- Export done: one file action, "Im Finder zeigen" (Windows "Im Explorer zeigen", Linux
  "Im Dateimanager zeigen"). CAF outputs end in `.caf`.
- Settings: appearance row System / Hell / Dunkel (default System). The cache folder default is
  per platform (macOS `~/Library/Caches/Chronon`, Windows `%LOCALAPPDATA%\Chronon\Cache`,
  Linux `~/.cache/chronon`); the boards show the macOS one.

## Corrections to the boards (apply when building, no new boards)

Decided 2026-10-07 after reviewing revision 1:

1. **Timeline row (11, 11a, 11b, 12):** the "FCPXML" checkbox becomes a row **"Timeline für"**
   with a dropdown, one choice. Version 1: "Final Cut Pro / Logic"; "Korrigiert" also offers
   "Keine" (audio only). "Sync" has no "Keine": the timeline is its only output. Premiere Pro
   and DaVinci Resolve are added to the list later (#7, #8) without layout changes. For a
   timeline in another format, the user exports again.
2. **Move / merge dialogs (05b, 05c):** multitrack devices (Presonus, x32) are not offered as
   targets: show them disabled. Only clip devices are regrouped or merged.
3. **Names of parallel tracks (14):** a multitrack device is written as one file per track,
   named `<Gerät>_<Spur>_korrigiert.<ext>` with the track's name (`x32_Summe L_korrigiert.wav`),
   or its number when unnamed (`x32_17_korrigiert.wav`). Board 14's single
   `x32_korrigiert.caf` is sample data only.

## Not designed yet

- Applying the appearance setting live (the row is designed, both themes exist).
- Drag and drop between devices (the boards use selection and menus; drag is a shortcut).
- Real waveform data and the audition's playback latency behaviour.
- Qt specifics (native slider and combo box will differ from the mock-ups).
