---
name: offtube
description: Every link is developed as a print on a contact sheet; mark what to keep and it develops onto your disk.
colors:
  paper: "#f3f3f0"
  paper-hi: "#fbfbf9"
  ink: "#141413"
  ink-2: "#474743"
  ink-3: "#64645e"
  ink-hover: "#000000"
  rule: "#d6d6cf"
  rule-2: "#a9a9a1"
  rebate: "#0f0f0e"
  rebate-2: "#20201e"
  edge: "#ebe7dc"
  edge-2: "#a19d92"
  pencil: "#d0103a"
  pencil-ink: "#b00d31"
  on-pencil: "#ffffff"
  safelight: "#f0962a"
typography:
  display:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(24px, 3vw, 38px)"
    fontWeight: 760
    lineHeight: 1.05
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 78"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(22px, 2.3vw, 32px)"
    fontWeight: 740
    lineHeight: 1.12
    letterSpacing: "-0.012em"
    fontVariation: "'wdth' 84"
  title:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "24px"
    fontWeight: 760
    lineHeight: 1.1
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 80"
  body:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 430
    lineHeight: 1.5
  label:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "11px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "0.1em"
    fontVariation: "'wdth' 72"
  edge:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.12em"
    fontFeature: "'tnum' 1"
    fontVariation: "'wdth' 62"
  action:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "19px"
    fontWeight: 760
    lineHeight: 1.1
    fontVariation: "'wdth' 86"
  input:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 430
    lineHeight: 1.3
  ui:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 430
    lineHeight: 1.45
  small:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13.5px"
    fontWeight: 430
    lineHeight: 1.5
  caption:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12.5px"
    fontWeight: 430
    lineHeight: 1.45
  code:
    fontFamily: "ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "12.5px"
    lineHeight: 1.55
rounded:
  none: "0px"
spacing:
  page: "clamp(16px, 3vw, 40px)"
  gutter: "clamp(24px, 3.4vw, 56px)"
  section: "56px"
  rebate: "10px 16px"
  sm: "8px"
  md: "12px"
  lg: "16px"
components:
  keep-button:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    height: "60px"
    width: "100%"
  keep-button-hover:
    backgroundColor: "{colors.ink-hover}"
    textColor: "{colors.paper}"
  button:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: "0 16px"
    height: "42px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0 16px"
    height: "42px"
  button-quiet-hover:
    backgroundColor: "{colors.paper-hi}"
  input:
    backgroundColor: "{colors.paper-hi}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "9px 12px"
    height: "42px"
  paste-rail:
    backgroundColor: "{colors.paper-hi}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0 18px"
    height: "50px"
  segment-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: "9px 16px"
  format-row:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    padding: "0 10px 0 14px"
    height: "48px"
  format-row-checked:
    backgroundColor: "{colors.paper-hi}"
  rebate:
    backgroundColor: "{colors.rebate}"
    textColor: "{colors.edge}"
    rounded: "{rounded.none}"
    padding: "{spacing.rebate}"
---

# Design System: offtube

## Overview

**Creative North Star: "The Contact Sheet"**

Every piece of media offtube finds is treated as a print on a darkroom contact sheet. The ground is bright neutral photo paper, never cream. Each image sits inside a black film-rebate band whose top and bottom edges carry small condensed edge printing: site, kind, frame number, size. The user marks what to keep the way a photographer marks a sheet, with a crimson china-marker: a circle means chosen, a tick means kept, a strike means failed. While a file downloads its print develops, from an amber-safelit negative to a positive.

The system is flat, square and ink-on-paper. It has no shadows, no gradients, no cards and no rounded corners. Black does the structural work: rebate bands, the Keep bar, 2px ink rules that open each section. Crimson and amber are rare because each one means something specific. Density is calm and operational. One print and one Keep action lead, and power options sit underneath as folded notes. The same world runs at three sizes: the web desk, the ~380px popup and the narrow side panel. They share tokens and differ only in scale.

Light is the default scene. Dark follows the OS and keeps the same world under safelight: paper goes near-black, the rebate goes to pure black with a 1px outline, and the pencil brightens.

