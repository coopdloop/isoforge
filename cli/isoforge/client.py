"""Typed HTTP client for the local gateway."""

from __future__ import annotations

from typing import Any

import httpx


class GatewayError(RuntimeError):
    """A gateway call failed. Carries structured detail for friendly display."""

    def __init__(self, message: str, *, status: int = 0, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload if isinstance(payload, dict) else {}

    @property
    def validation_errors(self) -> list[dict]:
        """IsoDSL errors, when the failure was a rejected design."""
        detail = self.payload.get("detail")
        if isinstance(detail, dict) and detail.get("errors"):
            return detail["errors"]
        return self.payload.get("errors") or []

    @property
    def hint(self) -> str:
        detail = self.payload.get("detail")
        if isinstance(detail, dict) and detail.get("hint"):
            return detail["hint"]
        return self.payload.get("hint", "")


class GatewayClient:
    """Thin wrapper over the gateway's REST surface."""

    def __init__(self, base_url: str, *, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GatewayClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise GatewayError(f"cannot reach the local gateway: {exc}") from exc

        if response.status_code >= 400:
            payload: Any = {}
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": response.text[:400]}
            message = payload.get("error") or payload.get("detail") or response.reason_phrase
            if isinstance(message, dict):
                message = message.get("error", str(message))
            raise GatewayError(str(message), status=response.status_code, payload=payload)

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # --- sessions ---
    def health(self) -> dict:
        return self._request("GET", "/health")

    def create_session(self, name: str = "", scene: dict | None = None,
                       project_id: str = "") -> dict:
        body: dict[str, Any] = {"name": name, "project_id": project_id}
        if scene is not None:
            body["scene"] = scene
        return self._request("POST", "/sessions", json=body)

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/sessions/{session_id}")

    def end_session(self, session_id: str) -> None:
        self._request("DELETE", f"/sessions/{session_id}")

    def send_message(self, session_id: str, message: str, **overrides) -> dict:
        body = {"message": message, **{k: v for k, v in overrides.items() if v}}
        return self._request("POST", f"/sessions/{session_id}/messages", json=body)

    # --- projects and scenes ---
    def list_projects(self, limit: int = 50) -> list[dict]:
        return self._request("GET", "/projects", params={"limit": limit})["projects"]

    def get_scene(self, project_id: str) -> dict:
        return self._request("GET", f"/scenes/{project_id}")

    def history(self, project_id: str, limit: int = 100) -> list[dict]:
        return self._request("GET", f"/scenes/{project_id}/history",
                             params={"limit": limit})["history"]

    def get_version(self, project_id: str, version: int) -> dict:
        return self._request("GET", f"/scenes/{project_id}/versions/{version}")

    def revert(self, project_id: str, version: int, session_id: str = "") -> dict:
        return self._request("POST", f"/scenes/{project_id}/revert",
                             json={"version": version, "session_id": session_id})

    def diff(self, project_id: str, from_version: int = 0, to_version: int = 0) -> dict:
        params = {}
        if from_version:
            params["from"] = from_version
        if to_version:
            params["to"] = to_version
        return self._request("GET", f"/scenes/{project_id}/diff", params=params)

    # --- render and export ---
    def render(self, *, session_id: str = "", project_id: str = "",
               version: int = 0, scene: dict | None = None) -> str:
        body: dict[str, Any] = {}
        if session_id:
            body["session_id"] = session_id
        if project_id:
            body["project_id"] = project_id
        if version:
            body["version"] = version
        if scene is not None:
            body["scene"] = scene
        return self._request("POST", "/render", json=body)["svg"]

    def export(self, *, fmt: str, session_id: str = "", project_id: str = "",
               version: int = 0, options: dict | None = None,
               filename: str = "") -> dict:
        body: dict[str, Any] = {"format": fmt, "options": options or {}}
        if session_id:
            body["session_id"] = session_id
        if project_id:
            body["project_id"] = project_id
        if version:
            body["version"] = version
        if filename:
            body["filename"] = filename
        return self._request("POST", "/export", json=body)

    def download(self, url: str) -> bytes:
        try:
            response = self._http.get(url)
        except httpx.RequestError as exc:
            raise GatewayError(f"cannot download artifact: {exc}") from exc
        if response.status_code >= 400:
            raise GatewayError(
                f"artifact download failed ({response.status_code})",
                status=response.status_code,
            )
        return response.content

    # --- themes ---
    def list_themes(self) -> list[dict]:
        return self._request("GET", "/themes")["themes"]

    def save_theme(self, name: str, colors: dict[str, str], description: str = "",
                   project_id: str = "") -> dict:
        return self._request("POST", "/themes", json={
            "name": name, "colors": colors,
            "description": description, "project_id": project_id,
        })

    def import_theme(self, raw: bytes) -> dict:
        return self._request("POST", "/themes/import", content=raw,
                             headers={"Content-Type": "application/json"})

    def delete_theme(self, theme_id: str) -> None:
        self._request("DELETE", f"/themes/{theme_id}")

    def validate(self, scene: dict) -> dict:
        return self._request("POST", "/schema/validate", json={"scene": scene})
