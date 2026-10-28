/* Shared helpers for popup, side panel, and service worker.
 * Classic script (not a module) so importScripts() works in the SW; DOM
 * helpers only touch `document` when called from a page.
 *
 * The contact-sheet world, same as the web app: a link becomes a print in
 * a black film rebate edge-printed with site and kind; crimson pencil marks
 * mean circled (chosen), ticked (kept), struck (failed); safelight amber
 * only while something develops.
 */
'use strict';

const OFFTUBE = {
  DEFAULT_SERVER: 'http://127.0.0.1:8000',
  MEDIA_WORD: { image: 'Image', video: 'Video', audio: 'Audio', mixed: 'Post' },
  ACTIVE: ['queued', 'starting', 'downloading', 'processing'],
  STATE_WORD: {
    queued: 'Waiting', starting: 'Starting', downloading: 'Developing', processing: 'Fixing',
    done: 'Kept', error: 'Failed', cancelled: 'Cancelled',
  },
  // Errors that mean "give the site a signed-in session".
  NEEDS_ACCESS: /not a bot|sign in|signed-in|needs to be reloaded|cookies|membership|login/i,
  // Jobs live in the server's memory: one it no longer knows stopped with it.
  RESTARTED: 'offtube restarted, so this keep stopped. Keep it again.',
  // Grease-pencil strokes in a 100×100 box, stretched over what they mark.
  MARKS: {
    circle: 'M20 28C34 9 76 6 91 27C103 44 92 75 60 89C33 99 5 84 6 57C7 37 23 21 47 15C55 13 62 13 69 15',
    tick: 'M12 56C21 62 29 71 37 84C49 59 66 33 91 10',
    strike: 'M14 18C38 40 62 62 88 84M86 16C62 40 38 62 14 86',
  },
  VIDEO_LADDER: ['best', '2160', '1440', '1080', '720', '480', '360'],
};

OFFTUBE.escapeHtml = function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
};
const esc = OFFTUBE.escapeHtml;
const pad2 = (n) => String(n).padStart(2, '0');

OFFTUBE.isWebUrl = function isWebUrl(v) {
  return /^https?:\/\//i.test(String(v || '').trim());
};

/* ---------------- server ---------------- */

OFFTUBE.normalizeServerUrl = function normalizeServerUrl(raw) {
  const v = String(raw || '').trim() || OFFTUBE.DEFAULT_SERVER;
  let u;
  try {
    u = new URL(v);
  } catch {
    throw new Error('Server URL is invalid. Use http://127.0.0.1:8000');
  }
  if (u.protocol !== 'http:') {
    throw new Error('Server must be http://127.0.0.1 (the local offtube process).');
  }
  if (u.username || u.password) {
    throw new Error('Server URL must not include credentials.');
  }
  const host = u.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (!['127.0.0.1', 'localhost', '::1'].includes(host)) {
    throw new Error('Server must be on this machine (127.0.0.1 or localhost).');
  }
  const port = u.port ? `:${u.port}` : '';
  // URL serializes IPv6 with brackets; hostname for ::1 is ::1.
  const hostPart = host === '::1' ? '[::1]' : u.hostname;
  return `http://${hostPart}${port}`;
};

OFFTUBE.api = async function api(server, path, body) {
  const res = await fetch(server + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status})`), { status: res.status });
  return data;
};

OFFTUBE.apiGet = async function apiGet(server, path) {
  const res = await fetch(server + path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status})`), { status: res.status });
  return data;
};

/* Which site is this URL on, if offtube supports it? The server matches it
 * against yt-dlp + gallery-dl extractors locally (no network), so asking on
 * every tab is cheap. null = unsupported; throws if the server is down. */
OFFTUBE.sourceOf = async function sourceOf(server, url) {
  if (!OFFTUBE.isWebUrl(url)) return null;
  const r = await OFFTUBE.apiGet(server, `/api/source?url=${encodeURIComponent(url)}`);
  return r.supported ? r.source : null;
};

