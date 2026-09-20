"""HTTP contract tests for the agent/chat routes."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from isoforge_py.api import agent_routes
from isoforge_py.api.app import app

from .fake_provider import FakeProvider, patch_scene_result, set_scene_result, text_result


@pytest.fixture
def client():
    agent_routes._conversations.clear()
    return TestClient(app)


@pytest.fixture
def scripted(monkeypatch):
    """Install a scripted provider in place of any real LLM backend."""

    def install(script):
        provider = FakeProvider(list(script))
        monkeypatch.setattr(agent_routes, "build_provider", lambda *a, **k: provider)
        return provider

    return install


class TestConversationLifecycle:
    def test_create_and_fetch(self, client, scripted):
        scripted([])
        created = client.post("/conversations", json={"project_id": "p1"}).json()
        assert created["has_scene"] is False
        fetched = client.get(f"/conversations/{created['id']}").json()
        assert fetched["id"] == created["id"]

    def test_create_with_seed_scene(self, client, scripted, single_cube):
        scripted([])
        created = client.post("/conversations", json={"scene": single_cube}).json()
        assert created["has_scene"] is True and len(created["scene_hash"]) == 64

    def test_seed_scene_is_validated(self, client, scripted):
        scripted([])
        r = client.post("/conversations", json={"scene": {"isodsl_version": "1.0.0"}})
        assert r.status_code == 400

    def test_delete(self, client, scripted):
        scripted([])
        cid = client.post("/conversations", json={}).json()["id"]
        assert client.delete(f"/conversations/{cid}").status_code == 204
        assert client.get(f"/conversations/{cid}").status_code == 404

    def test_unknown_conversation_404s(self, client):
        assert client.get("/conversations/nope").status_code == 404


class TestTurns:
    def test_first_turn_creates_scene(self, client, scripted, single_cube):
        scripted([set_scene_result(single_cube, "a purple cube")])
        cid = client.post("/conversations", json={}).json()["id"]

        body = client.post(f"/conversations/{cid}/turns", json={"message": "purple cube"}).json()
        assert body["changed"] and body["is_full_scene"]
        assert body["summary"] == "a purple cube"
        assert body["scene"]["shapes"][0]["id"] == "core"

    def test_second_turn_patches(self, client, scripted, single_cube):
        scripted(
            [
                set_scene_result(single_cube),
                patch_scene_result(
                    [{"op": "replace", "path": "/shapes/0/fill", "value": "@palette.accent"}],
                    "recolor to teal",
                ),
            ]
        )
        cid = client.post("/conversations", json={}).json()["id"]
        client.post(f"/conversations/{cid}/turns", json={"message": "cube"})
        body = client.post(f"/conversations/{cid}/turns", json={"message": "teal"}).json()

        assert body["is_full_scene"] is False
        assert body["patch"][0]["op"] == "replace"
        assert body["scene"]["shapes"][0]["fill"] == "@palette.accent"

    def test_failed_turn_returns_422_with_errors(self, client, scripted, single_cube):
        broken = copy.deepcopy(single_cube)
        broken["shapes"][0]["fill"] = "@palette.ghost"
        scripted([set_scene_result(broken)] * 3)

        cid = client.post("/conversations", json={}).json()["id"]
        r = client.post(f"/conversations/{cid}/turns", json={"message": "cube"})
        assert r.status_code == 422
        assert any(e["code"] == "ISO002" for e in r.json()["detail"]["errors"])

    def test_empty_message_rejected(self, client, scripted):
        scripted([])
        cid = client.post("/conversations", json={}).json()["id"]
        assert client.post(f"/conversations/{cid}/turns", json={"message": ""}).status_code == 422

    def test_conversational_turn_reports_no_change(self, client, scripted, single_cube):
        scripted([set_scene_result(single_cube), text_result("It is a cube.")])
        cid = client.post("/conversations", json={}).json()["id"]
        client.post(f"/conversations/{cid}/turns", json={"message": "cube"})
        body = client.post(f"/conversations/{cid}/turns", json={"message": "what?"}).json()
        assert body["changed"] is False and body["reply"] == "It is a cube."


class TestReloadAndThemeLock:
    def test_reload_replaces_scene(self, client, scripted, single_cube, load_scene):
        scripted([])
        cid = client.post("/conversations", json={"scene": single_cube}).json()["id"]
        other = load_scene("05-locked-palette")
        body = client.post(f"/conversations/{cid}/reload", json={"scene": other}).json()
        assert body["has_scene"] and len(body["scene_hash"]) == 64

    def test_reload_validates(self, client, scripted):
        scripted([])
        cid = client.post("/conversations", json={}).json()["id"]
        r = client.post(f"/conversations/{cid}/reload", json={"scene": {"bad": True}})
        assert r.status_code == 400

    def test_theme_lock_toggles(self, client, scripted, single_cube):
        scripted([])
        cid = client.post("/conversations", json={"scene": single_cube}).json()["id"]
        assert client.post(f"/conversations/{cid}/theme-lock", json={"locked": True}).json()[
            "theme_locked"
        ]
        assert not client.post(
            f"/conversations/{cid}/theme-lock", json={"locked": False}
        ).json()["theme_locked"]


class TestIntrospection:
    def test_tools_expose_both_design_tools(self, client):
        names = {t["name"] for t in client.get("/tools").json()["tools"]}
        assert {"set_scene", "patch_scene"} <= names

    def test_set_scene_embeds_isodsl_schema(self, client):
        """The tool schema must carry the real constraints, not a loose object type."""
        tools = {t["name"]: t for t in client.get("/tools").json()["tools"]}
        scene_schema = tools["set_scene"]["parameters"]["properties"]["scene"]
        assert scene_schema["additionalProperties"] is False
        assert "shapes" in scene_schema["properties"]

    def test_patch_tool_uses_rfc6902_ops(self, client):
        tools = {t["name"]: t for t in client.get("/tools").json()["tools"]}
        ops = tools["patch_scene"]["parameters"]["properties"]["ops"]
        assert "replace" in ops["items"]["properties"]["op"]["enum"]

    def test_providers_endpoint_lists_all_three(self, client):
        body = client.get("/providers").json()
        assert set(body["all"]) == {"openrouter", "anthropic", "ollama"}
