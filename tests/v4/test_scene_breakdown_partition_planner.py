from __future__ import annotations

import pytest

from app.api.errors import AppError
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection
from app.modules.v4.scene_breakdown_partition_planner import (
    build_legal_boundary_plan,
    project_scene_authoring_input,
)
from app.modules.v4.scene_breakdown_scene_materializer import (
    authoring_metrics,
    materialize_scene_section,
)


def _timing() -> dict:
    return {
        "section_id": "sec_001",
        "source_start": 0,
        "source_end": 14,
        "sample_rate_hz": 48000,
        "timing_atoms": [
            {"source_start": 0, "source_end": 4, "speech_start_sample": 0, "speech_end_sample": 43200, "end_boundary_safe": True},
            {"source_start": 5, "source_end": 9, "speech_start_sample": 48000, "speech_end_sample": 91200, "end_boundary_safe": True},
            {"source_start": 10, "source_end": 14, "speech_start_sample": 96000, "speech_end_sample": 139200, "end_boundary_safe": True},
        ],
        "clauses": [
            {"source_end": 4, "text": "aaaa", "end_boundary_safe": True},
            {"source_end": 9, "text": "bbbb", "end_boundary_safe": True},
            {"source_end": 14, "text": "cccc", "end_boundary_safe": True},
        ],
    }


def _word_timing_map() -> dict:
    return {
        "sample_rate_hz": 48000,
        "total_sample_count": 192000,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_spoken_mapping": [],
                "words": [
                    {"authored_start": 0, "authored_end": 4, "global_start_sample": 0, "global_end_sample": 43200},
                    {"authored_start": 5, "authored_end": 9, "global_start_sample": 48000, "global_end_sample": 91200},
                    {"authored_start": 10, "authored_end": 14, "global_start_sample": 96000, "global_end_sample": 139200},
                ],
            }
        ],
    }


def _scene() -> dict:
    return {
        "scene_key": "sec_001_scene_001",
        "visual_context_ref": "ctx_001",
        "source_start": 0,
        "source_end": 14,
        "script_text": "aaaa bbbb cccc",
    }


def _section() -> ExtractedSection:
    return ExtractedSection(
        section_id="sec_001",
        section_order=1,
        section_title="Test",
        section_role="body",
        resolved_script_span=(0, 14),
        script_excerpt="aaaa bbbb cccc",
        pacing_density="high",
        planned_narration_wpm=220,
    )


def _metadata(count: int, relation: str = "new_setup") -> list[dict[str, str]]:
    return [
        {
            "meaning_now": f"Meaning {index}",
            "visible_state_now": f"State {index}",
            "delta_now": f"Delta {index}",
            "continuity_with_previous": relation if index == 1 else "same_setup",
        }
        for index in range(1, count + 1)
    ]


def _output(boundary_ids: list[str], relation: str = "new_setup") -> dict:
    return {
        "section_id": "sec_001",
        "scenes": [
            {
                "scene_key": "sec_001_scene_001",
                "partition": {"boundary_ids": boundary_ids},
                "beats": _metadata(len(boundary_ids) + 1, relation),
            }
        ],
    }


def _authoring(plan: dict) -> dict:
    return {
        "section_id": "sec_001",
        "preferred_hold_seconds": {"min": 0.8, "max": 1.2},
        "scenes": [plan],
    }


def test_legal_boundary_plan_has_no_partition_recommendation_surface() -> None:
    plan = build_legal_boundary_plan(
        scene=_scene(),
        timing_section=_timing(),
        word_timing_map=_word_timing_map(),
    )
    assert set(plan) == {
        "scene_key",
        "source_start",
        "source_end",
        "script_text",
        "visual_context_ref",
        "duration_seconds",
        "legal_boundaries",
    }
    assert [item["boundary_id"] for item in plan["legal_boundaries"]] == [
        "boundary_001",
        "boundary_002",
    ]


def test_step1_input_exposes_every_legal_boundary_and_no_backend_choices() -> None:
    plan = build_legal_boundary_plan(
        scene=_scene(),
        timing_section=_timing(),
        word_timing_map=_word_timing_map(),
    )
    projected = project_scene_authoring_input(plan)
    assert set(projected) == {"scene_key", "visual_context_ref", "source_segments"}
    assert projected["visual_context_ref"] == "ctx_001"
    assert "".join(item["text"] for item in projected["source_segments"]) == _scene()["script_text"]
    assert [item["boundary_id_after"] for item in projected["source_segments"]] == [
        "boundary_001",
        "boundary_002",
        None,
    ]
    assert all("source_offset" not in item for item in projected["source_segments"])