OFFTUBE.getSettings = async function getSettings() {
  const s = await chrome.storage.local.get(['serverUrl', 'quality', 'otype', 'access', 'cookies_browser', 'gif']);
  let serverUrl = OFFTUBE.DEFAULT_SERVER;
  try {
    serverUrl = OFFTUBE.normalizeServerUrl(s.serverUrl);
  } catch {
    serverUrl = OFFTUBE.DEFAULT_SERVER;
  }
  return {
    serverUrl,
    quality: s.quality || '1080',
    otype: s.otype || 'video',
    access: s.access || 'none',
    cookiesBrowser: s.cookies_browser || 'chrome',
    gif: s.gif || {},
  };
};

OFFTUBE.healthProblems = function healthProblems(checks) {
  const problems = [];
  if (!checks?.ffmpeg) problems.push('ffmpeg missing');
  if (checks?.yt_dlp === 'missing') problems.push('yt-dlp missing');
  if (!checks?.js_runtime_ok) problems.push('no JS runtime');
  if (checks?.gallery_dl === 'missing') problems.push('gallery-dl missing');
  return problems;
};

/* Resolves true when the server answered. #health / #healthText. */
OFFTUBE.showHealth = async function showHealth(server) {
  const badge = document.getElementById('health');
  const text = document.getElementById('healthText');
  try {
    const { checks } = await OFFTUBE.apiGet(server, '/api/health');
    const problems = OFFTUBE.healthProblems(checks);
    badge.className = problems.length ? 'health is-bad' : 'health is-ok';
    text.textContent = problems.length ? problems.join(' · ') : 'Ready';
    badge.title = `yt-dlp ${checks.yt_dlp} · gallery-dl ${checks.gallery_dl || '?'}`;
    return true;
  } catch {
    badge.className = 'health is-bad';
    text.textContent = 'Server off';
    badge.title = 'Start offtube with ./run.sh';
    return false;
  }
};

/* ---------------- the contact sheet ---------------- */

OFFTUBE.markSvg = function markSvg(kind) {
  return `<svg class="mark mark-${kind}" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${OFFTUBE.MARKS[kind]}"/></svg>`;
};

// 1,2,3,5,7,8 → "1-3,5,7-8"
OFFTUBE.ranges = function ranges(nums) {
  const s = [...nums].sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i < s.length; i++) {
    let j = i;
    while (j + 1 < s.length && s[j + 1] === s[j] + 1) j++;
    out.push(j > i ? `${s[i]}-${s[j]}` : String(s[i]));
    i = j;
  }
  return out.join(',');
};

OFFTUBE.isCollection = function isCollection(info) {
  return Boolean(info) && (info.type === 'playlist' || (info.type === 'gallery' && info.count > 1));
};

OFFTUBE.developHtml = function developHtml(src) {
  return `<span class="develop"><img class="pos" src="${esc(src)}" alt="" />`
    + `<img class="neg" src="${esc(src)}" alt="" /><span class="mark-slot"></span></span>`;
};

/* An empty print: the rebate with a message where the image would be. */
OFFTUBE.blankHtml = function blankHtml(top, right, message, sub) {
  return `<figure class="print is-blank">
    <div class="rebate">
      <div class="edge-row"><span class="edge">${esc(top)}</span><span class="edge edge-dim">${esc(right)}</span></div>
      <div class="frame"><div class="frame-empty"><p class="blank-title">${esc(message)}</p>${sub ? `<p class="blank-sub">${esc(sub)}</p>` : ''}</div></div>
      <div class="edge-row"><span class="edge edge-dim">00</span><span class="edge edge-dim">offtube</span></div>
    </div>
  </figure>`;
};

OFFTUBE.exposingHtml = function exposingHtml(label) {
  return `<figure class="print is-exposing">
    <div class="rebate">
      <div class="edge-row"><span class="edge">${esc(label)}</span><span class="edge edge-amber">Exposing…</span></div>
      <div class="frame"><div class="frame-empty"><span class="edge edge-dim">Reading the link</span></div></div>
      <div class="edge-row"><span class="edge edge-dim">01</span><span class="edge edge-dim">offtube</span></div>
    </div>
  </figure>`;
};

// A cell is named by its visible text ("03 · Video"), which speech input
// users say aloud, so it must stay in the name (WCAG 2.5.3). Where a
// thumbnail stands in for the title, the title follows for screen readers.
const hiddenTitle = (e) => (e.thumbnail && e.title ? `<span class="visually-hidden">, ${esc(e.title)}</span>` : '');

