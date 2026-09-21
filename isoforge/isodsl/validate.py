"""Two-layer IsoDSL validation: JSON Schema for shape, then semantic rules for meaning.

See schemas/isodsl/v1/RULES.md. The Go validator implements the same rules with the same
codes; the shared fixture suite asserts they agree.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from .errors import IsoError, IsoErrorCode, IsoValidationError, ValidationResult
from .schema import SCHEMA

MAX_GROUP_DEPTH = 8
_PALETTE_REF = re.compile(r"^@palette\.([a-z][a-zA-Z0-9_]{0,31})$")
_LITERAL = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

#: Which size axis must be flat for each plane orientation (rule ISO013).
_PLANE_NORMAL = {"top": "z", "left": "y", "right": "x"}


@lru_cache(maxsize=1)
def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(SCHEMA)


def _fmt_path(parts: tuple[Any, ...]) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


def _schema_errors(scene: dict) -> list[IsoError]:
    errors: list[IsoError] = []
    for e in sorted(_schema_validator().iter_errors(scene), key=lambda e: list(e.absolute_path)):
        path = tuple(e.absolute_path)
        # oneOf against the shape discriminator produces a noisy tree; surface the
        # branch matching the declared 'type' so the message names the real problem.
        if e.validator == "oneOf" and isinstance(e.instance, dict) and "type" in e.instance:
            declared = e.instance.get("type")
            best = next(
                (
                    c
                    for c in e.context or []
                    if f"'{declared}'" in c.message or declared in str(c.schema_path)
                ),
                None,
            )
            if best is not None:
                errors.append(
                    IsoError(
                        IsoErrorCode.SCHEMA,
                        best.message,
                        _fmt_path(path + tuple(best.absolute_path)),
                        shape_id=e.instance.get("id"),
                    )
                )
                continue
        shape_id = e.instance.get("id") if isinstance(e.instance, dict) else None
        errors.append(IsoError(IsoErrorCode.SCHEMA, e.message, _fmt_path(path), shape_id=shape_id))
    return errors


def _walk(
    shapes: list[dict],
    origin: tuple[int, int, int] = (0, 0, 0),
    depth: int = 0,
    prefix: str = "shapes",
) -> Iterator[tuple[dict, tuple[int, int, int], int, str]]:
    """Yield (shape, absolute_origin, depth, path) for every shape, groups included."""
    for i, shape in enumerate(shapes):
        if not isinstance(shape, dict):
            continue
        path = f"{prefix}[{i}]"
        at = shape.get("at") or {}
        abs_origin = (
            origin[0] + int(at.get("x", 0)),
            origin[1] + int(at.get("y", 0)),
            origin[2] + int(at.get("z", 0)),
        )
        yield shape, abs_origin, depth, path
        if shape.get("type") == "group":
            children = shape.get("children") or []
            if isinstance(children, list):
                yield from _walk(children, abs_origin, depth + 1, f"{path}.children")


def _colors_in(obj: Any, path: str) -> Iterator[tuple[str, str]]:
    """Yield (color_string, path) for every value that looks like a color."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            if isinstance(v, str) and (_PALETTE_REF.match(v) or _LITERAL.match(v)):
                yield v, sub
            else:
                yield from _colors_in(v, sub)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _colors_in(v, f"{path}[{i}]")


