"""Scene diffing: minimal, identity-based patches."""

from __future__ import annotations

import copy

from isoforge.diff import diff_scenes


def shape(shape_id: str, x: int = 0, fill: str = "@palette.base") -> dict:
    return {"id": shape_id, "type": "cube", "at": {"x": x, "y": 0, "z": 0}, "fill": fill}


class TestBasics:
    def test_identical_scenes_produce_no_ops(self, single_cube):
        assert not diff_scenes(single_cube, copy.deepcopy(single_cube)).changed

    def test_recolor_is_one_replace(self, single_cube):
        after = copy.deepcopy(single_cube)
        after["shapes"][0]["fill"] = "@palette.accent"

        result = diff_scenes(single_cube, after)
        assert len(result.ops) == 1
        assert result.ops[0].op == "replace"
        assert result.ops[0].path == "/shapes/0/fill"
        assert result.ops[0].value == "@palette.accent"

    def test_added_key_is_one_add(self, single_cube):
        after = copy.deepcopy(single_cube)
        after["effects"] = {"glow": {"enabled": True}}

        result = diff_scenes(single_cube, after)
        assert len(result.ops) == 1
        assert result.ops[0].op == "add" and result.ops[0].path == "/effects"

    def test_removed_key_is_one_remove(self, single_cube):
        after = copy.deepcopy(single_cube)
        del after["meta"]

        result = diff_scenes(single_cube, after)
        assert [o.op for o in result.ops] == ["remove"]

    def test_counts_are_reported(self, single_cube):
        after = copy.deepcopy(single_cube)
        after["shapes"][0]["fill"] = "#123456"
        after["effects"] = {"glow": {"enabled": True}}

        result = diff_scenes(single_cube, after)
        assert result.added == 1 and result.replaced == 1 and result.removed == 0


class TestIdentityMatching:
    """The reason shapes are matched by id rather than position."""

    def test_insert_at_front_is_a_single_add(self):
        before = {"shapes": [shape("a"), shape("b", 1)]}
        after = {"shapes": [shape("new", 2), shape("a"), shape("b", 1)]}

        result = diff_scenes(before, after)
        assert len(result.ops) == 1
        assert result.ops[0].op == "add"

    def test_reordering_without_change_is_a_no_op(self):
        before = {"shapes": [shape("a"), shape("b", 1)]}
        after = {"shapes": [shape("b", 1), shape("a")]}
        assert not diff_scenes(before, after).changed

    def test_removal_is_reported_once(self):
        before = {"shapes": [shape("a"), shape("b", 1), shape("c", 2)]}
        after = {"shapes": [shape("a"), shape("c", 2)]}

        result = diff_scenes(before, after)
        assert [o.op for o in result.ops] == ["remove"]

    def test_edit_addressed_by_new_position(self):
        before = {"shapes": [shape("a"), shape("b", 1)]}
        after = {"shapes": [shape("b", 1, "#FF0000"), shape("a")]}

        result = diff_scenes(before, after)
        assert len(result.ops) == 1
        assert result.ops[0].path == "/shapes/0/fill"

    def test_removals_precede_additions(self):
        """Sequential application requires removals first, in reverse index order."""
        before = {"shapes": [shape("a"), shape("b", 1), shape("c", 2)]}
        after = {"shapes": [shape("a"), shape("d", 3)]}

        ops = diff_scenes(before, after).ops
        assert ops[0].op == "remove" and ops[-1].op == "add"

    def test_unkeyed_lists_fall_back_to_replace(self):
        before = {"tags": ["a", "b"]}
        after = {"tags": ["a", "c"]}

        result = diff_scenes(before, after)
        assert [o.op for o in result.ops] == ["replace"]


class TestDeterminism:
    def test_key_order_does_not_affect_output(self):
        before = {"a": 1, "b": 2}
        after_one = {"a": 9, "b": 2}
        after_two = {"b": 2, "a": 9}

        assert diff_scenes(before, after_one).to_list() == diff_scenes(before, after_two).to_list()

    def test_repeated_diffs_are_stable(self, load_scene):
        before = load_scene("03-all-primitives")
        after = copy.deepcopy(before)
        after["shapes"][0]["fill"] = "#123456"
        after["shapes"][1]["opacity"] = 0.5

        first = diff_scenes(before, after).to_list()
        for _ in range(5):
            assert diff_scenes(before, after).to_list() == first


class TestPointerEscaping:
    def test_slashes_and_tildes_escaped(self):
        before = {"a/b": 1, "c~d": 2}
        after = {"a/b": 9, "c~d": 9}

        paths = {o.path for o in diff_scenes(before, after).ops}
        assert paths == {"/a~1b", "/c~0d"}


def test_to_list_matches_rfc6902_shape():
    before = {"shapes": [shape("a")]}
    after = {"shapes": [shape("a", 0, "#FF0000")]}

    ops = diff_scenes(before, after).to_list()
    assert ops == [{"op": "replace", "path": "/shapes/0/fill", "value": "#FF0000"}]