/* The inspected link as a print (one item) or a sheet of circle-able cells
 * (a collection). `cells` caps how many cells a narrow surface shows. */
OFFTUBE.printHtml = function printHtml(info, { cells = 50, circle = true } = {}) {
  const site = (info.source && info.source.label) || 'Link';
  if (OFFTUBE.isCollection(info)) {
    const entries = (info.entries || []).slice(0, cells);
    const count = info.count || entries.length;
    const label = info.type === 'playlist' ? 'Playlist' : 'Collection';
    return `<figure class="print sheet" id="printFig">
      <div class="rebate">
        <div class="edge-row"><span class="edge">${esc(site)} · ${label}</span><span class="edge edge-dim">${esc(count)}${info.more ? '+' : ''} items</span></div>
        <ol class="contact">${entries.map((e, i) => `<li>
          <${circle ? 'button type="button"' : 'span'} class="cell"${circle ? ` aria-pressed="false" data-i="${i + 1}"` : ''}>
            <span class="cell-frame"><span class="cell-img">${e.thumbnail ? `<img src="${esc(e.thumbnail)}" alt="" loading="lazy" />` : `<span class="edge">${esc(e.title || `Item ${i + 1}`)}</span>`}</span>${circle ? OFFTUBE.markSvg('circle') : ''}</span>
            <span class="edge edge-dim cell-no">${pad2(i + 1)}${e.is_video ? ' · Video' : ''}</span>${hiddenTitle(e)}
          </${circle ? 'button' : 'span'}></li>`).join('')}</ol>
        <div class="edge-row"><span class="edge edge-dim">${circle ? 'Circle prints to keep only those' : `${esc(site)}`}</span><span class="edge" id="sheetCount">All ${esc(count)}${info.more ? '+' : ''}</span></div>
      </div>
      <figcaption class="print-cap">
        <h2 class="print-title">${esc(info.title || `${site} ${label.toLowerCase()}`)}</h2>
        ${info.uploader ? `<p class="print-meta">${esc(info.uploader)}</p>` : ''}
      </figcaption>
    </figure>`;
  }
  const kind = OFFTUBE.MEDIA_WORD[info.media] || (info.type === 'gallery' ? 'Post' : 'Video');
  const dur = info.duration_str && info.duration_str !== '-' ? info.duration_str : '';
  const top = (info.qualities || []).find((q) => /^\d+$/.test(q.id));
  const bottom = info.type === 'gallery' ? 'Original file' : top ? `Up to ${top.id}p` : info.media === 'audio' ? 'Audio' : '';
  const meta = [info.uploader, dur].filter(Boolean).map(esc).join(' · ');
  const note = info.is_live ? 'Live stream: keeping records from now on.'
    : info.part_of_playlist ? 'Part of a playlist — only this one is kept.' : '';
  return `<figure class="print" id="printFig">
    <div class="rebate">
      <div class="edge-row"><span class="edge">${esc(site)} · ${esc(kind)}</span><span class="edge edge-dim">${esc(info.is_live ? 'Live' : dur)}</span></div>
      <div class="frame">${info.thumbnail ? OFFTUBE.developHtml(info.thumbnail) : `<div class="frame-empty"><span class="edge">${esc(site)}</span><span class="mark-slot"></span></div>`}</div>
      <div class="edge-row"><span class="edge edge-dim">01A</span><span class="edge" id="printEdge">${esc(bottom)}</span></div>
    </div>
    <figcaption class="print-cap">
      <h2 class="print-title">${esc(info.title || 'Untitled')}</h2>
      ${meta ? `<p class="print-meta">${meta}</p>` : ''}
      ${note ? `<p class="print-note">${esc(note)}</p>` : ''}
    </figcaption>
  </figure>`;
};

/* What a session frame shows. A job started without inspect (or a gallery
 * post) has no thumbnail or title of its own: once kept, fall back to the
 * saved file itself, and name it by site and kind, never by a raw URL. */