**Key Characteristics:**
- Paper ground, black film rebate, condensed uppercase edge printing on the film.
- One crimson pencil, used for marks and a few accessibility affordances only.
- Safelight amber appears only while something develops.
- Square corners everywhere, with 1px and 2px ink or grey rules for structure.
- Marks are an SVG overlay drawn in with a wipe, and they never recolour the print.
- Live strips update in place without reflow.

## Colors

Achromatic paper and ink, with a near-black film. Only two hues exist, and both carry meaning.

### Primary
- **China-Marker Crimson** (`pencil`): the grease pencil. Its uses are pencil marks (circle = chosen, tick = kept, strike = failed), the error meter fill, the failed-health dot, focus rings, text selection and the caret. In dark mode it brightens to #ff4766.
- **Crimson Ink** (`pencil-ink`): the text-weight crimson for failure copy (field errors, the failed deck line, health "unreachable"). Dark: #ff6f86.

### Secondary
- **Safelight Amber** (`safelight`): the darkroom lamp. It appears in the developing negative's multiply wash, the progress-meter fill while running, the pulsing safelight over a frame being exposed (inspected), and amber edge print on a developing frame.

### Neutral
- **Photo Paper** (`paper`): the page ground and the text on black controls. Dark: #121211.
- **Paper Highlight** (`paper-hi`): input and paste-rail fills, the hovered or checked format row, and the brand mark's window. Dark: #1b1b19.
- **Ink** (`ink`): body text, primary controls, the selected segment, 2px section rules, the done meter and the ok-health dot. Dark: #ecebe5.
- **Ink 2 / Ink 3** (`ink-2`, `ink-3`): secondary copy (meta, hints, deck line) and tertiary copy (counts, field labels, placeholders). Dark: #bdbcb4 / #9a998f.
- **Ink Hover** (`ink-hover`): pure black (white in dark), the hover fill for black controls. It deepens the control and never tints it.
- **Rule / Rule 2** (`rule`, `rule-2`): 1px dividers between rows, notes and the bar, plus the meter track (`rule`). `rule-2` is used for input and icon-button strokes and for the dark-mode rebate outline.
- **Film Rebate / Frame Black** (`rebate`, `rebate-2`): the film band and the empty image box inside it. The log `pre` is also set on rebate. Dark: #000 / #1a1a18.
- **Edge Print / Dim Edge** (`edge`, `edge-2`): warm off-white lettering on the film and dimmer secondary edge text. These are the only warm neutrals, and they live only on black.

### Named Rules
**The Marks-Only Crimson Rule.** Crimson means one of three things: chosen (circle), kept (tick) or failed (strike). It is also used for failure text, focus rings, selection and caret. It is never a hover, never a link colour, and never decoration.

**The Safelight Rule.** Amber is on only while something is in the darkroom, being exposed or developing. When the work is done, amber goes away (the meter turns ink, the print turns positive).

