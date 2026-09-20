"""Color resolution and perceptual face shading.

Auto-shading happens in OKLab rather than HSL: shifting HSL lightness on a saturated
brand color desaturates it into mud, while OKLab keeps hue and chroma stable across the
three cube faces. Conversions are pinned by fixture so Python, Go and TypeScript agree.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .canonical import num

_PALETTE_REF = re.compile(r"^@palette\.([a-z][a-zA-Z0-9_]{0,31})$")
_LITERAL = re.compile(r"^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


class RGBA(NamedTuple):
    r: float  # 0..1 sRGB
    g: float
    b: float
    a: float = 1.0


def parse_hex(value: str) -> RGBA:
    m = _LITERAL.match(value)
    if not m:
        raise ValueError(f"not a literal IsoDSL color: {value!r}")
    rgb, alpha = m.group(1), m.group(2)
    return RGBA(
        int(rgb[0:2], 16) / 255.0,
        int(rgb[2:4], 16) / 255.0,
        int(rgb[4:6], 16) / 255.0,
        int(alpha, 16) / 255.0 if alpha else 1.0,
    )


def to_hex(c: RGBA, *, force_alpha: bool = False) -> str:
    """Emit uppercase #RRGGBB, or #RRGGBBAA when alpha is not opaque."""

    def ch(v: float) -> int:
        return max(0, min(255, int(round(v * 255.0))))

    out = f"#{ch(c.r):02X}{ch(c.g):02X}{ch(c.b):02X}"
    if force_alpha or c.a < 1.0:
        out += f"{ch(c.a):02X}"
    return out


def resolve_color(value: str | None, palette: dict[str, str], *, default: str = "#888888") -> str:
    """Resolve '@palette.x' to its literal, pass literals through, fall back on default."""
    if not value:
        return default
    if m := _PALETTE_REF.match(value):
        return palette.get(m.group(1), default)
    return value


# --- sRGB <-> OKLab -------------------------------------------------------------
# Bjorn Ottosson's OKLab. Constants are reproduced exactly; do not "simplify" them.


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


class OKLab(NamedTuple):
    L: float
    a: float
    b: float


def rgb_to_oklab(c: RGBA) -> OKLab:
    r, g, b = _srgb_to_linear(c.r), _srgb_to_linear(c.g), _srgb_to_linear(c.b)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = _cbrt(l), _cbrt(m), _cbrt(s)
    return OKLab(
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_to_rgb(lab: OKLab, alpha: float = 1.0) -> RGBA:
    l_ = lab.L + 0.3963377774 * lab.a + 0.2158037573 * lab.b
    m_ = lab.L - 0.1055613458 * lab.a - 0.0638541728 * lab.b
    s_ = lab.L - 0.0894841775 * lab.a - 1.2914855480 * lab.b
    l, m, s = l_ * l_ * l_, m_ * m_ * m_, s_ * s_ * s_
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
    return RGBA(
        clamp(_linear_to_srgb(r)), clamp(_linear_to_srgb(g)), clamp(_linear_to_srgb(b)), alpha
    )


def _cbrt(x: float) -> float:
    # Preserve sign; ** on a negative base raises in Python.
    return x ** (1.0 / 3.0) if x >= 0 else -((-x) ** (1.0 / 3.0))


def shade(color: str, offset: float) -> str:
    """Shift a color's perceptual lightness by `offset` (-1..1), preserving hue.

    offset 0 returns the input unchanged so that shading.enabled=false and an offset of
    zero are byte-identical.
    """
    if offset == 0:
        return to_hex(parse_hex(color))
    base = parse_hex(color)
    lab = rgb_to_oklab(base)
    shifted = OKLab(max(0.0, min(1.0, lab.L + offset)), lab.a, lab.b)
    return to_hex(oklab_to_rgb(shifted, base.a))


def with_opacity(color: str, opacity: float) -> tuple[str, float]:
    """Split a possibly-8-digit color into (#RRGGBB, combined_alpha) for SVG output."""
    c = parse_hex(color)
    combined = num(c.a * opacity)
    return to_hex(RGBA(c.r, c.g, c.b, 1.0)), combined
