from __future__ import annotations

import inspect
from pathlib import Path

from app import main as app_main


ROOT = Path(__file__).resolve().parents[2]


def _matching_routes(path: str, method: str):
    app = app_main.create_app()
    return [
        route
        for route in app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    ]


def test_canonical_visual_reference_routes_are_owned_by_v2_before_legacy_v4() -> None:
    cases = (
        ("/api/v4/projects/{project_id}/visual-references", "GET"),
        ("/api/v4/projects/{project_id}/visual-references/jobs", "POST"),
        ("/api/v4/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/regenerate/jobs", "POST"),
        ("/api/v4/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/upload", "POST"),
        ("/api/v4/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/prompt", "PUT"),
        ("/api/v4/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/approve", "POST"),
        ("/api/v4/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/clear", "POST"),
    )
    for path, method in cases:
        routes = _matching_routes(path, method)
        assert routes, f"missing canonical route: {method} {path}"
        assert routes[0].endpoint.__module__ == "app.api.routes.visual_references_canonical_v2"


def test_visual_reference_generation_has_no_platform_rq_runtime() -> None:
    assert not (ROOT / "app/worker.py").exists()
    assert not (ROOT / "app/infrastructure/redis.py").exists()


def test_startup_runs_visual_reference_v2_recovery_before_generic_media_recovery() -> None:
    source = inspect.getsource(app_main.app_lifespan)
    v2_index = source.index("recover_visual_reference_v2_media_ownership")
    generic_index = source.index("recover_media_ownership")
    assert v2_index < generic_index


def test_style_bible_visual_reference_entrypoint_uses_v2_without_legacy_job_polling() -> None:
    source = (ROOT / "src/features/studio/StyleBibleVisualReferencesWorkspace.tsx").read_text(encoding="utf-8")
    assert "StyleBibleVisualReferencesWorkspaceV2" in source
    assert "visualReferencesV2Client.publish" in source
    assert "visualReferenceV2Keys.workspace" in source
    assert "getVisualReferenceJobV4" not in source
    assert "generateVisualReferencesV4" not in source
    assert "visual_reference_v4_generate" not in source
