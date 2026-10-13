---
version: 1
slug: "web-index-html"
primary_target: "web/index.html"
related_targets: ["extension/popup/popup.html","extension/sidepanel/sidepanel.html"]
---

# Surface brief: offtube (extension popup, side panel, web app)

Scope: one product across three surfaces. Extension popup (quick keep, ~370px), side panel (docked column: current tab, options, session strip, library), web app at 127.0.0.1:8000 (full desk + library). Mode: Operate.

Audience/job: everyday keepers and power users (PRODUCT.md). Success = instant tab → kept; options never stand in the way. Must not feel like a generic SaaS, a dev tool, or a toy.

Constraints: no build step, strict CSP (self only, no inline script/style), self-hosted font only, MV3 no inline scripts. All existing behavior preserved (inspect, formats, clip, collection ranges, subtitles, access modes, queue/cancel/retry/clear, library save/delete/filter, health, prefs).

## Direction contract

THESIS: Every link is developed as a print on a contact sheet; you mark what to keep with a grease pencil and it develops onto your disk. Refuses the category default (centered paste box + gradient download button) and the editorial-hairline desk it replaces.

OWN-WORLD: Bright neutral photo-paper ground (never cream), black film-rebate bands framing every piece of media with light condensed edge printing (site, kind, frame number, size), one china-marker crimson used only for marks (circle = chosen, tick = kept, strike = failed), safelight amber only for developing. Archivo at normal width for UI, condensed caps for edge print and numerals. Square corners, no shadows, no gradients, no cards.

STORY: Open on any page → the tab's media is already a print with its site and kind printed on the film edge → one Keep → the print develops from negative to positive → a pencil tick. Collections arrive as a sheet you circle. Power options sit as notes below the print, folded until needed.

FIRST VIEWPORT: Popup: header strip; the tab's print at full width in its rebate band (edge print top and bottom); title and maker; a format strip; a full-width black Keep bar as the only primary action. Side panel: same print and Keep at top, then folded option notes, the session strip and the kept sheet. Web app: a paste rail across the top, the print or contact sheet left, a sticky keep column right, the developing strip and kept sheet below.

FORM: Contact Sheet (darkroom contact prints with china-marker marks), position 4 of 7 on the ordered list; raised with a marks-only state vocabulary (manual), no-reflow live strips (split-flap), overlay-only marks (orienteering), running total against free disk (j-card). Seed key 0033990b.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Signature interactions
- Develop: in-progress prints show their negative (inverted, amber-safelit) fading to positive with progress; edge print counts the percent.
- Mark: grease-pencil strokes drawn in (stroke animation, instant under reduced motion): circling frames in a collection builds the item range; tick on kept; strike on failed.

## Unresolved
- Dark scheme follows the OS (same world under safelight); light is the primary scene.
