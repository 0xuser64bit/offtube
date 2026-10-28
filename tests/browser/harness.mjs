// Shared by the browser suites: the stub server, optional dependencies, the
// axe-core audit and a private X server. Playwright, axe-core and Xvfb are
// optional unless OFFTUBE_BROWSER_TESTS=required (as in CI), so a missing
// one can't pass silently there.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const root = fileURLToPath(new URL("../..", import.meta.url));
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
export const playwright = optional(() => require("playwright"));
const axePath = optional(() => require.resolve("axe-core/axe.min.js"));
export const skip = playwright ? false : "Playwright is not installed";
export const skipAudit = skip || (!axePath && "axe-core is not installed");
// Real input (xinput.py) needs a private X server, so it never types into
// a developer's own screen: Xvfb, which only Linux has.
const xvfb = process.platform === "linux" && !spawnSync("Xvfb", ["-help"]).error;
export const skipRealInput = skip || (!xvfb && !required && "Xvfb is not installed");
export const python = process.env.PYTHON
  || (existsSync(path.join(root, ".venv/bin/python")) ? path.join(root, ".venv/bin/python") : "python3");

export function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

// A private X server for a headed browser and xinput.py, with the display
// number Xvfb picked once it is ready for clients.
export function startXServer() {
  return new Promise((resolve, reject) => {
    const server = spawn("Xvfb", ["-displayfd", "3", "-screen", "0", "1280x1024x24", "-nolisten", "tcp"],
      { stdio: ["ignore", "ignore", "pipe", "pipe"] });
    let number = "";
    let errors = "";
    server.stderr.on("data", (chunk) => { errors += chunk; });
    server.stdio[3].on("data", (chunk) => {
      number += chunk;
      if (number.endsWith("\n")) resolve({ display: `:${number.trim()}`, stop: () => server.kill() });
    });
    server.once("error", reject);
    server.once("exit", (code) => reject(new Error(`Xvfb exited with ${code}:\n${errors}`)));
  });
}

// The real server, a stubbed yt-dlp, and its own downloads/ holding `files`
// (newest first).
export async function startServer(files = []) {
  const data = mkdtempSync(path.join(tmpdir(), "offtube-browser-"));
  mkdirSync(path.join(data, "downloads"));
  files.forEach((name, age) => {
    const file = path.join(data, "downloads", name);
    writeFileSync(file, "x");
    utimesSync(file, 1_700_000_000 - age * 60, 1_700_000_000 - age * 60);
  });
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  let proc;
  let output = "";
  const boot = async () => {
    proc = spawn(python, [path.join(root, "tests/browser/stub_server.py"), root, data],
      { env: { ...process.env, PORT: String(port) }, stdio: ["ignore", "pipe", "pipe"] });
    proc.stdout.on("data", (chunk) => { output += chunk; });
    proc.stderr.on("data", (chunk) => { output += chunk; });
    for (const deadline = Date.now() + 20000; ;) {
      try {
        if ((await fetch(`${base}/api/health`)).ok) return;
      } catch { /* not listening yet */ }
      if (proc.exitCode !== null || Date.now() > deadline) throw new Error(`The stub server did not start:\n${output}`);
      await new Promise((resolve) => { setTimeout(resolve, 100); });
    }
  };
  const halt = async () => {
    if (proc.exitCode === null) {
      const exited = new Promise((resolve) => { proc.once("exit", resolve); });
      proc.kill("SIGTERM");
      await exited;
    }
  };
  await boot();
  return {
    base,
    // Stopped and started again on the same port and downloads/, as a
    // restart leaves things: kept files stay, jobs are gone. `whileDown`
    // runs in between.
    async restart(whileDown) {
      await halt();
      await whileDown?.();
      await boot();
    },
    async stop() {
      await halt();
      rmSync(data, { recursive: true, force: true });
    },
  };
}

// Page errors and console errors, which every test expects to stay empty.
export function watchProblems(page) {
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") problems.push(message.text()); });
  return problems;
}

