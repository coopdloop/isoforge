"""Locate and load the shared IsoDSL JSON Schema.

The schema lives once at the repo root and is shared verbatim by the Go and Python
validators. ISODSL_SCHEMA_PATH overrides the search for packaged installs.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
_REL = Path("schemas") / "isodsl" / "v1" / "isodsl.schema.json"


def _find_schema() -> Path:
    if env := os.environ.get("ISODSL_SCHEMA_PATH"):
        p = Path(env).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"ISODSL_SCHEMA_PATH does not exist: {p}")
        return p

    # Packaged wheels vendor the schema next to this module.
    vendored = Path(__file__).parent / "isodsl.schema.json"
    if vendored.is_file():
        return vendored

    # Development: walk up to the repo root.
    for parent in Path(__file__).resolve().parents:
        if (candidate := parent / _REL).is_file():
            return candidate

    raise FileNotFoundError(
        "Could not locate isodsl.schema.json. Set ISODSL_SCHEMA_PATH to point at it."
    )


SCHEMA_PATH: Path = _find_schema()


@lru_cache(maxsize=1)
def _load() -> dict:
    with SCHEMA_PATH.open("rb") as fh:
        return json.load(fh)


SCHEMA: dict = _load()

if SCHEMA.get("$id", "").find("/v1/") == -1:
    raise RuntimeError(f"Schema at {SCHEMA_PATH} is not IsoDSL v1")
