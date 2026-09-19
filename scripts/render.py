#!/usr/bin/env python3
"""Render harness (DESIGN.md §10.3): screenshots every artboard at the size it
actually appears in the README, so legibility and motion can be judged from
evidence instead of from the SVG source.

Usage:
  python scripts/render.py              -> build/comps/*.png (2x device pixels)
  python scripts/render.py --sheet      -> build/comps/_sheet.png contact sheet
  python scripts/render.py --scale 1    -> 1x, i.e. exact in-README pixel size

Requires a Chromium-family browser. Edge is used when Chrome is absent.
--sheet additionally needs Pillow (dev-only; not in requirements.txt).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSERS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;background:{bg}}}
img{{display:block;width:{w}px}}
</style></head><body><img src="../../assets/{name}.svg?v={stamp}"></body></html>"""


def browser() -> Path:
    for p in BROWSERS:
        if p.exists():
            return p
    raise SystemExit("no Chromium-family browser found")


def shoot(exe: Path, html: Path, png: Path, w: int, h: int, scale: int,
          motion: str = "reduced", profile: Path | None = None) -> None:
    args = [
        str(exe), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--disable-extensions",
        f"--force-device-scale-factor={scale}", f"--window-size={w},{h}",
        f"--screenshot={png}",
    ]
    if profile is not None:
        # A shared profile serves stale file:// copies of the artboards, which
        # silently showed a previous round's data. Isolate every run.
        args.append(f"--user-data-dir={profile}")
    if motion == "reduced":
        # Final states: entrance animations are mid-flight when a headless
        # screenshot fires (~0.9s), which makes comps unreliable. This also
        # exercises the reduced-motion path REQUIRED by DESIGN.md §6.
        args.append("--force-prefers-reduced-motion")
    else:
        args.append(f"--virtual-time-budget={motion}")
    subprocess.run(args + [html.as_uri()], check=True, capture_output=True, timeout=120)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render Night Signal artboards")
    ap.add_argument("--scale", type=int, default=2, help="device pixel ratio")
    ap.add_argument("--bg", default="#0d1117", help="page background behind the art")
    ap.add_argument("--only", default=None, help="render a single artboard stem")
    ap.add_argument("--sheet", action="store_true", help="also build a contact sheet")
    ap.add_argument("--motion", default="reduced",
                    help="'reduced' (default, final states) or a virtual-time budget in ms")
    args = ap.parse_args()

    exe = browser()
    out = ROOT / "build" / "comps"
    out.mkdir(parents=True, exist_ok=True)
    profile = ROOT / "build" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    stems = sorted(p.stem for p in (ROOT / "assets").glob("*.svg"))
    if args.only:
        stems = [s for s in stems if s == args.only]

    made = []
    for stem in stems:
        svg = (ROOT / "assets" / f"{stem}.svg")
        head = svg.read_text(encoding="utf-8")[:200]
        w = int(head.split('width="')[1].split('"')[0])
        h = int(head.split('height="')[1].split('"')[0])
        html = out / f"_{stem}.html"
        stamp = int(svg.stat().st_mtime)
        html.write_text(PAGE.format(bg=args.bg, w=w, name=stem, stamp=stamp), encoding="utf-8")
        png = out / f"{stem}.png"
        shoot(exe, html, png, w, h, args.scale, args.motion, profile)
        made.append((stem, w, h, png))
        print(f"  {stem:<24} {w}x{h} -> {png.relative_to(ROOT)}")

    if args.sheet and len(made) > 1:
        from PIL import Image

        pad, label_h = 16, 16
        max_w = max(w for _, w, _, _ in made)
        total_h = sum(int(h * args.scale) + label_h + pad for _, _, h, _ in made) + pad
        sheet = Image.new("RGB", (int(max_w * args.scale) + 2 * pad, total_h), (13, 17, 23))
        y = pad
        for stem, w, h, png in made:
            im = Image.open(png)
            sheet.paste(im, (pad, y))
            y += im.height + label_h + pad
        sheet.save(out / "_sheet.png")
        print(f"  sheet -> {(out / '_sheet.png').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
