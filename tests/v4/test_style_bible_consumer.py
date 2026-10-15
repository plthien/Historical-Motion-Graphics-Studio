from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactType
from app.db.models import Channel, Project
from app.db.session import build_session_factory
from app.main import create_app
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_analysis_input_builder import build_script_analysis_input
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.modules.platform.services import build_script_section_ranges
from app.modules.v4.style_bible_draft_builder import StyleBibleDraftBuilderV4, channel_dna_snapshot_sha256
from app.modules.v4.style_bible_input_builder import StyleBibleInputBuilderV4
from app.modules.v4.style_bible_service import StyleBibleServiceV4
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService
from tests.support.fixtures import sample_script_artifact
from tests.support.fixtures import sample_channel_dna

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_FIXTURE = ROOT / "tests" / "fixtures" / "v4" / "script_analysis" / "valid_script_analysis.json"


def empty_analysis_output(project_id: str, script_id: str, sections: list[dict[str, Any]]) -> dict:
    return {
        "artifact_type": "script_analysis_llm_output",
        "schema_version": "1.0",
        "project_id": project_id,
        "source_script_revision_id": script_id,
        "section_guidance": [
            {"section_id": section_id, "pacing_density": "medium"}
            for section_id in [item["section_id"] for item in sections]
        ],
        "narrative_markers": [],
        "continuity_catalog": {
            "characters": [],
            "locations": [],
            "recurring_props": [],
            "recurring_visual_subjects": [],
            "visual_motifs": [],
        },
        "visual_context_catalog": [
            {
                "label": "Complete authored Script",
                "mode": "story_world",
                "timeframe": None,
                "setting": None,
                "constraints": [],
                "presence_ranges": [
                    {
                        "section_id": item["section_id"],
                        "start_quote": item["narration"],
                        "end_quote": item["narration"],
                    }
                    for item in sections
                ],
            }
        ],
    }


def seed_project() -> tuple[str, str, str]:
    factory = build_session_factory(get_settings())
    with factory() as session:
        now = utc_now_iso()
        channel = Channel(
            channel_id=generate_id("channel"),
            name="Style Bible V4",
            language="English",
            niche="education",
            audience="curious viewers",
            channel_dna_json=sample_channel_dna(),
            channel_context_json={"name": "Style Bible V4", "language": "English", "niche": "education", "audience": "curious viewers"},
            created_at=now,
            updated_at=now,
        )
        session.add(channel)
        project = Project(
            project_id=generate_id("project"),
            channel_id=channel.channel_id,
            name="Style Bible V4 Project",
            description=None,
            channel_context_json=channel.channel_context_json,
            channel_dna_json=deepcopy(channel.channel_dna_json),
            created_at=now,
            updated_at=now,
        )
        session.add(project)
        session.flush()
        artifacts = ArtifactService(session, ArtifactRepository(session), get_settings())
        script = sample_script_artifact()
        script_staged = artifacts.write_artifact(
            logical_id=f"project/{project.project_id}/script",
            artifact_type=ArtifactType.JSON,
            artifact_role=ArtifactRole.SCRIPT,
            filename="script.json",
            content=json.dumps(script, ensure_ascii=False, indent=2).encode("utf-8"),
            provider_source="test",
        )
        script_id = script_staged.artifact.artifact_id
        project.script_artifact_id = script_id
        raw = empty_analysis_output(project.project_id, script_id, script["script_sections"])
        llm_input = build_script_analysis_input(
            project_id=project.project_id,
            script_revision_id=script_id,
            script_artifact=script,
        )
        canonical = ScriptAnalysisSemanticValidator().build_canonical(
            llm_input=llm_input,
            raw_output=raw,
            section_ranges=build_script_section_ranges(script["script_sections"]),
        )
        analysis_staged = artifacts.write_artifact(
            logical_id=f"project/{project.project_id}/script_analysis",
            artifact_type=ArtifactType.JSON,
            artifact_role=None,
            filename="script_analysis.json",
            content=json.dumps(canonical, ensure_ascii=False, indent=2).encode("utf-8"),
            provider_source="test",
        )
        analysis_id = analysis_staged.artifact.artifact_id
        project.script_analysis_artifact_id = analysis_id
        session.commit()
        return project.project_id, script_id, analysis_id


