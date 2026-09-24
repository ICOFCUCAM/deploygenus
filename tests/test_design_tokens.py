"""The stylesheet keeps the locked Phase 4 colour contract.

The values were chosen for their contrast (docs/design, Phase 4 §3–4). A later
edit that nudges a colour can quietly drop a pair below WCAG AA, so these
tests read the tokens out of deploypro.css itself and compute the ratios.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

CSS = (
    Path(__file__).parent.parent / "deploypro" / "web" / "static" / "deploypro.css"
).read_text()


def tokens(block_start: str) -> dict[str, str]:
    start = CSS.index(block_start)
    body = CSS[start : CSS.index("}", start)]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", body))


LIGHT = tokens(":root {")
DARK = tokens(':root[data-theme="dark"] {')


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def ratio(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_automatic_and_the_explicit_dark_palettes_are_the_same():
    start = CSS.index("@media (prefers-color-scheme: dark)")
    automatic = dict(
        re.findall(
            r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", CSS[start : CSS.index("}\n}", start)]
        )
    )
    assert automatic == DARK


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
class TestContrast:
    def backgrounds(self, palette):
        return [palette[name] for name in ("bg", "surface", "elevated")]

    @pytest.mark.parametrize(
        "text", ["text", "muted", "blue", "success", "caution", "failure", "neutral"]
    )
    def test_text_passes_aa_on_every_background(self, palette, text):
        for background in self.backgrounds(palette):
            assert ratio(palette[text], background) >= 4.5, (text, background)

    def test_interactive_boundaries_pass_non_text_contrast(self, palette):
        """SC 1.4.11: an input's border is what shows it is an input."""
        for background in self.backgrounds(palette):
            assert ratio(palette["control"], background) >= 3, background

    def test_the_focus_ring_is_visible_on_every_background(self, palette):
        for background in self.backgrounds(palette):
            assert ratio(palette["blue"], background) >= 3, background

    @pytest.mark.parametrize(
        ("fill", "label"),
        [
            ("blue", "on-blue"),
            ("blue-hover", "on-blue"),
            ("danger", "on-danger"),
            ("danger-hover", "on-danger"),
        ],
    )
    def test_button_labels_pass_aa(self, palette, fill, label):
        assert ratio(palette[label], palette[fill]) >= 4.5


def test_blue_is_not_a_status_colour():
    """C1: blue identifies DeployPro actions only."""
    for palette in (LIGHT, DARK):
        statuses = {palette[n] for n in ("success", "caution", "failure", "neutral")}
        assert palette["blue"] not in statuses


def test_the_focus_ring_keeps_its_gap():
    """The ring is the primary button's own blue: without the offset it
    disappears on the button (Phase 4 §3)."""
    rule = CSS[CSS.index(":focus-visible {") :]
    rule = rule[: rule.index("}")]
    assert "outline-offset: 2px" in rule


def test_every_animation_has_a_still_form():
    reduced = CSS[CSS.index("@media (prefers-reduced-motion: reduce)") :]
    for animated in re.findall(r"([^{}]+)\{[^{}]*animation:(?!\s*none)", CSS):
        selector = animated.strip().splitlines()[-1].strip().rstrip(",")
        if selector.startswith("@"):
            continue
        assert selector.split(",")[0].strip() in reduced, selector


def test_the_greyscale_shape_check_passes():
    """scripts/check_marks.py needs Playwright and Chromium, which the app
    does not depend on; it runs where they are installed."""
    pytest.importorskip("playwright")
    script = Path(__file__).parent.parent / "scripts" / "check_marks.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
