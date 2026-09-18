/* offtube popup — MV3, no inline scripts, async/await only.
 * Reads the active tab URL (needs "tabs" permission — without it tab.url
 * is silently undefined), lets the user override it, talks to the local
 * offtube server over http://127.0.0.1 (see host_permissions in manifest).
 */
'use strict';

const $ = (id) => document.getElementById(id);

async function getSettings() {
  const stored = await chrome.storage.local.get(['serverUrl', 'quality', 'access']);
  let serverUrl = OFFTUBE.DEFAULT_SERVER;
  try {
    serverUrl = OFFTUBE.normalizeServerUrl(stored.serverUrl);
  } catch {
    serverUrl = OFFTUBE.DEFAULT_SERVER;
  }
  return {
    serverUrl,
    quality: stored.quality || '1080',
    access: stored.access || 'none',
  };
}

function accessPayload() {
  const mode = $('access').value;
  if (mode === 'upload') return { cookies_mode: 'upload', cookies_text: $('cookieText').value };
  if (mode === 'browser') return { cookies_mode: 'browser', cookies_browser: 'chrome' };
  return { cookies_mode: 'none' };
}

function syncAccess() {
  $('cookieUpload').classList.toggle('hidden', $('access').value !== 'upload');
}

async function getActiveTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return { url: tab?.url || '', title: tab?.title || '' };
}

function setError(msg) {
  const el = $('error');
  if (!msg) { el.classList.add('hidden'); el.textContent = ''; return; }
  el.textContent = msg;
  el.classList.remove('hidden');
}

function setStatus(msg, pct) {
  $('status').textContent = msg;
  if (typeof pct === 'number') $('bar').style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

async function checkHealth(server) {
  const badge = $('health');
  try {
    const { checks } = await OFFTUBE.apiGet(server, '/api/health');
    const problems = OFFTUBE.healthProblems(checks);
    if (problems.length) {
      badge.className = 'status bad';
      $('healthText').textContent = problems.join(' · ');
    } else {
      badge.className = 'status ok';
      $('healthText').textContent = `ready · ${checks.yt_dlp || ''}`;
    }
  } catch {
    badge.className = 'status bad';
    $('healthText').textContent = 'server unreachable — start ./run.sh';
  }
}

function renderPreview(info) {
  const box = $('preview');
  if (info.type === 'playlist') {
    box.innerHTML = `<div><h3>${OFFTUBE.escapeHtml(info.title || 'Playlist')}</h3><p>Playlist · ${OFFTUBE.escapeHtml(String(info.count || ''))} videos</p></div>`;
    return;
  }
  const quals = (info.qualities || []).map((q) => OFFTUBE.escapeHtml(q.label)).join(' · ');
  const plNote = info.part_of_playlist
    ? '<p>Part of a playlist — open the side panel to grab more than this video.</p>'
    : '';
  box.innerHTML = `${info.thumbnail ? `<img src="${OFFTUBE.escapeHtml(info.thumbnail)}" alt="" />` : ''}`
    + `<div><h3>${OFFTUBE.escapeHtml(info.title || 'Video')}</h3><p>${OFFTUBE.escapeHtml(info.uploader || '')}${info.duration_str ? ` · ${OFFTUBE.escapeHtml(info.duration_str)}` : ''}</p><p>${quals}</p>${plNote}</div>`;
  const ladder = (info.qualities || []).map((q) => q.id);
  const sel = $('quality');
  for (const opt of sel.options) {
    if (ladder.length && opt.value !== 'best' && !opt.value.startsWith('audio') && !ladder.includes(opt.value)) {
      opt.disabled = true;
    } else {
      opt.disabled = false;
    }
  }
}

async function inspect() {
  setError('');
  const { serverUrl } = await getSettings();
  const url = $('url').value.trim();
  if (!url) { setError('Paste a link or open a YouTube tab first.'); return; }
  if (!OFFTUBE.isYouTubeUrl(url)) { setError('Only youtube.com / youtu.be links are supported.'); return; }
  $('inspect').disabled = true;
  try {
    const { info } = await OFFTUBE.api(serverUrl, '/api/info', { url, ...accessPayload() });
    renderPreview(info);
    setStatus('Inspected. Pick quality and hit Download.');
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

async function fillFromTab() {
  const { url, title } = await getActiveTabUrl();
  const line = $('tabLine');
  if (url && OFFTUBE.isYouTubeUrl(url)) {
    if (!$('url').value.trim()) $('url').value = url;
    line.textContent = `Tab: ${title || url}`.slice(0, 90);
    line.title = url;
  } else if (url) {
    line.textContent = 'Current tab is not YouTube — paste a link below.';
    line.title = url;
  } else {
    line.textContent = 'Could not read tab URL (need “tabs” permission). Paste a link below.';
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  try { await chrome.action.setBadgeText({ text: '' }); } catch { /* ignore */ }
  const { serverUrl, quality, access } = await getSettings();
  $('server').value = serverUrl;
  $('quality').value = quality;
  $('access').value = access;
  syncAccess();
  await fillFromTab();
  await checkHealth(serverUrl);
  if (OFFTUBE.isYouTubeUrl($('url').value)) inspect();
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

$('cookieFile').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $('cookieText').value = await f.text();
  $('cookieFileName').textContent = `${f.name} · ${(f.size / 1024).toFixed(1)} KB`;
});

$('server').addEventListener('change', async () => {
  try {
    const v = OFFTUBE.normalizeServerUrl($('server').value);
    $('server').value = v;
    await chrome.storage.local.set({ serverUrl: v });
    await checkHealth(v);
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