def test_style_bible_input_projects_only_continuity_catalog() -> None:
    analysis = json.loads(ANALYSIS_FIXTURE.read_text(encoding="utf-8"))
    payload = StyleBibleInputBuilderV4().build(
        project_id=analysis["project_id"],
        script_analysis_artifact_id="artifact_aaaaaaaaaaaa",
        script_analysis_payload=analysis,
    )
    assert set(payload) == {"schema_version", "project_id", "script_analysis_artifact_id", "source_script_revision_id", "continuity_catalog"}
    assert "section_guidance" not in payload
    assert "narrative_markers" not in payload
    assert payload["continuity_catalog"] == analysis["continuity_catalog"]


def test_draft_builder_adds_script_analysis_lineage_and_recurring_subjects() -> None:
    analysis = json.loads(ANALYSIS_FIXTURE.read_text(encoding="utf-8"))
    style_input = StyleBibleInputBuilderV4().build(
        project_id=analysis["project_id"],
        script_analysis_artifact_id="artifact_aaaaaaaaaaaa",
        script_analysis_payload=analysis,
    )
    channel = sample_channel_dna()
    artifact = StyleBibleDraftBuilderV4().build(
        project_id=analysis["project_id"],
        style_bible_revision_id="artifact_bbbbbbbbbbbb",
        style_bible_input=style_input,
        channel_dna_snapshot=channel,
        channel_dna_snapshot_sha256_value=channel_dna_snapshot_sha256(channel),
    )
    assert artifact["schema_version"] == "2.0"
    assert artifact["lineage"]["script_analysis_artifact_id"] == "artifact_aaaaaaaaaaaa"
    assert "story_context_artifact_id" not in artifact["lineage"]
    assert artifact["recurring_visual_subjects"][0]["subject_id"] == "subject_001"
    assert artifact["recurring_visual_subjects"][0]["occurrence_quotes"][0]["context"].startswith("Imagine waking")


