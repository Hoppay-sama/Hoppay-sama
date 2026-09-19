#!/usr/bin/env python3
"""Verification gate 1 (DESIGN.md §10): lint every generated artboard.

Checks, per file:
  - parses as valid XML
  - self-contained: no <script>, no external refs, no @import, no raster
  - fonts as paths: no <text>/<tspan>/<foreignObject>
  - reduced-motion block present
  - size budget
Plus a global receipt line: total payload the README must load.

Usage: python scripts/lint.py [--dir assets] [--budget 120]
Exit code 1 on any failure, so it can gate the publish step.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = {
    "<script": "script",
    "xlink:href": "external reference",
    "url(http": "external reference",
    "@import": "external stylesheet",
    "<image": "raster or external image",
    'href="http': "external reference",
}
EXPECTED = {
    "prefers-reduced-motion": "@media (prefers-reduced-motion: reduce)",
}
ABSENT = {
    "<text": "live <text> (fonts must be paths)",
    "<tspan": "live <tspan> (fonts must be paths)",
    "<foreignObject": "foreignObject (proxy support not guaranteed)",
    "class=\"beam\"": "removed beam geometry (DESIGN.md §6)",
}


def lint_file(path: Path, budget_kb: float) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems = []
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        problems.append(f"invalid XML: {exc}")
    for needle, why in FORBIDDEN.items():
        if needle in text:
            problems.append(f"contains {why} ({needle})")
    for needle, why in EXPECTED.items():
        if needle not in text:
            problems.append(f"missing {why}")
    for needle, why in ABSENT.items():
        if needle in text:
            problems.append(f"contains {why}")
    kb = len(text.encode("utf-8")) / 1024
    if kb > budget_kb:
        problems.append(f"{kb:.1f} KB exceeds the {budget_kb:g} KB budget")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint Night Signal artboards")
    ap.add_argument("--dir", default=str(ROOT / "assets"))
    ap.add_argument("--budget", type=float, default=120.0, help="KB per artboard")
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob("*.svg"))
    if not files:
        print(f"no artboards in {args.dir}", file=sys.stderr)
        return 1

    failed = 0
    total = 0
    for p in files:
        problems = lint_file(p, args.budget)
        kb = len(p.read_text(encoding="utf-8").encode("utf-8")) / 1024
        total += kb
        if problems:
            failed += 1
            print(f"FAIL {p.name}")
            for problem in problems:
                print(f"       {problem}")
        else:
            print(f"  ok {p.name:<28}{kb:6.1f} KB")
    print(f"{len(files) - failed}/{len(files)} artboards pass - {total:.1f} KB total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())