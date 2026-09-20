"""render_engine routes (spec service `render_engine`, merged into the single Python app)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..isodsl import validate
from ..isodsl.canonical import canonical_json, scene_hash
from ..render.export import ExportStore, media_type_for
from ..render.raster import DEFAULT_ICON_SIZES, RasterError, build_icon_bundle, render_png
from ..render.svg import render_svg

router = APIRouter(tags=["render"])
store = ExportStore()

Scene = Annotated[dict[str, Any], Field(description="IsoDSL v1 scene document")]


def _validated(scene: dict) -> dict:
    """Reject invalid scenes at the edge with actionable, structured errors."""
    result = validate(scene)
    if not result.valid:
        raise HTTPException(
            status_code=400,
            detail={"error": "scene failed IsoDSL validation", **result.to_dict()},
        )
    return scene


class SceneRequest(BaseModel):
    scene: Scene


class PngRequest(BaseModel):
    scene: Scene
    width: int | None = Field(default=None, ge=1, le=8192)
    height: int | None = Field(default=None, ge=1, le=8192)
    scale: float = Field(default=1.0, gt=0, le=16)


class IconBundleRequest(BaseModel):
    scene: Scene
    sizes: list[int] = Field(default_factory=lambda: list(DEFAULT_ICON_SIZES))
    padding: float = Field(default=0.08, ge=0, le=0.45)
    safe_zone: float = Field(default=0.04, ge=0, le=0.45)
    formats: list[Literal["png", "svg", "ico", "icns"]] = Field(
        default_factory=lambda: ["png", "svg", "ico"]
    )


class ExportSvgRequest(BaseModel):
    scene: Scene
    filename: str = "logo.svg"


class ExportPngRequest(PngRequest):
    filename: str = "logo.png"


class DiffRenderRequest(BaseModel):
    before: Scene
    after: Scene


@router.post("/render/svg", summary="Render IsoDSL scene to SVG")
def render_svg_route(req: SceneRequest) -> dict:
    scene = _validated(req.scene)
    return {"svg": render_svg(scene), "scene_hash": scene_hash(scene)}


@router.post("/render/png", summary="Render IsoDSL scene to PNG")
def render_png_route(req: PngRequest) -> Response:
    scene = _validated(req.scene)
    try:
        data = render_png(scene, width=req.width, height=req.height, scale=req.scale)
    except RasterError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    return Response(
        content=data,
        media_type="image/png",
        headers={"X-Scene-Hash": scene_hash(scene)},
    )


@router.post("/export/svg", summary="Export scene as a persisted SVG artifact")
def export_svg(req: ExportSvgRequest) -> dict:
    scene = _validated(req.scene)
    canvas = scene.get("canvas") or {}
    record = store.save(
        artifact_type="svg",
        filename=req.filename,
        data=render_svg(scene).encode("utf-8"),
        scene_hash=scene_hash(scene),
        width=int(canvas.get("width", 512)),
        height=int(canvas.get("height", 512)),
        transparent_background=canvas.get("background") is None,
    )
    return record.to_dict()


@router.post("/export/png", summary="Export scene as a persisted PNG artifact")
def export_png(req: ExportPngRequest) -> dict:
    scene = _validated(req.scene)
    try:
        data = render_png(scene, width=req.width, height=req.height, scale=req.scale)
    except RasterError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    canvas = scene.get("canvas") or {}
    record = store.save(
        artifact_type="png",
        filename=req.filename,
        data=data,
        scene_hash=scene_hash(scene),
        width=req.width or int(float(canvas.get("width", 512)) * req.scale),
        height=req.height or int(float(canvas.get("height", 512)) * req.scale),
        transparent_background=canvas.get("background") is None,
    )
    return record.to_dict()


@router.post("/export/icon-bundle", summary="Generate a full app-icon bundle")
def export_icon_bundle(req: IconBundleRequest) -> dict:
    scene = _validated(req.scene)
    try:
        bundle = build_icon_bundle(
            scene,
            sizes=req.sizes,
            padding=req.padding,
            safe_zone=req.safe_zone,
            formats=req.formats,
        )
    except RasterError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    record = store.save(
        artifact_type="icon-bundle",
        filename="icons.zip",
        data=bundle.to_zip(),
        scene_hash=bundle.scene_hash,
        metadata={"manifest": bundle.manifest, "sizes": sorted(set(req.sizes))},
    )
    return {"bundle_id": record.id, **record.to_dict()}


@router.post("/export/isoforge-json", summary="Export the canonical .isoforge.json")
def export_isoforge_json(req: ExportSvgRequest) -> dict:
    scene = _validated(req.scene)
    filename = req.filename if req.filename.endswith(".json") else "scene.isoforge.json"
    record = store.save(
        artifact_type="isoforge-json",
        filename=filename,
        data=canonical_json(scene).encode("utf-8"),
        scene_hash=scene_hash(scene),
    )
    return record.to_dict()


@router.post("/import/isoforge-json", summary="Validate and normalise an imported scene")
def import_isoforge_json(req: SceneRequest) -> dict:
    """Round-trip entry point for `isoforge chat --continue scene.isoforge.json`."""
    result = validate(req.scene)
    if not result.valid:
        raise HTTPException(
            status_code=400,
            detail={"error": "imported scene failed validation", **result.to_dict()},
        )
    return {
        "scene": req.scene,
        "scene_hash": scene_hash(req.scene),
        "canonical": canonical_json(req.scene),
    }


@router.post("/diff/render", summary="Render both sides of a version comparison")
def diff_render(req: DiffRenderRequest) -> dict:
    """Used by `isoforge diff` and the history explorer's side-by-side view."""
    before = _validated(req.before)
    after = _validated(req.after)
    return {
        "before": {"svg": render_svg(before), "scene_hash": scene_hash(before)},
        "after": {"svg": render_svg(after), "scene_hash": scene_hash(after)},
        "changed": scene_hash(before) != scene_hash(after),
    }


@router.get("/exports/{export_id}", summary="Export artifact metadata")
def get_export(export_id: str) -> dict:
    record = store.get(export_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "export not found"})
    return record.to_dict()


@router.get("/exports/{export_id}/download", summary="Download an export artifact")
def download_export(export_id: str) -> FileResponse:
    record = store.get(export_id)
    if record is None or not record.path.is_file():
        raise HTTPException(status_code=404, detail={"error": "export not found"})
    return FileResponse(
        record.path, media_type=media_type_for(record.path), filename=record.path.name
    )
