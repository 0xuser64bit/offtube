/* offtube service worker — context menu + keyboard shortcut.
 * No long-lived state in globals: read chrome.storage on every event.
 */
'use strict';

importScripts('shared.js');

// No URL patterns: the server knows 2,000+ sites (yt-dlp + gallery-dl), a
// static list here would always lag it. It decides; the badge says '!'.
const MENUS = [
  { id: 'offtube-keep-link', title: 'Keep link with offtube', contexts: ['link'] },
  { id: 'offtube-keep-page', title: 'Keep this page with offtube', contexts: ['page', 'image', 'video', 'audio'] },
];

// Chrome lets sidePanel.open() run only while the click or shortcut that
// asked for it is being handled, which a listener's first await ends. With
// Sign-in set to a cookies file a click must open the panel (the file lives
// in its textarea), and that can't wait for chrome.storage: so the entries'
// ids say it, and they are rebuilt when Sign-in changes. (A second, hidden
// entry would do, but Chrome then folds both into a submenu.)
const IN_PANEL = '-in-panel';

// One rebuild at a time, so the last, which read the newest setting, wins.
let menusBuilt = Promise.resolve();
function ensureMenus() {
  menusBuilt = menusBuilt.then(async () => {
    const { access } = await OFFTUBE.getSettings();
    const suffix = access === 'upload' ? IN_PANEL : '';
    await chrome.contextMenus.removeAll();
    for (const menu of MENUS) chrome.contextMenus.create({ ...menu, id: menu.id + suffix });
  }).catch((err) => console.error('offtube: failed to create context menus', err));
  return menusBuilt;
}

chrome.runtime.onInstalled.addListener(() => ensureMenus());
chrome.storage.local.onChanged.addListener((changes) => {
  if (changes.access) ensureMenus();
});

async function setBadge(text, color) {
  try {
    if (color) await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text: text || '' });
  } catch {
    /* action may be unavailable in tests */
  }
}

async function setTitle(title) {
  try { await chrome.action.setTitle({ title }); } catch { /* tests */ }
}

/* Quick keeps finish long after the menu click. The worker is ephemeral, so
 * the ids live in session storage and an alarm wakes the worker to finish
 * the report if a poll is cut short. */
const QUICK_KEEPS = 'quickKeeps';
const QUICK_ALARM = 'offtube-job-';
const WAITING = '#1b1a17';

async function readQuickKeeps() {
  return (await chrome.storage.session.get(QUICK_KEEPS))[QUICK_KEEPS] || {};
}

async function writeQuickKeeps(keeps) {
  await chrome.storage.session.set({ [QUICK_KEEPS]: keeps });
}

async function finishQuickKeep(id, job) {
  const keeps = await readQuickKeeps();
  // Already reported, perhaps by a poll racing the alarm.
  if (!keeps[id]) return;
  delete keeps[id];
  await writeQuickKeeps(keeps);
  try { await chrome.alarms.clear(QUICK_ALARM + id); } catch { /* no alarm */ }
  if (Object.keys(keeps).length) return; // a newer keep still owns the badge
  if (job.status === 'done') {
    const files = job.files || [];
    await setBadge('↓', '#20663c');
    await setTitle(files.length > 1 ? `Kept ${files.length} files` : `Kept ${files[0] || 'the file'}`);
  } else if (job.status === 'cancelled') {
    await setBadge('');
    await setTitle('Keep cancelled');
  } else {
    await setBadge('!', '#bc3d1e');
    await setTitle(job.detail || 'Keep failed');
  }
}

const quickPolling = new Set();
async function pollQuickKeep(id, server) {
  if (quickPolling.has(id)) return;
  quickPolling.add(id);
  try {
    for (;;) {
      if (!(await readQuickKeeps())[id]) return;
      let job;
      try {
        job = await OFFTUBE.apiGet(server, `/api/job?id=${encodeURIComponent(id)}`);
      } catch (err) {
        // A job the server no longer knows stopped with it.
        if (err.status === 404) {
          await finishQuickKeep(id, { id, status: 'error', detail: OFFTUBE.RESTARTED });
          return;
        }
        await new Promise((resolve) => { setTimeout(resolve, 1500); });
        continue;
      }
      if (OFFTUBE.ACTIVE.includes(job.status)) {
        await new Promise((resolve) => { setTimeout(resolve, 700); });
        continue;
      }
      await finishQuickKeep(id, job);
      return;
    }
  } finally {
    quickPolling.delete(id);
  }
}

async function trackQuickKeep(id, server) {
  const keeps = await readQuickKeeps();
  keeps[id] = { server, started: Date.now() };
  await writeQuickKeeps(keeps);
  // The alarm is the backstop for a worker that shuts down mid-download;
  // pollQuickKeep gives prompt feedback while it is alive.
  try {
    await chrome.alarms.create(QUICK_ALARM + id, { delayInMinutes: 0.5, periodInMinutes: 0.5 });
  } catch { /* alarms unavailable in tests */ }
  pollQuickKeep(id, server);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm.name.startsWith(QUICK_ALARM)) return;
  const id = alarm.name.slice(QUICK_ALARM.length);
  readQuickKeeps().then((keeps) => {
    if (keeps[id]) pollQuickKeep(id, keeps[id].server);
  });
});


// Only from a listener, before its first await (see IN_PANEL).
function openPanel(tab) {
  chrome.sidePanel.open({ windowId: tab?.windowId ?? chrome.windows.WINDOW_ID_CURRENT })
    .catch((err) => console.error('offtube: failed to open the side panel', err));
}

async function keepUrl(url) {
  if (!OFFTUBE.isWebUrl(url)) {
    await setBadge('!', '#bc3d1e');
    return;
  }
  const { serverUrl: server, quality, access, cookiesBrowser, gif, videoSpeed } = await OFFTUBE.getSettings();
  if (access === 'upload') {
    // Only while the menus catch up with a Sign-in change: too late to open
    // the panel here, so the link waits for the next time it opens.
    await chrome.storage.session.set({ pendingUrl: url });
    return;
  }
  await setBadge('…', WAITING);
  await setTitle('Keeping…');
  try {
    const cookies = access === 'browser'
      ? { cookies_mode: 'browser', cookies_browser: cookiesBrowser }
      : { cookies_mode: 'none' };
    const { job_id: id } = await OFFTUBE.api(server, '/api/download', {
      url, quality, video_speed: quality === 'gif' || quality.startsWith('audio') ? 1 : videoSpeed, ...cookies,
      ...(quality === 'gif' ? { gif_size: gif.gif_size ?? 480, gif_fps: gif.gif_fps ?? 15, gif_loop: gif.gif_loop ?? true } : {}),
    });
    await trackQuickKeep(id, server);
  } catch (err) {
    await setBadge('!', '#bc3d1e');
    await setTitle(err.message || 'Keep failed');
  }
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const id = String(info.menuItemId);
  // The page, never srcUrl: a media element's src is a blob: or a bare CDN
  // file, while the page (pin, post, watch URL) is what the engines read.
  const url = (id.startsWith('offtube-keep-link') ? info.linkUrl : info.pageUrl || tab?.url) || '';
  if (id.endsWith(IN_PANEL) && OFFTUBE.isWebUrl(url)) {
    // File/paste cookies live in the side panel's textarea, not here: the
    // panel takes the link, and keeps its own saved format and GIF choices.
    chrome.storage.session.set({ pendingUrl: url });
    openPanel(tab);
    return;
  }
  keepUrl(url);
});

chrome.commands.onCommand.addListener((command, tab) => {
  if (command === 'open-side-panel') openPanel(tab);
});
