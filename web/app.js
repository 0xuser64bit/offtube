/* yt-downloader frontend — no build step, plain JS. */
"use strict";

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const DEFAULT_VIDEO = [
  { id: "best", label: "Best available", sub: "highest stream · merges to MP4", tag: "auto" },
  { id: "2160", label: "2160p", sub: "4K ceiling" },
  { id: "1440", label: "1440p", sub: "QHD ceiling" },
  { id: "1080", label: "1080p", sub: "Full HD ceiling" },
  { id: "720", label: "720p", sub: "HD ceiling" },
  { id: "480", label: "480p", sub: "small file" },
  { id: "360", label: "360p", sub: "smallest" },
];
const DEFAULT_AUDIO = [
  { id: "audio_mp3", label: "MP3", sub: "192 kbps · widest support", tag: "audio" },
  { id: "audio_m4a", label: "M4A", sub: "192 kbps · Apple-friendly", tag: "audio" },
];

let fetchedQualities = null; // raw ladder from /api/info (video type)
let fetchToken = 0;
let pollTimer = null;
let queueTimer = null;
let allFiles = [];
let activeJobId = null;

const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------- payload ---------------- */
function outputType() {
  return document.querySelector('input[name="otype"]:checked')?.value || "video";
}
function selectedQuality() {
  return document.querySelector('input[name="q"]:checked')?.value || (outputType() === "audio" ? "audio_mp3" : "best");
}
function cookiesMode() {
  return document.querySelector('input[name="cmode"]:checked')?.value || "none";
}
function payload() {
  const pl = document.querySelector('input[name="plmode"]:checked');
    return {
      url: $("url").value.trim(),
      quality: selectedQuality(),
      subtitles: $("subs").checked,
      auto_subs: true,
      sub_lang: ($("subLang").value.trim() || "en").slice(0, 20),
    clip_from: $("clipFrom").value.trim(),
    clip_to: $("clipTo").value.trim(),
    playlist_mode: pl ? pl.value : "single",
    playlist_start: $("plStart").value.trim(),
    playlist_end: $("plEnd").value.trim(),
    playlist_items: $("plItems").value.trim(),
    cookies_mode: cookiesMode(),
    cookies_text: $("cookieText") ? $("cookieText").value : "",
    cookies_browser: $("cookieBrowserName") ? $("cookieBrowserName").value : "chrome",
  };
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/* ---------------- quality ladder ---------------- */
function videoOptions() {
  if (fetchedQualities && fetchedQualities.length) {
    return fetchedQualities
      .filter((q) => q.id === "best" || /^\d{3,4}$/.test(q.id))
      .map((q) => ({
        id: q.id,
        label: q.id === "best" ? "Best available" : `${q.id}p`,
        sub:
          q.id === "best"
            ? "highest detected stream"
            : q.filesize_str
              ? `~${q.filesize_str}`
              : "ceiling",
        tag: q.id === "best" ? "auto" : undefined,
      }));
  }
  return DEFAULT_VIDEO;
}
function audioOptions() {
  if (fetchedQualities && fetchedQualities.length) {
    const aud = fetchedQualities.filter((q) => String(q.id).startsWith("audio"));
    if (aud.length) {
      return aud.map((q) => ({
        id: q.id,
        label: q.id === "audio_mp3" ? "MP3" : q.id === "audio_m4a" ? "M4A" : q.label,
        sub: q.id === "audio_mp3" ? "192 kbps · widest support" : "192 kbps · Apple-friendly",
        tag: "audio",
      }));
    }
  }
  return DEFAULT_AUDIO;
}

function renderQualities() {
  const box = $("qualities");
  const keep = selectedQuality();
  const list = outputType() === "audio" ? audioOptions() : videoOptions();
  const ids = new Set(list.map((q) => q.id));
  const sel = ids.has(keep) ? keep : list[0].id;
  box.innerHTML = list
    .map(
      (q) => `<label class="q-opt">
        <input type="radio" name="q" value="${escapeHtml(q.id)}"${q.id === sel ? " checked" : ""} />
        <span class="q-row">
          <span class="q-radio" aria-hidden="true"></span>
          <span class="q-name">${escapeHtml(q.label)}</span>
          ${q.tag ? `<span class="q-tag">${escapeHtml(q.tag)}</span>` : ""}
          <span class="q-sub">${escapeHtml(q.sub || "")}</span>
        </span>
      </label>`
    )
    .join("");
  $("qualityNote").textContent =
    outputType() === "audio"
      ? "Audio is extracted with ffmpeg after download."
      : fetchedQualities
        ? "Ceilings come from the inspected video — unavailable heights fall back gracefully."
        : "“Best” keeps the highest available stream and merges to MP4.";
}

$$('input[name="otype"]').forEach((r) => r.addEventListener("change", renderQualities));

/* ---------------- option states ---------------- */
function syncStates() {
  const trimOn = $("clipFrom").value.trim() !== "" || $("clipTo").value.trim() !== "";
  $("trimState").textContent = trimOn ? "on" : "off";
  $("trimState").classList.toggle("is-on", trimOn);

  const isPl = document.querySelector('input[name="plmode"]:checked')?.value === "playlist";
  $("plState").textContent = isPl ? "on" : "off";
  $("plState").classList.toggle("is-on", isPl);
  const bits = [];
  if (isPl) {
    const s = $("plStart").value.trim(), e = $("plEnd").value.trim(), it = $("plItems").value.trim();
    bits.push(it ? `items ${it}` : s || e ? `#${s || "1"}–${e || "end"}` : "whole playlist");
  }
  $("plSummary").textContent = isPl ? bits.join("") || "playlist" : "single video";
  $("plRangeRow").style.opacity = isPl ? "1" : "0.45";

  const cm = cookiesMode();
  $("accessState").textContent = cm === "none" ? "off" : "on";
  $("accessState").classList.toggle("is-on", cm !== "none");
  $("accessSummary").textContent =
    cm === "none" ? "public — no login" : cm === "upload" ? "cookies file" : `browser — ${$("cookieBrowserName").value}`;
  $("cookieNone").classList.toggle("hidden", cm !== "none");
  $("cookieUpload").classList.toggle("hidden", cm !== "upload");
  $("cookieBrowser").classList.toggle("hidden", cm !== "browser");
}
["clipFrom", "clipTo", "plStart", "plEnd", "plItems"].forEach((id) => $(id).addEventListener("input", syncStates));
$$('input[name="plmode"]').forEach((r) => r.addEventListener("change", syncStates));
$$('input[name="cmode"]').forEach((r) => r.addEventListener("change", syncStates));
$("cookieBrowserName").addEventListener("change", syncStates);

$("clearClip").addEventListener("click", () => {
  $("clipFrom").value = "";
  $("clipTo").value = "";
  $("trimError").classList.add("hidden");
  syncStates();
  $("clipFrom").focus();
});

$("cookieFile").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("cookieText").value = await f.text();
  $("cookieFileName").textContent = `${f.name} · ${(f.size / 1024).toFixed(1)} KB`;
});

