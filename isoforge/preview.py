"""Terminal preview of an IsoDSL scene.

The browser gets the real SVG; the terminal gets a truecolor half-block rasterisation
so the CLI is genuinely usable on its own. We rasterise the same SVG the exporter
produces, which means what you see in the terminal is the actual artwork rather than
a separate approximation that could drift.
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Iterable

from rich.console import Console
from rich.text import Text

# Unicode upper half block: pairs of vertical pixels become one cell, doubling
# vertical resolution against a plain space-and-background approach.
HALF_BLOCK = "\u2580"


def _load_png(data: bytes):
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency in practice
        return None
    return Image.open(io.BytesIO(data)).convert("RGBA")


def render_png_to_text(
    png_bytes: bytes,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    background: tuple[int, int, int] | None = None,
) -> Text:
    """Convert PNG bytes into truecolor terminal text."""
    image = _load_png(png_bytes)
    if image is None:
        return Text("(install Pillow to see terminal previews)", style="dim")

    columns = max_width or min(shutil.get_terminal_size((80, 24)).columns - 4, 64)
    columns = max(8, columns)
    rows_limit = max_height or 28

    # Two pixel rows per text row, and terminal cells are roughly twice as tall as
    # they are wide, so the aspect correction cancels out to 1:1 here.
    target_w = columns
    target_h = int(image.height * (target_w / image.width))
    target_h = max(2, min(target_h, rows_limit * 2))
    if target_h % 2:
        target_h += 1

    from PIL import Image as PILImage

    image = image.resize((target_w, target_h), PILImage.Resampling.LANCZOS)
    pixels = image.load()

    def blend(px: tuple[int, int, int, int]) -> tuple[int, int, int] | None:
        r, g, b, a = px
        if a == 0:
            return None
        if a == 255:
            return (r, g, b)
        if background is None:
            # Preserve terminal transparency for nearly-clear pixels.
            if a < 24:
                return None
            bg = (0, 0, 0)
        else:
            bg = background
        alpha = a / 255
        return tuple(int(c * alpha + bg[i] * (1 - alpha)) for i, c in enumerate((r, g, b)))

    text = Text()
    for y in range(0, target_h, 2):
        for x in range(target_w):
            top = blend(pixels[x, y])
            bottom = blend(pixels[x, y + 1]) if y + 1 < target_h else None

            if top is None and bottom is None:
                text.append(" ")
            elif top is not None and bottom is None:
                text.append(HALF_BLOCK, style=f"rgb({top[0]},{top[1]},{top[2]})")
            elif top is None and bottom is not None:
                # Lower half only: a space with a coloured background fills the
                # bottom cell while leaving the top transparent.
                text.append(" ", style=f"on rgb({bottom[0]},{bottom[1]},{bottom[2]})")
            else:
                text.append(
                    HALF_BLOCK,
                    style=f"rgb({top[0]},{top[1]},{top[2]}) on rgb({bottom[0]},{bottom[1]},{bottom[2]})",
                )
        text.append("\n")
    return text


def scene_summary(scene: dict) -> str:
    """One-line description of a scene for compact CLI output."""
    shapes = scene.get("shapes") or []

    def count(nodes: Iterable) -> int:
        total = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "group":
                total += count(node.get("children") or [])
            else:
                total += 1
        return total

    palette = (scene.get("palette") or {}).get("colors") or {}
    grid = scene.get("grid") or {}
    return (
        f"{count(shapes)} shapes · {len(palette)} colors · "
        f"grid {grid.get('w', '?')}x{grid.get('d', '?')}x{grid.get('h', '?')}"
    )


def palette_swatches(scene: dict) -> Text:
    """Render the palette as colored blocks."""
    colors = (scene.get("palette") or {}).get("colors") or {}
    text = Text()
    for name, value in colors.items():
        if not isinstance(value, str) or not value.startswith("#"):
            continue
        text.append("  ", style=f"on {value[:7]}")
        text.append(f" {name} ", style="dim")
    return text


def print_scene(
    console: Console,
    png_bytes: bytes | None,
    scene: dict,
    *,
    max_width: int | None = None,
) -> None:
    """Print artwork, palette and summary together."""
    if png_bytes:
        console.print(render_png_to_text(png_bytes, max_width=max_width))
    console.print(palette_swatches(scene))
    console.print(f"[dim]{scene_summary(scene)}[/dim]")
