from __future__ import annotations

import pytest

from app.api.errors import AppError
from app.modules.v4.beat_timing_map import (
    build_scene_timing_context,
    compile_beat_timing_map,
    project_visual_intervals_for_source_spans,
)
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection
from app.modules.v4.scene_breakdown_micro_scene import build_micro_scenes
from app.modules.v4.voice_spoken_text import compile_voice_spoken_text
from app.modules.v4.voice_task3 import build_project_word_timing


def _override(
    override_id: str,
    start: int,
    end: int,
    spoken_text: str,
) -> dict:
    return {
        "id": override_id,
        "source_start": start,
        "source_end": end,
        "spoken_text": spoken_text,
    }


def _timing_context(
    authored: str,
    *,
    overrides: list[dict],
    delivery_cues: list[dict] | None = None,
) -> tuple[dict, dict, dict]:
    script = {
        "script_sections": [
            {
                "section_id": "sec_001",
                "narration": authored,
                "spoken_overrides": overrides,
                "delivery_cues": list(delivery_cues or []),
            }
        ]
    }
    compiled = compile_voice_spoken_text(script)
    compiled_section = compiled.sections[0]
    transcript = str(compiled_section["canonical_spoken_text"])

    character_timing = [
        {
            "character_index": index,
            "text": char,
            "spoken_start": index,
            "spoken_end": index + 1,
            "start_sample": index * 1000,
            "end_sample": (index + 1) * 1000,
        }
        for index, char in enumerate(transcript)
    ]
    words = build_project_word_timing(
        transcript=transcript,
        character_timing=character_timing,
        authored_spoken_mapping=list(compiled_section["authored_spoken_mapping"]),
        authored_section_start=0,
        authored_length=len(authored),
    )
    projected_words = [
        {
            **word,
            "global_start_sample": int(word["start_sample"]),
            "global_end_sample": int(word["end_sample"]),
        }
        for word in words
    ]
    projected_characters = [
        {
            **item,
            "global_start_sample": int(item["start_sample"]),
            "global_end_sample": int(item["end_sample"]),
        }
        for item in character_timing
    ]
    total_samples = max(1, len(transcript) * 1000)
    word_timing_map = {
        "schema_version": "narration_word_timing_map_v1",
        "source_script_artifact_id": "artifact_script",
        "source_narration_audio_artifact_id": "artifact_audio",
        "sample_rate_hz": 48000,
        "total_sample_count": total_samples,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_section_start": 0,
                "authored_section_end": len(authored),
                "sample_count": total_samples,
                "authored_spoken_mapping": list(
                    compiled_section["authored_spoken_mapping"]
                ),
                "character_timing": projected_characters,
                "words": projected_words,
            }
        ],
    }
    context = build_scene_timing_context(
        project_id="project_001",
        script_artifact_id="artifact_script",
        narration_audio_artifact_id="artifact_audio",
        narration_word_timing_map_artifact_id="artifact_timing",
        script={
            "script_sections": [
                {"section_id": "sec_001", "narration": authored}
            ]
        },
        word_timing_map=word_timing_map,
    )
    return context["sections"][0], word_timing_map, compiled_section


def _assert_atoms_are_ordered(atoms: list[dict]) -> None:
    for left, right in zip(atoms, atoms[1:], strict=False):
        assert int(left["source_end"]) <= int(right["source_start"])
        assert int(left["speech_end_sample"]) <= int(
            right["speech_start_sample"]
        )
    assert all(
        int(atom["source_end"]) > int(atom["source_start"])
        for atom in atoms
    )
    assert all(
        int(atom["speech_end_sample"]) >= int(atom["speech_start_sample"])
        for atom in atoms
    )


@pytest.mark.parametrize(
    ("authored", "override"),
    [
        ("$19.50", _override("override_currency", 0, 6, "nineteen dollars fifty cents")),
        ("1.77", _override("override_decimal", 0, 4, "one point seven seven")),
        ("X204", _override("override_code", 0, 4, "X two zero four")),
    ],
)
def test_whole_token_spoken_override_remains_one_indivisible_scene_atom(
    authored: str,
    override: dict,
) -> None:
    timing_section, _word_timing_map, _compiled = _timing_context(
        authored,
        overrides=[override],
    )
    atoms = timing_section["timing_atoms"]

    assert len(atoms) == 1
    assert atoms[0]["kind"] == "spoken_override"
    assert atoms[0]["indivisible"] is True
    assert [atoms[0]["source_start"], atoms[0]["source_end"]] == [
        0,
        len(authored),
    ]
    assert atoms[0]["source_text"] == authored
    _assert_atoms_are_ordered(atoms)


