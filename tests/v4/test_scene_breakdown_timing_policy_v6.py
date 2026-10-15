import json

import pytest

from app.core.config import get_settings
from app.db.enums import ArtifactType
from app.db.models import Project
from app.db.session import build_session_factory
from app.modules.platform.video_pacing import preferred_hold_range, resolve_video_pacing_profile
from app.modules.v4.beat_timing_map import build_scene_timing_context, project_visual_intervals_for_source_spans
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from app.modules.v4.scene_breakdown_timing import TIMING_POLICY_VERSION, apply_approved_voice_durations, evaluate_timing_diagnostics
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService
from tests.support.fixtures import sample_pacing_profiles
from tests.v4.combined_scene_breakdown_support import approved_seed


def _word(a0: int, a1: int, s0: int, s1: int) -> dict:
    return {"authored_start": a0, "authored_end": a1, "global_start_sample": s0, "global_end_sample": s1}


def _timing_map() -> dict:
    return {"sample_rate_hz": 48000, "total_sample_count": 480000, "sections": [{"section_id": "sec_001", "words": [_word(0, 3, 48000, 96000), _word(4, 7, 120000, 168000), _word(8, 13, 240000, 288000)]}]}


def _scenes() -> list[dict]:
    return [{"scene_id": "scene_001", "beats": [{"resolved_script_span": [0, 7], "script_quote": "one two", "estimated_duration_seconds": 0.0001}, {"resolved_script_span": [7, 13], "script_quote": "three", "estimated_duration_seconds": 0.0001}]}]


def test_timing_policy_version_is_approved_voice_v6() -> None:
    assert TIMING_POLICY_VERSION == "approved_voice_static_image_v6"


def test_realized_beat_duration_comes_from_approved_voice_samples() -> None:
    scenes = apply_approved_voice_durations(scenes=_scenes(), word_timing_map=_timing_map())
    assert scenes[0]["beats"][0]["estimated_duration_seconds"] == 5.0
    assert scenes[0]["beats"][1]["estimated_duration_seconds"] == 5.0


def test_diagnostics_consume_resolved_section_guidance() -> None:
    scenes = apply_approved_voice_durations(scenes=_scenes(), word_timing_map=_timing_map())
    minimum, maximum = preferred_hold_range(resolve_video_pacing_profile(sample_pacing_profiles(), "medium"), "medium")
    result = evaluate_timing_diagnostics(scenes=scenes, word_timing_map=_timing_map(), planned_narration_wpm=155, visual_cadence_guidance={"preferred_hold_seconds": {"min": minimum, "max": maximum}})
    assert result.metrics["preferred_hold_seconds"] == {"min": 5.5, "max": 8.0}
    assert result.metrics["planned_narration_wpm"] == 155
    assert "target_beat_count_for_section" not in result.metrics


def test_long_hold_warns_for_semantic_rescan_not_rejection() -> None:
    timing = _timing_map(); timing["total_sample_count"] = 960000
    scenes = apply_approved_voice_durations(scenes=[{"scene_id": "scene_001", "beats": [{"resolved_script_span": [0, 13], "script_quote": "one two three", "estimated_duration_seconds": 0.0001}]}], word_timing_map=timing)
    result = evaluate_timing_diagnostics(scenes=scenes, word_timing_map=timing, planned_narration_wpm=155, visual_cadence_guidance={"preferred_hold_seconds": {"min": 2.5, "max": 4.0}})
    assert result.status == "warning"
    assert result.metrics["above_preferred_ratio"] == 1.0
    assert "duration alone" in result.warnings[0]


def test_short_holds_warn_for_false_fragmentation_rescan() -> None:
    timing = {"sample_rate_hz": 48000, "total_sample_count": 96000, "sections": [{"section_id": "sec_001", "words": [_word(0, 3, 0, 19200), _word(4, 7, 38400, 57600)]}]}
    scenes = apply_approved_voice_durations(scenes=[{"scene_id": "scene_001", "beats": [{"resolved_script_span": [0, 3], "script_quote": "one", "estimated_duration_seconds": 0.0001}, {"resolved_script_span": [3, 7], "script_quote": "two", "estimated_duration_seconds": 0.0001}]}], word_timing_map=timing)
    result = evaluate_timing_diagnostics(scenes=scenes, word_timing_map=timing, planned_narration_wpm=155, visual_cadence_guidance={"preferred_hold_seconds": {"min": 6.0, "max": 8.0}})
    assert result.status == "warning"
    assert result.metrics["below_preferred_ratio"] == 1.0
    assert "same-dominant-still fragmentation" in result.warnings[0]


