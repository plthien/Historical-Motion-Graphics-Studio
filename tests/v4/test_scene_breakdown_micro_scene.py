from __future__ import annotations

import re

import pytest

from app.api.errors import AppError
from app.modules.v4.beat_timing_map import build_scene_timing_context
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection
from app.modules.v4.scene_breakdown_micro_scene import build_micro_scenes as _build_micro_scenes


def _section(text: str = "One. Two; Three.") -> ExtractedSection:
    return ExtractedSection(
        section_id="sec_001",
        section_order=1,
        section_title="Test",
        section_role="body",
        resolved_script_span=(0, len(text)),
        script_excerpt=text,
        pacing_density="high",
        planned_narration_wpm=220,
    )


def build_micro_scenes(*, section: ExtractedSection, **kwargs: object) -> list[dict]:
    return _build_micro_scenes(
        section=section,
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
        **kwargs,
    )


def _timing(text: str = "One. Two; Three.") -> dict:
    assert text == "One. Two; Three."
    return {
        "section_id": "sec_001",
        "source_start": 0,
        "source_end": len(text),
        "sample_rate_hz": 48000,
        "timing_atoms": [
            {"source_start": 0, "source_end": 4, "speech_start_sample": 0, "speech_end_sample": 48000, "end_boundary_safe": True},
            {"source_start": 5, "source_end": 9, "speech_start_sample": 57600, "speech_end_sample": 105600, "end_boundary_safe": True},
            {"source_start": 10, "source_end": 16, "speech_start_sample": 115200, "speech_end_sample": 163200, "end_boundary_safe": True},
        ],
        "clauses": [
            {"source_end": 4, "text": "One.", "end_boundary_safe": True},
            {"source_end": 9, "text": "Two;", "end_boundary_safe": True},
            {"source_end": 16, "text": "Three.", "end_boundary_safe": True},
        ],
    }

def _word_timing() -> dict:
    return {
        "sample_rate_hz": 48000,
        "total_sample_count": 163200,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_spoken_mapping": [],
                "words": [
                    {"authored_start": 0, "authored_end": 4, "global_start_sample": 0, "global_end_sample": 48000},
                    {"authored_start": 5, "authored_end": 9, "global_start_sample": 57600, "global_end_sample": 105600},
                    {"authored_start": 10, "authored_end": 16, "global_start_sample": 115200, "global_end_sample": 163200},
                ],
            }
        ],
    }



def test_normal_clauses_become_ordered_micro_scenes() -> None:
    scenes = build_micro_scenes(
        section=_section(),
        timing_section=_timing(),
        word_timing_map=_word_timing(),
        preferred_hold_seconds={"min": 0.8, "max": 1.4},
    )
    assert [item["scene_key"] for item in scenes] == [
        "sec_001_scene_001",
        "sec_001_scene_002",
        "sec_001_scene_003",
    ]
    assert "".join(item["script_text"] for item in scenes) == "One. Two; Three."
    assert [item["source_start"] for item in scenes] == [0, 4, 9]
    assert [item["source_end"] for item in scenes] == [4, 9, 16]


def test_visual_context_transition_forces_safe_micro_scene_cut_without_changing_setup() -> None:
    text = "One. Two."
    section = _section(text)
    timing_section = {
        "section_id": "sec_001",
        "source_start": 0,
        "source_end": len(text),
        "sample_rate_hz": 48000,
        "timing_atoms": [
            {"source_start": 0, "source_end": 4, "speech_start_sample": 0, "speech_end_sample": 48000, "end_boundary_safe": True},
            {"source_start": 5, "source_end": 9, "speech_start_sample": 48000, "speech_end_sample": 96000, "end_boundary_safe": True},
        ],
        "clauses": [{"source_end": 9, "text": text, "end_boundary_safe": True}],
    }
    word_timing_map = {
        "sample_rate_hz": 48000,
        "total_sample_count": 96000,
        "sections": [{
            "section_id": "sec_001",
            "authored_spoken_mapping": [],
            "words": [
                {"authored_start": 0, "authored_end": 4, "global_start_sample": 0, "global_end_sample": 48000},
                {"authored_start": 5, "authored_end": 9, "global_start_sample": 48000, "global_end_sample": 96000},
            ],
        }],
    }
    scenes = _build_micro_scenes(
        section=section,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 2.0, "max": 3.0},
        visual_context_catalog=[
            {"context_id": "ctx_001", "presence_ranges": [{"section_id": "sec_001", "resolved_script_span": [0, 4]}]},
            {"context_id": "ctx_002", "presence_ranges": [{"section_id": "sec_001", "resolved_script_span": [5, 9]}]},
        ],
    )
    assert [scene["visual_context_ref"] for scene in scenes] == ["ctx_001", "ctx_002"]
    assert "continuity_with_previous" not in scenes[1]


