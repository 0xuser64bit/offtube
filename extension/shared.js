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

OFFTUBE.healthProblems = function healthProblems(checks) {
  const problems = [];
  if (!checks?.ffmpeg) problems.push('ffmpeg missing');
  if (checks?.yt_dlp === 'missing') problems.push('yt-dlp missing');
  if (!checks?.js_runtime_ok) problems.push('no JS runtime');
  return problems;
};