OFFTUBE.jobDisplay = function jobDisplay(job, server) {
  const file = (job.files || [])[0] || '';
  const ext = (file.split('.').pop() || '').toLowerCase();
  const kind = /^(jpe?g|png|gif|webp|avif|heic)$/.test(ext) ? 'image'
    : /^(mp4|webm|mkv|mov|m4v)$/.test(ext) ? 'video' : /^(mp3|m4a|opus|ogg|flac|wav)$/.test(ext) ? 'audio' : '';
  const fileUrl = file ? `${server}/files/${encodeURIComponent(file)}` : '';
  const tail = ((job.url || '').replace(/[?#].*$/, '').split('/').filter(Boolean).pop() || '').slice(0, 24);
  const word = { image: 'image', video: 'video', audio: 'track' }[kind] || 'post';
  return {
    thumb: ext === 'gif' ? fileUrl : job.thumbnail || (kind === 'image' ? fileUrl : ''),
    video: !job.thumbnail && kind === 'video' ? fileUrl : '',
    title: job.title || `${job.source || 'Saved'} ${word}`,
    meta: job.title ? job.source || '' : [job.source, tail].filter(Boolean).join(' · '),
  };
};

OFFTUBE.developPrint = function developPrint(pct) {
  const fig = document.getElementById('printFig');
  if (!fig) return;
  fig.classList.add('is-developing');
  fig.style.setProperty('--p', String(pct / 100));
  const edge = document.getElementById('printEdge');
  if (edge) { edge.textContent = `Developing ${Math.round(pct)}%`; edge.classList.add('edge-amber'); }
};

OFFTUBE.finishPrint = function finishPrint(kind, label) {
  const fig = document.getElementById('printFig');
  if (!fig) return;
  fig.classList.remove('is-developing');
  const slot = fig.querySelector('.mark-slot');
  if (slot) slot.innerHTML = kind ? OFFTUBE.markSvg(kind) : '';
  const edge = document.getElementById('printEdge');
  if (edge) { edge.textContent = label; edge.classList.remove('edge-amber'); }
};

/* Format choices for what was inspected (or the default ladder). */
OFFTUBE.formatOptions = function formatOptions(info, otype) {
  const live = info && (info.is_live || ['is_live', 'is_upcoming'].includes(info.live_status));
  const gif = !info || (!live && info.media !== 'audio' && info.media !== 'image')
    ? [{ id: 'gif', name: 'GIF', sub: 'silent animation' }] : [];
  if (info && info.type === 'gallery') return [{ id: 'best', name: 'Original', sub: 'as posted' }, ...gif];
  const audio = [
    { id: 'audio_mp3', name: 'MP3', sub: '192 kbps · plays anywhere' },
    { id: 'audio_m4a', name: 'M4A', sub: '192 kbps · Apple-friendly' },
  ];
  if (otype === 'audio' || (info && info.media === 'audio')) return audio;
  const ladder = info && info.qualities ? info.qualities.filter((q) => q.id === 'best' || /^\d{3,4}$/.test(q.id)) : [];
  const video = ladder.length
    ? ladder.map((q) => (q.id === 'best' ? { id: 'best', name: 'Best', sub: 'highest stream' } : { id: q.id, name: `${q.id}p`, sub: q.filesize_str ? `~${q.filesize_str}` : '' }))
    : OFFTUBE.VIDEO_LADDER.map((id) => ({ id, name: id === 'best' ? 'Best' : `${id}p`, sub: '' }));
  return [...video, ...gif];
};

OFFTUBE.gifFields = function gifFields() {
  return {
    gif_size: Number(document.getElementById('gifSize').value),
    gif_fps: Number(document.getElementById('gifFps').value),
    gif_loop: document.getElementById('gifLoop').checked,
  };
};

OFFTUBE.parseTime = function parseTime(value) {
  const s = String(value ?? '').trim();
  if (!/^\d+(?::\d+){0,2}(?:\.\d+)?$/.test(s)) return null;
  const n = s.split(':').map(Number).reduce((a, b) => a * 60 + b, 0);
  return Number.isFinite(n) ? n : null;
};

OFFTUBE.trimProblem = function trimProblem(payload, info) {
  const start = OFFTUBE.parseTime(payload.clip_from), end = OFFTUBE.parseTime(payload.clip_to);
  if ((payload.clip_from && start === null) || (payload.clip_to && end === null)) return 'Use seconds or m:ss for the time range, like 1:30 → 1:35.';
  if (end !== null && end <= (start ?? 0)) return '“To” has to come after “From”.';
  if (info?.is_live && (payload.clip_from || payload.clip_to)) return 'Trim needs a finished video. Clear the times, or wait for the live stream to end.';
  if (payload.quality === 'gif' && info?.duration && start >= info.duration) return '“From” is past the end of this video. Choose an earlier time.';
  return '';
};

OFFTUBE.initGifOptions = function initGifOptions(getInfo, getQuality, includeTrim = false) {
  const $ = (id) => document.getElementById(id);
  $('gifOptions').innerHTML = `<fieldset class="gif-fields"><legend class="label">Make a GIF</legend>
    ${includeTrim ? `<div class="pair">
      <label class="field"><span class="label">From</span><input id="clipFrom" class="num" inputmode="decimal" placeholder="0:00" autocomplete="off" /></label>
      <label class="field"><span class="label">To</span><input id="clipTo" class="num" inputmode="decimal" placeholder="End" autocomplete="off" /></label>
    </div>` : ''}
    <div class="pair">
      <label class="field"><span class="label">Size</span><select id="gifSize"><option value="320">320 px · Small</option><option value="480" selected>480 px · Balanced</option><option value="640">640 px · Large</option><option value="960">960 px · Extra large</option></select></label>
      <label class="field"><span class="label">Motion</span><select id="gifFps"><option value="10">10 fps · Compact</option><option value="15" selected>15 fps · Balanced</option><option value="20">20 fps · Smooth</option><option value="25">25 fps · Smoother</option><option value="30">30 fps · Smoothest</option></select></label>
    </div>
    <label class="check"><input type="checkbox" id="gifLoop" checked /><span>Loop forever</span></label>
    <p class="hint">Silent, with optimized colors. Size is the longest edge.</p>
    <p class="hint num" id="gifSummary" role="status"></p>
    <div class="gif-actions"><button class="text-btn" type="button" id="gifFiveSeconds">Use a 5-second clip</button><button class="text-btn" type="button" id="gifWholeVideo">Use whole video</button></div>
  </fieldset>`;
  OFFTUBE.syncGifOptions = () => {
    const gif = getQuality() === 'gif';
    document.body.dataset.output = gif ? 'gif' : '';
    $('gifOptions').classList.toggle('hidden', !gif);
    if (gif && $('discTrim')) $('discTrim').open = true;
    $('dlLabel').textContent = gif ? 'Keep as GIF' : 'Keep';
    const info = getInfo();
    const start = OFFTUBE.parseTime($('clipFrom').value) ?? 0;
    const end = OFFTUBE.parseTime($('clipTo').value) ?? info?.duration;
    const duration = Number.isFinite(end) ? Math.max(0, Math.min(end, info?.duration || end) - start) : null;
    $('gifSummary').textContent = duration !== null
      ? `${Number(duration.toFixed(2))} seconds · about ${Math.ceil(duration * Number($('gifFps').value))} frames${duration > 15 ? ' · Large file likely; try a shorter clip.' : ''}`
      : `${$('clipFrom').value || $('clipTo').value ? 'Selected range' : 'Whole video'} · ${$('gifFps').value} fps · ${$('gifLoop').checked ? 'loops forever' : 'plays once'}${OFFTUBE.isCollection(info) ? '. One GIF per video; images are skipped.' : '. Set From / To for a smaller GIF.'}`;
  };
  ['gifSize', 'gifFps', 'gifLoop'].forEach((id) => $(id).addEventListener('change', () => {
    OFFTUBE.syncGifOptions();
    chrome.storage.local.set({ gif: OFFTUBE.gifFields() });
  }));
  ['clipFrom', 'clipTo'].forEach((id) => $(id).addEventListener('input', OFFTUBE.syncGifOptions));
  $('qualities').addEventListener('change', OFFTUBE.syncGifOptions);
  $('gifFiveSeconds').addEventListener('click', () => {
    const start = OFFTUBE.parseTime($('clipFrom').value) ?? 0, duration = getInfo()?.duration;
    $('clipFrom').value = String(duration && start >= duration ? 0 : start);
    $('clipTo').value = String(Math.min(Number($('clipFrom').value) + 5, duration || Infinity));
    $('clipTo').dispatchEvent(new Event('input', { bubbles: true }));
    $('clipFrom').focus();
  });
  $('gifWholeVideo').addEventListener('click', () => {
    $('clipFrom').value = '';
    $('clipTo').value = '';
    $('clipTo').dispatchEvent(new Event('input', { bubbles: true }));
  });
};

OFFTUBE.restoreGifOptions = function restoreGifOptions(gif) {
  if ([320, 480, 640, 960].includes(gif.gif_size)) document.getElementById('gifSize').value = gif.gif_size;
  if ([10, 15, 20, 25, 30].includes(gif.gif_fps)) document.getElementById('gifFps').value = gif.gif_fps;
  if (typeof gif.gif_loop === 'boolean') document.getElementById('gifLoop').checked = gif.gif_loop;
};

OFFTUBE.showGifResult = function showGifResult(job, server) {
  const result = document.getElementById('gifResult');
  const files = (job.files || []).filter((name) => /\.gif$/i.test(name));
  if (job.quality !== 'gif' || job.status !== 'done' || !files.length) {
    result.classList.add('hidden');
    return;
  }
  if (result.dataset.job !== job.id) {
    result.dataset.job = job.id;
    result.innerHTML = `<img src="${esc(`${server}/files/${encodeURIComponent(files[0])}`)}" alt="Your finished GIF" />`
      + `<div class="gif-actions">${files.map((name, i) => `<a class="text-btn" href="${esc(`${server}/files/${encodeURIComponent(name)}`)}" download target="_blank" rel="noreferrer">Save ${files.length > 1 ? `GIF ${i + 1}` : 'GIF'}</a>`).join('')}</div>`;
  }
  result.classList.remove('hidden');
};

/* Radio rows (side panel) or chips (popup); the chosen one gets circled. */
OFFTUBE.formatsHtml = function formatsHtml(list, selected, { chips = false } = {}) {
  const sel = list.some((q) => q.id === selected) ? selected : list[0].id;
  return list.map((q) => `<label class="${chips ? 'chip' : 'fmt'}">
      <input type="radio" name="q" value="${esc(q.id)}"${q.id === sel ? ' checked' : ''} />
      <span class="fmt-name">${esc(q.name)}${OFFTUBE.markSvg('circle')}</span>
      ${chips ? '' : `<span class="fmt-sub">${esc(q.sub || '')}</span>`}
    </label>`).join('');
};

/* ---------------- the deck and what is spoken ---------------- */

/* One polite announcer per surface (#announce). The deck and the stage
 * redraw on every poll; reading each redraw aloud would drown a screen
 * reader, so only what a link turned out to be, each change of a job's
 * state and failures are spoken. */
OFFTUBE.announce = function announce(text) {
  const el = document.getElementById('announce');
  if (el) el.textContent = text;
};

OFFTUBE.SPOKEN = { queued: 'Waiting to start.', starting: 'Starting.', downloading: 'Downloading.', processing: 'Finishing the file.' };
let spokenJob = '';

OFFTUBE.jobSpeech = function jobSpeech(job) {
  if (job.status === 'done') {
    const files = job.files || [];
    return files.length > 1 ? `Kept ${files.length} files.` : `Kept ${files[0] || 'the file'}.`;
  }
  if (job.status === 'cancelled') return 'Cancelled.';
  if (job.status === 'processing' && job.quality === 'gif') return 'Making the GIF.';
  return OFFTUBE.SPOKEN[job.status] || '';
};

/* Speaks a job once per state ('lost' marks a server outage). */
OFFTUBE.speakJob = function speakJob(job) {
  const said = job === 'lost' ? 'lost' : `${job.id}:${job.status}`;
  if (said === spokenJob) return;
  spokenJob = said;
  if (job === 'lost') OFFTUBE.announce('Lost the server — retrying…');
  else if (job.status !== 'error') OFFTUBE.announce(OFFTUBE.jobSpeech(job)); // setDeck speaks every failure
};

OFFTUBE.printSummary = function printSummary(info) {
  const site = (info.source && info.source.label) || 'Link';
  const title = info.title || 'Untitled';
  if (OFFTUBE.isCollection(info)) {
    const count = `${info.count || (info.entries || []).length}${info.more ? '+' : ''}`;
    return `${site} ${info.type === 'playlist' ? 'playlist' : 'collection'} ready, ${count} items: ${title}.`;
  }
  const kind = (OFFTUBE.MEDIA_WORD[info.media] || (info.type === 'gallery' ? 'Post' : 'Video')).toLowerCase();
  return `${site} ${kind} ready: ${title}.`;
};

OFFTUBE.setDeck = function setDeck(text, mode, pct) {
  const status = document.getElementById('jobStatus');
  const meter = document.getElementById('meter');
  status.textContent = text;
  status.parentElement.classList.toggle('is-error', mode === 'error');
  status.parentElement.classList.toggle('is-done', mode === 'done');
  meter.classList.toggle('is-error', mode === 'error');
  meter.classList.toggle('is-done', mode === 'done');
  if (typeof pct === 'number') {
    meter.style.setProperty('--p', String(Math.max(0, Math.min(100, pct)) / 100));
    meter.setAttribute('aria-valuenow', String(Math.round(pct)));
    document.getElementById('jobPct').textContent = mode === 'busy' && pct > 0 ? `${Math.round(pct)}%` : '';
  }
  if (mode === 'error') OFFTUBE.announce(text);
};

/* Problem block: struck mark + message (+ optional fix button). */
OFFTUBE.showProblem = function showProblem(msg) {
  const box = document.getElementById('problem');
  if (!box) return;
  if (!msg) { box.classList.add('hidden'); return; }
  document.getElementById('problemText').textContent = msg;
  const fix = document.getElementById('problemFix');
  if (fix) fix.classList.toggle('hidden', !OFFTUBE.NEEDS_ACCESS.test(msg));
  const mark = box.querySelector('.mark');
  if (mark) mark.replaceWith(mark.cloneNode(true)); // redraw
  box.classList.remove('hidden');
};

/* The loupe: what a contact sheet is read with. Hover a print, a sheet
 * frame or a kept image (mouse only) and a lens holds over it at 2.5x,
 * following the pointer; it never takes the click. */
OFFTUBE.wireLoupe = function wireLoupe(zoom = 2.5) {
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches) return;
  const lens = document.createElement('div');
  lens.className = 'loupe';
  lens.setAttribute('aria-hidden', 'true');
  document.body.append(lens);
  const TARGET = '.cell-img img, .develop img, .kept-thumb img';
  let img = null, timer = null, frame = 0, last = null;
  const hide = () => { clearTimeout(timer); img = null; lens.classList.remove('is-on'); };
  const place = () => {
    frame = 0;
    if (!img || !last || !img.naturalWidth) return;
    const r = img.getBoundingClientRect();
    const fit = getComputedStyle(img).objectFit;
    const scale = fit === 'cover' ? Math.max(r.width / img.naturalWidth, r.height / img.naturalHeight)
      : fit === 'contain' ? Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight)
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
  document.addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse') return;
    const host = e.target.closest('.cell-img, .develop, .kept-thumb');
    const target = host && host.querySelector('img.pos, .cell-img > img, .kept-thumb > img');
    if (!target || !target.matches(TARGET)) { hide(); return; }
    last = e;
    if (target !== img) {
      hide();
      img = target;
      timer = setTimeout(() => { place(); lens.classList.add('is-on'); }, 220);
      return;
    }
    if (!frame) frame = requestAnimationFrame(place);
  }, { passive: true });
  document.documentElement.addEventListener('mouseleave', hide);
  window.addEventListener('scroll', hide, { passive: true });
};

OFFTUBE.wireCookieFile = function wireCookieFile() {
  document.getElementById('cookieFile').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    document.getElementById('cookieText').value = await f.text();
    document.getElementById('cookieFileName').textContent = `${f.name} · ${(f.size / 1024).toFixed(1)} KB`;
  });
};
