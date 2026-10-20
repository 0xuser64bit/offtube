// Run with node --test tests/extension_gif.test.js. No browser or network needed.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const plain = (value) => JSON.parse(JSON.stringify(value));

function surface(script, local = {}, session = {}) {
  const nodes = new Map(), chosen = { q: 'best', access: 'none', plmode: 'single' };
  const callbacks = new Map(), opened = [], requests = [];
  const eventSource = () => ({ addListener() {} });
  function node(id) {
    if (nodes.has(id)) return nodes.get(id);
    const classes = new Set();
    const listeners = {};
    const el = {
      value: { gifSize: '480', gifFps: '15', url: 'https://example.com/video', subLang: 'en', cookieBrowserName: 'chrome' }[id] || '',
      checked: id === 'gifLoop', textContent: '', dataset: {}, children: [],
      selectedOptions: [{ textContent: 'Chrome' }], style: { setProperty() {} },
      classList: {
        add(c) { classes.add(c); }, remove(c) { classes.delete(c); }, contains(c) { return classes.has(c); },
        toggle(c, on = !classes.has(c)) { if (on) classes.add(c); else classes.delete(c); },
      },
      addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
      dispatchEvent(event) { for (const fn of listeners[event.type] || []) fn(event); },
      async fire(type) { for (const fn of listeners[type] || []) await fn({ target: el }); },
      setAttribute() {}, querySelector() { return null; }, querySelectorAll() { return []; },
      focus() {}, scrollIntoView() {},
      set innerHTML(html) {
        this.html = html;
        if (id === 'qualities') chosen.q = html.match(/name="q" value="([^"]+)" checked/)?.[1] || 'best';
      },
      get innerHTML() { return this.html || ''; },
    };
    el.parentElement = el;
    nodes.set(id, el);
    return el;
  }
  const document = {
    body: { dataset: {} }, getElementById: node,
    addEventListener(type, fn) { callbacks.set(type, fn); },
    querySelector(selector) {
      const name = selector.match(/name="([^"]+)"/)?.[1];
      if (!name) return null;
      const value = selector.match(/value="([^"]+)"/)?.[1];
      return { value: chosen[name], set checked(on) { if (on && value) chosen[name] = value; } };
    },
    querySelectorAll() { return []; },
  };
  const storageArea = (values) => ({
    async get() { return { ...values }; }, async set(data) { Object.assign(values, plain(data)); },
    async remove(keys) { for (const key of [].concat(keys)) delete values[key]; }, onChanged: eventSource(),
  });
  const chrome = {
    storage: { local: storageArea(local), session: storageArea(session) },
    tabs: { onActivated: eventSource(), onUpdated: eventSource(), async query() { throw Error('Pending links must not be replaced by the active tab'); } },
    action: { async setBadgeText() {}, async setBadgeBackgroundColor() {} },
    windows: { async getLastFocused() { return { id: 1 }; } },
    sidePanel: { async open(value) { opened.push(value); } },
    runtime: { onInstalled: eventSource() }, contextMenus: { onClicked: eventSource() }, commands: { onCommand: eventSource() },
  };
  const context = vm.createContext({
    document, chrome, URL, console, window: { close() {} }, importScripts() {},
    Event: class { constructor(type) { this.type = type; } },
    setTimeout() {}, setInterval() {}, clearInterval() {}, matchMedia: () => ({ matches: false }),
  });
  const run = (code) => vm.runInContext(code, context);
  run(readFileSync(path.join(root, 'extension/shared.js'), 'utf8'));
  const api = run('OFFTUBE');
  api.api = async (_server, route, body) => {
    requests.push({ route, body: plain(body) });
    return route === '/api/info' ? { info: { type: 'video', media: 'video', duration: 60 } } : { job_id: 'job-1' };
  };
  api.apiGet = async () => ({ files: [], jobs: [] });
  api.showHealth = async () => true;
  if (script) run(readFileSync(path.join(root, `extension/${script}`), 'utf8'));
  return { api, run, node, chosen, local, session, opened, requests, boot: () => callbacks.get('DOMContentLoaded')() };
}

test('GIF is offered for finished videos and mixed collections, excluding images, audio and live streams', () => {
  const { api } = surface();
  const hasGif = (info) => api.formatOptions(info, 'video').some((q) => q.id === 'gif');
  assert.equal(hasGif(null), true);
  assert.equal(hasGif({ type: 'video', media: 'video' }), true);
  assert.equal(hasGif({ type: 'gallery', media: 'mixed' }), true);
  for (const info of [
    { media: 'audio' }, { media: 'image', type: 'gallery' }, { media: 'video', is_live: true },
    { media: 'video', is_live: false, live_status: 'is_upcoming' },
  ]) assert.equal(hasGif(info), false);
  assert.equal(hasGif({ media: 'video', is_live: false, live_status: 'was_live' }), true);
  assert.equal(api.formatOptions(null, 'audio').some((q) => q.id === 'gif'), false);
});