let axeSource;
// WCAG 2.2 A/AA violations on the page as "rule: targets". axe is evaluated
// through the DevTools protocol, which no page CSP refuses.
export async function axeViolations(page) {
  axeSource ??= readFileSync(axePath, "utf8");
  // A newline first: the bundle may end in a line comment.
  if (!(await page.evaluate(() => Boolean(window.axe)))) await page.evaluate(`${axeSource}\nvoid 0;`);
  return page.evaluate(async () => (await window.axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
  })).violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`));
}

// What WCAG 1.4.12's text spacing costs the page as it stands: lines of
// text that a box now cuts off or that now run into other text, and any
// sideways scrolling. Decoration (pencil circles, frame numbers) is not, and
// text that was already cut off or overlapping is the design, as is text
// that ends in an ellipsis (file names and links, whose whole text is in a
// title or a field) and text passing under the pinned paste bar. The
// spacing goes in as a constructed stylesheet, which the pages' CSP allows,
// as it does a reader's own extension. Measured in the pages' own font:
// text shows in a fallback until it loads.
export async function textSpacingLosses(page) {
  return page.evaluate(async () => {
    await document.fonts.ready;
    const clips = (el) => /hidden|clip/.test(`${getComputedStyle(el).overflowX} ${getComputedStyle(el).overflowY}`);
    const scrolls = (el) => /hidden|clip|auto|scroll/.test(`${getComputedStyle(el).overflowX} ${getComputedStyle(el).overflowY}`);
    // The paste bar and the keep panel stay put while the page passes under
    // them; text in one and text outside it may meet in passing.
    const pinnedIn = (el) => {
      for (let box = el; box; box = box.parentElement) if (/fixed|sticky/.test(getComputedStyle(box).position)) return box;
      return null;
    };
    // The part of a line that shows: what its scrolling and clipping boxes
    // leave of it.
    const shown = (el, rect) => {
      let { left, top, right, bottom } = rect;
      for (let box = el; box && box !== document.body; box = box.parentElement) {
        if (!scrolls(box)) continue;
        const edge = box.getBoundingClientRect();
        left = Math.max(left, edge.left); top = Math.max(top, edge.top);
        right = Math.min(right, edge.right); bottom = Math.min(bottom, edge.bottom);
      }
      return { left, top, right, bottom };
    };
    const truncates = (el) => getComputedStyle(el).textOverflow === "ellipsis"
      || getComputedStyle(el).getPropertyValue("-webkit-line-clamp") !== "none";
    const describe = (el) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${[...el.classList].map((c) => `.${c}`).join("")}`
      + ` "${el.textContent.trim().replace(/\s+/g, " ").slice(0, 40)}"`;
    const problems = () => {
      const lines = [];
      const range = document.createRange();
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const el = node.parentElement;
        if (!node.textContent.trim() || el.closest("svg, script, style, .visually-hidden, [aria-hidden='true']")) continue;
        if (getComputedStyle(el).visibility === "hidden") continue;
        // A closed note's body is laid out but not shown; its summary is.
        const closed = el.closest("details:not([open])");
        if (closed && el.closest("summary")?.parentElement !== closed) continue;
        range.selectNodeContents(node);
        for (const rect of range.getClientRects()) {
          if (rect.width > 1 && rect.height > 1) lines.push({ el, rect, seen: shown(el, rect), pinned: pinnedIn(el) });
        }
      }
      const found = new Map();
      for (const { el, rect } of lines) {
        for (let box = el; box && box !== document.body; box = box.parentElement) {
          if (truncates(box)) break;
          if (!clips(box)) continue;
          const edge = box.getBoundingClientRect();
          if (rect.left < edge.left - 1 || rect.right > edge.right + 1 || rect.top < edge.top - 1 || rect.bottom > edge.bottom + 1) {
            found.set(`clip ${describe(el)}`, `${describe(el)} is cut off by ${describe(box)}`);
            break;
          }
        }
      }
      for (let i = 0; i < lines.length; i++) {
        for (let j = i + 1; j < lines.length; j++) {
          const a = lines[i], b = lines[j];
          if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el) || a.pinned !== b.pinned) continue;
          const across = Math.min(a.seen.right, b.seen.right) - Math.max(a.seen.left, b.seen.left);
          const down = Math.min(a.seen.bottom, b.seen.bottom) - Math.max(a.seen.top, b.seen.top);
          if (across > 2 && down > 2) {
            const [first, second] = [describe(a.el), describe(b.el)].sort();
            found.set(`overlap ${first} ${second}`, `${first} runs into ${second}`);
          }
        }
      }
      return found;
    };
    const before = problems();
    const spacing = new CSSStyleSheet();
    spacing.replaceSync(`* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }
      p { margin-bottom: 2em !important; }`);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, spacing];
    try {
      const losses = [...problems()].filter(([key]) => !before.has(key)).map(([, loss]) => loss);
      const wide = document.scrollingElement.scrollWidth;
      if (wide > innerWidth) losses.push(`the page scrolls sideways (${wide} px in ${innerWidth})`);
      return losses;
    } finally {
      document.adoptedStyleSheets = document.adoptedStyleSheets.filter((sheet) => sheet !== spacing);
    }
  });
}

// Press Tab (Shift+Tab with `back`) until `selector` has focus, requiring
// a visible focus ring at every stop on the way: on the control itself,
// its radio's label text or label, a print's frame, or the box drawn
// around a link field.
export async function tabTo(page, selector, { most = 40, back = false } = {}) {
  for (let i = 0; i < most; i++) {
    await page.keyboard.press(back ? "Shift+Tab" : "Tab");
    const stop = await page.evaluate((target) => {
      const el = document.activeElement;
      const parts = [el, el.matches("input") && el.nextElementSibling, el.closest(".fmt"),
        el.querySelector(".cell-img, .frame"), el.closest(".paste, .link-edit")];
      // An outline counts only where it can be seen: a radio drawn as a
      // chip is itself transparent, so its ring must be on the chip.
      const shows = (part) => {
        const style = getComputedStyle(part);
        const box = part.getBoundingClientRect();
        return style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0
          && Number(style.opacity) > 0 && box.width > 1 && box.height > 1;
      };
      return { here: el.matches(target), ringed: parts.filter(Boolean).some(shows), what: el.outerHTML.slice(0, 80) };
    }, selector);
    if (!stop.ringed) throw new Error(`no visible focus on ${stop.what}`);
    if (stop.here) return;
  }
  throw new Error(`Tab never reached ${selector}`);
}
