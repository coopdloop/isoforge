"""IsoDSL v1: schema access, validation, canonical serialization and geometry."""

from .errors import IsoError, IsoErrorCode
from .schema import SCHEMA, SCHEMA_PATH, SCHEMA_VERSION
from .validate import validate, validate_or_raise
from .canonical import canonical_json, canonical_bytes, fmt
from .geometry import (
    Face,
    FlatShape,
    PlacedFace,
    flatten,
    paint_order,
    project,
    scene_faces,
)
from .color import resolve_color, shade

__all__ = [
    "SCHEMA",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "IsoError",
    "IsoErrorCode",
    "validate",
    "validate_or_raise",
    "canonical_json",
    "canonical_bytes",
    "fmt",
    "Face",
    "FlatShape",
    "PlacedFace",
    "flatten",
    "paint_order",
    "project",
    "scene_faces",
    "resolve_color",
    "shade",
]
