/* offtube side panel — the full desk, docked: follows the tab you're on,
 * prints it, every option, the session strip and recent kept files.
 * Polling lives here (service workers are ephemeral).
 */
'use strict';

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const checked = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;

let server = OFFTUBE.DEFAULT_SERVER;
let info = null;
let infoUrl = '';
const marks = new Set();
let lastTabUrl = '';
let activeJobId = null;
let printJobId = null;
let resultJobId = null;
let pollTimer = null;
let inspectToken = 0;

const url = () => $('url').value.trim();
const access = () => checked('access') || 'none';
const quality = () => checked('q') || 'best';
const current = () => (info && infoUrl === url() ? info : null);
OFFTUBE.initGifOptions(current, quality);

/* ---------------- payload ---------------- */

function accessFields() {
  const mode = access();
  if (mode === 'upload') return { cookies_mode: 'upload', cookies_text: $('cookieText').value };
  if (mode === 'browser') return { cookies_mode: 'browser', cookies_browser: $('cookieBrowserName').value };
  return { cookies_mode: 'none' };
}

function scopeFields() {
  if (marks.size) return { playlist_mode: 'playlist', playlist_items: OFFTUBE.ranges(marks) };
  return {
    playlist_mode: checked('plmode') || 'single',
    playlist_start: $('plStart').value.trim(),
    playlist_end: $('plEnd').value.trim(),
    playlist_items: $('plItems').value.trim(),
  };
}

function payload() {
  const i = current();
  return {
    url: url(),
    quality: quality(),
    subtitles: quality() !== 'gif' && $('subs').checked,
    ...(quality() === 'gif' ? OFFTUBE.gifFields() : {}),
    auto_subs: true,
    sub_lang: ($('subLang').value.trim() || 'en').slice(0, 20),
    clip_from: $('clipFrom').value.trim(),
    clip_to: $('clipTo').value.trim(),
    ...scopeFields(),
    ...accessFields(),
    title: i ? i.title || '' : '',
    thumbnail: i ? i.thumbnail || '' : '',
  };
}

/* ---------------- formats + notes ---------------- */

// Format chips, as in the popup: video heights, GIF, then audio.
function renderFormats(selected) {
  const i = current();
  const list = OFFTUBE.formatOptions(i, 'video').concat(
    i && (i.media === 'audio' || i.type === 'gallery') ? [] : OFFTUBE.formatOptions(null, 'audio'),
  );
  let sel = selected || quality();
  if (i && i.media === 'audio' && !String(sel).startsWith('audio')) sel = 'audio_mp3';
  $('qualities').innerHTML = OFFTUBE.formatsHtml(list, sel, { chips: true });
  OFFTUBE.syncGifOptions();
  syncTally();
}

function invalidateSource() {
  inspectToken++;
  info = null;
  infoUrl = '';
  marks.clear();
  printJobId = null;
  resultJobId = null;
  $('gifResult').classList.add('hidden');
  document.body.dataset.kind = '';
  document.body.dataset.media = '';
}

function setLink(value, { edit = false, label = '' } = {}) {
  if (value !== url()) invalidateSource();
  $('url').value = value;
  $('linkText').textContent = label || value.replace(/^https?:\/\/(www\.)?/, '') || 'No link yet';
  $('linkRow').classList.toggle('hidden', edit);
  $('linkForm').classList.toggle('hidden', !edit);
}

function setNote(id, text, on) {
  $(id).textContent = text;
  $(id).classList.toggle('is-on', on);
}

function syncNotes() {
  const from = $('clipFrom').value.trim(), to = $('clipTo').value.trim();
  setNote('trimState', from || to ? `${from || 'start'} → ${to || 'end'}` : 'Whole video', Boolean(from || to));
  const mode = checked('plmode');
  let pl = 'This item';
  if (marks.size) pl = `${marks.size} circled`;
  else if (mode === 'playlist') {
    const s = $('plStart').value.trim(), e = $('plEnd').value.trim(), it = $('plItems').value.trim();
    pl = it ? `Items ${it}` : s || e ? `#${s || 1}–${e || 'last'}` : 'Whole collection';
  }
  setNote('plState', pl, Boolean(marks.size) || mode === 'playlist');
  // A range only applies to the whole collection; "This item" ignores it.
  const ranged = mode === 'playlist';
  for (const id of ['plStart', 'plEnd', 'plItems']) $(id).disabled = !ranged;
  $('plRangeRow').classList.toggle('is-off', !ranged);
  $('plItemsField').classList.toggle('is-off', !ranged);
  setNote('subsState', $('subs').checked ? ($('subLang').value.trim() || 'en') : 'Off', $('subs').checked);
  const a = access();
  const browserName = $('cookieBrowserName').selectedOptions[0]?.textContent || 'Browser';
  setNote('accessState', a === 'browser' ? `${browserName} cookies` : a === 'upload' ? 'Cookies file' : 'Not needed', a !== 'none');
  $('cookieNone').classList.toggle('hidden', a !== 'none');
  $('cookieBrowserPane').classList.toggle('hidden', a !== 'browser');
  $('cookieUpload').classList.toggle('hidden', a !== 'upload');
  syncTally();
}

