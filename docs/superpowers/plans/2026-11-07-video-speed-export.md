# Video Speed Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export videos at 1x, 1.25x, 1.5x, or 2x and preview the completed accelerated MP4 in offtube.

**Architecture:** A numeric `video_speed` travels from each client to `/api/download`, is strictly validated by the Python server, and is applied in the existing X-ready FFmpeg conversion pass. The job exposes the selected value so the desk and extension can show the actual generated local file in a `<video>` preview after success.

**Tech Stack:** Python standard-library HTTP server, yt-dlp, FFmpeg/ffprobe, plain HTML/CSS/JavaScript, Node built-in test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-video-speed-export-design.md`

## Global Constraints

- Accept exactly `1`, `1.25`, `1.5`, and `2` as numeric `video_speed` values.
- Preserve normal-speed output behavior and the existing H.264/AAC MP4 compatibility path.
- Use no new runtime dependencies or external assets.
- Do not leak local paths or request cookies through public job responses.
- Accelerated-export preview uses the completed local `/files/` resource, never a remote media URL.

## Review Focus

- Forged values (`0`, `1.1`, strings, booleans, NaN/Infinity) are rejected before a queue slot is consumed; test in Task 1.
- A normal-speed video does not unnecessarily acquire a speed suffix or transcode; test in Task 2.
- A video with no audio still produces a valid speed-adjusted MP4; test in Task 2.
- Failed acceleration does not mark an untouched source file as a successful speed export; test in Task 2.
- Popup-to-panel handoff and completed-preview markup retain the chosen speed and safely URL-encode filenames; test in Task 3.

---

### Task 1: Validate and publish video-speed requests

**Files:**
- Modify: `app.py: PUBLIC_JOB_FIELDS, build_download_opts(), enqueue_download()`
- Test: `tests/test_helpers.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `video_speed(payload: dict) -> float` and public job field `video_speed: float`.
- Consumes: `/api/download` request bodies from the desk and extension.

- [ ] **Step 1: Write failing validation/API tests**

Cover accepted 1.25x requests appearing in a job response and reject values
outside the fixed numeric allow-list without creating a job.

- [ ] **Step 2: Run the targeted pytest tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_helpers.py tests/test_api.py -q`

Expected: FAIL because `video_speed` is not validated or published.

- [ ] **Step 3: Implement `video_speed(payload) -> float` and wire it into enqueueing**

Validate an actual number (not `bool`) against the four supported speeds;
store the canonical float in the in-memory job and expose it only through the
public job whitelist.

- [ ] **Step 4: Run targeted Python tests**

Run: `./.venv/bin/python -m pytest tests/test_helpers.py tests/test_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_helpers.py tests/test_api.py
git commit -m "feat(download): Validate video speed requests"
```

### Task 2: Retime videos during the X-ready conversion

**Files:**
- Modify: `app.py: make_video_x_ready(), make_videos_x_ready(), run_download()`
- Test: `tests/test_ytdlp_worker.py`
- Test: `tests/test_helpers.py`

**Interfaces:**
- Consumes: `job["video_speed"]` from Task 1 and downloaded video paths.
- Produces: speed-suffixed, H.264/AAC MP4 paths for accelerated exports.

- [ ] **Step 1: Write failing FFmpeg argument and integration tests**

Assert 1.25x produces video `setpts` and audio `atempo` filters, uses the
shortened duration for progress, retains a valid MP4, and keeps 1x untouched.
Include an audio-less fixture and an acceleration failure case.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_helpers.py tests/test_ytdlp_worker.py -q`

Expected: FAIL because no speed transform exists.

- [ ] **Step 3: Extend the existing X-ready conversion pass**

When `video_speed > 1`, force H.264/AAC encoding in the same FFmpeg call;
compose the current scale/compatibility filter with `setpts` and use `atempo`
for audio. Atomically replace with a speed-labelled output only after probing
the result. Treat a requested speed-transform failure as job failure.

- [ ] **Step 4: Run focused tests and then all Python tests**

Run: `./.venv/bin/python -m pytest tests/test_helpers.py tests/test_ytdlp_worker.py -q`

Run: `./.venv/bin/python -m pytest tests -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_helpers.py tests/test_ytdlp_worker.py
git commit -m "feat(download): Export accelerated videos"
```

### Task 3: Add controls, persistence, and local previews

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`
- Modify: `extension/popup/popup.html`, `extension/popup/popup.js`
- Modify: `extension/sidepanel/sidepanel.html`, `extension/sidepanel/sidepanel.js`, `extension/shared.js`, `extension/ui.css`
- Test: `tests/test_web_gif.mjs`
- Test: `tests/extension_gif.test.js`

**Interfaces:**
- Consumes: `video_speed` request/job field from Tasks 1–2.
- Produces: one of the four numeric values in every video request and a
  controls-enabled local-file preview for completed accelerated jobs.

- [ ] **Step 1: Write failing desk and extension tests**

Cover payload inclusion, persisted selection, popup-to-panel handoff, hidden
controls for GIF/audio, and HTML-escaped `/files/` video previews after a
successful accelerated job.

- [ ] **Step 2: Run the Node tests to verify they fail**

Run: `node --test tests/test_web_gif.mjs tests/extension_gif.test.js`

Expected: FAIL because no speed controls or video-result preview exists.

- [ ] **Step 3: Implement speed controls and persistence on all user surfaces**

Use native radios/selects consistent with existing format controls. Keep 1x
as the default, send numeric `video_speed`, persist the choice in existing
local storage, and make the popup preserve it for the full side panel.

- [ ] **Step 4: Implement completed accelerated-export previews**

Render `<video controls preload="metadata">` only when a matching completed
job has `video_speed > 1` and a safe video filename. Reuse current escaped
local file URLs and add an accessible caption and Save link.

- [ ] **Step 5: Run Node tests and the full project gate**

Run: `node --test tests/test_web_gif.mjs tests/extension_gif.test.js`

Run: `bash tools/check.sh`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web extension tests
git commit -m "feat(ui): Preview accelerated video exports"
```

### Task 4: Document the video-speed workflow

**Files:**
- Modify: `README.md`
- Modify: `docs/guide.md`

**Interfaces:**
- Documents: the fixed supported speeds, MP4 conversion, and completed local
  preview behavior.

- [ ] **Step 1: Update the user-facing output and workflow descriptions**

State that accelerated video exports are available at 1.25x, 1.5x, and 2x;
they take an FFmpeg conversion and can be previewed after completion.

- [ ] **Step 2: Run documentation and whitespace checks**

Run: `git diff --check && bash tools/check.sh`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/guide.md
git commit -m "docs: Explain accelerated video exports"
```
