// Run with: node --test tests/test_web_gif.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8");
const script = readFileSync(new URL("../web/app.js", import.meta.url), "utf8")
  .replace(/restorePrefs\(\);\nsyncNotes\(\);\nwireLoupe\(\);[\s\S]*$/, "");

// A small DOM fixture exercises the shipped handlers without a browser dependency.
function desk() {
  const elements = new Map();
  const groups = new Map();
  const saved = new Map();
  const timers = new Map();  // pending setTimeout callbacks, never run on their own
  let timerId = 0;
  class Element {
    constructor(id = "") {
      this.id = id;
      this.value = "";
      this.dataset = {};
      this.listeners = {};
      this.attrs = {};
      this.textContent = "";
      this.selectedOptions = [{ textContent: "Chrome" }];
      this.style = { setProperty() {} };
      const classes = new Set();
      this.classList = {
        add: (...names) => names.forEach((name) => classes.add(name)),
        remove: (...names) => names.forEach((name) => classes.delete(name)),
        contains: (name) => classes.has(name),
        toggle: (name, on = !classes.has(name)) => { if (on) classes.add(name); else classes.delete(name); },
      };
    }
    set checked(on) { this._checked = on; if (on && this.name) for (const radio of groups.get(this.name)) if (radio !== this) radio._checked = false; }
    get checked() { return this._checked || false; }
    set innerHTML(value) {
      this._html = value;
      if (this.id === "qualities") {
        groups.set("q", []);
        for (const match of value.matchAll(/name="q" value="([^"]+)"( checked)?/g)) radio("q", match[1], Boolean(match[2]));
      }
    }
    get innerHTML() { return this._html || ""; }
    setAttribute(name, value) { this.attrs[name] = value; }
    getAttribute(name) { return this.attrs[name]; }
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
    async dispatch(type, detail = {}) { for (const handler of this.listeners[type] || []) await handler({ target: this, preventDefault() {}, ...detail }); }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    focus() { this.focused = true; }
  }
  function radio(name, value, checked = false) {
    const r = new Element();
    r.name = name;
    r.value = value;
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(r);
    r.checked = checked;
    return r;
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], new Element(match[1]));
  for (const match of html.matchAll(/<input[^>]*name="([^"]+)"[^>]*value="([^"]+)"([^>]*)>/g)) radio(match[1], match[2], match[3].includes("checked"));
  const queryAll = (selector) => selector.split(",").flatMap((part) => {
    const name = part.match(/input\[name="([^"]+)"\]/)?.[1];
    if (!name) return [];
    return (groups.get(name) || []).filter((r) => (!part.includes(":checked") || r.checked) && (!part.includes("[value=") || r.value === part.match(/\[value="([^"]+)"\]/)[1]));
  });
  const document = {
    getElementById: (id) => elements.get(id) || null,
    querySelector: (selector) => queryAll(selector)[0] || null,
    querySelectorAll: queryAll,
    body: { dataset: {} },
    addEventListener() {},
  };
  elements.get("jobStatus").parentElement = new Element();
  elements.get("gifSize").value = "480";
  elements.get("gifFps").value = "15";
  elements.get("gifLoop").checked = true;
  elements.get("subLang").value = "en";
  elements.get("gifResult").classList.add("hidden");
  const context = vm.createContext({
    document, window: {}, CSS: { escape: String }, Intl,
    localStorage: { getItem: (key) => saved.get(key) || null, setItem: (key, value) => saved.set(key, value) },
    setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout: (id) => timers.delete(id), setInterval: () => 1, clearInterval() {},
    fetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  vm.runInContext(script, context);
  const run = (source) => vm.runInContext(source, context);
  const choose = async (name, value) => { const r = groups.get(name).find((r) => r.value === value); r.checked = true; await r.dispatch("change"); };
  const info = (value) => {
    elements.get("url").value = "https://example.com/video";
    run(`state.info = ${JSON.stringify(value)}; state.infoUrl = $("url").value; renderFormats();`);
  };
  return { elements, context, run, choose, info, saved, timers };
}

test("GIF keeps whole videos or fractional timeframes with its own settings and no subtitles", async () => {
  const ui = desk();
  ui.info({ type: "video", media: "video", duration: 120 });
  await ui.choose("otype", "gif");
  ui.elements.get("subs").checked = true;
  assert.deepEqual(JSON.parse(ui.run("JSON.stringify(payload())")), {
    url: "https://example.com/video", quality: "gif", subtitles: false,
    gif_size: 480, gif_fps: 15, gif_loop: true, auto_subs: true, sub_lang: "en",
    clip_from: "", clip_to: "", playlist_mode: "single", playlist_start: "", playlist_end: "", playlist_items: "",
    cookies_mode: "none", cookies_text: "", cookies_browser: "", title: "", thumbnail: "",
  });
  assert.match(ui.elements.get("gifSummary").textContent, /Whole video · 120 seconds · about 1800 frames/);
  ui.elements.get("clipFrom").value = "1:00.25";
  ui.elements.get("clipTo").value = "1:05.5";
  ui.elements.get("gifLoop").checked = false;
  ui.run("syncNotes()");
  assert.equal(ui.run("trimProblem(payload())"), "");
  assert.match(ui.elements.get("gifSummary").textContent, /Selected timeframe · 5.25 seconds · about 79 frames · plays once/);
  assert.equal(ui.run("payload().gif_loop"), false);
  ui.elements.get("clipTo").value = "1:01.25";
  ui.run("syncNotes()");
  assert.match(ui.elements.get("gifSummary").textContent, /Selected timeframe · 1 second · about 15 frames/);
});

test("source restrictions preserve the preferred GIF output and separate video/audio quality", async () => {
  const ui = desk();
  ui.info({ type: "video", media: "video" });
  await ui.choose("q", "720");
  await ui.elements.get("qualities").dispatch("change");
  await ui.choose("otype", "gif");
  ui.info({ type: "video", media: "audio" });
  assert.equal(ui.run("outputType()"), "audio");
  ui.run("savePrefs()");
  assert.equal(JSON.parse(ui.saved.get("offtube.prefs")).otype, "gif");
  ui.info({ type: "gallery", media: "image" });
  assert.equal(ui.run("outputType()"), "video");
  ui.info({ type: "video", media: "video", is_live: true });
  assert.equal(ui.run("outputType()"), "video");
  ui.info({ type: "video", media: "video", live_status: "is_upcoming" });
  assert.equal(ui.run("outputType()"), "video");
  ui.info({ type: "video", media: "video", live_status: "was_live" });
  assert.equal(ui.run("outputType()"), "gif");
  await ui.choose("otype", "video");
  assert.equal(ui.run("selectedQuality()"), "720");
});

test("GIF estimates handle unknown lengths, collections, invalid times, and end-of-video clipping", async () => {
  const ui = desk();
  ui.info({ type: "video", media: "video", duration: 0 });
  await ui.choose("otype", "gif");
  assert.match(ui.elements.get("gifSummary").textContent, /Whole video · 15 fps/);
  ui.elements.get("clipFrom").value = "1e3";
  ui.run("syncNotes()");
  assert.match(ui.elements.get("gifSummary").textContent, /valid timeframe/);
  ui.elements.get("clipFrom").value = "";
  ui.elements.get("clipTo").value = "0";
  assert.match(ui.run("trimProblem(payload())"), /after/);
  ui.elements.get("clipTo").value = "";
  ui.info({ type: "playlist", media: "video", duration: 1000 });
  await ui.choose("plmode", "playlist");
  assert.match(ui.elements.get("gifSummary").textContent, /One GIF per video/);
  assert.doesNotMatch(ui.elements.get("gifSummary").textContent, /1000 seconds/);
  ui.info({ type: "video", media: "video", duration: 3.5 });
  await ui.choose("plmode", "single");
  await ui.elements.get("gifFiveSeconds").dispatch("click");
  assert.equal(ui.run("payload().clip_to"), "3.5");
  assert.match(ui.elements.get("gifSummary").textContent, /3.5 seconds/);
});

test("GIF tallies count videos instead of source files", async () => {
  const ui = desk();
  ui.run('state.preferredOutput = "gif"');
  ui.info({ type: "gallery", media: "mixed", count: 1 });
  assert.equal(ui.elements.get("tally").innerHTML, "<b>1 GIF per video</b> · images are skipped");
  ui.info({ type: "playlist", media: "video", count: 4 });
  await ui.choose("plmode", "playlist");
  ui.run("syncTally()");
  assert.equal(ui.elements.get("tally").innerHTML, "<b>4 items</b> · one GIF per video");
  ui.run("state.marks.add(1); syncTally()");
  assert.equal(ui.elements.get("tally").innerHTML, "<b>1 circled item</b> · one GIF per video");
});

test("switching sources detaches old GIF jobs and ignores late inspection results", async () => {
  const ui = desk();
  ui.info({ type: "video", media: "video" });
  ui.run('state.printJobId = "old-gif"');
  let respond;
  ui.context.fetch = () => new Promise((resolve) => { respond = resolve; });
  const pending = ui.run("inspect()");
  ui.elements.get("url").value = "https://example.com/new";
  await ui.elements.get("url").dispatch("input");
  respond({ ok: true, json: async () => ({ info: { type: "video", title: "Old source" } }) });
  await pending;
  assert.equal(ui.run("currentInfo()"), null);
  assert.equal(ui.run("state.printJobId"), null);
  assert.equal(ui.elements.get("fetchBtn").disabled, false);
  ui.run('showJob({id:"old-gif", status:"done", quality:"gif", files:["old.gif"]}, true)');
  assert.equal(ui.elements.get("gifResult").classList.contains("hidden"), true);
});

test("GIFs created without a source print and selected session jobs have a real preview and Save link", () => {
  const ui = desk();
  ui.run('state.printJobId = "gif-job"; showJob({id:"gif-job", status:"done", quality:"gif", files:["A & B.gif"]}, true)');
  assert.equal(ui.elements.get("gifResult").classList.contains("hidden"), false);
  assert.match(ui.elements.get("gifPreview").innerHTML, /src="\/files\/A%20%26%20B.gif"/);
  assert.equal(ui.elements.get("gifSave").href, "/files/A%20%26%20B.gif");
  ui.run('state.printJobId = null; state.previewJobId = "other"; showJob({id:"other", status:"processing", quality:"gif"}, true)');
  ui.run('showJob({id:"other", status:"done", quality:"gif", files:["1.gif", "2.gif"]}, true)');
  assert.match(ui.elements.get("gifReady").textContent, /2 GIFs are ready/);
  ui.run('showJob({id:"other", status:"error", quality:"gif", detail:"Could not make GIF"}, false)');
  assert.equal(ui.elements.get("gifResult").classList.contains("hidden"), true);
});

test("counts reported by a site are printed as text, never markup", async () => {
  const ui = desk();
  ui.info({ type: "playlist", media: "video", count: "<img src=x>" });
  await ui.choose("plmode", "playlist");
  ui.run("syncTally()");
  assert.match(ui.elements.get("tally").innerHTML, /&lt;img src=x&gt; items/);
  ui.info({ type: "gallery", media: "image", count: "9<i>" });
  await ui.choose("plmode", "single");
  ui.run("syncTally()");
  assert.doesNotMatch(ui.elements.get("tally").innerHTML, /<i>/);
});

test("a pasted link previews promptly; typing still waits for a pause", async () => {
  const ui = desk();
  const url = ui.elements.get("url");
  // A browser fires paste, then input once the value has changed.
  url.value = "https://www.youtube.com/watch?v=abc123abc12";
  await url.dispatch("paste");
  await url.dispatch("input", { inputType: "insertFromPaste" });
  assert.deepEqual([...ui.timers.values()].map((t) => t.ms), [150]);
  ui.timers.clear();
  url.value = "https://www.youtube.com/watch?v=abc";
  await url.dispatch("input", { inputType: "insertText" });
  assert.deepEqual([...ui.timers.values()].map((t) => t.ms), [700]);
});
