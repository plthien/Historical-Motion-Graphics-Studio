from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy import inspect
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.session import get_engine
from app.db.session import build_session_factory
from app.main import create_app
from app.modules.platform.contracts import PlatformContractValidator
from tests.support.fixtures import sample_channel_dna


def test_clean_database_contains_only_active_project_pointers() -> None:
    columns = {item["name"] for item in inspect(get_engine()).get_columns("projects")}
    assert "script_analysis_artifact_id" in columns
    assert "scene_plan_artifact_id" in columns
    assert "final_video_artifact_id" in columns
    assert "story_context_artifact_id" not in columns


def test_platform_and_v4_routes_are_the_only_active_pipeline_boundaries() -> None:
    paths = set(create_app().openapi()["paths"])
    assert "/api/v3/channels" in paths
    assert "/api/v3/projects" in paths
    assert "/api/v4/projects/{project_id}/script-analysis" in paths
    assert "/api/v4/projects/{project_id}/render" in paths
    forbidden = ("story-analyzer", "/api/v3/projects/{project_id}/scene-breakdown", "/api/v3/projects/{project_id}/render")
    assert not any(any(token in path for token in forbidden) for path in paths)


def test_platform_contract_registry_loads_active_authoring_schemas() -> None:
    validator = PlatformContractValidator()
    assert validator.load_schema("channel_dna.schema.json")["type"] == "object"
    assert validator.load_schema("script.schema.json")["type"] == "object"


def test_project_response_has_no_removed_pointer() -> None:
    spec = create_app().openapi()
    project_schema = spec["components"]["schemas"]["ProjectModel"]
    assert "story_context_artifact_id" not in project_schema.get("properties", {})


def test_channel_list_survives_corrupt_stored_channel_dna_and_dna_read_returns_contract_error() -> None:
    channel_id = generate_id("channel")
    now = utc_now_iso()
    context = {
        "name": "Corrupt DNA",
        "language": "English",
        "niche": "testing",
        "audience": "QA",
    }
    with build_session_factory(get_settings())() as session:
        session.execute(
            text(
                """
                insert into channels (
                    channel_id,
                    name,
                    language,
                    niche,
                    audience,
                    channel_dna_json,
                    channel_context_json,
                    created_at,
                    updated_at
                ) values (
                    :channel_id,
                    :name,
                    :language,
                    :niche,
                    :audience,
                    :channel_dna_json,
                    :channel_context_json,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "channel_id": channel_id,
                "name": context["name"],
                "language": context["language"],
                "niche": context["niche"],
                "audience": context["audience"],
                "channel_dna_json": '{"schema_version":"3.0","channel":',
                "channel_context_json": json.dumps(context, sort_keys=True),
                "created_at": now,
                "updated_at": now,
            },
        )
        session.commit()

    client = TestClient(create_app(), raise_server_exceptions=False)
    origin = {"Origin": "http://localhost:5173"}

    list_response = client.get("/api/v3/channels", headers=origin)
    assert list_response.status_code == 200
    assert list_response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    listed = [
        item
        for item in list_response.json()["items"]
        if item["channel_id"] == channel_id
    ][0]
    assert listed["has_channel_dna"] is True
    assert listed["channel_context"] == context

    card_response = client.get(f"/api/v3/channels/{channel_id}", headers=origin)
    assert card_response.status_code == 200
    assert card_response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert card_response.json()["channel_context"] == context

    dna_response = client.get(f"/api/v3/channels/{channel_id}/channel-dna", headers=origin)
    assert dna_response.status_code == 500
    assert dna_response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert dna_response.json()["error_code"] == "CHANNEL_DNA_STORED_INVALID"
    assert dna_response.json()["details"]["channel_id"] == channel_id

    repaired = sample_channel_dna()
    save_response = client.post(
        f"/api/v3/channels/{channel_id}/channel-dna",
        json={"channel_dna": repaired},
        headers=origin,
    )
    assert save_response.status_code == 200
    assert save_response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert save_response.json()["channel_context"] == repaired["channel"]

    repaired_response = client.get(f"/api/v3/channels/{channel_id}/channel-dna", headers=origin)
    assert repaired_response.status_code == 200
    assert repaired_response.json()["channel_dna"] == repaired


def test_runtime_openapi_requires_topic_script_handoff_and_section_mode() -> None:
    from app.main import app

    schemas = app.openapi()["components"]["schemas"]
    topic_required = set(schemas["TopicIdeaModel"]["required"])
    script_required = set(schemas["GenerateScriptUserInput"]["required"])
    assert {"pov_style", "avoid"}.issubset(topic_required)
    assert {"pov_style", "avoid", "section_count_mode", "narration_wpm"}.issubset(script_required)
    assert schemas["GenerateScriptUserInput"]["properties"]["section_count_mode"]["enum"] == [
        "llm_decides",
        "backend_plan",
    ]
    narration_wpm = schemas["GenerateScriptUserInput"]["properties"]["narration_wpm"]
    assert narration_wpm["$ref"].endswith("/NarrationWpmRangeModel")
    range_schema = schemas["NarrationWpmRangeModel"]
    assert range_schema["properties"]["min"]["minimum"] == 80
    assert range_schema["properties"]["max"]["maximum"] == 300
    assert "channel_dna_narration_wpm" in schemas["ProjectModel"]["required"]
