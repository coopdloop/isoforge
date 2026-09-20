"""Export artifact store.

Exports are written to disk and addressed by id so the gateway can hand the CLI or
browser a download URL without streaming bytes through every hop.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ArtifactType = Literal["svg", "png", "icon-bundle", "isoforge-json"]

_MEDIA_TYPES: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/vnd.microsoft.icon",
    ".zip": "application/zip",
    ".json": "application/json",
}


def media_type_for(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


@dataclass(slots=True)
class ExportRecord:
    id: str
    artifact_type: ArtifactType
    path: Path
    scene_hash: str
    created_at: float
    width: int | None = None
    height: int | None = None
    transparent_background: bool = True
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "export_id": self.id,
            "artifact_type": self.artifact_type,
            "file_path": str(self.path),
            "filename": self.path.name,
            "scene_hash": self.scene_hash,
            "size_bytes": self.path.stat().st_size if self.path.is_file() else 0,
            "width": self.width,
            "height": self.height,
            "transparent_background": self.transparent_background,
            "download_url": f"/exports/{self.id}/download",
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class ExportStore:
    """Filesystem-backed export registry with an in-memory index.

    The index is rebuilt from disk on startup so exports survive a service restart,
    which matters because the CLI may hand a user a path long after the fact.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(
            root or os.environ.get("EXPORT_OUTPUT_DIR", "./exports")
        ).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, ExportRecord] = {}
        self._reload()

    def _meta_path(self, export_id: str) -> Path:
        return self.root / export_id / "export.json"

    def _reload(self) -> None:
        for meta in self.root.glob("*/export.json"):
            try:
                data = json.loads(meta.read_text())
                path = Path(data["file_path"])
                if not path.is_file():
                    continue
                self._records[data["export_id"]] = ExportRecord(
                    id=data["export_id"],
                    artifact_type=data["artifact_type"],
                    path=path,
                    scene_hash=data.get("scene_hash", ""),
                    created_at=data.get("created_at", 0.0),
                    width=data.get("width"),
                    height=data.get("height"),
                    transparent_background=data.get("transparent_background", True),
                    metadata=data.get("metadata") or {},
                )
            except (OSError, ValueError, KeyError):
                continue  # A corrupt record must not take down the service.

    def save(
        self,
        *,
        artifact_type: ArtifactType,
        filename: str,
        data: bytes,
        scene_hash: str,
        width: int | None = None,
        height: int | None = None,
        transparent_background: bool = True,
        metadata: dict | None = None,
    ) -> ExportRecord:
        export_id = uuid.uuid4().hex
        target_dir = self.root / export_id
        target_dir.mkdir(parents=True, exist_ok=True)
        # Defend against traversal via a caller-supplied filename.
        target = target_dir / Path(filename).name
        target.write_bytes(data)

        record = ExportRecord(
            id=export_id,
            artifact_type=artifact_type,
            path=target,
            scene_hash=scene_hash,
            created_at=time.time(),
            width=width,
            height=height,
            transparent_background=transparent_background,
            metadata=metadata or {},
        )
        self._records[export_id] = record
        self._meta_path(export_id).write_text(json.dumps(record.to_dict(), indent=2))
        return record

    def get(self, export_id: str) -> ExportRecord | None:
        return self._records.get(export_id)

    def list(self, limit: int = 100) -> list[ExportRecord]:
        return sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    def delete(self, export_id: str) -> bool:
        record = self._records.pop(export_id, None)
        if record is None:
            return False
        shutil.rmtree(self.root / export_id, ignore_errors=True)
        return True
