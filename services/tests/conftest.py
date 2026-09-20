from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENES = REPO_ROOT / "testdata" / "scenes"
GOLDEN = REPO_ROOT / "testdata" / "golden"


def _load(path: Path) -> dict:
    with path.open("rb") as fh:
        return json.load(fh)


def valid_scene_paths() -> list[Path]:
    return sorted((SCENES / "valid").glob("*.isoforge.json"))


def invalid_scene_paths() -> list[Path]:
    return sorted((SCENES / "invalid").glob("*.isoforge.json"))


@pytest.fixture
def load_scene():
    def _loader(name: str) -> dict:
        return _load(SCENES / "valid" / f"{name}.isoforge.json")

    return _loader


@pytest.fixture
def single_cube(load_scene) -> dict:
    return load_scene("01-single-cube")
