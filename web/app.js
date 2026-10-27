/* offtube web app — contact sheet. Plain JS, no build step.
 *
 * A pasted link becomes a print: black film rebate, edge-printed with the
 * site and kind. Collections become a sheet whose prints you circle. While
 * a file downloads its print develops from negative to positive; when it
 * lands, a pencil tick. Marks are an overlay and never touch the print.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad2 = (n) => String(n).padStart(2, "0");
const checked = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;

// Errors that mean "give the site a signed-in session".
const NEEDS_ACCESS = /not a bot|sign in|signed-in|needs to be reloaded|cookies|membership|login/i;
const MEDIA_WORD = { image: "Image", video: "Video", audio: "Audio", mixed: "Post" };
const ACTIVE = ["queued", "starting", "downloading", "processing"];
const STATE_WORD = {
  queued: "Waiting", starting: "Starting", downloading: "Developing", processing: "Fixing",
  done: "Kept", error: "Failed", cancelled: "Cancelled",
};

// Grease-pencil strokes in a 100×100 box, stretched over whatever they mark.
const MARKS = {
  circle: "M20 28C34 9 76 6 91 27C103 44 92 75 60 89C33 99 5 84 6 57C7 37 23 21 47 15C55 13 62 13 69 15",
  tick: "M12 56C21 62 29 71 37 84C49 59 66 33 91 10",
  strike: "M14 18C38 40 62 62 88 84M86 16C62 40 38 62 14 86",
};
const markSvg = (kind) =>
  `<svg class="mark mark-${kind}" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${MARKS[kind]}"/></svg>`;

// One polite announcer for the desk (#announce). The deck and the print
// redraw on every poll; reading each redraw aloud would drown a screen
// reader, so only what a link turned out to be, each change of a job's
// state and failures are spoken.
function announce(text) { $("announce").textContent = text; }

const SPOKEN = { queued: "Waiting to start.", starting: "Starting.", downloading: "Downloading.", processing: "Finishing the file." };

function jobSpeech(job) {
  if (job.status === "done") {
    const files = job.files || [];
    return files.length > 1 ? `Kept ${files.length} files.` : `Kept ${files[0] || "the file"}.`;
  }
  if (job.status === "cancelled") return "Cancelled.";
  if (job.status === "processing" && job.quality === "gif") return "Making the GIF.";
  return SPOKEN[job.status] || "";
}

function speakJob(job) {
  const said = `${job.id}:${job.status}`;
  if (said === state.spoken) return;
  state.spoken = said;
  if (job.status !== "error") announce(jobSpeech(job)); // setDeck speaks every failure
}

function printSummary(info) {
  const site = (info.source && info.source.label) || "Link";
  const title = info.title || "Untitled";
  if (info.type === "playlist" || (info.type === "gallery" && info.count > 1)) {
    const count = `${info.count || (info.entries || []).length}${info.more ? "+" : ""}`;
    return `${site} ${info.type === "playlist" ? "playlist" : "collection"} ready, ${count} items: ${title}.`;
  }
  const kind = (info.type === "gallery" ? MEDIA_WORD[info.media] || "Post" : MEDIA_WORD[info.media] || "Video").toLowerCase();
  return `${site} ${kind} ready: ${title}.`;
}

const VIDEO_LADDER = [
  { id: "best", name: "Best available", sub: "highest stream" },
  { id: "2160", name: "2160p", sub: "4K" },
  { id: "1440", name: "1440p", sub: "QHD" },
  { id: "1080", name: "1080p", sub: "Full HD" },
  { id: "720", name: "720p", sub: "HD" },
  { id: "480", name: "480p", sub: "small" },
  { id: "360", name: "360p", sub: "smallest" },
];
const AUDIO_LADDER = [
  { id: "audio_mp3", name: "MP3", sub: "192 kbps · plays anywhere" },
  { id: "audio_m4a", name: "M4A", sub: "192 kbps · Apple-friendly" },
];

const state = {
  info: null,          // last successful inspect
  infoUrl: "",         // the field value that produced it
  marks: new Set(),    // circled item numbers on a collection sheet
  fetchToken: 0,
  activeJobId: null,   // job shown on the deck
  spoken: "",          // the job state last announced ("id:status")
  printJobId: null,    // job started from the current source (develops its print)
  previewJobId: null,  // source job or session job explicitly selected for its result
  pollTimer: null,
  files: [],           // kept files loaded so far, newest first
  total: 0,            // files in the library
  matched: 0,          // files matching the current search and kind
  diskFree: "",
  preferredOutput: "video",
  quality: { video: "best", audio: "audio_mp3" },
};

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

/* ================= what the next Keep will send ================= */

function outputType() { return checked("otype") || "video"; }
function selectedQuality() { return outputType() === "gif" ? "gif" : checked("q") || (outputType() === "audio" ? "audio_mp3" : "best"); }
function cookiesMode() { return checked("cmode") || "none"; }

function accessFields() {
  const mode = cookiesMode();
  return {
    cookies_mode: mode,
    cookies_text: mode === "upload" ? $("cookieText").value : "",
    cookies_browser: $("cookieBrowserName").value,
  };
}

// 1,2,3,5,7,8 → "1-3,5,7-8"
function ranges(nums) {
  const s = [...nums].sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i < s.length; i++) {
    let j = i;
    while (j + 1 < s.length && s[j + 1] === s[j] + 1) j++;
    out.push(j > i ? `${s[i]}-${s[j]}` : String(s[i]));
    i = j;
  }
  return out.join(",");
}

function scopeFields() {
  if (state.marks.size) return { playlist_mode: "playlist", playlist_items: ranges(state.marks) };
  return {
    playlist_mode: checked("plmode") || "single",
    playlist_start: $("plStart").value.trim(),
    playlist_end: $("plEnd").value.trim(),
    playlist_items: $("plItems").value.trim(),
  };
}

function currentInfo() {
  return state.info && state.infoUrl === $("url").value.trim() ? state.info : null;
}

function payload() {
  const info = currentInfo();
  return {
    url: $("url").value.trim(),
    quality: selectedQuality(),
    subtitles: outputType() !== "gif" && $("subs").checked,
    ...(outputType() === "gif" ? { gif_size: Number($("gifSize").value), gif_fps: Number($("gifFps").value), gif_loop: $("gifLoop").checked } : {}),
    auto_subs: true,
    sub_lang: ($("subLang").value.trim() || "en").slice(0, 20),
    clip_from: $("clipFrom").value.trim(),
    clip_to: $("clipTo").value.trim(),
    ...scopeFields(),
    ...accessFields(),
    title: info ? info.title || "" : "",
    thumbnail: info ? info.thumbnail || "" : "",
  };
}