@pytest.mark.parametrize(
    "preferred",
    [
        {"min": 2.0, "max": 2.0},
        {"min": True, "max": 2.0},
        {"min": 1.0, "max": float("nan")},
    ],
)
def test_timing_diagnostics_reject_invalid_preferred_hold_ranges(preferred: dict) -> None:
    with pytest.raises(ValueError, match="preferred hold range is invalid"):
        evaluate_timing_diagnostics(
            scenes=apply_approved_voice_durations(scenes=_scenes(), word_timing_map=_timing_map()),
            word_timing_map=_timing_map(),
            planned_narration_wpm=155,
            visual_cadence_guidance={"preferred_hold_seconds": preferred},
        )

def test_scene_timing_context_collapses_spoken_override_to_one_indivisible_atom() -> None:
    script = {
        "script_sections": [
            {
                "section_id": "sec_001",
                "narration": "Code D3444 now.",
            }
        ]
    }
    timing_map = {
        "schema_version": "narration_word_timing_map_v1",
        "source_script_artifact_id": "artifact_script",
        "source_narration_audio_artifact_id": "artifact_audio",
        "sample_rate_hz": 48000,
        "total_sample_count": 96000,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_section_start": 0,
                "authored_section_end": 15,
                "sample_count": 96000,
                "authored_spoken_mapping": [
                    {
                        "authored_start": 5,
                        "authored_end": 10,
                    }
                ],
                "words": [
                    {**_word(0, 4, 1000, 5000), "text": "Code"},
                    {**_word(5, 10, 6000, 7000), "text": "D"},
                    {**_word(5, 10, 8000, 10000), "text": "three"},
                    {**_word(5, 10, 11000, 13000), "text": "four"},
                    {**_word(5, 10, 14000, 16000), "text": "four"},
                    {**_word(5, 10, 17000, 19000), "text": "four"},
                    {**_word(11, 14, 21000, 26000), "text": "now"},
                ],
            }
        ],
    }

    context = build_scene_timing_context(
        project_id="project_001",
        script_artifact_id="artifact_script",
        narration_audio_artifact_id="artifact_audio",
        narration_word_timing_map_artifact_id="artifact_timing",
        script=script,
        word_timing_map=timing_map,
    )

    atoms = context["sections"][0]["timing_atoms"]
    assert [(item["kind"], item["source_start"], item["source_end"], item["source_text"]) for item in atoms] == [
        ("word", 0, 4, "Code"),
        ("spoken_override", 5, 10, "D3444"),
        ("word", 11, 14, "now"),
    ]
    override = atoms[1]
    assert override["indivisible"] is True
    assert override["start_boundary_safe"] is True
    assert override["end_boundary_safe"] is True
    assert override["speech_start_sample"] == 6000
    assert override["speech_end_sample"] == 19000
    assert len([item for item in atoms if item["source_start"] == 5 and item["source_end"] == 10]) == 1

    intervals = project_visual_intervals_for_source_spans(
        timing_map,
        [[0, 5], [5, 10], [10, 15]],
    )
    assert intervals[1]["speech_start_sample"] == override["speech_start_sample"]
    assert intervals[1]["speech_end_sample"] == override["speech_end_sample"]


def test_scene_timing_context_collapses_legacy_override_word_subspans() -> None:
    script = {
        "script_sections": [
            {
                "section_id": "sec_001",
                "narration": "Code D3444 now.",
            }
        ]
    }
    timing_map = {
        "schema_version": "narration_word_timing_map_v1",
        "source_script_artifact_id": "artifact_script",
        "source_narration_audio_artifact_id": "artifact_audio",
        "sample_rate_hz": 48000,
        "total_sample_count": 96000,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_section_start": 0,
                "authored_section_end": 15,
                "sample_count": 96000,
                "authored_spoken_mapping": [
                    {
                        "authored_start": 5,
                        "authored_end": 10,
                    }
                ],
                "words": [
                    {**_word(0, 4, 1000, 5000), "text": "Code"},
                    {**_word(5, 6, 6000, 7000), "text": "D"},
                    {**_word(6, 10, 8000, 19000), "text": "three-four-four-four"},
                    {**_word(11, 14, 21000, 26000), "text": "now"},
                ],
            }
        ],
    }

    context = build_scene_timing_context(
        project_id="project_001",
        script_artifact_id="artifact_script",
        narration_audio_artifact_id="artifact_audio",
        narration_word_timing_map_artifact_id="artifact_timing",
        script=script,
        word_timing_map=timing_map,
    )

    atoms = context["sections"][0]["timing_atoms"]
    assert [(item["kind"], item["source_start"], item["source_end"], item["source_text"]) for item in atoms] == [
        ("word", 0, 4, "Code"),
        ("spoken_override", 5, 10, "D3444"),
        ("word", 11, 14, "now"),
    ]
    override = atoms[1]
    assert override["speech_start_sample"] == 6000
    assert override["speech_end_sample"] == 19000


