"""Pre-merge check: every status mark is recognisable without colour.

Phase 4 (docs/design) makes shape, not colour, the thing that tells states
apart: the four colour families are nearly equally light, so in greyscale
only the geometry is left. This renders each mark exactly as the dashboard
draws it — from the template macro, in its own colour family, on both page
backgrounds, at the sizes the stylesheet uses — converts it to greyscale in
the browser, and compares every pair pixel by pixel.

A pair fails when too few pixels differ for the two to be told apart at a
glance. The pairs Phase 4 named (Queued/Ready, Failed/Cancelled,
Failed/caution) are reported first.

Needs Playwright and a Chromium (not part of the app's dependencies):

    pip install playwright
    python scripts/check_marks.py            # exit status 1 on failure
    CHROMIUM=/path/to/chrome python scripts/check_marks.py
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: The seven status marks and the colour family each is drawn in.
MARKS = {
    "queued": "neutral",
    "building": "neutral",
    "deploying": "neutral",
    "ready": "success",
    "failed": "failure",
    "cancelled": "neutral",
    "caution": "caution",
}

#: (background, {family: colour}) — the tokens from deploypro.css.
THEMES = {
    "light": (
        "#f7f5f0",
        {
            "neutral": "#62676b",
            "success": "#1d7a45",
            "caution": "#8f5b00",
            "failure": "#b3261e",
        },
    ),
    "dark": (
        "#111315",
        {
            "neutral": "#a8aaa6",
            "success": "#5fbf8a",
            "caution": "#e2a73d",
            "failure": "#f2877a",
        },
    ),
}

#: Rendered sizes in CSS pixels: a status in a row, and a large status.
SIZES = (13, 16)

#: The fraction of the pair's inked pixels that must differ clearly
#: (by more than a quarter of the grey range) for two marks to count as
#: different shapes.
THRESHOLD = 0.2

NAMED = [("queued", "ready"), ("failed", "cancelled"), ("failed", "caution")]


def svg_for(name: str) -> str:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(ROOT / "deploypro/web/templates")))
    env.globals["split_label"] = lambda text: (text, "")
    macros = env.get_template("_macros.html").module
    return str(macros.shape(name))


PAGE_SCRIPT = """
async ({marks, size, scale, background, colours}) => {
  const px = size * scale;
  const grey = [];
  for (const [name, svg, family] of marks) {
    const markup = svg.replace('<svg ', `<svg xmlns="http://www.w3.org/2000/svg" `
      + `width="${px}" height="${px}" style="color:${colours[family]}" `);
    const img = new Image();
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(markup);
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = px;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, px, px);
    ctx.drawImage(img, 0, 0, px, px);
    const data = ctx.getImageData(0, 0, px, px).data;
    const values = [];
    for (let i = 0; i < data.length; i += 4) {
      values.push(0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]);
    }
    grey.push([name, values]);
  }
  return grey;
}
"""


def compare(a: list[float], b: list[float], background: float) -> float:
    """The share of pixels inked in either mark whose grey levels differ by
    more than 64 of 255."""
    inked = differing = 0
    for x, y in zip(a, b, strict=True):
        if abs(x - background) > 32 or abs(y - background) > 32:
            inked += 1
            if abs(x - y) > 64:
                differing += 1
    return differing / inked if inked else 0.0


def luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


async def main() -> int:
    from playwright.async_api import async_playwright

    marks = [(name, svg_for(name), family) for name, family in MARKS.items()]
    failures = []
    report = []
    async with async_playwright() as pw:
        launch = (
            {"executable_path": os.environ["CHROMIUM"]}
            if "CHROMIUM" in os.environ
            else {}
        )
        browser = await pw.chromium.launch(**launch)
        page = await browser.new_page()
        for theme, (background, colours) in THEMES.items():
            for size, scale in itertools.product(SIZES, (1, 2)):
                grey = dict(
                    await page.evaluate(
                        PAGE_SCRIPT,
                        {
                            "marks": marks,
                            "size": size,
                            "scale": scale,
                            "background": background,
                            "colours": colours,
                        },
                    )
                )
                pairs = NAMED + [
                    p for p in itertools.combinations(MARKS, 2) if p not in NAMED
                ]
                for left, right in pairs:
                    score = compare(grey[left], grey[right], luminance(background))
                    row = (theme, size, scale, left, right, round(score, 3))
                    report.append(row)
                    if score < THRESHOLD:
                        failures.append(row)
        await browser.close()

    worst = sorted(report, key=lambda r: r[-1])[:6]
    print("closest pairs (theme, css px, scale, a, b, share of pixels that differ):")
    for row in worst:
        print("  ", json.dumps(row))
    print("named pairs, weakest case:")
    for left, right in NAMED:
        weakest = min((r for r in report if r[3:5] == (left, right)), key=lambda r: r[-1])
        theme, size, scale = weakest[:3]
        print(f"   {left} vs {right}: {weakest[-1]:.3f} ({theme}, {size}px, x{scale})")
    if failures:
        print(f"FAIL: {len(failures)} pair(s) below {THRESHOLD}")
        for row in failures:
            print("  ", json.dumps(row))
        return 1
    print(
        f"ok: all {len(MARKS)} marks distinguishable in greyscale "
        f"({len(report)} comparisons, threshold {THRESHOLD})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
