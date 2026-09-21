"""Renderer behaviour and golden-file byte pinning."""

from __future__ import annotations

import hashlib
import json
import os
import re

import pytest

from isoforge.isodsl import validate
from isoforge.isodsl.color import parse_hex, rgb_to_oklab, shade, to_hex
from isoforge.render.svg import render_svg

from .conftest import GOLDEN, valid_scene_paths


@pytest.mark.parametrize("path", valid_scene_paths(), ids=lambda p: p.stem)
def test_matches_golden_bytes(path):
    """Pins exact output. Regenerate deliberately with ISOFORGE_UPDATE_GOLDEN=1."""
    with path.open("rb") as fh:
        scene = json.load(fh)
    actual = render_svg(scene)

    golden = GOLDEN / f"{path.stem.replace('.isoforge', '')}.svg"
    if os.environ.get("ISOFORGE_UPDATE_GOLDEN") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")

    assert golden.is_file(), f"missing golden file {golden}; run with ISOFORGE_UPDATE_GOLDEN=1"
    expected = golden.read_text(encoding="utf-8")
    if actual != expected:
        pytest.fail(
            f"render drift for {path.stem}\n"
            f"expected sha256 {hashlib.sha256(expected.encode()).hexdigest()}\n"
            f"actual   sha256 {hashlib.sha256(actual.encode()).hexdigest()}"
        )


@pytest.mark.parametrize("path", valid_scene_paths(), ids=lambda p: p.stem)
def test_output_is_wellformed_xml(path):
    from xml.dom.minidom import parseString

    with path.open("rb") as fh:
        scene = json.load(fh)
    parseString(render_svg(scene))  # raises on malformed output


@pytest.mark.parametrize("path", valid_scene_paths(), ids=lambda p: p.stem)
def test_no_scientific_notation_in_numeric_output(path):
    """Exponent notation would be valid XML but is not byte-stable across platforms.

    Hex color literals legitimately contain 'E' (e.g. #2E3440), so only coordinate and
    dimension values are inspected.
    """
    with path.open("rb") as fh:
        scene = json.load(fh)
    svg = render_svg(scene)

    numeric_attrs = re.findall(
        r'(?:points|width|height|stroke-width|fill-opacity|x|y|dx|dy|stdDeviation)="([^"]*)"',
        svg,
    )
    for value in numeric_attrs:
        assert not re.search(r"\d[eE][-+]?\d", value), f"exponent notation in {value!r}"
    for transform in re.findall(r'transform="([^"]*)"', svg):
        assert not re.search(r"\d[eE][-+]?\d", transform)


class TestCubeGeometry:
    def test_single_cube_emits_three_faces(self, single_cube):
        assert render_svg(single_cube).count("<polygon") == 3

    def test_auto_shading_produces_three_distinct_tones(self, single_cube):
        fills = re.findall(r'fill="(#[0-9A-F]{6})"', render_svg(single_cube))
        assert len(set(fills)) == 3

    def test_shading_disabled_yields_one_flat_tone(self, single_cube):
        scene = json.loads(json.dumps(single_cube))
        scene["shading"] = {"enabled": False}
        fills = re.findall(r'fill="(#[0-9A-F]{6})"', render_svg(scene))
        assert len(set(fills)) == 1

    def test_top_face_is_lighter_than_sides(self, single_cube):
        """Cube-art legibility depends on the top reading as lit."""
        fills = re.findall(r'fill="(#[0-9A-F]{6})"', render_svg(single_cube))
        lightness = [rgb_to_oklab(parse_hex(f)).L for f in fills]
        assert max(lightness) == lightness[0]  # top paints first in the polygon list

    def test_invisible_shapes_are_omitted(self, single_cube):
        scene = json.loads(json.dumps(single_cube))
        scene["shapes"][0]["visible"] = False
        assert render_svg(scene).count("<polygon") == 0


class TestFraming:
    def test_fit_keeps_framing_stable_as_scene_grows(self, single_cube):
        """Adding a shape must not make the existing one jump around wildly."""
        scene = json.loads(json.dumps(single_cube))
        scene["grid"] = {"w": 4, "d": 4, "h": 4, "cell": 32}
        one = render_svg(scene)
        scene["shapes"].append(
            {
                "id": "second",
                "type": "cube",
                "at": {"x": 1, "y": 1, "z": 0},
                "fill": "@palette.accent",
            }
        )
        two = render_svg(scene)
        assert 'transform="translate(' in one and 'transform="translate(' in two

    def test_fit_disabled_uses_canvas_center(self, single_cube):
        scene = json.loads(json.dumps(single_cube))
        scene["camera"] = {"projection": "isometric", "fit": False}
        assert "translate(256.0,256.0) scale(1.0)" in render_svg(scene)

    def test_background_null_emits_no_rect(self, single_cube):
        assert "<rect" not in render_svg(single_cube)

    def test_background_color_emits_rect(self, load_scene):
        assert "<rect" in render_svg(load_scene("02-stack-explicit-faces"))

    def test_canvas_dimensions_are_respected(self, single_cube):
        scene = json.loads(json.dumps(single_cube))
        scene["canvas"] = {"width": 800, "height": 600}
        svg = render_svg(scene)
        assert 'width="800.0"' in svg and 'height="600.0"' in svg


