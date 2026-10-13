/* Shared helpers for popup, side panel, and service worker.
 * Classic script (not a module) so importScripts() works in the SW.
 */
'use strict';

const OFFTUBE = {
  DEFAULT_SERVER: 'http://127.0.0.1:8000',
  YT_RE: /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)\//i,
};

OFFTUBE.isYouTubeUrl = function isYouTubeUrl(v) {
  return OFFTUBE.YT_RE.test(String(v || '').trim());
};

OFFTUBE.escapeHtml = function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
};

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
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
};

OFFTUBE.apiGet = async function apiGet(server, path) {
  const res = await fetch(server + path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
};

OFFTUBE.getSettings = async function getSettings() {
  const s = await chrome.storage.local.get(['serverUrl', 'quality', 'otype', 'access', 'cookies_browser']);
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
  };
};

/* DOM helpers below assume the ids popup.html and sidepanel.html share:
 * #health/#healthText, #error, #cookieFile/#cookieText/#cookieFileName. */

OFFTUBE.setError = function setError(msg) {
  const el = document.getElementById('error');
  el.textContent = msg || '';
  el.classList.toggle('hidden', !msg);
};

OFFTUBE.showHealth = async function showHealth(server) {
  const badge = document.getElementById('health');
  const text = document.getElementById('healthText');
  try {
    const { checks } = await OFFTUBE.apiGet(server, '/api/health');
    const problems = OFFTUBE.healthProblems(checks);
    badge.className = problems.length ? 'status bad' : 'status ok';
    text.textContent = problems.length ? problems.join(' · ') : `ready · ${checks.yt_dlp || ''}`;
  } catch {
    badge.className = 'status bad';
    text.textContent = 'server unreachable — start ./run.sh';
  }
};

OFFTUBE.wireCookieFile = function wireCookieFile() {
  document.getElementById('cookieFile').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    document.getElementById('cookieText').value = await f.text();
    document.getElementById('cookieFileName').textContent = `${f.name} · ${(f.size / 1024).toFixed(1)} KB`;
  });
};

OFFTUBE.healthProblems = function healthProblems(checks) {
  const problems = [];
  if (!checks?.ffmpeg) problems.push('ffmpeg missing');
  if (checks?.yt_dlp === 'missing') problems.push('yt-dlp missing');
  if (!checks?.js_runtime_ok) problems.push('no JS runtime');
  return problems;
};