function syncTally() {
  const i = current();
  if (!i) { $('tally').textContent = ''; return; }
  let what;
  const perVideo = quality() === 'gif' ? ' · one GIF per video' : '';
  if (marks.size) what = `<b>${marks.size} circled</b>${perVideo}`;
  else if (OFFTUBE.isCollection(i) && checked('plmode') === 'playlist') what = `<b>${esc(i.count)}${i.more ? '+' : ''} items</b>${perVideo}`;
  else if (i.type === 'gallery') what = quality() === 'gif' ? '<b>1 GIF per video</b> · images are skipped' : '<b>1 file</b> · kept as posted';
  else {
    const q = (i.qualities || []).find((x) => x.id === quality());
    what = `<b>1 ${quality() === 'gif' ? 'GIF' : quality().startsWith('audio') ? 'audio file' : 'video'}</b>${q && q.filesize_str ? ` · ~${esc(q.filesize_str)}` : ''}`;
  }
  $('tally').innerHTML = what;
}

function syncSheet() {
  const i = current();
  if (!OFFTUBE.isCollection(i)) { $('sheetTools').classList.add('hidden'); return; }
  const all = `${i.count}${i.more ? '+' : ''}`;
  $('markCount').textContent = marks.size ? `${marks.size} circled — only these are kept` : `Keeping all ${all}`;
  if ($('sheetCount')) $('sheetCount').textContent = marks.size ? `${marks.size} circled` : `All ${all}`;
  $('sheetTools').classList.remove('hidden');
  syncNotes();
}

/* ---------------- source + inspect ---------------- */

// Follow the active tab. A tab with nothing to keep, or a stopped server,
// is said on an empty print; the link field opens for a paste.
async function fillFromTab(force) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabUrl = tab?.url || '';
  const host = tabUrl.replace(/^\w+:\/\/(www\.)?/, '').split('/')[0] || 'this tab';
  const idle = !url() || url() === lastTabUrl;
  let source = null;
  try {
    source = await OFFTUBE.sourceOf(server, tabUrl);
  } catch {
    $('stage').innerHTML = OFFTUBE.blankHtml('offtube', 'Server off', 'offtube isn’t running.', 'Start it with ./run.sh — this panel picks it up.');
    return false;
  }
  if (!source) {
    if (idle || force) {
      info = null;
      document.body.dataset.kind = '';
      $('sheetTools').classList.add('hidden');
      $('stage').innerHTML = OFFTUBE.blankHtml(host, 'No print', 'Nothing to keep on this tab.', 'Open a video, post, pin or track — or paste a link below.');
      setLink('', { edit: true });
      lastTabUrl = '';
    }
    return false;
  }
  if (force || idle) {
    lastTabUrl = tabUrl;
    setLink(tabUrl, { label: `${source.label} · ${tab.title || host}` });
  }
  return true;
}

