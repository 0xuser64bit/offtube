/* offtube popup — the instant path: open on a page, the tab's media is
 * already a print, one Keep. Polling lives here while the popup is open;
 * the side panel holds the full controls. MV3, no inline scripts.
 */
'use strict';

const $ = (id) => document.getElementById(id);

let server = OFFTUBE.DEFAULT_SERVER;
let savedBrowser = 'chrome';
let info = null;        // last inspect for the current link
const marks = new Set(); // circled cells on a collection sheet
let jobId = null;
let inspectToken = 0;

const url = () => $('url').value.trim();

function setStage(html) { $('stage').innerHTML = html; }

function setLink(value, { edit = false } = {}) {
  $('url').value = value;
  $('linkText').textContent = value.replace(/^https?:\/\/(www\.)?/, '') || 'No link yet';
  $('linkRow').classList.toggle('hidden', edit);
  $('linkForm').classList.toggle('hidden', !edit);
  $('dlBtn').disabled = !value;
}

function accessPayload() {
  const mode = $('access').value;
  if (mode === 'browser') return { cookies_mode: 'browser', cookies_browser: savedBrowser };
  return { cookies_mode: 'none' };
}

function renderChips(selected) {
  const list = OFFTUBE.formatOptions(info, 'video').concat(
    info && (info.media === 'audio' || info.type === 'gallery') ? [] : OFFTUBE.formatOptions(null, 'audio'),
  );
  const sel = info && info.media === 'audio' && !String(selected).startsWith('audio') ? 'audio_mp3' : selected;
  $('qualities').innerHTML = OFFTUBE.formatsHtml(list, sel, { chips: true });
}
const chosenQuality = () => document.querySelector('input[name="q"]:checked')?.value || 'best';

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

function syncSheet() {
  if (!OFFTUBE.isCollection(info)) { $('sheetTools').classList.add('hidden'); return; }
  const all = `${info.count}${info.more ? '+' : ''}`;
  $('markCount').textContent = marks.size ? `${marks.size} circled — only these are kept` : `Keeping all ${all}`;
  const sc = $('sheetCount');
  if (sc) sc.textContent = marks.size ? `${marks.size} circled` : `All ${all}`;
  $('sheetTools').classList.remove('hidden');
  $('dlLabel').textContent = marks.size ? `Keep ${marks.size}` : `Keep all ${all}`;
}