/* ================= formats ================= */

function formatOptions() {
  const info = currentInfo();
  if (info && info.type === "gallery") {
    return [{ id: "best", name: "Original files", sub: "as posted" }];
  }
  if (outputType() === "audio") return AUDIO_LADDER;
  const ladder = info && info.qualities ? info.qualities.filter((q) => q.id === "best" || /^\d{3,4}$/.test(q.id)) : [];
  if (!ladder.length) return VIDEO_LADDER;
  return ladder.map((q) => q.id === "best"
    ? { id: "best", name: "Best available", sub: "highest stream" }
    : { id: q.id, name: `${q.id}p`, sub: q.filesize_str ? `~${q.filesize_str}` : "", size: q.filesize_str });
}

function renderFormats() {
  const info = currentInfo();
  const canGif = !info || (!info.is_live && !["is_live", "is_upcoming"].includes(info.live_status) && info.media !== "audio" && info.media !== "image");
  let type = state.preferredOutput;
  if (info?.media === "audio") type = "audio";
  else if (info?.media === "image" || (type === "gif" && !canGif) || (type === "audio" && info?.type === "gallery")) type = "video";
  document.querySelector(`input[name="otype"][value="${type}"]`).checked = true;
  $("otypeSeg").classList.toggle("hidden", info?.media === "audio" || info?.media === "image");
  $("gifTypeChoice").classList.toggle("hidden", !canGif);
  $("audioTypeChoice").classList.toggle("hidden", info?.type === "gallery");
  $("videoTypeLabel").textContent = info?.type === "gallery" ? "Original" : "Video";
  const gif = outputType() === "gif";
  document.body.dataset.output = outputType();
  $("formatFieldset").classList.toggle("hidden", gif);
  $("gifOptions").classList.toggle("hidden", !gif);
  $("dlLabel").textContent = gif ? "Keep as GIF" : "Keep";
  if (gif) $("discTrim").open = true;
  const keep = state.quality[type];
  const list = formatOptions();
  const sel = list.some((q) => q.id === keep) ? keep : list[0].id;
  $("qualities").innerHTML = list.map((q) => `<label class="fmt">
      <input type="radio" name="q" value="${esc(q.id)}"${q.id === sel ? " checked" : ""} />
      <span class="fmt-name">${esc(q.name)}${markSvg("circle")}</span>
      <span class="fmt-sub">${esc(q.sub || "")}</span>
    </label>`).join("");
  $("qualityNote").textContent =
    info && info.type === "gallery" ? "Every image and clip is saved exactly as the site serves it, full size."
      : outputType() === "audio" ? "Converted with ffmpeg once the download lands."
        : info && info.qualities ? "Sizes are the site’s estimates. A height it doesn’t have falls back to the next best."
          : "Best keeps the highest stream and saves MP4.";
  syncTally();
  syncGifSummary();
}

function syncGifSummary() {
  const info = currentInfo();
  const collection = state.marks.size || (checked("plmode") === "playlist" && (info?.type === "playlist" || info?.type === "gallery" || info?.part_of_playlist));
  const bad = trimProblem(payload());
  if (bad) { $("gifSummary").textContent = "Choose a valid timeframe to estimate your GIF."; return; }
  const start = parseTime($("clipFrom").value) ?? 0;
  const sourceDuration = !collection && info?.duration > 0 ? info.duration : null;
  const end = parseTime($("clipTo").value) ?? sourceDuration;
  const duration = end === null ? null : Math.max(0, Math.min(end, sourceDuration ?? end) - start);
  const scope = collection ? "One GIF per video; images are skipped" : start || $("clipTo").value.trim() ? "Selected timeframe" : "Whole video";
  const seconds = duration === null ? null : Number(duration.toFixed(2));
  const frames = Math.ceil(duration * Number($("gifFps").value));
  const timing = duration === null ? `${$("gifFps").value} fps` : `${seconds} second${seconds === 1 ? "" : "s"}${collection ? " per video, up to its end" : ""} · about ${frames} frame${frames === 1 ? "" : "s"}`;
  $("gifSummary").textContent = `${scope} · ${timing} · ${$("gifLoop").checked ? "loops forever" : "plays once"}${duration > 15 ? " · Large file likely; try a shorter clip." : ""}`;
}

/* ================= option notes ================= */

function syncNotes() {
  const from = $("clipFrom").value.trim(), to = $("clipTo").value.trim();
  setNote("trimState", from || to ? `${from || "start"} → ${to || "end"}` : "Whole video", Boolean(from || to));

  const mode = checked("plmode");
  let pl = "This item";
  if (state.marks.size) pl = `${state.marks.size} circled`;
  else if (mode === "playlist") {
    const s = $("plStart").value.trim(), e = $("plEnd").value.trim(), it = $("plItems").value.trim();
    pl = it ? `Items ${it}` : s || e ? `#${s || 1}–${e || "last"}` : "Whole collection";
  }
  setNote("plState", pl, Boolean(state.marks.size) || mode === "playlist");
  // A range only applies to the whole collection; "This item" ignores it.
  const ranged = mode === "playlist";
  for (const id of ["plStart", "plEnd", "plItems"]) $(id).disabled = !ranged;
  $("plRangeRow").classList.toggle("is-off", !ranged);
  $("plItemsField").classList.toggle("is-off", !ranged);

  const subs = $("subs").checked;
  setNote("subsState", subs ? ($("subLang").value.trim() || "en") : "Off", subs);

  const cm = cookiesMode();
  const browserName = $("cookieBrowserName").selectedOptions[0]?.textContent || "Browser";
  setNote("accessState", cm === "browser" ? `${browserName} cookies` : cm === "upload" ? "Cookies file" : "Not needed", cm !== "none");
  $("cookieNone").classList.toggle("hidden", cm !== "none");
  $("cookieBrowser").classList.toggle("hidden", cm !== "browser");
  $("cookieUpload").classList.toggle("hidden", cm !== "upload");
  syncTally();
  syncGifSummary();
}

function setNote(id, text, on) {
  const el = $(id);
  el.textContent = text;
  el.classList.toggle("is-on", on);
}

