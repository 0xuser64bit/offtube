# Contributing to offtube

[Home](README.md) · [Architecture](ARCHITECTURE.md) ·
[Product and design](DESIGN.md) · [API](docs/api.md)

Keep changes small, useful, and consistent across the web desk and
extension. There is no frontend build step, web framework, or database to
set up. Start with the [user guide](docs/guide.md) if you have not run the
product yet.

## Development setup

Use Python 3.10+, Node.js, FFmpeg, and ffprobe. Node 24 matches the CI
JavaScript environment; the user-facing YouTube runtime check accepts
Node 23.5+ or Deno 2.x.

From the repository root on macOS/Linux:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
./.venv/bin/python app.py
```

On Windows, create the venv with `python` and use
`.venv\Scripts\python.exe`. The Python tests also run there; the full
`tools/check.sh` gate requires Bash. Keep development servers on loopback.

## Find your way around

| Path | Responsibility |
| --- | --- |
| [`app.py`](app.py) | HTTP handling, source resolution, inspection, queue, engine orchestration, cookies, library, shutdown. |
| [`tools/ytdlp_worker.py`](tools/ytdlp_worker.py) | Isolated yt-dlp download process and JSON event output. |
| [`web/`](web/) | No-build web desk: HTML, CSS, JavaScript, and self-hosted Archivo. |
| [`extension/`](extension/) | MV3 service worker, shared helpers, popup, and side panel. |
| [`tests/`](tests/) | pytest, Node unit tests, and browser suites. |
| [`deploy/`](deploy/) | Native launchd and systemd templates. |
| [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml) | Bundled runtime and loopback-only service configuration. |
| [`downloads/`](downloads/), [`cookies/`](cookies/) | Runtime data, not source assets; contents must not be committed. |

Read the [architecture invariants](ARCHITECTURE.md#invariants-to-preserve)
before changing request validation, process lifetimes, cookies, or file
handling. Read [product and design](DESIGN.md) before changing a surface.
Extract a module when a tested responsibility boundary helps a real
change, not just to make `app.py` shorter.

## Run the checks

```sh
bash tools/check.sh
```

The gate compiles Python, runs pytest, checks shipped JavaScript syntax,
runs Node tests and the optional browser suites, then checks Git patch
whitespace. It chooses `.venv/bin/python` if available; override it with
`PYTHON=/path/to/python`. Node.js, FFmpeg, and ffprobe must be on `PATH`.

For a narrower iteration:

```sh
./.venv/bin/python -m pytest tests -q
./.venv/bin/python -m pytest tests/test_api.py -q
node --test tests/extension_gif.test.js tests/test_web_gif.mjs
```

The backend tests do not contact public media sites. They use real HTTP
handlers with stubbed inspection, fake engine processes, local fixture
servers, and generated media. A passing suite does not prove a live
third-party site's current behavior.

### Browser and accessibility checks

Playwright and axe-core are optional locally and required in CI. To
install them without adding a frontend package manifest to the project:

```sh
npm install --no-save --no-package-lock --prefix /tmp/offtube-browser \
  playwright@1.63.0 axe-core@4.14.0
NODE_PATH=/tmp/offtube-browser/node_modules \
  /tmp/offtube-browser/node_modules/.bin/playwright install chromium
NODE_PATH=/tmp/offtube-browser/node_modules \
  node --test tests/browser/desk.test.mjs tests/browser/extension.test.mjs
```

Use an equivalent temporary directory on Windows. The browser harness
also accepts `PYTHON` for its server interpreter. The pinned axe-core
version matters because audit rule sets change between releases.

The desk and unpacked extension run against the real server with stubbed
media. Coverage includes keep flows, collection selection, library search,
server outages/restarts, keyboard-only operation, light/dark axe-core
audits, text spacing, and desk reflow at 320 CSS pixels.

On Debian/Ubuntu, also install Chromium's native dependencies and the
X11/XTest libraries used for real input. The Playwright command may request
administrator access to install system packages:

```sh
/tmp/offtube-browser/node_modules/.bin/playwright install --with-deps chromium
sudo apt-get install -y xvfb libx11-6 libxtst6
```

With those Linux dependencies, exercise Chrome's actual context menus,
shortcut, and side panel through input on a private Xvfb display:

```sh
NODE_PATH=/tmp/offtube-browser/node_modules \
  node --test tests/browser/background.test.mjs
```

Missing Playwright, axe-core, or Xvfb causes the relevant local tests to
report skips. Once real-input tests run, missing X11/XTest libraries are
failures, not skips. In CI, `OFFTUBE_BROWSER_TESTS=required` prevents
missing browser dependencies from silently passing. These checks do not
exercise a screen reader or the toolbar popup's own window, and are not
accessibility certification.

### What CI adds

The [workflow](.github/workflows/ci.yml) runs the full gate on Python 3.10
and 3.12, backend and browser checks on Windows and macOS, launcher and
native-service smoke tests, Linux real-input browser checks, and Docker
build/startup/worker/reaping/shutdown checks on amd64 and arm64. Describing
that coverage does not imply the latest workflow run is green.

## Make a focused change

- Preserve web/extension feature parity; controls follow inspected engine
  and media type, not just a site's hostname.
- Add regression coverage next to the behavior you change. Cover invalid
  inputs and cancellation as well as the successful path.
- Preserve the no-build frontend, restrictive CSP, self-hosted fonts, and
  the [contact-sheet visual language](DESIGN.md#the-contact-sheet).
- Update the relevant user guide or API reference in the same change.
- Check `git diff --check`; never include media downloads, cookie exports,
  browser profiles, private URLs, or unrelated generated files.

## Documentation and screenshots

The README is the front door. Keep detailed setup/workflows in
[`docs/guide.md`](docs/guide.md), protocol details in
[`docs/api.md`](docs/api.md), engineering constraints in
[`ARCHITECTURE.md`](ARCHITECTURE.md), and product/design intent in
[`DESIGN.md`](DESIGN.md). Link rather than copying the same explanation
into every file. Use relative links and descriptive image alternatives.

Screenshots show the shipped interface with controlled, fictional sample
content. Do not imply they prove a live site's availability. See
[`docs/assets/README.md`](docs/assets/README.md) for provenance and capture
guidance. Keep screenshots free of account information and cookie text.

## Report a problem

Include your OS, launch method (native, Docker, or service), relevant tool
versions from `/api/health`, the error or sanitized transfer-log excerpt,
and the smallest sequence that reproduces it. For an extension issue,
include the Chrome version and which surface you used.

Say whether public media from the same site works. Share a public example
only when you have permission; never send a private media URL, cookies,
browser profile, or a full authenticated request payload.
