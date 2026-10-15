from __future__ import annotations

from copy import deepcopy

import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.session import build_session_factory
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.scene_breakdown_two_step_external_service import ExternalSceneBreakdownTwoStepService
from tests.support.external_authoring import external_import_payload
from tests.v4.combined_scene_breakdown_support import approved_seed, full_external_output


def test_scene_breakdown_prompt_is_semantic_first_and_web_search_is_bounded() -> None:
    from pathlib import Path

    prompt = Path("contracts/prompts/v4/scene_breakdown_full/system.md").read_text(encoding="utf-8")
    assert "Web research may be used only to verify or clarify subject-matter facts or visual facts" in prompt
    assert "Research must not be used to discover or reconstruct" in prompt
    assert "smallest consecutive narration span" in prompt
    assert "two or more independently drawable state changes" in prompt
    assert "Beat count and scene count are semantic outcomes" in prompt
    assert "Do not create or delete Beats to satisfy sentence count, word count, duration" in prompt
    assert "The rescan is mandatory when materially indicated; a structural change is not." in prompt
    assert "A Beat may legitimately remain shorter or longer than the preferred hold guidance" in prompt
    assert "There is no cadence-compliance quota" in prompt
    assert "absolute positions on the approved Voice timeline" in prompt
    assert "Do not sum individual clause durations" in prompt
    assert "Lack of a supplied safe timing mapping is never a reason to move, split, merge, or delete" in prompt
    assert "pacing-conformance" not in prompt
    assert "# Previous context" not in prompt
    assert "previous_scene_purpose" not in prompt
    assert "previous_visual_tail" not in prompt
    assert "target_beat_count_for_section" not in prompt
    assert "beat_word_policy" not in prompt


def test_full_scene_prompt_and_import_publish_paired_artifacts() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        assert package["prompt_version"] == "scene_breakdown_full_external_v11"
        assert "every input Script section" in package["prompt_text"]
        payload = external_import_payload(package, full_external_output())
        validation = service.validate_import(project_id, payload)
        assert validation["valid"] is True
        result = service.import_result(project_id, payload)
        assert result["scene_plan_artifact_id"].startswith("artifact_")
        assert result["visual_plan_artifact_id"].startswith("artifact_")
        assert result["visual_plan"]["source_scene_plan_revision_id"] == result["scene_plan_artifact_id"]
        current = SceneBreakdownServiceV4(session).get_current_scene_plan(project_id)
        assert current["generated"] is True
        assert current["visual_plan"]["source_scene_plan_revision_id"] == current["scene_plan_artifact_id"]


def test_full_scene_import_rejects_missing_or_reordered_sections_atomically() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        output = full_external_output()
        output["sections"] = list(reversed(output["sections"]))
        with pytest.raises(AppError) as exc_info:
            service.import_result(project_id, external_import_payload(package, output))
        assert exc_info.value.error_code == "SCENE_BREAKDOWN_SECTION_COVERAGE_INVALID"
        current = SceneBreakdownServiceV4(session).get_current_scene_plan(project_id)
        assert current["generated"] is False


def test_full_scene_import_rejects_one_invalid_section_without_partial_publication() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        output = full_external_output()
        invalid = deepcopy(output)
        invalid["sections"][1]["scenes"][0]["beats"][0]["script_text"] = "invented narration"
        with pytest.raises(AppError):
            service.import_result(project_id, external_import_payload(package, invalid))
        current = SceneBreakdownServiceV4(session).get_current_scene_plan(project_id)
        assert current["generated"] is False


def test_full_scene_json_can_be_uploaded_after_reload_using_reserved_pair() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        package = ExternalSceneBreakdownService(session).build_prompt(project_id)
        payload = external_import_payload(package, full_external_output())

    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        assert service.validate_import(project_id, payload)["valid"] is True
        result = service.import_result(project_id, payload)
        assert result["scene_plan_artifact_id"] == package["artifact_ids"]["scene_plan"]
        assert result["visual_plan_artifact_id"] == package["artifact_ids"]["visual_plan"]


def test_full_scene_import_rejects_reservation_missing_current_audit_metadata() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        reservation = service.reservations.pending_for(project_id=project_id, stage="scene_breakdown")
        assert reservation is not None
        source = dict(reservation.source_json)
        for key in ("prompt_version", "prompt_sha256", "raw_schema_id", "raw_schema_sha256"):
            source.pop(key, None)
        reservation.source_json = source
        session.commit()
        payload = external_import_payload(package, full_external_output())

    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        with pytest.raises(AppError) as validate_exc:
            service.validate_import(project_id, payload)
        assert validate_exc.value.error_code == "SCENE_BREAKDOWN_RESERVATION_AUDIT_INVALID"
        with pytest.raises(AppError) as import_exc:
            service.import_result(project_id, payload)
        assert import_exc.value.error_code == "SCENE_BREAKDOWN_RESERVATION_AUDIT_INVALID"