async function inspect() {
  const u = url();
  OFFTUBE.showProblem('');
  if (!u) return;
  invalidateSource();
  const my = inspectToken;
  $('stage').innerHTML = OFFTUBE.exposingHtml(u.replace(/^https?:\/\/(www\.)?/, '').split('/')[0]);
  $('sheetTools').classList.add('hidden');
  try {
    const { info: got } = await OFFTUBE.api(server, '/api/info', { url: u, ...accessFields() });
    if (my !== inspectToken || u !== url()) return;
    info = got;
    infoUrl = u;
    marks.clear();
    printJobId = null;
    document.body.dataset.kind = info.type === 'gallery' ? 'gallery' : '';
    document.body.dataset.media = info.media || '';
    $('stage').innerHTML = OFFTUBE.printHtml(info);
    setLink(u, { label: `${(info.source && info.source.label) || 'Link'} · ${u.replace(/^https?:\/\/(www\.)?/, '')}` });
    document.querySelector(`input[name="plmode"][value="${OFFTUBE.isCollection(info) ? 'playlist' : 'single'}"]`).checked = true;
    renderFormats();
    syncSheet();
    syncNotes();
  } catch (err) {
    if (my !== inspectToken || u !== url()) return;
    info = null;
    document.body.dataset.kind = '';
    document.body.dataset.media = '';
    $('stage').innerHTML = OFFTUBE.blankHtml('offtube', 'No print', 'Couldn’t read this link.', 'Fix the link, or set up sign-in below.');
    setLink(u, { edit: true });
    OFFTUBE.showProblem(err.message);
    if (OFFTUBE.NEEDS_ACCESS.test(err.message)) $('discAccess').open = true;
    renderFormats();
  }
}

$('stage').addEventListener('click', (e) => {
  const cell = e.target.closest('.cell');
  if (!cell) return;
  const i = Number(cell.dataset.i);
  const on = cell.getAttribute('aria-pressed') !== 'true';
  cell.setAttribute('aria-pressed', String(on));
  if (on) marks.add(i); else marks.delete(i);
  syncSheet();
});
$('markAll').addEventListener('click', () => {
  $$('.cell').forEach((c) => { c.setAttribute('aria-pressed', 'true'); marks.add(Number(c.dataset.i)); });
  syncSheet();
});
$('markNone').addEventListener('click', () => {
  $$('.cell').forEach((c) => c.setAttribute('aria-pressed', 'false'));
  marks.clear();
  syncSheet();
});

/* ---------------- keep + job ---------------- */

function setDeck(text, mode, pct) {
  $('jobStatus').textContent = text;
  const line = $('jobStatus').parentElement;
  line.classList.toggle('is-error', mode === 'error');
  line.classList.toggle('is-done', mode === 'done');
  $('meter').classList.toggle('is-error', mode === 'error');
  $('meter').classList.toggle('is-done', mode === 'done');
  if (typeof pct === 'number') {
    $('meter').style.setProperty('--p', String(Math.max(0, Math.min(100, pct)) / 100));
    $('meter').setAttribute('aria-valuenow', String(Math.round(pct)));
    $('jobPct').textContent = mode === 'busy' && pct > 0 ? `${Math.round(pct)}%` : '';
  }
}

function showJob(job, live) {
  const pct = job.progress || 0;
  $('log').textContent = (job.log || []).slice(-30).join('\n');
  const isPrint = printJobId === job.id;
  if (resultJobId === job.id) OFFTUBE.showGifResult(job, server);
  if (job.status === 'done') {
    const files = job.files || [];
    setDeck(files.length > 1 ? `Kept ${files.length} files.` : `Kept ${files[0] || 'the file'}.`, 'done', 100);
    if (isPrint) OFFTUBE.finishPrint('tick', 'Kept');
  } else if (job.status === 'error') {
    setDeck(job.detail || 'That didn’t work.', 'error', pct);
    if (isPrint) OFFTUBE.finishPrint('strike', 'Failed');
    if (OFFTUBE.NEEDS_ACCESS.test(job.detail || '')) $('discAccess').open = true;
  } else if (job.status === 'cancelled') {
    setDeck('Cancelled.', 'idle', 0);
    if (isPrint) OFFTUBE.finishPrint('', 'Cancelled');
  } else {
    setDeck(job.detail || OFFTUBE.STATE_WORD[job.status] || job.status, 'busy', pct);
    if (isPrint && live) OFFTUBE.developPrint(pct);
  }
  $('cancelBtn').classList.toggle('hidden', !(live && OFFTUBE.ACTIVE.includes(job.status)));
}

function pollJob(id) {
  clearInterval(pollTimer);
  activeJobId = id;
  let inFlight = false;
  pollTimer = setInterval(async () => {
    if (inFlight) return;
    inFlight = true;
    try {
      const job = await OFFTUBE.apiGet(server, `/api/job?id=${encodeURIComponent(id)}`);
      if (activeJobId !== id) return;
      showJob(job, true);
      if (!OFFTUBE.ACTIVE.includes(job.status)) {
        clearInterval(pollTimer);
        activeJobId = null;
        refreshFiles();
        refreshQueue();
      }
    } catch {
      if (activeJobId === id) setDeck('Lost the server — retrying…', 'busy');
    } finally {
      inFlight = false;
    }
  }, 800);
}

