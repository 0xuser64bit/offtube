// The extension's background worker, driven the way people use it: its
// right-click menu entries and its keyboard shortcut.
// node --test tests/browser/background.test.mjs
// Chromium's own menus and shortcuts take input only from the windowing
// system, so this runs a headed Chromium on a private Xvfb display and
// clicks and types through XTest (xinput.py). Linux only; see harness.mjs.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { promisify } from "node:util";
import { playwright, python, root, skipRealInput as skip, startServer, startXServer } from "./harness.mjs";

const PAGE = "https://www.youtube.com/watch?v=pagepage123";
const LINKS = "https://www.youtube.com/feed/videos";
const LINKED = "https://www.youtube.com/watch?v=linklink123";
// High and to the left in the page, so a context menu opens below and to
// the right of the pointer, which then rests on none of its entries.
const SPOT = "320,200";
// The arrow keys count back from a menu's end: Chrome's "Inspect" ends a
// link's menu, "View page source" and "Inspect" a page's.
const LINK_ENTRY = ["key:Up", "key:Up", "key:Return"];
const PAGE_ENTRY = ["key:Up", "key:Up", "key:Up", "key:Return"];

let x;
let server;
let profile;
let context;
let worker;
let page;
before(async () => {
  if (skip) return;
  x = await startXServer();
  server = await startServer();
  profile = mkdtempSync(path.join(tmpdir(), "offtube-input-"));
  const extension = path.join(root, "extension");
  const { WAYLAND_DISPLAY, ...env } = process.env;
  context = await playwright.chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: false,
    viewport: null,
    env: { ...env, DISPLAY: x.display },
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`,
      "--ozone-platform=x11", "--window-position=0,0", "--window-size=1280,1024"],
  });
  // Stand-ins for the site: the menus send only the URLs.
  await context.route("https://www.youtube.com/**", (route) => route.fulfill({
    contentType: "text/html",
    body: route.request().url() === PAGE
      ? "<title>A video</title><p>A video page.</p>"
      : `<title>Videos</title><a href="${LINKED}" style="position:fixed;inset:0">A linked video</a>`,
  }));
  [worker] = context.serviceWorkers();
  worker ??= await context.waitForEvent("serviceworker");
  page = context.pages()[0] ?? await context.newPage();
});
after(async () => {
  await context?.close();
  await server?.stop();
  x?.stop();
  if (profile) rmSync(profile, { recursive: true, force: true });
});

const run = promisify(execFile);
async function input(...steps) {
  await run(python, [path.join(root, "tests/browser/xinput.py"), ...steps], { env: { ...process.env, DISPLAY: x.display } });
}

async function eventually(check, failure, ms = 10000) {
  for (const deadline = Date.now() + ms; ;) {
    const value = await check();
    if (value) return value;
    assert.ok(Date.now() < deadline, failure);
    await new Promise((resolve) => { setTimeout(resolve, 50); });
  }
}

const jobs = async () => (await (await fetch(`${server.base}/api/jobs`)).json()).jobs;
const jobFor = async (url) => (await jobs()).find((job) => job.url === url);
const badge = () => worker.evaluate(() => chrome.action.getBadgeText({}));
const panels = () => worker.evaluate(async () => (await chrome.runtime.getContexts({ contextTypes: ["SIDE_PANEL"] })).length);

// Saved choices reset, with Sign-in set to `access` (none saved means none),
// the badge cleared and the side panel closed; then `url` in the browser,
// once the menu `entry` for that Sign-in is there.
async function startAt(url, entry, access) {
  await input("key:Escape");
  await worker.evaluate(async ([base, access]) => {
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
    await chrome.storage.local.set({ serverUrl: base, ...(access && { access }) });
    await chrome.action.setBadgeText({ text: "" });
    if ((await chrome.runtime.getContexts({ contextTypes: ["SIDE_PANEL"] })).length) {
      await chrome.sidePanel.close({ windowId: (await chrome.windows.getLastFocused()).id });
    }
  }, [server.base, access]);
  await eventually(async () => (await panels()) === 0, "the side panel stayed open");
  await eventually(() => worker.evaluate((id) => chrome.contextMenus.update(id, {}).then(() => true, () => false), entry),
    `no ${entry} menu entry`);
  await page.goto(url);
}

test("with Sign-in set to a cookies file, a right-clicked link opens the side panel with it", { skip }, async () => {
  // Only the panel has the file; the menu entry's id says to open it.
  await startAt(LINKS, "offtube-keep-link-in-panel", "upload");
  const queued = (await jobs()).length;
  await worker.evaluate(() => {
    self.handovers = [];
    self.recordHandover = (changes) => {
      if (changes.pendingUrl) self.handovers.push(changes.pendingUrl.newValue ?? "taken");
    };
    chrome.storage.session.onChanged.addListener(self.recordHandover);
  });
  try {
    await input(`menu:${SPOT}`, ...LINK_ENTRY);
    await eventually(async () => (await panels()) === 1, "the side panel didn't open");
    await eventually(async () => (await worker.evaluate(() => self.handovers.join(" "))) === `${LINKED} taken`,
      "the side panel didn't take the link");
    assert.equal((await jobs()).length, queued, "a link was kept without the cookies file");
  } finally {
    await worker.evaluate(() => chrome.storage.session.onChanged.removeListener(self.recordHandover));
  }
});

test("“Keep link with offtube” keeps the link that was right-clicked", { skip }, async () => {
  await startAt(LINKS, "offtube-keep-link");
  await input(`menu:${SPOT}`, ...LINK_ENTRY);
  const job = await eventually(() => jobFor(LINKED), "the server got no job for the link");
  assert.equal(job.quality, "1080");
  await eventually(async () => (await badge()) === "↓", "the badge doesn't say the link was queued");
});

test("“Keep this page with offtube” keeps the page itself", { skip }, async () => {
  await startAt(PAGE, "offtube-keep-page");
  await input(`menu:${SPOT}`, ...PAGE_ENTRY);
  await eventually(() => jobFor(PAGE), "the server got no job for the page");
  await eventually(async () => (await badge()) === "↓", "the badge doesn't say the page was queued");
});

test("Alt+Shift+O opens the side panel", { skip }, async () => {
  await startAt(PAGE, "offtube-keep-page");
  await input(`click:${SPOT}`, "key:Alt+Shift+O");
  await eventually(async () => (await panels()) === 1, "the side panel didn't open");
});
