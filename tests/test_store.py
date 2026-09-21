"""Version storage: plain JSON files on disk."""

from __future__ import annotations

import copy
import json

import pytest

from isoforge.isodsl.errors import IsoValidationError
from isoforge.store import Project, _slugify


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path / "test-logo")


def recolor(scene: dict, fill: str) -> dict:
    out = copy.deepcopy(scene)
    out["shapes"][0]["fill"] = fill
    return out


class TestSaving:
    def test_first_save_is_v1(self, project, single_cube):
        version = project.save(single_cube, "first")
        assert version.number == 1
        assert version.summary == "first"
        assert version.path.is_file()

    def test_versions_increment(self, project, single_cube):
        project.save(single_cube)
        project.save(recolor(single_cube, "@palette.accent"))
        assert [v.number for v in project.versions()] == [1, 2]

    def test_invalid_scene_never_reaches_disk(self, project, single_cube):
        broken = recolor(single_cube, "@palette.ghost")
        with pytest.raises(IsoValidationError):
            project.save(broken)
        assert project.versions() == []

    def test_earlier_versions_are_immutable(self, project, single_cube):
        v1 = project.save(single_cube)
        project.save(recolor(single_cube, "@palette.accent"))
        assert project.get(1).scene_hash == v1.scene_hash

    def test_latest_pointer_file_written(self, project, single_cube):
        project.save(single_cube)
        latest = project.path / "latest.isoforge.json"
        assert latest.is_file()
        assert json.loads(latest.read_text())["version"] == 1

    def test_saved_file_is_readable_json(self, project, single_cube):
        version = project.save(single_cube, "readable")
        document = json.loads(version.path.read_text())
        assert document["summary"] == "readable"
        assert document["scene"]["shapes"][0]["id"] == "core"

    def test_scene_hash_recorded(self, project, single_cube):
        from isoforge.isodsl import scene_hash

        assert project.save(single_cube).scene_hash == scene_hash(single_cube)


class TestReading:
    def test_latest_returns_newest(self, project, single_cube):
        project.save(single_cube)
        project.save(recolor(single_cube, "#123456"), "second")
        assert project.latest.summary == "second"

    def test_scene_property_is_latest_scene(self, project, single_cube):
        project.save(single_cube)
        project.save(recolor(single_cube, "#123456"))
        assert project.scene["shapes"][0]["fill"] == "#123456"

    def test_empty_project_has_no_latest(self, project):
        assert project.latest is None and project.scene is None

    def test_get_missing_version(self, project, single_cube):
        project.save(single_cube)
        assert project.get(99) is None

    def test_shape_count_includes_group_children(self, project, load_scene):
        version = project.save(load_scene("03-all-primitives"))
        # 4 top-level shapes plus 3 pips inside the group.
        assert version.shape_count == 7

    def test_corrupt_file_is_skipped(self, project, single_cube):
        project.save(single_cube)
        (project.path / "v2.isoforge.json").write_text("{not json")
        assert [v.number for v in project.versions()] == [1]


class TestRevert:
    def test_revert_appends_rather_than_truncating(self, project, single_cube):
        v1 = project.save(single_cube)
        project.save(recolor(single_cube, "@palette.accent"))
        project.save(recolor(single_cube, "#123456"))

        reverted = project.revert(1)
        assert reverted.number == 4
        assert reverted.scene_hash == v1.scene_hash

    def test_history_survives_revert(self, project, single_cube):
        project.save(single_cube)
        project.save(recolor(single_cube, "@palette.accent"))
        project.save(recolor(single_cube, "#123456"))
        project.revert(1)

        assert [v.number for v in project.versions()] == [1, 2, 3, 4]
        assert project.get(3) is not None

    def test_revert_summary_names_target(self, project, single_cube):
        project.save(single_cube)
        project.save(recolor(single_cube, "#123456"))
        assert project.revert(1).summary == "Revert to v1"

    def test_revert_to_missing_version_raises(self, project, single_cube):
        project.save(single_cube)
        with pytest.raises(KeyError):
            project.revert(99)


class TestDiscovery:
    def test_list_all_skips_empty_projects(self, tmp_path, single_cube):
        Project(tmp_path / "empty")
        Project(tmp_path / "real").save(single_cube)

        found = Project.list_all(tmp_path)
        assert [p.path.name for p in found] == ["real"]

    def test_most_recent_prefers_newest(self, tmp_path, single_cube):
        import os
        import time

        old = Project(tmp_path / "old")
        old.save(single_cube)
        time.sleep(0.01)
        new = Project(tmp_path / "new")
        new.save(single_cube)
        # Make the ordering unambiguous regardless of filesystem timestamp resolution.
        os.utime(new.latest.path, (time.time() + 10, time.time() + 10))

        assert Project.most_recent(tmp_path).path.name == "new"

    def test_most_recent_on_empty_root(self, tmp_path):
        assert Project.most_recent(tmp_path / "nothing") is None

    def test_name_metadata_round_trips(self, project, single_cube):
        project.set_name("My Cool Logo")
        project.save(single_cube)
        assert Project(project.path).name == "My Cool Logo"

    def test_name_defaults_to_directory(self, project, single_cube):
        project.save(single_cube)
        assert project.name == "test-logo"


class TestSlug:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("My Logo", "my-logo"),
            ("Iso/Forge", "iso-forge"),
            ("  spaced  ", "spaced"),
            ("!!!", "logo"),
            ("", "logo"),
            ("CAPS_and_underscores", "caps-and-underscores"),
        ],
    )
    def test_slugify(self, raw, expected):
        assert _slugify(raw) == expected

    def test_long_names_truncated(self):
        assert len(_slugify("x" * 200)) <= 48


def test_project_survives_reopen(tmp_path, single_cube):
    """Restarting the CLI must not lose work."""
    first = Project(tmp_path / "persist")
    first.save(single_cube, "original")

    second = Project(tmp_path / "persist")
    assert second.latest.summary == "original"
