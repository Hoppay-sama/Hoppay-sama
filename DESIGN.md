# OIONOS — "Night Signal"

Art direction, scene specs, and build constraints for the profile README.
This file is the source of truth for every generated artboard. The generator
(`scripts/generate_profile.py`) reads `profile/config.json`; this document
explains why each choice exists and what may not be changed casually.

---

## 1. Concept

The profile is a four-act night film, and GitHub's own page scroll is the
camera. Every frame is an SVG authored in this repository, generated from live
GitHub data, and refreshed daily by a GitHub Action. No third-party widgets,
no hosted dependency that can die, no scripts (GitHub strips them) — only what
the platform natively renders.

One sentence: **a nocturnal city under an omen sky, where your year of commits
becomes the skyline and your projects become the records.**

Why it is built this way: the 2026 state of the art in profile art is themed
single cards (galaxy generators, game-styled banners, neofetch grids). A
multi-act scroll narrative where live data *is* the scenery is the
differentiator, and it is only possible because GitHub's image proxy serves
self-contained SVG faithfully (SMIL, CSS keyframes, `@media`, embedded
resources — `<script>` is stripped).

## 2. Reference board

Studied before building. Each entry: what it does, what we take, what we avoid.

| Reference | Steal | Avoid |
|---|---|---|
| `mrkjrcxcnd/mrkjrcxcnd` — one authored 92 KB SVG, mobile artboard via `<picture>` | Full-art authorship; separate mobile composition | Everything inside one image (no real text for a11y/SEO) |
| `NissonCX/death-stranding-profile` — game-cinematic generated cards | Fonts baked into the SVG (we go further: font-to-path); hand-cut geometry | Card-stack layout; external edge service |
| `vinimlo/galaxy-profile` (462★) — generator + config + Action pattern | Repo architecture; auto-update loop | Generic themed card look |
| Scalar README techniques | Knowledge of `<foreignObject>`/CSS-in-SVG capability | Dependence on exotic features we do not need |
| `zaccesss/.profile` — neofetch ASCII + live stats | Live data rendered densely but tastefully | Monospace-everywhere sameness |

## 3. Palette

Measurable tokens. These exact values live in `profile/config.json` under
`palette` and are the only colors the generator may emit (language signal hues
are the single sanctioned exception).

| Token | Hex | Role |
|---|---|---|
| `void` | `#03050A` | Deepest ground, page outer |
| `night` | `#04060C` | Artboard base |
| `panel` | `#0A0F1E` | Raised masses (towers, plaques) |
| `line` | `#1B2748` | Hairlines, frames |
| `lineFaint` | `#141E36` | Secondary strokes |
| `ink` | `#EAF1FA` | Primary text |
| `inkDim` | `#9AA7C2` | Body text |
| `inkFaint` | `#5C6A88` | Microtype, non-essential |
| `cyan` | `#8FD3FF` | Signal: moonlight, structure, links |
| `cyanDim` | `#4E7FA8` | Secondary signal |
| `star` | `#C9D6E8` | Starfield |
| `amber` | `#E0A458` | Human heat: lit windows, streak beacon |

Rules: backgrounds never lighter than `#0A0F1E`; all light is emitted, never
flat-filled; cyan is structure, amber is life — do not swap their roles.

Language signal hues (bar only, in order): TypeScript `#6FA8DC`, Python
`#6DA8C9`, Solidity `#C98A6B`, Astro `#D9A05B`, Other `#3A4560`.

## 4. Typography

| Face | Role | Weights | Notes |
|---|---|---|---|
| Bebas Neue | Display: wordmark, act numerals, stat figures | 400 | OFL. Wordmark tracking 0.30–0.34em |
| JetBrains Mono | HUD, labels, microtype, timecodes | 300/400/500 | OFL. Uppercase, tracking 0.14–0.24em, sizes 8–12px |
| Inter | Body: taglines, loglines | 400/500 | OFL |

**Embedding strategy: font-to-path, always.** The generator converts every
string to SVG path outlines at build time (fontTools). Rationale: GitHub's
proxy never loads external fonts, embedded base64 bloats files, and path
output renders identically on every OS. Consequences: text inside artboards is
not selectable — acceptable because every artboard is decorative; the README
carries the real text (§8). Font sources are committed under `assets/fonts/`
with their OFL licenses.