def test_scene_timing_context_splits_alignment_words_that_touch_override_suffix() -> None:
    narration = "Code D3444-coded now."
    spoken = "Code D three four four four-coded now."
    script = {
        "script_sections": [
            {
                "section_id": "sec_001",
                "narration": narration,
            }
        ]
    }
    timing_map = {
        "schema_version": "narration_word_timing_map_v1",
        "source_script_artifact_id": "artifact_script",
        "source_narration_audio_artifact_id": "artifact_audio",
        "sample_rate_hz": 48000,
        "total_sample_count": 96000,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_section_start": 0,
                "authored_section_end": len(narration),
                "sample_count": 96000,
                "authored_spoken_mapping": [
                    {
                        "authored_start": 5,
                        "authored_end": 10,
                        "section_authored_start": 5,
                        "section_authored_end": 10,
                        "spoken_start": 5,
                        "spoken_end": 27,
                    }
                ],
                "character_timing": [
                    {
                        "spoken_start": index,
                        "spoken_end": index + 1,
                        "global_start_sample": index * 1000,
                        "global_end_sample": (index + 1) * 1000,
                    }
                    for index, _character in enumerate(spoken)
                ],
                "words": [
                    {**_word(0, 4, 0, 4000), "text": "Code", "spoken_start": 0, "spoken_end": 4},
                    {**_word(5, 10, 5000, 6000), "text": "D", "spoken_start": 5, "spoken_end": 6},
                    {**_word(5, 10, 7000, 12000), "text": "three", "spoken_start": 7, "spoken_end": 12},
                    {**_word(5, 10, 13000, 17000), "text": "four", "spoken_start": 13, "spoken_end": 17},
                    {**_word(5, 10, 18000, 22000), "text": "four", "spoken_start": 18, "spoken_end": 22},
                    {**_word(5, 16, 23000, 33000), "text": "four-coded", "spoken_start": 23, "spoken_end": 33},
                    {**_word(17, 21, 34000, 38000), "text": "now.", "spoken_start": 34, "spoken_end": 38},
                ],
            }
        ],
    }

    context = build_scene_timing_context(
        project_id="project_001",
        script_artifact_id="artifact_script",
        narration_audio_artifact_id="artifact_audio",
        narration_word_timing_map_artifact_id="artifact_timing",
        script=script,
        word_timing_map=timing_map,
    )

    atoms = context["sections"][0]["timing_atoms"]
    assert [(item["kind"], item["source_start"], item["source_end"], item["source_text"]) for item in atoms] == [
        ("word", 0, 4, "Code"),
        ("spoken_override", 5, 10, "D3444"),
        ("word", 10, 16, "-coded"),
        ("word", 17, 21, "now."),
    ]
    assert atoms[1]["speech_start_sample"] == 5000
    assert atoms[1]["speech_end_sample"] == 27000
    assert atoms[2]["speech_start_sample"] == 27000
    assert atoms[2]["speech_end_sample"] == 33000
    assert context["sections"][0]["spoken_override_spans"] == [{"source_start": 5, "source_end": 10}]
    assert "spoken_start" not in context["sections"][0]["spoken_override_spans"][0]
    assert "spoken_end" not in context["sections"][0]["spoken_override_spans"][0]


def test_full_scene_breakdown_input_accepts_explicit_spoken_override_coordinates_without_public_leak() -> None:
    project_id, _script_id, _analysis_id, _style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        artifacts = ArtifactService(session, ArtifactRepository(session))
        timing = json.loads(artifacts.read_artifact_bytes(str(project.narration_word_timing_map_artifact_id)).decode("utf-8"))
        first_section = timing["sections"][0]
        first_word = first_section["words"][0]
        first_section["authored_spoken_mapping"] = [
            {
                "authored_start": first_word["authored_start"],
                "authored_end": first_word["authored_end"],
                "spoken_start": first_word["spoken_start"],
                "spoken_end": first_word["spoken_end"],
            }
        ]
        staged = artifacts.write_artifact(
            logical_id=f"project/{project.project_id}/voice/narration-word-timing-map",
            artifact_type=ArtifactType.JSON,
            filename="narration_word_timing_map.json",
            content=json.dumps(timing, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            provider_source="scene_breakdown_timing_policy_test",
        )
        project.narration_word_timing_map_artifact_id = staged.artifact_id
        session.commit()

        external = ExternalSceneBreakdownService(session)
        current_project, script, analysis, style, word_timing = external._current_context(project_id)
        payload, _sections = external._full_input(current_project, script, analysis, style, word_timing)

    spans = payload["timing_context"]["sections"][0]["spoken_override_spans"]
    assert spans == [{"source_start": first_word["authored_start"], "source_end": first_word["authored_end"]}]
    assert "spoken_start" not in spans[0]
    assert "spoken_end" not in spans[0]

