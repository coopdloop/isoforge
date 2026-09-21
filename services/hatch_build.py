"""Vendor shared assets into the wheel.

The IsoDSL schema lives once at the repo root and is shared by the Go and Python
validators. A source checkout finds it by walking up the tree, but an installed wheel
has no repo above it, so the schema is copied in at build time.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SCHEMA_REL = Path("schemas") / "isodsl" / "v1" / "isodsl.schema.json"


class VendorSchemaHook(BuildHookInterface):
    PLUGIN_NAME = "vendor-schema"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        source = root.parent / SCHEMA_REL
        if not source.is_file():
            source = root / SCHEMA_REL
        if not source.is_file():
            raise FileNotFoundError(
                f"cannot vendor {SCHEMA_REL}: not found relative to {root}"
            )

        target = root / "isoforge_py" / "isodsl" / "isodsl.schema.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        build_data.setdefault("artifacts", []).append(
            "isoforge_py/isodsl/isodsl.schema.json"
        )