Type scale (desktop artboard / mobile artboard): wordmark 84/46; act numeral
30/22; stat figures 30/22; body 13/12; logline 12/11; HUD 10/9; caption 8/8.
Italic is never used. Never mix more than three type sizes in one block.

## 5. Scenes

Every scene exists as a desktop artboard (820 wide) and a mobile artboard
(390 wide), swapped in the README via `<picture>` at `max-width: 600px`.

### Act 0 — TITLE (`act0-title`, 820×430 / 390×620)
Sky gradient `void → night → indigo`. Starfield (90 stars desktop, 55 mobile)
with seeded positions and opacities. The omen-bird constellation: nodes plus
stroke segments, cyan, the head node brighter. Wordmark `OIONOS`; beneath it
`MARC HERNANDEZ` (cyan, tracked) and `SOFTWARE ENGINEER` (dim). Tagline
(body). Act index chips `01 · THE WATCHER … 04 · TRANSMISSION`. HUD: Manila
coordinates (top-left), live local time PH (top-right), regen note
(bottom-left), scroll cue (bottom-right). Live data: local time.

### Act I — THE WATCHER (`act1-watcher`, 820×300 / 390×460)
Bio card: two short paragraphs from config, then the equipment manifest — a
compact glyph-and-label grid (languages / frontend / backend / data / tools).
The full manifest lives in a native `<details>` block in the README, not in
the art. ASCII portrait is a config toggle, default **off**; when enabled it
occupies the left third and the manifest compresses to a column.

### Act II — THE CITY (`act2-city`, 820×560 / 390×720)
The centerpiece. One tower per contribution week (all 52–53 desktop; mobile
groups weeks in pairs, 26 towers);
tower height maps weekly commit count through a clamped square-root curve so
one huge week cannot dominate the skyline. Lit amber windows = days with
commits; window count scales with that day's activity. A beacon marks the
current streak position. HUD readout row: commits (12 mo), stars, pull
requests (live), then the language signal bar. Caption microtype:
"every lit window is a day you shipped." Live data: contribution calendar,
stars, PRs, languages.

### Act III — THE RECORDS (`act3-records`, 820×400 / 390×560)
Up to four featured repositories as poster plaques: index, name (display),
category label (cyan), logline (body), language, live star count (amber).
Repos and order come from `profile/config.json` (`featured`), validated
against the GitHub API at generation time; a repo that disappears degrades to
"archive" instead of breaking the build. Live data: stars, languages.

### Act IV — TRANSMISSION (`act4-transmission`, 820×300 / 390×480)
End credits. Channels as labeled lines: LinkedIn, Email, WhatsApp — each a
real link in the README, mirrored in the art. Sign-off line, sprocket footer,
year stamp. No live data.

Between every act, a film-sprocket divider is rendered as a thin standalone
strip (`divider.svg`) so scene breaks read as reel splices.

## 6. Motion

SMIL where it survives best; CSS keyframes where simpler. All animation is
entrance-only (the film starts when the page loads) plus three ambient loops.
Exact specification:

| Name | Applies to | Timing |
|---|---|---|
| `line-draw` | Constellation strokes | stroke-dashoffset → 0, 1200ms, `ease-out`, stagger 150ms per segment |
| `node-in` | Constellation nodes | opacity 0 → 1, 500ms, stagger 120ms, starts after its line-draw |
| `star-in` | Starfield | opacity 0 → target, 800ms, stagger `(i mod 12) × 40ms`, delay 300ms |
| `wordmark-in` | Wordmark | opacity 0→1, translateY 6px→0, 900ms `cubic-bezier(0.16,1,0.3,1)`, delay 600ms |
| `block-in` | Name, role, tagline, chips | opacity 0→1, 700ms, stagger 150ms, begins at 900ms |
| `beacon` | City beacon | opacity 1 ↔ 0.3, 2400ms `ease-in-out` infinite |
| `window-flicker` | ~6% of lit windows | opacity ±0.25, period 6–9s, seeded offset |
| `scroll-cue` | Caret in scroll cue | blink 1200ms `steps(2)` infinite |

Sequence budget for a title card: all entrance motion complete by 2400ms.
Ambient loops must be luminance-neutral (no visible pumping).

