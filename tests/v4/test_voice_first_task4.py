from __future__ import annotations

import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.models import Project
from app.db.session import build_session_factory
from app.modules.v4.beat_timing_map import compile_beat_timing_map
from app.modules.v4.production_artifact_loader import ProductionArtifactLoaderV4
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from tests.v4.combined_scene_breakdown_support import _seed_approved_voice_pair, approved_seed, generate_combined_pair


def _word_map() -> dict:
    return {
        "schema_version": "narration_word_timing_map_v1",
        "source_script_artifact_id": "script_current",
        "source_narration_audio_artifact_id": "audio_current",
        "sample_rate_hz": 48_000,
        "total_sample_count": 3_600,
        "sections": [
            {
                "section_id": "sec_001",
                "section_order": 1,
                "authored_section_start": 0,
                "authored_section_end": 22,
                "sample_count": 3_600,
                "global_start_sample": 0,
                "global_end_sample": 3_600,
                "authored_spoken_mapping": [],
                "words": [
                    {"text": "Alpha", "authored_start": 0, "authored_end": 5, "global_start_sample": 480, "global_end_sample": 800},
                    {"text": "beta", "authored_start": 6, "authored_end": 10, "global_start_sample": 900, "global_end_sample": 1_100},
                    {"text": "gamma", "authored_start": 11, "authored_end": 16, "global_start_sample": 1_800, "global_end_sample": 2_100},
                    {"text": "delta", "authored_start": 17, "authored_end": 22, "global_start_sample": 2_600, "global_end_sample": 3_000},
                ],
            }
        ],
    }


def _scene_plan() -> dict:
    return {
        "project_id": "project_task4",
        "scene_plan_revision_id": "scene_current",
        "source_script_revision_id": "script_current",
        "scenes": [
            {
                "scene_id": "scene_001",
                "scene_order": 1,
                "resolved_script_span": [0, 22],
                "beats": [
                    {"beat_id": "beat_001", "beat_order": 1, "local_beat_order": 1, "resolved_script_span": [0, 10]},
                    {"beat_id": "beat_002", "beat_order": 2, "local_beat_order": 2, "resolved_script_span": [11, 16]},
                    {"beat_id": "beat_003", "beat_order": 3, "local_beat_order": 3, "resolved_script_span": [17, 22]},
                ],
            }
        ],
    }


def test_beat_timing_map_uses_voice_samples_for_leading_pause_and_trailing_visual_coverage() -> None:
    timing = compile_beat_timing_map(
        project_id="project_task4",
        script_artifact_id="script_current",
        scene_plan_artifact_id="scene_current",
        narration_audio_artifact_id="audio_current",
        narration_word_timing_map_artifact_id="word_current",
        scene_plan=_scene_plan(),
        word_timing_map=_word_map(),
    )
    beats = timing["beats"]
    assert [(beat["speech_start_sample"], beat["speech_end_sample"]) for beat in beats] == [
        (480, 1_100),
        (1_800, 2_100),
        (2_600, 3_000),
    ]
    assert [(beat["visual_start_sample"], beat["visual_end_sample"]) for beat in beats] == [
        (0, 1_800),
        (1_800, 2_600),
        (2_600, 3_600),
    ]
    assert beats[0]["speech_end_sample"] < beats[0]["visual_end_sample"]
    assert beats[-1]["speech_end_sample"] < beats[-1]["visual_end_sample"]
    assert timing["source_narration_word_timing_map_artifact_id"] == "word_current"


def test_beat_timing_map_rejects_scene_boundary_strictly_inside_spoken_override() -> None:
    word_map = _word_map()
    word_map["sections"][0]["authored_spoken_mapping"] = [
        {"authored_start": 6, "authored_end": 10, "spoken_start": 6, "spoken_end": 20}
    ]
    scene_plan = _scene_plan()
    scene_plan["scenes"][0]["beats"][0]["resolved_script_span"] = [0, 8]
    scene_plan["scenes"][0]["beats"][1]["resolved_script_span"] = [8, 16]
    with pytest.raises(AppError) as exc:
        compile_beat_timing_map(
            project_id="project_task4",
            script_artifact_id="script_current",
            scene_plan_artifact_id="scene_current",
            narration_audio_artifact_id="audio_current",
            narration_word_timing_map_artifact_id="word_current",
            scene_plan=scene_plan,
            word_timing_map=word_map,
        )
    assert exc.value.error_code == "BEAT_TIMING_BOUNDARY_UNSAFE"


def test_scene_prompt_requires_approved_voice_and_reservation_freezes_exact_pair() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        project = session.get(Project, project_id)
        assert project is not None
        audio_id = project.narration_audio_artifact_id
        word_id = project.narration_word_timing_map_artifact_id
        service.build_prompt(project_id)
        reservation = service.reservations.pending_for(project_id=project_id, stage="scene_breakdown")
        assert reservation is not None
        assert reservation.source_json["narration_audio_artifact_id"] == audio_id
        assert reservation.source_json["narration_word_timing_map_artifact_id"] == word_id
        current_project, script, analysis, style, word_timing = service._current_context(project_id)
        runtime_input, _sections = service._full_input(current_project, script, analysis, style, word_timing)
        assert runtime_input["timing_context"]["sample_rate_hz"] == 48_000
        assert runtime_input["timing_context"]["sections"]
        project.narration_audio_artifact_id = None
        project.narration_word_timing_map_artifact_id = None
        session.commit()
    with factory() as session:
        with pytest.raises(AppError) as exc:
            ExternalSceneBreakdownService(session).build_prompt(project_id)
        assert exc.value.error_code == "SCENE_BREAKDOWN_VOICE_NOT_APPROVED"


def test_scene_approval_publishes_beat_timing_atomically_without_invalidating_voice() -> None:
    result = generate_combined_pair(approve=True)
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        assert project.narration_audio_artifact_id is not None
        assert project.narration_word_timing_map_artifact_id is not None
        assert project.beat_timing_map_artifact_id is not None
        service = SceneBreakdownServiceV4(session)
        timing = ProductionArtifactLoaderV4(service.artifacts, service.v4).load_beat_timing_map(
            project,
            str(project.beat_timing_map_artifact_id),
            require_current=True,
        )
        assert timing["source_scene_plan_revision_id"] == project.scene_plan_artifact_id
        assert timing["source_narration_audio_artifact_id"] == project.narration_audio_artifact_id
        assert timing["source_narration_word_timing_map_artifact_id"] == project.narration_word_timing_map_artifact_id
        assert timing["beats"][0]["visual_start_sample"] == 0
        assert timing["beats"][-1]["visual_end_sample"] == timing["total_narration_sample_count"]


def test_pending_scene_prompt_restoration_is_fenced_by_exact_voice_pair() -> None:
    project_id, script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        project = session.get(Project, project_id)
        assert project is not None
        old_audio = project.narration_audio_artifact_id
        old_word_timing = project.narration_word_timing_map_artifact_id
        _seed_approved_voice_pair(session, project, script_id)
        session.refresh(project)
        assert project.narration_audio_artifact_id != old_audio
        assert project.narration_word_timing_map_artifact_id != old_word_timing
        restored = service.get_pending_prompt(project_id)
        assert restored["generated"] is False
        assert restored["prompt_package"] is None
        assert package["artifact_ids"]
