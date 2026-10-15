from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.modules.platform.video_pacing import resolve_video_pacing_profile
from app.api.errors import AppError
from app.modules.v4.scene_breakdown_anchor_evidence import build_anchor_assignment_spans
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection, build_section_input
from app.modules.v4.scene_breakdown_validator import validate_and_normalize_section
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from tests.support.fixtures import sample_pacing_profiles


def _analysis_input(narration: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return (
        {
            "project_id": "project_aaaaaaaaaaaa",
            "source_script_revision_id": "artifact_bbbbbbbbbbbb",
            "script": {
                "sections": [
                    {
                        "section_id": "sec_001",
                        "narration": narration,
                    }
                ]
            },
        },
        [{"section_id": "sec_001", "start": 0, "end": len(narration)}],
    )


def _raw_character_analysis(
    narration: str,
    *,
    presence_ranges: list[dict[str, str]],
    occurrences: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    llm_input, ranges = _analysis_input(narration)
    if occurrences is None:
        occurrences = [
            {
                "section_id": "sec_001",
                "quote": "A baby wakes hungry.",
                "context_quote": "A baby wakes hungry.",
            },
            {
                "section_id": "sec_001",
                "quote": "The child eats.",
                "context_quote": "The child eats.",
            },
        ]
    raw = {
        "artifact_type": "script_analysis_llm_output",
        "schema_version": "1.0",
        "project_id": llm_input["project_id"],
        "source_script_revision_id": llm_input["source_script_revision_id"],
        "section_guidance": [{"section_id": "sec_001", "pacing_density": "medium"}],
        "narrative_markers": [],
        "continuity_catalog": {
            "characters": [
                {
                    "label": "Reconstructed baby",
                    "description": "The same reconstructed child across the practical scenario.",
                    "explicit_visual_traits": [],
                    "identity_markers": ["same reconstructed child"],
                    "continuity_notes": ["Keep one canonical identity."],
                    "physical_constraints": [],
                    "entity_kind": "human_character",
                    "narrative_role": "survival reconstruction subject",
                    "occurrences": occurrences,
                    "presence_ranges": presence_ranges,
                }
            ],
            "locations": [],
            "recurring_props": [],
            "recurring_visual_subjects": [],
            "visual_motifs": [],
        },
        "visual_context_catalog": [
            {
                "label": "Complete test narration",
                "mode": "representative_scenario",
                "timeframe": None,
                "setting": None,
                "constraints": [],
                "presence_ranges": [
                    {
                        "section_id": "sec_001",
                        "start_quote": narration,
                        "end_quote": narration,
                    }
                ],
            }
        ],
    }
    return raw, llm_input, ranges


def test_script_analysis_resolves_broad_presence_range_without_per_sentence_occurrences() -> None:
    narration = "A baby wakes hungry. The provider prepares food. The child eats."
    raw, llm_input, ranges = _raw_character_analysis(
        narration,
        presence_ranges=[
            {
                "section_id": "sec_001",
                "start_quote": "A baby wakes hungry.",
                "end_quote": "The child eats.",
            }
        ],
    )
    canonical = ScriptAnalysisSemanticValidator().build_canonical(
        llm_input=llm_input,
        raw_output=raw,
        section_ranges=ranges,
    )
    character = canonical["continuity_catalog"]["characters"][0]
    assert len(character["occurrences"]) == 2
    assert character["presence_ranges"] == [
        {
            "section_id": "sec_001",
            "start_quote": "A baby wakes hungry.",
            "end_quote": "The child eats.",
            "resolved_script_span": [0, len(narration)],
        }
    ]


def test_script_analysis_assigns_deterministic_visual_context_ids_and_preserves_null_authority() -> None:
    narration = "A baby wakes hungry. The provider prepares food. The child eats."
    raw, llm_input, ranges = _raw_character_analysis(
        narration,
        presence_ranges=[{"section_id": "sec_001", "start_quote": "A baby wakes hungry.", "end_quote": "The child eats."}],
    )
    raw["visual_context_catalog"] = [
        {
            "label": "Opening reconstruction",
            "mode": "reconstruction",
            "timeframe": None,
            "setting": None,
            "constraints": [],
            "presence_ranges": [{"section_id": "sec_001", "start_quote": "A baby wakes hungry.", "end_quote": "A baby wakes hungry."}],
        },
        {
            "label": "Food scenario",
            "mode": "representative_scenario",
            "timeframe": "generic prehistory",
            "setting": "temporary shelter",
            "constraints": ["keep the action physically plausible"],
            "presence_ranges": [{"section_id": "sec_001", "start_quote": "The provider prepares food.", "end_quote": "The child eats."}],
        },
    ]
    canonical = ScriptAnalysisSemanticValidator().build_canonical(
        llm_input=llm_input,
        raw_output=raw,
        section_ranges=ranges,
    )
    assert [item["context_id"] for item in canonical["visual_context_catalog"]] == ["ctx_001", "ctx_002"]
    assert canonical["visual_context_catalog"][0]["timeframe"] is None
    assert canonical["visual_context_catalog"][0]["setting"] is None


@pytest.mark.parametrize(
    ("catalog", "error_code"),
    [
        (
            [
                {"label": "Same", "mode": "story_world", "timeframe": None, "setting": None, "constraints": [], "presence_ranges": [{"section_id": "sec_001", "start_quote": "A baby wakes hungry.", "end_quote": "A baby wakes hungry."}]},
                {"label": " same ", "mode": "story_world", "timeframe": None, "setting": None, "constraints": [], "presence_ranges": [{"section_id": "sec_001", "start_quote": "The provider prepares food.", "end_quote": "The child eats."}]},
            ],
            "SCRIPT_ANALYSIS_VISUAL_CONTEXT_DUPLICATE",
        ),
        (
            [
                {"label": "First", "mode": "story_world", "timeframe": None, "setting": None, "constraints": [], "presence_ranges": [{"section_id": "sec_001", "start_quote": "A baby wakes hungry.", "end_quote": "The provider prepares food."}]},
                {"label": "Second", "mode": "evidence_view", "timeframe": None, "setting": None, "constraints": [], "presence_ranges": [{"section_id": "sec_001", "start_quote": "The provider prepares food.", "end_quote": "The child eats."}]},
            ],
            "SCRIPT_ANALYSIS_VISUAL_CONTEXT_OVERLAP",
        ),
        (
            [
                {"label": "Partial", "mode": "story_world", "timeframe": None, "setting": None, "constraints": [], "presence_ranges": [{"section_id": "sec_001", "start_quote": "A baby wakes hungry.", "end_quote": "A baby wakes hungry."}]},
            ],
            "SCRIPT_ANALYSIS_VISUAL_CONTEXT_COVERAGE_INVALID",
        ),
    ],
)
def test_script_analysis_rejects_invalid_visual_context_partition(catalog: list[dict[str, Any]], error_code: str) -> None:
    narration = "A baby wakes hungry. The provider prepares food. The child eats."
    raw, llm_input, ranges = _raw_character_analysis(
        narration,
        presence_ranges=[{"section_id": "sec_001", "start_quote": "A baby wakes hungry.", "end_quote": "The child eats."}],
    )
    raw["visual_context_catalog"] = catalog
    with pytest.raises(AppError) as exc_info:
        ScriptAnalysisSemanticValidator().build_canonical(
            llm_input=llm_input,
            raw_output=raw,
            section_ranges=ranges,
        )
    assert exc_info.value.error_code == error_code


def test_script_analysis_allows_multiple_nonoverlapping_presence_ranges_for_one_entity() -> None:
    narration = "A baby wakes hungry. Generic Neanderthal infant evidence. The child eats."
    raw, llm_input, ranges = _raw_character_analysis(
        narration,
        presence_ranges=[
            {
                "section_id": "sec_001",
                "start_quote": "A baby wakes hungry.",
                "end_quote": "A baby wakes hungry.",
            },
            {
                "section_id": "sec_001",
                "start_quote": "The child eats.",
                "end_quote": "The child eats.",
            },
        ],
    )
    canonical = ScriptAnalysisSemanticValidator().build_canonical(
        llm_input=llm_input,
        raw_output=raw,
        section_ranges=ranges,
    )
    spans = [item["resolved_script_span"] for item in canonical["continuity_catalog"]["characters"][0]["presence_ranges"]]
    assert len(spans) == 2
    detour_start = narration.index("Generic")
    detour_end = narration.index("The child")
    assert spans[0][1] <= detour_start
    assert spans[1][0] >= detour_end


def test_script_analysis_rejects_direct_occurrence_outside_presence_scope() -> None:
    narration = "A baby wakes hungry. The provider prepares food. The child eats."
    raw, llm_input, ranges = _raw_character_analysis(
        narration,
        presence_ranges=[
            {
                "section_id": "sec_001",
                "start_quote": "A baby wakes hungry.",
                "end_quote": "A baby wakes hungry.",
            }
        ],
    )
    with pytest.raises(AppError) as exc_info:
        ScriptAnalysisSemanticValidator().build_canonical(
            llm_input=llm_input,
            raw_output=raw,
            section_ranges=ranges,
        )
    assert exc_info.value.error_code == "SCRIPT_ANALYSIS_OCCURRENCE_OUTSIDE_PRESENCE_RANGE"


def test_script_analysis_rejects_ambiguous_presence_boundary_quote() -> None:
    narration = "Baby waits. Baby waits again."
    occurrences = [
        {"section_id": "sec_001", "quote": "Baby waits.", "context_quote": "Baby waits."},
        {"section_id": "sec_001", "quote": "Baby waits again.", "context_quote": "Baby waits again."},
    ]
    raw, llm_input, ranges = _raw_character_analysis(
        narration,
        occurrences=occurrences,
        presence_ranges=[
            {"section_id": "sec_001", "start_quote": "Baby", "end_quote": "Baby waits again."}
        ],
    )
    with pytest.raises(AppError) as exc_info:
        ScriptAnalysisSemanticValidator().build_canonical(
            llm_input=llm_input,
            raw_output=raw,
            section_ranges=ranges,
        )
    assert exc_info.value.error_code == "SCRIPT_ANALYSIS_PRESENCE_RANGE_QUOTE_AMBIGUOUS"


def _anchor(kind: str, text: str) -> tuple[str, dict[str, Any]]:
    span = [0, len(text)]
    base = {
        "label": f"Canonical {kind}",
        "description": f"Canonical {kind} used in the reconstruction.",
        "explicit_visual_traits": [],
        "continuity_notes": [],
        "approval_status": "text_approved",
        "presence_ranges": [
            {
                "section_id": "sec_001",
                "start_quote": text,
                "end_quote": text,
                "resolved_script_span": span,
            }
        ],
        "occurrence_quotes": [
            {
                "section_id": "sec_002",
                "quote": "Different direct anchor",
                "context": "Different direct anchor",
                "resolved_script_position": 1000,
            }
        ],
    }
    if kind == "character":
        return "characters", {**base, "char_id": "char_001", "identity_markers": [], "entity_kind": "human_character", "narrative_role": "subject"}
    if kind == "location":
        return "locations", {**base, "loc_id": "loc_001", "identity_markers": [], "location_kind": "setting"}
    if kind == "prop":
        return "recurring_props", {**base, "prop_id": "prop_001", "identity_markers": [], "prop_kind": "object", "significance": "test"}
    return "recurring_visual_subjects", {**base, "subject_id": "subject_001", "visual_type": "process"}


@pytest.mark.parametrize(
    ("kind", "evidence_category", "ref_id"),
    [
        ("character", "characters", "char_001"),
        ("location", "locations", "loc_001"),
        ("prop", "recurring_props", "prop_001"),
        ("subject", "recurring_visual_subjects", "subject_001"),
    ],
)
def test_presence_ranges_are_assignment_authority_for_all_entity_categories(kind: str, evidence_category: str, ref_id: str) -> None:
    text = "The narration continues without repeating the canonical label."
    collection, anchor = _anchor(kind, text)
    style: dict[str, Any] = {
        "characters": [],
        "locations": [],
        "recurring_props": [],
        "recurring_visual_subjects": [],
        "motifs": [],
    }
    style[collection] = [anchor]
    spans = build_anchor_assignment_spans(
        style_bible=style,
        section_id="sec_001",
        section_excerpt=text,
        section_absolute_start=0,
    )
    assert spans[evidence_category][ref_id] == [[0, len(text)]]


def _visual(selected_character_refs: list[str], *, continuity: str = "new_setup") -> dict[str, Any]:
    return {
        "representation_family": "character_environment_lived",
        "selected_character_refs": selected_character_refs,
        "selected_location_refs": [],
        "selected_prop_refs": [],
        "selected_recurring_visual_subject_refs": [],
        "selected_motif_refs": [],
        "visual_concept": "A clear reconstruction frame.",
        "subject_action": "The child waits beside the provider.",
        "environment_emphasis": "A simple camp background.",
        "visible_text": [],
        "framing": "medium",
        "camera_angle": "eye_level",
        "camera_movement": "static",
        "composition": "The child is centered with open space around the figure.",
        "lighting_direction": None,
        "color_emphasis": None,
        "continuity_with_previous": continuity,
    }


def _style_for_scene_presence(text: str, range_end: int) -> dict[str, Any]:
    return {
        "status": "text_approved",
        "visual_style_snapshot": {
            "visual_identity": {
                "medium": ["Test medium"],
                "linework": ["Test linework"],
                "rendering": ["Test rendering"],
                "palette": ["Test palette"],
                "shape_language": [],
                "texture": [],
                "editorial_language": ["Keep editorial representation source-safe and readable."],
            },
            "entity_styles": {
                "character": {"appearance": [], "avoid": []},
                "location": {"appearance": [], "avoid": []},
                "prop": {"appearance": [], "avoid": []},
                "recurring_visual_subject": {"appearance": [], "avoid": []},
            },
            "global_negative_constraints": [],
        },
        "characters": [
            {
                "char_id": "char_001",
                "label": "Reconstructed baby",
                "description": "The same reconstructed child.",
                "narrative_role": "subject",
                "explicit_visual_traits": [],
                "identity_markers": [],
                "continuity_notes": [],
                "approval_status": "text_approved",
                "occurrence_quotes": [
                    {
                        "quote": "Direct anchor elsewhere",
                        "context": "Direct anchor elsewhere",
                        "resolved_script_position": 1000,
                        "section_id": "sec_002",
                    }
                ],
                "presence_ranges": [
                    {
                        "section_id": "sec_001",
                        "start_quote": text[:range_end],
                        "end_quote": text[:range_end],
                        "resolved_script_span": [0, range_end],
                    }
                ],
            }
        ],
        "locations": [],
        "recurring_props": [],
        "recurring_visual_subjects": [],
        "motifs": [],
    }


def _word_timing_map(text: str, *, start: int = 0, section_id: str = "sec_001") -> dict[str, Any]:
    words: list[dict[str, int]] = []
    cursor = 0
    sample_rate = 48_000
    for index, token in enumerate(text.split()):
        local_start = text.index(token, cursor)
        local_end = local_start + len(token)
        words.append(
            {
                "authored_start": start + local_start,
                "authored_end": start + local_end,
                "global_start_sample": index * sample_rate,
                "global_end_sample": (index + 1) * sample_rate,
            }
        )
        cursor = local_end
    return {
        "sample_rate_hz": sample_rate,
        "total_sample_count": max(sample_rate, len(words) * sample_rate),
        "sections": [{"section_id": section_id, "words": words}],
    }


def test_scene_breakdown_new_setup_accepts_reference_from_presence_range_without_exact_name() -> None:
    text = "The child waits quietly."
    style = _style_for_scene_presence(text, len(text))
    section = ExtractedSection("sec_001", 1, "Test", "body", (0, len(text)), text, "medium", 155)
    analysis = {
        "narrative_markers": [],
        "visual_context_catalog": [{
            "context_id": "ctx_001",
            "presence_ranges": [{"section_id": "sec_001", "resolved_script_span": [0, len(text)]}],
        }],
    }
    section_input = build_section_input(
        section=section,
        script_analysis=analysis,
        style_bible=style,
        video_pacing_profile=resolve_video_pacing_profile(sample_pacing_profiles(), "medium"),
    )
    descriptor = section_input["visual_context"]["reference_catalog"]["characters"]["char_001"]
    assert descriptor["evidence_script_texts"] == []
    assert descriptor["presence_ranges"][0]["section_id"] == "sec_001"
    raw = {
        "scenes": [
            {
                "beats": [
                    {
                        "script_text": text,
                        "visual": _visual(["char_001"]),
                    }
                ],
            }
        ]
    }
    normalized = validate_and_normalize_section(
        raw_output=raw,
        section=section,
        section_input=section_input,
        script_analysis=analysis,
        style_bible=style,
        word_timing_map=_word_timing_map(text),
    )
    assert normalized["scenes"][0]["beats"][0]["eligible_character_refs"] == ["char_001"]


def test_scene_breakdown_rejects_same_reference_outside_presence_range_at_new_scene_boundary() -> None:
    first = "The child waits quietly."
    second = " Generic infant evidence follows."
    text = first + second
    style = _style_for_scene_presence(text, len(first))
    section = ExtractedSection("sec_001", 1, "Test", "body", (0, len(text)), text, "medium", 155)
    section_input = build_section_input(
        section=section,
        script_analysis={"narrative_markers": []},
        style_bible=style,
        video_pacing_profile=resolve_video_pacing_profile(sample_pacing_profiles(), "medium"),
    )
    raw = {
        "scenes": [
            {
                "beats": [
                    {"script_text": first, "visual": _visual(["char_001"])}
                ],
            },
            {
                "beats": [
                    {"script_text": second, "visual": _visual(["char_001"])}
                ],
            },
        ]
    }
    with pytest.raises(Exception) as exc_info:
        validate_and_normalize_section(
            raw_output=raw,
            section=section,
            section_input=section_input,
            style_bible=style,
            word_timing_map=_word_timing_map(text),
        )
    error = exc_info.value
    report = getattr(error, "report", None)
    assert report is not None
    assert any(issue["code"] == "visual_reference_unsupported" for issue in report["semantic_errors"])
