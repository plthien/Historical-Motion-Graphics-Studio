from __future__ import annotations

from pathlib import Path

import yaml

from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]
STATIC_OPENAPI = ROOT / "contracts" / "openapi" / "youtube_ai_studio_api.openapi.yaml"


def test_static_openapi_authoring_pacing_contracts_match_runtime() -> None:
    runtime = create_app().openapi()
    static = yaml.safe_load(STATIC_OPENAPI.read_text(encoding="utf-8"))

    assert static["info"]["version"] == runtime["info"]["version"]
    for name in ("GenerateScriptUserInput", "TopicIdeaModel", "NarrationIdentityModel", "ChannelDnaV3Model"):
        assert static["components"]["schemas"][name] == runtime["components"]["schemas"][name], name

    assert static["components"]["schemas"]["ExternalPromptDraftReadResponse"] == runtime["components"]["schemas"]["ExternalPromptDraftReadResponse"]
    for route in (
        "/api/v3/projects/{project_id}/topic-ideas/prompt",
        "/api/v3/projects/{project_id}/script-analysis/prompt",
        "/api/v4/projects/{project_id}/scene-breakdown/scene/prompt",
    ):
        static_get = dict(static["paths"][route]["get"])
        static_get.pop("x-implementation-status", None)
        assert static_get == runtime["paths"][route]["get"], route
