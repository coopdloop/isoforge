"""Orchestrator tests: tool dispatch, patching, and the validate-repair loop.

These cover ADR-001's central guarantee, so they are the most important tests in the
agent layer: no unvalidated payload may ever become the scene.
"""

from __future__ import annotations

import copy
import json

import pytest

from isoforge.agent.orchestrator import Conversation, Orchestrator, TurnError, apply_patch
from isoforge.agent.providers.base import ProviderError
from isoforge.agent.tools import PATCH_SCENE, SET_SCENE

from .fake_provider import (
    FakeProvider,
    patch_scene_result,
    save_theme_result,
    set_scene_result,
    text_result,
)


@pytest.fixture
def conversation():
    return Conversation(project_id="p1", model_provider="fake", model_name="fake-1")


@pytest.fixture
def seeded(single_cube):
    return Conversation(
        project_id="p1",
        model_provider="fake",
        model_name="fake-1",
        scene=copy.deepcopy(single_cube),
    )


class TestSetScene:
    async def test_valid_scene_is_accepted(self, conversation, single_cube):
        provider = FakeProvider([set_scene_result(single_cube, "initial design")])
        result = await Orchestrator(provider).run_turn(conversation, "make a purple cube")

        assert result.changed and result.is_full_scene
        assert result.summary == "initial design"
        assert conversation.scene == single_cube
        assert provider.call_count == 1

    async def test_reply_falls_back_to_summary(self, conversation, single_cube):
        provider = FakeProvider([set_scene_result(single_cube, "a cube", text="")])
        result = await Orchestrator(provider).run_turn(conversation, "cube")
        assert result.reply == "a cube"

    async def test_scene_hash_is_reported(self, conversation, single_cube):
        from isoforge.isodsl.canonical import scene_hash

        provider = FakeProvider([set_scene_result(single_cube)])
        result = await Orchestrator(provider).run_turn(conversation, "cube")
        assert result.scene_hash == scene_hash(single_cube)

    async def test_missing_scene_argument_is_rejected(self, conversation):
        from isoforge.agent.providers.base import CompletionResult, ToolCall

        bad = CompletionResult(
            tool_calls=[ToolCall(name=SET_SCENE, arguments={"summary": "oops"})], text=""
        )
        provider = FakeProvider([bad, bad, bad])
        with pytest.raises(TurnError):
            await Orchestrator(provider).run_turn(conversation, "cube")


class TestPatchScene:
    async def test_patch_applies_to_current_scene(self, seeded):
        provider = FakeProvider(
            [
                patch_scene_result(
                    [{"op": "replace", "path": "/shapes/0/fill", "value": "@palette.accent"}],
                    "recolor",
                )
            ]
        )
        result = await Orchestrator(provider).run_turn(seeded, "make it teal")

        assert result.changed and not result.is_full_scene
        assert result.patch == [
            {"op": "replace", "path": "/shapes/0/fill", "value": "@palette.accent"}
        ]
        assert seeded.scene["shapes"][0]["fill"] == "@palette.accent"

    async def test_patch_preserves_untouched_fields(self, seeded):
        original_meta = copy.deepcopy(seeded.scene["meta"])
        provider = FakeProvider(
            [patch_scene_result([{"op": "replace", "path": "/shapes/0/fill", "value": "#123456"}])]
        )
        await Orchestrator(provider).run_turn(seeded, "recolor")
        assert seeded.scene["meta"] == original_meta

    async def test_append_shape_via_dash_pointer(self, seeded):
        new_shape = {
            "id": "second",
            "type": "cube",
            "at": {"x": 1, "y": 1, "z": 0},
            "fill": "@palette.accent",
        }
        provider = FakeProvider(
            [patch_scene_result([{"op": "add", "path": "/shapes/-", "value": new_shape}])]
        )
        await Orchestrator(provider).run_turn(seeded, "add another cube")
        assert len(seeded.scene["shapes"]) == 2

    async def test_patch_without_existing_scene_fails(self, conversation):
        ops = [{"op": "replace", "path": "/shapes/0/fill", "value": "#fff"}]
        provider = FakeProvider([patch_scene_result(ops)] * 3)
        with pytest.raises(TurnError, match="before any scene exists"):
            await Orchestrator(provider).run_turn(conversation, "recolor")

    async def test_patch_tool_withheld_until_scene_exists(self, conversation, single_cube):
        provider = FakeProvider([set_scene_result(single_cube)])
        await Orchestrator(provider).run_turn(conversation, "cube")
        offered = {t.name for t in provider.tools_offered[0]}
        assert PATCH_SCENE not in offered and SET_SCENE in offered

    async def test_patch_tool_offered_once_scene_exists(self, seeded):
        provider = FakeProvider([text_result("ok")])
        await Orchestrator(provider).run_turn(seeded, "hello")
        assert PATCH_SCENE in {t.name for t in provider.tools_offered[0]}

    async def test_bad_pointer_is_reported(self, seeded):
        ops = [{"op": "replace", "path": "/nonexistent/deeply/nested", "value": 1}]
        provider = FakeProvider([patch_scene_result(ops)] * 3)
        with pytest.raises(TurnError):
            await Orchestrator(provider).run_turn(seeded, "break it")