$('dlBtn').addEventListener('click', async () => {
  const p = payload();
  OFFTUBE.showProblem('');
  if (!p.url) { OFFTUBE.showProblem('Paste a link first.'); $('url').focus(); return; }
  const bad = OFFTUBE.trimProblem(p, current());
  if (bad) { OFFTUBE.showProblem(bad); $('discTrim').open = true; return; }
  const sourceToken = inspectToken;
  $('dlBtn').disabled = true;
  $('gifResult').classList.add('hidden');
  setDeck('Starting…', 'busy', 0);
  try {
    await chrome.storage.local.set({ quality: quality(), access: access() });
    const { job_id: id } = await OFFTUBE.api(server, '/api/download', p);
    const sameSource = sourceToken === inspectToken && p.url === url();
    printJobId = sameSource && current() ? id : null;
    resultJobId = sameSource ? id : null;
    if (printJobId) OFFTUBE.developPrint(0);
    pollJob(id);
    refreshQueue();
  } catch (err) {
    setDeck(err.message, 'error', 0);
    if (OFFTUBE.NEEDS_ACCESS.test(err.message)) $('discAccess').open = true;
  } finally {
    $('dlBtn').disabled = false;
  }
});

$('cancelBtn').addEventListener('click', async () => {
  if (!activeJobId) return;
  try { await OFFTUBE.api(server, '/api/cancel', { job_id: activeJobId }); setDeck('Stopping…', 'busy'); } catch (err) { setDeck(err.message, 'error'); }
  refreshQueue();
});

/* ---------------- this session: a vertical strip, updated in place ---------------- */

function stripFrame(jid) {
  const li = document.createElement('li');
  li.className = 'strip-frame';
  li.dataset.jid = jid;
  li.tabIndex = 0;
  li.innerHTML = `<div class="frame js-frame"></div>
    <div class="edge-row"><span class="edge edge-dim js-no"></span><span class="edge js-state"></span></div>
    <p class="strip-title js-title"></p>
    <div class="strip-actions"><button type="button" class="text-btn js-cancel">Cancel</button><button type="button" class="text-btn js-retry">Retry</button></div>`;
  return li;
}

function updateFrame(li, job, no) {
  const pct = Math.round(job.progress || 0);
  const active = OFFTUBE.ACTIVE.includes(job.status);
  li.querySelector('.js-no').textContent = String(no).padStart(2, '0');
  const st = li.querySelector('.js-state');
  st.textContent = job.quality === 'gif' && job.status === 'processing' ? `Making GIF ${pct}%` : job.status === 'downloading' && pct ? `Developing ${pct}%` : OFFTUBE.STATE_WORD[job.status] || job.status;
  st.classList.toggle('edge-amber', active);
  const frame = li.querySelector('.js-frame');
  const show = OFFTUBE.jobDisplay(job, server);
  const key = show.thumb || show.video;
  if (frame.dataset.thumb !== key) {
    frame.dataset.thumb = key;
    frame.innerHTML = show.thumb ? OFFTUBE.developHtml(show.thumb)
      : show.video ? `<span class="develop"><video class="pos" src="${esc(show.video)}#t=0.5" preload="metadata" muted playsinline aria-hidden="true"></video><span class="mark-slot"></span></span>`
        : `<div class="frame-empty"><span class="edge">${esc(job.source || 'offtube')}</span><span class="mark-slot"></span></div>`;
    frame.dataset.mark = '';
  }
  li.classList.toggle('is-developing', active);
  li.style.setProperty('--p', String(active ? pct / 100 : 1));
  const kind = job.status === 'done' ? 'tick' : job.status === 'error' ? 'strike' : '';
  if (frame.dataset.mark !== kind) {
    frame.dataset.mark = kind;
    const slot = frame.querySelector('.mark-slot');
    if (slot) slot.innerHTML = kind ? OFFTUBE.markSvg(kind) : '';
  }
  const title = show.title;
  li.querySelector('.js-title').textContent = title;
  li.querySelector('.js-no').textContent = [String(no).padStart(2, '0'), show.meta].filter(Boolean).join(' · ');
  li.setAttribute('aria-label', `${title}: ${st.textContent}`);
  li.querySelector('.js-cancel').classList.toggle('hidden', !active);
  li.querySelector('.js-retry').classList.toggle('hidden', job.status !== 'error' && job.status !== 'cancelled');
}