// The j-card's discipline: always say what this Keep adds up to.
function syncTally() {
  const info = currentInfo();
  const free = state.diskFree ? `${esc(state.diskFree)} free on this disk` : "";
  let what = "";
  if (info) {
    const gif = outputType() === "gif";
    const perVideo = gif ? " · one GIF per video" : "";
    const many = info.type === "playlist" || (info.type === "gallery" && info.count > 1);
    if (state.marks.size) what = `<b>${state.marks.size} circled item${state.marks.size === 1 ? "" : "s"}</b>${perVideo}`;
    else if (many && (checked("plmode") === "playlist")) what = `<b>${esc(info.count || "All")}${info.more ? "+" : ""} items</b>${perVideo}`;
    else if (info.type === "gallery") what = gif
      ? "<b>1 GIF per video</b> · images are skipped"
      : `<b>${info.count > 1 ? `${esc(info.count)} files` : "1 file"}</b>`;
    else {
      const size = (formatOptions().find((q) => q.id === selectedQuality()) || {}).size;
      what = `<b>1 ${gif ? "GIF" : outputType() === "audio" ? "audio file" : "video"}</b>${size && !gif ? ` · ~${esc(size)}` : ""}`;
    }
  }
  $("tally").innerHTML = [what, free].filter(Boolean).join(" · ");
}

/* ================= the print ================= */

function looksLikeUrl(v) {
  return /^(https?:\/\/)?[a-z0-9-]+(\.[a-z0-9-]+)+\/\S/i.test(v.trim());
}

function showProblem(msg) {
  $("infoErrorText").textContent = msg;
  $("infoErrorFix").classList.toggle("hidden", !NEEDS_ACCESS.test(msg));
  const box = $("infoError");
  box.classList.remove("hidden");
  // restart the strike
  const svg = box.querySelector(".mark");
  svg.replaceWith(svg.cloneNode(true));
}
function hideProblem() { $("infoError").classList.add("hidden"); }

function showBlank() {
  $("gifResult").classList.add("hidden");
  $("blank").classList.remove("hidden");
  $("print").classList.add("hidden");
  document.body.dataset.kind = "";
  document.body.dataset.media = "";
}

