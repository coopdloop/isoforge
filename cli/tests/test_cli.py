"""CLI unit tests.

These cover the logic that is easy to get wrong and painful to debug by hand:
port selection, binary discovery, error presentation and scene summarisation.
Full end-to-end flows are exercised against the real services separately.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from isoforge.client import GatewayError
from isoforge.preview import scene_summary
from isoforge.supervisor import StartupError, Supervisor, find_binary, free_port, port_is_free


class TestPortHelpers:
    def test_free_port_is_usable(self):
        port = free_port()
        assert 1024 < port < 65536
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))  # must not raise

    def test_free_port_varies(self):
        assert len({free_port() for _ in range(5)}) > 1

    def test_port_is_free_detects_listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            taken = sock.getsockname()[1]
            assert not port_is_free(taken)


class TestBinaryDiscovery:
    def test_finds_repo_built_binary(self):
        """A source checkout that has run `make build` should just work."""
        repo_binary = Path(__file__).resolve().parents[2] / "isoforged"
        found = find_binary("isoforged")
        if repo_binary.is_file():
            assert found is not None
        # When absent, discovery must fail cleanly rather than raise.

    def test_missing_binary_returns_none(self):
        assert find_binary("definitely-not-a-real-binary-xyz") is None


class TestSupervisor:
    def test_rejects_occupied_port_with_actionable_message(self, tmp_path):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            taken = sock.getsockname()[1]

            supervisor = Supervisor(tmp_path)
            with pytest.raises(StartupError) as exc:
                supervisor.start(gateway_port=taken)
            assert "already in use" in str(exc.value)
            assert "--port" in str(exc.value)

    def test_stop_is_idempotent(self, tmp_path):
        supervisor = Supervisor(tmp_path)
        supervisor.stop()
        supervisor.stop()  # must not raise

    def test_gateway_url_format(self, tmp_path):
        supervisor = Supervisor(tmp_path)
        supervisor.gateway_port = 4747
        assert supervisor.gateway_url == "http://127.0.0.1:4747"


class TestGatewayError:
    def test_extracts_nested_validation_errors(self):
        exc = GatewayError("bad", status=422, payload={
            "detail": {"errors": [{"code": "ISO002", "message": "unknown ref",
                                   "remedy": "use a real key"}]}
        })
        assert exc.validation_errors[0]["code"] == "ISO002"

    def test_extracts_flat_validation_errors(self):
        exc = GatewayError("bad", status=400, payload={
            "errors": [{"code": "ISO010", "message": "out of bounds"}]
        })
        assert exc.validation_errors[0]["code"] == "ISO010"

    def test_hint_surfaced_for_unavailable_backend(self):
        exc = GatewayError("down", status=503, payload={"hint": "service may have crashed"})
        assert "crashed" in exc.hint

    def test_no_errors_when_payload_empty(self):
        assert GatewayError("plain").validation_errors == []


class TestSceneSummary:
    def test_counts_flat_shapes(self):
        scene = {
            "shapes": [{"id": "a", "type": "cube"}, {"id": "b", "type": "cube"}],
            "palette": {"colors": {"x": "#FFFFFF"}},
            "grid": {"w": 4, "d": 4, "h": 4},
        }
        summary = scene_summary(scene)
        assert "2 shapes" in summary and "1 colors" in summary and "4x4x4" in summary

    def test_counts_shapes_inside_groups(self):
        """Group children are real shapes and must be counted."""
        scene = {
            "shapes": [
                {"id": "a", "type": "cube"},
                {"id": "g", "type": "group", "children": [
                    {"id": "b", "type": "cube"}, {"id": "c", "type": "cube"},
                ]},
            ],
            "palette": {"colors": {}},
            "grid": {"w": 2, "d": 2, "h": 2},
        }
        assert "3 shapes" in scene_summary(scene)

    def test_handles_empty_scene(self):
        assert "0 shapes" in scene_summary({})


class TestStateFile:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ISOFORGE_DATA_DIR", str(tmp_path))
        import importlib

        from isoforge import main

        importlib.reload(main)
        main.save_state("sess-1", "proj-1", 4747)
        assert main.load_state() == {
            "session_id": "sess-1", "project_id": "proj-1", "port": 4747,
        }

    def test_missing_state_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ISOFORGE_DATA_DIR", str(tmp_path / "nothing"))
        import importlib

        from isoforge import main

        importlib.reload(main)
        assert main.load_state() == {}

    def test_corrupt_state_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ISOFORGE_DATA_DIR", str(tmp_path))
        import importlib

        from isoforge import main

        importlib.reload(main)
        main.state_file().parent.mkdir(parents=True, exist_ok=True)
        main.state_file().write_text("{not json")
        assert main.load_state() == {}


class TestPreviewRendering:
    def test_png_to_text_produces_colored_output(self):
        from PIL import Image
        import io

        from isoforge.preview import render_png_to_text

        image = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")

        text = render_png_to_text(buf.getvalue(), max_width=8)
        assert len(str(text).strip()) > 0
        assert any("rgb(255,0,0)" in str(span.style) for span in text.spans)

    def test_transparent_pixels_render_as_spaces(self):
        from PIL import Image
        import io

        from isoforge.preview import render_png_to_text

        image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        buf = io.BytesIO()
        image.save(buf, format="PNG")

        assert str(render_png_to_text(buf.getvalue(), max_width=8)).strip() == ""


def test_cli_exposes_expected_commands():
    from typer.main import get_command

    from isoforge.main import app

    commands = get_command(app).commands
    for expected in ("chat", "history", "diff", "revert", "export", "doctor", "theme"):
        assert expected in commands
