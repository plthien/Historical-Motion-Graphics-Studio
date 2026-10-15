from __future__ import annotations

from typing import Any

from app.modules.platform.video_pacing import resolve_video_pacing_profile
from app.modules.v4.scene_breakdown_anchor_evidence import (
    authoritative_match_terms,
    build_anchor_assignment_spans,
    find_exact_text_spans,
)
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection, build_section_input
from app.modules.v4.scene_breakdown_validator import validate_and_normalize_section
from tests.support.fixtures import sample_pacing_profiles


def _style_with_character(anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "text_approved",
        "visual_style_snapshot": {
            "visual_identity": {"medium": ["Test medium"], "linework": ["Test linework"], "rendering": ["Test rendering"], "palette": ["Test palette"], "shape_language": [], "texture": [], "editorial_language": ["Use the clearest honest representation mode for the locked meaning."]},
            "entity_styles": {"character": {"appearance": [], "avoid": []}, "location": {"appearance": [], "avoid": []}, "prop": {"appearance": [], "avoid": []}, "recurring_visual_subject": {"appearance": [], "avoid": []}},
            "global_negative_constraints": [],
        },
        "characters": [anchor],
        "locations": [],
        "recurring_props": [],
        "recurring_visual_subjects": [],
        "motifs": [],
    }


def _character(**overrides: Any) -> dict[str, Any]:
    value = {
        "char_id": "char_001",
        "label": "Atlas Team",
        "description": "A recurring team.",
        "narrative_role": "main group",
        "explicit_visual_traits": [],
        "identity_markers": ["blue jackets"],
        "continuity_notes": ["appears across examples"],
        "approval_status": "text_approved",
        "occurrence_quotes": [
            {
                "quote": "Atlas Team",
                "context": "Atlas Team gathered early.",
                "resolved_script_position": 0,
                "section_id": "sec_001",
            }
        ],
        "presence_ranges": [
            {
                "section_id": "sec_001",
                "start_quote": "Atlas Team",
                "end_quote": "Atlas Team",
                "resolved_script_span": [0, 10],
            }
        ],
    }
    value.update(overrides)
    return value


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


def test_direct_alias_outside_presence_scope_does_not_self_authorize_reference() -> None:
    spans = build_anchor_assignment_spans(
        style_bible=_style_with_character(_character(approved_aliases=["triage unit"])),
        section_id="sec_001",
        section_excerpt="Atlas Team met the triage unit.",
        section_absolute_start=0,
    )
    assert spans["characters"]["char_001"] == [[0, 10]]


def test_direct_mention_matching_is_boundary_safe_and_case_insensitive() -> None:
    assert find_exact_text_spans("cat catalog Cat", "cat") == [[0, 3], [12, 15]]
    assert find_exact_text_spans("The CATALOG entry stayed uppercase.", "catalog") == [[4, 11]]


def test_visual_fields_are_not_authoritative_aliases() -> None:
    anchor = _character(label="Atlas Team", approved_aliases=[])
    terms = authoritative_match_terms(anchor)
    assert "Atlas Team" in terms
    assert "blue jackets" not in terms
    spans = build_anchor_assignment_spans(
        style_bible=_style_with_character(anchor),
        section_id="sec_002",
        section_excerpt="Blue jackets crossed the frame without the team name.",
        section_absolute_start=100,
    )
    assert spans["characters"] == {}


def test_materializer_uses_presence_scope_for_eligible_refs() -> None:
    text = "Later, atlas team returns with a cleaner plan."
    section = ExtractedSection("sec_002", 2, "Return", "body", (100, 100 + len(text)), text, "low", 155)
    style = _style_with_character(
        _character(
            presence_ranges=[
                {
                    "section_id": "sec_002",
                    "start_quote": text,
                    "end_quote": text,
                    "resolved_script_span": [100, 100 + len(text)],
                }
            ]
        )
    )
    analysis = {
        "narrative_markers": [],
        "visual_context_catalog": [{
            "context_id": "ctx_001",
            "presence_ranges": [{
                "section_id": "sec_002",
                "resolved_script_span": [100, 100 + len(text)],
            }],
        }],
    }
    section_input = build_section_input(
        section=section,
        script_analysis=analysis,
        style_bible=style,
        video_pacing_profile=resolve_video_pacing_profile(sample_pacing_profiles(), "medium"),
    )
    raw = {
        "scenes": [
            {
                "beats": [
                    {"script_text": text, "visual": {
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_001"], "selected_location_refs": [], "selected_prop_refs": [], "selected_recurring_visual_subject_refs": [], "selected_motif_refs": [],
                        "visual_concept": "Atlas Team returns in one generated image", "subject_action": "The team returns", "environment_emphasis": "A neutral setting", "visible_text": [],
                        "framing": "medium", "camera_angle": "eye_level", "camera_movement": "static", "composition": "The team is centered", "lighting_direction": None, "color_emphasis": None, "continuity_with_previous": "new_setup"
                    }}
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
        word_timing_map=_word_timing_map(text, start=100, section_id="sec_002"),
    )
    assert normalized["scenes"][0]["beats"][0]["eligible_character_refs"] == ["char_001"]