**The Never-Cream Rule.** The paper is a cool neutral grey-white (#f3f3f0). Warmth belongs only to the edge print on black film.

## Typography

**Display Font:** Archivo, variable, self-hosted (wght 400–800, wdth 62–100), falling back to ui-sans-serif and system-ui
**Label/Mono Font:** Archivo condensed for labels and edge print. `ui-monospace` is used only for the log and the cookie textarea.

**Character:** One family, worked through its width axis. UI copy sits at normal width. Headings tighten to 78–86% width, and the film's edge printing goes fully condensed (62%) in tracked uppercase with tabular numerals, like lettering exposed into the film stock.

### Hierarchy
Eight steps plus two fluid sizes; both surfaces draw from the same ramp (the extension simply uses fewer steps).
- **Display** (760, clamp(24px, 3vw, 38px), wdth 78): the blank-sheet call-to-paste set over the film. Used only there.
- **Headline** (740, clamp(22px, 2.3vw, 32px), 1.12, wdth 84, balanced wrap): the print title under the rebate. Extension: the Action size (19px), clamped to 3 lines.
- **Title** (760, 24px, 1.1, wdth 80): section heads ("This session", "Kept") and the wordmark. Extension: 19px.
- **Action** (760, 19px, wdth 86): the Keep bar label on every surface; extension headings and blank-print titles.
- **Input** (430, 16px): the paste rail (16px keeps mobile browsers from zooming).
- **Body** (430, 15px/1.5): web UI copy. Notes run to 62ch, problems to 68ch, the footer to 78ch.
- **UI** (430, 14px/1.45): controls, note rows, deck lines; the extension's base size.
- **Small** (430, 13.5px): hints, tally, counts, kept-file names, strip titles.
- **Caption** (430, 12.5px): dates, file meta, the health line; `ui-monospace` at this size for the log and cookie textarea.
- **Label** (650, 11px, wdth 72, 0.1em, uppercase, ink-3): form field labels and legends ("Format", "From", "Sign-in").
- **Edge** (600, 11px, wdth 62, 0.12em, uppercase, tabular): film edge printing only, in `edge`, `edge-2` or amber — 11px on every surface, including thumbnails.

### Named Rules
**The Edge-Print-Lives-On-Film Rule.** Condensed tracked uppercase in edge colours appears only inside a rebate band or a film strip. Off the film, small uppercase is a form label above its own control, never a kicker above a heading.

**The Tabular Numbers Rule.** Frame numbers, sizes, percentages, counts and numeric inputs use tabular figures so live values don't jitter.

## Layout

The web desk is a two-column grid: the print or contact sheet on the left (`minmax(0, 1fr)`) and a sticky keep column on the right (320–384px, sticky at 98px). Columns are separated by the gutter token and capped at 1480px, with the page token as side padding. A sticky top bar holds the brand, the centred paste rail (max 860px) and health, and is separated by a 1px rule. Below the desk come full-width sections ("This session", "Kept"), each opened by a 2px ink rule and a baseline-aligned row head: title, count, then a text button pushed right.

Under 960px the desk stacks to one column and the keep column stops being sticky. Under 720px the paste rail wraps to its own bar row, the contact sheet goes to 3 columns, the kept sheet to 2, and session frames shrink from 216px to 172px.

In the extension, the popup is fixed at 380px with 12–14px padding. The side panel is a single column where sections are opened by the same 2px ink rule. The session strip runs vertically, and its sprocket rails sit on the left and right.

**The No-Reflow Rule.** Live strips (session, progress, tally) update the existing nodes in place. Progress moves by `transform: scaleX` and opacity, and text sits in fixed slots. Nothing shifts as jobs advance.

## Elevation & Depth

The system has no shadows. Depth comes from material contrast instead: black film lying on paper, the slightly lighter frame-black image box inside the rebate, and paper-hi fills for active rows and fields. In dark mode the rebate gets a 1px `rule-2` outline so the film still reads against near-black paper. Overlays are limited to the pencil marks, the safelight wash and the 55% rebate scrim behind the blank-sheet call.

**The Flat Film Rule.** Nothing floats. Separation uses rules, black bands or a paper-hi fill, never a shadow, glow or gradient.

## Shapes

Every corner is square (`rounded.none`). Strokes are 1px for dividers and input borders and 1.5px ink for framed controls (paste rail, segmented control, buttons). Section openers use 2px ink. The only curves are hand-drawn: the pencil circle, tick and strike are rounded-cap SVG strokes. On every surface they pass through a fractal-noise displacement filter (`#wax`, defined once per page) for a waxy grease-pencil edge. Small exceptions are the round 8–9px health dot and the 1.6px-radius perforation holes in the film tile. Perforations are a background tile, horizontal on the web strip and vertical on the extension strip.

## Components

### Buttons
- **Keep bar:** the single primary action. It is a full-width black bar (60px on the web, 52px in the extension) with the label at Keep scale and a download glyph drawn as an SVG stroke. On hover the fill deepens to `ink-hover` and the arrow drops 2px. On active the bar presses 1px. When disabled it drops to 0.45–0.6 opacity.
- **Button:** solid ink with a 1.5px ink border, 650 weight, 42px (36px in the extension).
- **Quiet:** transparent with an ink border, and a paper-hi fill on hover.
- **Text button:** ink underline at 1px thickness, thickening to 2px on hover. This is how secondary actions appear ("Circle all shown", "Clear finished", "Save", "Delete").
- **Icon button:** a 42px square with a `rule-2` border and a 16px SVG stroke icon. Its border goes ink on hover.

### Segmented control
A 1.5px ink frame with 3px inset padding. The selected segment is solid ink with paper text, and unselected segments hover to paper-hi. Focus is a crimson ring inset 5px.

### Format rows and chips
Format rows (web and side panel) are 48px rows split by 1px rules: name on the left (680) and a tabular size on the right in `ink-3`. The checked row gets a paper-hi fill, and its name is **circled in pencil**. That circle is the chosen state, not a colour change. Popup chips use the same circle on a bare text label.

### Inputs / Fields
These have square corners, a 1px `rule-2` stroke and a paper-hi fill, and are 42px tall (38px in the extension). On focus the border goes ink and a 2px crimson ring appears at 1px offset. Field errors are 14px, 600 weight, in `pencil-ink`. The paste rail is a 1.5px ink frame with a 50px input and an attached black "Preview" button, and the whole rail takes the crimson focus ring.

### Folded notes
Power options sit in `<details>` rows between 1px rules. Each row shows the name (680) on the left and the current state on the right in `ink-3` (ink and 650 when set), followed by a chevron that rotates 90° when open. The body is folded until needed.

### The Print (signature)
A rebate band holds a top edge row, the frame and a bottom edge row. The frame is `rebate-2` and centres the image (up to min(64vh, 620px)). The develop layer stacks an inverted, sepia-shifted negative over the positive, with an amber multiply wash. Both fade with progress `--p`, and the edge print counts the percent. Under the band, the caption carries the headline title, ink-2 meta and text-button tools.

### Contact sheet (signature)
Collections render as square `rebate-2` cells in a film band. Each cell is a toggle button (`aria-pressed`). Pressed cells show a pencil circle around the **image box only**, never the edge print beneath it. Hover outlines the image 1px in `edge-2`. Focus outlines it 2px in crimson.

### Pencil marks (signature)
These are SVG overlays (`viewBox 0 0 100 100`, non-scaling stroke, about 4–4.5px) stretched over what they mark and drawn in with a 0.5s left-to-right clip-path wipe (instant under reduced motion). The circle rings, the tick runs off the bottom-right corner, and the strike crosses the frame out. They have `pointer-events: none` and never alter the pixels beneath.

### Session strip and kept sheet (signature)
The session strip is literal film: a rebate band with perforation rails, 216px frames (16:10), edge-colour titles and text actions. The current frame is outlined in `edge`. A kept frame is ticked, and a failed one is struck. The kept library is a contact sheet of small rebate prints (4:3 on the web, 1:1 in the extension) with edge print for index and type·size. A filename, date and Save/Delete text buttons sit below each print.

### Health and meter
Health is condensed uppercase status with a round dot: neutral `ink-3` while checking, ink when ok, crimson when unreachable. The meter is a 3px `rule` track with a scaleX fill, amber while running, ink when done and crimson when failed.

## Do's and Don'ts

### Do:
- **Do** frame every piece of media in a black rebate band with condensed edge printing top and bottom.
- **Do** express chosen, kept and failed only with pencil marks (circle, tick, strike) drawn as an overlay.
- **Do** ring the image box only. A circle never encloses the edge print or caption.
- **Do** use amber only while exposing or developing, and switch to ink once done.
- **Do** keep corners square and separate with 1px rules, 2px ink section rules or a paper-hi fill.
- **Do** update live strips in place with transform and opacity, using tabular numerals.
- **Do** keep light as the default scheme and let `prefers-color-scheme: dark` remap the same tokens.
- **Do** let one black Keep bar be the only primary action on a surface.

### Don't:
- **Don't** use crimson for hovers, links, badges, headings or decoration.
- **Don't** recolour, tint or filter a print to show a mark. The mark sits on top.
- **Don't** use shadows, gradients, rounded cards or cream paper.
- **Don't** use edge-print styling off the film, and don't put an uppercase kicker above a heading.
- **Don't** let progress or new jobs shift surrounding layout.
- **Don't** load fonts from a CDN or inline styles. Archivo is self-hosted and the CSP is `style-src 'self'`.
