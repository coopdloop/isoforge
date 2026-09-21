"""Version history as plain JSON files.

Every accepted design is written as `v1.isoforge.json`, `v2.isoforge.json`, ... inside
a project directory. That is the whole storage layer: no database, no schema
migrations, no server. Directory listing gives history, reading two files gives a diff,
and copying an old file forward gives revert.

This is also more git-friendly than a database, which matters because the point of the
product is designs you can version like source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .isodsl import canonical_json, scene_hash, validate
from .isodsl.errors import IsoValidationError

VERSION_RE = re.compile(r"^v(\d+)\.isoforge\.json$")
META_NAME = "project.json"


def default_data_dir() -> Path:
    """Where projects live. Honours ISOFORGE_DATA_DIR, then XDG."""
    if override := os.environ.get("ISOFORGE_DATA_DIR"):
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / "isoforge"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "logo"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Version:
    """One immutable snapshot of a design."""

    number: int
    scene: dict
    summary: str
    created_at: str
    scene_hash: str
    path: Path

    @property
    def shape_count(self) -> int:
        def count(nodes) -> int:
            total = 0
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                total += count(node.get("children")) if node.get("type") == "group" else 1
            return total

        return count(self.scene.get("shapes"))


class Project:
    """A logo workspace: one directory of numbered scene files."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    # --- discovery ---

    @classmethod
    def open(cls, name: str, root: Path | None = None) -> Project:
        root = root or default_data_dir()
        return cls(root / _slugify(name))

    @classmethod
    def list_all(cls, root: Path | None = None) -> list[Project]:
        """Projects that contain at least one version, most recently changed first."""
        root = root or default_data_dir()
        if not root.is_dir():
            return []
        projects = [cls(p) for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
        projects = [p for p in projects if p.latest is not None]
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)

    @classmethod
    def most_recent(cls, root: Path | None = None) -> Project | None:
        found = cls.list_all(root)
        return found[0] if found else None

    # --- metadata ---

    @property
    def name(self) -> str:
        meta = self._meta()
        return meta.get("name") or self.path.name

    @property
    def updated_at(self) -> float:
        latest = self.latest
        return latest.path.stat().st_mtime if latest else 0.0

    def _meta(self) -> dict:
        try:
            return json.loads((self.path / META_NAME).read_text())
        except (OSError, ValueError):
            return {}

    def set_name(self, name: str) -> None:
        meta = self._meta()
        meta["name"] = name
        meta.setdefault("created_at", _now())
        (self.path / META_NAME).write_text(json.dumps(meta, indent=2) + "\n")

    # --- versions ---

    def versions(self) -> list[Version]:
        """All versions in ascending order."""
        found: list[Version] = []
        for entry in self.path.iterdir():
            match = VERSION_RE.match(entry.name)
            if not match:
                continue
            try:
                document = json.loads(entry.read_text())
            except (OSError, ValueError):
                continue  # a corrupt file must not break history
            scene = document.get("scene", document)
            found.append(
                Version(
                    number=int(match.group(1)),
                    scene=scene,
                    summary=document.get("summary", ""),
                    created_at=document.get("created_at", ""),
                    scene_hash=document.get("scene_hash") or scene_hash(scene),
                    path=entry,
                )
            )
        return sorted(found, key=lambda v: v.number)

    def get(self, number: int) -> Version | None:
        return next((v for v in self.versions() if v.number == number), None)

    @property
    def latest(self) -> Version | None:
        found = self.versions()
        return found[-1] if found else None

    @property
    def scene(self) -> dict | None:
        latest = self.latest
        return latest.scene if latest else None

    def save(self, scene: dict, summary: str = "") -> Version:
        """Validate and append a new version.

        Invalid scenes never reach disk, whatever produced them.
        """
        result = validate(scene)
        if not result.valid:
            raise IsoValidationError(result)

        number = (self.latest.number + 1) if self.latest else 1
        created = _now()
        digest = scene_hash(scene)

        document = {
            "isoforge_version": 1,
            "version": number,
            "summary": summary,
            "created_at": created,
            "scene_hash": digest,
            "scene": scene,
        }
        target = self.path / f"v{number}.isoforge.json"
        target.write_text(canonical_json(document) + "\n")

        # Stable path for tooling and `isoforge export` shortcuts.
        shutil.copyfile(target, self.path / "latest.isoforge.json")

        return Version(number, scene, summary, created, digest, target)

    def revert(self, number: int) -> Version:
        """Restore an earlier version by appending a copy of it.

        History is never truncated: reverting to v2 from v5 creates v6, so the user can
        always undo the undo. In a tool where reverts are cheap and frequent, silently
        destroying v3-v5 would be an unpleasant surprise.
        """
        target = self.get(number)
        if target is None:
            raise KeyError(f"version {number} does not exist")
        return self.save(target.scene, f"Revert to v{number}")

    def delete(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
