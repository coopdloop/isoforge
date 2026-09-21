"""IsoDSL v1: schema access, validation, canonical serialization and geometry."""

from .canonical import canonical_bytes, canonical_json, fmt, scene_hash
from .color import resolve_color, shade
from .errors import IsoError, IsoErrorCode
from .geometry import (
    Face,
    FlatShape,
    PlacedFace,
    flatten,
    paint_order,
    project,
    scene_faces,
)
from .schema import SCHEMA, SCHEMA_PATH, SCHEMA_VERSION
from .validate import validate, validate_or_raise

__all__ = [
    "SCHEMA",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "Face",
    "FlatShape",
    "IsoError",
    "IsoErrorCode",
    "PlacedFace",
    "canonical_bytes",
    "canonical_json",
    "flatten",
    "fmt",
    "paint_order",
    "project",
    "resolve_color",
    "scene_faces",
    "scene_hash",
    "shade",
    "validate",
    "validate_or_raise",
]