function renderExposing(url) {
  announce("Reading the link…");
  $("gifResult").classList.add("hidden");
  $("blank").classList.add("hidden");
  const host = (url.replace(/^https?:\/\//, "").split("/")[0] || "").replace(/^www\./, "");
  $("print").innerHTML = `<figure class="print is-exposing">
      <div class="rebate">
        <div class="edge-row"><span class="edge">${esc(host)}</span><span class="edge edge-amber">Exposing…</span></div>
        <div class="frame"><div class="frame-empty"><span class="edge edge-dim">Reading the link</span></div></div>
        <div class="edge-row"><span class="edge edge-dim">01</span><span class="edge edge-dim">offtube</span></div>
      </div>
      <figcaption class="print-cap"><h2 class="print-title">Reading the link…</h2><p class="print-meta">Nothing is kept until you press Keep.</p></figcaption>
    </figure>`;
  $("print").classList.remove("hidden");
}

function developHtml(src, extra = "") {
  return `<span class="develop"><img class="pos" src="${esc(src)}" alt="" />` +
    `<img class="neg" src="${esc(src)}" alt="" /><span class="mark-slot">${extra}</span></span>`;
}

// A cell is named by its visible text ("03 · Video 1:05"), which speech
// input users say aloud, so it must stay in the name (WCAG 2.5.3). Where a
// thumbnail stands in for the title, the title follows for screen readers.
const hiddenTitle = (e) => (e.thumbnail && e.title ? `<span class="visually-hidden">, ${esc(e.title)}</span>` : "");

function renderPrint(info) {
  $("gifResult").classList.add("hidden");
  state.info = info;
  state.infoUrl = $("url").value.trim();
  state.marks.clear();
  document.body.dataset.kind = info.type === "gallery" ? "gallery" : "";
  document.body.dataset.media = info.media || "";
  const site = (info.source && info.source.label) || "Link";
  const many = info.type === "playlist" || (info.type === "gallery" && info.count > 1);
  const box = $("print");
  $("blank").classList.add("hidden");

  if (many) {
    const entries = info.entries || [];
    const count = info.count || entries.length;
    const label = info.type === "playlist" ? "Playlist" : "Collection";
    box.innerHTML = `<figure class="print sheet" id="printFig">
      <div class="rebate">
        <div class="edge-row"><span class="edge">${esc(site)} · ${label}</span><span class="edge edge-dim">${esc(count)}${info.more ? "+" : ""} items</span></div>
        <ol class="contact">${entries.map((e, i) => `<li>
          <button type="button" class="cell" aria-pressed="false" data-i="${i + 1}">
            <span class="cell-frame"><span class="cell-img">${e.thumbnail ? `<img src="${esc(e.thumbnail)}" alt="" loading="lazy" />` : `<span class="edge">${esc(e.title || `Item ${i + 1}`)}</span>`}</span>${markSvg("circle")}</span>
            <span class="edge-row"><span class="edge edge-dim">${pad2(i + 1)}${e.is_video ? " · Video" : ""}</span><span class="edge edge-dim">${esc(e.duration_str && e.duration_str !== "-" ? e.duration_str : "")}</span></span>${hiddenTitle(e)}
          </button></li>`).join("")}</ol>
        <div class="edge-row"><span class="edge edge-dim">Circle prints to keep only those</span><span class="edge" id="sheetCount"></span></div>
      </div>
      <figcaption class="print-cap">
        <h2 class="print-title">${esc(info.title || `${site} ${label.toLowerCase()}`)}</h2>
        ${info.uploader ? `<p class="print-meta">${esc(info.uploader)}</p>` : ""}
        <p class="print-tools"><span class="count" id="markCount"></span><button class="text-btn" type="button" id="markAll">Circle all shown</button><button class="text-btn" type="button" id="markNone">Clear circles</button></p>
        ${info.more || count > entries.length ? `<p class="print-note">The sheet shows the first ${entries.length}. Keep takes the whole collection unless you circle prints or set a range under Collection.</p>` : ""}
      </figcaption>
    </figure>`;
    document.querySelector('input[name="plmode"][value="playlist"]').checked = true;
    syncMarks();
  } else {
    const kind = info.type === "gallery" ? MEDIA_WORD[info.media] || "Post" : MEDIA_WORD[info.media] || "Video";
    const right = info.is_live ? "Live" : info.duration_str && info.duration_str !== "-" ? info.duration_str : "";
    const top = (info.qualities || []).find((q) => /^\d+$/.test(q.id));
    const bottom = info.type === "gallery" ? "Original file" : top ? `Up to ${top.id}p` : info.media === "audio" ? "Audio" : "";
    const meta = [info.uploader, info.type === "video" && right && !info.is_live ? right : ""].filter(Boolean).map(esc).join(" · ");
    const notes = [
      info.is_live ? "Live stream: keeping records from now on, and trim doesn’t apply." : "",
      info.part_of_playlist ? "Part of a playlist. Only this one is kept unless you choose Collection." : "",
    ].filter(Boolean).map((n) => `<p class="print-note">${esc(n)}</p>`).join("");
    box.innerHTML = `<figure class="print" id="printFig">
      <div class="rebate">
        <div class="edge-row"><span class="edge">${esc(site)} · ${esc(kind)}</span><span class="edge edge-dim">${esc(right)}</span></div>
        <div class="frame">${info.thumbnail ? developHtml(info.thumbnail) : `<div class="frame-empty"><span class="edge">${esc(site)}</span><span class="mark-slot"></span></div>`}</div>
        <div class="edge-row"><span class="edge edge-dim">01A</span><span class="edge" id="printEdge">${esc(bottom)}</span></div>
      </div>
      <figcaption class="print-cap">
        <h2 class="print-title">${esc(info.title || "Untitled")}</h2>
        ${meta ? `<p class="print-meta">${meta}</p>` : ""}
        ${notes}
      </figcaption>
    </figure>`;
    document.querySelector('input[name="plmode"][value="single"]').checked = true;
  }
  if (info.media === "audio") document.querySelector('input[name="otype"][value="audio"]').checked = true;
  box.classList.remove("hidden");
  announce(printSummary(info));
  renderFormats();
  syncNotes();
}

function syncMarks() {
  const info = currentInfo();
  if (!info) return;
  const n = state.marks.size;
  const count = info.count || 0;
  const all = `${count}${info.more ? "+" : ""}`;
  if ($("markCount")) $("markCount").textContent = n ? `${n} circled — only these are kept` : `Keeping all ${all}`;
  if ($("sheetCount")) $("sheetCount").textContent = n ? `${n} circled` : `All ${all}`;
  syncNotes();
}

$("print").addEventListener("click", (e) => {
  const cell = e.target.closest(".cell");
  if (cell) {
    const i = Number(cell.dataset.i);
    const on = cell.getAttribute("aria-pressed") !== "true";
    cell.setAttribute("aria-pressed", String(on));
    if (on) state.marks.add(i); else state.marks.delete(i);
    syncMarks();
    return;
  }
  if (e.target.closest("#markAll")) {
    $$(".cell").forEach((c) => { c.setAttribute("aria-pressed", "true"); state.marks.add(Number(c.dataset.i)); });
    syncMarks();
  } else if (e.target.closest("#markNone")) {
    $$(".cell").forEach((c) => c.setAttribute("aria-pressed", "false"));
    state.marks.clear();
    syncMarks();
  }
});

/* ================= inspect ================= */

async function inspect() {
  const url = $("url").value.trim();
  hideProblem();
  if (!url) { showProblem("Paste a link first."); $("url").focus(); return; }
  const my = ++state.fetchToken;
  state.printJobId = null;
  state.previewJobId = null;
  const btn = $("fetchBtn");
  btn.disabled = true;
  btn.textContent = "Reading…";
  renderExposing(url);
  try {
    const { info } = await post("/api/info", { url, ...accessFields() });
    if (my !== state.fetchToken || url !== $("url").value.trim()) return;
    renderPrint(info);
  } catch (err) {
    if (my !== state.fetchToken) return;
    state.info = null;
    state.marks.clear();
    showBlank();
    renderFormats();
    syncNotes();
    showProblem(err.message);
    if (NEEDS_ACCESS.test(err.message)) $("discAccess").open = true;
  } finally {
    if (my === state.fetchToken) { btn.disabled = false; btn.textContent = "Preview"; }
  }
}

let debounce = null;
$("pasteForm").addEventListener("submit", (e) => { e.preventDefault(); clearTimeout(debounce); inspect(); });
$("url").addEventListener("input", (e) => {
  clearTimeout(debounce);
  ++state.fetchToken;
  state.info = null;
  state.infoUrl = "";
  state.printJobId = null;
  state.previewJobId = null;
  state.marks.clear();
  $("fetchBtn").disabled = false;
  $("fetchBtn").textContent = "Preview";
  showBlank();
  hideProblem();
  setTrimError("");
  renderFormats();
  syncNotes();
  const v = $("url").value.trim();
  if (!v) return;
  // A paste or drop delivers the whole link at once; typing gets a pause to finish.
  const whole = e.inputType === "insertFromPaste" || e.inputType === "insertFromDrop";
  debounce = setTimeout(() => { if (looksLikeUrl(v) && v !== state.infoUrl) inspect(); }, whole ? 150 : 700);
});
$("infoErrorFix").addEventListener("click", () => {
  const d = $("discAccess");
  d.open = true;
  d.scrollIntoView({ behavior: "smooth", block: "center" });
  d.querySelector('input[value="browser"]').focus();
});

/* ================= keep ================= */

function parseTime(s) {
  s = String(s || "").trim();
  if (!s) return null;
  if (!/^\d+(?::\d+){0,2}(?:\.\d+)?$/.test(s)) return null;
  const parts = s.split(":");
  if (parts.length > 3) return null;
  const nums = parts.map(Number);
  if (nums.some((n) => !Number.isFinite(n) || n < 0)) return null;
  const total = nums.reduce((a, n) => a * 60 + n, 0);
  return Number.isFinite(total) ? total : null;
}

function trimProblem(p) {
  const f = p.clip_from ? parseTime(p.clip_from) : null;
  const t = p.clip_to ? parseTime(p.clip_to) : null;
  if ((p.clip_from && f === null) || (p.clip_to && t === null)) return "Those times don’t read. Use seconds or m:ss, like 1:30 → 2:45.";
  if (t !== null && t <= (f ?? 0)) return "“To” has to come after “From”.";
  const info = currentInfo();
  if (info && info.is_live && (p.clip_from || p.clip_to)) return "Trim doesn’t apply to a live stream. Clear it, or wait for the stream to end.";
  if (p.quality === "gif" && info?.duration && info.type !== "playlist" && info.type !== "gallery" && checked("plmode") !== "playlist" && f >= info.duration) return "“From” is past the end of this video. Choose an earlier time.";
  return "";
}

function setTrimError(message) {
  $("trimError").textContent = message;
  $("trimError").classList.toggle("hidden", !message);
  for (const id of ["clipFrom", "clipTo"]) $(id).setAttribute("aria-invalid", String(Boolean(message)));
}

$("dlBtn").addEventListener("click", async () => {
  const p = payload();
  setTrimError("");
  if (!p.url) { showProblem("Paste a link first."); $("url").focus(); return; }
  const bad = trimProblem(p);
  if (bad) {
    setTrimError(bad);
    $("discTrim").open = true;
    $(p.clip_from && parseTime(p.clip_from) === null ? "clipFrom" : "clipTo").focus();
    return;
  }
  const btn = $("dlBtn");
  $("gifResult").classList.add("hidden");
  btn.disabled = true;
  $("dlLabel").textContent = "Starting…";
  setDeck("Starting…", "busy", 0);
  try {
    const { job_id } = await post("/api/download", p);
    state.activeJobId = job_id;
    state.printJobId = $("url").value.trim() === p.url ? job_id : null;
    state.previewJobId = state.printJobId;
    $("cancelBtn").classList.remove("hidden");
    if (state.printJobId && currentInfo()) {
      const frame = $("printFig")?.querySelector(".frame");
      if (frame?.dataset.gif) {
        frame.innerHTML = currentInfo().thumbnail ? developHtml(currentInfo().thumbnail) : '<div class="frame-empty"><span class="mark-slot"></span></div>';
        delete frame.dataset.gif;
      }
      developPrint(0);
    }
    pollJob(job_id);
    refreshQueue();
  } catch (err) {
    setDeck(err.message, "error", 0);
    if (NEEDS_ACCESS.test(err.message)) $("discAccess").open = true;
  } finally {
    btn.disabled = false;
    $("dlLabel").textContent = outputType() === "gif" ? "Keep as GIF" : "Keep";
  }
});

$("cancelBtn").addEventListener("click", async () => {
  if (!state.activeJobId) return;
  $("cancelBtn").disabled = true;
  try {
    await post("/api/cancel", { job_id: state.activeJobId });
    setDeck("Stopping…", "busy");
  } catch (err) {
    setDeck(`Couldn’t cancel: ${err.message}`, "error");
  } finally {
    $("cancelBtn").disabled = false;
    refreshQueue();
  }
});

function setDeck(text, mode, pct) {
  const line = $("jobStatus").parentElement;
  $("jobStatus").textContent = text;
  line.classList.toggle("is-error", mode === "error");
  line.classList.toggle("is-done", mode === "done");
  $("meter").classList.toggle("is-error", mode === "error");
  $("meter").classList.toggle("is-done", mode === "done");
  if (typeof pct === "number") {
    $("meter").style.setProperty("--p", String(Math.max(0, Math.min(100, pct)) / 100));
    $("meter").setAttribute("aria-valuenow", String(Math.round(pct)));
    $("jobPct").textContent = mode === "busy" && pct > 0 ? `${Math.round(pct)}%` : "";
  }
  if (mode === "error") announce(text);
}

// The print in the stage develops while its own job runs.
function developPrint(pct) {
  const fig = $("printFig");
  if (!fig) return;
  fig.classList.add("is-developing");
  fig.style.setProperty("--p", String(pct / 100));
  const edge = $("printEdge");
  if (edge) { edge.textContent = `Developing ${Math.round(pct)}%`; edge.classList.add("edge-amber"); }
}
function finishPrint(kind, label) {
  const fig = $("printFig");
  if (!fig) return;
  fig.classList.remove("is-developing");
  const slot = fig.querySelector(".mark-slot");
  if (slot && kind) slot.innerHTML = markSvg(kind);
  const edge = $("printEdge");
  if (edge) { edge.textContent = label; edge.classList.remove("edge-amber"); }
}

function showJob(job, live) {
  const pct = job.progress || 0;
  speakJob(job);
  $("jobLog").textContent = (job.log || []).slice(-40).join("\n");
  const isSource = state.printJobId === job.id;
  const isPrint = isSource && Boolean(currentInfo());
  const preview = isSource || state.previewJobId === job.id;
  if (job.status !== "done" || job.quality !== "gif" || !preview) $("gifResult").classList.add("hidden");
  if (job.status === "done") {
    const files = job.files || [];
    setDeck(files.length > 1 ? `Kept ${files.length} files.` : `Kept ${files[0] || "the file"}.`, "done", 100);
    if (isPrint) finishPrint("tick", "Kept");
    if (preview && job.quality === "gif" && files.length) {
      const fileUrl = `/files/${encodeURIComponent(files[0])}`;
      const frame = isPrint && $("printFig")?.querySelector(".frame");
      if (frame && frame.dataset.gif !== fileUrl) {
        frame.innerHTML = `<span class="develop"><img class="pos gif-preview" src="${esc(fileUrl)}" alt="Your finished GIF" /><span class="mark-slot">${markSvg("tick")}</span></span>`;
        frame.dataset.gif = fileUrl;
      }
      $("gifPreviewFrame").classList.toggle("hidden", Boolean(frame));
      if (!frame && $("gifPreview").dataset.file !== fileUrl) {
        $("gifPreview").innerHTML = `<img class="gif-preview" src="${esc(fileUrl)}" alt="Your finished GIF" />`;
        $("gifPreview").dataset.file = fileUrl;
      }
      $("gifSave").href = fileUrl;
      $("gifReady").textContent = files.length > 1 ? `${files.length} GIFs are ready. Previewing the first; all are in Kept.` : "Your GIF is ready.";
      $("gifResult").classList.remove("hidden");
    }
  } else if (job.status === "error") {
    setDeck(job.detail || "That didn’t work.", "error", pct);
    if (isPrint) finishPrint("strike", "Failed");
    if (NEEDS_ACCESS.test(job.detail || "")) $("discAccess").open = true;
  } else if (job.status === "cancelled") {
    setDeck("Cancelled.", "idle", 0);
    if (isPrint) finishPrint("", "Cancelled");
  } else {
    let extra = "";
    if (job.speed) extra += ` · ${(job.speed / 1e6).toFixed(1)} MB/s`;
    if (job.eta) extra += ` · ${job.eta}s left`;
    setDeck((job.detail || STATE_WORD[job.status] || job.status) + extra, "busy", pct);
    if (isPrint && live) developPrint(pct);
  }
  const running = ACTIVE.includes(job.status);
  $("cancelBtn").classList.toggle("hidden", !(running && live));
}

function pollJob(jobId) {
  clearInterval(state.pollTimer);
  let inFlight = false;
  state.pollTimer = setInterval(async () => {
    if (inFlight) return;
    inFlight = true;
    try {
      const job = await api(`/api/job?id=${encodeURIComponent(jobId)}`);
      if (state.activeJobId !== jobId) return;
      showJob(job, true);
      if (!ACTIVE.includes(job.status)) {
        clearInterval(state.pollTimer);
        state.activeJobId = null;
        if (job.status === "done") loadFiles();
        refreshQueue();
      }
    } catch {
      if (state.activeJobId === jobId) {
        setDeck("Lost the server — retrying…", "busy");
        if (state.spoken !== "lost") { state.spoken = "lost"; announce("Lost the server — retrying…"); }
      }
    } finally {
      inFlight = false;
    }
  }, 700);
}

async function viewJob(jobId) {
  try {
    const job = await api(`/api/job?id=${encodeURIComponent(jobId)}`);
    clearInterval(state.pollTimer);
    state.activeJobId = ACTIVE.includes(job.status) ? jobId : null;
    state.previewJobId = jobId;
    showJob(job, Boolean(state.activeJobId));
    if (state.activeJobId) pollJob(jobId);
    $("logBox").open = job.status === "error";
    $$(".strip-frame").forEach((li) => li.setAttribute("aria-current", String(li.dataset.jid === jobId)));
  } catch (err) {
    setDeck(`Couldn’t open that job: ${err.message}`, "error");
  }
}

/* ================= this session: a strip that never reflows ================= */

function stripFrame(jid) {
  const li = document.createElement("li");
  li.className = "strip-frame";
  li.dataset.jid = jid;
  li.tabIndex = 0;
  li.innerHTML = `<div class="edge-row"><span class="edge edge-dim js-no"></span><span class="edge js-state"></span></div>
    <div class="frame js-frame"></div>
    <p class="strip-title js-title"></p>
    <div class="edge-row"><span class="edge edge-dim js-meta"></span></div>
    <div class="strip-actions"><button type="button" class="text-btn js-cancel">Cancel</button><button type="button" class="text-btn js-retry">Retry</button></div>`;
  return li;
}

// What a session frame shows. A job started without inspect (or a gallery
// post) has no thumbnail or title of its own: once kept, fall back to the
// saved file itself, and name it by site and kind, never by a raw URL.
function jobDisplay(job) {
  const file = (job.files || [])[0] || "";
  const ext = (file.split(".").pop() || "").toLowerCase();
  const kind = /^(jpe?g|png|gif|webp|avif|heic)$/.test(ext) ? "image"
    : /^(mp4|webm|mkv|mov|m4v)$/.test(ext) ? "video" : /^(mp3|m4a|opus|ogg|flac|wav)$/.test(ext) ? "audio" : "";
  const fileUrl = file ? `/files/${encodeURIComponent(file)}` : "";
  const tail = ((job.url || "").replace(/[?#].*$/, "").split("/").filter(Boolean).pop() || "").slice(0, 24);
  const word = { image: "image", video: "video", audio: "track" }[kind] || "post";
  return {
    thumb: ext === "gif" ? fileUrl : job.thumbnail || (kind === "image" ? fileUrl : ""),
    video: !job.thumbnail && kind === "video" ? fileUrl : "",
    title: job.title || `${job.source || "Saved"} ${word}`,
    meta: [job.source, job.title ? (job.quality && job.quality !== "best" ? job.quality.replace("audio_", "") : "") : tail].filter(Boolean).join(" · "),
  };
}

function updateFrame(li, job, no) {
  const pct = Math.round(job.progress || 0);
  const active = ACTIVE.includes(job.status);
  li.querySelector(".js-no").textContent = pad2(no);
  const st = li.querySelector(".js-state");
  st.textContent = job.quality === "gif" && job.status === "processing" ? `Making GIF ${pct}%` : job.status === "downloading" && pct ? `${STATE_WORD.downloading} ${pct}%` : STATE_WORD[job.status] || job.status;
  st.classList.toggle("edge-amber", active);
  const frame = li.querySelector(".js-frame");
  const show = jobDisplay(job);
  const key = show.thumb || show.video;
  if (frame.dataset.thumb !== key) {
    frame.dataset.thumb = key;
    frame.innerHTML = show.thumb ? developHtml(show.thumb)
      : show.video ? `<span class="develop"><video class="pos" src="${esc(show.video)}#t=0.5" preload="metadata" muted playsinline aria-hidden="true"></video><span class="mark-slot"></span></span>`
        : `<div class="frame-empty"><span class="edge">${esc(job.source || "offtube")}</span><span class="mark-slot"></span></div>`;
    frame.dataset.mark = "";
  }
  li.classList.toggle("is-developing", active);
  li.style.setProperty("--p", String(active ? pct / 100 : 1));
  const markKind = job.status === "done" ? "tick" : job.status === "error" ? "strike" : "";
  if (frame.dataset.mark !== markKind) {
    frame.dataset.mark = markKind;
    const slot = frame.querySelector(".mark-slot");
    if (slot) slot.innerHTML = markKind ? markSvg(markKind) : "";
  }
  const title = show.title;
  li.querySelector(".js-title").textContent = title;
  li.querySelector(".js-meta").textContent = show.meta;
  li.setAttribute("aria-label", `${title}: ${st.textContent}`);
  li.querySelector(".js-cancel").classList.toggle("hidden", !active);
  li.querySelector(".js-retry").classList.toggle("hidden", job.status !== "error" && job.status !== "cancelled");
}

async function refreshQueue() {
  try {
    const { jobs } = await api("/api/jobs");
    const all = jobs || [];
    const shown = all.slice(-12).reverse();
    const ol = $("queue");
    const keep = new Set(shown.map((j) => j.id));
    $$(".strip-frame", ol).forEach((li) => { if (!keep.has(li.dataset.jid)) li.remove(); });
    shown.forEach((job, i) => {
      let li = ol.querySelector(`[data-jid="${CSS.escape(job.id)}"]`);
      if (!li) li = stripFrame(job.id);
      updateFrame(li, job, all.indexOf(job) + 1);
      if (ol.children[i] !== li) ol.insertBefore(li, ol.children[i] || null);
    });
    const active = all.filter((j) => ACTIVE.includes(j.status)).length;
    $("queueCount").textContent = all.length ? `${active ? `${active} developing · ` : ""}${all.length} print${all.length === 1 ? "" : "s"}` : "";
    $("queueEmpty").classList.toggle("hidden", all.length > 0);
    $("clearQueue").classList.toggle("hidden", !all.some((j) => !ACTIVE.includes(j.status)));
  } catch { /* the health line already says the server is down */ }
}

$("queue").addEventListener("click", async (e) => {
  const li = e.target.closest(".strip-frame");
  if (!li) return;
  const jid = li.dataset.jid;
  if (e.target.closest(".js-cancel")) {
    e.target.disabled = true;
    try { await post("/api/cancel", { job_id: jid }); } catch (err) { setDeck(`Couldn’t cancel: ${err.message}`, "error"); }
    refreshQueue();
  } else if (e.target.closest(".js-retry")) {
    e.target.disabled = true;
    try {
      const { job_id } = await post("/api/retry", { job_id: jid });
      if (state.printJobId === jid) state.printJobId = job_id;
      state.previewJobId = job_id;
      state.activeJobId = job_id;
      $("gifResult").classList.add("hidden");
      setDeck("Starting…", "busy", 0);
      $("cancelBtn").classList.remove("hidden");
      pollJob(job_id);
    } catch (err) { setDeck(`Couldn’t retry: ${err.message}`, "error"); }
    e.target.disabled = false;
    refreshQueue();
  } else {
    viewJob(jid);
  }
});
$("queue").addEventListener("keydown", (e) => {
  const li = e.target.closest(".strip-frame");
  if (li && e.target === li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); viewJob(li.dataset.jid); }
});
$("clearQueue").addEventListener("click", async () => {
  try { await post("/api/jobs/clear", {}); } catch (err) { setDeck(`Couldn’t clear: ${err.message}`, "error"); }
  refreshQueue();
});

/* ================= kept: the library sheet ================= */

const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

const lazyVideos = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (!en.isIntersecting) return;
    const v = en.target;
    v.src = v.dataset.src;
    lazyVideos.unobserve(v);
  });
}, { rootMargin: "200px" }) : null;