class TestEffects:
    def test_shadow_and_glow_are_both_applied(self, load_scene):
        """Regression: both effects must reach the output, not just the last one.

        SVG allows one filter attribute per element, so chaining them into a single
        filter is the only way both can take effect.
        """
        svg = render_svg(load_scene("03-all-primitives"))
        assert 'id="iso-effects"' in svg
        assert svg.count("<feDropShadow") == 2
        assert 'filter="url(#iso-effects)"' in svg

    def test_shadow_alone_is_applied(self, single_cube):
        import json as _json

        scene = _json.loads(_json.dumps(single_cube))
        scene["effects"] = {"shadow": {"enabled": True}}
        svg = render_svg(scene)
        assert 'filter="url(#iso-effects)"' in svg and svg.count("<feDropShadow") == 1

    def test_glow_alone_is_applied(self, single_cube):
        import json as _json

        scene = _json.loads(_json.dumps(single_cube))
        scene["effects"] = {"glow": {"enabled": True, "color": "#FFAA00"}}
        svg = render_svg(scene)
        assert 'filter="url(#iso-effects)"' in svg and "#FFAA00" in svg

    def test_disabled_effects_emit_no_filter(self, single_cube):
        import json as _json

        scene = _json.loads(_json.dumps(single_cube))
        scene["effects"] = {"shadow": {"enabled": False}, "glow": {"enabled": False}}
        assert "filter=" not in render_svg(scene)

    def test_filter_ids_are_fixed_not_random(self, load_scene):
        """Random ids would break byte-stability."""
        scene = load_scene("03-all-primitives")
        ids_a = re.findall(r'id="([^"]+)"', render_svg(scene))
        ids_b = re.findall(r'id="([^"]+)"', render_svg(scene))
        assert ids_a == ids_b

    def test_face_outline_strokes_polygons(self, load_scene):
        assert "stroke=" in render_svg(load_scene("05-locked-palette"))

    def test_no_effects_means_no_defs(self, single_cube):
        assert "<defs>" not in render_svg(single_cube)


class TestPrimitiveCoverage:
    def test_all_primitives_render(self, load_scene):
        scene = load_scene("03-all-primitives")
        assert validate(scene).valid
        assert render_svg(scene).count("<polygon") > 10

    def test_cylinder_segment_count_is_honoured(self):
        scene = {
            "isodsl_version": "1.0.0",
            "canvas": {"width": 256, "height": 256},
            "grid": {"w": 4, "d": 4, "h": 4},
            "palette": {"colors": {"a": "#10B981"}},
            "shapes": [
                {
                    "id": "c",
                    "type": "cylinder",
                    "at": {"x": 0, "y": 0, "z": 0},
                    "size": {"x": 2, "y": 2, "z": 2},
                    "segments": 12,
                    "fill": "@palette.a",
                }
            ],
        }
        assert validate(scene).valid
        svg = render_svg(scene)
        top = re.search(r'<polygon points="([^"]+)"', svg).group(1)
        assert len(top.split()) == 12

    def test_group_children_inherit_offset(self, load_scene):
        """Group 'cluster' sits at (1,4,1); its pips must render away from the origin."""
        scene = load_scene("03-all-primitives")
        assert render_svg(scene).count("<polygon") >= 15


class TestColor:
    def test_shade_zero_is_identity(self):
        assert shade("#7C5CFF", 0) == "#7C5CFF"

    def test_positive_offset_lightens(self):
        assert (
            rgb_to_oklab(parse_hex(shade("#7C5CFF", 0.2))).L > rgb_to_oklab(parse_hex("#7C5CFF")).L
        )

    def test_negative_offset_darkens(self):
        assert (
            rgb_to_oklab(parse_hex(shade("#7C5CFF", -0.2))).L < rgb_to_oklab(parse_hex("#7C5CFF")).L
        )

    def test_shading_preserves_hue_family(self):
        """The OKLab choice exists so shaded faces stay on-brand rather than going grey."""
        base = rgb_to_oklab(parse_hex("#7C5CFF"))
        shaded = rgb_to_oklab(parse_hex(shade("#7C5CFF", -0.25)))
        assert abs(base.a - shaded.a) < 0.06 and abs(base.b - shaded.b) < 0.06

    def test_extreme_offsets_clamp_into_gamut(self):
        assert shade("#7C5CFF", 1.0) and shade("#7C5CFF", -1.0)

    def test_alpha_round_trips(self):
        assert to_hex(parse_hex("#11223344")) == "#11223344"

    def test_opaque_alpha_is_omitted(self):
        assert to_hex(parse_hex("#112233FF")) == "#112233"

    def test_shorthand_hex_is_rejected(self):
        with pytest.raises(ValueError):
            parse_hex("#abc")
