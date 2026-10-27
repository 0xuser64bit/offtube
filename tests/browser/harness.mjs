// Shared by the browser suites: the stub server, optional dependencies and
// the axe-core audit. Playwright and axe-core are optional unless
// OFFTUBE_BROWSER_TESTS=required (as in CI), so a missing browser can't
// pass silently there.
import { spawn } from "node:child_process";
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
const python = process.env.PYTHON
  || (existsSync(path.join(root, ".venv/bin/python")) ? path.join(root, ".venv/bin/python") : "python3");

export function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
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
