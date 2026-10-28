// The extension's popup and side panel in a real browser:
// node --test tests/browser/extension.test.mjs
// The unpacked extension is loaded into Chromium against the stub server.
// Needs Playwright with Chromium, and axe-core for the accessibility audit
// (see harness.mjs for when they may be missing).
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { axeViolations, freePort, playwright, root, skip, skipAudit, startServer, tabTo, textSpacingLosses, watchProblems } from "./harness.mjs";

let context;
let worker;
let profile;
before(async () => {
  if (!playwright) return;
  const extension = path.join(root, "extension");
  profile = mkdtempSync(path.join(tmpdir(), "offtube-extension-"));
  // Extensions need a persistent profile and the full Chromium build; the
  // headless shell can't load them.
  context = await playwright.chromium.launchPersistentContext(profile, {
    channel: "chromium",
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  [worker] = context.serviceWorkers();
  worker ??= await context.waitForEvent("serviceworker");
});
after(async () => {
  await context?.close();
  if (profile) rmSync(profile, { recursive: true, force: true });
});

// A fresh start against `base`: no saved choices and no link handed over.
async function useServer(base) {
  await worker.evaluate(async (url) => {
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
    await chrome.storage.local.set({ serverUrl: url });
  }, base);
}

// The popup and the side panel opened as tabs, at their real widths. The
// popup's "active tab" is then itself, which has nothing to keep, so it
// opens the link field, as on any such page.
async function openSurface(name, colorScheme = "light") {
  const page = await context.newPage();
  await page.setViewportSize(name === "popup" ? { width: 380, height: 640 } : { width: 400, height: 900 });
  await page.emulateMedia({ colorScheme });
  const problems = watchProblems(page);
  const extensionId = new URL(worker.url()).host;
  await page.goto(`chrome-extension://${extensionId}/${name}/${name}.html`);
  return { page, problems };
}

async function listen(page) {
  await page.evaluate(() => {
    window.spoken = [];
    new MutationObserver(() => window.spoken.push(document.getElementById("announce").textContent))
      .observe(document.getElementById("announce"), { childList: true, characterData: true, subtree: true });
  });
}

async function paste(page, url) {
  await page.waitForSelector("#linkForm:not(.hidden)");
  await page.fill("#url", url);
  await page.press("#url", "Enter");
}

async function keepAndWait(page) {
  await page.click("#dlBtn");
  await page.waitForFunction(() => /^Kept /.test(document.getElementById("jobStatus").textContent), null, { timeout: 15000 });
}

test("the popup says plainly when offtube isn't running", { skip }, async () => {
  await useServer(`http://127.0.0.1:${await freePort()}`);
  const { page, problems } = await openSurface("popup");
  try {
    await page.waitForFunction(() => document.getElementById("stage").textContent.includes("offtube isn’t running."));
    assert.equal(await page.isVisible("#linkRow"), false);
    // Only the refused connections themselves may be logged.
    assert.deepEqual(problems.filter((p) => !p.includes("ERR_CONNECTION_REFUSED")), []);
  } finally {
    await page.close();
  }
});

test("the popup keeps a pasted link and says so once", { skip }, async () => {
  const server = await startServer();
  await useServer(server.base);
  const { page, problems } = await openSurface("popup");
  try {
    await listen(page);
    await paste(page, "https://www.youtube.com/watch?v=abc123abc12");
    await page.waitForSelector("#stage .print-title");
    assert.equal(await page.textContent("#stage .print-title"), "A test video");
    await keepAndWait(page);
    const kept = await (await fetch(`${server.base}/api/files`)).json();
    assert.deepEqual(kept.files.map((f) => f.name), ["A test video [abc123abc12].mp4"]);
    const spoken = await page.evaluate(() => window.spoken);
    assert.equal(spoken[0], "Reading the link…");
    assert.equal(spoken[1], "YouTube video ready: A test video.");
    assert.equal(spoken.at(-1), "Kept A test video [abc123abc12].mp4.");
    assert.ok(spoken.length <= 5 && new Set(spoken).size === spoken.length, spoken.join(" | "));
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
    await server.stop();
  }
});

test("a collection moves from the popup to the side panel with its link and format", { skip }, async () => {
  const server = await startServer();
  await useServer(server.base);
  const { page: popup } = await openSurface("popup");
  let panel;
  try {
    await paste(popup, "https://www.youtube.com/playlist?list=PLabc");
    await popup.waitForSelector("#stage .cell");
    await popup.click('label:has(input[name="q"][value="720"])');
    // Opening the real panel is the browser's part, and it would take the
    // handover before the tab below could.
    await popup.evaluate(() => {
      chrome.sidePanel.open = async () => {};
      window.close = () => {};
    });
    await popup.click("#pickInPanel");
    for (const deadline = Date.now() + 5000; ;) {
      const { pendingUrl } = await worker.evaluate(() => chrome.storage.session.get("pendingUrl"));
      if (pendingUrl) break;
      assert.ok(Date.now() < deadline, "the popup handed nothing over");
      await new Promise((resolve) => { setTimeout(resolve, 20); });
    }
    let problems;
    ({ page: panel, problems } = await openSurface("sidepanel"));
    await panel.waitForSelector("#stage .cell");
    assert.equal(await panel.inputValue("#url"), "https://www.youtube.com/playlist?list=PLabc");
    assert.equal(await panel.isChecked('input[name="q"][value="720"]'), true);
    await panel.click('.cell[data-i="2"]');
    assert.equal(await panel.getAttribute('.cell[data-i="2"]', "aria-pressed"), "true");
    assert.equal(await panel.textContent("#markCount"), "1 circled — only these are kept");
    // The handover is used once.
    assert.deepEqual(await worker.evaluate(() => chrome.storage.session.get(["pendingUrl"])), {});
    assert.deepEqual(problems, []);
  } finally {
    await popup.close();
    await panel?.close();
    await server.stop();
  }
});

test("the popup and the side panel say when a restart cut a keep short", { skip }, async () => {
  const server = await startServer();
  await useServer(server.base);
  try {
    for (const surface of ["popup", "sidepanel"]) {
      const { page, problems } = await openSurface(surface);
      try {
        await paste(page, "https://www.youtube.com/watch?v=abc123abc12");
        await page.waitForSelector("#stage .print-title");
        await page.click("#dlBtn");
        await page.waitForFunction(() => /^Downloading/.test(document.getElementById("jobStatus").textContent));
        await server.restart();
        await page.waitForFunction(() => document.getElementById("jobStatus").textContent
          === "offtube restarted, so this keep stopped. Keep it again.", null, { timeout: 10000 });
        assert.equal(await page.isVisible("#cancelBtn"), false, surface);
        assert.deepEqual(problems.filter((p) => !/ERR_CONNECTION_REFUSED|status of 404/.test(p)), [], surface);
      } finally {
        await page.close();
      }
    }
  } finally {
    await server.stop();
  }
});

test("the side panel shows the newest kept files and the library's size", { skip }, async () => {
  const names = Array.from({ length: 15 }, (_, age) => `pin_${String(14 - age).padStart(2, "0")}.jpg`);
  const server = await startServer(names);
  await useServer(server.base);
  const { page, problems } = await openSurface("sidepanel");
  try {
    await page.waitForSelector(".kept-cell");
    assert.equal(await page.$$eval(".kept-cell", (els) => els.length), 12);
    assert.equal(await page.textContent(".kept-name"), "pin_14.jpg");
    assert.match(await page.textContent("#filesCount"), /^15( · |$)/);
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
    await server.stop();
  }
});

test("the popup and the side panel work from the keyboard alone, with focus always shown", { skip }, async () => {
  const server = await startServer();
  await useServer(server.base);
  let { page, problems } = await openSurface("popup");
  try {
    // The popup opens with focus in its link field when the tab has nothing.
    await page.waitForFunction(() => document.activeElement?.id === "url");
    await page.keyboard.type("https://www.youtube.com/watch?v=abc123abc12");
    await page.keyboard.press("Enter");
    await page.waitForSelector("#stage .print-title");
    // The link field is the popup's last control: Keep and the formats
    // are above it.
    await tabTo(page, "#dlBtn", { back: true });
    await tabTo(page, 'input[name="q"]:checked', { back: true });
    await page.keyboard.press("ArrowRight");
    assert.equal(await page.isChecked('input[name="q"][value="720"]'), true);
    await tabTo(page, "#dlBtn");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => /^Kept /.test(document.getElementById("jobStatus").textContent), null, { timeout: 15000 });
    assert.deepEqual(problems, []);
    await page.close();

    ({ page, problems } = await openSurface("sidepanel"));
    await page.waitForSelector(".kept-cell");
    await tabTo(page, "#url");
    await page.keyboard.type("https://www.youtube.com/playlist?list=PLabc");
    await page.keyboard.press("Enter");
    await page.waitForSelector("#stage .cell");
    await tabTo(page, '.cell[data-i="3"]', { back: true });  // the sheet is above the link field
    await page.keyboard.press("Enter");
    assert.equal(await page.getAttribute('.cell[data-i="3"]', "aria-pressed"), "true");
    await tabTo(page, "#discTrim > summary");
    await page.keyboard.press("Enter");
    assert.equal(await page.evaluate(() => document.getElementById("discTrim").open), true);
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
    await server.stop();
  }
});

test("popup and side panel keep every word in view with WCAG text spacing (1.4.12)", { skip }, async () => {
  const server = await startServer(["Song [b].mp3", "pin_1.jpg", "Loop [c].gif"]);
  await useServer(server.base);
  const found = [];
  const check = async (page, state) => {
    for (const loss of await textSpacingLosses(page)) found.push(`${state}: ${loss}`);
  };
  let { page, problems } = await openSurface("popup");
  try {
    await page.waitForSelector("#linkForm:not(.hidden)");
    await check(page, "popup, nothing on this tab");
    await paste(page, "https://www.youtube.com/watch?v=abc123abc12");
    await page.waitForSelector("#stage .print-title");
    await check(page, "popup, print");
    assert.deepEqual(problems, []);
    await page.close();

    ({ page, problems } = await openSurface("sidepanel"));
    await page.waitForSelector(".kept-cell");
    await check(page, "side panel, library");
    await paste(page, "https://www.youtube.com/watch?v=abc123abc12");
    await page.waitForSelector("#stage .print-title");
    await page.evaluate(() => { for (const note of document.querySelectorAll("details")) note.open = true; });
    await check(page, "side panel, print with every note open");
    await page.click("#changeLink");
    await paste(page, "https://www.youtube.com/playlist?list=PLabc");
    await page.waitForSelector("#stage .cell");
    await page.click('.cell[data-i="1"]');
    await check(page, "side panel, contact sheet");
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
    await server.stop();
  }
  assert.deepEqual(found, []);
});

test("popup and side panel meet WCAG 2.2 AA in light and dark (axe-core)", { skip: skipAudit }, async () => {
  const server = await startServer(["Song [b].mp3", "pin_1.jpg", "Loop [c].gif"]);
  const found = [];
  const audit = async (page, state) => {
    for (const violation of await axeViolations(page)) found.push(`${state} ${violation}`);
  };
  try {
    for (const colorScheme of ["light", "dark"]) {
      await useServer(`http://127.0.0.1:${await freePort()}`);
      let { page } = await openSurface("popup", colorScheme);
      await page.waitForFunction(() => document.getElementById("stage").textContent.includes("offtube isn’t running."));
      await audit(page, `${colorScheme}/popup, server off`);
      await page.close();

      await useServer(server.base);
      ({ page } = await openSurface("popup", colorScheme));
      await page.waitForSelector("#linkForm:not(.hidden)");
      await audit(page, `${colorScheme}/popup, nothing on this tab`);
      await paste(page, "https://www.youtube.com/watch?v=abc123abc12");
      await page.waitForSelector("#stage .print-title");
      await audit(page, `${colorScheme}/popup, print`);
      await keepAndWait(page);
      await audit(page, `${colorScheme}/popup, kept`);
      await page.close();

      ({ page } = await openSurface("sidepanel", colorScheme));
      await page.waitForSelector(".kept-cell");
      await paste(page, "https://www.youtube.com/playlist?list=PLabc");
      await page.waitForSelector("#stage .cell");
      await page.click('.cell[data-i="1"]');
      await page.evaluate(() => { for (const note of document.querySelectorAll("details")) note.open = true; });
      await audit(page, `${colorScheme}/side panel, contact sheet with every note open`);
      await page.close();
    }
  } finally {
    await server.stop();
  }
  assert.deepEqual(found, []);
});
