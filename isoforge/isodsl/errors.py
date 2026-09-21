"""Structured validation errors.

Error codes are a stable public contract: the agent repair loop feeds them back to the
LLM verbatim, and the web UI's ValidationBadge renders them. Never renumber a code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class IsoErrorCode(StrEnum):
    SCHEMA = "SCHEMA"
    DUPLICATE_ID = "ISO001"
    UNKNOWN_PALETTE_REF = "ISO002"
    LITERAL_UNDER_LOCK = "ISO003"
    OUT_OF_BOUNDS = "ISO010"
    EXTENT_OVERFLOW = "ISO011"
    NESTING_TOO_DEEP = "ISO012"
    PLANE_THICKNESS = "ISO013"


#: Human-facing remedy text. Shown to the LLM during repair, so phrase each one as an
#: actionable instruction rather than a description of the failure.
REMEDY: dict[IsoErrorCode, str] = {
    IsoErrorCode.SCHEMA: (
        "Emit only properties defined in the IsoDSL v1 schema. Unknown properties are "
        "rejected outright; there is no passthrough."
    ),
    IsoErrorCode.DUPLICATE_ID: (
        "Give every shape a unique id across the whole tree, including shapes nested in "
        "groups. Rename the duplicate rather than removing either shape."
    ),
    IsoErrorCode.UNKNOWN_PALETTE_REF: (
        "Reference only keys that exist in palette.colors, or add the missing key to the "
        "palette in the same edit."
    ),
    IsoErrorCode.LITERAL_UNDER_LOCK: (
        "The palette is locked. Replace literal hex colors with '@palette.<name>' "
        "references, or unlock the palette first if the user asked for a new color."
    ),
    IsoErrorCode.OUT_OF_BOUNDS: (
        "Place the shape inside the grid. Remember that a shape inside a group is offset "
        "by that group's 'at' as well as its own."
    ),
    IsoErrorCode.EXTENT_OVERFLOW: (
        "Shrink the shape's size or move its origin so that origin + size stays within the "
        "grid, or enlarge the grid to fit."
    ),
    IsoErrorCode.NESTING_TOO_DEEP: "Flatten the structure; groups may nest at most 8 deep.",
    IsoErrorCode.PLANE_THICKNESS: (
        "A plane is a flat quad. Omit the size component on its normal axis, or set it to 0."
    ),
}


@dataclass(frozen=True, slots=True)
class IsoError:
    """One validation failure, addressed to a precise location in the document."""

    code: IsoErrorCode
    message: str
    #: JSON Pointer-ish path, e.g. "shapes[2].faces.top.fill".
    path: str = ""
    #: Shape id when the failure is attributable to one, for UI highlighting.
    shape_id: str | None = None

    @property
    def remedy(self) -> str:
        return REMEDY.get(self.code, "")

    def __str__(self) -> str:
        where = f" at {self.path}" if self.path else ""
        return f"[{self.code}]{where}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        d = {"code": str(self.code), "message": self.message, "path": self.path}
        if self.shape_id:
            d["shape_id"] = self.shape_id
        if self.remedy:
            d["remedy"] = self.remedy
        return d


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    errors: list[IsoError] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid

    def to_dict(self) -> dict:
        return {"valid": self.valid, "errors": [e.to_dict() for e in self.errors]}

    def as_prompt(self) -> str:
        """Render errors as repair instructions for the LLM."""
        lines = []
        for e in self.errors:
            lines.append(f"- {e}")
            if e.remedy:
                lines.append(f"  fix: {e.remedy}")
        return "\n".join(lines)


class IsoValidationError(Exception):
    """Raised by validate_or_raise. Carries the full structured result."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__(f"{len(result.errors)} IsoDSL validation error(s)\n{result.as_prompt()}")
