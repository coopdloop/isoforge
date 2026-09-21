"""Scene diffing.

Produces RFC-6902 operations between two scenes. Arrays of shapes are matched by `id`
rather than by position, because a positional diff reports "every shape changed" when
one shape is inserted at the front — useless in a tool whose whole job is showing what
the model actually edited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PatchOp:
    op: str
    path: str
    value: Any = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.op in ("add", "replace", "test"):
            out["value"] = self.value
        return out


@dataclass(slots=True)
class Diff:
    ops: list[PatchOp] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.ops)

    @property
    def added(self) -> int:
        return sum(1 for o in self.ops if o.op == "add")

    @property
    def removed(self) -> int:
        return sum(1 for o in self.ops if o.op == "remove")

    @property
    def replaced(self) -> int:
        return sum(1 for o in self.ops if o.op == "replace")

    def to_list(self) -> list[dict]:
        return [o.to_dict() for o in self.ops]


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _keyed_by_id(items: list) -> bool:
    return bool(items) and all(isinstance(i, dict) and isinstance(i.get("id"), str) for i in items)


def _diff_value(path: str, a: Any, b: Any) -> list[PatchOp]:
    if _same(a, b):
        return []

    if isinstance(a, dict) and isinstance(b, dict):
        return _diff_object(path, a, b)

    if isinstance(a, list) and isinstance(b, list) and _keyed_by_id(a) and _keyed_by_id(b):
        return _diff_keyed_array(path, a, b)

    return [PatchOp("replace", path, b)]


def _diff_object(path: str, a: dict, b: dict) -> list[PatchOp]:
    ops: list[PatchOp] = []
    for key in sorted(set(a) | set(b)):  # sorted for deterministic output
        child = f"{path}/{_escape(key)}"
        if key in a and key not in b:
            ops.append(PatchOp("remove", child))
        elif key not in a and key in b:
            ops.append(PatchOp("add", child, b[key]))
        else:
            ops.extend(_diff_value(child, a[key], b[key]))
    return ops


def _diff_keyed_array(path: str, a: list, b: list) -> list[PatchOp]:
    ops: list[PatchOp] = []
    a_index = {item["id"]: item for item in a}
    b_index = {item["id"]: item for item in b}

    # Removals first, in reverse order, so earlier indices stay valid as the patch is
    # applied sequentially.
    for i in range(len(a) - 1, -1, -1):
        if a[i]["id"] not in b_index:
            ops.append(PatchOp("remove", f"{path}/{i}"))

    # Modifications to surviving shapes, addressed by their new position.
    for new_index, item in enumerate(b):
        if (previous := a_index.get(item["id"])) is not None:
            ops.extend(_diff_value(f"{path}/{new_index}", previous, item))

    # Additions last.
    for item in b:
        if item["id"] not in a_index:
            ops.append(PatchOp("add", f"{path}/-", item))

    return ops


def diff_scenes(before: dict, after: dict) -> Diff:
    """Compare two scenes and return the minimal patch between them."""
    return Diff(_diff_value("", before, after))
