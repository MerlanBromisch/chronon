# Export concept

Decided 2026-09-25. Status: concept, not implemented yet.

## Two outputs

1. **Sync** — a timeline file (FCPXML first; Premiere/Resolve later) that places the
   *original* files. Nothing is written except the timeline. Drift stays in the material and
   is only reported.
2. **Sync + correct** — new audio files, plus a timeline that uses them. This is the main
   workflow: audio is mixed in Logic, video is cut in Final Cut, and the video often goes into
   Logic as a picture reference while mixing.

Originals are never modified, moved or written next to. All output goes into a separate
output folder.

## Corrected audio

- Every audio source gets a new file on one common timeline: **drift removed and sample rate
  converted in one step** (e.g. x32 at 44 099.47 Hz real → 48 000 Hz), so all files can be
  dropped into Logic without any conversion there. Exact rate ratio, high-quality resampling;
  never ffmpeg `asetrate` with a fractional rate (it silently truncates to an integer).
- **Padded with silence to timeline zero**: every file starts at 0 and is dropped into Logic
  at 0. (Simplest for the user; chosen over BWF time stamps.)
- Bit depth and channel layout as in the source; target rate is a setting (default 48 kHz).
- The reference is also written (padded), so every output file shares the same zero.

## Video

- Video is **never re-encoded**. It is only placed on the timeline.
- Camera audio is separated and treated like any other audio source: drift- and
  rate-corrected into its own file. The video is then used together with that new audio
  (in Final Cut the camera's own audio is muted; in Logic the video is the picture reference).
- The video itself keeps its clock drift. It is placed so the error is smallest over the clip
  (centred rather than start-aligned); the remaining error is reported and a warning is shown
  when it exceeds half a frame. Retiming video in the timeline is a later stage.

## Reference

- The time reference (whose clock defines the timeline) is selectable. Default: the best
  audio source (e.g. the desk).

## Report and verification

- Every export writes a report (JSON + readable text): per file the source path, measured
  offset, drift, confidence, the correction applied and the output path.
- After writing, each corrected file is measured again against the reference; expected
  result 0 ppm, 0 s. This check is part of the export, not optional — it is the only guard
  against silent mis-corrections.

## Open points

- **Timeline zero** — the earliest start of any file (nothing is cut; the reference is padded
  too) or the reference start (material before it is cut off).
- **Several files per device** (Zoom split at 2 GiB, camera halves) — one output file per
  device with gaps as silence (one track per device in Logic), or one per clip.
- **Files over 4 GiB** (4 h stereo 24-bit ≈ 4.1 GB exceeds the WAV limit) — RF64 or CAF;
  must be checked in Logic and Final Cut.
- **Video in Logic** — whether Logic's FCPXML import places the movie, or Chronon must state
  the movie start offset to enter by hand. Needs testing.