async function refreshQueue() {
  try {
    const { jobs } = await OFFTUBE.apiGet(server, '/api/jobs?limit=20');
    const all = jobs || [];
    const shown = all.slice(-8).reverse();
    const ol = $('queue');
    const keep = new Set(shown.map((j) => j.id));
    $$('.strip-frame', ol).forEach((li) => { if (!keep.has(li.dataset.jid)) li.remove(); });
    shown.forEach((job, i) => {
      let li = ol.querySelector(`[data-jid="${CSS.escape(job.id)}"]`);
      if (!li) li = stripFrame(job.id);
      updateFrame(li, job, all.indexOf(job) + 1);
      if (ol.children[i] !== li) ol.insertBefore(li, ol.children[i] || null);
    });
    const active = all.filter((j) => OFFTUBE.ACTIVE.includes(j.status)).length;
    $('queueCount').textContent = all.length ? `${active ? `${active} developing · ` : ''}${all.length} print${all.length === 1 ? '' : 's'}` : '';
    $('queueEmpty').classList.toggle('hidden', all.length > 0);
    $('clearQueue').classList.toggle('hidden', !all.some((j) => !OFFTUBE.ACTIVE.includes(j.status)));
  } catch { /* health shows the server is down */ }
}

$('queue').addEventListener('click', async (e) => {
  const li = e.target.closest('.strip-frame');
  if (!li) return;
  const jid = li.dataset.jid;
  if (e.target.closest('.js-cancel')) {
    try { await OFFTUBE.api(server, '/api/cancel', { job_id: jid }); } catch (err) { setDeck(err.message, 'error'); }
  } else if (e.target.closest('.js-retry')) {
    try { const { job_id: id } = await OFFTUBE.api(server, '/api/retry', { job_id: jid }); pollJob(id); } catch (err) { setDeck(err.message, 'error'); }
  } else {
    try {
      const job = await OFFTUBE.apiGet(server, `/api/job?id=${encodeURIComponent(jid)}`);
      const live = OFFTUBE.ACTIVE.includes(job.status);
      resultJobId = jid;
      showJob(job, live);
      if (live) pollJob(jid);
      $('logBox').open = job.status === 'error';
    } catch (err) { setDeck(err.message, 'error'); }
  }
  refreshQueue();
});
$('queue').addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('strip-frame')) { e.preventDefault(); e.target.click(); }
});
$('clearQueue').addEventListener('click', async () => {
  try { await OFFTUBE.api(server, '/api/jobs/clear', {}); } catch (err) { setDeck(err.message, 'error'); }
  refreshQueue();
});

/* ---------------- kept ---------------- */

async function refreshFiles() {
  try {
    const { files, total, disk_free_str: free } = await OFFTUBE.apiGet(server, '/api/files?limit=12');
    const list = (files || []).slice(0, 12);
    const count = total ?? list.length; // a server before paging sent no total
    $('filesCount').textContent = count ? `${count}${free ? ` · ${free} free` : ''}` : '';
    $('filesEmpty').classList.toggle('hidden', list.length > 0);
    $('files').innerHTML = list.map((f, i) => {
      const href = esc(server + f.url);
      const ext = (f.name.split('.').pop() || '').toUpperCase();
      const thumb = f.kind === 'image' ? `<img src="${href}" alt="" loading="lazy" />`
        : f.kind === 'video' ? `<video src="${href}#t=0.5" preload="metadata" muted playsinline aria-hidden="true"></video>`
          : `<span class="edge">${esc(ext || 'File')}</span>`;
      return `<li class="kept-cell"><div class="kept-thumb">${thumb}</div>
        <div class="edge-row"><span class="edge edge-dim">${String(i + 1).padStart(2, '0')}</span><span class="edge">${esc(f.size_str)}</span></div>
        <p class="kept-name"><a href="${href}" target="_blank" rel="noreferrer" title="Save ${esc(f.name)}">${esc(f.name)}</a></p></li>`;
    }).join('');
  } catch { /* ignore */ }
}

/* ---------------- boot + wiring ---------------- */