/* ---------------- inspect ---------------- */
function looksLikeUrl(v) {
  return /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\//i.test(v.trim());
}

let debounce = null;
$("url").addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    if (looksLikeUrl($("url").value)) fetchInfo();
  }, 700);
});
$("url").addEventListener("paste", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => fetchInfo(), 250);
});
$("url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); fetchInfo(); }
});
$("fetchBtn").addEventListener("click", fetchInfo);

function previewLoading() {
  const box = $("infoBox");
  box.classList.remove("is-empty");
  box.innerHTML = `<div class="preview-loading" aria-label="Inspecting link">
    <div class="thumb-skel"></div>
    <div class="lines-skel"><i style="width:82%"></i><i style="width:60%"></i><i></i></div>
  </div>`;
}

async function fetchInfo() {
  const url = $("url").value.trim();
  const err = $("infoError");
  err.classList.add("hidden");
  if (!url) {
    err.textContent = "Paste a YouTube URL first.";
    err.classList.remove("hidden");
    return;
  }
  const my = ++fetchToken;
  const btn = $("fetchBtn");
  btn.disabled = true;
  btn.textContent = "Inspecting…";
  previewLoading();
  try {
    const { info } = await api("/api/info", { method: "POST", body: JSON.stringify(payload()) });
    if (my !== fetchToken) return; // superseded by a newer paste
    renderPreview(info);
  } catch (e) {
    if (my !== fetchToken) return;
    $("infoBox").classList.add("is-empty");
    $("infoBox").innerHTML = `<div class="preview-empty">
      <p class="preview-empty-title">Couldn’t inspect that link</p>
      <p class="preview-empty-sub">Nothing was queued. Fix the address or access method and try again.</p>
    </div>`;
    err.textContent = e.message;
    err.classList.remove("hidden");
  } finally {
    if (my === fetchToken) { btn.disabled = false; btn.textContent = "Inspect"; }
  }
}

