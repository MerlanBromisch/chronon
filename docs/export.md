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
- **Padding is optional** (default on): padded files start at timeline zero and are dropped
  into Logic at 0. Unpadded files start with their own audio (no minutes of silence before a
  short take in the middle of a long recording) and carry their timeline position as a BWF
  time stamp (Logic: move region to recorded position — to verify) and in the timeline file.
- **Timeline zero = the earliest start of any file.** Nothing is cut off; the reference is
  padded too when something started before it.
- Bit depth and channel layout as in the source; target rate is a setting (default 48 kHz).
- The reference is also written, so every output file shares the same zero.
- **One output file per clip** by default. Optionally the clips of a device are joined into
  one file per device, gaps filled with silence.
- **Format:** WAV by default. A file that would exceed 2 GiB is written as CAF instead (WAV
  and AIFF are limited to 4 GiB, some programs already fail at 2 GiB) and the report says
  so. The format can also be fixed by the user. CAF / RF64 support in Logic and Final Cut
  must be verified.

## Devices

A device is a group of files that share one clock: the split files of a Zoom take, the
halves filmed by one camera, all channels of a desk. Chronon groups them automatically
(folder, file name pattern such as `ZOOM…`, `C23…`, `R62_…`, header metadata such as the
recorder model) and the user can correct the grouping.

- Analysis: all clips of a device share one drift, so short clips profit from long ones,
  and any channel of a desk can serve as its reference (as `analyze -r` does today).
- Export: per clip or joined per device (see above).

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

- **CAF / RF64 in Logic and Final Cut** — test before relying on them.
- **BWF time stamp placement in Logic** for unpadded files — test.
- **Video in Logic** — whether Logic's FCPXML import places the movie, or Chronon must state
  the movie start offset to enter by hand. Needs testing.