function restorePendingOptions(options = {}) {
  OFFTUBE.restoreGifOptions(options.gif || {});
  $('clipFrom').value = typeof options.clip_from === 'string' ? options.clip_from : '';
  $('clipTo').value = typeof options.clip_to === 'string' ? options.clip_to : '';
  renderFormats(options.quality);
  syncNotes();
}

document.addEventListener('DOMContentLoaded', async () => {
  try { await chrome.action.setBadgeText({ text: '' }); } catch { /* ignore */ }
  const s = await OFFTUBE.getSettings();
  server = s.serverUrl;
  $('server').value = server;
  $('cookieBrowserName').value = s.cookiesBrowser;
  const pick = (name, v) => { const r = document.querySelector(`input[name="${name}"][value="${v}"]`); if (r) r.checked = true; };
  pick('access', s.access);
  OFFTUBE.restoreGifOptions(s.gif);
  renderFormats(s.quality);
  OFFTUBE.wireLoupe();
  syncNotes();

  await OFFTUBE.showHealth(server);
  const session = await chrome.storage.session.get(['pendingUrl', 'pendingOptions']);
  if (session.pendingUrl) {
    setLink(session.pendingUrl);
    lastTabUrl = session.pendingUrl;
    restorePendingOptions(session.pendingOptions);
    await chrome.storage.session.remove(['pendingUrl', 'pendingOptions']);
  } else {
    await fillFromTab(false);
  }
  if (url()) inspect();
  refreshQueue();
  refreshFiles();
  setInterval(refreshQueue, 4000);
});

async function followTab() {
  const ok = await fillFromTab(false);
  if (ok && url() === lastTabUrl && url() !== infoUrl) inspect();
}
chrome.tabs.onActivated.addListener(followTab);
chrome.tabs.onUpdated.addListener((_id, change, tab) => { if (tab.active && change.url) followTab(); });
chrome.storage.session.onChanged.addListener(async (changes) => {
  if (!changes.pendingUrl || !changes.pendingUrl.newValue) return;
  setLink(changes.pendingUrl.newValue);
  lastTabUrl = changes.pendingUrl.newValue;
  restorePendingOptions(changes.pendingOptions?.newValue);
  await chrome.storage.session.remove(['pendingUrl', 'pendingOptions']);
  inspect();
});

$('linkForm').addEventListener('submit', (e) => { e.preventDefault(); inspect(); });
$('changeLink').addEventListener('click', () => { setLink(url(), { edit: true }); $('url').select(); });
$('url').addEventListener('paste', () => setTimeout(inspect, 150));
$('url').addEventListener('input', () => {
  invalidateSource();
  $('stage').innerHTML = OFFTUBE.blankHtml('offtube', 'Preview needed', 'Preview this link.', 'Choose Preview to read the video or post.');
  renderFormats();
  syncSheet();
});
$('qualities').addEventListener('change', () => { syncTally(); chrome.storage.local.set({ quality: quality() }); });
['clipFrom', 'clipTo', 'plStart', 'plEnd', 'plItems', 'subLang'].forEach((id) => $(id).addEventListener('input', syncNotes));
$$('input[name="plmode"]').forEach((r) => r.addEventListener('change', syncNotes));
$$('input[name="access"]').forEach((r) => r.addEventListener('change', async () => {
  syncNotes();
  await chrome.storage.local.set({ access: access() });
}));
$('subs').addEventListener('change', syncNotes);
$('cookieBrowserName').addEventListener('change', async () => {
  syncNotes();
  await chrome.storage.local.set({ cookies_browser: $('cookieBrowserName').value });
});
OFFTUBE.wireCookieFile();
$('problemFix').addEventListener('click', () => {
  const d = $('discAccess');
  d.open = true;
  d.scrollIntoView({ behavior: 'smooth', block: 'center' });
});
$('openLib').addEventListener('click', () => chrome.tabs.create({ url: `${server}/` }));
$('server').addEventListener('change', async () => {
  try {
    const v = OFFTUBE.normalizeServerUrl($('server').value);
    $('server').value = v;
    server = v;
    await chrome.storage.local.set({ serverUrl: v });
    await OFFTUBE.showHealth(v);
    refreshQueue();
    refreshFiles();
  } catch (err) {
    OFFTUBE.showProblem(err.message);
    $('server').value = server;
  }
});
