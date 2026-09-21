"""Determinism tests: same scene in, same bytes out.

If any of these fail, the product's central claim (reproducible, diffable designs) is
broken, so they are deliberately strict.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from isoforge.isodsl import canonical_bytes, fmt, project
from isoforge.isodsl.canonical import canonical_json, scene_hash
from isoforge.isodsl.geometry import flatten, paint_order
from isoforge.render.svg import render_svg

from .conftest import valid_scene_paths


@pytest.mark.parametrize("path", valid_scene_paths(), ids=lambda p: p.stem)
def test_render_is_byte_stable_across_repeats(path):
    with path.open("rb") as fh:
        scene = json.load(fh)
    digests = {hashlib.sha256(render_svg(scene).encode()).hexdigest() for _ in range(8)}
    assert len(digests) == 1


@pytest.mark.parametrize("path", valid_scene_paths(), ids=lambda p: p.stem)
def test_render_ignores_shape_array_order(path):
    """ISO020: reversing the input array must not change a single output byte."""
    with path.open("rb") as fh:
        scene = json.load(fh)
    if len(scene.get("shapes", [])) < 2:
        pytest.skip("needs at least two shapes")

    shuffled = dict(scene)
    shuffled["shapes"] = list(reversed(scene["shapes"]))
    assert render_svg(shuffled) == render_svg(scene)


@pytest.mark.parametrize("path", valid_scene_paths(), ids=lambda p: p.stem)
def test_canonical_json_ignores_key_order(path):
    """ISO022: key insertion order must not affect the canonical encoding."""
    with path.open("rb") as fh:
        scene = json.load(fh)
    reordered = {k: scene[k] for k in reversed(list(scene.keys()))}
    assert canonical_bytes(reordered) == canonical_bytes(scene)


def test_scene_hash_changes_when_design_changes(single_cube):
    before = scene_hash(single_cube)
    mutated = json.loads(json.dumps(single_cube))
    mutated["shapes"][0]["fill"] = "@palette.accent"
    assert scene_hash(mutated) != before


def test_scene_hash_stable_under_cosmetic_reformatting(single_cube):
    """Whitespace and key order are not design changes and must not bump the hash."""
    reformatted = json.loads(json.dumps(single_cube, indent=8, sort_keys=True))
    assert scene_hash(reformatted) == scene_hash(single_cube)


def test_depth_sort_is_geometry_not_array_order(load_scene):
    """Fixture 04 lists shapes in deliberately wrong order; check the documented result."""
    scene = load_scene("04-depth-sort-order")
    order = [s.id for s in paint_order(flatten(scene))]
    assert order == ["origin", "mid_a", "mid_b", "far", "apex"]


def test_depth_ties_break_on_id():
    """Equal (x+y+z) must resolve by id byte order, never by position in the array."""
    scene = {
        "isodsl_version": "1.0.0",
        "canvas": {"width": 256, "height": 256},
        "grid": {"w": 4, "d": 4, "h": 4},
        "palette": {"colors": {"a": "#FFFFFF"}},
        "shapes": [
            {"id": "zebra", "type": "cube", "at": {"x": 2, "y": 0, "z": 0}, "fill": "@palette.a"},
            {"id": "alpha", "type": "cube", "at": {"x": 0, "y": 2, "z": 0}, "fill": "@palette.a"},
            {"id": "mango", "type": "cube", "at": {"x": 1, "y": 1, "z": 0}, "fill": "@palette.a"},
        ],
    }
    assert [s.id for s in paint_order(flatten(scene))] == ["alpha", "mango", "zebra"]


class TestNumberFormatting:
    """ISO021: fixed precision, no exponent, no negative zero."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0.0"),
            (-0.0, "0.0"),
            (1, "1.0"),
            (0.5, "0.5"),
            (1e-9, "0.0"),
            (0.1 + 0.2, "0.3"),
            (1234.56789, "1234.5679"),
            (-27.712812921102035, "-27.7128"),
            (1e12, "1000000000000.0"),
            (0.00005, "0.0001"),
        ],
    )
    def test_formats(self, value, expected):
        assert fmt(value) == expected

    def test_never_uses_scientific_notation(self):
        for v in (1e-20, 1e20, -1e-15):
            assert "e" not in fmt(v).lower()

    def test_negative_zero_collapses(self):
        assert fmt(-0.0) == fmt(0.0) == "0.0"


class TestProjection:
    """Coordinates the TypeScript preview renderer must reproduce exactly."""

    def test_origin_maps_to_origin(self):
        assert project(0, 0, 0) == (0.0, 0.0)

    def test_z_is_up(self):
        _, sy = project(0, 0, 1)
        assert sy < 0

    def test_x_and_y_mirror_horizontally(self):
        sx_x, _ = project(1, 0, 0)
        sx_y, _ = project(0, 1, 0)
        assert sx_x == -sx_y

    def test_depth_increases_with_coordinate_sum(self):
        """The invariant the whole paint-order rule rests on."""
        prev = None
        for total in range(6):
            _, sy = project(total, 0, 0)
            if prev is not None:
                assert sy > prev
            prev = sy

    @pytest.mark.parametrize(
        "coord,expected",
        [
            ((1, 0, 0), (27.7128, 16.0)),
            ((0, 1, 0), (-27.7128, 16.0)),
            ((0, 0, 1), (0.0, -32.0)),
            ((1, 1, 1), (0.0, 0.0)),
            ((2, 1, 0), (27.7128, 48.0)),
        ],
    )
    def test_pinned_isometric_coordinates(self, coord, expected):
        assert project(*coord, 32.0) == expected

    @pytest.mark.parametrize(
        "coord,expected",
        [((1, 0, 0), (32.0, 16.0)), ((0, 0, 1), (0.0, -32.0))],
    )
    def test_pinned_dimetric_coordinates(self, coord, expected):
        assert project(*coord, 32.0, "dimetric") == expected


def test_canonical_json_round_trips(single_cube):
    text = canonical_json(single_cube)
    assert json.loads(text) == json.loads(canonical_json(json.loads(text)))
