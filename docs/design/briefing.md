# Design briefing: Chronon desktop app

Paste this into a design session. Feedback and discussion may be in German.

## What Chronon is
Chronon syncs recordings of the same event made on several devices — a mixing desk with many
tracks, a Zoom field recorder, cameras, a phone — by comparing their waveforms. It also
measures each device's **clock drift** (devices never run at exactly the same speed; over a 4 h
show a few ppm add up to tens of milliseconds) and can write drift-corrected audio. The result
goes to an editor (Final Cut Pro / Logic now, Premiere and Resolve later) as a timeline file.

Think "PluralEyes, but sample-accurate and honest about how sure it is".

Users: people who record theatre, concerts, interviews with more than one device and edit in
an NLE / DAW. They know their recorders, not signal processing. Primary user right now: the
author, on a Mac; the app must also run on Windows and Linux.

## Version 1 scope: drop & export
No timeline editor in version 1. Wrong matches are fixed in the editor afterwards; the app's job
is to sync fast, show clearly how reliable each result is, let you listen to check, and export.

Flow:
1. **Drop** files / folders (audio and video; often 10–30 files, up to hundreds of GB).
2. **Devices & reference** — Chronon groups files into devices automatically:
   - *parallel tracks*: one recording, many mono files (a desk's 18 channels),
   - *clips*: numbered files of one recorder (`ZOOM0003.WAV`, `ZOOM0004.WAV`; a camera splitting
     at 4 GB).
   The user picks the **reference** (the timeline's clock), usually one or more desk tracks.
   This choice matters more than anything else for quality — make it prominent, not a setting.
3. **Sync** — progress with step name, percentage and an honest time left (the musical below
   takes ~46 s). Cancel any time.
4. **Result** — per device / file: start position, drift, confidence, reliable or not, notes.
   Re-run with another reference.
5. **Listen** — pick a point in time, play a few seconds of reference and file together
   (e.g. reference left ear, file right ear, or mixed) to hear whether they line up. A small
   waveform of both around that point helps.
6. **Export** — two modes:
   - *Sync*: timeline that places the original files (nothing re-rendered),
   - *Correct*: writes drift-corrected audio files + timeline (video is never re-encoded).
   Options: WAV / CAF, join clips per device into one file, output folder (never the
   originals' folder — the app refuses). Timeline format: FCPXML (Premiere/Resolve later).

## Real example data

### Musical, 4 h (hard case: hall, PA, music)
Reference: band desk (Presonus), 18 mono tracks, of which tracks 17 and 18 are chosen as
reference. Results:

| Device | Files | Type | Start on timeline | Drift | Confidence | Notes |
|---|---|---|---|---|---|---|
| Presonus (reference) | `Musical 23.6.26_1 … _18 #01.wav` | 18 parallel tracks, 48 kHz | 0:00:09.790 | — | — | time reference |
| x32 ensemble desk | `Ohne Namen_1 … _32#01.aif` | 32 parallel tracks, 44.1 kHz | 0:00:24.997 | −12.01 ppm | 100 % | measured via its track 24 |
| ZOOM | `ZOOM0003.WAV`, `ZOOM0004.WAV` | 2 clips, stereo | 0:01:46.811 | −8.23 / −11.21 ppm | 41 % / 30 % | clock changes rate between clips |
| Camera C2378 (Sony) | `C2378.MP4`, `C2379.MP4` | 2 video clips | 0:00:00.000 | −6.37 / −5.69 ppm | 16 % / 18 % | video placed within ±40 ms / ±25 ms |
| Camera R62 | `R62_0040.MP4`, `R62_0041.MP4` | 2 video clips | 0:23:37.689 | −3.17 / −2.74 ppm | 63 % / 59 % | video placed within ±26 ms / ±16 ms |

All five are reliable. Low confidence (16 %) is *not* bad here: confidence = share of
measurement windows that agree, and a camera in a loud hall agrees in few windows but is still
accurate to ~0.1 ms. The UI must not make 16 % look like failure; "reliable / not reliable" is
the verdict, confidence is detail.

### Interview (easy case)
Reference: Zoom H5, 3 parallel tracks (`ZOOM0001_TrLR/Tr1/Tr2.WAV`). `Merlan #01.wav` (Logic
recording): start +3.333 s, drift −153.38 ppm (a cheap clock: 0.55 s over an hour),
confidence 88 %, reliable.

### States to design (examples, not from real data)
- **No reliable match**: a phone in another room, confidence 3 % → "no reliable match"; it is
  left out of the timeline unless the user forces it.
- **Clock wanders**: a battery recorder whose drift is not a straight line, "clock wanders
  ±1.8 ms" (warning, still usable).
- **Short clip borrows drift**: a 40 s clip too short to measure drift, takes it from its
  sibling clip ("drift from ZOOM0003").
- Empty start screen, sync running, sync cancelled, error (ffmpeg cannot read a file),
  export refused (output folder = originals' folder), export done (open folder / reveal in
  Finder, open in Final Cut).

## Constraints (the design will be rebuilt in Qt)
- Desktop window, resizable, minimum ~1024 × 700; mouse + keyboard. Dark theme first (NLE
  users), light theme possible later.
- Standard building blocks: lists, tables with columns, tree (devices → files), splitters,
  tabs, buttons, combo boxes, progress bars, dialogs, drag & drop zone.
- Custom-drawn is fine for: small waveforms, confidence / reliability badges, a simple
  time ruler.
- Avoid web-only effects: backdrop blur, glassmorphism, complex animations, parallax, hover
  cards that depend on CSS tricks. Simple shadows, rounded corners, colour and typography are
  fine.
- No accounts, no cloud, no onboarding carousel. Everything is local.
- One window. Steps can be a sidebar, tabs or a single scrolling view — that is a design
  question, not a given.

## What I want from the first round
**Wireframes, greyscale, no visual styling yet**: all screens and the states above, as one
clickable mock-up. Focus on order of steps, what is visible when, and wording. Visual design
(colour, type, badges) is a second round once the flow is agreed.