def test_contracts_validate_strict_style_bible_v4() -> None:
    validator = V4ContractValidator()
    for name in (
        "style_bible_input_v1.schema.json",
        "style_bible_build_request_v1.schema.json",
        "project_style_bible_v1.schema.json",
        "style_bible_build_response_v1.schema.json",
        "style_bible_read_response_v1.schema.json",
        "style_bible_text_update_request_v1.schema.json",
        "style_bible_text_approve_request_v1.schema.json",
    ):
        assert validator.load_schema(name)["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_service_generates_style_bible_from_current_script_analysis() -> None:
    project_id, script_id, analysis_id = seed_project()
    factory = build_session_factory(get_settings())
    with factory() as session:
        artifact, artifact_id = StyleBibleServiceV4(session).generate(project_id, analysis_id)
        assert artifact["lineage"] == {
            "script_analysis_artifact_id": analysis_id,
            "source_script_artifact_id": script_id,
            "channel_dna_snapshot_sha256": channel_dna_snapshot_sha256(sample_channel_dna()),
            "continuity_review_artifact_id": None,
            "removed_continuity_refs": [],
        }
        assert artifact["style_bible_revision_id"] == artifact_id
    with factory() as session:
        response = StyleBibleServiceV4(session).get(project_id)
        assert response["generated"] is True
        assert response["artifact_id"] == artifact_id


def test_v4_style_bible_api_generate_and_read() -> None:
    project_id, _, analysis_id = seed_project()
    client = TestClient(create_app())
    generated = client.post(
        f"/api/v4/projects/{project_id}/style-bible/generate",
        json={"script_analysis_artifact_id": analysis_id},
    )
    assert generated.status_code == 200, generated.text
    artifact_id = generated.json()["artifact_id"]
    read = client.get(f"/api/v4/projects/{project_id}/style-bible")
    assert read.status_code == 200, read.text
    assert read.json()["artifact_id"] == artifact_id


def test_static_openapi_and_frontend_client_expose_style_bible_v4() -> None:
    import yaml

    spec = yaml.safe_load((ROOT / "contracts" / "openapi" / "youtube_ai_studio_api.openapi.yaml").read_text(encoding="utf-8"))
    runtime_spec = create_app().openapi()
    for path, method in (
        ("/api/v4/projects/{project_id}/style-bible/generate", "post"),
        ("/api/v4/projects/{project_id}/style-bible", "get"),
    ):
        assert path in spec["paths"]
        assert method in spec["paths"][path]
        assert spec["paths"][path][method]["operationId"] == runtime_spec["paths"][path][method]["operationId"]
        assert spec["paths"][path][method]["responses"] == runtime_spec["paths"][path][method]["responses"]
    client_source = (ROOT / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "generateStyleBibleDraftV4" in client_source
    assert "script_analysis_artifact_id" in client_source
    assert "recurring_visual_subjects" in client_source


def test_visual_reference_inventory_is_owned_by_native_v4_runtime() -> None:
    source = (ROOT / "app" / "modules" / "v4" / "visual_reference_prompt_compiler.py").read_text(encoding="utf-8")
    assert "recurring_visual_subjects" in source
    assert not (ROOT / "app" / "modules" / "v3").exists()


def _editable_content_from_v4_artifact(artifact: dict) -> dict:
    fields = {
        "characters": ("char_id", "label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints", "entity_kind", "narrative_role"),
        "locations": ("loc_id", "label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints", "location_kind"),
        "recurring_props": ("prop_id", "label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints", "prop_kind"),
        "recurring_visual_subjects": ("subject_id", "label", "description", "visual_type", "explicit_visual_traits", "continuity_notes", "physical_constraints"),
        "motifs": ("motif_id", "label", "description"),
    }
    return {
        collection: [
            {field: deepcopy(item[field]) for field in selected_fields}
            for item in artifact[collection]
        ]
        for collection, selected_fields in fields.items()
    }


def test_text_update_preserves_script_analysis_owned_evidence() -> None:
    from app.modules.v4.style_bible_text_revision import apply_text_update
    from app.modules.v4.style_bible_validator import StyleBibleSemanticValidatorV4

    analysis = json.loads(ANALYSIS_FIXTURE.read_text(encoding="utf-8"))
    channel = sample_channel_dna()
    style_input = StyleBibleInputBuilderV4().build(
        project_id=analysis["project_id"],
        script_analysis_artifact_id="artifact_aaaaaaaaaaaa",
        script_analysis_payload=analysis,
    )
    current = StyleBibleDraftBuilderV4().build(
        project_id=analysis["project_id"],
        style_bible_revision_id="artifact_bbbbbbbbbbbb",
        style_bible_input=style_input,
        channel_dna_snapshot=channel,
        channel_dna_snapshot_sha256_value=channel_dna_snapshot_sha256(channel),
    )
    editable = _editable_content_from_v4_artifact(current)
    editable["recurring_visual_subjects"][0]["description"] = "Updated visual identity guidance."
    revised = apply_text_update(current, editable, "artifact_cccccccccccc")

    assert revised["recurring_visual_subjects"][0]["description"] == "Updated visual identity guidance."
    assert revised["recurring_visual_subjects"][0]["occurrence_quotes"] == current["recurring_visual_subjects"][0]["occurrence_quotes"]
    assert revised["recurring_visual_subjects"][0]["presence_ranges"] == current["recurring_visual_subjects"][0]["presence_ranges"]
    assert revised["lineage"] == current["lineage"]
    StyleBibleSemanticValidatorV4().validate(
        style_bible=revised,
        script_analysis=analysis,
        channel_dna=channel,
    )



def test_style_bible_semantic_validator_rejects_presence_range_tampering() -> None:
    from app.api.errors import AppError
    from app.modules.v4.style_bible_validator import StyleBibleSemanticValidatorV4

    analysis = json.loads(ANALYSIS_FIXTURE.read_text(encoding="utf-8"))
    channel = sample_channel_dna()
    style_input = StyleBibleInputBuilderV4().build(
        project_id=analysis["project_id"],
        script_analysis_artifact_id="artifact_aaaaaaaaaaaa",
        script_analysis_payload=analysis,
    )
    current = StyleBibleDraftBuilderV4().build(
        project_id=analysis["project_id"],
        style_bible_revision_id="artifact_bbbbbbbbbbbb",
        style_bible_input=style_input,
        channel_dna_snapshot=channel,
        channel_dna_snapshot_sha256_value=channel_dna_snapshot_sha256(channel),
    )
    tampered = deepcopy(current)
    target = next(
        item for collection in ("characters", "locations", "recurring_props", "recurring_visual_subjects")
        for item in tampered[collection]
        if item.get("presence_ranges")
    )
    target["presence_ranges"][0]["resolved_script_span"][1] += 1

    with pytest.raises(AppError) as exc_info:
        StyleBibleSemanticValidatorV4().validate(
            style_bible=tampered,
            script_analysis=analysis,
            channel_dna=channel,
        )
    assert exc_info.value.error_code == "STYLE_BIBLE_ARTIFACT_INVALID"
    assert str(exc_info.value.details.get("cause", "")).endswith("_presence_range_mismatch")

def test_generate_runs_semantic_validation_before_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.errors import AppError

    project_id, _, analysis_id = seed_project()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = StyleBibleServiceV4(session)

        def reject(**_: object) -> None:
            raise AppError("STYLE_BIBLE_ARTIFACT_INVALID", "forced semantic failure", 409, retryable=False)

        monkeypatch.setattr(service.style_loader.semantic, "validate", reject)
        with pytest.raises(AppError) as exc_info:
            service.generate(project_id, analysis_id)
        assert exc_info.value.error_code == "STYLE_BIBLE_ARTIFACT_INVALID"

    with factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        assert project.style_bible_artifact_id is None


def test_style_bible_loader_rejects_tampered_artifact_bytes() -> None:
    from app.api.errors import AppError
    from app.db.models import Artifact

    project_id, _, analysis_id = seed_project()
    factory = build_session_factory(get_settings())
    with factory() as session:
        _, artifact_id = StyleBibleServiceV4(session).generate(project_id, analysis_id)
        row = session.get(Artifact, artifact_id)
        assert row is not None
        artifact_path = get_settings().resolve_configured_path(get_settings().artifact_root) / row.path
        artifact_path.write_text("{}", encoding="utf-8")

    with factory() as session:
        with pytest.raises(AppError) as exc_info:
            StyleBibleServiceV4(session).get(project_id)
        assert exc_info.value.error_code == "STYLE_BIBLE_ARTIFACT_CORRUPT"


def test_platform_loader_has_no_style_bible_compatibility_reader() -> None:
    source = (ROOT / "app" / "modules" / "platform" / "artifact_loader.py").read_text(encoding="utf-8")
    assert "load_style_bible" not in source
    assert "schema_version" not in source or "1.1" not in source


def test_character_entity_kind_taxonomy_is_locked_across_v4_contracts() -> None:
    validator = V4ContractValidator()
    raw = validator.load_schema("script_analysis_llm_output_v1.schema.json")
    canonical = validator.load_schema("script_analysis_v1.schema.json")
    style = validator.load_schema("project_style_bible_v1.schema.json")
    update = validator.load_schema("style_bible_text_update_request_v1.schema.json")

    assert raw["$defs"]["character_raw"]["properties"]["entity_kind"]["enum"] == ["human_character", "human_group"]
    assert canonical["$defs"]["character"]["properties"]["entity_kind"]["enum"] == ["human_character", "human_group"]
    assert style["$defs"]["character"]["properties"]["entity_kind"]["enum"] == ["human_character", "human_group"]
    assert update["properties"]["editable_content"]["properties"]["characters"]["items"]["properties"]["entity_kind"]["enum"] == ["human_character", "human_group"]