class TestRepairLoop:
    """The heart of ADR-001."""

    async def test_invalid_then_valid_recovers(self, conversation, single_cube):
        broken = copy.deepcopy(single_cube)
        broken["shapes"][0]["fill"] = "@palette.does_not_exist"

        provider = FakeProvider(
            [set_scene_result(broken, "bad"), set_scene_result(single_cube, "fixed")]
        )
        result = await Orchestrator(provider).run_turn(conversation, "cube")

        assert result.changed and result.repair_attempts == 1
        assert conversation.scene == single_cube

    async def test_repair_prompt_carries_error_codes(self, conversation, single_cube):
        broken = copy.deepcopy(single_cube)
        broken["shapes"][0]["fill"] = "@palette.ghost"
        provider = FakeProvider([set_scene_result(broken), set_scene_result(single_cube)])
        await Orchestrator(provider).run_turn(conversation, "cube")

        second_call = provider.calls[1]
        assert any("ISO002" in m.content for m in second_call)
        assert any(m.role == "tool" for m in second_call)

    async def test_exhausted_repairs_leaves_scene_untouched(self, seeded, single_cube):
        """A failing turn must not corrupt a previously good design."""
        broken = copy.deepcopy(single_cube)
        broken["shapes"][0]["fill"] = "@palette.ghost"
        provider = FakeProvider([set_scene_result(broken)] * 3)

        with pytest.raises(TurnError) as exc:
            await Orchestrator(provider).run_turn(seeded, "cube")

        assert seeded.scene == single_cube
        assert any(e["code"] == "ISO002" for e in exc.value.errors)

    async def test_repair_attempts_are_bounded(self, conversation, single_cube):
        broken = copy.deepcopy(single_cube)
        broken["shapes"][0]["fill"] = "@palette.ghost"
        provider = FakeProvider([set_scene_result(broken)] * 5)

        with pytest.raises(TurnError):
            await Orchestrator(provider, max_repair_attempts=2).run_turn(conversation, "x")
        assert provider.call_count == 3  # initial + 2 repairs

    async def test_unvalidated_scene_never_persists(self, conversation, single_cube):
        broken = copy.deepcopy(single_cube)
        broken["shapes"][0]["rotation"] = 45  # schema violation
        provider = FakeProvider([set_scene_result(broken)] * 3)

        with pytest.raises(TurnError):
            await Orchestrator(provider).run_turn(conversation, "x")
        assert conversation.scene is None


