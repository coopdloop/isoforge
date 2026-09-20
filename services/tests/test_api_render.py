"""HTTP contract tests for the render/export routes."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from isoforge_py.api.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Point the export store at a temp dir so tests never touch ./exports."""
    from isoforge_py.api import render_routes
    from isoforge_py.render.export import ExportStore

    monkeypatch.setattr(render_routes, "store", ExportStore(tmp_path / "exports"))
    return TestClient(app)


class TestHealth:
    def test_reports_both_logical_services(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert set(body["services"]) == {"agent_orchestrator", "render_engine"}

    def test_reports_raster_backend(self, client):
        assert client.get("/health").json()["raster_backend"] in {
            "resvg", "cairosvg", "unavailable"
        }

    def test_schema_is_servable_to_clients(self, client):
        schema = client.get("/schema/isodsl").json()
        assert schema["title"] == "IsoDSL Scene"


class TestRender:
    def test_svg_round_trip(self, client, single_cube):
        body = client.post("/render/svg", json={"scene": single_cube}).json()
        assert body["svg"].startswith("<svg") and len(body["scene_hash"]) == 64

    def test_png_returns_image_bytes(self, client, single_cube):
        r = client.post("/render/png", json={"scene": single_cube, "width": 64, "height": 64})
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG")

    def test_png_carries_scene_hash_header(self, client, single_cube):
        r = client.post("/render/png", json={"scene": single_cube, "width": 32, "height": 32})
        assert len(r.headers["x-scene-hash"]) == 64

    def test_invalid_scene_returns_structured_400(self, client, single_cube):
        """The UI renders these errors inline, so the shape is a contract."""
        bad = json.loads(json.dumps(single_cube))
        bad["shapes"] = [{"id": "x", "type": "cube", "at": {"x": 0, "y": 0, "z": 0}, "nope": 1}]
        r = client.post("/render/svg", json={"scene": bad})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["valid"] is False
        assert detail["errors"][0]["code"] == "SCHEMA"

    def test_semantic_error_includes_remedy(self, client, single_cube):
        bad = json.loads(json.dumps(single_cube))
        bad["shapes"] = [
            {"id": "x", "type": "cube", "at": {"x": 0, "y": 0, "z": 0}, "fill": "@palette.ghost"}
        ]
        detail = client.post("/render/svg", json={"scene": bad}).json()["detail"]
        assert detail["errors"][0]["code"] == "ISO002"
        assert detail["errors"][0]["remedy"]

    def test_oversized_raster_rejected_by_validation(self, client, single_cube):
        r = client.post("/render/png", json={"scene": single_cube, "width": 99999})
        assert r.status_code == 422


class TestValidateScene:
    def test_valid_scene_reports_true(self, client, single_cube):
        assert client.post("/validate-scene", json={"scene": single_cube}).json()["valid"]

    def test_invalid_scene_is_200_not_500(self, client):
        """Invalid input is an expected outcome in a chat tool, not a server error."""
        r = client.post("/validate-scene", json={"scene": {"isodsl_version": "1.0.0"}})
        assert r.status_code == 200 and r.json()["valid"] is False

    def test_accepts_bare_scene_body(self, client, single_cube):
        assert client.post("/validate-scene", json=single_cube).json()["valid"]


class TestExports:
    def test_svg_export_is_downloadable(self, client, single_cube):
        rec = client.post("/export/svg", json={"scene": single_cube}).json()
        r = client.get(rec["download_url"])
        assert r.status_code == 200 and r.content.startswith(b"<svg")

    def test_png_export_records_dimensions(self, client, single_cube):
        rec = client.post(
            "/export/png", json={"scene": single_cube, "width": 128, "height": 128}
        ).json()
        assert rec["width"] == 128 and rec["height"] == 128

    def test_icon_bundle_returns_manifest(self, client, single_cube):
        rec = client.post(
            "/export/icon-bundle", json={"scene": single_cube, "sizes": [32, 64]}
        ).json()
        names = {entry["name"] for entry in rec["metadata"]["manifest"]}
        assert "png/icon-32.png" in names and "favicon.ico" in names

    def test_icon_bundle_downloads_as_zip(self, client, single_cube):
        import io
        import zipfile

        rec = client.post(
            "/export/icon-bundle", json={"scene": single_cube, "sizes": [32]}
        ).json()
        data = client.get(f"/exports/{rec['bundle_id']}/download").content
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert "png/icon-32.png" in zf.namelist()

    def test_isoforge_json_export_is_canonical(self, client, single_cube):
        rec = client.post("/export/isoforge-json", json={"scene": single_cube}).json()
        text = client.get(rec["download_url"]).text
        assert json.loads(text)["isodsl_version"] == "1.0.0"
        assert text.index('"isodsl_version"') < text.index('"canvas"')  # key order pinned

    def test_metadata_lookup(self, client, single_cube):
        rec = client.post("/export/svg", json={"scene": single_cube}).json()
        assert client.get(f"/exports/{rec['export_id']}").json()["artifact_type"] == "svg"

    def test_unknown_export_404s(self, client):
        assert client.get("/exports/deadbeef").status_code == 404
        assert client.get("/exports/deadbeef/download").status_code == 404

    def test_filename_traversal_is_neutralised(self, client, single_cube):
        """A caller-supplied filename must never escape the export directory."""
        rec = client.post(
            "/export/svg", json={"scene": single_cube, "filename": "../../etc/pwned.svg"}
        ).json()
        assert rec["filename"] == "pwned.svg"
        assert "/etc/pwned.svg" not in rec["file_path"]


class TestImportAndDiff:
    def test_import_returns_canonical_form(self, client, single_cube):
        body = client.post("/import/isoforge-json", json={"scene": single_cube}).json()
        assert body["canonical"].startswith("{")
        assert len(body["scene_hash"]) == 64

    def test_import_rejects_invalid(self, client):
        r = client.post("/import/isoforge-json", json={"scene": {"isodsl_version": "1.0.0"}})
        assert r.status_code == 400

    def test_diff_detects_change(self, client, single_cube):
        after = json.loads(json.dumps(single_cube))
        after["shapes"][0]["fill"] = "@palette.accent"
        body = client.post(
            "/diff/render", json={"before": single_cube, "after": after}
        ).json()
        assert body["changed"] is True
        assert body["before"]["svg"] != body["after"]["svg"]

    def test_diff_detects_no_change(self, client, single_cube):
        body = client.post(
            "/diff/render", json={"before": single_cube, "after": single_cube}
        ).json()
        assert body["changed"] is False
