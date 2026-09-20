"""Validation contract tests. Every fixture's expected error code is pinned here."""

from __future__ import annotations

import json

import pytest

from isoforge_py.isodsl import IsoErrorCode, validate, validate_or_raise
from isoforge_py.isodsl.errors import IsoValidationError

from .conftest import invalid_scene_paths, valid_scene_paths


@pytest.mark.parametrize("path", valid_scene_paths(), ids=lambda p: p.stem)
def test_valid_fixtures_pass(path):
    with path.open("rb") as fh:
        result = validate(json.load(fh))
    assert result.valid, result.as_prompt()


@pytest.mark.parametrize("path", invalid_scene_paths(), ids=lambda p: p.stem)
def test_invalid_fixtures_report_expected_code(path):
    """Each invalid fixture declares the code it must trigger via _expect_error."""
    with path.open("rb") as fh:
        scene = json.load(fh)
    expected = scene.pop("_expect_error")
    scene.pop("_note", None)

    result = validate(scene)
    assert not result.valid
    codes = {str(e.code) for e in result.errors}
    assert expected in codes, f"expected {expected}, got {sorted(codes)}"


def test_every_error_carries_actionable_remedy():
    """The repair loop is only as good as these strings; none may be blank."""
    for code in IsoErrorCode:
        from isoforge_py.isodsl.errors import REMEDY

        assert REMEDY.get(code), f"{code} has no remedy text"


def test_duplicate_id_detected_across_group_boundary(single_cube):
    scene = dict(single_cube)
    scene["shapes"] = [
        {"id": "core", "type": "cube", "at": {"x": 0, "y": 0, "z": 0}, "fill": "@palette.base"},
        {
            "id": "g",
            "type": "group",
            "at": {"x": 1, "y": 1, "z": 0},
            "children": [
                {"id": "core", "type": "cube", "at": {"x": 0, "y": 0, "z": 0},
                 "fill": "@palette.base"}
            ],
        },
    ]
    result = validate(scene)
    assert not result.valid
    assert any(e.code == IsoErrorCode.DUPLICATE_ID for e in result.errors)


def test_group_offset_accumulates_for_bounds_check(single_cube):
    """A child in bounds locally can still be out of bounds globally."""
    scene = dict(single_cube)
    scene["grid"] = {"w": 4, "d": 4, "h": 4, "cell": 32}
    scene["shapes"] = [
        {
            "id": "g",
            "type": "group",
            "at": {"x": 3, "y": 3, "z": 3},
            "children": [
                {"id": "far", "type": "cube", "at": {"x": 2, "y": 0, "z": 0},
                 "fill": "@palette.base"}
            ],
        }
    ]
    result = validate(scene)
    assert any(e.code == IsoErrorCode.OUT_OF_BOUNDS for e in result.errors)


def test_unknown_palette_ref_lists_known_keys(single_cube):
    """Error text must name the valid options so the LLM can self-correct in one hop."""
    scene = dict(single_cube)
    scene["shapes"] = [
        {"id": "core", "type": "cube", "at": {"x": 0, "y": 0, "z": 0}, "fill": "@palette.nope"}
    ]
    result = validate(scene)
    err = next(e for e in result.errors if e.code == IsoErrorCode.UNKNOWN_PALETTE_REF)
    assert "base" in err.message and "accent" in err.message


def test_locked_palette_rejects_literal_in_effects(load_scene):
    scene = load_scene("05-locked-palette")
    scene["effects"]["outline"]["color"] = "#FF0000"
    result = validate(scene)
    assert any(e.code == IsoErrorCode.LITERAL_UNDER_LOCK for e in result.errors)


def test_unlocked_palette_allows_literals(single_cube):
    scene = dict(single_cube)
    scene["shapes"] = [
        {"id": "core", "type": "cube", "at": {"x": 0, "y": 0, "z": 0}, "fill": "#123456"}
    ]
    assert validate(scene).valid


def test_unknown_property_is_rejected(single_cube):
    """additionalProperties:false is the guard that makes LLM tool output trustworthy."""
    scene = dict(single_cube)
    scene["shapes"] = [
        {"id": "core", "type": "cube", "at": {"x": 0, "y": 0, "z": 0}, "rotation": 45}
    ]
    result = validate(scene)
    assert not result.valid
    assert any(e.code == IsoErrorCode.SCHEMA for e in result.errors)


def test_validate_or_raise_carries_structured_result(single_cube):
    scene = dict(single_cube)
    scene["shapes"] = [{"id": "x", "type": "cube", "at": {"x": 99, "y": 0, "z": 0}}]
    with pytest.raises(IsoValidationError) as exc:
        validate_or_raise(scene)
    assert exc.value.result.errors


def test_non_dict_input_does_not_crash():
    for garbage in ([], "scene", 42, None):
        result = validate(garbage)
        assert not result.valid


def test_as_prompt_includes_remedies(single_cube):
    scene = dict(single_cube)
    scene["shapes"] = [
        {"id": "core", "type": "cube", "at": {"x": 0, "y": 0, "z": 0}, "fill": "@palette.ghost"}
    ]
    prompt = validate(scene).as_prompt()
    assert "ISO002" in prompt and "fix:" in prompt
