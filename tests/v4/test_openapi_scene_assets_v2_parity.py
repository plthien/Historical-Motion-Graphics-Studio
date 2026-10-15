from __future__ import annotations

from pathlib import Path

import yaml

from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]
STATIC_OPENAPI = ROOT / "contracts" / "openapi" / "youtube_ai_studio_api.openapi.yaml"

V2_SCHEMAS = (
    "SceneAssetV2Beat",
    "SceneAssetV2BeatOperationStatus",
    "SceneAssetV2BeatResponse",
    "SceneAssetV2CancelResponse",
    "SceneAssetV2Candidate",
    "SceneAssetV2Continuity",
    "SceneAssetV2ExecutionStatusResponse",
    "SceneAssetV2MutationResponse",
    "SceneAssetV2OperationStatusResponse",
    "SceneAssetV2OperationSubmissionResponse",
    "SceneAssetV2OperationSummary",
    "SceneAssetV2PublishResponse",
    "SceneAssetV2Scene",
    "SceneAssetV2SectionApprovalResponse",
    "SceneAssetV2TaskStatus",
    "SceneAssetV2WorkspaceActions",
    "SceneAssetV2WorkspaceResponse",
)


def test_static_openapi_scene_assets_v2_contracts_match_runtime() -> None:
    runtime = create_app().openapi()
    static = yaml.safe_load(STATIC_OPENAPI.read_text(encoding="utf-8"))

    assert static["info"]["version"] == runtime["info"]["version"]
    for name in V2_SCHEMAS:
        assert static["components"]["schemas"][name] == runtime["components"]["schemas"][name], name


def test_scene_assets_v2_read_routes_are_present_in_static_and_runtime_openapi() -> None:
    runtime_paths = create_app().openapi()["paths"]
    static_paths = yaml.safe_load(STATIC_OPENAPI.read_text(encoding="utf-8"))["paths"]
    required = (
        "/api/v4/projects/{project_id}/scene-assets",
        "/api/v4/projects/{project_id}/scene-assets/execution-status",
        "/api/v4/projects/{project_id}/scene-assets/jobs/{operation_id}",
    )
    for path in required:
        assert path in runtime_paths
        assert path in static_paths
        assert "get" in runtime_paths[path]
        assert "get" in static_paths[path]
