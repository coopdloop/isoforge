"""Isometric projection, shape tessellation and deterministic paint ordering.

This module is the reference implementation. The TypeScript preview renderer in
web/src/iso/ is a direct transliteration of it, and both are pinned to the shared
coordinate fixtures so the browser preview and the exported file can never disagree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from .canonical import num
from .color import resolve_color, shade, with_opacity

#: Exact trig for the isometric transform (RULES.md). sqrt(3)/2 and 1/2.
COS30 = math.sqrt(3.0) / 2.0
SIN30 = 0.5


class Face(StrEnum):
    TOP = "top"
    LEFT = "left"
    RIGHT = "right"


Point = tuple[float, float]


def project(
    x: float, y: float, z: float, cell: float = 1.0, projection: str = "isometric"
) -> Point:
    """Map a grid coordinate to screen space.

    +x goes right-and-down, +y goes left-and-down, +z goes up. Screen depth increases
    monotonically with (x + y + z), which is what makes the paint-order rule sound.
    """
    if projection == "dimetric":
        return (num((x - y) * cell), num(((x + y) * 0.5 - z) * cell))
    return (num((x - y) * COS30 * cell), num(((x + y) * SIN30 - z) * cell))


@dataclass(slots=True)
class FlatShape:
    """A shape with group offsets resolved into an absolute origin."""

    id: str
    type: str
    origin: tuple[int, int, int]
    size: tuple[float, float, float]
    fill: str | None
    faces: dict
    opacity: float
    raw: dict
    #: Product of this shape's opacity and all ancestor group opacities.
    inherited_opacity: float = 1.0

    @property
    def depth(self) -> int:
        return self.origin[0] + self.origin[1] + self.origin[2]

    @property
    def sort_key(self) -> tuple[int, str]:
        """ISO020: depth ascending, ties broken by id byte order."""
        return (self.depth, self.id)


@dataclass(slots=True)
class PlacedFace:
    """One projected polygon ready to be emitted as SVG."""

    shape_id: str
    face: Face
    points: list[Point]
    fill: str
    opacity: float
    stroke: str | None = None
    stroke_width: float = 0.0
    #: Secondary ordering within a shape: top paints last so it reads as "on top".
    order: int = 0
    extra: dict = field(default_factory=dict)


_DEFAULT_SIZE = (1.0, 1.0, 1.0)
_FACE_PAINT_ORDER = {Face.LEFT: 0, Face.RIGHT: 1, Face.TOP: 2}


def flatten(scene: dict) -> list[FlatShape]:
    """Resolve groups into a flat list of absolutely-positioned shapes."""
    out: list[FlatShape] = []

    def walk(shapes: list, origin: tuple[int, int, int], opacity: float) -> None:
        for shape in shapes:
            if not isinstance(shape, dict) or shape.get("visible") is False:
                continue
            at = shape.get("at") or {}
            abs_origin = (
                origin[0] + int(at.get("x", 0)),
                origin[1] + int(at.get("y", 0)),
                origin[2] + int(at.get("z", 0)),
            )
            eff_opacity = opacity * float(shape.get("opacity", 1.0))
            if shape.get("type") == "group":
                walk(shape.get("children") or [], abs_origin, eff_opacity)
                continue
            size = shape.get("size") or {}
            out.append(
                FlatShape(
                    id=shape.get("id", ""),
                    type=shape.get("type", ""),
                    origin=abs_origin,
                    size=(
                        float(size.get("x", _DEFAULT_SIZE[0])),
                        float(size.get("y", _DEFAULT_SIZE[1])),
                        float(size.get("z", _DEFAULT_SIZE[2])),
                    ),
                    fill=shape.get("fill"),
                    faces=shape.get("faces") or {},
                    opacity=float(shape.get("opacity", 1.0)),
                    raw=shape,
                    inherited_opacity=eff_opacity,
                )
            )

    walk(scene.get("shapes") or [], (0, 0, 0), 1.0)
    return out


def paint_order(shapes: list[FlatShape]) -> list[FlatShape]:
    """ISO020: sort by (x+y+z) then id. Input array order is never consulted."""
    return sorted(shapes, key=lambda s: s.sort_key)


def _face_style(
    shape: FlatShape, face: Face, palette: dict, shading: dict
) -> tuple[str, float, str | None, float]:
    """Resolve (fill, opacity, stroke, stroke_width) for one face."""
    override = shape.faces.get(str(face)) or {}
    base = resolve_color(shape.fill, palette)

    if "fill" in override:
        fill = resolve_color(override["fill"], palette)
    elif shading.get("enabled", True):
        fill = shade(base, float(shading.get(str(face), 0.0)))
    else:
        fill = base

    fill, alpha = with_opacity(fill, float(override.get("opacity", 1.0)))
    opacity = num(alpha * shape.inherited_opacity)
    stroke = resolve_color(override["stroke"], palette) if "stroke" in override else None
    return fill, opacity, stroke, float(override.get("strokeWidth", 0.0))


def _cube_faces(shape: FlatShape, cell: float, proj: str) -> dict[Face, list[Point]]:
    x, y, z = shape.origin
    sx, sy, sz = shape.size
    x1, y1, z1 = x + sx, y + sy, z + sz
    p = lambda a, b, c: project(a, b, c, cell, proj)
    return {
        # Top: the z1 plane, seen from above.
        Face.TOP: [p(x, y, z1), p(x1, y, z1), p(x1, y1, z1), p(x, y1, z1)],
        # Left: the y1 face, catching less light.
        Face.LEFT: [p(x, y1, z1), p(x1, y1, z1), p(x1, y1, z), p(x, y1, z)],
        # Right: the x1 face, in deepest shadow.
        Face.RIGHT: [p(x1, y, z1), p(x1, y1, z1), p(x1, y1, z), p(x1, y, z)],
    }


def _ramp_faces(shape: FlatShape, cell: float, proj: str) -> dict[Face, list[Point]]:
    """Wedge sloping down toward `facing`, built from per-corner top heights.

    Deriving every face from one height function keeps the four facings consistent and
    lets degenerate faces (the side that collapses to a line) fall out naturally rather
    than being special-cased.
    """
    x, y, z = shape.origin
    sx, sy, sz = shape.size
    x1, y1, z1 = x + sx, y + sy, z + sz
    facing = shape.raw.get("facing", "south")
    p = lambda a, b, c: project(a, b, c, cell, proj)

    def h(cx: float, cy: float) -> float:
        """Height of the sloped top surface at a base corner: high opposite `facing`."""
        if facing == "east":
            return z1 if cx == x else z
        if facing == "west":
            return z1 if cx == x1 else z
        if facing == "north":
            return z1 if cy == y1 else z
        return z1 if cy == y else z  # south

    def quad(corners: list[tuple[float, float]]) -> list[Point]:
        """Vertical face: top edge follows the slope, bottom edge sits on z."""
        top_edge = [p(cx, cy, h(cx, cy)) for cx, cy in corners]
        bottom_edge = [p(cx, cy, z) for cx, cy in reversed(corners)]
        pts = top_edge + bottom_edge
        # Drop consecutive duplicates so a collapsed face becomes a triangle or vanishes.
        out: list[Point] = []
        for pt in pts:
            if not out or out[-1] != pt:
                out.append(pt)
        if len(out) > 1 and out[0] == out[-1]:
            out.pop()
        return out

    return {
        Face.TOP: [
            p(x, y, h(x, y)),
            p(x1, y, h(x1, y)),
            p(x1, y1, h(x1, y1)),
            p(x, y1, h(x, y1)),
        ],
        Face.LEFT: quad([(x, y1), (x1, y1)]),
        Face.RIGHT: quad([(x1, y), (x1, y1)]),
    }


def _cylinder_faces(shape: FlatShape, cell: float, proj: str) -> dict[Face, list[Point]]:
    """Ellipse cap plus a side wall, tessellated at a fixed segment count."""
    x, y, z = shape.origin
    sx, sy, sz = shape.size
    cx, cy = x + sx / 2.0, y + sy / 2.0
    rx, ry = sx / 2.0, sy / 2.0
    z1 = z + sz
    segs = int(shape.raw.get("segments", 48))

    ring: list[tuple[float, float]] = []
    for i in range(segs):
        theta = 2.0 * math.pi * i / segs
        ring.append((cx + rx * math.cos(theta), cy + ry * math.sin(theta)))

    top = [project(a, b, z1, cell, proj) for a, b in ring]

    # Side wall: the silhouette half of the ring, extruded down.
    projected = [(project(a, b, z1, cell, proj)[0], i) for i, (a, b) in enumerate(ring)]
    left_i = min(projected)[1]
    right_i = max(projected)[1]
    front: list[int] = []
    i = right_i
    while True:
        front.append(i)
        if i == left_i:
            break
        i = (i + 1) % segs
    wall = [project(ring[i][0], ring[i][1], z1, cell, proj) for i in front]
    wall += [project(ring[i][0], ring[i][1], z, cell, proj) for i in reversed(front)]

    return {Face.TOP: top, Face.LEFT: wall, Face.RIGHT: []}


def _plane_faces(shape: FlatShape, cell: float, proj: str) -> dict[Face, list[Point]]:
    x, y, z = shape.origin
    sx, sy, sz = shape.size
    orientation = shape.raw.get("orientation", "top")
    p = lambda a, b, c: project(a, b, c, cell, proj)
    if orientation == "top":
        quad = [p(x, y, z), p(x + sx, y, z), p(x + sx, y + sy, z), p(x, y + sy, z)]
        face = Face.TOP
    elif orientation == "left":
        quad = [p(x, y, z + sz), p(x + sx, y, z + sz), p(x + sx, y, z), p(x, y, z)]
        face = Face.LEFT
    else:
        quad = [p(x, y, z + sz), p(x, y + sy, z + sz), p(x, y + sy, z), p(x, y, z)]
        face = Face.RIGHT
    return {face: quad}


_TESSELLATORS = {
    "cube": _cube_faces,
    "ramp": _ramp_faces,
    "cylinder": _cylinder_faces,
    "plane": _plane_faces,
}


def scene_faces(scene: dict) -> list[PlacedFace]:
    """Produce every polygon of a scene, in exact paint order.

    This is the single entry point the SVG writer consumes; anything that renders an
    IsoDSL scene must go through here to stay deterministic.
    """
    grid = scene.get("grid") or {}
    camera = scene.get("camera") or {}
    palette = (scene.get("palette") or {}).get("colors") or {}
    shading = scene.get("shading") or {}
    cell = float(grid.get("cell", 32))
    proj = camera.get("projection", "isometric")

    out: list[PlacedFace] = []
    for shape in paint_order(flatten(scene)):
        tess = _TESSELLATORS.get(shape.type)
        if tess is None:
            continue
        geometry = tess(shape, cell, proj)
        for face, points in geometry.items():
            if len(points) < 3:
                continue
            fill, opacity, stroke, stroke_width = _face_style(shape, face, palette, shading)
            if shape.type == "plane":
                raw_stroke = shape.raw.get("stroke")
                if raw_stroke:
                    stroke = resolve_color(raw_stroke, palette)
                    stroke_width = float(shape.raw.get("strokeWidth", 0.0))
            out.append(
                PlacedFace(
                    shape_id=shape.id,
                    face=face,
                    points=points,
                    fill=fill,
                    opacity=opacity,
                    stroke=stroke,
                    stroke_width=stroke_width,
                    order=_FACE_PAINT_ORDER[face],
                )
            )
    return out


def bounds(faces: list[PlacedFace]) -> tuple[float, float, float, float]:
    """Axis-aligned screen bounds of every polygon: (min_x, min_y, max_x, max_y)."""
    if not faces:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for f in faces for p in f.points]
    ys = [p[1] for f in faces for p in f.points]
    return (min(xs), min(ys), max(xs), max(ys))
