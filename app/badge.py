"""README badge, rendered as SVG.

This is the distribution mechanism, not a decoration. A maintainer who fixes
their suite wants to show the number, and every badge sits in a README that
other engineers read. Self-hosted rather than proxied through shields.io so the
link points at this service and the click-through lands on the report.

Text width is estimated from character widths because there is no font metrics
library here and adding one for this is not worth the dependency. Slightly
imperfect padding on unusual strings is an acceptable trade.
"""

from __future__ import annotations

# Rough per-character advance widths at 11px DejaVu Sans, scaled by 10.
_NARROW = set("iljI.,:;'|!")
_WIDE = set("MW@mw")


def _text_width(text: str) -> float:
    width = 0.0
    for ch in text:
        if ch in _NARROW:
            width += 3.4
        elif ch in _WIDE:
            width += 9.5
        elif ch.isupper():
            width += 7.6
        elif ch.isdigit():
            width += 7.0
        else:
            width += 6.4
    return width


def color_for(score: int) -> str:
    if score >= 95:
        return "#4c1"       # bright green
    if score >= 85:
        return "#97ca00"    # green
    if score >= 70:
        return "#dfb317"    # yellow
    if score >= 50:
        return "#fe7d37"    # orange
    return "#e05d44"        # red


def render(score: int, label: str = "trust score") -> str:
    value = f"{score}/100"

    label_w = _text_width(label) + 20
    value_w = _text_width(value) + 20
    total = label_w + value_w
    color = color_for(score)

    # Text is placed at 10x scale and scaled down, which is how shields.io gets
    # crisp sub-pixel positioning.
    label_x = label_w * 5
    value_x = (label_w + value_w / 2) * 10

    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{total:.0f}" height="20" role="img" aria-label="{label}: {value}">
  <title>{label}: {value}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total:.0f}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_w:.0f}" height="20" fill="#555"/>
    <rect x="{label_w:.0f}" width="{value_w:.0f}" height="20" fill="{color}"/>
    <rect width="{total:.0f}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
    <text aria-hidden="true" x="{label_x:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(label_w - 20) * 10:.0f}">{label}</text>
    <text x="{label_x:.0f}" y="140" transform="scale(.1)" textLength="{(label_w - 20) * 10:.0f}">{label}</text>
    <text aria-hidden="true" x="{value_x:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(value_w - 20) * 10:.0f}">{value}</text>
    <text x="{value_x:.0f}" y="140" transform="scale(.1)" textLength="{(value_w - 20) * 10:.0f}">{value}</text>
  </g>
</svg>"""


def render_unknown(label: str = "trust score") -> str:
    """Shown when a repo has never been scanned - still a working badge."""
    value = "unscanned"
    label_w = _text_width(label) + 20
    value_w = _text_width(value) + 20
    total = label_w + value_w

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total:.0f}" height="20" role="img" aria-label="{label}: {value}">
  <title>{label}: {value}</title>
  <clipPath id="r"><rect width="{total:.0f}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_w:.0f}" height="20" fill="#555"/>
    <rect x="{label_w:.0f}" width="{value_w:.0f}" height="20" fill="#9f9f9f"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="110">
    <text x="{label_w * 5:.0f}" y="140" transform="scale(.1)">{label}</text>
    <text x="{(label_w + value_w / 2) * 10:.0f}" y="140" transform="scale(.1)">{value}</text>
  </g>
</svg>"""