def test_multiple_overrides_inside_one_project_word_are_supported() -> None:
    timing_section, _word_timing_map, compiled = _timing_context(
        "A1B2",
        overrides=[
            _override("override_a", 0, 2, "alpha"),
            _override("override_b", 2, 4, "beta"),
        ],
    )

    assert compiled["canonical_spoken_text"] == "alphabeta"
    atoms = timing_section["timing_atoms"]
    assert [
        (atom["kind"], atom["source_start"], atom["source_end"])
        for atom in atoms
    ] == [
        ("spoken_override", 0, 2),
        ("spoken_override", 2, 4),
    ]
    assert all(atom["indivisible"] is True for atom in atoms)
    assert "".join(atom["source_text"] for atom in atoms) == "A1B2"
    _assert_atoms_are_ordered(atoms)


def test_multiple_overrides_inside_one_word_compile_through_scene_and_beat_timing() -> None:
    authored = "A1B2"
    timing_section, word_timing_map, _compiled = _timing_context(
        authored,
        overrides=[
            _override("override_a", 0, 2, "alpha"),
            _override("override_b", 2, 4, "beta"),
        ],
    )
    atoms = timing_section["timing_atoms"]
    spans = [
        [int(atom["source_start"]), int(atom["source_end"])]
        for atom in atoms
    ]
    assert spans == [[0, 2], [2, 4]]

    intervals = project_visual_intervals_for_source_spans(
        word_timing_map,
        spans,
    )
    assert [
        (
            item["speech_start_sample"],
            item["speech_end_sample"],
            item["visual_start_sample"],
            item["visual_end_sample"],
        )
        for item in intervals
    ] == [
        (0, 5000, 0, 5000),
        (5000, 9000, 5000, 9000),
    ]

    section = ExtractedSection(
        section_id="sec_001",
        section_order=1,
        section_title="Test",
        section_role="body",
        resolved_script_span=(0, len(authored)),
        script_excerpt=authored,
        pacing_density="high",
        planned_narration_wpm=220,
    )
    scenes = build_micro_scenes(
        section=section,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 0.1, "max": 20.0},
        visual_context_catalog=[
            {
                "context_id": "ctx_001",
                "presence_ranges": [
                    {
                        "section_id": section.section_id,
                        "resolved_script_span": list(section.resolved_script_span),
                    }
                ],
            }
        ],
    )
    assert [scene["script_text"] for scene in scenes] == [authored]

    scene_plan = {
        "project_id": "project_001",
        "scene_plan_revision_id": "artifact_scene",
        "source_script_revision_id": "artifact_script",
        "scenes": [
            {
                "scene_id": "scene_001",
                "scene_order": 1,
                "beats": [
                    {
                        "beat_id": "beat_001",
                        "beat_order": 1,
                        "local_beat_order": 1,
                        "resolved_script_span": [0, 2],
                    },
                    {
                        "beat_id": "beat_002",
                        "beat_order": 2,
                        "local_beat_order": 2,
                        "resolved_script_span": [2, 4],
                    },
                ],
            }
        ],
    }
    compiled_timing = compile_beat_timing_map(
        project_id="project_001",
        script_artifact_id="artifact_script",
        scene_plan_artifact_id="artifact_scene",
        narration_audio_artifact_id="artifact_audio",
        narration_word_timing_map_artifact_id="artifact_timing",
        scene_plan=scene_plan,
        word_timing_map=word_timing_map,
    )
    assert [
        (
            beat["speech_start_sample"],
            beat["speech_end_sample"],
            beat["visual_start_sample"],
            beat["visual_end_sample"],
        )
        for beat in compiled_timing["beats"]
    ] == [
        (0, 5000, 0, 5000),
        (5000, 9000, 5000, 9000),
    ]


