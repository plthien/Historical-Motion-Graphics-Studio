from __future__ import annotations

from pathlib import Path

import yaml

from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


TWO_STEP_SCENE_METHODS = {
    "/api/v4/projects/{project_id}/scene-breakdown/scene/prompt": {"get", "post"},
    "/api/v4/projects/{project_id}/scene-breakdown/scene/import/validate": {"post"},
    "/api/v4/projects/{project_id}/scene-breakdown/scene/import": {"post"},
    "/api/v4/projects/{project_id}/scene-breakdown/visual/prompt": {"get", "post"},
    "/api/v4/projects/{project_id}/scene-breakdown/visual/import/validate": {"post"},
    "/api/v4/projects/{project_id}/scene-breakdown/visual/import": {"post"},
}
LEGACY_COMBINED_SCENE_PATHS = (
    "/api/v4/projects/{project_id}/scene-breakdown/prompt",
    "/api/v4/projects/{project_id}/scene-breakdown/import",
    "/api/v4/projects/{project_id}/scene-breakdown/import/validate",
)


def test_active_api_has_external_authoring_and_no_internal_llm_generation_routes() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v3/projects/{project_id}/topic-ideas/prompt" in paths
    assert "/api/v3/projects/{project_id}/script-analysis/prompt" in paths
    assert "/api/v4/projects/{project_id}/scene-breakdown/scene/prompt" in paths
    assert "/api/v4/projects/{project_id}/scene-breakdown/visual/prompt" in paths

    for prompt_path in (
        "/api/v3/projects/{project_id}/topic-ideas/prompt",
        "/api/v3/projects/{project_id}/script-analysis/prompt",
        "/api/v4/projects/{project_id}/scene-breakdown/scene/prompt",
    ):
        assert "get" in paths[prompt_path]
        assert "post" in paths[prompt_path]
        schema = paths[prompt_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/ExternalPromptDraftReadResponse")

    visual_prompt_path = "/api/v4/projects/{project_id}/scene-breakdown/visual/prompt"
    assert set(paths[visual_prompt_path]) >= {"get", "post"}
    visual_schema = paths[visual_prompt_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert visual_schema["$ref"].endswith("/ExternalPromptDraftReadResponse")

    for path in LEGACY_COMBINED_SCENE_PATHS:
        assert path not in paths

    forbidden = (
        "/api/v3/projects/{project_id}/topic-ideas/generate",
        "/api/v3/projects/{project_id}/scripts/generate",
        "/api/v3/projects/{project_id}/scripts/research-generate",
        "/api/v4/projects/{project_id}/script-analysis/generate",
        "/api/v4/projects/{project_id}/scene-breakdown/generate",
    )
    for path in forbidden:
        assert path not in paths
    assert not any(path.startswith("/api/logs") for path in paths)
    assert not any(path.startswith("/api/settings/llm") for path in paths)


def test_static_openapi_matches_runtime_two_step_scene_authoring_surface() -> None:
    runtime_paths = create_app().openapi()["paths"]
    static_document = yaml.safe_load(
        (ROOT / "contracts" / "openapi" / "youtube_ai_studio_api.openapi.yaml").read_text(encoding="utf-8")
    )
    static_paths = static_document["paths"]

    for path, methods in TWO_STEP_SCENE_METHODS.items():
        assert path in runtime_paths
        assert path in static_paths
        assert methods <= set(runtime_paths[path])
        assert methods <= set(static_paths[path])
        for method in methods:
            assert static_paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"] == (
                runtime_paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
            )

    for path in LEGACY_COMBINED_SCENE_PATHS:
        assert path not in runtime_paths
        assert path not in static_paths


def test_internal_llm_and_research_runtime_are_absent() -> None:
    forbidden_paths = [
        "app/providers/llm",
        "app/modules/platform/research",
        "app/services/llm_gateway.py",
        "app/services/llm_structured_output.py",
        "app/services/llm_settings.py",
        "app/services/llm_diagnostics.py",
        "app/api/routes/logs.py",
        "app/platform/llm",
        "app/modules/v4/script_analysis_transport.py",
        "contracts/registries/llm_provider_registry.yaml",
        "contracts/schemas/settings/provider_settings.schema.json",
        "tools/web_research_sources",
    ]
    for relative in forbidden_paths:
        assert not (ROOT / relative).exists(), relative


def test_platform_rq_runtime_is_absent_and_durable_runners_remain() -> None:
    for relative in (
        "app/worker.py",
        "app/infrastructure/redis.py",
        "app/modules/v4/render_worker.py",
        "app/modules/v4/render_recovery.py",
        "scripts/windows/start_worker.bat",
    ):
        assert not (ROOT / relative).exists(), relative

    scene_runner = (ROOT / "app" / "scene_asset_coordinator_runner.py").read_text(encoding="utf-8")
    assert "SceneAssetCoordinatorRunner" in scene_runner
    assert "claim_due_operation" in scene_runner
    assert "rq" not in scene_runner.lower()
    assert "redis" not in scene_runner.lower()

    visual_reference_runner = (ROOT / "app" / "visual_reference_coordinator_runner.py").read_text(encoding="utf-8")
    assert "VisualReferenceCoordinatorRunner" in visual_reference_runner
    assert "claim_due_operation" in visual_reference_runner
    assert "rq" not in visual_reference_runner.lower()
    assert "redis" not in visual_reference_runner.lower()