function keptThumb(f) {
  const ext = (f.name.split(".").pop() || "").toUpperCase();
  if (f.kind === "image") return `<img src="${esc(f.url)}" alt="" loading="lazy" />`;
  if (f.kind === "video") return `<video data-src="${esc(f.url)}#t=0.5" preload="metadata" muted playsinline aria-hidden="true"></video>`;
  return `<span class="edge">${esc(ext || "File")}</span>`;
}

const FILES_PAGE = 60;
let filesRequest = 0; // the latest library request; older answers are dropped
let searchTimer = null;

function renderFiles() {
  const list = state.files;
  $("footFiles").textContent = state.total
    ? `${state.total} file${state.total === 1 ? "" : "s"}${state.diskFree ? ` · ${state.diskFree} free` : ""}`
    : "";
  $("filesEmpty").textContent = state.total ? "Nothing matches." : "Nothing kept yet. Files you keep land in downloads/.";
  $("filesEmpty").classList.toggle("hidden", list.length > 0);
  const more = state.matched - list.length;
  $("files").innerHTML = list.map((f, i) => {
    const ext = (f.name.split(".").pop() || "").toUpperCase();
    const when = f.modified ? dateFmt.format(new Date(f.modified)) : "";
    return `<li class="kept-cell">
      <div class="kept-thumb">${keptThumb(f)}</div>
      <div class="edge-row"><span class="edge edge-dim">${pad2(i + 1)}</span><span class="edge">${esc(ext)} · ${esc(f.size_str)}</span></div>
      <p class="kept-name" title="${esc(f.name)}">${esc(f.name)}</p>
      <p class="kept-meta">${esc(when)}</p>
      <div class="kept-actions"><a href="${esc(f.url)}" download>Save</a><button class="text-btn" type="button" data-del="${esc(f.name)}">Delete</button></div>
    </li>`;
  }).join("") + (more > 0
    ? `<li class="kept-more"><button class="btn btn-quiet" type="button" id="moreFiles">Show ${Math.min(FILES_PAGE, more)} more</button></li>` : "");
  $$("video[data-src]", $("files")).forEach((v) => { if (lazyVideos) lazyVideos.observe(v); else v.src = v.dataset.src; });
}