test('time validation accepts subsecond ranges and rejects malformed, reversed, out-of-bounds and live trims', () => {
  const { api } = surface();
  assert.equal(api.parseTime(0), 0);
  assert.equal(api.parseTime('1:02:03.5'), 3723.5);
  assert.equal(api.trimProblem({ quality: 'gif', clip_from: '1.25', clip_to: '1.5' }, { duration: 2 }), '');
  for (const payload of [{ clip_from: 'bad' }, { clip_to: '0' }, { clip_from: '5', clip_to: '4' }, { quality: 'gif', clip_from: '10' }]) {
    assert.ok(api.trimProblem(payload, { duration: 10 }));
  }
  assert.ok(api.trimProblem({ clip_to: '1' }, { is_live: true }));
});

test('GIF settings, five-second shortcut and whole-video reset use the same trim inputs', async () => {
  const s = surface('popup/popup.js');
  s.chosen.q = 'gif';
  s.api.restoreGifOptions({ gif_size: 640, gif_fps: 20, gif_loop: false });
  s.node('clipFrom').value = '0.5';
  await s.node('gifFiveSeconds').fire('click');
  assert.equal(s.node('clipTo').value, '5.5');
  await s.node('gifFps').fire('change');
  assert.deepEqual(s.local.gif, { gif_size: 640, gif_fps: 20, gif_loop: false });
  assert.match(s.node('gifSummary').textContent, /5 seconds/);
  await s.node('gifWholeVideo').fire('click');
  assert.equal(s.node('clipFrom').value, '');
  assert.equal(s.node('clipTo').value, '');
  assert.match(s.node('gifSummary').textContent, /Whole video/);
});

test('popup GIF requests carry subsecond trim and encoding preferences', async () => {
  const s = surface('popup/popup.js');
  s.chosen.q = 'gif';
  s.node('clipFrom').value = '1.25'; s.node('clipTo').value = '2.5';
  s.node('gifLoop').checked = false;
  await s.run('keep()');
  assert.equal(s.requests[0].body.quality, 'gif');
  assert.equal(s.requests[0].body.clip_from, '1.25');
  assert.equal(s.requests[0].body.clip_to, '2.5');
  assert.equal(s.requests[0].body.gif_loop, false);
});

test('opening the side panel preserves the popup URL, GIF selection, times and preferences', async () => {
  const s = surface('popup/popup.js');
  s.chosen.q = 'gif';
  s.node('clipFrom').value = '1:02'; s.node('clipTo').value = '1:07';
  s.api.restoreGifOptions({ gif_size: 320, gif_fps: 10, gif_loop: false });
  await s.run('openPanel()');
  assert.equal(s.session.pendingUrl, 'https://example.com/video');
  assert.deepEqual(s.session.pendingOptions, {
    quality: 'gif', clip_from: '1:02', clip_to: '1:07', gif: { gif_size: 320, gif_fps: 10, gif_loop: false },
  });
  assert.equal(s.opened.length, 1);
});

test('side panel consumes a pending GIF without overwriting the requested link', async () => {
  const s = surface('sidepanel/sidepanel.js', {}, {
    pendingUrl: 'https://example.com/right-clicked-link',
    pendingOptions: { quality: 'gif', clip_from: '1.5', clip_to: '3', gif: { gif_size: 960, gif_fps: 30, gif_loop: false } },
  });
  await s.boot();
  const payload = plain(s.run('payload()'));
  assert.equal(payload.url, 'https://example.com/right-clicked-link');
  assert.equal(payload.quality, 'gif');
  assert.equal(payload.clip_from, '1.5');
  assert.equal(payload.gif_size, 960);
  assert.equal(payload.gif_fps, 30);
  assert.equal(payload.gif_loop, false);
  assert.equal(payload.subtitles, false);
  assert.deepEqual(s.session, {});
});

test('context-menu GIF saves reuse saved encoding preferences', async () => {
  const s = surface('background.js', { quality: 'gif', gif: { gif_size: 640, gif_fps: 20, gif_loop: false } });
  await s.run('keepUrl("https://example.com/video")');
  assert.equal(s.requests[0].body.quality, 'gif');
  assert.equal(s.requests[0].body.gif_size, 640);
  assert.equal(s.requests[0].body.gif_fps, 20);
  assert.equal(s.requests[0].body.gif_loop, false);
});

test('completed GIF preview uses the actual animation, with escaped links for each file', () => {
  const s = surface();
  const job = { id: 'job', quality: 'gif', status: 'done', files: ['one & two.gif', 'second.gif'], thumbnail: 'https://example.com/still.jpg' };
  s.api.showGifResult(job, 'http://localhost:8000');
  assert.match(s.node('gifResult').innerHTML, /one%20%26%20two.gif/);
  assert.match(s.node('gifResult').innerHTML, /Save GIF 2/);
  assert.equal(s.node('gifResult').classList.contains('hidden'), false);
  assert.equal(s.api.jobDisplay(job, 'http://localhost:8000').thumb, 'http://localhost:8000/files/one%20%26%20two.gif');
  s.api.showGifResult({ ...job, status: 'processing' }, 'http://localhost:8000');
  assert.equal(s.node('gifResult').classList.contains('hidden'), true);
});
