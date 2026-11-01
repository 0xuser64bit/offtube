# Product and design

[Home](README.md) · [Architecture](ARCHITECTURE.md) ·
[Contributing](CONTRIBUTING.md)

## What offtube is for

offtube is a personal media keeper: point at a link, see what it contains,
choose what to keep, and get files on your own machine. It is not a hosted
download service, streaming library, or account system.

Two kinds of people share the same product:

- **Everyday keepers** want the thing in their current tab, saved, without
  learning about engines, formats, or logs.
- **Power users** run the local server, supply their own cookies, select
  quality and ranges, trim clips, and inspect failures.

The design is progressive: the simple path is obvious, and full controls
are one deliberate step away, not removed. Success means getting the file
the user intended and explaining plainly when that is not possible.

### Product rules

1. **The link is the interface.** Inspect first; reveal controls based on
   the actual engine and media, not the hostname alone.
2. **One clear keep action.** The popup is the quick path; the side panel
   and desk carry deeper options, progress, and the library.
3. **Say what happened.** Inspecting, unsupported, sign-in needed, queued,
   saving, kept, failed, and server unreachable each need a next step.
4. **Local and honest.** No implied cloud account, authentication boundary,
   DRM bypass, or universal site compatibility.
5. **No invented proof.** There are no established user counts,
   testimonials, performance benchmarks, or accessibility certification to
   market as fact. Screenshots use labeled demo content.

The name is **offtube**, lowercase. The voice is plain, direct, confident,
and a little dry. Use **keep** for the brand action, **link** for input,
**collection** for playlists/boards/albums, and **Sign-in** for cookie-based
access. Explain limitations without blaming the user.

## The contact sheet

Every link becomes a print on a photographer's contact sheet. Neutral
photo paper is the ground. A black film border, or rebate, frames the
media; condensed edge printing carries the site, kind, number, and size.
A crimson grease pencil expresses a choice or outcome:

| Mark | Meaning |
| --- | --- |
| Circle | Chosen format or selected collection print. |
| Tick | Kept file. |
| Strike | Failed keep. |
| Amber development wash | Work in progress, not a completed state. |

The print develops from a negative into a positive while saving. A mouse
hover can hold a loupe over an image. The library is one continuous contact
sheet, not a set of unrelated cards. See the
[collection screenshot](docs/assets/collection.png).

The world stays flat, square, and ink-on-paper. No shadows, glossy
gradients, rounded dashboard cards, or decorative status colors. Light is
the default; `prefers-color-scheme: dark` remaps the same materials rather
than inventing a different product.

## Visual tokens

The shipped source of truth is [`web/styles.css`](web/styles.css) and
[`extension/ui.css`](extension/ui.css). Keep their shared intent aligned.
The design-tool record in [`.impeccable/design.json`](.impeccable/design.json)
is supporting metadata, not a replacement for the actual CSS.

| Token | Light | Dark | Job |
| --- | --- | --- | --- |
| `paper` | `#f3f3f0` | `#121211` | Cool neutral page ground, never cream. |
| `paper-hi` | `#fbfbf9` | `#1b1b19` | Fields and active rows. |
| `ink` | `#141413` | `#ecebe5` | Text and primary structural contrast. |
| `ink-2` | `#474743` | `#bdbcb4` | Supporting copy. |
| `ink-3` | `#64645e` | `#a19d92` | Labels and secondary metadata. |
| `rule` | `#d6d6cf` | `#2d2d2a` | Dividers. |
| `rebate` | `#0f0f0e` | `#000000` | Film border. |
| `edge` | `#ebe7dc` | `#ebe7dc` | Edge printing on film. |
| `pencil` | `#d0103a` | `#ff4766` | Selection/outcome marks and focus affordances. |
| `safelight` | `#f0962a` | `#f0962a` | Active development only. |

**Pencil rule:** crimson is not a decorative accent for headings, links,
or hover fills. A mark sits over an image; it does not recolor the image.
**Safelight rule:** amber disappears when work finishes.
**Edge-print rule:** condensed film lettering belongs on film; off-film
uppercase labels identify their own controls, not ornamental kickers.

### Type and mark

One self-hosted variable family, **Archivo**, does the work. UI copy uses
normal width; headings narrow to roughly 78–86%; film edge printing goes
fully condensed at 62%, with tracking and tabular figures. Monospace is
reserved for the transfer log and cookie text.

The desk uses 15px body text, 14px UI text, 19px actions, 24px section titles,
and fluid print headlines. Labels and edge printing are 11px. Extension
surfaces use the same hierarchy at a smaller scale. Read the CSS for exact
weights, responsive sizes, and contrast variants instead of duplicating
every token here.

The mark is a strip of film with five perforations on either side, two
frames, and a crimson tick on the second. Its film colors stay fixed in
both schemes. The drawing lives in [`web/favicon.svg`](web/favicon.svg)
and inline surface headers; [`tools/gen_icons.py`](tools/gen_icons.py)
produces the extension PNG icons. Reuse it rather than introducing a
second logo vocabulary.

## Layout and interaction

- **Desk:** a print/contact sheet on the left, sticky keep controls on the
  right, then full-width session and library sections. Below 960px the
  desk stacks; below 720px the paste rail wraps and sheet density reduces.
- **Popup:** a 380px-wide quick decision, a compact quality choice, and one
  Keep. Clicking away closes it; it is not the home for long workflows.
- **Side panel:** a persistent narrow column that follows tabs, with full
  controls, a vertical session strip, and recently kept files.
- **Structure:** square corners, 1px rules, 2px section openers, and paper
  fills. Film bands provide depth, not shadows.
- **Primary action:** one full-width black Keep bar. Secondary actions
  are underlined text or quiet outlined buttons.
- **Options:** native `<details>` rows show their name and current value
  while folded. Only relevant controls appear after inspection.
- **Selection:** collection prints are real toggle buttons with
  `aria-pressed`; pencil circles surround the image, not its caption.
- **Live updates:** reuse existing progress/session nodes. Transform and
  opacity animate progress; fixed slots and tabular numbers limit shifts.

The only curves are meaningful exceptions: pencil strokes, film
perforations, the status dot, and the loupe. The loupe is decorative,
`aria-hidden`, and absent on touch. Pencil overlays do not intercept input.

## Accessibility and implementation constraints

Target WCAG 2.2 AA: keyboard-operable controls, visible focus, readable
contrast in both themes, text and status announcements alongside marks,
and reduced-motion support. A circle or color must not be the only
machine-readable indication of state.

The frontend is plain HTML/CSS/JavaScript with no build. Both surfaces
self-host their font. Do not introduce CDN assets, inline scripts/styles,
or handlers that violate the shipped Content Security Policy. Prefer
native controls and explicit labels to custom interaction machinery.

Browser regression checks cover keyboard use, axe-core audits, text
spacing, and narrow desk reflow. Their limits, including the absence of
real screen-reader certification, are documented in
[Contributing](CONTRIBUTING.md#browser-and-accessibility-checks).
