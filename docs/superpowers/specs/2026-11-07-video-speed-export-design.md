# Video-speed export design

## Intent

Let a keeper save a video at a faster, deliberate playback speed when a
normal-speed file is not the right fit. The completed file must be easy to
check in offtube, rather than asking the user to trust an opaque conversion.

## Goals

- Offer normal (1x), 1.25x, 1.5x, and 2x speeds for video exports.
- Keep the selected speed consistent across the desk and extension surfaces.
- Produce a conventional, locally playable MP4 with matched video and audio
  timing, retaining the existing X-ready H.264/AAC constraints.
- Show a controls-enabled preview of a completed accelerated export and retain
  its normal Save link.
- Reject forged or unsupported API values before a job is queued.

## Non-goals

- Streaming or sampling a remote source before it has been downloaded.
- Changing GIF, audio-only, image-only, or normal-speed exports.
- Adding arbitrary speed values, slow motion, pitch preservation controls, or
  a separate preview job.

## Constraints

- The current preview is inspection metadata and a thumbnail, not a playable
  remote source URL; an accurate pre-export preview would require a second
  fetch/transcode.
- The app remains plain HTML/CSS/JavaScript with no new client dependencies.
- FFmpeg performs the transform under the existing job process/cancellation
  model, and must leave the original file intact if the transform fails.
- API and UI labels use `video_speed` so they do not collide with the existing
  job `speed` field (transfer bytes per second).

## Approaches considered

1. Browser-only `playbackRate` preview: inexpensive, but exports the original
   file and therefore does not prove the result.
2. Download/transcode a separate sample before keeping: accurate upfront, but
   doubles work and performs poorly for collections.
3. **Selected:** carry a validated speed setting into the existing finished
   file pipeline, then preview the real generated MP4. This is the reliable
   path and reuses the cancellation, progress, compatibility, and local-file
   serving already in the product.

## Design

The desk, side panel, and popup expose the four speeds only for video output;
normal speed remains the default and is persisted per surface. Each request
sends a numeric `video_speed`. The popup hands its choice to the side panel
when the user opens the full controls.

The server accepts only `1`, `1.25`, `1.5`, and `2`, records the value in the
public job response, and applies a speed setting greater than one while
making each downloaded video X-ready. FFmpeg uses `setpts=PTS/<speed>` for
video and `atempo=<speed>` for audio, then writes a H.264/AAC MP4 with the
existing compatibility guarantees. The transformed file is visibly named
with its speed. A failed requested transform fails the job rather than
claiming that an unmodified original is the requested export.

When an accelerated video job finishes, the desk and extension render a
controls-enabled `<video>` element backed by `/files/...`, plus download
links. The normal library and session thumbnails continue to work unchanged.

## Builder decisions

- Use a fixed numeric allow-list rather than floating-point ranges.
- Only export speeds above 1 require a re-encode; 1x preserves current
  byte-for-byte behavior when no X-compatibility conversion is needed.
- Reuse the current X-ready conversion stage rather than add a second FFmpeg
  pass.
- Keep the post-export preview local and complete; it is intentionally not a
  new remote-source preview workflow.
