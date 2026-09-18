#!/usr/bin/env python3
"""OIONOS - Night Signal profile generator.

Reads profile/config.json + a GitHub data snapshot and writes the artboard
SVGs into assets/. DESIGN.md is the source of truth for the visual system.

Rules this file obeys (see DESIGN.md):
  - Output is self-contained: no external refs, no <script>, fonts as paths.
  - Deterministic: same data in -> byte-identical SVG out (seeded PRNG).
  - Layout only; palette and copy come from config.

Deps: fonttools (requirements.txt).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform

ROOT = Path(__file__).resolve().parents[1]
FONT_FILES = {
    "display": "BebasNeue-Regular.ttf",
    "mono": "JetBrainsMono-Regular.ttf",
    "monoMedium": "JetBrainsMono-Medium.ttf",
    "body": "Inter.ttf",
}
_NUM = re.compile(r"-?\d+\.\d+")


def round_path(d: str, digits: int = 1) -> str:
    """Trim float precision in path data. 0.1px is invisible at these sizes."""
    return _NUM.sub(lambda m: f"{float(m.group()):.{digits}f}", d)
ARTBOARDS = [
    ("act0-title", "title"),
    ("act1-watcher", "watcher"),
    ("act2-city", "city"),
    ("act3-records", "records"),
    ("act4-transmission", "transmission"),
    ("divider", "divider"),
]


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def demo_data(seed: int, today: date) -> dict:
    """Deterministic synthetic snapshot so art can be built without a token."""
    rng = random.Random(seed)
    days = []
    for i in range(365):
        d = today - timedelta(days=364 - i)
        base = 0.4 if d.weekday() >= 5 else 2.6
        c = max(0, int(rng.gauss(base, 2.1)))
        if rng.random() < 0.16:
            c = 0
        days.append({"date": d.isoformat(), "count": c})
    for k in range(1, 13):
        days[-k]["count"] = max(2, days[-k]["count"])
    commits = sum(d["count"] for d in days) + 731
    return {
        "fetchedAt": datetime.combine(today, time.min, tzinfo=timezone.utc).isoformat(timespec="seconds"),
        "user": {"followers": 42, "publicRepos": 12},
        "totals": {"stars": 38, "pullRequests": 96, "commits12mo": commits},
        "days": days,
        "repos": {
            "veritras": {"stars": 1, "language": "TypeScript"},
            "designvault": {"stars": 1, "language": "MDX"},
            "maghapon-cafe": {"stars": 0, "language": "Astro"},
        },
        "languages": [
            {"name": "TypeScript", "pct": 41},
            {"name": "Python", "pct": 22},
            {"name": "Solidity", "pct": 14},
            {"name": "Astro", "pct": 9},
            {"name": "Other", "pct": 14},
        ],
    }


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "oionos-night-signal",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_live(cfg: dict, token: str) -> dict:
    """Fetch a snapshot from the GitHub API (Action use)."""
    login = cfg["identity"]["login"]
    user = _get_json(f"https://api.github.com/users/{login}", token)
    repos = _get_json(
        f"https://api.github.com/users/{login}/repos?per_page=100&sort=pushed", token
    )

    query = """
    query($login:String!){
      user(login:$login){
        contributionsCollection{
          totalCommitContributions
          totalPullRequestContributions
          contributionCalendar{
            totalContributions
            weeks{ contributionDays{ date contributionCount } }
          }
        }
      }
    }"""
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": {"login": login}}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "oionos-night-signal",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        gql = json.load(r)["data"]["user"]["contributionsCollection"]

    days = [
        {"date": d["date"], "count": d["contributionCount"]}
        for w in gql["contributionCalendar"]["weeks"]
        for d in w["contributionDays"]
    ]
    stars = sum(r["stargazers_count"] for r in repos if not r["fork"])
    sizes: dict[str, int] = {}
    for r in repos:
        lang = r.get("language")
        if lang:
            sizes[lang] = sizes.get(lang, 0) + max(r.get("size", 0), 1)
    top = sorted(sizes.items(), key=lambda kv: -kv[1])[:4]
    total = sum(sizes.values()) or 1
    languages = [
        {"name": n, "pct": round(100 * s / total)} for n, s in top
    ]
    used = sum(l["pct"] for l in languages)
    if used < 100:
        languages.append({"name": "Other", "pct": 100 - used})

    return {
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user": {
            "followers": user.get("followers", 0),
            "publicRepos": user.get("public_repos", 0),
        },
        "totals": {
            "stars": stars,
            "pullRequests": gql["totalPullRequestContributions"],
            "commits12mo": gql["totalCommitContributions"],
        },
        "days": days,
        "repos": {
            r["name"].lower(): {"stars": r["stargazers_count"], "language": r.get("language") or "Code"}
            for r in repos
        },
        "languages": languages,
    }


# --------------------------------------------------------------------------
# fonts -> paths
# --------------------------------------------------------------------------

class Fonts:
    """Specimen text as SVG path data. Kerning is intentionally skipped:
    display type is tracked out anyway and body text is short. ponytail:
    add GPOS kerning only if a scene ever reads visibly loose."""

    def __init__(self, font_dir: Path):
        self.dir = font_dir
        self._cache: dict[str, tuple] = {}

    def _face(self, face: str):
        if face not in self._cache:
            from fontTools.ttLib import TTFont
            from fontTools.varLib.instancer import instantiateVariableFont

            font = TTFont(self.dir / FONT_FILES[face], lazy=True)
            if "fvar" in font:
                for axis in font["fvar"].axes:
                    if axis.axisTag == "wght" and abs(axis.defaultValue - 400) > 1:
                        font = instantiateVariableFont(font, {"wght": 400})
                        break
            self._cache[face] = (
                font.getGlyphSet(),
                font.getBestCmap(),
                font["hmtx"],
                font["head"].unitsPerEm,
            )
        return self._cache[face]

    def measure(self, face: str, text: str, size: float, tracking: float = 0.0) -> float:
        _, cmap, hmtx, upm = self._face(face)
        scale = size / upm
        w = 0.0
        for ch in text:
            gname = cmap.get(ord(ch))
            w += (hmtx[gname][0] * scale if gname else size * 0.5) + tracking
        return max(0.0, w - (tracking if text else 0.0))

    def path(self, face: str, text: str, size: float, tracking: float = 0.0) -> str:
        glyphs, cmap, hmtx, upm = self._face(face)
        scale = size / upm
        digits = 0 if size < 10 else 1
        cursor = 0.0
        parts = []
        for ch in text:
            gname = cmap.get(ord(ch))
            if gname is None:
                cursor += size * 0.5 + tracking
                continue
            pen = SVGPathPen(glyphs)
            glyphs[gname].draw(TransformPen(pen, Transform(scale, 0, 0, -scale, cursor, 0)))
            d = pen.getCommands()
            if d:
                parts.append(round_path(d, digits))
            cursor += hmtx[gname][0] * scale + tracking
        return " ".join(parts)

    def wrap(self, face: str, text: str, size: float, tracking: float, max_w: float) -> list[str]:
        words = text.split()
        lines, cur = [], ""
        for word in words:
            probe = f"{cur} {word}".strip()
            if self.measure(face, probe, size, tracking) <= max_w or not cur:
                cur = probe
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines


# --------------------------------------------------------------------------
# svg primitives
# --------------------------------------------------------------------------

REDUCED_MOTION = """
@media (prefers-reduced-motion: reduce){
  *{animation:none !important}
  .b{opacity:1 !important}
  .l{stroke-dashoffset:0 !important}
  .n,.s{opacity:1 !important}
  .caret{opacity:1 !important}
}
"""


class Svg:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.defs: list[str] = []
        self.body: list[str] = []

    def add_def(self, s: str) -> None:
        self.defs.append(s)

    def add(self, s: str) -> None:
        self.body.append(s)

    def render(self, css: str) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}">'
            f"<defs>{''.join(self.defs)}</defs>"
            f"<style>{css}{REDUCED_MOTION}</style>"
            f"{''.join(self.body)}</svg>"
        )


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bg_gradient(svg: Svg, gid: str, stops: list[tuple[float, str, float]]) -> None:
    parts = "".join(
        f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in stops
    )
    svg.add_def(f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">{parts}</linearGradient>')


def vignette_grain(svg: Svg, pal: dict, grain: float, vig: float) -> None:
    svg.add_def(
        f'<radialGradient id="vig" cx="0.5" cy="0.38" r="1.05">'
        f'<stop offset="0.5" stop-color="#000" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#000" stop-opacity="{vig}"/></radialGradient>'
    )
    svg.add_def(
        '<filter id="grain" x="0" y="0" width="100%" height="100%">'
        '<feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch"/>'
        '<feColorMatrix type="saturate" values="0"/></filter>'
    )
    svg.add(f'<rect width="{svg.w}" height="{svg.h}" fill="url(#vig)"/>')
    if grain > 0:
        svg.add(
            f'<rect width="{svg.w}" height="{svg.h}" filter="url(#grain)" opacity="{grain}"/>'
        )


def text(
    svg: Svg,
    fonts: Fonts,
    face: str,
    s: str,
    size: float,
    x: float,
    y: float,
    fill: str,
    tracking: float = 0.0,
    anchor: str = "start",
    opacity: float = 1.0,
    cls: str = "",
    style: str = "",
) -> float:
    """Place a run of text (as paths). y is the baseline. Returns width."""
    d = fonts.path(face, s, size, tracking)
    w = fonts.measure(face, s, size, tracking)
    ax = {"start": 0.0, "middle": -w / 2, "end": -w}[anchor]
    if d:
        cs = f' class="{cls}"' if cls else ""
        stl = f' style="{style}"' if style else ""
        svg.add(
            f'<path{cs}{stl} transform="translate({x + ax:.2f} {y:.2f})" '
            f'd="{d}" fill="{fill}" opacity="{opacity}"/>'
        )
    return w


def line(x1, y1, x2, y2, stroke, width=1, opacity=1.0, cls="", style="", dash=None) -> str:
    da = f' stroke-dasharray="{dash}" stroke-dashoffset="{dash}"' if dash else ""
    cs = f' class="{cls}"' if cls else ""
    stl = f' style="{style}"' if style else ""
    return (
        f'<line{cs}{stl} x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{width}" opacity="{opacity}"{da}/>'
    )


def rect(x, y, w, h, fill, opacity=1.0, rx=0, stroke=None, sw=1, cls="", style="") -> str:
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    cs = f' class="{cls}"' if cls else ""
    stl = f' style="{style}"' if style else ""
    return (
        f'<rect{cs}{stl} x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
        f'fill="{fill}" opacity="{opacity}"{st}/>'
    )


def rpath(x: float, y: float, w: float, h: float) -> str:
    """A rectangle as a subpath for bulk geometry."""
    return f"M{x:.1f} {y:.1f}h{w:.1f}v{h:.1f}h-{w:.1f}Z"


def circle(cx, cy, r, fill, opacity=1.0, stroke=None, sw=1, cls="", style="") -> str:
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    cs = f' class="{cls}"' if cls else ""
    stl = f' style="{style}"' if style else ""
    return (
        f'<circle{cs}{stl} cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
        f'fill="{fill}" opacity="{opacity}"{st}/>'
    )


def star_path(cx: float, cy: float, r: float) -> str:
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.42
        pts.append(f"{cx + rad * math.cos(ang):.2f},{cy + rad * math.sin(ang):.2f}")
    return "M" + "L".join(pts) + "Z"


def starfield(svg: Svg, rng: random.Random, pal: dict, n: int, y_max: float,
              w: float, animate: bool = True) -> None:
    for i in range(n):
        x, y = rng.uniform(0, w), rng.uniform(0, y_max)
        r = 1.35 if rng.random() > 0.86 else 0.8
        op = round(rng.uniform(0.14, 0.62), 2)
        if animate and i % 4 == 0:
            delay = round(0.3 + (i % 12) * 0.04, 2)
            svg.add(circle(x, y, r, pal["star"], op, cls="s", style=f"animation-delay:{delay}s"))
        else:
            svg.add(circle(x, y, r, pal["star"], op))


def bird_constellation(svg: Svg, pal: dict, x: float, y: float, s: float,
                       opacity: float = 0.8, cls_stroke: str = "l") -> None:
    """The omen-bird asterism, base art 400x220, scaled to width s."""
    k = s / 400.0
    g = f'<g transform="translate({x:.1f} {y:.1f}) scale({k:.4f})" opacity="{opacity}">'
    segs = [
        ("M40 150 Q120 34 200 100", 196),
        ("M200 100 Q280 34 360 150", 196),
    ]
    for i, (d, ln) in enumerate(segs):
        g += (
            f'<path class="{cls_stroke}" style="animation-delay:{0.15 * i:.2f}s" d="{d}" '
            f'fill="none" stroke="{pal["cyan"]}" stroke-width="0.8" '
            f'stroke-dasharray="{ln}" stroke-dashoffset="{ln}"/>'
        )
    nodes = [(40, 150, 1.6), (120, 80, 1.2), (200, 100, 2.6), (280, 80, 1.2), (360, 150, 1.6)]
    for i, (cx, cy, r) in enumerate(nodes):
        col = pal["cyan"] if (cx, cy) == (200, 100) else pal["star"]
        g += (
            f'<circle class="n" style="animation-delay:{0.35 + 0.12 * i:.2f}s" '
            f'cx="{cx}" cy="{cy}" r="{r}" fill="{col}" opacity="0"/>'
        )
    g += "</g>"
    svg.add(g)


def sprockets(svg: Svg, pal: dict, x0: float, y: float, w: float, hole_w=22, gap=16, h=9) -> None:
    n = int((w + gap) // (hole_w + gap))
    total = n * hole_w + (n - 1) * gap
    cx = x0 + (w - total) / 2
    for i in range(n):
        svg.add(
            rect(cx + i * (hole_w + gap), y, hole_w, h, "none", 1.0, rx=2,
                 stroke=pal["line"], sw=0.9)
        )


def hud_frame(svg: Svg, pal: dict, inset: float = 12) -> None:
    svg.add(
        f'<rect x="{inset}" y="{inset}" width="{svg.w - 2 * inset}" height="{svg.h - 2 * inset}" '
        f'fill="none" stroke="{pal["line"]}" stroke-width="0.7" opacity="0.5"/>'
    )


BASE_CSS = """
.b{opacity:0;animation:blockIn .7s cubic-bezier(.16,1,.3,1) forwards}
@keyframes blockIn{to{opacity:1}}
.s{opacity:0;animation:starIn .8s ease-out forwards}
@keyframes starIn{to{opacity:1}}
.l{animation:lineDraw 1.2s ease-out forwards}
@keyframes lineDraw{to{stroke-dashoffset:0}}
.n{animation:nodeIn .5s ease-out forwards}
@keyframes nodeIn{to{opacity:1}}
.beacon{animation:pulse 2.4s ease-in-out infinite}
@keyframes pulse{50%{opacity:.3}}
.beam{animation:beamPulse 2.4s ease-in-out infinite}
@keyframes beamPulse{0%,100%{opacity:.04}50%{opacity:.09}}
.caret{animation:blink 1.2s steps(2) infinite}
@keyframes blink{50%{opacity:0}}
.f{animation:flicker 7s ease-in-out infinite}
@keyframes flicker{0%,100%{opacity:.7}50%{opacity:.25}}
"""


# --------------------------------------------------------------------------
# scenes
# --------------------------------------------------------------------------

def scene_title(cfg, data, fonts: Fonts, mobile: bool):
    pal = cfg["palette"]
    identity, scene = cfg["identity"], cfg["scene"]
    variant = cfg["variant"]["hero"]
    W, H = (390, 620) if mobile else (820, 430)
    svg = Svg(W, H)
    rng = random.Random(scene["seed"] + (7 if mobile else 0))
    bg_gradient(svg, "bg", [(0, pal["void"], 1), (0.62, "#071026", 1), (1, "#0B1632", 1)])
    svg.add(rect(0, 0, W, H, "url(#bg)"))
    starfield(svg, rng, pal, 55 if mobile else 90, H * 0.85, W)

    cx = W / 2
    if not mobile and variant == "B":
        bird_constellation(svg, pal, 44, 40, 230, 0.5)
        base = H - 46
        rr = random.Random(scene["seed"] + 3)
        subs = ""
        for i in range(52):
            bh = rr.uniform(6, 38)
            subs += rpath(16 + i * 15.2, base - bh, 11, bh)
        svg.add(
            f'<g class="b" style="animation-delay:.5s">'
            f'<path d="{subs}" fill="{pal["panel"]}" opacity="0.8"/></g>'
        )
        svg.add(line(16, base, W - 16, base, pal["line"], 0.8, 0.9))
    elif not mobile and variant == "C":
        bird_constellation(svg, pal, 640, 36, 160, 0.55)
    else:
        bird_constellation(svg, pal, cx - (165 if mobile else 160), 56 if mobile else 4,
                           330 if mobile else 320, 0.85)

    wm_size = (46 if mobile else (96 if variant == "C" else 76))
    wm_track = wm_size * 0.32
    wm_y = 300 if mobile else (232 if variant == "C" else 244)
    wm_w = fonts.measure("display", identity["handle"], wm_size, wm_track)
    d = fonts.path("display", identity["handle"], wm_size, wm_track)
    svg.add_def(
        '<filter id="glow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feGaussianBlur stdDeviation="7"/></filter>'
    )
    svg.add(
        f'<g class="b" style="animation-delay:.55s">'
        f'<path transform="translate({cx - wm_w / 2:.1f} {wm_y})" d="{d}" fill="{pal["cyan"]}" '
        f'opacity="0.22" filter="url(#glow)"/>'
        f'<path transform="translate({cx - wm_w / 2:.1f} {wm_y})" d="{d}" fill="{pal["ink"]}"/>'
        f"</g>"
    )

    y = wm_y + (30 if mobile else 28)
    text(svg, fonts, "mono", identity["name"], 9 if mobile else 10, cx, y, pal["cyan"],
         3.6 if mobile else 4.6, "middle", cls="b", style="animation-delay:.9s")
    y += 18 if mobile else 22
    text(svg, fonts, "mono", identity["role"], 8 if mobile else 10, cx, y, pal["inkDim"],
         2.4, "middle", cls="b", style="animation-delay:1.05s")

    y += 30 if mobile else 32
    max_w = (W - 70) if mobile else 560
    lines = fonts.wrap("body", identity["tagline"], 12 if mobile else 13, 0, max_w)
    for i, ln in enumerate(lines):
        text(svg, fonts, "body", ln, 12 if mobile else 13, cx, y + i * 20, pal["inkDim"],
             0, "middle", cls="b", style=f"animation-delay:{1.2 + i * 0.1:.2f}s")

    y += (len(lines) - 1) * 20 + 26
    text(svg, fonts, "mono", identity["availability"], 8 if mobile else 9, cx, y,
         pal["cyanDim"], 2.2, "middle", cls="b", style="animation-delay:1.45s")

    if mobile:
        chips = "01 WATCHER · 02 CITY · 03 RECORDS · 04 TRANSMISSION"
        text(svg, fonts, "mono", chips, 7.5, cx, H - 46, pal["inkFaint"], 1.6, "middle",
             cls="b", style="animation-delay:1.6s")

    text(svg, fonts, "mono", identity["coords"], 9, 20, 24, pal["inkFaint"], 1.4)
    if cfg["features"]["localTime"]:
        try:
            fetched = datetime.fromisoformat(data.get("fetchedAt", ""))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
        except ValueError:
            fetched = datetime.now(timezone.utc)
        local = fetched
        try:
            from zoneinfo import ZoneInfo
            local = fetched.astimezone(ZoneInfo(identity["timezone"]))
        except Exception:
            pass
        text(svg, fonts, "mono", f"SIGNAL {local.strftime('%H:%M')} {identity['tzLabel']}",
             9, W - 20, 24, pal["inkFaint"], 1.4, "end")
    text(svg, fonts, "mono", f"LAST REGEN {scene['regenUtc']} UTC", 8, 20, H - 18,
         pal["inkFaint"], 1.4)
    cue = "SCROLL TO BEGIN" if not mobile else "SCROLL"
    text(svg, fonts, "mono", cue, 8, W - 30, H - 18, pal["inkFaint"], 1.4, "end")
    svg.add(rect(W - 24, H - 25, 4, 9, pal["cyan"], 0.8, cls="caret"))

    hud_frame(svg, pal)
    vignette_grain(svg, pal, scene["grain"] * 0.6, scene["vignette"])
    return svg


def scene_watcher(cfg, data, fonts: Fonts, mobile: bool):
    pal = cfg["palette"]
    identity, scene = cfg["identity"], cfg["scene"]
    W, H = (390, 460) if mobile else (820, 300)
    svg = Svg(W, H)
    rng = random.Random(scene["seed"] + 11)
    svg.add(rect(0, 0, W, H, pal["night"]))
    starfield(svg, rng, pal, 14 if mobile else 22, 56, W, animate=False)
    m = 20 if mobile else 40

    text(svg, fonts, "mono", f"ACT I — THE WATCHER · {scene['timecodes']['watcher']}",
         8 if mobile else 9, m, 30, pal["cyan"], 1.8)
    text(svg, fonts, "display", "THE WATCHER", 26 if mobile else 32, m, 68,
         pal["ink"], 4.6)
    svg.add(line(m, 82, W - m, 82, pal["line"], 0.8, 0.8))

    y = 112 if not mobile else 104
    lines = fonts.wrap("body", identity["tagline"], 12, 0, W - 2 * m)
    for i, ln in enumerate(lines):
        text(svg, fonts, "body", ln, 12, m, y + i * 19, pal["inkDim"])
    y += len(lines) * 19 + 10
    text(svg, fonts, "mono", identity["availability"] + f" · {identity['coords']}",
         8, m, y, pal["cyanDim"], 1.6)

    if mobile:
        ys = y + 42
        for group in cfg["stack"]:
            text(svg, fonts, "mono", group["label"], 8, m, ys, pal["inkFaint"], 2)
            text(svg, fonts, "mono", " · ".join(group["items"]), 9, m, ys + 16,
                 pal["inkDim"], 0.8)
            ys += 42
            svg.add(line(m, ys - 20, W - m, ys - 20, pal["lineFaint"], 0.6, 0.7))
    else:
        groups = cfg["stack"]
        colw = (W - 2 * m) / len(groups)
        y0 = 196
        for i, group in enumerate(groups):
            x = m + i * colw
            if i:
                svg.add(line(x - 10, y0 - 26, x - 10, H - 34, pal["lineFaint"], 0.6, 0.7))
            text(svg, fonts, "mono", group["label"], 8, x, y0, pal["inkFaint"], 2)
            for j, item in enumerate(group["items"]):
                text(svg, fonts, "mono", item, 9.5, x, y0 + 20 + j * 16, pal["inkDim"], 0.6)

    hud_frame(svg, pal)
    vignette_grain(svg, pal, scene["grain"] * 0.5, scene["vignette"] * 0.8)
    return svg


def weeks_from_days(days: list[dict]) -> list[dict]:
    """Group day records into Mon-start weeks."""
    weeks: list[dict] = []
    cur: list[dict] = []
    for d in days:
        cur.append(d)
        if date.fromisoformat(d["date"]).weekday() == 6:
            weeks.append({"days": cur, "total": sum(x["count"] for x in cur)})
            cur = []
    if cur:
        weeks.append({"days": cur, "total": sum(x["count"] for x in cur)})
    return weeks


def current_streak(days: list[dict]) -> int:
    streak = 0
    for d in reversed(days):
        if d["count"] > 0:
            streak += 1
        else:
            break
    return streak


def scene_city(cfg, data, fonts: Fonts, mobile: bool):
    pal = cfg["palette"]
    scene, langs = cfg["scene"], data["languages"]
    W, H = (390, 720) if mobile else (820, 560)
    svg = Svg(W, H)
    rng = random.Random(scene["seed"] + (23 if mobile else 17))
    bg_gradient(svg, "bg", [(0, pal["void"], 1), (0.55, "#060D20", 1), (1, "#0A1430", 1)])
    svg.add(rect(0, 0, W, H, "url(#bg)"))
    starfield(svg, rng, pal, 40 if mobile else 80, H * 0.55, W)

    if cfg["features"]["moon"] and not mobile:
        svg.add(circle(752, 88, 20, pal["star"], 0.05))
        svg.add(circle(752, 88, 11, pal["star"], 0.16))
        svg.add(circle(752, 88, 20, pal["cyan"], 0.04))

    text(svg, fonts, "mono", f"ACT II — THE CITY · {scene['timecodes']['city']}",
         8 if mobile else 9, 20, 30, pal["cyan"], 1.8)

    weeks = weeks_from_days(data["days"])
    n = len(weeks)
    pitch = ((W - 2 * 18) / n) if mobile else ((W - 2 * 20) / n)
    bw = pitch * 0.72
    x0 = (W - n * pitch) / 2 + (pitch - bw) / 2
    baseline = H * 0.64 if mobile else 440
    max_h = H * 0.42 if mobile else 300
    if mobile:
        # pair weeks so towers stay 13px+ wide
        merged = []
        for i in range(0, n - 1, 2):
            merged.append({"days": weeks[i]["days"] + weeks[i + 1]["days"],
                           "total": weeks[i]["total"] + weeks[i + 1]["total"]})
        if n % 2:
            merged.append(weeks[-1])
        weeks = merged
        n = len(weeks)
        pitch = (W - 2 * 18) / n
        bw = pitch * 0.72
        x0 = (W - n * pitch) / 2 + (pitch - bw) / 2

    top = max(w["total"] for w in weeks) or 1
    rich = cfg["variant"]["city"] == "rich"
    tower_bands: dict[float, list[str]] = {0.35: [], 0.55: [], 0.7: [], 0.85: [], 0.95: []}
    win_bands: dict[float, list[str]] = {0.3: [], 0.5: [], 0.7: []}
    edges: list[str] = []
    flickers: list[str] = []
    beacon: tuple[float, float] | None = None
    for i, wk in enumerate(weeks):
        x = x0 + i * pitch
        if wk["total"] == 0:
            tower_bands[0.35].append(rpath(x, baseline - 5, bw, 5))
            continue
        h = 18 + (max_h - 18) * math.sqrt(wk["total"] / top) * rng.uniform(0.86, 1.0)
        band = min(tower_bands, key=lambda b: abs(b - rng.uniform(0.55, 0.95)))
        tower_bands[band].append(rpath(x, baseline - h, bw, h))
        if not mobile:
            edges.append(rpath(x, baseline - h, bw, 1.2))
        beacon = (x + bw / 2, baseline - h)
        days_lit = [d for d in wk["days"] if d["count"] > 0]
        if days_lit and (rich or wk["total"] >= top * 0.35):
            for j, day in enumerate(days_lit):
                if not rich and rng.random() < 0.35:
                    continue
                wx = x + bw * (0.2 + 0.35 * (j % 2))
                n_win = 1 + min(3, day["count"]) if rich else 1 + min(2, day["count"])
                for _ in range(n_win):
                    wy = baseline - rng.uniform(8, max(10, h - 5))
                    if rng.random() < 0.06:
                        flickers.append(
                            rect(wx, wy, 2.6, 3.8, pal["amber"], 0.6, cls="f",
                                 style=f"animation-delay:{rng.uniform(0, 6):.1f}s")
                        )
                    else:
                        wop = min(win_bands, key=lambda b: abs(b - rng.uniform(0.25, 0.7)))
                        win_bands[wop].append(rpath(wx, wy, 2.6, 3.8))
    towers = ""
    for op, subs in tower_bands.items():
        if subs:
            towers += f'<path d="{"".join(subs)}" fill="{pal["panel"]}" opacity="{op}"/>'
    if edges:
        towers += f'<path d="{"".join(edges)}" fill="{pal["line"]}" opacity="0.9"/>'
    for op, subs in win_bands.items():
        if subs:
            towers += f'<path d="{"".join(subs)}" fill="{pal["amber"]}" opacity="{op}"/>'
    towers += "".join(flickers)
    if beacon:
        beacon_x, beacon_top = beacon
        towers += (
            f'<polygon class="beam" points="{beacon_x - 6:.1f},{beacon_top:.1f} '
            f'{beacon_x + 6:.1f},{beacon_top:.1f} {beacon_x + 16:.1f},{max(beacon_top - 120, 8):.1f} '
            f'{beacon_x - 16:.1f},{max(beacon_top - 120, 8):.1f}" fill="{pal["amber"]}" opacity="0.055"/>'
        )
        towers += circle(beacon_x, beacon_top - 2, 2.6, pal["amber"], 0.9, cls="beacon")
    svg.add(f'<g class="b" style="animation-delay:.5s">{towers}</g>')

    svg.add(line(18, baseline + 0.5, W - 18, baseline + 0.5, pal["line"], 0.8, 0.9))
    months = {}
    for i, wk in enumerate(weeks):
        for d in wk["days"]:
            dt = date.fromisoformat(d["date"])
            if dt.month not in months:
                months[dt.month] = i
    step = 2 if mobile else 1
    for m_i, (month, idx) in enumerate(sorted(months.items())):
        if m_i % step:
            continue
        mx = x0 + idx * pitch
        svg.add(line(mx, baseline + 1, mx, baseline + 5, pal["lineFaint"], 0.7, 0.9))
        name = date(2000, month, 1).strftime("%b").upper()
        text(svg, fonts, "mono", name, 7, mx, baseline + 15, pal["inkFaint"], 1)

    if mobile:
        text(svg, fonts, "mono", "COMMITS AS A SKYLINE", 8, W - 20, 30, pal["inkFaint"], 1.6, "end")
    else:
        text(svg, fonts, "mono", "ONE YEAR OF COMMITS, BUILT AS A SKYLINE", 8,
             W - 20, 30, pal["inkFaint"], 1.6, "end")
        text(svg, fonts, "mono", "EVERY LIT WINDOW IS A DAY YOU SHIPPED", 8, W - 20, 46,
             pal["inkFaint"], 1.6, "end", 0.75)

    sy = baseline + 55
    svg.add(line(20, sy, W - 20, sy, pal["line"], 0.7, 0.7))
    stats = [
        ("COMMITS · 12 MO", f"{data['totals']['commits12mo']:,}"),
        ("STARS", f"{data['totals']['stars']:,}"),
        ("PULL REQUESTS", f"{data['totals']['pullRequests']:,}"),
    ]
    if mobile:
        widths = (W - 40) / 3
        for i, (label, val) in enumerate(stats):
            x = 20 + i * widths
            text(svg, fonts, "mono", label, 7, x, sy + 24, pal["inkFaint"], 1.2)
            text(svg, fonts, "display", val, 22, x, sy + 52, pal["ink"], 2.4)
        by = sy + 78
    else:
        x = 40
        for label, val in stats:
            text(svg, fonts, "mono", label, 8, x, sy + 22, pal["inkFaint"], 1.6)
            text(svg, fonts, "display", val, 30, x, sy + 54, pal["ink"], 3)
            x += 160
        by = sy + 30

    total_pct = sum(l["pct"] for l in langs) or 100
    bx, bw_ = (20, W - 40) if mobile else (470, W - 490)
    lane = 4 if not mobile else 5
    svg.add(rect(bx, by - 8, bw_, lane, pal["lineFaint"], 1, rx=lane / 2))
    cursor = 0.0
    seg_bounds = []
    for l in langs:
        seg = bw_ * l["pct"] / total_pct
        svg.add(rect(bx + cursor, by - 8, max(seg - 1, 1), lane,
                     cfg["languages"]["colors"].get(l["name"], pal["inkFaint"]), 0.95, rx=lane / 2))
        seg_bounds.append((cursor, cursor + seg, l))
        cursor += seg
    for x0_, x1_, l in seg_bounds[:3]:
        text(svg, fonts, "mono", f"{l['name']} {l['pct']}%", 7,
             bx + (x0_ + x1_) / 2, by + 13, pal["inkFaint"], 0.8, "middle")

    hud_frame(svg, pal)
    vignette_grain(svg, pal, scene["grain"], scene["vignette"])
    return svg


def scene_records(cfg, data, fonts: Fonts, mobile: bool):
    pal = cfg["palette"]
    scene = cfg["scene"]
    W, H = (390, 560) if mobile else (820, 400)
    svg = Svg(W, H)
    rng = random.Random(scene["seed"] + 5)
    bg_gradient(svg, "bg", [(0, pal["night"], 1), (1, "#070C1C", 1)])
    svg.add(rect(0, 0, W, H, "url(#bg)"))
    starfield(svg, rng, pal, 24, H * 0.4, W, animate=False)

    text(svg, fonts, "mono", f"ACT III — THE RECORDS · {scene['timecodes']['records']}",
         8 if mobile else 9, 20, 30, pal["cyan"], 1.8)
    if not mobile:
        text(svg, fonts, "mono", "LIVE STARS · UPDATED DAILY", 9,
             W - 20, 30, pal["inkFaint"], 1.6, "end")

    featured = cfg["featured"]
    margin = 20
    if mobile:
        gap, ph = 12, 124
        y = 52
        boxes = [(margin, y + i * (ph + gap), W - 2 * margin, ph)
                 for i in range(len(featured))]
    else:
        gap = 14
        pw = (W - 2 * margin - gap * (len(featured) - 1)) / len(featured)
        boxes = [(margin + i * (pw + gap), 54, pw, H - 108)
                 for i in range(len(featured))]

    for idx, ((bx, by, pw, ph), item) in enumerate(zip(boxes, featured), start=1):
        repo = data["repos"].get(item["repo"].lower(), {})
        stars = repo.get("stars", 0)
        lang = repo.get("language", "Code")
        svg.add(f'<g class="b" style="animation-delay:{0.25:.2f}s">')
        svg.add(rect(bx, by, pw, ph, pal["panel"], 0.45))
        svg.add(f'<rect x="{bx}" y="{by}" width="{pw}" height="{ph}" fill="none" stroke="{pal["line"]}" stroke-width="0.8"/>')
        svg.add(line(bx, by, bx + 10, by, pal["cyan"], 1, 0.75))
        svg.add(line(bx, by, bx, by + 10, pal["cyan"], 1, 0.75))
        tx = bx + 16
        text(svg, fonts, "mono", f"0{idx} / FEATURE", 8, tx, by + 22,
             pal["inkFaint"], 1.8)
        text(svg, fonts, "display", item["repo"].upper(), 24 if mobile else 26, tx, by + 52,
             pal["ink"], 4)
        text(svg, fonts, "mono", item["category"], 8, tx, by + 68, pal["cyan"], 2)
        ll = fonts.wrap("body", item["logline"], 11 if mobile else 12, 0, pw - 32)
        for i, ln in enumerate(ll[:1 if mobile else 3]):
            text(svg, fonts, "body", ln, 11 if mobile else 12, tx, by + 90 + i * 17,
                 pal["inkDim"], 0)
        fy = by + ph - 16
        text(svg, fonts, "mono", lang.upper(), 8, tx, fy, pal["inkFaint"], 1.4)
        sw_ = fonts.measure("mono", str(stars), 9, 1.2)
        svg.add(f'<path transform="translate({bx + pw - 16 - sw_ - 14:.1f} {fy - 3:.1f})" '
                f'd="{star_path(0, 0, 4.4)}" fill="{pal["amber"]}" opacity="0.9"/>')
        text(svg, fonts, "mono", str(stars), 9, bx + pw - 16, fy, pal["amber"], 1.2, "end")
        text(svg, fonts, "display", f"0{idx}", 88 if not mobile else 64,
             bx + pw - 18, by + ph - 20, pal["ink"], 0, "end", 0.06)
        svg.add("</g>")

    html = "NEXT — ACT IV · TRANSMISSION"
    text(svg, fonts, "mono", html, 8, 20, H - 18, pal["inkFaint"], 1.6)
    hud_frame(svg, pal)
    vignette_grain(svg, pal, scene["grain"] * 0.5, scene["vignette"] * 0.75)
    return svg


def scene_transmission(cfg, data, fonts: Fonts, mobile: bool):
    pal = cfg["palette"]
    scene = cfg["scene"]
    W, H = (390, 480) if mobile else (820, 300)
    svg = Svg(W, H)
    rng = random.Random(scene["seed"] + 3)
    svg.add(rect(0, 0, W, H, pal["night"]))
    starfield(svg, rng, pal, 30 if mobile else 50, H * 0.6, W, animate=False)
    cx = W / 2

    text(svg, fonts, "mono", f"ACT IV — TRANSMISSION · {scene['timecodes']['transmission']}",
         8 if mobile else 9, cx, 36, pal["cyan"], 1.8, "middle")
    text(svg, fonts, "display", "TRANSMISSION", 34 if mobile else 46, cx,
         92 if mobile else 96, pal["ink"], 6.5, "middle")
    svg.add(line(W * 0.3, 108 if mobile else 114, W * 0.7, 108 if mobile else 114,
                 pal["line"], 0.8, 0.8))

    links = cfg["links"]
    if mobile:
        y = 150
        for l in links:
            text(svg, fonts, "mono", l["label"], 8, cx, y, pal["cyanDim"], 2.2, "middle")
            text(svg, fonts, "mono", l["value"], 10.5, cx, y + 17, pal["inkDim"], 0.8, "middle")
            y += 54
    else:
        xs = [W * 0.22, W * 0.5, W * 0.78]
        for x, l in zip(xs, links):
            text(svg, fonts, "mono", l["label"], 9, x, 170, pal["cyanDim"], 2.2, "middle")
            text(svg, fonts, "mono", l["value"], 11, x, 192, pal["inkDim"], 0.8, "middle")
        for x, l in zip(xs[1:], links[1:]):
            text(svg, fonts, "mono", "·", 11, x - (xs[1] - xs[0]) / 2, 192,
                 pal["inkFaint"], 0, "middle")

    text(svg, fonts, "mono", "SIGNAL ENDS · THANK YOU FOR SCROLLING",
         7.5, cx, H - 46 if mobile else H - 52, pal["inkFaint"], 1.6, "middle")
    text(svg, fonts, "mono", f"© {cfg['identity'].get('year', 2026)} OIONOS",
         7.5, cx, H - 32 if mobile else H - 38, pal["inkFaint"], 1.6, "middle", 0.7)
    sprockets(svg, pal, 20, H - 18, W - 40)
    vignette_grain(svg, pal, scene["grain"] * 0.4, scene["vignette"] * 0.7)
    return svg


def scene_divider(cfg, data, fonts: Fonts, mobile: bool):
    pal = cfg["palette"]
    W, H = (390, 20) if mobile else (820, 20)
    svg = Svg(W, H)
    # Deliberately no background: the splice strip floats on whatever theme
    # renders it, so it never reads as a black bar on the page.
    sprockets(svg, pal, 0, 5.5, W, hole_w=22, gap=16, h=9)
    return svg


SCENES = {
    "title": scene_title,
    "watcher": scene_watcher,
    "city": scene_city,
    "records": scene_records,
    "transmission": scene_transmission,
    "divider": scene_divider,
}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def render_all(cfg: dict, data: dict, fonts: Fonts, out_dir: Path) -> list[tuple[str, int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = []
    for name, key in ARTBOARDS:
        for mobile, suffix in ((False, ""), (True, "-m")):
            svg = SCENES[key](cfg, data, fonts, mobile)
            markup = svg.render(BASE_CSS)
            for bad in ("<script", "xlink:href", "url(http", "@import", 'href="http'):
                if bad in markup:
                    raise SystemExit(f"self-containment check failed: {bad} in {name}{suffix}")
            path = out_dir / f"{name}{suffix}.svg"
            path.write_text(markup, encoding="utf-8")
            receipt.append((f"{name}{suffix}.svg", len(markup.encode("utf-8"))))
    return receipt


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Night Signal artboards")
    ap.add_argument("--config", default=str(ROOT / "profile" / "config.json"))
    ap.add_argument("--data", default=str(ROOT / "profile" / "github-data.json"))
    ap.add_argument("--fonts", default=str(ROOT / "assets" / "fonts"))
    ap.add_argument("--out", default=str(ROOT / "assets"))
    ap.add_argument("--demo", action="store_true", help="synthetic data, no network")
    ap.add_argument("--fetch", action="store_true", help="fetch live data, save snapshot, generate")
    ap.add_argument("--hero", choices=["A", "B", "C"], help="override hero variant")
    ap.add_argument("--city", choices=["rich", "sparse"], help="override city density")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.hero:
        cfg["variant"]["hero"] = args.hero
    if args.city:
        cfg["variant"]["city"] = args.city
    fonts = Fonts(Path(args.fonts))

    if args.demo:
        data = demo_data(cfg["scene"]["seed"], date.today())
    elif args.fetch:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("GITHUB_TOKEN is required for --fetch", file=sys.stderr)
            return 2
        data = fetch_live(cfg, token)
        Path(args.data).parent.mkdir(parents=True, exist_ok=True)
        Path(args.data).write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))

    # Generate into a temp dir first; only a fully successful run replaces assets.
    # ponytail: two-pass rename, atomic enough for a profile repo.
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        receipt = render_all(cfg, data, fonts, tmp_dir)
        for name, _ in receipt:
            shutil.move(str(tmp_dir / name), str(out / name))

    total = sum(size for _, size in receipt)
    for name, size in receipt:
        print(f"  {name:<28} {size / 1024:6.1f} KB")
    print(f"generated {len(receipt)} artboards, {total / 1024:.1f} KB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
