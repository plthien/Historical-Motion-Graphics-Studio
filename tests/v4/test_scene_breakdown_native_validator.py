from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.api.errors import AppError
from app.modules.platform.video_pacing import resolve_video_pacing_profile
from tests.support.fixtures import sample_pacing_profiles
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection, build_section_input
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.scene_breakdown_validator import (
    SceneBreakdownValidationError,
    validate_and_normalize_section,
    validate_reusable_section_fragment,
)


PLANNED_WPM = 155
VIDEO_PACING_PROFILE = resolve_video_pacing_profile(sample_pacing_profiles(), "medium")
STYLE = {
    "status": "text_approved",
    "visual_style_snapshot": {
        "visual_identity": {
            "medium": ["Test medium"],
            "linework": ["Test linework"],
            "rendering": ["Test rendering"],
            "palette": ["Test palette"],
            "shape_language": [],
            "texture": [],
            "editorial_language": ["Use the clearest honest representation mode for the locked meaning."],
        },
        "entity_styles": {
            "character": {"appearance": [], "avoid": []},
            "location": {"appearance": [], "avoid": []},
            "prop": {"appearance": [], "avoid": []},
            "recurring_visual_subject": {"appearance": [], "avoid": []},
        },
        "global_negative_constraints": [],
    },
    "characters": [],
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


def _section(
    text: str,
    *,
    start: int = 0,
    section_role: str = "body",
) -> ExtractedSection:
    return ExtractedSection(
        section_id="sec_001",
        section_order=1,
        section_title="Native",
        section_role=section_role,
        resolved_script_span=(start, start + len(text)),
        script_excerpt=text,
        pacing_density="medium",
        planned_narration_wpm=PLANNED_WPM,
    )


def _analysis(text: str, *, start: int = 0) -> dict[str, Any]:
    return {
        "narrative_markers": [],
        "visual_context_catalog": [
            {
                "context_id": "ctx_001",
                "presence_ranges": [
                    {
                        "section_id": "sec_001",
                        "resolved_script_span": [start, start + len(text)],
                    }
                ],
            }
        ],
    }


def _input(text: str, *, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    effective_analysis = analysis or _analysis(text)
    return build_section_input(
        section=_section(text),
        script_analysis=effective_analysis,
        style_bible=STYLE,
        video_pacing_profile=VIDEO_PACING_PROFILE,
    )


def _visual(index: int) -> dict[str, Any]:
    return {
        "representation_family": "character_environment_lived",
        "selected_character_refs": [],
        "selected_location_refs": [],
        "selected_prop_refs": [],
        "selected_recurring_visual_subject_refs": [],
        "selected_motif_refs": [],
        "visual_concept": "Test generated image",
        "subject_action": "Test action",
        "environment_emphasis": "Test setting",
        "visible_text": [],
        "framing": "medium",
        "camera_angle": "eye_level",
        "camera_movement": "static",
        "composition": "Test composition",
        "lighting_direction": None,
        "color_emphasis": None,
        "continuity_with_previous": "new_setup" if index == 0 else "same_setup",
    }


def _raw(*parts: str) -> dict[str, Any]:
    return {
        "scenes": [
            {
                "beats": [
                    {"script_text": part, "visual": _visual(index)}
                    for index, part in enumerate(parts)
                ],
            }
        ]
    }


def _validate(text: str, raw: dict[str, Any], *, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    effective_analysis = analysis or _analysis(text)
    return validate_and_normalize_section(
        raw_output=raw,
        section=_section(text),
        section_input=_input(text, analysis=effective_analysis),
        script_analysis=effective_analysis,
        style_bible=STYLE,
        word_timing_map=_word_timing_map(text),
    )


def _previous_fragment(*, composition: str, subject_action: str) -> dict[str, Any]:
    return {
        "scenes": [
            {
                "beats": [
                    {
                        "visual": {
                            "visual_concept": "Previous generated image concept.",
                            "subject_action": subject_action,
                            "selected_character_refs": [],
                            "selected_location_refs": [],
                            "selected_prop_refs": [],
                            "selected_recurring_visual_subject_refs": [],
                            "selected_motif_refs": ["motif_003"],
                            "framing": "graphic_layout",
                            "composition": composition,
                        }
                    }
                ]
            }
        ]
    }


def _long_carry_values() -> tuple[str, str]:
    composition_detail = (
        "Detailed continuity composition preserves exact placement, scale, contact geometry, separated depth, "
        "current-state staging, readable negative space, and the generated editorial arrangement without mutation. "
    )
    action_detail = (
        "The current subject state preserves the exact visible action, body and load relationship, hand contact, "
        "object orientation, physical state, and transient performance information required by the next section. "
    )
    return (
        "UNIQUE_START_" + (composition_detail * 4) + "_UNIQUE_END",
        "UNIQUE_ACTION_START_" + (action_detail * 2) + "_UNIQUE_ACTION_END",
    )


def test_build_section_input_preserves_long_previous_visual_tail_exactly() -> None:
    long_composition, long_subject_action = _long_carry_values()
    carry = SceneBreakdownServiceV4._carry_from_fragment(
        _previous_fragment(
            composition=long_composition,
            subject_action=long_subject_action,
        )
    )

    payload = build_section_input(
        section=_section("Alpha opens."),
        script_analysis={"narrative_markers": []},
        style_bible=STYLE,
        carry_over=carry,
        video_pacing_profile=VIDEO_PACING_PROFILE,
    )
    tail = payload["visual_context"]["previous_visual_tail"]

    assert len(long_composition) > 600
    assert len(long_subject_action) > 320
    assert tail["composition"] == long_composition
    assert tail["visual_state"] == long_subject_action
    assert tail["selected_motif_refs"] == ["motif_003"]


def test_previous_visual_tail_schema_has_no_authored_prose_upper_bounds() -> None:
    schema = V4ContractValidator().load_schema(
        "scene_breakdown_section_llm_input_v2.schema.json"
    )
    tail = schema["properties"]["visual_context"]["properties"]["previous_visual_tail"]
    object_schema = next(item for item in tail["oneOf"] if item.get("type") == "object")
    composition = object_schema["properties"]["composition"]
    visual_state = object_schema["properties"]["visual_state"]

    assert composition["minLength"] == 1
    assert "maxLength" not in composition
    assert visual_state["minLength"] == 1
    assert "maxLength" not in visual_state
    assert "first beat of every new scene" not in tail["description"]
    assert "Scene boundaries do not themselves force a visual reset" in tail["description"]


def test_section_role_has_no_authored_prose_upper_bound_and_is_preserved() -> None:
    role = (
        "brief archaeological checkpoint — durable instruments prove sophisticated "
        "sound control"
    )
    section = _section(
        "Ancient instruments preserve evidence of controlled sound.",
        section_role=role,
    )

    schema = V4ContractValidator().load_schema(
        "scene_breakdown_section_llm_input_v2.schema.json"
    )
    section_role = schema["properties"]["section_context"]["properties"][
        "section_role"
    ]
    payload = build_section_input(
        section=section,
        script_analysis={"narrative_markers": []},
        style_bible=STYLE,
        video_pacing_profile=VIDEO_PACING_PROFILE,
    )

    assert len(role) > 80
    assert section_role["minLength"] == 1
    assert "maxLength" not in section_role
    assert payload["section_context"]["section_role"] == role


@pytest.mark.parametrize("category", ["characters", "locations", "props", "subjects", "motifs"])
def test_reference_catalog_descriptions_have_no_authored_prose_upper_bound(category: str) -> None:
    schema = V4ContractValidator().load_schema(
        "scene_breakdown_section_llm_input_v2.schema.json"
    )
    catalog = schema["properties"]["visual_context"]["properties"]["reference_catalog"]
    descriptor = next(iter(catalog["properties"][category]["patternProperties"].values()))
    description = descriptor["properties"]["description"]

    assert description["minLength"] == 1
    assert "maxLength" not in description


def test_build_section_input_preserves_long_character_description_exactly() -> None:
    text = "A prehistoric adult carries the shared load."
    long_description = (
        "Recurring representative prehistoric adult and the person most often followed by the second-person narration. "
        "Stable identity cues are dark untidy medium-length hair, a short dark beard, and a simple untailored "
        "ochre-brown animal-hide wrap secured with a cord-like waist tie. The wrap has an irregular asymmetric lower "
        "edge and a clearly visible irregular dark spot pattern across the hide, creating an immediate prehistoric "
        "animal-hide cue without identifying a specific animal species. This person belongs to the primary prehistoric "
        "community but remains individually recognizable when the narration isolates one person's burden, fatigue, "
        "decision, or reaction. Additional production continuity authority preserves the same anatomy, silhouette, "
        "wardrobe construction, material behavior, and readable spotted-hide identity in every approved appearance."
    )
    style = deepcopy(STYLE)
    style["characters"] = [
        {
            "char_id": "char_001",
            "label": "Representative prehistoric adult",
            "description": long_description,
            "explicit_visual_traits": [],
            "continuity_notes": [],
            "approval_status": "text_approved",
            "occurrence_quotes": [],
            "presence_ranges": [
                {
                    "section_id": "sec_001",
                    "start_quote": text,
                    "end_quote": text,
                    "resolved_script_span": [0, len(text)],
                }
            ],
        }
    ]

    payload = build_section_input(
        section=_section(text),
        script_analysis={"narrative_markers": []},
        style_bible=style,
        video_pacing_profile=VIDEO_PACING_PROFILE,
    )

    assert len(long_description) > 600
    assert payload["visual_context"]["reference_catalog"]["characters"]["char_001"]["description"] == long_description


@pytest.mark.parametrize("field", ["composition", "visual_state"])
def test_previous_visual_tail_schema_still_rejects_empty_authored_prose(field: str) -> None:
    carry = SceneBreakdownServiceV4._carry_from_fragment(
        _previous_fragment(
            composition="Previous arrangement.",
            subject_action="Previous visible action.",
        )
    )
    assert carry is not None
    carry["previous_visual_tail"][field] = ""

    with pytest.raises(AppError) as exc_info:
        build_section_input(
            section=_section("Alpha opens."),
            script_analysis={"narrative_markers": []},
            style_bible=STYLE,
            carry_over=carry,
            video_pacing_profile=VIDEO_PACING_PROFILE,
        )

    assert exc_info.value.error_code == "SCENE_BREAKDOWN_SECTION_INPUT_INVALID"
    assert any(
        error["path"].startswith("$.visual_context.previous_visual_tail")
        for error in exc_info.value.details["errors"]
    )


def test_build_section_input_has_no_numeric_segmentation_constraints() -> None:
    payload = _input("Alpha opens. Beta follows.")
    assert "constraints" not in payload
    serialized = str(payload)
    assert "target_beat_count_for_section" not in serialized
    assert "beat_word_policy" not in serialized
    assert payload["visual_cadence_guidance"]["preferred_hold_seconds"] == {"min": 5.5, "max": 8.0}
    assert "pacing_density" not in serialized
    assert "planned_narration_wpm" not in serialized
    assert "video_pacing_profile" not in serialized
    assert "pacing_mode" not in serialized


def test_build_section_input_preserves_large_approved_style_snapshot_without_downstream_caps() -> None:
    style = deepcopy(STYLE)
    style["visual_style_snapshot"]["visual_identity"] = {
        "medium": ["M" * 1300],
        "linework": ["L" * 1300],
        "rendering": ["R" * 1300],
        "palette": [f"palette-{index}" for index in range(30)],
        "shape_language": [],
        "texture": [],
        "editorial_language": ["E" * 1300],
    }
    style["visual_style_snapshot"]["entity_styles"]["character"]["appearance"] = ["C" * 2500]
    style["visual_style_snapshot"]["entity_styles"]["location"]["appearance"] = ["E" * 2500]
    style["visual_style_snapshot"]["entity_styles"]["prop"]["appearance"] = ["P" * 2500]
    style["visual_style_snapshot"]["entity_styles"]["recurring_visual_subject"]["appearance"] = ["S" * 2500]
    style["visual_style_snapshot"]["global_negative_constraints"] = [
        f"avoid-{index}-" + ("x" * 320) for index in range(44)
    ]
    payload = build_section_input(section=_section("Alpha opens."), script_analysis={"narrative_markers": []}, style_bible=style, video_pacing_profile=VIDEO_PACING_PROFILE)
    visual_style = payload["visual_context"]["style"]
    assert len(visual_style["global_avoid"]) == 44
    assert len(visual_style["palette"]) == 30
    assert len(visual_style["medium"]) == 1300
    assert len(visual_style["editorial_language"]) == 1300
    assert len(visual_style["character_style"]) == 2500
    assert visual_style["global_avoid"][-1].startswith("avoid-43-")


def test_build_section_input_materializes_marker_script_text() -> None:
    text = "Alpha opens. Reveal lands hard."
    analysis = {
        "narrative_markers": [
            {
                "marker_id": "marker_001",
                "section_id": "sec_001",
                "marker_type": "reveal",
                "importance": "high",
                "purpose": "decisive turn",
                "resolved_script_span": [13, 31],
            }
        ]
    }
    payload = build_section_input(section=_section(text), script_analysis=analysis, style_bible=STYLE, video_pacing_profile=VIDEO_PACING_PROFILE)
    assert payload["analysis_guidance"]["important_narrative_markers"][0]["script_text"] == "Reveal lands hard."
    assert "candidate_boundaries" not in payload
    assert "annotated_narration" not in str(payload)


def test_validator_derives_spans_and_approved_voice_timing_after_semantic_partition() -> None:
    text = "Alpha opens. Beta follows."
    section = _section(text, start=20)
    analysis = _analysis(text, start=20)
    section_input = build_section_input(
        section=section,
        script_analysis=analysis,
        style_bible=STYLE,
        video_pacing_profile=VIDEO_PACING_PROFILE,
    )
    fragment = validate_and_normalize_section(
        raw_output=_raw("Alpha opens.", " Beta follows."),
        section=section,
        section_input=section_input,
        script_analysis=analysis,
        style_bible=STYLE,
        word_timing_map=_word_timing_map(text, start=20),
    )
    beats = [beat for scene in fragment["scenes"] for beat in scene["beats"]]
    assert [beat["script_quote"] for beat in beats] == ["Alpha opens.", " Beta follows."]
    assert [beat["resolved_script_span"] for beat in beats] == [[20, 32], [32, 46]]
    assert [beat["estimated_duration_seconds"] for beat in beats] == [2.0, 2.0]
    assert fragment["backend_qc"]["timing_policy_version"] == "approved_voice_static_image_v6"
    assert fragment["backend_qc"]["timing_metrics"]["planned_narration_wpm"] == PLANNED_WPM


@pytest.mark.parametrize(
    "parts,code",
    [
        (["Alpha opens.", "Beta changed."], "script_text_mismatch"),
        (["Alpha opens.", "Beta follows. Extra"], "script_text_added"),
        (["Beta follows.", "Alpha opens."], "script_text_mismatch"),
        (["Alpha opens."], "source_partition_invalid"),
        ([""], "raw_schema_invalid"),
    ],
)
def test_validator_rejects_non_source_partitions(parts: list[str], code: str) -> None:
    text = "Alpha opens. Beta follows."
    with pytest.raises(SceneBreakdownValidationError) as exc:
        _validate(text, _raw(*parts))
    serialized = str(exc.value.report)
    assert exc.value.failure_kind in {"source_partition_invalid", "raw_schema_invalid"}
    assert code in serialized or exc.value.failure_kind == code


def test_long_semantic_beat_is_valid_and_only_reported_in_timing_metrics() -> None:
    long_text = " ".join(f"word{i}" for i in range(1, 101)) + "."
    fragment = _validate(long_text, _raw(long_text))
    report = fragment["backend_qc"]
    assert report["timing_status"] == "warning"
    assert report["timing_warnings"]
    assert report["timing_metrics"]["beat_count"] == 1
    assert report["timing_metrics"]["max_beat_duration_seconds"] > 30
    assert not report["semantic_errors"]


def test_scene_breakdown_rejects_missing_resolved_visual_cadence_guidance() -> None:
    text = "Alpha opens. Beta follows."
    section_input = _input(text)
    section_input.pop("visual_cadence_guidance")
    with pytest.raises(SceneBreakdownValidationError) as exc:
        validate_and_normalize_section(
            raw_output=_raw("Alpha opens.", " Beta follows."),
            section=_section(text),
            section_input=section_input,
            style_bible=STYLE,
            word_timing_map=_word_timing_map(text),
        )
    assert exc.value.failure_kind == "source_integrity_failed"
    assert "scene_input_source_mismatch" in str(exc.value.report)


def test_reused_fragment_recomputes_same_v6_approved_voice_timing_diagnostics() -> None:
    text = "Alpha opens. Beta follows."
    canonical = _validate(text, _raw("Alpha opens.", " Beta follows."))
    rebuilt = validate_reusable_section_fragment(
        fragment=canonical,
        section=_section(text),
        section_input=_input(text),
        script_analysis=_analysis(text),
        style_bible=STYLE,
        word_timing_map=_word_timing_map(text),
    )
    assert rebuilt["backend_qc"]["timing_status"] == canonical["backend_qc"]["timing_status"]
    assert rebuilt["backend_qc"]["timing_policy_version"] == "approved_voice_static_image_v6"
    assert rebuilt["backend_qc"]["timing_metrics"] == canonical["backend_qc"]["timing_metrics"]


def test_scene_breakdown_requires_visual_context_authority() -> None:
    text = "Alpha opens."
    with pytest.raises(SceneBreakdownValidationError) as exc:
        validate_and_normalize_section(
            raw_output=_raw(text),
            section=_section(text),
            section_input=_input(text),
            script_analysis={"narrative_markers": []},
            style_bible=STYLE,
            word_timing_map=_word_timing_map(text),
        )
    assert "visual_context_authority_missing" in str(exc.value.report)


def test_reused_fragment_requires_locked_visual_context_ref() -> None:
    text = "Alpha opens."
    canonical = _validate(text, _raw(text))
    canonical["scenes"][0]["beats"][0].pop("visual_context_ref")
    with pytest.raises(SceneBreakdownValidationError) as exc:
        validate_reusable_section_fragment(
            fragment=canonical,
            section=_section(text),
            section_input=_input(text),
            script_analysis=_analysis(text),
            style_bible=STYLE,
            word_timing_map=_word_timing_map(text),
        )
    assert "visual_context_ref_missing" in str(exc.value.report)


def test_scene_breakdown_rejects_removed_asset_classification_fields() -> None:
    text = "Alpha opens."
    raw = _raw(text)
    raw["scenes"][0]["beats"][0]["visual"]["asset_strategy"] = "generated_still"
    raw["scenes"][0]["beats"][0]["visual"]["asset_details"] = {
        "type": "generated_still",
        "image_subject": "Legacy subject",
        "environment": "Legacy setting",
        "focal_action": "Legacy action",
    }
    with pytest.raises(SceneBreakdownValidationError) as exc:
        _validate(text, raw)
    assert exc.value.failure_kind == "raw_schema_invalid"
    errors = exc.value.report.get("errors", [])
    assert any("asset_strategy" in item["message"] for item in errors)
    assert any("asset_details" in item["message"] for item in errors)
