"""Canonical number formatting and JSON serialization (rules ISO021, ISO022).

Determinism is the product's core promise, so nothing here may depend on dict insertion
order, platform float formatting, or locale. The same rules are implemented in Go; both
are pinned by the shared fixture suite.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

#: ISO021: fixed decimal places for every emitted float.
PRECISION = 4
_QUANT = Decimal(1).scaleb(-PRECISION)


def fmt(value: float | Decimal) -> str:
    """Format a number for output: fixed precision, no exponent, no negative zero.

    Python's repr gives 0.1+0.2 -> '0.30000000000000004' and formats small magnitudes in
    scientific notation; both would break byte-stability. Decimal with explicit half-up
    rounding matches Go's strconv.FormatFloat(-1, 'f') behaviour after quantisation.
    """
    d = Decimal(str(value)) if not isinstance(value, Decimal) else value
    q = d.quantize(_QUANT, rounding=ROUND_HALF_UP)
    if q == 0:
        q = abs(q)  # collapse -0.0000 to 0.0000
    s = f"{q:f}"
    # Trim trailing zeros but always keep at least one decimal place for float-ness.
    if "." in s:
        s = s.rstrip("0")
        if s.endswith("."):
            s += "0"
    return s


def num(value: float | Decimal) -> float:
    """Quantise a number to canonical precision, returning a float."""
    return float(fmt(value))


#: ISO022: schema-declared key order. Keys absent here sort last, alphabetically, so
#: forward-compatible additions degrade predictably instead of scrambling output.
_KEY_ORDER: dict[str, int] = {
    k: i
    for i, k in enumerate(
        [
            "isodsl_version",
            "meta",
            "canvas",
            "grid",
            "camera",
            "palette",
            "shading",
            "effects",
            "shapes",
            # meta
            "name",
            "description",
            "tags",
            # canvas / grid / camera
            "width",
            "height",
            "background",
            "padding",
            "w",
            "d",
            "h",
            "cell",
            "show",
            "projection",
            "fit",
            # palette
            "id",
            "locked",
            "colors",
            # shading / effects
            "enabled",
            "top",
            "left",
            "right",
            "outline",
            "shadow",
            "glow",
            "color",
            "opacity",
            "blur",
            "offset",
            "radius",
            "intensity",
            "scope",
            # shape
            "type",
            "at",
            "size",
            "facing",
            "orientation",
            "segments",
            "fill",
            "faces",
            "stroke",
            "strokeWidth",
            "bevel",
            "visible",
            "label",
            "children",
            # coords
            "x",
            "y",
            "z",
        ]
    )
}


def _key_rank(key: str) -> tuple[int, str]:
    return (_KEY_ORDER.get(key, len(_KEY_ORDER)), key)


def canonicalize(value: Any) -> Any:
    """Recursively reorder keys and quantise floats into canonical form."""
    if isinstance(value, dict):
        return {
            k: canonicalize(value[k])
            for k in sorted(value.keys(), key=_key_rank)
            if not k.startswith("_")  # drop fixture annotations
        }
    if isinstance(value, list):
        return [canonicalize(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return num(value)
    return value


def canonical_json(scene: dict, *, indent: int | None = 2) -> str:
    """Serialize a scene to its canonical textual form.

    indent=2 is the on-disk form (git-friendly, human-diffable); indent=None is the
    compact form used for hashing and the WebSocket wire.
    """
    return json.dumps(
        canonicalize(scene),
        indent=indent,
        separators=(",", ": ") if indent is not None else (",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(scene: dict) -> bytes:
    """Compact canonical encoding, suitable for hashing and content addressing."""
    return canonical_json(scene, indent=None).encode("utf-8")


def scene_hash(scene: dict) -> str:
    """Stable content hash of a scene. Identical scenes always hash identically."""
    import hashlib

    return hashlib.sha256(canonical_bytes(scene)).hexdigest()