def _semantic_errors(scene: dict) -> list[IsoError]:
    errors: list[IsoError] = []
    shapes = scene.get("shapes") or []
    grid = scene.get("grid") or {}
    palette = scene.get("palette") or {}
    colors = palette.get("colors") or {}
    locked = bool(palette.get("locked"))
    gw, gd, gh = int(grid.get("w", 0)), int(grid.get("d", 0)), int(grid.get("h", 0))

    seen: dict[str, str] = {}
    for shape, origin, depth, path in _walk(shapes):
        sid = shape.get("id")
        stype = shape.get("type")

        # ISO001 - unique ids across the flattened tree.
        if isinstance(sid, str):
            if sid in seen:
                errors.append(
                    IsoError(
                        IsoErrorCode.DUPLICATE_ID,
                        f"id '{sid}' is already used at {seen[sid]}",
                        f"{path}.id",
                        shape_id=sid,
                    )
                )
            else:
                seen[sid] = path

        # ISO012 - bounded nesting.
        if depth >= MAX_GROUP_DEPTH:
            errors.append(
                IsoError(
                    IsoErrorCode.NESTING_TOO_DEEP,
                    f"group nesting depth {depth + 1} exceeds maximum {MAX_GROUP_DEPTH}",
                    path,
                    shape_id=sid,
                )
            )

        # ISO010 - origin inside the grid. Groups are pure offsets and may sit on the
        # far edge, so only leaf shapes are bounds-checked.
        if stype != "group":
            x, y, z = origin
            if not (0 <= x < gw and 0 <= y < gd and 0 <= z < gh):
                errors.append(
                    IsoError(
                        IsoErrorCode.OUT_OF_BOUNDS,
                        f"absolute origin ({x}, {y}, {z}) is outside grid {gw}x{gd}x{gh}",
                        f"{path}.at",
                        shape_id=sid,
                    )
                )
            else:
                # ISO011 - extent inside the grid.
                size = shape.get("size") or {}
                sx = float(size.get("x", 1) or 0)
                sy = float(size.get("y", 1) or 0)
                sz = float(size.get("z", 1) or 0)
                for axis, o, s, limit in (("x", x, sx, gw), ("y", y, sy, gd), ("z", z, sz, gh)):
                    if o + s > limit:
                        errors.append(
                            IsoError(
                                IsoErrorCode.EXTENT_OVERFLOW,
                                f"extent on {axis} reaches {o + s:g}, exceeding grid limit {limit}",
                                f"{path}.size.{axis}",
                                shape_id=sid,
                            )
                        )

        # ISO013 - planes are flat on their normal axis.
        if stype == "plane":
            normal = _PLANE_NORMAL.get(shape.get("orientation", ""))
            size = shape.get("size") or {}
            if normal and float(size.get(normal, 0) or 0) != 0:
                errors.append(
                    IsoError(
                        IsoErrorCode.PLANE_THICKNESS,
                        f"plane with orientation '{shape.get('orientation')}' must have "
                        f"zero extent on {normal}, got {size.get(normal)}",
                        f"{path}.size.{normal}",
                        shape_id=sid,
                    )
                )

    # ISO002 / ISO003 - colors across shapes and effects.
    for root, label in ((scene.get("shapes"), "shapes"), (scene.get("effects"), "effects")):
        if root is None:
            continue
        for color, cpath in _colors_in(root, label):
            if m := _PALETTE_REF.match(color):
                if m.group(1) not in colors:
                    known = ", ".join(sorted(colors)) or "(none)"
                    errors.append(
                        IsoError(
                            IsoErrorCode.UNKNOWN_PALETTE_REF,
                            f"'{color}' does not resolve; palette defines: {known}",
                            cpath,
                        )
                    )
            elif locked:
                errors.append(
                    IsoError(
                        IsoErrorCode.LITERAL_UNDER_LOCK,
                        f"literal color '{color}' is not allowed while the palette is locked",
                        cpath,
                    )
                )

    return errors


def validate(scene: Any) -> ValidationResult:
    """Validate a scene. Schema errors short-circuit, since semantic checks assume shape."""
    if not isinstance(scene, dict):
        return ValidationResult(
            False,
            [IsoError(IsoErrorCode.SCHEMA, f"scene must be an object, got {type(scene).__name__}")],
        )

    if errors := _schema_errors(scene):
        return ValidationResult(False, errors)

    errors = _semantic_errors(scene)
    return ValidationResult(not errors, errors)


def validate_or_raise(scene: Any) -> dict:
    """Validate and return the scene, or raise IsoValidationError."""
    result = validate(scene)
    if not result.valid:
        raise IsoValidationError(result)
    return scene