// The server searches and pages the whole library. A refresh keeps as many
// rows as are showing, `more` appends the next page, and `reset` starts over
// for a new search or kind.
async function loadFiles({ more = false, reset = false } = {}) {
  const my = ++filesRequest;
  const shown = state.files.length;
  const params = new URLSearchParams({
    offset: String(more ? shown : 0),
    limit: String(more || reset ? FILES_PAGE : Math.min(500, Math.max(FILES_PAGE, shown))),
  });
  const q = $("fileSearch").value.trim();
  const kind = checked("kind") || "all";
  if (q) params.set("q", q);
  if (kind !== "all") params.set("kind", kind);
  try {
    const data = await api(`/api/files?${params}`);
    if (my !== filesRequest) return;
    const page = data.files || [];
    if (more) {
      const have = new Set(state.files.map((f) => f.name));
      state.files = state.files.concat(page.filter((f) => !have.has(f.name)));
    } else {
      state.files = page;
    }
    state.total = data.total ?? state.files.length;
    state.matched = data.matched ?? state.files.length;
    state.diskFree = data.disk_free_str || "";
    renderFiles();
    syncTally();
  } catch { /* server down */ }
}

$("refreshFiles").addEventListener("click", () => loadFiles());
$("fileSearch").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadFiles({ reset: true }), 200);
});
$$('input[name="kind"]').forEach((r) => r.addEventListener("change", () => loadFiles({ reset: true })));
$("files").addEventListener("click", async (e) => {
  if (e.target.closest("#moreFiles")) { e.target.disabled = true; loadFiles({ more: true }); return; }
  const btn = e.target.closest("[data-del]");
  if (!btn) return;
  const name = btn.dataset.del;
  if (!window.confirm(`Delete “${name}” from downloads/? This can’t be undone.`)) return;
  btn.disabled = true;
  try {
    await post("/api/files/delete", { name });
    loadFiles();
  } catch (err) {
    setDeck(`Couldn’t delete: ${err.message}`, "error");
    btn.disabled = false;
  }
});

