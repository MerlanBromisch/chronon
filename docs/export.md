# Export concept

Decided 2026-09-25. Status: implemented — `chronon correct` (corrected audio + FCPXML)
and `chronon sync` (FCPXML of the originals).

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
- Channel layout as in the source; bit depth as in the source but at least 24 bit (16-bit camera
  audio is not requantised to 16 bit after resampling, so no dither is needed; peaks the
  resampling pushes above 0 dBFS are clipped). Target rate is a setting (default 48 kHz).
- Corrected files are named after their device: `<device>_korrigiert` for a single file or
  joined clips, `<device>_<track>_korrigiert` for parallel tracks (track name, else its number),
  `<device>_<file>_korrigiert` for clips not joined (just `<file>_korrigiert` when the file name
  starts with the device's, e.g. `ZOOM0003`). Extension `.wav` or `.caf`.
- The reference is also written, so every output file shares the same zero.
- **One output file per clip** by default. With `--join` the clips of a device become one file,
  gaps filled with silence; each clip keeps its own drift correction. Parallel tracks are not
  joined (each channel stays a file).
- **Format:** WAV by default. A padded file over 2 GiB is written as CAF. An unpadded file
  stays WAV up to 4 GiB, because it needs its BWF time stamp and CAF cannot hold one; beyond
  4 GiB it becomes CAF and is placed from the timeline file. Not RF64: libsndfile left its data
  size at 0xFFFFFFFF, and once Logic appended its waveform chunk (`LGWV`, it does that to every
  imported file) other programs read that chunk as 15–26 s of audio.
- **BWF time stamp** in every WAV / RF64 output: its timeline position counted from
  01:00:00:00 (Logic's default project start), so "move region to recorded position" works
  and Final Cut reads it as the media start.

## Devices

A device is a group of files that share one clock: the split files of a Zoom take, the
halves filmed by one camera, all channels of a desk. Chronon groups them automatically
(folder, file name pattern such as `ZOOM…`, `C23…`, `R62_…`, header metadata such as the
recorder model) and the user can correct the grouping.

Two kinds of grouping, decided 2026-10-05 after looking at the musical:

- **Parallel tracks** (desk channels, Zoom Tr1/Tr2/LR): same folder, sample rate, length and
  BWF time stamp. Measured once through the track that matches best; every track gets exactly
  the same correction (so all 32 channels of a desk can be corrected in one go).
- **Clips of a device** (ZOOM0003/0004, C2378/C2379): same folder, file name prefix, format and
  recorder tag. Each clip is measured; a clip too short or too weak to show its own drift takes
  the drift of its sibling.
- A device does **not** get one common drift: the musical's Zoom ran at −8.23 ppm in its first
  half and −11.21 ppm in its second, each half on its own line within ±0.5 ms. Forcing one rate
  would be off by up to ~10 ms at the ends.
- Metadata groups files but does not place them: both cameras use record-run timecode, so
  C2379's timecode continues C2378's although it started 38 minutes later.
- Automatic, shown before the results, can be switched off.
- Export: per clip (default) or joined per device; one timeline lane per device.

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

- **CAF in Final Cut** — imports (2026-10-05). (Logic: verified 2026-09-25 with the musical — padded WAV and
  CAF outputs dropped at 0 line up, including the 4 h Zoom CAF.)
- **BWF time stamp placement in Logic** for unpadded files — test.
- ~~Video in Logic~~ — resolved 2026-10-05: Logic's FCPXML import brings the corrected audio
  and the video as picture reference, correctly placed. No manual movie start needed.
