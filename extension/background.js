/* offtube service worker — context menu + keyboard shortcut.
 * No long-lived state in globals: read chrome.storage on every event.
 */
'use strict';

importScripts('shared.js');

const YT_PATTERNS = [
  '*://*.youtube.com/*',
  '*://youtube.com/*',
  '*://youtu.be/*',
  '*://*.youtu.be/*',
  '*://*.youtube-nocookie.com/*',
];

async function ensureMenus() {
  await chrome.contextMenus.removeAll();
  await chrome.contextMenus.create({
    id: 'offtube-keep-link',
    title: 'Keep with offtube',
    contexts: ['link'],
    targetUrlPatterns: YT_PATTERNS,
  });
  await chrome.contextMenus.create({
    id: 'offtube-keep-page',
    title: 'Keep this video with offtube',
    contexts: ['page', 'video'],
    documentUrlPatterns: YT_PATTERNS,
  });
}

chrome.runtime.onInstalled.addListener(async () => {
  try {
    await ensureMenus();
  } catch (err) {
    console.error('offtube: failed to create context menus', err);
  }
});

async function setBadge(text, color) {
  try {
    if (color) await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text: text || '' });
  } catch {
    /* action may be unavailable in tests */
  }
}

async function keepUrl(url) {
  if (!OFFTUBE.isYouTubeUrl(url)) {
    await setBadge('!', '#bc3d1e');
    return;
  }
  const stored = await chrome.storage.local.get(['serverUrl', 'quality', 'access']);
  let server;
  try {
    server = OFFTUBE.normalizeServerUrl(stored.serverUrl);
  } catch {
    await setBadge('!', '#bc3d1e');
    return;
  }
  // File/paste cookies live in the side panel textarea, not in the SW.
  if (stored.access === 'upload') {
    await chrome.storage.session.set({ pendingUrl: url });
    const win = await chrome.windows.getLastFocused();
    await chrome.sidePanel.open({ windowId: win.id });
    return;
  }
  await setBadge('…', '#1b1a17');
  try {
    const access = stored.access === 'browser'
      ? { cookies_mode: 'browser', cookies_browser: 'chrome' }
      : { cookies_mode: 'none' };
    await OFFTUBE.api(server, '/api/download', {
      url,
      quality: stored.quality || '1080',
      ...access,
    });
    await setBadge('↓', '#20663c');
  } catch {
    await setBadge('!', '#bc3d1e');
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.srcUrl || info.pageUrl || (tab && tab.url) || '';
  await keepUrl(url);
});

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== 'open-side-panel') return;
  const win = await chrome.windows.getLastFocused();
  await chrome.sidePanel.open({ windowId: win.id });
});