class TestConversationalTurns:
    async def test_text_only_turn_changes_nothing(self, seeded, single_cube):
        provider = FakeProvider([text_result("It's a cube on a grid.")])
        result = await Orchestrator(provider).run_turn(seeded, "what is this?")

        assert not result.changed
        assert result.reply == "It's a cube on a grid."
        assert seeded.scene == single_cube

    async def test_history_accumulates(self, seeded):
        provider = FakeProvider([text_result("one"), text_result("two")])
        orchestrator = Orchestrator(provider)
        await orchestrator.run_turn(seeded, "first")
        await orchestrator.run_turn(seeded, "second")
        assert len(seeded.messages) == 4  # 2 user + 2 assistant

    async def test_provider_failure_surfaces_clearly(self, conversation):
        provider = FakeProvider([ProviderError("429 rate limited", retryable=True)])
        with pytest.raises(TurnError, match="model call failed"):
            await Orchestrator(provider).run_turn(conversation, "cube")


class TestThemeLock:
    async def test_save_theme_does_not_alter_scene(self, seeded, single_cube):
        provider = FakeProvider([save_theme_result("My Brand")])
        result = await Orchestrator(provider).run_turn(seeded, "save this palette")

        assert result.theme_saved["name"] == "My Brand"
        assert result.theme_saved["colors"] == single_cube["palette"]["colors"]
        assert not result.changed
        assert seeded.scene == single_cube

    async def test_lock_clamps_stray_literals_to_palette(self, seeded):
        """A deterministic fix beats spending a repair round-trip on a trivial slip."""
        seeded.theme_locked = True
        seeded.scene["palette"]["locked"] = True

        drifted = copy.deepcopy(seeded.scene)
        drifted["shapes"][0]["fill"] = "#7C5CFF"  # literal equal to palette.base
        provider = FakeProvider([set_scene_result(drifted)])

        result = await Orchestrator(provider).run_turn(seeded, "keep it purple")
        assert result.changed
        assert seeded.scene["shapes"][0]["fill"] == "@palette.base"

    async def test_lock_rejects_truly_new_literal(self, seeded):
        seeded.theme_locked = True
        seeded.scene["palette"]["locked"] = True

        drifted = copy.deepcopy(seeded.scene)
        drifted["shapes"][0]["fill"] = "#FF0000"  # not in the palette
        provider = FakeProvider([set_scene_result(drifted)] * 3)

        with pytest.raises(TurnError) as exc:
            await Orchestrator(provider).run_turn(seeded, "make it red")
        assert any(e["code"] == "ISO003" for e in exc.value.errors)

    async def test_lock_is_announced_in_system_prompt(self, seeded):
        seeded.theme_locked = True
        provider = FakeProvider([text_result("ok")])
        await Orchestrator(provider).run_turn(seeded, "hi")
        assert "Theme lock is ON" in provider.last_system_prompt()


class TestSystemPrompt:
    async def test_current_scene_is_grounded_in_prompt(self, seeded):
        provider = FakeProvider([text_result("ok")])
        await Orchestrator(provider).run_turn(seeded, "hi")
        prompt = provider.last_system_prompt()
        assert "# Current scene" in prompt and '"core"' in prompt

    async def test_empty_session_gets_palette_suggestions(self, conversation, single_cube):
        provider = FakeProvider([set_scene_result(single_cube)])
        await Orchestrator(provider).run_turn(conversation, "cube")
        assert "Curated palettes" in provider.last_system_prompt()

    async def test_shape_ids_are_listed_for_patching(self, seeded):
        provider = FakeProvider([text_result("ok")])
        await Orchestrator(provider).run_turn(seeded, "hi")
        assert "shape ids currently in the scene: core" in provider.last_system_prompt()


class TestApplyPatch:
    def test_does_not_mutate_input(self, single_cube):
        before = json.dumps(single_cube, sort_keys=True)
        apply_patch(single_cube, [{"op": "replace", "path": "/shapes/0/fill", "value": "#fff"}])
        assert json.dumps(single_cube, sort_keys=True) == before

    def test_remove_operation(self, single_cube):
        out = apply_patch(single_cube, [{"op": "remove", "path": "/shapes/0"}])
        assert out["shapes"] == []

    def test_invalid_op_raises_turn_error(self, single_cube):
        with pytest.raises(TurnError):
            apply_patch(single_cube, [{"op": "nonsense", "path": "/shapes"}])
