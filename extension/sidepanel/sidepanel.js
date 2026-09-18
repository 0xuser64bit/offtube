/* offtube side panel — full controls + progress. Polling lives here
 * (not in the service worker — service workers are ephemeral).
 */
'use strict';

const $ = (id) => document.getElementById(id);
let pollTimer = null;
let lastTabUrl = '';
let lastInspected = '';
let activeJobId = null;

async function getSettings() {
  const s = await chrome.storage.local.get(['serverUrl', 'quality', 'otype', 'access']);
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
  };
}

function syncAccess() {
  $('cookieUpload').classList.toggle('hidden', $('access').value !== 'upload');
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

function accessFields() {
  const mode = $('access').value;
  if (mode === 'upload') return { cookies_mode: 'upload', cookies_text: $('cookieText').value };
  if (mode === 'browser') return { cookies_mode: 'browser', cookies_browser: 'chrome' };
  return { cookies_mode: 'none' };
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
    ...accessFields(),
  };
}

async function checkHealth(server) {
  try {
    const { checks } = await OFFTUBE.apiGet(server, '/api/health');
    const problems = OFFTUBE.healthProblems(checks);
    if (problems.length) {
      $('health').className = 'status bad';
      $('healthText').textContent = problems.join(' · ');
    } else {
      $('health').className = 'status ok';
      $('healthText').textContent = `ready · ${checks.yt_dlp || ''}`;
    }
  } catch {
    $('health').className = 'status bad';
    $('healthText').textContent = 'server unreachable — start ./run.sh';
  }
}

async function fillFromTab(force) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || '';
  if (url && OFFTUBE.isYouTubeUrl(url)) {
    const current = $('url').value.trim();
    if (force || !current || current === lastTabUrl) {
      $('url').value = url;
      lastTabUrl = url;
    }
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
  if (!url || !OFFTUBE.isYouTubeUrl(url)) {
    setError('Only youtube.com / youtu.be links are supported.');
    return;
  }
  try {
    lastInspected = url;
    const { info } = await OFFTUBE.api(serverUrl, '/api/info', { url, ...accessFields() });
    if (info.type === 'playlist') {
      $('preview').innerHTML = `<div><strong>${OFFTUBE.escapeHtml(info.title || 'Playlist')}</strong><br /><span class="muted">Playlist · ${OFFTUBE.escapeHtml(String(info.count || ''))} videos</span></div>`;
      document.querySelector('input[name="plmode"][value="playlist"]').checked = true;
    } else {
      const quals = (info.qualities || []).map((q) => OFFTUBE.escapeHtml(q.label)).join(' · ');
      const plNote = info.part_of_playlist
        ? '<br /><span class="muted">Part of a playlist — switch Playlist below if you want more than this video.</span>'
        : '';
      $('preview').innerHTML = `${info.thumbnail ? `<img src="${OFFTUBE.escapeHtml(info.thumbnail)}" alt="" />` : ''}<div><strong>${OFFTUBE.escapeHtml(info.title || 'Video')}</strong><br /><span class="muted">${OFFTUBE.escapeHtml(info.uploader || '')} ${OFFTUBE.escapeHtml(info.duration_str || '')}<br />${quals}</span>${plNote}</div>`;
    }
  } catch (err) {
    setError(err.message);
  }
}

async function refreshQueue(server) {
  try {
    const { jobs } = await OFFTUBE.apiGet(server, '/api/jobs?limit=5');
    $('queueCount').textContent = String((jobs || []).length);
    $('queue').innerHTML = (jobs || []).slice(-5).reverse().map((j) => {
      const cancellable = ['queued', 'starting', 'downloading', 'processing'].includes(j.status);
      const cancel = cancellable
        ? `<button class="btn quiet small" data-cancel="${OFFTUBE.escapeHtml(j.id)}" type="button">Cancel</button>`
        : '';
      return `<div class="job"><strong>${OFFTUBE.escapeHtml(j.status)}</strong> · ${Math.round(j.progress || 0)}%<br /><span class="muted">${OFFTUBE.escapeHtml((j.url || '').slice(0, 60))}</span>${cancel}</div>`;
    }).join('') || '<p class="muted">No downloads yet.</p>';
  } catch { /* server down — health badge already shows it */ }
}

async function refreshFiles(server) {
  try {
    const { files } = await OFFTUBE.apiGet(server, '/api/files');
    $('files').innerHTML = (files || []).slice(0, 20).map((f) => (
      `<div class="file"><span>${OFFTUBE.escapeHtml(f.name)}</span><a href="${OFFTUBE.escapeHtml(server + f.url)}" target="_blank" rel="noreferrer">Save</a></div>`
    )).join('') || '<p class="muted">Nothing saved yet.</p>';
  } catch { /* ignore */ }
}

