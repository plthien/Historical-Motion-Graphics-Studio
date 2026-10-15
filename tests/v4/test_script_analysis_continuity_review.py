from __future__ import annotations

import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.session import build_session_factory
from app.modules.platform.external_script_analysis import ExternalScriptAnalysisService
from app.modules.platform.script_analysis_continuity_review import ScriptAnalysisContinuityReviewService
from app.modules.v4.script_analysis_service import ScriptAnalysisService
from app.modules.v4.style_bible_service import StyleBibleServiceV4
from tests.support.external_authoring import (
    combined_script_analysis_output,
    create_channel_project,
    external_import_payload,
    import_and_select_topic,
)


def _request() -> dict:
    return {
        "user_input": {
            "target_duration_minutes": 1,
            "pov_style": "second person",
            "avoid": [],
            "section_count_mode": "llm_decides",
            "pacing_mode": "medium",
            "narration_wpm": {"min": 145, "max": 165},
        },
        "selected_topic_idea_id": "idea_001",
    }


def _occurrences() -> list[dict]:
    return [
        {
            "section_id": "sec_001",
            "quote": "You turn off the light",
            "context_quote": "You turn off the light, the room goes soft and quiet.",
        },
        {
            "section_id": "sec_002",
            "quote": "rest was never just rest",
            "context_quote": "But for most of human life, rest was never just rest.",
        },
    ]


def _ranges() -> list[dict]:
    return [
        {
            "section_id": "sec_001",
            "start_quote": "You turn off the light, the room goes soft and quiet.",
            "end_quote": "You turn off the light, the room goes soft and quiet.",
        },
        {
            "section_id": "sec_002",
            "start_quote": "But for most of human life, rest was never just rest.",
            "end_quote": "But for most of human life, rest was never just rest.",
        },
    ]


def _output_with_continuity() -> dict:
    output = combined_script_analysis_output()
    catalog = output["analysis"]["continuity_catalog"]
    catalog["characters"] = [
        {
            "label": "Recurring watcher",
            "description": "A recurring person whose identity can be locked or released.",
            "explicit_visual_traits": ["simple silhouette"],
            "identity_markers": ["same person"],
            "continuity_notes": ["keep recognizable when active"],
            "physical_constraints": [],
            "entity_kind": "human_character",
            "narrative_role": "continuity test",
            "occurrences": _occurrences(),
            "presence_ranges": _ranges(),
        }
    ]
    catalog["recurring_props"] = [
        {
            "label": "Recurring blanket",
            "description": "A prop used to test optional identity locking.",
            "explicit_visual_traits": ["plain blanket"],
            "identity_markers": ["same blanket"],
            "continuity_notes": ["only lock when retained"],
            "physical_constraints": [],
            "prop_kind": "household_object",
            "occurrences": _occurrences(),
            "presence_ranges": _ranges(),
        }
    ]
    catalog["recurring_visual_subjects"] = [
        {
            "label": "Recurring darkness",
            "description": "A recurring visual subject used across sections.",
            "visual_type": "environmental_state",
            "explicit_visual_traits": ["soft dark field"],
            "continuity_notes": ["same visual subject when retained"],
            "physical_constraints": [],
            "occurrences": _occurrences(),
            "presence_ranges": _ranges(),
        }
    ]
    return output


def _import(session):
    _channel_id, project_id = create_channel_project(session)
    import_and_select_topic(session, project_id)
    service = ExternalScriptAnalysisService(session)
    package = service.build_prompt(project_id, _request())
    imported = service.import_result(project_id, external_import_payload(package, _output_with_continuity()))
    return project_id, imported


def test_remove_filters_style_bible_without_mutating_script_analysis_and_restore_invalidates_downstream() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, imported = _import(session)
        analysis = imported["script_analysis"]
        assert [item["prop_id"] for item in analysis["continuity_catalog"]["recurring_props"]] == ["prop_001"]

        reviews = ScriptAnalysisContinuityReviewService(session)
        initial = reviews.get(project_id)
        assert initial["review_artifact_id"] is None
        assert initial["removed_continuity_refs"] == []
        assert {"char_001", "prop_001", "subject_001"}.issubset(set(initial["available_continuity_refs"]))

        removed = reviews.mutate(
            project_id,
            {
                "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                "expected_review_artifact_id": None,
                "continuity_ref": "prop_001",
                "removed": True,
            },
        )
        assert removed["removed_continuity_refs"] == ["prop_001"]

        # The imported canonical Analysis remains immutable and complete.
        current_analysis = ExternalScriptAnalysisService(session).projects.get(project_id)
        assert current_analysis is not None
        read_analysis = ScriptAnalysisService(session).get(project_id)["artifact"]
        assert [item["prop_id"] for item in read_analysis["continuity_catalog"]["recurring_props"]] == ["prop_001"]

        style, style_id = StyleBibleServiceV4(session).generate(project_id, imported["script_analysis_artifact_id"])
        assert [item["char_id"] for item in style["characters"]] == ["char_001"]
        assert style["recurring_props"] == []
        assert [item["subject_id"] for item in style["recurring_visual_subjects"]] == ["subject_001"]
        assert style["lineage"]["continuity_review_artifact_id"] == removed["review_artifact_id"]
        assert style["lineage"]["removed_continuity_refs"] == ["prop_001"]

        project = reviews.projects.get(project_id)
        assert project is not None
        # Simulate later derived pointers; review changes must invalidate them.
        project.scene_plan_artifact_id = imported["script_artifact_id"]
        project.visual_plan_artifact_id = imported["script_analysis_artifact_id"]
        session.commit()

        restored = reviews.mutate(
            project_id,
            {
                "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                "expected_review_artifact_id": removed["review_artifact_id"],
                "continuity_ref": "prop_001",
                "removed": False,
            },
        )
        assert restored["removed_continuity_refs"] == []
        project = reviews.projects.get(project_id)
        assert project is not None
        assert project.style_bible_artifact_id is None
        assert project.scene_plan_artifact_id is None
        assert project.visual_plan_artifact_id is None

        rebuilt, rebuilt_id = StyleBibleServiceV4(session).generate(project_id, imported["script_analysis_artifact_id"])
        assert rebuilt_id != style_id
        assert [item["prop_id"] for item in rebuilt["recurring_props"]] == ["prop_001"]


