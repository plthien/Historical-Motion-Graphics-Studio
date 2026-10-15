from __future__ import annotations

import pytest

from app.modules.v4.beat_timing_map import (
    compile_beat_timing_map,
    project_visual_intervals_for_source_spans,
)
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection
from app.modules.v4.scene_breakdown_micro_scene import build_micro_scenes
from app.modules.v4.scene_breakdown_partition_planner import build_legal_boundary_plan
from app.modules.v4.scene_breakdown_timing import apply_approved_voice_durations


def _word_timing_map() -> dict:
    return {
        "schema_version": "narration_word_timing_map_v1",
        "source_script_artifact_id": "artifact_script",
        "source_narration_audio_artifact_id": "artifact_audio",
        "sample_rate_hz": 48000,
        "total_sample_count": 192000,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_spoken_mapping": [],
                "words": [
                    {"authored_start": 0, "authored_end": 2, "global_start_sample": 4800, "global_end_sample": 24000},
                    {"authored_start": 3, "authored_end": 5, "global_start_sample": 38400, "global_end_sample": 57600},
                    {"authored_start": 6, "authored_end": 8, "global_start_sample": 72000, "global_end_sample": 91200},
                ],
            },
            {
                "section_id": "sec_002",
                "authored_spoken_mapping": [],
                "words": [
                    {"authored_start": 9, "authored_end": 11, "global_start_sample": 120000, "global_end_sample": 139200},
                    {"authored_start": 12, "authored_end": 14, "global_start_sample": 153600, "global_end_sample": 172800},
                ],
            },
        ],
    }


def _timing_section(section_id: str) -> dict:
    if section_id == "sec_001":
        return {
            "section_id": "sec_001",
            "source_start": 0,
            "source_end": 8,
            "sample_rate_hz": 48000,
            "timing_atoms": [
                {"source_start": 0, "source_end": 2, "speech_start_sample": 4800, "speech_end_sample": 24000, "end_boundary_safe": True},
                {"source_start": 3, "source_end": 5, "speech_start_sample": 38400, "speech_end_sample": 57600, "end_boundary_safe": True},
                {"source_start": 6, "source_end": 8, "speech_start_sample": 72000, "speech_end_sample": 91200, "end_boundary_safe": True},
            ],
            "clauses": [
                {"source_end": 2, "text": "aa", "end_boundary_safe": True},
                {"source_end": 5, "text": "bb", "end_boundary_safe": True},
                {"source_end": 8, "text": "cc", "end_boundary_safe": True},
            ],
        }
    return {
        "section_id": "sec_002",
        "source_start": 9,
        "source_end": 14,
        "sample_rate_hz": 48000,
        "timing_atoms": [
            {"source_start": 9, "source_end": 11, "speech_start_sample": 120000, "speech_end_sample": 139200, "end_boundary_safe": True},
            {"source_start": 12, "source_end": 14, "speech_start_sample": 153600, "speech_end_sample": 172800, "end_boundary_safe": True},
        ],
        "clauses": [
            {"source_end": 11, "text": "dd", "end_boundary_safe": True},
            {"source_end": 14, "text": "ee", "end_boundary_safe": True},
        ],
    }


def _scene(section_id: str) -> dict:
    if section_id == "sec_001":
        return {
            "scene_key": "sec_001_scene_001",
            "visual_context_ref": "ctx_001",
            "source_start": 0,
            "source_end": 8,
            "script_text": "aa bb cc",
            "duration_seconds": 0.001,
        }
    return {
        "scene_key": "sec_002_scene_001",
        "visual_context_ref": "ctx_001",
        "source_start": 9,
        "source_end": 14,
        "script_text": "dd ee",
        "duration_seconds": 0.001,
    }


def _spans_for_partition(plan: dict, boundary_ids: list[str]) -> list[list[int]]:
    offsets = [int(plan["source_start"])]
    boundary_by_id = {
        str(item["boundary_id"]): int(item["source_offset"])
        for item in plan["legal_boundaries"]
    }
    offsets.extend(boundary_by_id[item] for item in boundary_ids)
    offsets.append(int(plan["source_end"]))
    return [[start, end] for start, end in zip(offsets[:-1], offsets[1:], strict=True)]