function renderPreview(info) {
  const box = $("infoBox");
  box.classList.remove("is-empty");
  if (info.type === "playlist") {
    const total = info.count || (info.entries || []).length;
    const entries = (info.entries || []).slice(0, 8);
    const more = total > entries.length ? `<div class="preview-meta">Showing first ${entries.length} of ${escapeHtml(String(total))} — use Playlist / range to narrow it.</div>` : "";
    box.innerHTML = `<div class="preview-full">
      ${info.thumbnail ? `<img src="${escapeHtml(info.thumbnail)}" alt="" loading="lazy" />` : ""}
      <div style="min-width:0;flex:1">
        <span class="preview-kind">Playlist · ${escapeHtml(String(total))} videos</span>
        <h3>${escapeHtml(info.title || "Playlist")}</h3>
        <div class="preview-meta">${escapeHtml(info.uploader || "")}</div>
        <ul class="preview-entries">
          ${entries.map((e, i) => `<li><span class="n">${String(i + 1).padStart(2, "0")}</span><span class="t">${escapeHtml(e.title || "Untitled")}</span><span class="d">${escapeHtml(e.duration_str || "")}</span></li>`).join("")}
        </ul>
        ${more}
      </div>
    </div>`;
    document.querySelector('input[name="plmode"][value="playlist"]').checked = true;
    $("discPlaylist").open = true;
    fetchedQualities = null;
  } else {
    fetchedQualities = info.qualities || null;
    const quals = (info.qualities || []).map((q) => escapeHtml(q.label)).join(" · ");
    const liveNote = info.is_live
      ? `<div class="preview-meta">Live stream — download records from now on; trim ranges don't apply.</div>`
      : "";
    box.innerHTML = `<div class="preview-full">
      ${info.thumbnail ? `<img src="${escapeHtml(info.thumbnail)}" alt="" loading="lazy" />` : ""}
      <div style="min-width:0;flex:1">
        <span class="preview-kind">Video${info.is_live ? " · live" : ""}</span>
        <h3>${escapeHtml(info.title || "Video")}</h3>
        <div class="preview-meta">${escapeHtml(info.uploader || "")}${info.duration_str ? ` · <span class="mono">${escapeHtml(info.duration_str)}</span>` : ""}</div>
        <div class="preview-meta"><span class="mono">${quals}</span></div>
        ${liveNote}
      </div>
    </div>`;
  }
  renderQualities();
  syncStates();
}

/* ---------------- download ---------------- */
function parseTime(s) {
  s = String(s || "").trim();
  if (!s) return null;
  if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s);
  const parts = s.split(":");
  if (parts.length > 3) return null;
  const nums = parts.map(Number);
  if (nums.some((n) => Number.isNaN(n) || n < 0)) return null;
  return nums.reduce((a, n) => a * 60 + n, 0);
}

$("dlBtn").addEventListener("click", async () => {
  const btn = $("dlBtn");
  const p = payload();
  $("trimError").classList.add("hidden");
  if (!p.url) {
    setDeck("Paste a link first — step 01.", "error");
    $("url").focus();
    return;
  }
  const f = p.clip_from ? parseTime(p.clip_from) : null;
  const t = p.clip_to ? parseTime(p.clip_to) : null;
  if ((p.clip_from && f === null) || (p.clip_to && t === null)) {
    $("trimError").textContent = "Those times don’t parse. Use MM:SS, e.g. 01:30 → 02:45.";
    $("trimError").classList.remove("hidden");
    $("discTrim").open = true;
    return;
  }
  if (f !== null && t !== null && t <= f) {
    $("trimError").textContent = "“To” must be after “From”.";
    $("trimError").classList.remove("hidden");
    $("discTrim").open = true;
    return;
  }
  btn.disabled = true;
  btn.classList.add("is-busy");
  const label = btn.innerHTML;
  setDeck("Starting…", "busy");
  setProgress(0, null);
  try {
    const { job_id } = await api("/api/download", { method: "POST", body: JSON.stringify(p) });
    activeJobId = job_id;
    pollJob(job_id);
    refreshQueue();
  } catch (e) {
    setDeck("Error: " + e.message, "error");
    btn.disabled = false;
    btn.classList.remove("is-busy");
    btn.innerHTML = label;
  }
});

