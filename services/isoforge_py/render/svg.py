"""Deterministic SVG writer.

We emit SVG text by hand rather than via svgwrite because the product's core promise is
byte-identical output: attribute order, float formatting, id generation and element
ordering all have to be ours. Same scene in, same bytes out, on every platform and
forever.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from ..isodsl.canonical import fmt, num
from ..isodsl.color import parse_hex, resolve_color, to_hex
from ..isodsl.geometry import Face, PlacedFace, bounds, scene_faces

SVG_NS = "http://www.w3.org/2000/svg"


def _points_attr(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{fmt(x)},{fmt(y)}" for x, y in points)


def _opacity_attr(value: float) -> str:
    return "" if value >= 1.0 else f' fill-opacity="{fmt(value)}"'


def _viewport(scene: dict, faces: list[PlacedFace]) -> tuple[float, float, float, float, float]:
    """Compute the fitted transform: (scale, tx, ty, width, height).

    Auto-fit keeps framing stable as shapes come and go, which matters because the user
    is iterating conversationally and should not see the logo jump around between turns.
    """
    canvas = scene.get("canvas") or {}
    width = float(canvas.get("width", 512))
    height = float(canvas.get("height", 512))
    padding = float(canvas.get("padding", 0.08))
    camera = scene.get("camera") or {}

    if not faces or not camera.get("fit", True):
        return (1.0, num(width / 2.0), num(height / 2.0), width, height)

    min_x, min_y, max_x, max_y = bounds(faces)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    avail_w = width * (1.0 - 2.0 * padding)
    avail_h = height * (1.0 - 2.0 * padding)
    scale = min(avail_w / span_x, avail_h / span_y)

    # Center the projected bounding box in the canvas.
    tx = width / 2.0 - (min_x + max_x) / 2.0 * scale
    ty = height / 2.0 - (min_y + max_y) / 2.0 * scale
    return (num(scale), num(tx), num(ty), width, height)


def _defs(scene: dict, palette: dict) -> tuple[list[str], dict[str, str]]:
    """Build filter defs for shadow/glow. Ids are fixed strings, never random."""
    effects = scene.get("effects") or {}
    parts: list[str] = []
    refs: dict[str, str] = {}

    shadow = effects.get("shadow") or {}
    glow = effects.get("glow") or {}
    shadow_on = bool(shadow.get("enabled"))
    glow_on = bool(glow.get("enabled"))

    def _shadow_primitive() -> str:
        color = resolve_color(shadow.get("color", "#000000"), palette)
        offset = shadow.get("offset") or {}
        return (
            f'<feDropShadow dx="{fmt(offset.get("x", 0))}" dy="{fmt(offset.get("y", 4))}"'
            f' stdDeviation="{fmt(float(shadow.get("blur", 4)) / 2.0)}"'
            f' flood-color="{color}" flood-opacity="{fmt(shadow.get("opacity", 0.25))}"/>'
        )

    def _glow_primitive() -> str:
        color = resolve_color(glow.get("color", "#FFFFFF"), palette)
        return (
            f'<feDropShadow dx="0" dy="0"'
            f' stdDeviation="{fmt(float(glow.get("radius", 8)) / 2.0)}"'
            f' flood-color="{color}" flood-opacity="{fmt(glow.get("intensity", 0.6))}"/>'
        )

    # Glow and shadow are chained into one filter because SVG allows only a single
    # filter attribute per element; defining two separately would silently drop one.
    if shadow_on or glow_on:
        primitives = ""
        if glow_on:
            primitives += _glow_primitive()
        if shadow_on:
            primitives += _shadow_primitive()
        parts.append(
            '<filter id="iso-effects" x="-75%" y="-75%" width="250%" height="250%">'
            f"{primitives}</filter>"
        )
        refs["effects"] = "iso-effects"

    return parts, refs


def render_svg(scene: dict, *, pretty: bool = True) -> str:
    """Render a validated IsoDSL scene to SVG text.

    The scene must already be validated; this function assumes well-formed input and
    does not re-check it.
    """
    palette = (scene.get("palette") or {}).get("colors") or {}
    effects = scene.get("effects") or {}
    faces = scene_faces(scene)
    scale, tx, ty, width, height = _viewport(scene, faces)

    nl = "\n" if pretty else ""
    ind = "  " if pretty else ""

    out: list[str] = []
    out.append(
        f'<svg xmlns="{SVG_NS}" width="{fmt(width)}" height="{fmt(height)}"'
        f' viewBox="0 0 {fmt(width)} {fmt(height)}">'
    )

    meta = scene.get("meta") or {}
    if name := meta.get("name"):
        out.append(f"{ind}<title>{escape(str(name))}</title>")
    if desc := meta.get("description"):
        out.append(f"{ind}<desc>{escape(str(desc))}</desc>")

    def_parts, filter_refs = _defs(scene, palette)
    if def_parts:
        out.append(f"{ind}<defs>{''.join(def_parts)}</defs>")

    background = (scene.get("canvas") or {}).get("background")
    if background:
        bg = resolve_color(background, palette)
        bg_hex, bg_alpha = to_hex(parse_hex(bg)), parse_hex(bg).a
        out.append(
            f'{ind}<rect x="0" y="0" width="{fmt(width)}" height="{fmt(height)}"'
            f' fill="{bg_hex[:7]}"{_opacity_attr(bg_alpha)}/>'
        )

    group_attrs = f'transform="translate({fmt(tx)},{fmt(ty)}) scale({fmt(scale)})"'
    if fid := filter_refs.get("effects"):
        group_attrs += f' filter="url(#{fid})"'
    out.append(f"{ind}<g {group_attrs}>")

    outline = effects.get("outline") or {}
    outline_on = bool(outline.get("enabled"))
    outline_color = resolve_color(outline.get("color", "#000000"), palette) if outline_on else None
    # Stroke width is specified in canvas pixels, so divide out the fit scale to keep
    # line weight visually constant regardless of how far the scene was zoomed.
    outline_width = num(float(outline.get("width", 1)) / scale) if outline_on else 0.0
    outline_face_scope = outline.get("scope", "face") == "face"

    for face in faces:
        attrs = [f'points="{_points_attr(face.points)}"', f'fill="{face.fill}"']
        if face.opacity < 1.0:
            attrs.append(f'fill-opacity="{fmt(face.opacity)}"')

        stroke = face.stroke
        stroke_width = face.stroke_width
        if stroke is None and outline_on and outline_face_scope:
            stroke = outline_color
            stroke_width = outline_width
        if stroke and stroke_width > 0:
            attrs.append(f'stroke="{stroke}"')
            attrs.append(f'stroke-width="{fmt(stroke_width)}"')
            attrs.append('stroke-linejoin="round"')

        out.append(f"{ind}{ind}<polygon {' '.join(attrs)}/>")

    out.append(f"{ind}</g>")
    out.append("</svg>")
    return nl.join(out) + nl


def render_svg_bytes(scene: dict, *, pretty: bool = True) -> bytes:
    return render_svg(scene, pretty=pretty).encode("utf-8")
