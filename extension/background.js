/* offtube service worker — context menu + keyboard shortcut.
 * No long-lived state in globals: read chrome.storage on every event.
 */
'use strict';

importScripts('shared.js');

// No URL patterns: the server knows 2,000+ sites (yt-dlp + gallery-dl), a
// static list here would always lag it. It decides; the badge says '!'.
async function ensureMenus() {
  await chrome.contextMenus.removeAll();
  await chrome.contextMenus.create({
    id: 'offtube-keep-link',
    title: 'Keep link with offtube',
    contexts: ['link'],
  });
  await chrome.contextMenus.create({
    id: 'offtube-keep-page',
    title: 'Keep this page with offtube',
    contexts: ['page', 'image', 'video', 'audio'],
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
  if (!OFFTUBE.isWebUrl(url)) {
    await setBadge('!', '#bc3d1e');
    return;
  }
  const { serverUrl: server, quality, access, cookiesBrowser } = await OFFTUBE.getSettings();
  // File/paste cookies live in the side panel textarea, not in the SW.
  if (access === 'upload') {
    await chrome.storage.session.set({ pendingUrl: url });
    const win = await chrome.windows.getLastFocused();
    if (win) await chrome.sidePanel.open({ windowId: win.id });
    return;
  }
  await setBadge('…', '#1b1a17');
  try {
    const cookies = access === 'browser'
      ? { cookies_mode: 'browser', cookies_browser: cookiesBrowser }
      : { cookies_mode: 'none' };
    await OFFTUBE.api(server, '/api/download', { url, quality, ...cookies });
    await setBadge('↓', '#20663c');
  } catch {
    await setBadge('!', '#bc3d1e');
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  // The page, never srcUrl: a media element's src is a blob: or a bare CDN
  // file, while the page (pin, post, watch URL) is what the engines read.
  const url = info.menuItemId === 'offtube-keep-link'
    ? info.linkUrl
    : info.pageUrl || (tab && tab.url);
  await keepUrl(url || '');
});

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== 'open-side-panel') return;
  const win = await chrome.windows.getLastFocused();
  if (win) await chrome.sidePanel.open({ windowId: win.id });
});
