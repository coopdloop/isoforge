"""SVG -> PNG rasterization and app-icon bundle assembly.

resvg is the primary backend: it is a static Rust library with no system dependencies,
which keeps `pipx install isoforge` working on a clean machine. cairosvg is accepted as
a fallback only if someone has it installed, since requiring libcairo would defeat the
point.

Determinism note: PNG encoders love to embed timestamps. Pillow is driven explicitly
with no ancillary chunks so the same scene yields the same bytes forever.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from ..isodsl.canonical import scene_hash
from .svg import render_svg

Backend = Literal["resvg", "cairosvg"]

#: Standard app-icon ladder: favicon through macOS 512@2x.
DEFAULT_ICON_SIZES: tuple[int, ...] = (16, 32, 48, 64, 128, 256, 512, 1024)
#: Sizes Windows .ico actually supports.
ICO_SIZES: tuple[int, ...] = (16, 32, 48, 64, 128, 256)


class RasterError(RuntimeError):
    pass


def _available_backend() -> Backend:
    try:
        import resvg_py  # noqa: F401

        return "resvg"
    except ImportError:
        pass
    try:
        import cairosvg  # noqa: F401

        return "cairosvg"
    except ImportError as exc:
        raise RasterError(
            "No SVG rasterizer available. Install the 'raster' extra: pip install "
            "'isoforge-py[raster]'"
        ) from exc


def svg_to_png(svg: str, *, width: int, height: int, backend: Backend | None = None) -> bytes:
    """Rasterize SVG text to PNG bytes at an exact pixel size."""
    backend = backend or _available_backend()

    if backend == "resvg":
        import resvg_py

        raw = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height)
        data = bytes(raw) if isinstance(raw, (list, bytearray)) else raw
    else:
        import cairosvg

        data = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"), output_width=width, output_height=height
        )

    # Normalise through Pillow so metadata and encoder settings are ours, not the
    # backend's, which is what makes byte-for-byte reproducibility possible.
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGBA")
        if im.size != (width, height):
            im = im.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue()


def render_png(
    scene: dict,
    *,
    width: int | None = None,
    height: int | None = None,
    scale: float = 1.0,
    backend: Backend | None = None,
) -> bytes:
    """Render a validated scene straight to PNG bytes."""
    canvas = scene.get("canvas") or {}
    # Explicit None checks, not truthiness: width=0 is an error, not "unspecified".
    w = int(width) if width is not None else round(float(canvas.get("width", 512)) * scale)
    h = int(height) if height is not None else round(float(canvas.get("height", 512)) * scale)
    if w <= 0 or h <= 0:
        raise RasterError(f"invalid raster size {w}x{h}")
    return svg_to_png(render_svg(scene), width=w, height=h, backend=backend)


def _apply_icon_framing(scene: dict, padding: float, safe_zone: float) -> dict:
    """Re-frame a scene for icon output.

    Icons need the artwork inset from the edge so it survives rounded-corner masking on
    macOS/iOS. We express that as extra canvas padding rather than scaling the geometry,
    so the underlying design is untouched.
    """
    import json

    out = json.loads(json.dumps(scene))
    canvas = out.setdefault("canvas", {})
    effective = min(0.45, max(0.0, padding + safe_zone))
    canvas["padding"] = effective
    # Icons are square by definition; force a square canvas so the ladder is uniform.
    side = max(int(canvas.get("width", 512)), int(canvas.get("height", 512)))
    canvas["width"] = side
    canvas["height"] = side
    out.setdefault("camera", {})["fit"] = True
    return out


@dataclass(slots=True)
class IconBundle:
    """In-memory result of an icon-bundle export."""

    scene_hash: str
    files: dict[str, bytes]

    @property
    def manifest(self) -> list[dict]:
        return [{"name": name, "bytes": len(data)} for name, data in sorted(self.files.items())]

    def to_zip(self) -> bytes:
        """Deterministic zip: fixed timestamps and sorted entries."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(self.files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, self.files[name])
        return buf.getvalue()

    def write_to(self, directory: Path) -> list[Path]:
        directory = Path(directory)
        written: list[Path] = []
        for name in sorted(self.files):
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.files[name])
            written.append(target)
        return written


def build_icon_bundle(
    scene: dict,
    *,
    sizes: Iterable[int] = DEFAULT_ICON_SIZES,
    padding: float = 0.08,
    safe_zone: float = 0.04,
    formats: Iterable[str] = ("png", "svg", "ico"),
    backend: Backend | None = None,
) -> IconBundle:
    """Produce a full app-icon set from one scene.

    Emits the PNG ladder, an ICO for favicons, and the source SVG. The PNG ladder is
    also laid out under `icon.iconset/` using Apple's exact naming so that
    `iconutil -c icns icon.iconset` works directly.
    """
    formats = set(formats)
    sizes = sorted({int(s) for s in sizes if int(s) > 0})
    if not sizes:
        raise RasterError("icon bundle requires at least one positive size")

    framed = _apply_icon_framing(scene, padding, safe_zone)
    svg = render_svg(framed)
    files: dict[str, bytes] = {}

    pngs: dict[int, bytes] = {}
    if "png" in formats or "ico" in formats or "icns" in formats:
        for size in sizes:
            pngs[size] = svg_to_png(svg, width=size, height=size, backend=backend)

    if "png" in formats:
        for size, data in pngs.items():
            files[f"png/icon-{size}.png"] = data

        # Apple iconset layout: 1x and 2x pairs at 16..512.
        for base in (16, 32, 128, 256, 512):
            if base in pngs:
                files[f"icon.iconset/icon_{base}x{base}.png"] = pngs[base]
            if (retina := base * 2) in pngs:
                files[f"icon.iconset/icon_{base}x{base}@2x.png"] = pngs[retina]

    if "svg" in formats:
        files["icon.svg"] = svg.encode("utf-8")

    if "ico" in formats:
        ico_sizes = [s for s in sizes if s in ICO_SIZES] or [min(sizes)]
        largest = max(ico_sizes)
        with Image.open(io.BytesIO(pngs[largest])) as im:
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, format="ICO", sizes=[(s, s) for s in sorted(ico_sizes)])
            files["favicon.ico"] = buf.getvalue()

    return IconBundle(scene_hash=scene_hash(scene), files=files)