async function inspect() {
  const u = url();
  OFFTUBE.showProblem('');
  if (!u) return;
  const my = ++inspectToken;
  info = null;
  marks.clear();
  document.body.dataset.kind = '';
  document.body.dataset.media = '';
  setStage(OFFTUBE.exposingHtml(u.replace(/^https?:\/\/(www\.)?/, '').split('/')[0]));
  try {
    const { info: got } = await OFFTUBE.api(server, '/api/info', { url: u, ...accessPayload() });
    if (my !== inspectToken) return;
    info = got;
    document.body.dataset.kind = info.type === 'gallery' ? 'gallery' : '';
    document.body.dataset.media = info.media || '';
    setStage(OFFTUBE.printHtml(info, { cells: 8 }));
    renderChips(chosenQuality());
    syncSheet();
    if (!OFFTUBE.isCollection(info)) $('dlLabel').textContent = 'Keep';
  } catch (err) {
    if (my !== inspectToken) return;
    setStage(OFFTUBE.blankHtml('offtube', 'No print', 'Couldn’t read this link.', 'You can still try Keep, or fix the link below.'));
    OFFTUBE.showProblem(err.message);
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

async function pollJob(id) {
  for (;;) {
    await new Promise((r) => { setTimeout(r, 700); });
    if (jobId !== id) return;
    let job;
    try {
      job = await OFFTUBE.apiGet(server, `/api/job?id=${encodeURIComponent(id)}`);
    } catch {
      setDeck('Lost the server — retrying…', 'busy');
      continue;
    }
    const pct = job.progress || 0;
    if (job.status === 'done') {
      const files = job.files || [];
      setDeck(files.length > 1 ? `Kept ${files.length} files.` : `Kept ${files[0] || 'the file'}.`, 'done', 100);
      OFFTUBE.finishPrint('tick', 'Kept');
    } else if (job.status === 'error') {
      setDeck(job.detail || 'That didn’t work.', 'error', pct);
      OFFTUBE.finishPrint('strike', 'Failed');
      if (OFFTUBE.NEEDS_ACCESS.test(job.detail || '')) $('problemFix').classList.remove('hidden');
    } else if (job.status === 'cancelled') {
      setDeck('Cancelled.', 'idle', 0);
      OFFTUBE.finishPrint('', 'Cancelled');
    } else {
      setDeck(job.detail || OFFTUBE.STATE_WORD[job.status] || job.status, 'busy', pct);
      OFFTUBE.developPrint(pct);
      continue;
    }
    jobId = null;
    $('cancelBtn').classList.add('hidden');
    return;
  }
}

async function keep() {
  const u = url();
  if (!u) return;
  OFFTUBE.showProblem('');
  // A pasted cookies file lives in the side panel, never in the popup.
  if ($('access').value === 'upload') {
    await chrome.storage.session.set({ pendingUrl: u });
    await openPanel();
    return;
  }
  const collection = OFFTUBE.isCollection(info);
  const body = {
    url: u,
    quality: chosenQuality(),
    playlist_mode: collection ? 'playlist' : 'single',
    playlist_items: marks.size ? OFFTUBE.ranges(marks) : '',
    ...accessPayload(),
    title: info ? info.title || '' : '',
    thumbnail: info ? info.thumbnail || '' : '',
  };
  $('dlBtn').disabled = true;
  setDeck('Starting…', 'busy', 0);
  try {
    await chrome.storage.local.set({ quality: body.quality, access: $('access').value });
    const { job_id: id } = await OFFTUBE.api(server, '/api/download', body);
    jobId = id;
    $('cancelBtn').classList.remove('hidden');
    OFFTUBE.developPrint(0);
    pollJob(id);
  } catch (err) {
    setDeck(err.message, 'error', 0);
    if (OFFTUBE.NEEDS_ACCESS.test(err.message)) OFFTUBE.showProblem(err.message);
  } finally {
    $('dlBtn').disabled = false;
  }
}

async function openPanel() {
  const win = await chrome.windows.getLastFocused();
  if (win) await chrome.sidePanel.open({ windowId: win.id });
  window.close();
}

document.addEventListener('DOMContentLoaded', async () => {
  try { await chrome.action.setBadgeText({ text: '' }); } catch { /* ignore */ }
  const s = await OFFTUBE.getSettings();
  server = s.serverUrl;
  savedBrowser = s.cookiesBrowser;
  $('access').value = s.access;
  $('accessBrowser').textContent = `Use ${savedBrowser[0].toUpperCase()}${savedBrowser.slice(1)}’s cookies`;
  renderChips(s.quality);
  OFFTUBE.wireLoupe();

  if (!(await OFFTUBE.showHealth(server))) {
    setStage(OFFTUBE.blankHtml('offtube', 'Server off', 'offtube isn’t running.', 'Start it with ./run.sh, then open this again.'));
    setLink('', { edit: false });
    $('linkRow').classList.add('hidden');
    return;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabUrl = tab?.url || '';
  let source = null;
  try { source = await OFFTUBE.sourceOf(server, tabUrl); } catch { /* handled by health */ }
  if (!source) {
    const host = tabUrl.replace(/^\w+:\/\/(www\.)?/, '').split('/')[0];
    setStage(OFFTUBE.blankHtml(host || 'this tab', 'No print', 'Nothing to keep on this tab.', 'Open a video, post, pin or track — or paste a link below.'));
    setLink('', { edit: true });
    $('url').focus();
    return;
  }
  setLink(tabUrl);
  inspect();
});

$('dlBtn').addEventListener('click', keep);
$('cancelBtn').addEventListener('click', async () => {
  if (!jobId) return;
  try { await OFFTUBE.api(server, '/api/cancel', { job_id: jobId }); setDeck('Stopping…', 'busy'); } catch (err) { setDeck(err.message, 'error'); }
});
$('qualities').addEventListener('change', () => chrome.storage.local.set({ quality: chosenQuality() }));
$('access').addEventListener('change', async () => {
  await chrome.storage.local.set({ access: $('access').value });
  if ($('access').value !== 'upload' && url()) inspect();
});
$('problemFix').addEventListener('click', async () => {
  $('access').value = 'browser';
  await chrome.storage.local.set({ access: 'browser' });
  inspect();
});
$('changeLink').addEventListener('click', () => { setLink(url(), { edit: true }); $('url').select(); });
$('linkForm').addEventListener('submit', (e) => {
  e.preventDefault();
  setLink(url(), { edit: true });
  $('dlBtn').disabled = !url();
  inspect();
});
$('url').addEventListener('input', () => { $('dlBtn').disabled = !url(); });
$('pickInPanel').addEventListener('click', async () => {
  await chrome.storage.session.set({ pendingUrl: url() });
  await openPanel();
});
$('openPanel').addEventListener('click', openPanel);
$('openLib').addEventListener('click', () => chrome.tabs.create({ url: `${server}/` }));
