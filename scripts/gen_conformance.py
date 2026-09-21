"""Regenerate the TypeScript/Python geometry conformance fixture.

The Python renderer is the reference implementation. This captures its exact output so
the browser preview can be pinned to it; if they diverge, the user would see something
different from what they export. Run from services/: uv run python ../scripts/gen_conformance.py
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from isoforge_py.isodsl.color import shade  # noqa: E402
from isoforge_py.isodsl.geometry import flatten, paint_order, project, scene_faces  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

COORDS = [
    (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1),
    (2, 1, 0), (3, 2, 1), (-1, 2, 3), (0.5, 0.5, 0.5), (2.25, 0, 1.75),
]
CELLS = (1.0, 32.0, 28.0)
COLORS = ["#7C5CFF", "#FF6B35", "#2E3440", "#FFFFFF", "#000000", "#10B981", "#F43F5E"]
OFFSETS = [0, 0.12, -0.14, -0.28, 0.5, -0.5, 1.0, -1.0]


def main() -> None:
    out: dict = {"projection": [], "shade": [], "scenes": {}}

    for projection in ("isometric", "dimetric"):
        for x, y, z in COORDS:
            for cell in CELLS:
                sx, sy = project(x, y, z, cell, projection)
                out["projection"].append(
                    {"projection": projection, "x": x, "y": y, "z": z,
                     "cell": cell, "sx": sx, "sy": sy}
                )

    for color in COLORS:
        for offset in OFFSETS:
            out["shade"].append({"color": color, "offset": offset,
                                 "result": shade(color, offset)})

    for path in sorted(glob.glob(str(ROOT / "testdata/scenes/valid/*.json"))):
        name = Path(path).name
        scene = json.loads(Path(path).read_text())
        faces = scene_faces(scene)
        out["scenes"][name] = {
            "paint_order": [s.id for s in paint_order(flatten(scene))],
            "face_count": len(faces),
            "faces": [
                {"shape_id": f.shape_id, "face": str(f.face), "fill": f.fill,
                 "opacity": f.opacity, "points": [list(p) for p in f.points]}
                for f in faces
            ],
        }

    target = ROOT / "testdata/golden/geometry-conformance.json"
    target.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {target.relative_to(ROOT)}: "
          f"{len(out['projection'])} coords, {len(out['shade'])} shades, "
          f"{len(out['scenes'])} scenes")


if __name__ == "__main__":
    main()