function setDeck(text, state) {
  const line = $("jobStatus");
  line.textContent = text;
  line.classList.toggle("is-error", state === "error");
  line.classList.toggle("is-done", state === "done");
  $("meter").classList.toggle("is-error", state === "error");
  $("meter").classList.toggle("is-done", state === "done");
  $("jobPct").classList.toggle("is-active", state !== "idle");
  if (state === "done") $("dlLede").textContent = "Saved. Grab it from the library.";
  else if (state === "error") $("dlLede").textContent = "That failed — details below.";
  else if (state === "busy") $("dlLede").textContent = "Working — don’t close this tab.";
  else if (state === "cancelled") $("dlLede").textContent = "Ready when you are.";
}

function setProgress(pct, job) {
  $("bar").style.width = `${pct}%`;
  $("meter").setAttribute("aria-valuenow", String(Math.round(pct)));
  $("jobPct").textContent = job && job.total_bytes ? `${pct}%` : pct > 0 ? `${pct}%` : "—";
}

async function pollJob(job_id) {
  clearInterval(pollTimer);
  const btn = $("dlBtn");
  const original = `<svg width="14" height="14" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M6 1v7.2M2.8 5.6 6 8.8l3.2-3.2M2 10.5h8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg> Download`;
  btn.innerHTML = "Working…";
  let inFlight = false;
  pollTimer = setInterval(async () => {
    if (inFlight) return;
    inFlight = true;
    try {
      const job = await api(`/api/job?id=${encodeURIComponent(job_id)}`);
      const pct = job.progress || 0;
      setProgress(pct, job);
      let extra = "";
      if (job.speed) extra += ` · ${(job.speed / 1e6).toFixed(1)} MB/s`;
      if (job.eta) extra += ` · ETA ${job.eta}s`;
      if (job.status === "done") {
        clearInterval(pollTimer);
        btn.disabled = false;
        btn.classList.remove("is-busy");
        btn.innerHTML = original;
        setDeck(`Done — ${(job.files || []).join(", ") || "saved to downloads/"}`, "done");
        $("jobLog").textContent = (job.log || []).slice(-30).join("\n");
        activeJobId = null;
        loadFiles();
        refreshQueue();
      } else if (job.status === "error") {
        clearInterval(pollTimer);
        btn.disabled = false;
        btn.classList.remove("is-busy");
        btn.innerHTML = original;
        setDeck(job.detail || "Download failed.", "error");
        $("jobLog").textContent = (job.log || []).slice(-30).join("\n");
        activeJobId = null;
        refreshQueue();
      } else if (job.status === "cancelled") {
        clearInterval(pollTimer);
        btn.disabled = false;
        btn.classList.remove("is-busy");
        btn.innerHTML = original;
        setDeck("Cancelled.", "cancelled");
        setProgress(0, null);
        activeJobId = null;
        refreshQueue();
      } else {
        setDeck((job.detail || job.status) + extra, "busy");
        $("jobLog").textContent = (job.log || []).slice(-30).join("\n");
        refreshQueue(true);
      }
    } catch {
      setDeck("Lost connection to the server — retrying…", "busy");
    } finally {
      inFlight = false;
    }
  }, 800);
}

