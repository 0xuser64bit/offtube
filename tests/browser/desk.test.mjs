// The web desk in a real browser: node --test tests/browser/desk.test.mjs
// Needs Playwright with Chromium, and axe-core for the accessibility audit.
// Skipped when they aren't installed, unless OFFTUBE_BROWSER_TESTS=required
// (as in CI), so a missing browser can't pass silently there.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const require = createRequire(import.meta.url);
const required = process.env.OFFTUBE_BROWSER_TESTS === "required";
function optional(resolve) {
  try {
    return resolve();
  } catch (error) {
    if (required) throw error;
    return null;
  }
}
const playwright = optional(() => require("playwright"));
const axePath = optional(() => require.resolve("axe-core/axe.min.js"));
const python = process.env.PYTHON
  || (existsSync(path.join(root, ".venv/bin/python")) ? path.join(root, ".venv/bin/python") : "python3");
const skip = playwright ? false : "Playwright is not installed";

let browser;
before(async () => { if (playwright) browser = await playwright.chromium.launch(); });
after(async () => { await browser?.close(); });

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

// The real server, a stubbed yt-dlp, and its own downloads/ holding `files`
// (newest first).
async function startServer(files = []) {
  const data = mkdtempSync(path.join(tmpdir(), "offtube-browser-"));
  mkdirSync(path.join(data, "downloads"));
  files.forEach((name, age) => {
    const file = path.join(data, "downloads", name);
    writeFileSync(file, "x");
    utimesSync(file, 1_700_000_000 - age * 60, 1_700_000_000 - age * 60);
  });
  const port = await freePort();
  const proc = spawn(python, [path.join(root, "tests/browser/stub_server.py"), root, data],
    { env: { ...process.env, PORT: String(port) }, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  proc.stdout.on("data", (chunk) => { output += chunk; });
  proc.stderr.on("data", (chunk) => { output += chunk; });
  const base = `http://127.0.0.1:${port}`;
  for (const deadline = Date.now() + 20000; ;) {
    try {
      if ((await fetch(`${base}/api/health`)).ok) break;
    } catch { /* not listening yet */ }
    if (proc.exitCode !== null || Date.now() > deadline) throw new Error(`The stub server did not start:\n${output}`);
    await new Promise((resolve) => { setTimeout(resolve, 100); });
  }
  return {
    base,
    async stop() {
      if (proc.exitCode === null) {
        const exited = new Promise((resolve) => { proc.once("exit", resolve); });
        proc.kill("SIGTERM");
        await exited;
      }
      rmSync(data, { recursive: true, force: true });
    },
  };
}

async function openDesk(server, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, ...options });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") problems.push(message.text()); });
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

test("meets WCAG 2.2 AA in light and dark (axe-core)", { skip: skip || (!axePath && "axe-core is not installed") }, async () => {
  const axe = axePath && readFileSync(axePath, "utf8");
  // Videos come from the keep step: the stub's clip is real media.
  const server = await startServer(["Song [b].mp3", "pin_1.jpg", "Loop [c].gif", "notes.srt"]);
  const found = [];
  try {
    for (const colorScheme of ["light", "dark"]) {
      // The page's CSP refuses inline scripts, the audit's included.
      const { page, close } = await openDesk(server, { colorScheme, bypassCSP: true });
      const audit = async (state) => {
        await page.addScriptTag({ content: axe });
        const violations = await page.evaluate(async () => (await window.axe.run(document, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
        })).violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`));
        for (const violation of violations) found.push(`${colorScheme}/${state} ${violation}`);
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