**Reduced motion:** every SVG contains
`@media (prefers-reduced-motion: reduce)` that disables all animation and
renders elements in their final state. No information may exist only in
motion.

## 7. Art-direction principles

Falsifiable, checkable per artboard:

1. **Night is the frame** — no background lighter than `#0A0F1E`.
2. **One accent does the work** — cyan is signal, amber is human heat, nothing
   else; language hues appear only in the signal bar.
3. **Data is scenery, never a card** — no bordered stat cards, no rounded
   gradient panels. Readouts are HUD lines; towers and plaques are masses.
4. **Negative space ≥ 55%** of every artboard.
5. **Hairlines, grain, vignette** unify every act; grain ≤ 10% opacity;
   vignette never crushes corner text.
6. **Deterministic** — same data in, byte-identical SVG out (seeded PRNG).

Banned: emoji section headings; shields.io badge walls; rounded gradient
cards; animated GIFs; third-party widget embeds; skill-icon grids; rotating
role typewriters; more than one glow color per scene; drop shadows on text
blocks; any external URL inside an SVG.

## 8. Accessibility

- Every artboard has a descriptive `alt` on its `<img>`.
- The README carries real, structured text for every act (headings, bio,
  project list with links, contact links). Nothing that matters lives only in
  an image.
- Chapter navigation uses native heading anchors.
- Reduced motion honored (§6).
- Body text contrast: `inkDim` on `night` ≥ 7:1; `inkFaint` microtype is
  decorative only, never the sole carrier of meaning.

## 9. Architecture

```
profile/config.json          identity, palette, features, featured repos, variant pick
profile/github-data.json     cached API snapshot (offline dev + tests)
scripts/generate_profile.py  sole generator: fetch → model → scenes → SVG
assets/act*.svg              generated artboards (desktop + mobile)
assets/divider.svg           sprocket divider strip
assets/fonts/*.ttf + OFL     committed font sources with licenses
.github/workflows/profile.yml  daily regen + dispatch + push, atomic
README.md                    authored once: structure, text, picture embeds
```

- Python stdlib + `fonttools` only. No Pillow while portrait is disabled.
- Determinism: one seeded PRNG per scene, seed in config; the only variant
  between runs is GitHub data itself.
- Failure safety: workflow generates into a temp dir and replaces `assets/`
  only on full success; a failed API call never publishes partial art.
- Caching: GitHub's image proxy may serve a cached copy for a while after a
  daily regen. If that is ever observed, append a `?v=YYYYMMDD` stamp to the
  README image URLs; do not rename assets.
- Retired: `profile-3d-city.svg` and `.github/workflows/city.yml` are removed
  once Act II verifies. The old README's pacman strip (which embedded another
  user's data) and all `Hoppay-sama` references die with the rewrite.

## 10. Verification gates

1. SVG lint: valid XML; zero external references; no `<script>`; size budget
   ≤ 120 KB per artboard; font paths present, no `<text>` without intent.
2. Deterministic regen: running the generator twice on the same data produces
   byte-identical files.
3. Render harness: README rendered with GitHub-like styles at 1280 / 768 /
   390 px, dark and light, plus reduced-motion pass; screenshots archived per
   round.
4. GitHub-side: branch blob preview renders; proxy image loads verified on the
   live page; console and network clean.
5. Ceiling gate: visual critic scored against the reference board — ≥ 8/10,
   zero P0/P1 findings. Fix rounds capped; plateaus reported with evidence.
6. Receipts: one line per round in the PR/commit trail.

## 11. Content decisions (locked)

- Nameplate: `OIONOS` hero + `MARC HERNANDEZ` + `SOFTWARE ENGINEER`.
- Channels: LinkedIn, Email (`Xiannadev@gmail.com`), WhatsApp. X/Twitter
  intentionally dropped (stale handle).
- Featured repos: Veritras, DesignVault, Maghapon-Cafe (Nescafei reserved as
  optional fourth; config decides).
- Dark-only imagery in both GitHub themes; two artboard sizes per act.
- Portrait: off (toggle retained in config).
- Live elements: contribution skyline, featured repos, stats HUD, language
  bar, local time. Last-activity line intentionally excluded.
