"""PNG rasterization and icon-bundle tests."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from isoforge.render.raster import (
    DEFAULT_ICON_SIZES,
    RasterError,
    build_icon_bundle,
    render_png,
)

pytest.importorskip("resvg_py", reason="raster extra not installed")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestPng:
    def test_emits_valid_png(self, single_cube):
        data = render_png(single_cube, width=128, height=128)
        assert data.startswith(PNG_MAGIC)

    def test_honours_requested_dimensions(self, single_cube):
        from PIL import Image

        with Image.open(io.BytesIO(render_png(single_cube, width=200, height=150))) as im:
            assert im.size == (200, 150)

    def test_scale_multiplies_canvas(self, single_cube):
        from PIL import Image

        with Image.open(io.BytesIO(render_png(single_cube, scale=2.0))) as im:
            assert im.size == (1024, 1024)

    def test_output_is_byte_stable(self, single_cube):
        """PNG encoders often embed timestamps; ours must not."""
        digests = {
            hashlib.sha256(render_png(single_cube, width=64, height=64)).hexdigest()
            for _ in range(4)
        }
        assert len(digests) == 1

    def test_transparency_preserved_when_background_null(self, single_cube):
        from PIL import Image

        with Image.open(io.BytesIO(render_png(single_cube, width=64, height=64))) as im:
            assert im.convert("RGBA").getpixel((1, 1))[3] == 0

    def test_opaque_when_background_set(self, load_scene):
        from PIL import Image

        scene = load_scene("02-stack-explicit-faces")
        with Image.open(io.BytesIO(render_png(scene, width=64, height=64))) as im:
            assert im.convert("RGBA").getpixel((1, 1))[3] == 255

    def test_rejects_zero_size(self, single_cube):
        with pytest.raises(RasterError):
            render_png(single_cube, width=0, height=0)


class TestIconBundle:
    def test_default_ladder_is_complete(self, single_cube):
        bundle = build_icon_bundle(single_cube)
        for size in DEFAULT_ICON_SIZES:
            assert f"png/icon-{size}.png" in bundle.files

    def test_includes_favicon_and_svg(self, single_cube):
        bundle = build_icon_bundle(single_cube)
        assert "favicon.ico" in bundle.files
        assert bundle.files["icon.svg"].startswith(b"<svg")

    def test_iconset_uses_apple_naming(self, single_cube):
        """These exact names are what `iconutil -c icns` requires."""
        names = build_icon_bundle(single_cube).files
        for expected in (
            "icon.iconset/icon_16x16.png",
            "icon.iconset/icon_16x16@2x.png",
            "icon.iconset/icon_512x512@2x.png",
        ):
            assert expected in names

    def test_custom_sizes_respected(self, single_cube):
        bundle = build_icon_bundle(single_cube, sizes=[24, 48])
        pngs = {n for n in bundle.files if n.startswith("png/")}
        assert pngs == {"png/icon-24.png", "png/icon-48.png"}

    def test_format_filter_respected(self, single_cube):
        bundle = build_icon_bundle(single_cube, formats=["svg"])
        assert set(bundle.files) == {"icon.svg"}

    def test_rejects_empty_size_list(self, single_cube):
        with pytest.raises(RasterError):
            build_icon_bundle(single_cube, sizes=[])

    def test_icons_are_square_even_for_rect_canvas(self, single_cube):
        from PIL import Image

        scene = json.loads(json.dumps(single_cube))
        scene["canvas"] = {"width": 800, "height": 400}
        bundle = build_icon_bundle(scene, sizes=[64])
        with Image.open(io.BytesIO(bundle.files["png/icon-64.png"])) as im:
            assert im.size == (64, 64)

    def test_safe_zone_insets_artwork(self, single_cube):
        """Larger safe zones must shrink the artwork so rounded masks don't clip it."""
        from PIL import Image

        def opaque_pixels(safe_zone: float) -> int:
            bundle = build_icon_bundle(
                single_cube, sizes=[128], padding=0.0, safe_zone=safe_zone, formats=["png"]
            )
            with Image.open(io.BytesIO(bundle.files["png/icon-128.png"])) as im:
                rgba = im.convert("RGBA")
                return sum(
                    1
                    for x in range(rgba.width)
                    for y in range(rgba.height)
                    if rgba.getpixel((x, y))[3] > 0
                )

        assert opaque_pixels(0.30) < opaque_pixels(0.0)

    def test_zip_is_deterministic(self, single_cube):
        a = build_icon_bundle(single_cube, sizes=[32, 64]).to_zip()
        b = build_icon_bundle(single_cube, sizes=[32, 64]).to_zip()
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()

    def test_zip_contents_readable(self, single_cube):
        data = build_icon_bundle(single_cube, sizes=[32]).to_zip()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.testzip() is None
            assert "png/icon-32.png" in zf.namelist()

    def test_bundle_carries_scene_hash(self, single_cube):
        from isoforge.isodsl.canonical import scene_hash

        assert build_icon_bundle(single_cube, sizes=[32]).scene_hash == scene_hash(single_cube)

    def test_write_to_disk(self, single_cube, tmp_path):
        written = build_icon_bundle(single_cube, sizes=[32], formats=["png"]).write_to(tmp_path)
        assert written and all(p.is_file() for p in written)

    def test_source_scene_not_mutated(self, single_cube):
        before = json.dumps(single_cube, sort_keys=True)
        build_icon_bundle(single_cube, sizes=[32])
        assert json.dumps(single_cube, sort_keys=True) == before