/* ================= health ================= */

async function checkHealth() {
  const el = $("health");
  try {
    const { checks } = await api("/api/health");
    const problems = [];
    if (!checks.ffmpeg) problems.push("ffmpeg missing");
    if (checks.yt_dlp === "missing") problems.push("yt-dlp missing");
    if (checks.gallery_dl === "missing") problems.push("gallery-dl missing");
    if (!checks.js_runtime_ok) problems.push("no JS runtime");
    el.className = problems.length ? "health is-bad" : "health is-ok";
    $("healthText").textContent = problems.length ? problems.join(" · ") : "Ready";
    const env = `yt-dlp ${checks.yt_dlp} · gallery-dl ${checks.gallery_dl || "?"} · ${(checks.ffmpeg_version || "ffmpeg ?").split(" Copyright")[0]} · JS ${checks.js_runtime || "none"}`;
    el.title = env;
    $("envLine").textContent = env;
  } catch {
    el.className = "health is-bad";
    $("healthText").textContent = "Server off — run ./run.sh";
    el.title = "";
  }
}

/* ================= prefs (local only; never cookies) ================= */

const PREFS = "offtube.prefs";
function savePrefs() {
  try {
    localStorage.setItem(PREFS, JSON.stringify({
      otype: state.preferredOutput, quality: selectedQuality(), qualities: state.quality, cmode: cookiesMode(),
      browser: $("cookieBrowserName").value, subLang: $("subLang").value, subs: $("subs").checked,
      gifSize: $("gifSize").value, gifFps: $("gifFps").value, gifLoop: $("gifLoop").checked,
    }));
  } catch { /* private mode */ }
}
function restorePrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem(PREFS) || "{}"); } catch { p = {}; }
  if (!p || typeof p !== "object") p = {};
  const pick = (name, v) => { const r = v && document.querySelector(`input[name="${name}"][value="${CSS.escape(v)}"]`); if (r) r.checked = true; };
  pick("otype", p.otype);
  state.preferredOutput = outputType();
  for (const type of ["video", "audio"]) {
    const value = p.qualities?.[type] || p.quality;
    if ((type === "audio" ? AUDIO_LADDER : VIDEO_LADDER).some((q) => q.id === value)) state.quality[type] = value;
  }
  pick("cmode", p.cmode);
  if (p.browser) $("cookieBrowserName").value = p.browser;
  if (p.subLang) $("subLang").value = p.subLang;
  if (typeof p.subs === "boolean") $("subs").checked = p.subs;
  if (["320", "480", "640", "960"].includes(p.gifSize)) $("gifSize").value = p.gifSize;
  if (["10", "15", "20", "25", "30"].includes(p.gifFps)) $("gifFps").value = p.gifFps;
  if (typeof p.gifLoop === "boolean") $("gifLoop").checked = p.gifLoop;
  renderFormats();
}

