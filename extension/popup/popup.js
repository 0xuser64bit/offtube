/* offtube popup — MV3, no inline scripts, async/await only.
 * Reads the active tab URL (needs "tabs" permission — without it tab.url
 * is silently undefined), lets the user override it, talks to the local
 * offtube server over http://127.0.0.1 (see host_permissions in manifest).
 */
'use strict';

const $ = (id) => document.getElementById(id);

const { getSettings, setError } = OFFTUBE;
let savedBrowser = 'chrome';

function accessPayload() {
  const mode = $('access').value;
  if (mode === 'upload') return { cookies_mode: 'upload', cookies_text: $('cookieText').value };
  if (mode === 'browser') return { cookies_mode: 'browser', cookies_browser: savedBrowser };
  return { cookies_mode: 'none' };
}

function syncAccess() {
  $('cookieUpload').classList.toggle('hidden', $('access').value !== 'upload');
}

async function getActiveTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return { url: tab?.url || '', title: tab?.title || '' };
}

function setStatus(msg, pct) {
  $('status').textContent = msg;
  if (typeof pct === 'number') $('bar').style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function renderPreview(info) {
  $('preview').innerHTML = OFFTUBE.previewHtml(info);
  OFFTUBE.fitControls(info);
}

async function inspect() {
  setError('');
  const { serverUrl } = await getSettings();
  const url = $('url').value.trim();
  if (!url) { setError('Paste a link first.'); return; }
  $('inspect').disabled = true;
  try {
    const { info } = await OFFTUBE.api(serverUrl, '/api/info', { url, ...accessPayload() });
    renderPreview(info);
    setStatus(info.type === 'gallery' ? 'Inspected. Files are kept as posted.' : 'Inspected. Pick quality and hit Download.');
  } catch (err) {
    setError(err.message);
  } finally {
    $('inspect').disabled = false;
  }
}

async function pollJob(server, jobId) {
  for (;;) {
    await new Promise((r) => { setTimeout(r, 900); });
    let job;
    try {
      job = await OFFTUBE.apiGet(server, `/api/job?id=${encodeURIComponent(jobId)}`);
    } catch {
      setStatus('Lost connection — retrying…');
      continue;
    }
    setStatus(job.detail || job.status, job.progress || 0);
    if (job.status === 'done') {
      setStatus(`Done — ${(job.files || []).join(', ') || 'saved'}`, 100);
      return;
    }
    if (job.status === 'error' || job.status === 'cancelled') {
      setError(job.detail || job.status);
      return;
    }
  }
}

async function download() {
  setError('');
  const url = $('url').value.trim();
  if (!url) { setError('Paste a link first.'); return; }
  const btn = $('download');
  btn.disabled = true;
  try {
    const serverUrl = OFFTUBE.normalizeServerUrl($('server').value);
    await chrome.storage.local.set({
      quality: $('quality').value,
      access: $('access').value,
      serverUrl,
    });
    const { job_id } = await OFFTUBE.api(serverUrl, '/api/download', {
      url, quality: $('quality').value, ...accessPayload(),
    });
    setStatus('Queued…', 0);
    await pollJob(serverUrl, job_id);
  } catch (err) {
    setError(err.message);
    setStatus('Failed.');
  } finally {
    btn.disabled = false;
  }
}

async function fillFromTab(serverUrl) {
  const { url, title } = await getActiveTabUrl();
  const line = $('tabLine');
  line.title = url;
  if (!url) {
    line.textContent = 'Could not read tab URL (need “tabs” permission). Paste a link below.';
    return;
  }
  let source;
  try {
    source = await OFFTUBE.sourceOf(serverUrl, url);
  } catch {
    line.textContent = 'Server unreachable — start ./run.sh, then reopen.';
    return;
  }
  if (!source) {
    line.textContent = 'Nothing offtube can keep on this tab — paste a link below.';
    return;
  }
  if (!$('url').value.trim()) $('url').value = url;
  line.textContent = `${source.label} · ${title || url}`.slice(0, 90);
}

document.addEventListener('DOMContentLoaded', async () => {
  try { await chrome.action.setBadgeText({ text: '' }); } catch { /* ignore */ }
  const { serverUrl, quality, access, cookiesBrowser } = await getSettings();
  savedBrowser = cookiesBrowser;
  $('server').value = serverUrl;
  $('quality').value = quality;
  $('access').value = access;
  syncAccess();
  await fillFromTab(serverUrl);
  await OFFTUBE.showHealth(serverUrl);
  if ($('url').value.trim()) inspect();
});

$('useTab').addEventListener('click', async () => {
  const { url } = await getActiveTabUrl();
  if (url) { $('url').value = url; inspect(); }
});

$('url').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); inspect(); }
});

$('inspect').addEventListener('click', inspect);
$('download').addEventListener('click', download);

$('access').addEventListener('change', async () => {
  syncAccess();
  await chrome.storage.local.set({ access: $('access').value });
});

OFFTUBE.wireCookieFile();

$('server').addEventListener('change', async () => {
  try {
    const v = OFFTUBE.normalizeServerUrl($('server').value);
    $('server').value = v;
    await chrome.storage.local.set({ serverUrl: v });
    await OFFTUBE.showHealth(v);
  } catch (err) {
    setError(err.message);
    $('server').value = OFFTUBE.DEFAULT_SERVER;
  }
});

$('openPanel').addEventListener('click', async () => {
  const win = await chrome.windows.getLastFocused();
  await chrome.sidePanel.open({ windowId: win.id });
  window.close();
});

$('openLib').addEventListener('click', async () => {
  const { serverUrl } = await getSettings();
  await chrome.tabs.create({ url: `${serverUrl}/` });
});
