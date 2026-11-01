# Documentation assets

[Home](../../README.md) · [Documentation guidance](../../CONTRIBUTING.md#documentation-and-screenshots)

These are screenshots of offtube's shipped interface, captured on
November 1, 2026. The web desk and extension pages used the real client
code with a local fixture server standing in for media inspection.

**The content is fictional.** “Aster Vale”, “Tidal”, the collection titles,
thumbnails, video IDs, durations, and size estimates are demonstration
data, not a live-site availability claim or an endorsement. The poster
artwork was drawn for these fixtures in HTML/CSS; it is not downloaded
third-party media. No account cookies or private media were used.

| Asset | What it shows |
| --- | --- |
| `desk-light.png`, `desk-dark.png` | The same video-inspection desk in both color schemes. |
| `collection.png` | Individual playlist entries selected on the contact sheet. |
| `extension-popup.png` | The extension's popup page with a quality selected. |
| `extension-panel.png` | The side-panel page with full controls. |

The desktop captures are reduced from 2880 × 2000 to 1600 × 1111 pixels;
extension captures retain their original two-times pixel density. The
extension captures show the actual extension pages, not Chrome's toolbar
popup window or browser chrome. All assets are stored locally so the docs
do not depend on a third-party image host.

When replacing a capture, use controlled, authorized sample media, wait for
fonts and images to load, dismiss transient hover/focus artifacts, and
avoid cropping away controls needed to understand the workflow. Capture
the same state in both schemes for a theme-aware pair. Update this file
if the sample content or capture method changes.

The README's logo is the existing [`web/favicon.svg`](../../web/favicon.svg),
not a separate documentation-only brand. Archivo's font license is in
[`web/fonts/OFL-Archivo.txt`](../../web/fonts/OFL-Archivo.txt).