def test_scene_timing_rejects_zero_duration_project_owned_word() -> None:
    authored = "Alpha beta"
    _timing_section, word_timing_map, _compiled = _timing_context(
        authored,
        overrides=[],
    )
    first_word = word_timing_map["sections"][0]["words"][0]
    first_word["global_end_sample"] = first_word["global_start_sample"]

    with pytest.raises(AppError) as exc_info:
        build_scene_timing_context(
            project_id="project_001",
            script_artifact_id="artifact_script",
            narration_audio_artifact_id="artifact_audio",
            narration_word_timing_map_artifact_id="artifact_timing",
            script={
                "script_sections": [
                    {"section_id": "sec_001", "narration": authored}
                ]
            },
            word_timing_map=word_timing_map,
        )
    assert exc_info.value.error_code == "BEAT_TIMING_MAP_INVALID"
    assert exc_info.value.details["global_start_sample"] == exc_info.value.details["global_end_sample"]


def test_multiple_overrides_preserve_inter_override_source_ownership() -> None:
    timing_section, word_timing_map, compiled = _timing_context(
        "A1xB2",
        overrides=[
            _override("override_a", 0, 2, "alpha"),
            _override("override_b", 3, 5, "beta"),
        ],
    )

    assert compiled["canonical_spoken_text"] == "alphaxbeta"
    atoms = timing_section["timing_atoms"]
    assert [
        (atom["kind"], atom["source_start"], atom["source_end"])
        for atom in atoms
    ] == [
        ("spoken_override", 0, 2),
        ("word", 2, 3),
        ("spoken_override", 3, 5),
    ]
    assert "".join(atom["source_text"] for atom in atoms) == "A1xB2"
    _assert_atoms_are_ordered(atoms)

    intervals = project_visual_intervals_for_source_spans(
        word_timing_map,
        [
            [int(atom["source_start"]), int(atom["source_end"])]
            for atom in atoms
        ],
    )
    assert len(intervals) == len(atoms)
    for left, right in zip(intervals, intervals[1:], strict=False):
        assert int(left["visual_end_sample"]) == int(
            right["visual_start_sample"]
        )


def test_ordinary_words_around_override_remain_ordered() -> None:
    authored = "Pay $19.50 now"
    start = authored.index("$")
    end = start + len("$19.50")
    timing_section, _word_timing_map, _compiled = _timing_context(
        authored,
        overrides=[
            _override(
                "override_currency",
                start,
                end,
                "nineteen dollars fifty cents",
            )
        ],
    )
    atoms = timing_section["timing_atoms"]

    assert [atom["kind"] for atom in atoms] == [
        "word",
        "spoken_override",
        "word",
    ]
    assert atoms[1]["source_text"] == "$19.50"
    _assert_atoms_are_ordered(atoms)


def test_delivery_cue_at_override_boundary_is_valid_but_inside_is_not() -> None:
    override = _override("override_001", 0, 4, "alpha beta")
    timing_section, _word_timing_map, _compiled = _timing_context(
        "A1B2",
        overrides=[override],
        delivery_cues=[
            {
                "id": "cue_after",
                "kind": "pause_after",
                "source_offset": 4,
                "pause_strength": "short",
            }
        ],
    )
    assert timing_section["timing_atoms"][0]["kind"] == "spoken_override"

    with pytest.raises(AppError) as exc_info:
        _timing_context(
            "A1B2",
            overrides=[override],
            delivery_cues=[
                {
                    "id": "cue_inside",
                    "kind": "pause_after",
                    "source_offset": 2,
                    "pause_strength": "short",
                }
            ],
        )
    assert exc_info.value.error_code == "VOICE_AUTHORED_BOUNDARY_UNSAFE"


def test_overlapping_spoken_overrides_fail_before_scene_timing() -> None:
    script = {
        "script_sections": [
            {
                "section_id": "sec_001",
                "narration": "A1B2",
                "spoken_overrides": [
                    _override("override_a", 0, 3, "alpha"),
                    _override("override_b", 2, 4, "beta"),
                ],
                "delivery_cues": [],
            }
        ]
    }
    with pytest.raises(AppError) as exc_info:
        compile_voice_spoken_text(script)
    assert exc_info.value.error_code == "VOICE_SPOKEN_TEXT_INVALID"