def test_visual_context_transition_rejects_unsafe_timing_boundary() -> None:
    section = _section("One. Two.")
    with pytest.raises(AppError) as exc_info:
        _build_micro_scenes(
            section=section,
            timing_section={
                "section_id": "sec_001",
                "source_start": 0,
                "source_end": 9,
                "sample_rate_hz": 48000,
                "timing_atoms": [{"source_start": 0, "source_end": 9, "speech_start_sample": 0, "speech_end_sample": 96000, "end_boundary_safe": True}],
                "clauses": [{"source_end": 9, "text": "One. Two.", "end_boundary_safe": True}],
            },
            word_timing_map={
                "sample_rate_hz": 48000,
                "total_sample_count": 96000,
                "sections": [{"section_id": "sec_001", "authored_spoken_mapping": [], "words": [{"authored_start": 0, "authored_end": 9, "global_start_sample": 0, "global_end_sample": 96000}]}],
            },
            preferred_hold_seconds={"min": 0.1, "max": 3.0},
            visual_context_catalog=[
                {"context_id": "ctx_001", "presence_ranges": [{"section_id": "sec_001", "resolved_script_span": [0, 4]}]},
                {"context_id": "ctx_002", "presence_ranges": [{"section_id": "sec_001", "resolved_script_span": [5, 9]}]},
            ],
        )
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_VISUAL_CONTEXT_BOUNDARY_INVALID"


def test_tiny_segments_accumulate_and_final_remainder_merges_backward() -> None:
    scenes = build_micro_scenes(
        section=_section(),
        timing_section=_timing(),
        word_timing_map=_word_timing(),
        preferred_hold_seconds={"min": 1.5, "max": 2.5},
    )
    assert len(scenes) == 1
    assert scenes[0]["source_start"] == 0
    assert scenes[0]["source_end"] == 16
    assert scenes[0]["script_text"] == "One. Two; Three."
    assert all(item["duration_seconds"] >= 1.5 for item in scenes)


def test_entire_section_shorter_than_preferred_minimum_fails_closed() -> None:
    with pytest.raises(AppError) as exc_info:
        build_micro_scenes(
            section=_section(),
            timing_section=_timing(),
            word_timing_map=_word_timing(),
            preferred_hold_seconds={"min": 4.0, "max": 5.0},
        )
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_MICRO_SCENE_MINIMUM_HOLD_UNSATISFIABLE"


@pytest.mark.parametrize(
    "preferred",
    [
        {"min": 2.0, "max": 2.0},
        {"min": 3.0, "max": 2.0},
        {"min": 0.0, "max": 2.0},
        {"min": True, "max": 2.0},
        {"min": 1.0, "max": float("inf")},
    ],
)
def test_micro_scene_planner_rejects_invalid_preferred_hold_ranges(preferred: dict) -> None:
    with pytest.raises(AppError) as exc_info:
        build_micro_scenes(
            section=_section(),
            timing_section=_timing(),
            word_timing_map=_word_timing(),
            preferred_hold_seconds=preferred,
        )
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_MICRO_SCENE_GUIDANCE_INVALID"


def test_semicolon_is_natural_but_ordinary_word_endpoint_is_not_a_scene_cut() -> None:
    text = "Alpha beta; Gamma delta"
    timing_section, word_timing_map = _context_for_text(text)
    scenes = build_micro_scenes(
        section=_section(text),
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 0.1, "max": 20.0},
    )
    assert [item["script_text"] for item in scenes] == ["Alpha beta;", " Gamma delta"]
    assert all(item["script_text"] != "Alpha" for item in scenes)


