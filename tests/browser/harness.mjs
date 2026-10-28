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
