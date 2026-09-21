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


class TestCreate:
    """`isoforge chat` must start a new design, not append to the last one."""

    def test_create_without_name_is_timestamped(self, tmp_path):
        project = Project.create(root=tmp_path)
        assert project.slug.startswith("logo-")

    def test_two_unnamed_creates_can_coexist(self, tmp_path, single_cube):
        first = Project.create(root=tmp_path)
        first.save(single_cube)
        second = Project.create(root=tmp_path)
        second.save(single_cube)
        assert first.path != second.path
        assert len(Project.list_all(tmp_path)) == 2

    def test_named_collision_is_suffixed_not_merged(self, tmp_path, single_cube):
        first = Project.create("nimbus", root=tmp_path)
        first.save(single_cube)

        second = Project.create("nimbus", root=tmp_path)
        assert second.slug == "nimbus-2"
        # The original keeps its work.
        assert len(first.versions()) == 1
        assert second.versions() == []

    def test_empty_shell_is_reused(self, tmp_path):
        """An abandoned empty project should not spawn endless suffixes."""
        first = Project.create("draft", root=tmp_path)
        second = Project.create("draft", root=tmp_path)
        assert first.path == second.path

    def test_create_records_display_name(self, tmp_path):
        project = Project.create("My Cool Logo", root=tmp_path)
        assert project.name == "My Cool Logo"
        assert project.slug == "my-cool-logo"


class TestFind:
    def test_find_by_slug(self, tmp_path, single_cube):
        Project.create("nimbus", root=tmp_path).save(single_cube)
        assert Project.find("nimbus", tmp_path) is not None

    def test_find_by_display_name(self, tmp_path, single_cube):
        project = Project.create("Nimbus Cloud", root=tmp_path)
        project.save(single_cube)
        found = Project.find("Nimbus Cloud", tmp_path)
        assert found is not None and found.slug == project.slug

    def test_find_is_case_insensitive(self, tmp_path, single_cube):
        Project.create("Nimbus Cloud", root=tmp_path).save(single_cube)
        assert Project.find("nimbus cloud", tmp_path) is not None

    def test_find_missing_returns_none(self, tmp_path):
        assert Project.find("nothing", tmp_path) is None


class TestMetadata:
    def test_update_individual_fields(self, project, single_cube):
        project.save(single_cube)
        project.update_meta(name="Renamed", description="a test", tags=["work", "client"])

        reopened = Project(project.path)
        assert reopened.name == "Renamed"
        assert reopened.description == "a test"
        assert reopened.tags == ["work", "client"]

    def test_omitted_fields_are_preserved(self, project, single_cube):
        project.save(single_cube)
        project.update_meta(name="Keep", tags=["one"])
        project.update_meta(description="added later")

        reopened = Project(project.path)
        assert reopened.name == "Keep"
        assert reopened.tags == ["one"]
        assert reopened.description == "added later"

    def test_tags_can_be_cleared(self, project, single_cube):
        project.save(single_cube)
        project.update_meta(tags=["temp"])
        project.update_meta(tags=[])
        assert Project(project.path).tags == []

    def test_metadata_survives_without_versions(self, tmp_path):
        project = Project.create("empty-meta", root=tmp_path)
        project.update_meta(description="no versions yet")
        assert Project(project.path).description == "no versions yet"

    def test_created_at_is_recorded(self, project, single_cube):
        project.save(single_cube)
        project.update_meta(name="x")
        assert project.created_at


class TestRenameSlug:
    def test_directory_moves(self, tmp_path, single_cube):
        project = Project.create("before", root=tmp_path)
        project.save(single_cube)

        renamed = project.rename_slug("after")
        assert renamed.slug == "after"
        assert not (tmp_path / "before").exists()
        assert len(renamed.versions()) == 1

    def test_rename_to_existing_raises(self, tmp_path, single_cube):
        Project.create("taken", root=tmp_path).save(single_cube)
        other = Project.create("other", root=tmp_path)
        other.save(single_cube)

        with pytest.raises(FileExistsError):
            other.rename_slug("taken")

    def test_rename_to_same_slug_is_a_noop(self, tmp_path, single_cube):
        project = Project.create("stable", root=tmp_path)
        project.save(single_cube)
        assert project.rename_slug("stable").slug == "stable"


def test_delete_removes_everything(tmp_path, single_cube):
    project = Project.create("doomed", root=tmp_path)
    project.save(single_cube)
    project.delete()
    assert not project.path.exists()
    assert Project.list_all(tmp_path) == []