def test_full_scene_validate_dry_runs_canonical_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.v4 import scene_breakdown_external_service as module
    from app.modules.v4.visual_plan_assembler import VisualPlanProjectionError

    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        payload = external_import_payload(package, full_external_output())
        monkeypatch.setattr(
            module,
            "assemble_visual_plan_from_scene_breakdown",
            lambda **_kwargs: (_ for _ in ()).throw(
                VisualPlanProjectionError("VISUAL_PLAN_TEST_FAILURE", "projection failed")
            ),
        )
        with pytest.raises(AppError) as exc_info:
            service.validate_import(project_id, payload)
        assert exc_info.value.error_code == "VISUAL_PLAN_TEST_FAILURE"
        assert exc_info.value.status_code == 422


def test_scene_visual_import_persistence_error_returns_cors_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    project_id, _script_id, _analysis_id, _style_id = approved_seed()

    def fail_import(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AppError(
            "SCENE_BREAKDOWN_IMPORT_PERSISTENCE_FAILED",
            "Scene Breakdown was valid but could not be persisted.",
            500,
            retryable=True,
            details={"phase": "paired_artifact_publication", "exception_type": "RuntimeError"},
        )

    monkeypatch.setattr(
        ExternalSceneBreakdownTwoStepService,
        "import_visual_result",
        fail_import,
    )
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post(
            f"/api/v4/projects/{project_id}/scene-breakdown/visual/import",
            json={"result_json": "{}"},
            headers={"Origin": "http://localhost:5173"},
        )
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert response.json()["error_code"] == "SCENE_BREAKDOWN_IMPORT_PERSISTENCE_FAILED"


def test_scene_prompt_projects_only_local_cadence_and_approved_voice_timing() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        project, script, analysis, style, word_timing = service._current_context(project_id)
        runtime_input, _sections = service._full_input(project, script, analysis, style, word_timing)
        assert set(runtime_input["script_context"]) == {"title", "sections"}
        for item in runtime_input["script_context"]["sections"]:
            assert "planned_narration_wpm" not in item["section_context"]
            assert "pacing_density" not in item["analysis_guidance"]
            hold = item["visual_cadence_guidance"]["preferred_hold_seconds"]
            assert hold["min"] > 0 and hold["max"] > hold["min"]
        timing_context = runtime_input["timing_context"]
        assert timing_context["total_narration_sample_count"] > 0
        assert all(item["actual_duration_samples"] > 0 for item in timing_context["sections"])
        assert all(item["timing_atoms"] for item in timing_context["sections"])
        for timing_section in timing_context["sections"]:
            previous_start = -1
            for atom in timing_section["timing_atoms"]:
                assert set(atom) == {
                    "kind",
                    "source_start",
                    "source_end",
                    "source_text",
                    "indivisible",
                    "start_boundary_safe",
                    "end_boundary_safe",
                    "speech_start_sample",
                    "speech_end_sample",
                }
                assert atom["kind"] in {"word", "spoken_override"}
                assert atom["indivisible"] is (atom["kind"] == "spoken_override")
                assert atom["source_end"] > atom["source_start"]
                assert atom["speech_end_sample"] >= atom["speech_start_sample"]
                assert atom["speech_start_sample"] >= previous_start
                previous_start = atom["speech_start_sample"]
        prompt = package["prompt_text"]
        assert "visual_cadence_guidance" in prompt
        assert "approved Narration Audio + NarrationWordTimingMap pair" in prompt
        assert "absolute positions on the approved Voice timeline" in prompt
        assert "The rescan is mandatory when materially indicated; a structural change is not." in prompt
        assert "A Beat may legitimately remain shorter or longer than the preferred hold guidance" in prompt
        assert "There is no cadence-compliance quota" in prompt
        assert "previous_scene_purpose" not in prompt
        assert "previous_visual_tail" not in prompt
        for leaked in ("planned_narration_wpm", "video_pacing_profile", "pacing_mode", "pacing_density"):
            assert leaked not in prompt


def test_pending_scene_breakdown_prompt_package_is_restored_after_reload() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        package = ExternalSceneBreakdownService(session).build_prompt(project_id)

    with factory() as session:
        restored = ExternalSceneBreakdownService(session).get_pending_prompt(project_id)
        assert restored["generated"] is True
        assert restored["prompt_package"] == package
        assert restored["prompt_package"]["artifact_ids"] == package["artifact_ids"]


def test_scene_prompt_is_domain_agnostic_and_delegates_channel_craft() -> None:
    from pathlib import Path

    prompt = Path("contracts/prompts/v4/scene_breakdown_full/system.md").read_text(
        encoding="utf-8"
    )
    assert "single current authoring skill already present in the current context" in prompt
    for leaked in (
        "archaeological context",
        "specific species",
        "tool type",
        "cooking method",
        "literal historical/scientific event",
    ):
        assert leaked not in prompt
    assert "unsupported domain-specific identity" in prompt