/* ================= the loupe ================= */
// What a contact sheet is read with: hover a print, a sheet frame or a kept
// image (mouse only) and a lens holds over it at 2.5×, following the pointer.
// It appears after a short pause so sweeping across the sheet doesn't
// flicker, and it never takes the click (circling still works).
function wireLoupe(zoom = 2.5) {
  if (!matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  const lens = document.createElement("div");
  lens.className = "loupe";
  lens.setAttribute("aria-hidden", "true");
  document.body.append(lens);
  const TARGET = ".cell-img img, .develop img, .kept-thumb img";
  let img = null, timer = null, frame = 0, last = null;
  const hide = () => { clearTimeout(timer); img = null; lens.classList.remove("is-on"); };
  const place = () => {
    frame = 0;
    if (!img || !last || !img.naturalWidth) return;
    const r = img.getBoundingClientRect();
    const fit = getComputedStyle(img).objectFit;
    const scale = fit === "cover" ? Math.max(r.width / img.naturalWidth, r.height / img.naturalHeight)
      : fit === "contain" ? Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight)
        : r.width / img.naturalWidth;
    const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
    const fx = (last.clientX - r.left - (r.width - w) / 2) / w;
    const fy = (last.clientY - r.top - (r.height - h) / 2) / h;
    const size = lens.offsetWidth, inner = lens.clientWidth;
    lens.style.backgroundImage = `url(${JSON.stringify(img.currentSrc || img.src)})`;
    lens.style.backgroundSize = `${w * zoom}px ${h * zoom}px`;
    lens.style.backgroundPosition = `${inner / 2 - fx * w * zoom}px ${inner / 2 - fy * h * zoom}px`;
    lens.style.transform = `translate(${last.clientX - size / 2}px, ${last.clientY - size / 2}px)`;
  };
  document.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    const host = e.target.closest(".cell-img, .develop, .kept-thumb");
    const target = host && host.querySelector("img.pos, .cell-img > img, .kept-thumb > img");
    if (!target || !target.matches(TARGET)) { hide(); return; }
    last = e;
    if (target !== img) {
      hide();
      img = target;
      timer = setTimeout(() => { place(); lens.classList.add("is-on"); }, 220);
      return;
    }
    if (!frame) frame = requestAnimationFrame(place);
  }, { passive: true });
  document.documentElement.addEventListener("mouseleave", hide);
  window.addEventListener("scroll", hide, { passive: true });
}

/* ================= wiring ================= */

$$('input[name="otype"]').forEach((r) => r.addEventListener("change", () => { state.preferredOutput = outputType(); renderFormats(); }));
$("qualities").addEventListener("change", () => { state.quality[outputType()] = checked("q"); syncTally(); savePrefs(); });
["clipFrom", "clipTo", "plStart", "plEnd", "plItems", "subLang"].forEach((id) => $(id).addEventListener("input", () => { setTrimError(""); syncNotes(); }));
$$('input[name="plmode"], input[name="cmode"]').forEach((r) => r.addEventListener("change", syncNotes));
$("subs").addEventListener("change", syncNotes);
$("cookieBrowserName").addEventListener("change", syncNotes);
$("clearClip").addEventListener("click", () => {
  $("clipFrom").value = "";
  $("clipTo").value = "";
  setTrimError("");
  syncNotes();
  $("clipFrom").focus();
});
$("cookieFile").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("cookieText").value = await f.text();
  $("cookieFileName").textContent = `${f.name} · ${(f.size / 1024).toFixed(1)} KB`;
});
document.addEventListener("change", (e) => {
  if (e.target.matches('input[name="otype"], input[name="cmode"], #cookieBrowserName, #subs')) savePrefs();
});
$("subLang").addEventListener("input", savePrefs);
["gifSize", "gifFps", "gifLoop"].forEach((id) => $(id).addEventListener("change", () => { syncGifSummary(); savePrefs(); }));
$("gifFiveSeconds").addEventListener("click", () => {
  const start = parseTime($("clipFrom").value) ?? 0;
  const duration = currentInfo()?.duration;
  $("clipFrom").value = String(duration && start >= duration ? 0 : start);
  $("clipTo").value = String(Math.min(Number($("clipFrom").value) + 5, duration || Infinity));
  $("discTrim").open = true;
  setTrimError("");
  syncNotes();
  $("clipFrom").focus();
});

restorePrefs();
syncNotes();
wireLoupe();
checkHealth();
loadFiles();
refreshQueue();
setInterval(() => refreshQueue(), 4000);
setInterval(checkHealth, 30000);