/* ---------------- queue ---------------- */
async function refreshQueue(silent) {
  try {
    const { jobs } = await api("/api/jobs");
    const list = (jobs || []).slice(-5).reverse();
    $("queueCount").textContent = String((jobs || []).length);
    if (!list.length) {
      if (!silent) $("queue").innerHTML = `<p class="empty">No downloads yet this session.</p>`;
      return;
    }
    $("queue").innerHTML = list
      .map((j) => {
        const pct = Math.round(j.progress || 0);
        const short = (j.url || "").replace(/^https?:\/\//, "").slice(0, 42);
        const cancel = j.status === "queued"
          ? `<button class="job-cancel" data-cancel="${escapeHtml(j.id)}" type="button">Cancel</button>`
          : "";
        return `<div class="job" data-state="${escapeHtml(j.status)}">
          <div class="job-top"><span class="badge" data-s="${escapeHtml(j.status)}">${escapeHtml(j.status)}</span><span class="job-pct">${pct}%</span></div>
          <div class="job-detail" title="${escapeHtml(j.url || "")}">${escapeHtml(short)}</div>
          <div class="job-bar"><i style="width:${pct}%"></i></div>
          ${cancel}
        </div>`;
      })
      .join("");
  } catch { /* ignore */ }
}

$("queue").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-cancel]");
  if (!btn) return;
  btn.disabled = true;
  try {
    await api("/api/cancel", { method: "POST", body: JSON.stringify({ job_id: btn.dataset.cancel }) });
  } catch (err) {
    setDeck("Cancel failed: " + err.message, "error");
  }
  refreshQueue();
});

/* ---------------- files ---------------- */
let diskFreeStr = "";
function renderFiles() {
  const q = $("fileSearch").value.trim().toLowerCase();
  const box = $("files");
  const list = allFiles.filter((f) => !q || f.name.toLowerCase().includes(q));
  $("footFiles").textContent = allFiles.length
    ? `${allFiles.length} file${allFiles.length === 1 ? "" : "s"} · downloads/${diskFreeStr ? ` · ${diskFreeStr} free` : ""}`
    : "";
  if (!list.length) {
    box.innerHTML = allFiles.length
      ? `<p class="empty">No files match “${escapeHtml(q)}”.</p>`
      : `<p class="empty">Nothing saved yet.</p>`;
    return;
  }
  box.innerHTML = list
    .map((f) => `<div class="file">
      <div class="file-info">
        <span class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        <span class="file-sub">${escapeHtml(f.size_str)} · ${escapeHtml((f.modified || "").replace("T", " "))}</span>
      </div>
      <a class="file-link" href="${escapeHtml(f.url)}" download>Save</a>
      <button class="file-del" data-del="${escapeHtml(f.name)}" type="button" aria-label="Delete ${escapeHtml(f.name)}">Delete</button>
    </div>`)
    .join("");
}

async function loadFiles() {
  try {
    const { files, disk_free_str } = await api("/api/files");
    allFiles = files || [];
    diskFreeStr = disk_free_str || "";
    renderFiles();
  } catch { /* ignore */ }
}
$("refreshFiles").addEventListener("click", loadFiles);
$("fileSearch").addEventListener("input", renderFiles);

$("files").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-del]");
  if (!btn) return;
  const name = btn.dataset.del;
  if (!window.confirm(`Delete “${name}” from downloads/?`)) return;
  btn.disabled = true;
  try {
    await api("/api/files/delete", { method: "POST", body: JSON.stringify({ name }) });
    loadFiles();
  } catch (err) {
    setDeck("Delete failed: " + err.message, "error");
    btn.disabled = false;
  }
});

/* ---------------- health ---------------- */
async function checkHealth() {
  try {
    const { checks } = await api("/api/health");
    const el = $("health");
    const problems = [];
    if (!checks.ffmpeg) problems.push("ffmpeg missing");
    if (checks.yt_dlp === "missing") problems.push("yt-dlp missing");
    if (problems.length) {
      el.className = "status is-bad";
      $("healthText").textContent = problems.join(" · ");
    } else {
      el.className = "status is-ok";
      $("healthText").textContent = `ready · yt-dlp ${checks.yt_dlp} · ffmpeg ok`;
    }
    $("envLine").textContent = `env · yt-dlp ${checks.yt_dlp} · ${(checks.ffmpeg_version || "ffmpeg ?").slice(0, 42)}`;
  } catch {
    $("health").className = "status is-bad";
    $("healthText").textContent = "server unreachable";
  }
}

/* ---------------- init ---------------- */
renderQualities();
syncStates();
checkHealth();
loadFiles();
refreshQueue();
queueTimer = setInterval(() => refreshQueue(true), 5000);
