// The web desk in a real browser: node --test tests/browser/desk.test.mjs
// Needs Playwright with Chromium, and axe-core for the accessibility audit
// (see harness.mjs for when they may be missing).
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { axeViolations, playwright, skip, skipAudit, startServer, tabTo, textSpacingLosses, watchProblems } from "./harness.mjs";

let browser;
before(async () => { if (playwright) browser = await playwright.chromium.launch(); });
after(async () => { await browser?.close(); });

async function openDesk(server, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, ...options });
  const page = await context.newPage();
  const problems = watchProblems(page);
  await page.goto(`${server.base}/`);
  return { page, problems, close: () => context.close() };
}

async function preview(page, url) {
  await page.fill("#url", url);
  await page.press("#url", "Enter");
}

async function keepAndWait(page) {
  await page.click("#dlBtn");
  await page.waitForFunction(() => /^Kept /.test(document.getElementById("jobStatus").textContent), null, { timeout: 15000 });
}

test("keeps a video from a pasted link to the Kept sheet", { skip }, async () => {
  const server = await startServer();
  const { page, problems, close } = await openDesk(server);
  try {
    await preview(page, "https://www.youtube.com/watch?v=abc123abc12");
    await page.waitForSelector("#printFig .print-title");
    assert.equal(await page.textContent("#printFig .print-title"), "A test video");
    await keepAndWait(page);
    await page.waitForSelector(".kept-cell");
    assert.deepEqual(await page.$$eval(".kept-name", (els) => els.map((el) => el.textContent)), ["A test video [abc123abc12].mp4"]);
    await page.waitForSelector('.strip-frame[aria-label$=": Kept"]');
    assert.deepEqual(problems, []);
  } finally {
    await close();
    await server.stop();
  }
});

test("circling prints on a collection keeps only those", { skip }, async () => {
  const server = await startServer();
  const { page, problems, close } = await openDesk(server);
  try {
    await preview(page, "https://www.youtube.com/playlist?list=PLabc");
    await page.waitForSelector("#printFig.sheet .cell");
    await page.click('.cell[data-i="2"]');
    assert.equal(await page.getAttribute('.cell[data-i="2"]', "aria-pressed"), "true");
    assert.equal(await page.textContent("#markCount"), "1 circled — only these are kept");
    assert.equal(await page.textContent("#plState"), "1 circled");
    assert.deepEqual(problems, []);
  } finally {
    await close();
    await server.stop();
  }
});

test("a keep cut short by a restart says so instead of retrying forever", { skip }, async () => {
  const server = await startServer();
  const { page, problems, close } = await openDesk(server);
  try {
    await preview(page, "https://www.youtube.com/watch?v=abc123abc12");
    await page.waitForSelector("#printFig .print-title");
    await page.click("#dlBtn");
    await page.waitForFunction(() => /^Downloading/.test(document.getElementById("jobStatus").textContent));
    // Jobs live in memory: the restarted server doesn't know this one.
    await server.restart();
    await page.waitForFunction(() => document.getElementById("jobStatus").textContent
      === "offtube restarted, so this keep stopped. Keep it again.", null, { timeout: 10000 });
    assert.equal(await page.isVisible("#cancelBtn"), false);
    assert.equal(await page.textContent("#printEdge"), "Failed");
    const asked = [];
    page.on("request", (request) => { if (request.url().includes("/api/job?")) asked.push(request.url()); });
    await page.waitForTimeout(1500);
    assert.deepEqual(asked, [], "it kept asking for the job");
    // Only the requests that failed meanwhile may be logged.
    assert.deepEqual(problems.filter((p) => !/ERR_CONNECTION_REFUSED|status of 404/.test(p)), []);
  } finally {
    await close();
    await server.stop();
  }
});

