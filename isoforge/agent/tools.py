"""Tool schemas exposed to the LLM.

Per ADR-001 these are the *only* channel through which design data may enter the system.
The tool parameter schemas embed the real IsoDSL schema, so a well-behaved provider
rejects malformed structure before we ever see it, and our validator catches the rest.
"""

from __future__ import annotations

from typing import Any

from ..isodsl import SCHEMA
from .providers.base import ToolSpec

SET_SCENE = "set_scene"
PATCH_SCENE = "patch_scene"
SAVE_THEME = "save_theme"

#: RFC-6902 operation schema, narrowed to the ops that make sense for a scene graph.
_PATCH_OP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["op", "path"],
    "properties": {
        "op": {
            "type": "string",
            "enum": ["add", "remove", "replace", "move", "copy", "test"],
        },
        "path": {
            "type": "string",
            "description": (
                "JSON Pointer into the scene, e.g. '/shapes/0/fill' or "
                "'/palette/colors/accent'. Use '/shapes/-' to append."
            ),
        },
        "value": {"description": "Required for add, replace and test."},
        "from": {"description": "Source pointer, required for move and copy."},
    },
}


def _scene_parameter_schema() -> dict[str, Any]:
    """Embed the IsoDSL schema as the `scene` argument's type.

    Inlining rather than $ref-ing keeps this self-contained for providers that do not
    resolve external references.
    """
    scene_schema = {k: v for k, v in SCHEMA.items() if not k.startswith("$")}
    scene_schema["$defs"] = SCHEMA.get("$defs", {})
    return {
        "type": "object",
        "required": ["scene", "summary"],
        "properties": {
            "scene": scene_schema,
            "summary": {
                "type": "string",
                "description": (
                    "One short line describing the change, written for a version history "
                    "list. Example: 'Add teal glass cube on top of the stack'."
                ),
            },
        },
    }


def _patch_parameter_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["ops", "summary"],
        "properties": {
            "ops": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "items": _PATCH_OP_SCHEMA,
            },
            "summary": {
                "type": "string",
                "description": "One short line describing the change for version history.",
            },
        },
    }


SET_SCENE_TOOL = ToolSpec(
    name=SET_SCENE,
    description=(
        "Replace the entire scene with a new IsoDSL document. Use this only for the "
        "first design in a session, or when the user asks for something fundamentally "
        "different. For any incremental change, prefer patch_scene."
    ),
    parameters=_scene_parameter_schema(),
)

PATCH_SCENE_TOOL = ToolSpec(
    name=PATCH_SCENE,
    description=(
        "Apply a minimal RFC-6902 JSON Patch to the current scene. This is the preferred "
        "tool: it produces small, reviewable diffs and preserves the parts of the design "
        "the user did not ask to change."
    ),
    parameters=_patch_parameter_schema(),
)

SAVE_THEME_TOOL = ToolSpec(
    name=SAVE_THEME,
    description=(
        "Save the current palette as a reusable named theme. Call this only when the user "
        "explicitly asks to save, remember, or name a palette."
    ),
    parameters={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "description": "Human-readable theme name."},
            "colors": {
                "type": "object",
                "description": "Palette to save. Defaults to the current scene's palette.",
                "additionalProperties": {"type": "string"},
            },
        },
    },
)

ALL_TOOLS: list[ToolSpec] = [SET_SCENE_TOOL, PATCH_SCENE_TOOL, SAVE_THEME_TOOL]


def tools_for(has_scene: bool) -> list[ToolSpec]:
    """Offer only the tools that make sense in the current state.

    Withholding patch_scene on an empty session removes a whole class of failure where
    the model patches a document that does not exist yet.
    """
    return ALL_TOOLS if has_scene else [SET_SCENE_TOOL, SAVE_THEME_TOOL]


def describe_tools() -> list[dict[str, Any]]:
    """Introspection payload for GET /tools."""
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in ALL_TOOLS
    ]
