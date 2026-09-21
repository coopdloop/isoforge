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


def slugify(name: str) -> str:
    """Turn a display name into a filesystem- and URL-safe handle."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "logo"


#: Internal alias retained for readability at call sites within this module.
_slugify = slugify


def _default_project_name() -> str:
    """Timestamped name for an unnamed design, so each `chat` is its own project.

    Deliberately local time, not UTC: this string is a label the user reads in
    `isoforge list`, and it should match the clock on their wall.
    """
    return datetime.now().astimezone().strftime("logo-%Y%m%d-%H%M%S")


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
        """Open (or create) the project with this name."""
        root = root or default_data_dir()
        return cls(root / _slugify(name))

    @classmethod
    def find(cls, name: str, root: Path | None = None) -> Project | None:
        """Look up an existing project by slug or display name."""
        root = root or default_data_dir()
        direct = root / _slugify(name)
        if direct.is_dir():
            return cls(direct)
        lowered = name.strip().lower()
        return next((p for p in cls.list_all(root) if p.name.lower() == lowered), None)

    @classmethod
    def create(cls, name: str | None = None, root: Path | None = None) -> Project:
        """Create a new project, never reusing one that already has work in it.

        Without this, a second `isoforge chat` would silently append versions to the
        previous design, which is the opposite of starting something new.
        """
        root = root or default_data_dir()
        base = _slugify(name) if name else _default_project_name()

        candidate = root / base
        counter = 2
        while candidate.is_dir() and any(VERSION_RE.match(p.name) for p in candidate.iterdir()):
            candidate = root / f"{base}-{counter}"
            counter += 1

        project = cls(candidate)
        # Only record an explicit display name. Storing the slug as the name would
        # make every design look "named" in listings when it isn't.
        if name:
            project.update_meta(name=name)
        else:
            project.touch()
        return project

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
    def slug(self) -> str:
        """Directory name, and the handle `--project` accepts."""
        return self.path.name

    @property
    def name(self) -> str:
        return self._meta().get("name") or self.path.name

    @property
    def description(self) -> str:
        return self._meta().get("description", "")

    @property
    def tags(self) -> list[str]:
        return list(self._meta().get("tags") or [])

    @property
    def created_at(self) -> str:
        return self._meta().get("created_at", "")

    @property
    def updated_at(self) -> float:
        latest = self.latest
        return latest.path.stat().st_mtime if latest else 0.0

    def _meta(self) -> dict:
        try:
            return json.loads((self.path / META_NAME).read_text())
        except (OSError, ValueError):
            return {}

    def _write_meta(self, meta: dict) -> None:
        meta.setdefault("created_at", _now())
        meta["updated_at"] = _now()
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / META_NAME).write_text(json.dumps(meta, indent=2) + "\n")

    def touch(self) -> None:
        """Write the metadata file so the project exists on disk before any versions."""
        self._write_meta(self._meta())

    def set_name(self, name: str) -> None:
        self.update_meta(name=name)

    def update_meta(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Update editable metadata. Omitted fields are left untouched."""
        meta = self._meta()
        if name is not None:
            meta["name"] = name
        if description is not None:
            meta["description"] = description
        if tags is not None:
            meta["tags"] = tags
        self._write_meta(meta)

    def rename_slug(self, new_slug: str) -> Project:
        """Move the project directory so `--project <slug>` keeps working."""
        target = self.path.parent / _slugify(new_slug)
        if target == self.path:
            return self
        if target.exists():
            raise FileExistsError(f"a project directory named '{target.name}' already exists")
        self.path.rename(target)
        return Project(target)

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