test("the library reaches every kept file through the server", { skip }, async () => {
  const names = Array.from({ length: 70 }, (_, age) => `pin_${String(69 - age).padStart(4, "0")}.jpg`);
  const server = await startServer(names);
  const { page, problems, close } = await openDesk(server);
  const rows = () => page.$$eval(".kept-cell", (els) => els.length);
  try {
    await page.waitForSelector(".kept-cell");
    assert.match(await page.textContent("#footFiles"), /^70 files/);
    assert.equal(await rows(), 60);
    await page.click("#moreFiles");
    await page.waitForFunction(() => document.querySelectorAll(".kept-cell").length === 70);
    await page.fill("#fileSearch", "pin_0000");
    await page.waitForFunction(() => document.querySelectorAll(".kept-cell").length === 1);
    assert.equal(await page.textContent(".kept-name"), "pin_0000.jpg");
    assert.deepEqual(problems, []);
  } finally {
    await close();
    await server.stop();
  }
});

test("a screen reader hears what was found and each change of state, not every tick", { skip }, async () => {
  const server = await startServer();
  const { page, close } = await openDesk(server);
  try {
    await page.evaluate(() => {
      window.spoken = [];
      new MutationObserver(() => window.spoken.push(document.getElementById("announce").textContent))
        .observe(document.getElementById("announce"), { childList: true, characterData: true, subtree: true });
      // Nothing else on the desk may be a live region: those redraw every poll.
      window.liveRegions = [...document.querySelectorAll("[aria-live]")].map((el) => el.id || el.className);
    });
    await preview(page, "https://www.youtube.com/watch?v=abc123abc12");
    await page.waitForSelector("#printFig .print-title");
    await keepAndWait(page);
    const spoken = await page.evaluate(() => window.spoken);
    assert.deepEqual(await page.evaluate(() => window.liveRegions), []);
    assert.equal(spoken[0], "Reading the link…");
    assert.equal(spoken[1], "YouTube video ready: A test video.");
    assert.equal(spoken.at(-1), "Kept A test video [abc123abc12].mp4.");
    // About ten progress ticks went by; only changes of state were spoken.
    assert.ok(spoken.length <= 5, spoken.join(" | "));
    assert.equal(new Set(spoken).size, spoken.length, spoken.join(" | "));
  } finally {
    await close();
    await server.stop();
  }
});

test("a keyboard alone can preview, circle and keep, with focus always shown", { skip }, async () => {
  const server = await startServer();
  const { page, problems, close } = await openDesk(server);
  try {
    await tabTo(page, "#url", { most: 5 });
    await page.keyboard.type("https://www.youtube.com/playlist?list=PLabc");
    await page.keyboard.press("Enter");
    await page.waitForSelector("#printFig.sheet .cell");
    await tabTo(page, '.cell[data-i="2"]');
    await page.keyboard.press("Space");
    assert.equal(await page.getAttribute('.cell[data-i="2"]', "aria-pressed"), "true");
    assert.equal(await page.textContent("#markCount"), "1 circled — only these are kept");
    await tabTo(page, "#dlBtn");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => /^Kept /.test(document.getElementById("jobStatus").textContent), null, { timeout: 15000 });
    assert.deepEqual(problems, []);
  } finally {
    await close();
    await server.stop();
  }
});

