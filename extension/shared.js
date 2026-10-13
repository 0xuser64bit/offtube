/* Shared helpers for popup, side panel, and service worker.
 * Classic script (not a module) so importScripts() works in the SW.
 */
'use strict';

const OFFTUBE = {
  DEFAULT_SERVER: 'http://127.0.0.1:8000',
  MEDIA_WORD: { image: 'Image', video: 'Video', audio: 'Audio', mixed: 'Post' },
};

OFFTUBE.isWebUrl = function isWebUrl(v) {
  return /^https?:\/\//i.test(String(v || '').trim());
};

/* Which site is this URL on, if offtube supports it? The server matches it
 * against yt-dlp + gallery-dl extractors locally (no network), so asking on
 * every tab is cheap. null = unsupported; throws if the server is down. */
OFFTUBE.sourceOf = async function sourceOf(server, url) {
  if (!OFFTUBE.isWebUrl(url)) return null;
  const r = await OFFTUBE.apiGet(server, `/api/source?url=${encodeURIComponent(url)}`);
  return r.supported ? r.source : null;
};

/* Preview markup shared by popup and side panel: site · kind, title,
 * uploader, then a thumbnail grid for collections or one hero image. */
OFFTUBE.previewHtml = function previewHtml(info) {
  const e = OFFTUBE.escapeHtml;
  const many = (info.count || 0) > 1;
  let kind;
  if (info.type === 'playlist') kind = `Playlist · ${info.count || '?'} items`;
  else if (info.type === 'gallery') kind = many ? `${info.count}${info.more ? '+' : ''} items` : OFFTUBE.MEDIA_WORD[info.media] || 'Post';
  else kind = (OFFTUBE.MEDIA_WORD[info.media] || 'Video') + (info.is_live ? ' · live' : '');
  const site = info.source && info.source.label ? `<b>${e(info.source.label)}</b> · ` : '';
  const grid = info.type === 'gallery' && many
    ? `<ul class="grid">${(info.entries || []).slice(0, 8).map((x) => `<li>${x.thumbnail ? `<img src="${e(x.thumbnail)}" alt="" loading="lazy" />` : ''}</li>`).join('')}</ul>`
    : '';
  const hero = !grid && info.thumbnail ? `<img src="${e(info.thumbnail)}" alt="" />` : '';
  const sub = [info.uploader, info.duration_str && info.duration_str !== '-' ? info.duration_str : '']
    .filter(Boolean).map(e).join(' · ');
  const quals = (info.qualities || []).map((q) => e(q.label)).join(' · ');
  const note = info.part_of_playlist ? '<p>Part of a playlist — only this one is kept.</p>' : '';
  return `${hero}<div><p class="kind">${site}${e(kind)}</p><h3>${e(info.title || 'Untitled')}</h3>`
    + `${sub ? `<p>${sub}</p>` : ''}${quals ? `<p>${quals}</p>` : ''}${note}${grid}</div>`;
};

/* Fit the controls to what was inspected: galleries keep originals, so
 * body[data-kind=gallery] hides .video-only controls; audio-only sources
 * default to MP3; heights the video doesn't have are disabled. */
OFFTUBE.fitControls = function fitControls(info) {
  document.body.dataset.kind = info.type === 'gallery' ? 'gallery' : '';
  const sel = document.getElementById('quality');
  if (info.media === 'audio' && !sel.value.startsWith('audio')) sel.value = 'audio_mp3';
  const ladder = (info.qualities || []).map((q) => q.id);
  for (const opt of sel.options) {
    opt.disabled = ladder.length > 0 && opt.value !== 'best'
      && !opt.value.startsWith('audio') && !ladder.includes(opt.value);
  }
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
  if (checks?.gallery_dl === 'missing') problems.push('gallery-dl missing');
  return problems;
};
