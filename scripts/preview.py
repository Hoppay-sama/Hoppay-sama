#!/usr/bin/env python3
"""Local verification harness: renders every artboard in a browser page so the
real SVG output (animations, fonts-as-paths) can be inspected at true size.

Usage: python scripts/preview.py  ->  build/preview.html
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    figures = []
    for p in sorted((ROOT / "assets").glob("*.svg")):
        if p.name.startswith("divider"):
            continue
        kind = "mobile" if p.stem.endswith("-m") else "desktop"
        figures.append(
            f'<figure class="{kind}"><img src="../assets/{p.name}" alt="{p.stem}" '
            f'width="{390 if kind == "mobile" else 820}">'
            f"<figcaption>{p.stem} · {p.stat().st_size // 1024} KB</figcaption></figure>"
        )
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{background:#0d1117;color:#8b949e;font:12px ui-monospace,Consolas,monospace;padding:32px;margin:0}}
h2{{color:#e6edf3;font-size:13px;letter-spacing:.1em;font-weight:500}}
.grid{{display:flex;flex-wrap:wrap;gap:28px;align-items:flex-start}}
figure{{margin:0}} img{{display:block;border:1px solid #21262d}}
figcaption{{padding-top:6px}}
.light{{background:#f6f8fa;padding:28px;border-radius:6px}}
.light figcaption{{color:#57606a}}
.light img{{border-color:#d0d7de}}
</style></head><body>
<h2>NIGHT SIGNAL — ARTBOARDS (as seen on dark GitHub)</h2>
<div class="grid">{''.join(figures)}</div>
<h2 style="margin-top:44px">LIGHT THEME CONTEXT</h2>
<div class="light"><div class="grid">{''.join(figures)}</div></div>
</body></html>"""
    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    (out / "preview.html").write_text(html, encoding="utf-8")
    print(out / "preview.html")


if __name__ == "__main__":
    main()
