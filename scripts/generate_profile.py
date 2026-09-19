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


# Sky ramp stops. Scenes must use these constants so the legibility gate below
# audits the exact grounds the art is painted on (DESIGN.md §10.1).
SKY_MID = "#060D20"     # city mid sky
SKY_LOW = "#071026"     # title mid sky
SKY_FLOOR = "#0A1430"   # city horizon glow
SKY_TITLE = "#0B1632"   # title floor
SKY_PANEL = "#070C1C"   # records backdrop
GROUND = "#060C1A"      # ground below the skyline (HUD sits here)

# Skyline modules: one floor pitch and one window column pitch shared by every
# tower, so a year of weeks reads as a single city grid instead of noise.
FLOOR = 7.0
COLP = 4.4
WIN_W = 2.6


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = (a, b) if a > b else (b, a)
    return (hi + 0.05) / (lo + 0.05)


# Legibility gate (round 1 finding): every text token is measured against every
# ground it can land on. Primary tiers carry the information, so they must clear
# 7:1; the faintest tier is decoration and must still clear AA at 4.5:1.
TEXT_GATE = {"ink": 7.0, "inkDim": 7.0, "inkFaint": 4.5, "cyan": 4.5, "cyanDim": 4.5}


def audit_contrast(cfg: dict) -> list[str]:
    pal = {**cfg["palette"], "skyMid": SKY_MID, "skyLow": SKY_LOW, "skyFloor": SKY_FLOOR,
           "titleFloor": SKY_TITLE, "skyPanel": SKY_PANEL, "ground": GROUND}
    grounds = [k for k in ("void", "night", "panel", "skyMid", "skyLow", "skyFloor",
                           "titleFloor", "skyPanel", "ground") if k in pal]
    notes, bad = [], []
    for token, bar in TEXT_GATE.items():
        ground, worst = "", 99.0
        for g in grounds:
            r = contrast(pal[token], pal[g])
            if r < worst:
                ground, worst = g, r
        notes.append(f"  {token:<9} {worst:5.2f}:1 on {ground:<10} (bar {bar:g}:1)")
        if worst < bar:
            bad.append(f"{token} {worst:.2f}:1 on {ground} < {bar}:1")
    if bad:
        raise SystemExit("legibility gate failed: " + "; ".join(bad))
    return notes


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
    bg_gradient(svg, "bg", [(0, pal["void"], 1), (0.62, SKY_LOW, 1), (1, SKY_TITLE, 1)])
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
            text(svg, fonts, "mono", group["label"], 8, m, ys, pal["inkDim"], 2)
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
            text(svg, fonts, "mono", group["label"], 8, x, y0, pal["inkDim"], 2)
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
    baseline = H * 0.72 if mobile else 415.0
    # Mobile carries more sky, so the band is taller: a quiet year otherwise
    # left the poster two-thirds empty.
    max_h = H * 0.46 if mobile else 265.0

    # Light model: a light dome over the city, darkest sky at the zenith, and a
    # dark ground under the HUD. Without the dome the masses had nothing to
    # silhouette against (round 1: "the skyline doesn't survive render scale").
    gy = baseline / H
    bg_gradient(svg, "bg", [(0, pal["void"], 1), (gy * 0.5, SKY_MID, 1),
                            (gy, SKY_FLOOR, 1), (1, GROUND, 1)])
    svg.add(rect(0, 0, W, H, "url(#bg)"))
    starfield(svg, rng, pal, 40 if mobile else 80, H * 0.5, W)

    if cfg["features"]["moon"] and not mobile:
        svg.add_def(
            '<radialGradient id="moonglow">'
            f'<stop offset="0" stop-color="{pal["cyan"]}" stop-opacity="0.16"/>'
            f'<stop offset="0.4" stop-color="{pal["cyan"]}" stop-opacity="0.05"/>'
            f'<stop offset="1" stop-color="{pal["cyan"]}" stop-opacity="0"/></radialGradient>'
        )
        svg.add(circle(688, 126, 48, "url(#moonglow)"))
        svg.add(circle(688, 126, 15, pal["star"], 0.24))
        # Carved disc -> crescent. A flat grey circle read as a blob at scale.
        svg.add(circle(683, 123, 15.4, pal["void"], 0.94))

    text(svg, fonts, "mono", f"ACT II — THE CITY · {scene['timecodes']['city']}",
         8 if mobile else 9, 20, 30, pal["cyan"], 1.8)

    streak = current_streak(data["days"])
    weeks = weeks_from_days(data["days"])
    n = len(weeks)
    if mobile:
        # pair weeks so towers stay ~10px wide
        merged = []
        for i in range(0, n - 1, 2):
            merged.append({"days": weeks[i]["days"] + weeks[i + 1]["days"],
                           "total": weeks[i]["total"] + weeks[i + 1]["total"]})
        if n % 2:
            merged.append(weeks[-1])
        weeks = merged
        n = len(weeks)

    margin = 18 if mobile else 20
    pitch = (W - 2 * margin) / n
    bw = pitch * (0.76 if mobile else 0.68)
    x0 = (W - n * pitch) / 2 + (pitch - bw) / 2

    top = max(w["total"] for w in weeks) or 1
    rich = cfg["variant"]["city"] == "rich"

    # Heights are resolved before anything is drawn: the back row is sized from
    # the median of the real skyline so scenery can never out-mass the data.
    heights: list[float] = []
    for wk in weeks:
        if wk["total"] == 0:
            heights.append(0.0)
            continue
        # .78 exponent keeps a real height spread; sqrt flattened every week
        # into the same ~200px tower.
        ratio = (wk["total"] / top) ** 0.78
        heights.append(min(max_h, 16 + (max_h - 16) * ratio * rng.uniform(0.72, 1.02)))
    lit_h = sorted(h for h in heights if h > 0)
    median_h = lit_h[len(lit_h) // 2] if lit_h else max_h * 0.4

    # Tower bodies are one bulk path on a shared vertical gradient: lit crown
    # falling into haze at the base. The base stop must stay far enough from the
    # sky to read as a mass (>= ~2:1); #151F38 measured 1.12:1 and vanished.
    svg.add_def(
        '<linearGradient id="tower" gradientUnits="userSpaceOnUse" '
        f'x1="0" y1="{baseline - max_h:.0f}" x2="0" y2="{baseline:.0f}">'
        '<stop offset="0" stop-color="#3E5484"/>'
        '<stop offset="0.5" stop-color="#2C3A58"/>'
        '<stop offset="1" stop-color="#212C46"/>'
        '</linearGradient>'
    )
    # A darker, offset back row: pure depth cue, no data. Capped against the
    # median of the real skyline — when it was sized off max_h it towered over
    # the (generally short) real weeks and the scenery became the hero.
    brng = random.Random(scene["seed"] + 91)
    back: list[str] = []
    cursor_x = x0 - pitch
    while cursor_x < W - margin:
        bh = min(median_h * brng.uniform(0.5, 1.05), max_h * 0.8)
        back.append(rpath(cursor_x, baseline - bh, bw * 1.2, bh))
        cursor_x += pitch * brng.uniform(0.85, 1.15)
    back_path = f'<path d="{"".join(back)}" fill="{pal["lineFaint"]}" opacity="0.55"/>'

    roof: list[str] = []       # moonlit rooflines: the silhouette reads by them
    roof_lo: list[str] = []    # short towers keep a quieter edge
    crown: list[str] = []      # top-floor light on the tallest weeks
    body: list[str] = []
    win_bands: dict[float, list[str]] = {0.85: [], 0.6: []}
    flickers: list[str] = []
    lit_weeks = [i for i, w in enumerate(weeks) if w["total"] > 0]
    newest_lit = lit_weeks[-1] if lit_weeks else -1
    beacon: tuple[float, float] | None = None

    for i, wk in enumerate(weeks):
        x = x0 + i * pitch
        h = heights[i]
        if wk["total"] == 0:
            body.append(rpath(x, baseline - 4, bw, 4))
            continue
        body.append(rpath(x, baseline - h, bw, h))
        tall = h > max_h * 0.55
        (roof if tall else roof_lo).append(rpath(x, baseline - h, bw, 1.4))
        if h > max_h * 0.72:
            roof.append(rpath(x, baseline - h - 0.6, bw, 0.6))
        if i == newest_lit:
            beacon = (x + bw / 2, baseline - h)

        # Windows stack up a shared floor grid, so every tower reads as a facade
        # rather than as scattered dashes (round 1 finding).
        floors = max(1, int((h - 12) // FLOOR))
        cols = max(1, int(bw // COLP))
        # Every window column starts on its own floor. A single shared start
        # lined the lit floors up across the whole city into one dashed stripe.
        filled = [rng.randrange(0, max(1, floors // 2 + 1)) for _ in range(cols)]
        for k, day in enumerate(d for d in wk["days"] if d["count"] > 0):
            if not rich and rng.random() < 0.34:
                continue
            c = k % cols
            for _ in range(1 + min(day["count"], 2 if mobile else 3)):
                if filled[c] >= floors:
                    break
                wy = baseline - 8 - filled[c] * FLOOR
                filled[c] += 1
                wx = x + 2.4 + c * COLP
                if rng.random() < 0.06:
                    flickers.append(
                        rect(wx, wy, WIN_W, 3.4, pal["amber"], 0.7, cls="f",
                             style=f"animation-delay:{rng.uniform(0, 6):.1f}s")
                    )
                else:
                    win_bands[0.85 if c == 0 else 0.6].append(rpath(wx, wy, WIN_W, 3.4))
        if tall and floors > 2:
            crown.append(rpath(x + bw / 2 - 1.4, baseline - h + 1.8, 2.8, 3.2))

    towers = back_path
    if body:
        towers += f'<path d="{"".join(body)}" fill="url(#tower)" opacity="0.92"/>'
    if roof_lo:
        towers += f'<path d="{"".join(roof_lo)}" fill="{pal["cyanDim"]}" opacity="0.42"/>'
    if roof:
        towers += f'<path d="{"".join(roof)}" fill="{pal["cyanDim"]}" opacity="0.8"/>'
    for op, subs in win_bands.items():
        if subs:
            towers += f'<path d="{"".join(subs)}" fill="{pal["amber"]}" opacity="{op}"/>'
    if crown:
        towers += f'<path d="{"".join(crown)}" fill="{pal["amber"]}" opacity="0.95"/>'
    towers += "".join(flickers)
    if beacon:
        bx, by = beacon
        # A beacon, not a searchlight: a soft spill plus the pulsing plug. The
        # old hard-edged wedge was flat clip-art and pumped luminance, which
        # DESIGN.md §6 forbids (round 1 P0 finding).
        svg.add_def(
            '<radialGradient id="beaconglow">'
            f'<stop offset="0" stop-color="{pal["amber"]}" stop-opacity="0.4"/>'
            f'<stop offset="0.35" stop-color="{pal["amber"]}" stop-opacity="0.11"/>'
            f'<stop offset="1" stop-color="{pal["amber"]}" stop-opacity="0"/></radialGradient>'
        )
        towers += circle(bx, by - 3, 16, "url(#beaconglow)")
        towers += circle(bx, by - 3, 2.6, pal["amber"], 0.95, cls="beacon")
    svg.add(f'<g class="b" style="animation-delay:.5s">{towers}</g>')

    svg.add(line(margin, baseline + 0.5, W - margin, baseline + 0.5, pal["line"], 0.8, 0.9))
    months: dict[int, int] = {}
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
        svg.add(line(mx, baseline + 1, mx, baseline + 5.5, pal["line"], 0.7, 1))
        name = date(2000, month, 1).strftime("%b").upper()
        # Axis labels carry the data, so they sit in the 7:1 tier (round 1).
        text(svg, fonts, "mono", name, 7.5 if mobile else 8, mx, baseline + 16,
             pal["inkDim"], 1)

    caption = ["ONE YEAR OF COMMITS, BUILT AS A SKYLINE",
               f"EVERY LIT WINDOW IS A DAY YOU SHIPPED · STREAK {streak}D"]
    if mobile:
        caption = [f"SKYLINE · STREAK {streak}D"]
    for i, cap in enumerate(caption):
        text(svg, fonts, "mono", cap, 7.5 if mobile else 8, W - margin,
             30 + i * (14 if mobile else 16), pal["inkDim"], 1.6, "end")

    sy = (H - 128) if mobile else (baseline + 55)
    svg.add(line(margin, sy - 15, W - margin, sy - 15, pal["line"], 0.7, 0.7))
    stats = [
        ("COMMITS · 12 MO", f"{data['totals']['commits12mo']:,}"),
        ("STARS", f"{data['totals']['stars']:,}"),
        ("PULL REQUESTS", f"{data['totals']['pullRequests']:,}"),
    ]
    if mobile:
        widths = (W - 2 * margin) / 3
        for i, (label, val) in enumerate(stats):
            x = margin + i * widths
            text(svg, fonts, "mono", label, 7, x, sy, pal["inkDim"], 1.2)
            text(svg, fonts, "display", val, 22, x, sy + 34, pal["ink"], 2.4)
        by = H - 74
    else:
        x = margin + 20
        for label, val in stats:
            text(svg, fonts, "mono", label, 8, x, sy, pal["inkDim"], 1.6)
            text(svg, fonts, "display", val, 30, x, sy + 35, pal["ink"], 3)
            x += 150
        by = sy + 23

    total_pct = sum(l["pct"] for l in langs) or 100
    bx, bw_ = (margin, W - 2 * margin) if mobile else (470, W - 490)
    lane = 5 if mobile else 4
    svg.add(rect(bx, by, bw_, lane, pal["lineFaint"], 1, rx=lane / 2))
    cursor = 0.0
    seg_bounds = []
    for l in langs:
        seg = bw_ * l["pct"] / total_pct
        svg.add(rect(bx + cursor, by, max(seg - 1, 1), lane,
                     cfg["languages"]["colors"].get(l["name"], pal["inkFaint"]), 0.95, rx=lane / 2))
        seg_bounds.append((cursor, cursor + seg, l))
        cursor += seg
    for x0_, x1_, l in seg_bounds:
        if x1_ - x0_ < 54:  # too narrow to label without turning to mush
            continue
        text(svg, fonts, "mono", f"{l['name']} {l['pct']}%", 7.5 if mobile else 8,
             bx + (x0_ + x1_) / 2, by + 14, pal["inkDim"], 0.8, "middle")

    hud_frame(svg, pal)
    vignette_grain(svg, pal, scene["grain"], scene["vignette"])
    return svg


def scene_records(cfg, data, fonts: Fonts, mobile: bool):
    pal = cfg["palette"]
    scene = cfg["scene"]
    W, H = (390, 560) if mobile else (820, 400)
    svg = Svg(W, H)
    rng = random.Random(scene["seed"] + 5)
    bg_gradient(svg, "bg", [(0, pal["night"], 1), (1, SKY_PANEL, 1)])
    svg.add(rect(0, 0, W, H, "url(#bg)"))
    starfield(svg, rng, pal, 24, H * 0.4, W, animate=False)

    text(svg, fonts, "mono", f"ACT III — THE RECORDS · {scene['timecodes']['records']}",
         8 if mobile else 9, 20, 30, pal["cyan"], 1.8)
    if not mobile:
        text(svg, fonts, "mono", "LIVE STARS · UPDATED DAILY", 9,
             W - 20, 30, pal["inkDim"], 1.6, "end")

    featured = cfg["featured"]
    margin = 20
    if mobile:
        # 140 tall so a two-line logline fits: truncating it to one line hid
        # information (round 1 finding).
        gap, ph = 12, 140
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
        name = item["repo"].upper()
        svg.add(f'<g class="b" style="animation-delay:{0.25:.2f}s">')
        svg.add(rect(bx, by, pw, ph, pal["panel"], 0.32))
        svg.add(f'<rect x="{bx}" y="{by}" width="{pw}" height="{ph}" fill="none" stroke="{pal["line"]}" stroke-width="0.8"/>')
        svg.add(line(bx, by, bx + 10, by, pal["cyan"], 1, 0.75))
        svg.add(line(bx, by, bx, by + 10, pal["cyan"], 1, 0.75))
        tx = bx + 16

        # Plate number as a quiet watermark in the empty top-right, sized so it
        # can never collide with the name (round 1: it read as a smudge when it
        # sat on the star row at the bottom).
        ghost = f"0{idx}"
        ghost_size = 52 if mobile else 60
        ghost_w = fonts.measure("display", ghost, ghost_size, 0)
        name_size = 24.0 if mobile else 26.0
        while name_size > 17 and fonts.measure("display", name, name_size, 4) > pw - 46 - ghost_w:
            name_size -= 1
        text(svg, fonts, "display", ghost, ghost_size, bx + pw - 14, by + 56,
             pal["cyan"], 0, "end", 0.07)

        text(svg, fonts, "mono", f"0{idx} / FEATURE", 8, tx, by + 22,
             pal["inkDim"], 1.8)
        text(svg, fonts, "display", name, name_size, tx, by + 52, pal["ink"], 4)
        text(svg, fonts, "mono", item["category"], 8, tx, by + 68, pal["cyan"], 2)
        log = fonts.wrap("body", item["logline"], 11 if mobile else 12, 0, pw - 32)
        for i, ln in enumerate(log[:2 if mobile else 3]):
            text(svg, fonts, "body", ln, 11 if mobile else 12, tx,
                 by + (86 + i * 15 if mobile else 92 + i * 17), pal["inkDim"], 0)

        fy = by + ph - 16
        svg.add(line(tx, fy - 13, bx + pw - 16, fy - 13, pal["line"], 0.6, 0.7))
        text(svg, fonts, "mono", lang.upper(), 8, tx, fy, pal["inkDim"], 1.4)
        sw_ = fonts.measure("mono", str(stars), 9, 1.2)
        svg.add(f'<path transform="translate({bx + pw - 16 - sw_ - 14:.1f} {fy - 3:.1f})" '
                f'd="{star_path(0, 0, 4.4)}" fill="{pal["amber"]}" opacity="0.9"/>')
        text(svg, fonts, "mono", str(stars), 9, bx + pw - 16, fy, pal["amber"], 1.2, "end")
        svg.add("</g>")

    html = "NEXT — ACT IV · TRANSMISSION"
    text(svg, fonts, "mono", html, 8, 20, H - 18, pal["inkDim"], 1.6)
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
            # newline="\n": without it Python writes os.linesep, so Windows and
            # the Linux Action would produce different bytes for identical art.
            path.write_text(markup, encoding="utf-8", newline="\n")
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
    for line in audit_contrast(cfg):
        print(line)
    if args.hero:
        cfg["variant"]["hero"] = args.hero
    if args.city:
        cfg["variant"]["city"] = args.city
    fonts = Fonts(Path(args.fonts))

    snapshot: dict | None = None
    if args.demo:
        data = demo_data(cfg["scene"]["seed"], date.today())
    elif args.fetch:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("GITHUB_TOKEN is required for --fetch", file=sys.stderr)
            return 2
        data = fetch_live(cfg, token)
        snapshot = data
    else:
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))

    # Generate into a temp dir first; only a fully successful run replaces assets
    # and only then is the snapshot written, so a failed build can never publish
    # data that no artboard reflects.
    # ponytail: two-pass rename, atomic enough for a profile repo.
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        receipt = render_all(cfg, data, fonts, tmp_dir)
        for name, _ in receipt:
            shutil.move(str(tmp_dir / name), str(out / name))

    if snapshot is not None:
        Path(args.data).parent.mkdir(parents=True, exist_ok=True)
        Path(args.data).write_text(json.dumps(snapshot, indent=2), encoding="utf-8",
                                   newline="\n")

    total = sum(size for _, size in receipt)
    for name, size in receipt:
        print(f"  {name:<28} {size / 1024:6.1f} KB")
    print(f"generated {len(receipt)} artboards, {total / 1024:.1f} KB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
