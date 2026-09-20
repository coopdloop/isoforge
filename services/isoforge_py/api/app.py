"""The single Python service: agent orchestrator + render engine in one FastAPI app.

The spec describes these as two services on :5001 and :5002. They are merged here per
the ratified plan (PLAN.md section 6): route namespaces do not collide, the gateway
points both AGENT_SERVICE_URL and RENDER_SERVICE_URL at this app, and either half can
be split back out later without changing a single route.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..isodsl import SCHEMA, SCHEMA_PATH, SCHEMA_VERSION, validate
from . import agent_routes, render_routes

app = FastAPI(
    title="IsoForge Python services",
    version="0.1.0",
    description="Agent orchestration and deterministic rendering for IsoForge.",
)

app.include_router(render_routes.router)
app.include_router(agent_routes.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, Any]:
    """Merged health for both logical services; the gateway health-gates on this."""
    raster = "unavailable"
    try:
        import resvg_py  # noqa: F401

        raster = "resvg"
    except ImportError:
        try:
            import cairosvg  # noqa: F401

            raster = "cairosvg"
        except ImportError:
            pass

    return {
        "status": "ok",
        "services": ["agent_orchestrator", "render_engine"],
        "isodsl_version": SCHEMA_VERSION,
        "schema_path": str(SCHEMA_PATH),
        "raster_backend": raster,
    }


@app.get("/schema/isodsl", tags=["validation"])
def get_schema() -> dict:
    """Serve the schema so the web UI and SDK can validate client-side."""
    return SCHEMA


@app.post("/validate-scene", tags=["validation"])
def validate_scene(payload: dict) -> JSONResponse:
    """Validate a scene. Always 200; the body carries the verdict.

    A malformed scene is a normal, expected outcome in a conversational tool, not an
    HTTP error, so the UI can render errors inline without exception handling.
    """
    scene = payload.get("scene", payload)
    result = validate(scene)
    return JSONResponse(status_code=200, content=result.to_dict())


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("ISOFORGE_PY_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENT_ORCHESTRATOR_PORT", "5001")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
