/* offtube side panel — full parity with the local web UI, URL auto-filled
 * from the active tab. Polling lives here (not in the service worker —
 * service workers are ephemeral, so no setInterval / state in globals there).
 */
'use strict';

const DEFAULT_SERVER = 'http://127.0.0.1:8000';
const YT_RE = /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\//i;

const $ = (id) => document.getElementById(id);
let pollTimer = null;

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

async function getSettings() {
  const s = await chrome.storage.local.get(['serverUrl', 'quality', 'otype']);
  return {
    serverUrl: String(s.serverUrl || DEFAULT_SERVER).replace(/\/+$/, ''),
    quality: s.quality || '1080',
    otype: s.otype || 'video',
  };
}

function isYouTubeUrl(v) {
  return YT_RE.test(String(v || '').trim());
}

async function api(server, path, body) {
  const res = await fetch(server + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function apiGet(server, path) {
  const res = await fetch(server + path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function setError(msg) {
  const el = $('error');
  if (!msg) { el.classList.add('hidden'); el.textContent = ''; return; }
  el.textContent = msg;
  el.classList.remove('hidden');
}

function otype() {
  return document.querySelector('input[name="otype"]:checked')?.value || 'video';
}

function effectiveQuality() {
  const q = $('quality').value;
  if (otype() === 'audio' && !q.startsWith('audio')) return 'audio_mp3';
  return q;
}

async function checkHealth(server) {
  try {
    const { checks } = await apiGet(server, '/api/health');
    $('health').className = 'status ok';
    $('healthText').textContent = `ready · ${checks.yt_dlp || ''}`;
  } catch {
    $('health').className = 'status bad';
    $('healthText').textContent = 'server unreachable — start ./run.sh';
  }
}

async function fillFromTab(force) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || '';
  if (url && isYouTubeUrl(url)) {
    if (force || !$('url').value.trim()) $('url').value = url;
    $('tabLine').textContent = `Tab: ${(tab.title || url)}`.slice(0, 100);
    $('tabLine').title = url;
    return true;
  }
  $('tabLine').textContent = url
    ? 'Current tab is not YouTube — paste a link below.'
    : 'Could not read tab URL. Paste a link below.';
  return false;
}

async function inspect() {
  setError('');
  const { serverUrl } = await getSettings();
  const url = $('url').value.trim();
  if (!url || !isYouTubeUrl(url)) { setError('Only youtube.com / youtu.be links are supported.'); return; }
  try {
    const { info } = await api(serverUrl, '/api/info', { url, cookies_mode: 'none' });
    if (info.type === 'playlist') {
      $('preview').innerHTML = `<div><strong>${escapeHtml(info.title || 'Playlist')}</strong><br /><span class="muted">Playlist · ${escapeHtml(String(info.count || ''))} videos</span></div>`;
      document.querySelector('input[name="plmode"][value="playlist"]').checked = true;
    } else {
      const quals = (info.qualities || []).map((q) => escapeHtml(q.label)).join(' · ');
      $('preview').innerHTML = `${info.thumbnail ? `<img src="${escapeHtml(info.thumbnail)}" alt="" />` : ''}<div><strong>${escapeHtml(info.title || 'Video')}</strong><br /><span class="muted">${escapeHtml(info.uploader || '')} ${escapeHtml(info.duration_str || '')}<br />${quals}</span></div>`;
    }
  } catch (err) {
    setError(err.message);
  }
}

function payload() {
  return {
    url: $('url').value.trim(),
    quality: effectiveQuality(),
    subtitles: $('subs').checked,
    auto_subs: true,
    sub_lang: 'en',
    clip_from: $('clipFrom').value.trim(),
    clip_to: $('clipTo').value.trim(),
    playlist_mode: document.querySelector('input[name="plmode"]:checked')?.value || 'single',
    cookies_mode: $('access').value,
    cookies_browser: 'chrome',
  };
}

async function refreshQueue(server) {
  try {
    const { jobs } = await apiGet(server, '/api/jobs?limit=5');
    $('queueCount').textContent = String((jobs || []).length);
    $('queue').innerHTML = (jobs || []).slice(-5).reverse().map((j) => (
      `<div class="job"><strong>${escapeHtml(j.status)}</strong> · ${Math.round(j.progress || 0)}%<br /><span class="muted">${escapeHtml((j.url || '').slice(0, 60))}</span></div>`
    )).join('') || '<p class="muted">No downloads yet.</p>';
  } catch { /* server down — health badge already shows it */ }
}

async function refreshFiles(server) {
  try {
    const { files } = await apiGet(server, '/api/files');
    $('files').innerHTML = (files || []).slice(0, 20).map((f) => (
      `<div class="file"><span>${escapeHtml(f.name)}</span><a href="${escapeHtml(server + f.url)}" target="_blank" rel="noreferrer">Save</a></div>`
    )).join('') || '<p class="muted">Nothing saved yet.</p>';
  } catch { /* ignore */ }
}

async function pollJob(server, jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const job = await apiGet(server, `/api/job?id=${encodeURIComponent(jobId)}`);
      $('bar').style.width = `${job.progress || 0}%`;
      $('status').textContent = job.detail || job.status;
      $('log').textContent = (job.log || []).slice(-30).join('\n');
      refreshQueue(server);
      if (['done', 'error', 'cancelled'].includes(job.status)) {
        clearInterval(pollTimer);
        $('download').disabled = false;
        refreshFiles(server);
        if (job.status !== 'done') setError(job.detail || job.status);
      }
    } catch {
      $('status').textContent = 'Lost connection — retrying…';
    }
  }, 900);
}

document.addEventListener('DOMContentLoaded', async () => {
  const { serverUrl, quality, otype: ot } = await getSettings();
  $('server').value = serverUrl;
  $('quality').value = quality;
  const r = document.querySelector(`input[name="otype"][value="${ot}"]`);
  if (r) r.checked = true;
  await fillFromTab(false);
  await checkHealth(serverUrl);
  refreshQueue(serverUrl);
  refreshFiles(serverUrl);
  if (isYouTubeUrl($('url').value)) inspect();
});

$('useTab').addEventListener('click', async () => { await fillFromTab(true); inspect(); });
$('inspect').addEventListener('click', inspect);
$('url').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); inspect(); }
});

document.querySelectorAll('input[name="otype"]').forEach((el) => el.addEventListener('change', async () => {
  await chrome.storage.local.set({ otype: otype() });
  if (otype() === 'audio' && !$('quality').value.startsWith('audio')) $('quality').value = 'audio_mp3';
  if (otype() === 'video' && $('quality').value.startsWith('audio')) $('quality').value = '1080';
}));

$('download').addEventListener('click', async () => {
  setError('');
  const { serverUrl } = await getSettings();
  const p = payload();
  if (!p.url) { setError('Paste a link first.'); return; }
  $('download').disabled = true;
  $('status').textContent = 'Starting…';
  try {
    await chrome.storage.local.set({ quality: $('quality').value, otype: otype() });
    const { job_id } = await api(serverUrl, '/api/download', p);
    pollJob(serverUrl, job_id);
  } catch (err) {
    setError(err.message);
    $('download').disabled = false;
  }
});

$('openLib').addEventListener('click', async () => {
  const { serverUrl } = await getSettings();
  await chrome.tabs.create({ url: `${serverUrl}/` });
});

$('server').addEventListener('change', async () => {
  const v = ($('server').value.trim() || DEFAULT_SERVER).replace(/\/+$/, '');
  await chrome.storage.local.set({ serverUrl: v });
  await checkHealth(v);
  refreshQueue(v);
  refreshFiles(v);
});