@pytest.mark.parametrize("boundary_ids", [[], ["boundary_001"], ["boundary_002"], ["boundary_001", "boundary_002"]])
def test_materializer_accepts_any_ordered_legal_subset_and_materializes_exact_spans(boundary_ids: list[str]) -> None:
    plan = build_legal_boundary_plan(
        scene=_scene(),
        timing_section=_timing(),
        word_timing_map=_word_timing_map(),
    )
    materialized, _metrics = materialize_scene_section(
        section=_section(),
        authoring_section=_authoring(plan),
        output_section=_output(boundary_ids),
        require_first_new_setup=True,
    )
    offsets = [0, *({"boundary_001": 4, "boundary_002": 9}[value] for value in boundary_ids), 14]
    assert [beat["resolved_script_span"] for beat in materialized["scenes"][0]["beats"]] == [
        [start, end] for start, end in zip(offsets[:-1], offsets[1:], strict=True)
    ]
    assert "".join(beat["script_quote"] for beat in materialized["scenes"][0]["beats"]) == _scene()["script_text"]


@pytest.mark.parametrize(
    ("boundary_ids", "error_code"),
    [
        (["boundary_999"], "unknown_boundary_id"),
        (["boundary_001", "boundary_001"], "duplicate_boundary_id"),
        (["boundary_002", "boundary_001"], "unordered_boundary_ids"),
    ],
)
def test_materializer_rejects_unknown_duplicate_and_out_of_order_boundary_ids(
    boundary_ids: list[str],
    error_code: str,
) -> None:
    plan = build_legal_boundary_plan(
        scene=_scene(),
        timing_section=_timing(),
        word_timing_map=_word_timing_map(),
    )
    with pytest.raises(AppError) as exc_info:
        materialize_scene_section(
            section=_section(),
            authoring_section=_authoring(plan),
            output_section=_output(boundary_ids),
            require_first_new_setup=True,
        )
    assert exc_info.value.details["errors"][0]["code"] == error_code


def test_materializer_rejects_cross_scene_boundary_id() -> None:
    plan = build_legal_boundary_plan(
        scene=_scene(),
        timing_section=_timing(),
        word_timing_map=_word_timing_map(),
    )
    output = _output(["boundary_003"])
    with pytest.raises(AppError) as exc_info:
        materialize_scene_section(
            section=_section(),
            authoring_section=_authoring(plan),
            output_section=output,
            require_first_new_setup=True,
        )
    assert exc_info.value.details["errors"][0]["code"] == "unknown_boundary_id"


def test_selected_result_qc_has_no_partition_selection_metrics() -> None:
    plan = build_legal_boundary_plan(
        scene=_scene(),
        timing_section=_timing(),
        word_timing_map=_word_timing_map(),
    )
    materialized, selection_metrics = materialize_scene_section(
        section=_section(),
        authoring_section=_authoring(plan),
        output_section=_output(["boundary_001"]),
        require_first_new_setup=True,
    )
    metrics = authoring_metrics(
        authoring_section=_authoring(plan),
        materialized=materialized,
        selection_metrics=selection_metrics,
        word_timing_map=_word_timing_map(),
    )
    assert all("selection" not in key for key in metrics)
    assert metrics["beat_count"] == 2


@pytest.mark.parametrize(
    "preferred",
    [
        {"min": 2.0, "max": 2.0},
        {"min": 3.0, "max": 2.0},
        {"min": False, "max": 2.0},
        {"min": 1.0, "max": float("-inf")},
    ],
)
def test_selected_result_qc_rejects_invalid_preferred_hold_ranges(preferred: dict) -> None:
    plan = build_legal_boundary_plan(
        scene=_scene(),
        timing_section=_timing(),
        word_timing_map=_word_timing_map(),
    )
    materialized, selection_metrics = materialize_scene_section(
        section=_section(),
        authoring_section=_authoring(plan),
        output_section=_output(["boundary_001"]),
        require_first_new_setup=True,
    )
    authoring = _authoring(plan)
    authoring["preferred_hold_seconds"] = preferred
    with pytest.raises(AppError) as exc_info:
        authoring_metrics(
            authoring_section=authoring,
            materialized=materialized,
            selection_metrics=selection_metrics,
            word_timing_map=_word_timing_map(),
        )
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_PARTITION_GUIDANCE_INVALID"