def test_review_rejects_unsupported_or_stale_ref_and_stale_revision() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, imported = _import(session)
        reviews = ScriptAnalysisContinuityReviewService(session)
        with pytest.raises(AppError) as unsupported:
            reviews.mutate(
                project_id,
                {
                    "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                    "expected_review_artifact_id": None,
                    "continuity_ref": "prop_999",
                    "removed": True,
                },
            )
        assert unsupported.value.error_code == "CONTINUITY_REVIEW_REF_UNSUPPORTED"

        first = reviews.mutate(
            project_id,
            {
                "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                "expected_review_artifact_id": None,
                "continuity_ref": "char_001",
                "removed": True,
            },
        )
        with pytest.raises(AppError) as stale:
            reviews.mutate(
                project_id,
                {
                    "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                    "expected_review_artifact_id": None,
                    "continuity_ref": "subject_001",
                    "removed": True,
                },
            )
        assert stale.value.error_code == "CONTINUITY_REVIEW_REVISION_CONFLICT"
        assert first["review_artifact_id"] is not None


def test_stale_review_from_previous_script_analysis_is_ignored_and_can_be_superseded() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, imported = _import(session)
        reviews = ScriptAnalysisContinuityReviewService(session)
        first_review = reviews.mutate(
            project_id,
            {
                "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                "expected_review_artifact_id": None,
                "continuity_ref": "char_001",
                "removed": True,
            },
        )

        service = ExternalScriptAnalysisService(session)
        next_package = service.build_prompt(project_id, _request())
        next_imported = service.import_result(
            project_id,
            external_import_payload(next_package, _output_with_continuity()),
        )
        assert next_imported["script_analysis_artifact_id"] != imported["script_analysis_artifact_id"]

        # Review state is scoped to the exact Script + Analysis lineage.
        current = reviews.get(project_id)
        assert current["review_artifact_id"] is None
        assert current["removed_continuity_refs"] == []

        second_review = reviews.mutate(
            project_id,
            {
                "expected_script_analysis_artifact_id": next_imported["script_analysis_artifact_id"],
                "expected_review_artifact_id": None,
                "continuity_ref": "prop_001",
                "removed": True,
            },
        )
        assert second_review["review_artifact_id"] != first_review["review_artifact_id"]
        assert second_review["removed_continuity_refs"] == ["prop_001"]


def test_continuity_review_http_contract_remove_and_restore() -> None:
    from fastapi.testclient import TestClient
    from app.main import create_app

    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, imported = _import(session)

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        initial = client.get(f"/api/v4/projects/{project_id}/script-analysis/continuity-review")
        assert initial.status_code == 200
        initial_body = initial.json()
        assert initial_body["script_analysis_artifact_id"] == imported["script_analysis_artifact_id"]
        assert initial_body["review_artifact_id"] is None
        assert "char_001" in initial_body["available_continuity_refs"]

        removed = client.patch(
            f"/api/v4/projects/{project_id}/script-analysis/continuity-review",
            json={
                "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                "expected_review_artifact_id": None,
                "continuity_ref": "char_001",
                "removed": True,
            },
        )
        assert removed.status_code == 200
        removed_body = removed.json()
        assert removed_body["removed_continuity_refs"] == ["char_001"]
        assert removed_body["review_artifact_id"] is not None

        restored = client.patch(
            f"/api/v4/projects/{project_id}/script-analysis/continuity-review",
            json={
                "expected_script_analysis_artifact_id": imported["script_analysis_artifact_id"],
                "expected_review_artifact_id": removed_body["review_artifact_id"],
                "continuity_ref": "char_001",
                "removed": False,
            },
        )
        assert restored.status_code == 200
        assert restored.json()["removed_continuity_refs"] == []