def test_unsafe_clause_boundary_is_not_used() -> None:
    timing = _timing()
    timing["clauses"][0]["end_boundary_safe"] = False
    scenes = build_micro_scenes(
        section=_section(),
        timing_section=timing,
        word_timing_map=_word_timing(),
        preferred_hold_seconds={"min": 0.8, "max": 1.4},
    )
    assert scenes[0]["source_end"] == 9
    assert "".join(item["script_text"] for item in scenes) == "One. Two; Three."


def test_long_sentence_is_not_split_arbitrarily() -> None:
    text = "A deliberately long sentence with many words but no authored internal clause boundary."
    section = _section(text)
    timing = {
        "section_id": "sec_001",
        "source_start": 0,
        "source_end": len(text),
        "sample_rate_hz": 48000,
        "timing_atoms": [
            {
                "source_start": 0,
                "source_end": len(text),
                "speech_start_sample": 0,
                "speech_end_sample": 480000,
                "end_boundary_safe": True,
            }
        ],
        "clauses": [{"source_end": len(text), "text": text, "end_boundary_safe": True}],
    }
    word_timing = {
        "sample_rate_hz": 48000,
        "total_sample_count": 480000,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_spoken_mapping": [],
                "words": [
                    {
                        "authored_start": 0,
                        "authored_end": len(text),
                        "global_start_sample": 0,
                        "global_end_sample": 480000,
                    }
                ],
            }
        ],
    }
    scenes = build_micro_scenes(
        section=section,
        timing_section=timing,
        word_timing_map=word_timing,
        preferred_hold_seconds={"min": 1.9, "max": 2.4},
    )
    assert len(scenes) == 1
    assert scenes[0]["script_text"] == text


def _context_for_text(
    text: str,
    *,
    mappings: list[dict] | None = None,
) -> tuple[dict, dict]:
    token_matches = list(re.finditer(r"\S+", text))
    words = [
        {
            "text": match.group(0),
            "authored_start": match.start(),
            "authored_end": match.end(),
            "global_start_sample": index * 48000,
            "global_end_sample": index * 48000 + 24000,
        }
        for index, match in enumerate(token_matches)
    ]
    total_samples = max(48000, len(words) * 48000)
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
                "authored_section_end": len(text),
                "sample_count": total_samples,
                "authored_spoken_mapping": list(mappings or []),
                "words": words,
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
                {
                    "section_id": "sec_001",
                    "narration": text,
                }
            ]
        },
        word_timing_map=word_timing_map,
    )
    return context["sections"][0], word_timing_map


def _assert_micro_scene_invariants(
    scenes: list[dict],
    *,
    text: str,
) -> None:
    assert scenes
    assert [item["local_scene_order"] for item in scenes] == list(
        range(1, len(scenes) + 1)
    )
    assert scenes[0]["source_start"] == 0
    assert scenes[-1]["source_end"] == len(text)
    assert "".join(item["script_text"] for item in scenes) == text
    assert all(item["source_end"] > item["source_start"] for item in scenes)
    assert all(item["script_text"].strip() for item in scenes)
    for left, right in zip(scenes, scenes[1:], strict=False):
        assert left["source_end"] == right["source_start"]


@pytest.mark.parametrize(
    ("text", "expected_scene_texts"),
    [
        ("First\nSecond.", ["First", "\nSecond."]),
        ("A\nB\nC", ["A", "\nB", "\nC"]),
        ("First\n\nSecond.", ["First", "\n\nSecond."]),
        ("First.\nSecond.", ["First.", "\nSecond."]),
        ("First;\nSecond.", ["First;", "\nSecond."]),
        ("What?!\nNext.", ["What?!", "\nNext."]),
        ("Wait...\nNext.", ["Wait...", "\nNext."]),
        ('"Stop."\nNext.', ['"Stop."', "\nNext."]),
        ("Thing (really.)\nNext.", ["Thing (really.)", "\nNext."]),
        ('"Stop." Next.', ['"Stop."', ' Next.']),
        ("Thing (really.) Next.", ["Thing (really.)", " Next."]),
        ("One; Two.", ["One;", " Two."]),
        ("What?! Next.", ["What?!", " Next."]),
        ("Wait... Next.", ["Wait...", " Next."]),
        ("One.   Two.", ["One.", "   Two."]),
        ("One.   ", ["One.   "]),
        ("Short; this is much longer.", ["Short;", " this is much longer."]),
        ("Final sentence.\n\n", ["Final sentence.\n\n"]),
    ],
)
def test_micro_scene_clause_boundaries_preserve_exact_source(
    text: str,
    expected_scene_texts: list[str],
) -> None:
    timing_section, word_timing_map = _context_for_text(text)
    section = _section(text)
    scenes = build_micro_scenes(
        section=section,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 0.1, "max": 20.0},
    )

    assert [item["script_text"] for item in scenes] == expected_scene_texts
    _assert_micro_scene_invariants(scenes, text=text)