test("reflows at 320 CSS px without scrolling sideways (WCAG 1.4.10)", { skip }, async () => {
  // 320 px is also a 1280 px window zoomed to 400%.
  const server = await startServer(["Song [b].mp3", "pin_1.jpg", "Loop [c].gif",
    "A file name long enough to need wrapping in a narrow column of the sheet [abcdefghijk].mp4"]);
  const { page, problems, close } = await openDesk(server, { viewport: { width: 320, height: 800 } });
  // In the desk's own font, which a fresh page shows only once it loads.
  const sideways = () => page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.querySelectorAll("body *")]
      .filter((el) => { const box = el.getBoundingClientRect(); return box.width > 0 && box.right > innerWidth + 1; })
      .map((el) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${el.getAttribute("aria-label") ? ` "${el.getAttribute("aria-label")}"` : ""}`);
  });
  try {
    await page.waitForSelector(".kept-cell");
    assert.deepEqual(await sideways(), [], "blank desk");
    await preview(page, "https://www.youtube.com/watch?v=abc123abc12");
    await page.waitForSelector("#printFig .print-title");
    await page.evaluate(() => { for (const note of document.querySelectorAll("details.note")) note.open = true; });
    assert.deepEqual(await sideways(), [], "print with every note open");
    await preview(page, "https://www.youtube.com/playlist?list=PLabc");
    await page.waitForSelector("#printFig.sheet .cell");
    assert.deepEqual(await sideways(), [], "contact sheet");
    assert.equal(await page.evaluate(() => document.scrollingElement.scrollWidth), 320);
    assert.deepEqual(problems, []);
  } finally {
    await close();
    await server.stop();
  }
});

test("keeps every word in view with WCAG text spacing, at 1280 and 320 CSS px (1.4.12)", { skip }, async () => {
  const server = await startServer(["Song [b].mp3", "pin_1.jpg", "Loop [c].gif",
    "A file name long enough to need wrapping in a narrow column of the sheet [abcdefghijk].mp4"]);
  const found = [];
  try {
    for (const width of [1280, 320]) {
      const { page, problems, close } = await openDesk(server, { viewport: { width, height: 900 } });
      const check = async (state) => {
        for (const loss of await textSpacingLosses(page)) found.push(`${width} px/${state}: ${loss}`);
      };
      try {
        await page.waitForSelector(".kept-cell");
        await check("blank");
        await preview(page, "https://www.youtube.com/watch?v=abc123abc12");
        await page.waitForSelector("#printFig .print-title");
        await page.evaluate(() => { for (const note of document.querySelectorAll("details.note")) note.open = true; });
        await check("print with every note open");
        await keepAndWait(page);
        await page.evaluate(() => { document.getElementById("logBox").open = true; });
        await check("kept, log open");
        await preview(page, "https://www.youtube.com/playlist?list=PLabc");
        await page.waitForSelector("#printFig.sheet .cell");
        await page.click('.cell[data-i="1"]');
        await check("contact sheet");
        assert.deepEqual(problems, []);
      } finally {
        await close();
      }
    }
  } finally {
    await server.stop();
  }
  assert.deepEqual(found, []);
});

test("meets WCAG 2.2 AA in light and dark (axe-core)", { skip: skipAudit }, async () => {
  // Videos come from the keep step: the stub's clip is real media.
  const server = await startServer(["Song [b].mp3", "pin_1.jpg", "Loop [c].gif", "notes.srt"]);
  const found = [];
  try {
    for (const colorScheme of ["light", "dark"]) {
      const { page, close } = await openDesk(server, { colorScheme });
      const audit = async (state) => {
        for (const violation of await axeViolations(page)) found.push(`${colorScheme}/${state} ${violation}`);
      };
      try {
        await page.waitForSelector(".kept-cell");
        await audit("blank");
        await preview(page, "https://www.youtube.com/watch?v=abc123abc12");
        await page.waitForSelector("#printFig .print-title");
        await page.evaluate(() => { for (const note of document.querySelectorAll("details.note")) note.open = true; });
        await audit("print with every note open");
        await keepAndWait(page);
        await page.evaluate(() => { document.getElementById("logBox").open = true; });
        await audit("kept, log open");
        await preview(page, "https://www.youtube.com/playlist?list=PLabc");
        await page.waitForSelector("#printFig.sheet .cell");
        await page.click('.cell[data-i="1"]');
        await audit("contact sheet");
        await preview(page, "https://example.com/not-a-site");
        await page.waitForSelector("#infoError:not(.hidden)");
        await audit("refused link");
      } finally {
        await close();
      }
    }
  } finally {
    await server.stop();
  }
  assert.deepEqual(found, []);
});