def _compile(spans_by_scene: list[list[list[int]]]) -> dict:
    scenes = []
    beat_order = 0
    for scene_order, spans in enumerate(spans_by_scene, 1):
        beats = []
        for local_beat_order, span in enumerate(spans, 1):
            beat_order += 1
            beats.append(
                {
                    "beat_id": f"beat_{beat_order:03d}",
                    "beat_order": beat_order,
                    "local_beat_order": local_beat_order,
                    "resolved_script_span": span,
                }
            )
        scenes.append(
            {
                "scene_id": f"scene_{scene_order:03d}",
                "scene_order": scene_order,
                "beats": beats,
            }
        )
    return compile_beat_timing_map(
        project_id="project_001",
        script_artifact_id="artifact_script",
        scene_plan_artifact_id="artifact_scene",
        narration_audio_artifact_id="artifact_audio",
        narration_word_timing_map_artifact_id="artifact_timing",
        scene_plan={
            "project_id": "project_001",
            "scene_plan_revision_id": "artifact_scene",
            "source_script_revision_id": "artifact_script",
            "scenes": scenes,
        },
        word_timing_map=_word_timing_map(),
    )


def _durations(compiled: dict) -> list[float]:
    return [
        round(
            (item["visual_end_sample"] - item["visual_start_sample"]) / 48000,
            3,
        )
        for item in compiled["beats"]
    ]


def test_selected_boundaries_match_final_timing_for_leading_pause_and_section_tail_gap() -> None:
    plan = build_legal_boundary_plan(
        scene=_scene("sec_001"),
        timing_section=_timing_section("sec_001"),
        word_timing_map=_word_timing_map(),
    )
    selected_boundary_ids = ["boundary_001", "boundary_002"]
    spans = _spans_for_partition(plan, selected_boundary_ids)
    compiled = _compile([spans, [[9, 14]]])

    materialized = apply_approved_voice_durations(
        scenes=[
            {
                "beats": [
                    {"resolved_script_span": span}
                    for span in spans
                ]
            }
        ],
        word_timing_map=_word_timing_map(),
    )
    realized = [
        round(float(beat["estimated_duration_seconds"]), 3)
        for beat in materialized[0]["beats"]
    ]

    assert realized == [0.8, 0.7, 1.0]
    assert _durations(compiled)[:3] == realized


def test_selected_final_section_boundary_matches_final_narration_tail() -> None:
    plan = build_legal_boundary_plan(
        scene=_scene("sec_002"),
        timing_section=_timing_section("sec_002"),
        word_timing_map=_word_timing_map(),
    )
    spans = _spans_for_partition(plan, ["boundary_001"])
    compiled = _compile([[[0, 8]], spans])

    assert _durations(compiled)[-2:] == [0.7, 0.8]


def test_selected_partition_uses_same_canonical_visual_intervals() -> None:
    plan = build_legal_boundary_plan(
        scene=_scene("sec_001"),
        timing_section=_timing_section("sec_001"),
        word_timing_map=_word_timing_map(),
    )
    spans = _spans_for_partition(plan, ["boundary_001"])
    projected = project_visual_intervals_for_source_spans(
        _word_timing_map(),
        spans,
    )
    projected_durations = [
        round((item["visual_end_sample"] - item["visual_start_sample"]) / 48000, 3)
        for item in projected
    ]
    compiled = _compile([spans, [[9, 14]]])

    assert projected_durations == [0.8, 1.7]
    assert _durations(compiled)[:2] == projected_durations


def test_micro_scenes_use_project_global_visual_intervals_across_section_tail() -> None:
    section = ExtractedSection(
        section_id="sec_001",
        section_order=1,
        section_title="One",
        section_role="body",
        resolved_script_span=(0, 8),
        script_excerpt="aa bb cc",
        pacing_density="high",
        planned_narration_wpm=220,
    )
    scenes = build_micro_scenes(
        section=section,
        timing_section=_timing_section("sec_001"),
        word_timing_map=_word_timing_map(),
        preferred_hold_seconds={"min": 0.6, "max": 1.1},
        visual_context_catalog=[
            {
                "context_id": "ctx_001",
                "presence_ranges": [
                    {"section_id": "sec_001", "resolved_script_span": [0, 8]}
                ],
            }
        ],
    )

    assert [item["source_end"] for item in scenes] == [2, 5, 8]
    assert [item["duration_seconds"] for item in scenes] == [0.8, 0.7, 1.0]
    assert "".join(item["script_text"] for item in scenes) == "aa bb cc"