def test_micro_scene_spoken_override_crossing_newline_blocks_internal_clause_cut() -> None:
    text = "Alpha\nBeta."
    timing_section, word_timing_map = _context_for_text(
        text,
        mappings=[
            {
                "authored_start": 0,
                "authored_end": len(text),
                "spoken_start": 0,
                "spoken_end": len(text),
            }
        ],
    )
    section = _section(text)

    assert any(
        int(clause["source_end"]) == len("Alpha")
        and clause["end_boundary_safe"] is False
        for clause in timing_section["clauses"]
    )

    scenes = build_micro_scenes(
        section=section,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 0.1, "max": 20.0},
    )

    assert [item["script_text"] for item in scenes] == [text]
    _assert_micro_scene_invariants(scenes, text=text)


def test_micro_scene_spoken_override_crossing_punctuation_blocks_internal_clause_cut() -> None:
    text = "Code X.204 now."
    timing_section, word_timing_map = _context_for_text(
        text,
        mappings=[
            {
                "authored_start": 5,
                "authored_end": 10,
                "spoken_start": 5,
                "spoken_end": 10,
            }
        ],
    )
    section = _section(text)

    assert any(
        int(clause["source_end"]) == 7
        and clause["end_boundary_safe"] is False
        for clause in timing_section["clauses"]
    )
    scenes = build_micro_scenes(
        section=section,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 0.1, "max": 20.0},
    )

    assert len(scenes) == 1
    assert scenes[0]["script_text"] == text
    _assert_micro_scene_invariants(scenes, text=text)


@pytest.mark.parametrize(
    "text",
    [
        "U.S. team moved.",
        "Value 2.5 stayed stable.",
        "Height 1.77 remained.",
        "Code X.204 appeared.",
        "Price $19.50 stayed at 50%.",
    ],
)
def test_micro_scene_internal_boundaries_are_safe_timing_atom_endpoints(
    text: str,
) -> None:
    timing_section, word_timing_map = _context_for_text(text)
    section = _section(text)
    scenes = build_micro_scenes(
        section=section,
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 0.1, "max": 20.0},
    )

    safe_atom_ends = {
        int(atom["source_end"])
        for atom in timing_section["timing_atoms"]
        if atom.get("end_boundary_safe") is True
    }
    assert all(
        int(scene["source_end"]) in safe_atom_ends
        for scene in scenes[:-1]
    )
    _assert_micro_scene_invariants(scenes, text=text)

    token_ranges = [
        (match.start(), match.end())
        for match in re.finditer(r"\S+", text)
    ]
    for scene in scenes[:-1]:
        endpoint = int(scene["source_end"])
        assert not any(start < endpoint < end for start, end in token_ranges)


@pytest.mark.parametrize(
    "text",
    [
        "Dr. Smith arrived.",
        "Mr. Jones waited.",
        "U.S. teams moved.",
        "e.g. tools remained.",
        "i.e. the same case continued.",
    ],
)
def test_abbreviation_endpoint_does_not_force_tiny_scene(
    text: str,
) -> None:
    timing_section, word_timing_map = _context_for_text(text)
    scenes = build_micro_scenes(
        section=_section(text),
        timing_section=timing_section,
        word_timing_map=word_timing_map,
        preferred_hold_seconds={"min": 0.1, "max": 20.0},
    )

    assert len(scenes) >= 1
    assert not scenes[0]["script_text"].strip() in {"Dr.", "Mr.", "U.S.", "e.g.", "i.e."}
    _assert_micro_scene_invariants(scenes, text=text)