async function pollJob(server, jobId) {
  clearInterval(pollTimer);
  activeJobId = jobId;
  pollTimer = setInterval(async () => {
    try {
      const job = await OFFTUBE.apiGet(server, `/api/job?id=${encodeURIComponent(jobId)}`);
      if (activeJobId !== jobId) return;
      $('bar').style.width = `${job.progress || 0}%`;
      $('status').textContent = job.detail || job.status;
      $('log').textContent = (job.log || []).slice(-30).join('\n');
      refreshQueue(server);
      if (['done', 'error', 'cancelled'].includes(job.status)) {
        clearInterval(pollTimer);
        $('download').disabled = false;
        activeJobId = null;
        refreshFiles(server);
        if (job.status !== 'done') setError(job.detail || job.status);
      }
    } catch {
      if (activeJobId === jobId) $('status').textContent = 'Lost connection — retrying…';
    }
  }, 900);
}

document.addEventListener('DOMContentLoaded', async () => {
  try { await chrome.action.setBadgeText({ text: '' }); } catch { /* ignore */ }
  const { serverUrl, quality, otype: ot, access } = await getSettings();
  $('server').value = serverUrl;
  $('quality').value = quality;
  $('access').value = access;
  syncAccess();
  const r = document.querySelector(`input[name="otype"][value="${ot}"]`);
  if (r) r.checked = true;

  const session = await chrome.storage.session.get(['pendingUrl']);
  if (session.pendingUrl) {
    $('url').value = session.pendingUrl;
    lastTabUrl = session.pendingUrl;
    await chrome.storage.session.remove('pendingUrl');
    await fillFromTab(false);
  } else {
    await fillFromTab(false);
  }
  await checkHealth(serverUrl);
  refreshQueue(serverUrl);
  refreshFiles(serverUrl);
  if (OFFTUBE.isYouTubeUrl($('url').value)) inspect();
});

async function followTabIfNeeded() {
  const changed = await fillFromTab(false);
  const current = $('url').value.trim();
  if (changed && current === lastTabUrl && current !== lastInspected) inspect();
}

chrome.storage.session.onChanged.addListener(async (changes) => {
  if (!changes.pendingUrl || !changes.pendingUrl.newValue) return;
  $('url').value = changes.pendingUrl.newValue;
  lastTabUrl = changes.pendingUrl.newValue;
  await chrome.storage.session.remove('pendingUrl');
  inspect();
});

chrome.tabs.onActivated.addListener(async () => {
  await followTabIfNeeded();
});
chrome.tabs.onUpdated.addListener(async (_id, change, tab) => {
  if (!tab.active || !change.url) return;
  await followTabIfNeeded();
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
  const p = payload();
  if (!p.url) { setError('Paste a link first.'); return; }
  $('download').disabled = true;
  $('status').textContent = 'Starting…';
  try {
    const serverUrl = OFFTUBE.normalizeServerUrl($('server').value);
    await chrome.storage.local.set({ quality: $('quality').value, otype: otype(), access: $('access').value, serverUrl });
    const { job_id } = await OFFTUBE.api(serverUrl, '/api/download', p);
    $('download').disabled = false;
    pollJob(serverUrl, job_id);
  } catch (err) {
    setError(err.message);
    $('download').disabled = false;
  }
});

$('queue').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-cancel]');
  if (!btn) return;
  btn.disabled = true;
  try {
    const { serverUrl } = await getSettings();
    await OFFTUBE.api(serverUrl, '/api/cancel', { job_id: btn.dataset.cancel });
    refreshQueue(serverUrl);
  } catch (err) {
    setError('Cancel failed: ' + err.message);
    btn.disabled = false;
  }
});

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

$('openLib').addEventListener('click', async () => {
  const { serverUrl } = await getSettings();
  await chrome.tabs.create({ url: `${serverUrl}/` });
});

$('server').addEventListener('change', async () => {
  try {
    const v = OFFTUBE.normalizeServerUrl($('server').value);
    $('server').value = v;
    await chrome.storage.local.set({ serverUrl: v });
    await checkHealth(v);
    refreshQueue(v);
    refreshFiles(v);
  } catch (err) {
    setError(err.message);
    $('server').value = OFFTUBE.DEFAULT_SERVER;
  }
});
