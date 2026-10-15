from __future__ import annotations

from copy import deepcopy
import re

import pytest

from app.api.errors import AppError
from app.main import app
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection, _visual_style_context
from app.modules.v4.scene_breakdown_semantic_validator import (
    _validate_semantic_progression,
    combined_fragment_with_visuals,
    validate_semantic_progression_across_fragments,
)
from app.modules.v4.scene_breakdown_two_step_external_service import ExternalSceneBreakdownTwoStepService


def _route_methods(path: str) -> set[str]:
    methods: set[str] = set()
    for route in app.routes:
        if getattr(route, "path", None) == path:
            methods.update(getattr(route, "methods", set()) or set())
    return methods


def _locked_fragment(*relations: str) -> list[dict[str, object]]:
    return [
        {
            "section_id": "sec_001",
            "scenes": [
                {
                    "beats": [
                        {"continuity_with_previous": relation}
                        for relation in relations
                    ]
                }
            ],
        }
    ]


def _reference_carry_case(relation: str) -> tuple[dict, dict, list[dict]]:
    def visual(beat_id: str, *, characters: list[str], locations: list[str], props: list[str]) -> dict:
        return {
            "beat_id": beat_id,
            "visual_context_ref": "ctx_001",
            "representation_family": "character_environment_lived",
            "selected_character_refs": characters,
            "selected_location_refs": locations,
            "selected_prop_refs": props,
            "selected_recurring_visual_subject_refs": [],
            "selected_motif_refs": [],
            "visual_concept": f"Visual concept {beat_id}",
            "subject_action": f"Subject action {beat_id}",
            "environment_emphasis": f"Environment emphasis {beat_id}",
            "composition": f"Composition {beat_id}",
            "previous_beat_image_reference_required": (
                beat_id != "beat_001" and relation in {"same_setup", "match_composition"}
            ),
        }

    return (
        {
            "scenes": [
                {
                    "scene_id": "scene_001",
                    "beat_plans": [
                        visual("beat_001", characters=["char_001"], locations=["loc_001"], props=["prop_001"]),
                        visual("beat_002", characters=["char_001"], locations=["loc_001"], props=["prop_001"]),
                    ],
                }
            ]
        },
        {
            "scenes": [
                {
                    "scene_id": "scene_001",
                    "beats": [
                        {
                            "beat_id": "beat_001",
                            "visual_context_ref": "ctx_001",
                            "eligible_character_refs": ["char_001"],
                            "eligible_location_refs": ["loc_001"],
                            "eligible_prop_refs": ["prop_001"],
                            "eligible_recurring_visual_subject_refs": [],
                            "eligible_motif_refs": [],
                        },
                        {
                            "beat_id": "beat_002",
                            "visual_context_ref": "ctx_001",
                            "eligible_character_refs": [],
                            "eligible_location_refs": [],
                            "eligible_prop_refs": [],
                            "eligible_recurring_visual_subject_refs": [],
                            "eligible_motif_refs": [],
                        },
                    ],
                }
            ]
        },
        _locked_fragment("new_setup", relation),
    )


def _valid_visual_output() -> dict:
    raw, _scene_plan, _locked = _reference_carry_case("same_setup")
    raw["artifact_id"] = "artifact_000000000001"
    for visual in raw["scenes"][0]["beat_plans"]:
        visual.update(
            {
                "visible_text": [],
                "framing": "medium",
                "camera_angle": "eye_level",
                "camera_movement": "static",
                "lighting_direction": None,
                "color_emphasis": None,
            }
        )
    return raw


def _long_composition() -> str:
    detail = (
        "Detailed current-state spatial direction keeps the primary subject in the left foreground, "
        "the supporting group in a separated middle-ground arc, and the evidence object at the exact "
        "contact point while preserving readable negative space and preventing accidental compositing. "
    )
    return "UNIQUE_START_" + (detail * 3) + "_UNIQUE_END"


def test_runtime_exposes_only_two_step_scene_authoring_surface() -> None:
    project = "{project_id}"
    legacy_prompt = f"/api/v4/projects/{project}/scene-breakdown/prompt"
    legacy_import = f"/api/v4/projects/{project}/scene-breakdown/import"
    legacy_validate = f"/api/v4/projects/{project}/scene-breakdown/import/validate"

    assert _route_methods(legacy_prompt) == set()
    assert _route_methods(legacy_import) == set()
    assert _route_methods(legacy_validate) == set()

    assert "GET" in _route_methods(f"/api/v4/projects/{project}/scene-breakdown/scene/prompt")
    assert "POST" in _route_methods(f"/api/v4/projects/{project}/scene-breakdown/scene/prompt")
    assert "POST" in _route_methods(f"/api/v4/projects/{project}/scene-breakdown/scene/import")
    assert "GET" in _route_methods(f"/api/v4/projects/{project}/scene-breakdown/visual/prompt")
    assert "POST" in _route_methods(f"/api/v4/projects/{project}/scene-breakdown/visual/prompt")
    assert "POST" in _route_methods(f"/api/v4/projects/{project}/scene-breakdown/visual/import")


def test_step1_owns_relation_while_step2_dependency_decision_survives_merge() -> None:
    locked = {
        "scenes": [
            {
                "local_scene_order": 1,
                "beats": [
                    {
                        "local_beat_order": 1,
                        "continuity_with_previous": "match_subject",
                    }
                ],
            }
        ]
    }
    merged = combined_fragment_with_visuals(
        locked_fragment=locked,
        visuals_by_beat_id={
            "beat_001": {
                "continuity_with_previous": "new_setup",
                "previous_beat_image_reference_required": False,
            }
        },
        beat_ids_in_order=["beat_001"],
    )

    visual = merged["scenes"][0]["beats"][0]["visual"]
    assert visual["continuity_with_previous"] == "match_subject"
    assert visual["previous_beat_image_reference_required"] is False



def test_scene_prompt_context_exposes_only_minimal_step1_decision_surface() -> None:
    section = ExtractedSection(
        section_id="sec_001",
        section_order=1,
        section_title="Test",
        section_role="hook",
        resolved_script_span=(0, 4),
        script_excerpt="Test",
        pacing_density="high",
        planned_narration_wpm=220,
    )
    full_input = {
        "script_context": {
            "title": "T",
            "sections": [
                {
                    "section_context": {
                        "section_id": "sec_001",
                        "section_order": 1,
                        "section_title": "Test",
                        "section_role": "hook",
                        "script_text": "Test",
                    },
                    "analysis_guidance": {"important_narrative_markers": []},
                    "visual_cadence_guidance": {"preferred_hold_seconds": {"min": 0.1, "max": 1.0}},
                }
            ],
        },
        "narration_context": {"narration_identity": {"voice": ["calm"]}},
        "timing_context": {
            "sample_rate_hz": 48000,
            "total_narration_sample_count": 96000,
            "sections": [
                {
                    "section_id": "sec_001",
                    "source_start": 0,
                    "source_end": 4,
                    "sample_rate_hz": 48000,
                    "timing_atoms": [
                        {
                            "kind": "word",
                            "source_start": 0,
                            "source_end": 4,
                            "source_text": "Test",
                            "indivisible": False,
                            "start_boundary_safe": True,
                            "end_boundary_safe": True,
                            "speech_start_sample": 1200,
                            "speech_end_sample": 8400,
                        }
                    ],
                    "clauses": [],
                }
            ],
        },
        "visual_context": {
            "style": {"palette": ["red"], "medium": "paint"},
            "reference_catalog": {
                "characters": {
                    "char_001": {
                        "label": "Child",
                        "description": "same child",
                        "visual_traits": ["red tunic"],
                        "continuity_notes": ["same identity"],
                        "physical_constraints": [],
                        "evidence_script_texts": ["child"],
                        "presence_ranges": [{"section_id": "sec_001", "start_quote": "a", "end_quote": "b"}],
                    }
                },
                "locations": {},
                "props": {},
                "subjects": {},
                "motifs": {"motif_001": {"label": "arc", "visual_traits": ["blue"]}},
            },
            "visual_context_catalog": [
                {
                    "context_id": "ctx_001",
                    "label": "Test context",
                    "mode": "story_world",
                    "timeframe": None,
                    "setting": None,
                    "constraints": [],
                    "presence_ranges": [
                        {
                            "section_id": "sec_001",
                            "start_quote": "Test",
                            "end_quote": "Test",
                            "resolved_script_span": [0, 4],
                        }
                    ],
                }
            ],
        },
    }

    word_timing = {
        "sample_rate_hz": 48000,
        "total_sample_count": 96000,
        "sections": [
            {
                "section_id": "sec_001",
                "authored_spoken_mapping": [],
                "words": [
                    {
                        "authored_start": 0,
                        "authored_end": 4,
                        "global_start_sample": 1200,
                        "global_end_sample": 8400,
                    }
                ],
            }
        ],
    }
    plan = ExternalSceneBreakdownTwoStepService._build_scene_authoring_plan(
        full_input,
        [section],
        word_timing,
    )
    result = ExternalSceneBreakdownTwoStepService._scene_prompt_input(
        full_input,
        [section],
        plan,
        "artifact_000000000000",
    )

    assert set(result) == {"artifact_id", "title", "sections", "scene_authoring_plan"}
    assert result["artifact_id"] == "artifact_000000000000"
    assert result["title"] == "T"
    assert result["sections"] == [
        {
            "section_id": "sec_001",
            "section_order": 1,
            "section_title": "Test",
            "section_role": "hook",
            "preferred_hold_seconds": {"min": 0.1, "max": 1.0},
        }
    ]
    assert result["sections"][0]["preferred_hold_seconds"] == plan["sections"][0][
        "preferred_hold_seconds"
    ]
    assert "timing_context" not in result
    assert "narration_context" not in result
    assert "reference_context" not in result
    serialized = str(result)
    for hidden_timing_field in (
        "timing_atoms",
        "duration_seconds",
        "sample_rate_hz",
        "source_start",
        "source_end",
        "word_index",
    ):
        assert hidden_timing_field not in serialized
    scene = result["scene_authoring_plan"]["sections"][0]["scenes"][0]
    assert set(scene) == {"scene_key", "visual_context_ref", "source_segments"}
    assert scene["visual_context_ref"] == "ctx_001"
    assert scene["source_segments"] == [{"text": "Test", "boundary_id_after": None}]


def _valid_scene_authoring_input() -> dict:
    return {
        "artifact_id": "artifact_000000000000",
        "title": "Test",
        "sections": [
            {
                "section_id": "sec_001",
                "section_order": 1,
                "section_title": "Test",
                "section_role": "hook",
                "preferred_hold_seconds": {"min": 0.1, "max": 1.0},
            }
        ],
        "scene_authoring_plan": {
            "sections": [
                {
                    "section_id": "sec_001",
                    "scenes": [
                        {
                            "scene_key": "sec_001_scene_001",
                            "visual_context_ref": "ctx_001",
                            "source_segments": [
                                {"text": "Test", "boundary_id_after": None}
                            ],
                        }
                    ],
                }
            ]
        },
    }


def test_scene_authoring_input_v2_validates_preferred_hold_contract() -> None:
    validator = V4ContractValidator()
    valid = _valid_scene_authoring_input()
    assert validator.validate(
        "scene_breakdown_scene_authoring_input_v2.schema.json",
        valid,
        error_code="TEST_INVALID",
    ) == valid

    missing = deepcopy(valid)
    missing["sections"][0].pop("preferred_hold_seconds")
    extra = deepcopy(valid)
    extra["sections"][0]["preferred_hold_seconds"]["unit"] = "seconds"
    non_positive = deepcopy(valid)
    non_positive["sections"][0]["preferred_hold_seconds"]["min"] = 0
    wrong_type = deepcopy(valid)
    wrong_type["sections"][0]["preferred_hold_seconds"]["max"] = "1.0"

    for invalid in (missing, extra, non_positive, wrong_type):
        with pytest.raises(AppError) as exc_info:
            validator.validate(
                "scene_breakdown_scene_authoring_input_v2.schema.json",
                invalid,
                error_code="TEST_INVALID",
            )
        assert exc_info.value.error_code == "TEST_INVALID"


def test_scene_prompt_maps_resolved_cadence_by_section_identity() -> None:
    full_input = {
        "script_context": {
            "title": "Identity mapping",
            "sections": [
                {
                    "section_context": {
                        "section_id": "sec_001",
                        "section_order": 1,
                        "section_title": "One",
                        "section_role": "hook",
                    }
                },
                {
                    "section_context": {
                        "section_id": "sec_002",
                        "section_order": 2,
                        "section_title": "Two",
                        "section_role": "body",
                    }
                },
            ],
        }
    }
    authoring_plan = {
        "sections": [
            {
                "section_id": "sec_002",
                "preferred_hold_seconds": {"min": 8.0, "max": 11.0},
                "scenes": [],
            },
            {
                "section_id": "sec_001",
                "preferred_hold_seconds": {"min": 2.5, "max": 4.0},
                "scenes": [],
            },
        ]
    }

    projected = ExternalSceneBreakdownTwoStepService._scene_prompt_input(
        full_input,
        [],
        authoring_plan,
    )

    assert projected["sections"][0]["preferred_hold_seconds"] == {
        "min": 2.5,
        "max": 4.0,
    }
    assert projected["sections"][1]["preferred_hold_seconds"] == {
        "min": 8.0,
        "max": 11.0,
    }
    assert "preferred_hold_seconds" not in projected["scene_authoring_plan"][
        "sections"
    ][0]


def test_scene_prompt_fails_closed_when_resolved_cadence_is_missing() -> None:
    full_input = {
        "script_context": {
            "title": "Missing cadence",
            "sections": [
                {
                    "section_context": {
                        "section_id": "sec_001",
                        "section_order": 1,
                        "section_title": "One",
                        "section_role": "hook",
                    }
                }
            ],
        }
    }
    with pytest.raises(AppError) as exc_info:
        ExternalSceneBreakdownTwoStepService._scene_prompt_input(
            full_input,
            [],
            {"sections": []},
        )
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID"
    assert exc_info.value.details["path"] == "$.sections[0].preferred_hold_seconds"


@pytest.mark.parametrize(
    "preferred",
    [
        {"min": 2.0, "max": 2.0},
        {"min": 3.0, "max": 2.0},
        {"min": 0.0, "max": 2.0},
        {"min": True, "max": 2.0},
        {"min": 1.0, "max": float("nan")},
    ],
)
def test_scene_prompt_fails_closed_for_invalid_resolved_cadence(preferred: dict) -> None:
    full_input = {
        "script_context": {
            "title": "Invalid cadence",
            "sections": [
                {
                    "section_context": {
                        "section_id": "sec_001",
                        "section_order": 1,
                        "section_title": "One",
                        "section_role": "hook",
                    }
                }
            ],
        }
    }
    plan = {
        "sections": [
            {
                "section_id": "sec_001",
                "preferred_hold_seconds": preferred,
                "scenes": [],
            }
        ]
    }
    with pytest.raises(AppError) as exc_info:
        ExternalSceneBreakdownTwoStepService._scene_prompt_input(full_input, [], plan)
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID"


def test_scene_prompt_contract_leaves_partition_authority_with_step1() -> None:
    from pathlib import Path

    system_prompt = Path("contracts/prompts/v4/scene_breakdown_scene/system.md").read_text(encoding="utf-8")
    user_prompt = Path("contracts/prompts/v4/scene_breakdown_scene/user.md").read_text(encoding="utf-8")

    assert "Scene boundary != visual reset" in system_prompt
    assert "does not recommend Beat partitions" in system_prompt
    assert "Step 1 owns the semantic partition choice" in system_prompt
    assert "boundary_ids: []" in system_prompt
    assert "boundary_id" in system_prompt
    assert "first Beat of the entire returned plan" in system_prompt
    assert "future-source" in system_prompt
    assert "Do not" in system_prompt
    assert "timing arithmetic" in system_prompt
    assert "complete legal boundary grid" in system_prompt
    assert "Treat each Beat as one keyframe" in system_prompt
    assert "narration chunks" in system_prompt
    assert "word count, narration length, sentence length, clause count" in system_prompt
    assert "only an available cut point" in system_prompt
    assert "There is no default Beat count" in system_prompt
    assert "derive the keyframes needed to make its visual progression complete" in system_prompt
    assert "Keyframe validity is not proportional to the magnitude of change" in system_prompt
    assert "Do not merge adjacent keyframes merely because only one small detail changes" in system_prompt
    assert "preferred hold guidance do not determine Beat count" in system_prompt
    assert "Do not require each resulting Beat to meet the preferred minimum hold" in system_prompt
    assert "# Preferred hold cadence guidance" in system_prompt
    assert "only after keyframe derivation and legal-grid mapping" in system_prompt
    assert "secondary cadence-conformance and semantic-rescan signal" in system_prompt
    assert "target Beat count" in system_prompt
    assert "without timing arithmetic" in system_prompt
    assert "independently justify that change" in system_prompt
    assert "Exact realized Beat timing remains backend-owned" in system_prompt
    assert "redundant keyframes" in system_prompt
    assert "ordered storyboard" in system_prompt
    assert "External research, remembered facts, prior chats, and outside knowledge do not authorize" in system_prompt
    assert "outside knowledge do not authorize Beat meaning" in system_prompt
    assert "merge-left / merge-right / merge-both" not in system_prompt
    assert "raw timing atoms" not in user_prompt
    assert "reconstruct timestamps" in user_prompt
    assert "later Scene and Section boundaries do not imply reset" in user_prompt
    assert "one keyframe in the Scene's ordered visual sequence" in user_prompt
    assert "There is no default Beat count" in user_prompt
    assert "First derive the complete Scene-level keyframe progression" in user_prompt
    assert "small source-owned visual delta may justify its own Beat" in user_prompt
    assert "preferred hold guidance do not determine Beat count or minimum Beat duration" in user_prompt
    assert "Only after keyframe derivation and legal-grid mapping" in user_prompt
    assert "secondary cadence sanity/rescan signal" in user_prompt
    assert "normal semantic/keyframe tests independently justify the change" in user_prompt
    assert "Exact realized Beat timing remains backend-owned" in user_prompt
    assert "only when its opening source authorizes no new keyframe state" in user_prompt

    assert "mandatory local cadence reconciliation" not in system_prompt
    assert "LONG Beat" not in system_prompt
    assert "timing_context.sections" not in system_prompt
    assert "spoken_override" not in system_prompt


def test_visual_prompt_consumes_locked_visual_semantic_beats_without_remerging() -> None:
    from pathlib import Path

    prompt = Path("contracts/prompts/v4/scene_breakdown_visual/system.md").read_text(encoding="utf-8")

    assert "Visual-Semantic Beat" in prompt
    assert "semantic keyframes selected by Step 1" in prompt
    assert "every genuine locked `delta_now`, regardless of magnitude" in prompt
    assert "Step 2 must not re-evaluate whether Step 1's locked delta was important enough to deserve a Beat" in prompt
    assert "54% Character / Environment / Lived Scene" in prompt
    assert "13% Object / Evidence Close-up" in prompt
    assert "33% Text / Diagram / Editorial" in prompt
    assert "complete Visual Plan" in prompt
    assert "graphic_sequence` may carry editorial grammar/layout logic only" in prompt
    assert "single frozen keyframe" in prompt
    assert not re.search(r"cadence[- ](?:sized|validated)", prompt, re.IGNORECASE)
    assert "Do not merge neighboring Beats" in prompt
    assert "one facet of a larger sentence/proposition" in prompt
    assert "state-transition continuity" in prompt
    assert "do not merge two Beat meanings into one image" in prompt


def test_visual_prompt_locks_selected_entity_physical_constraints() -> None:
    from pathlib import Path

    prompt = Path("contracts/prompts/v4/scene_breakdown_visual/system.md").read_text(encoding="utf-8")

    assert "`physical_constraints` as locked canonical physical authority" in prompt
    assert "apply only to that exact selected entity" in prompt
    assert "merely eligible but unselected entities" in prompt
    assert "Apparent image size, screen occupancy, foreshortening, and perspective" in prompt
    assert "intrinsic physical size, proportions, real-world relative scale, graspability" in prompt
    assert "a Beat is invalid if any visible selected canonical entity is staged" in prompt
    assert "Do not return them as a new output field" in prompt
    assert "do not use them to choose `representation_family`" in prompt


def test_visual_prompt_requires_one_frozen_image_before_fields_family_and_ratio_review() -> None:
    from pathlib import Path

    prompt = Path("contracts/prompts/v4/scene_breakdown_visual/system.md").read_text(encoding="utf-8")

    assert "one concrete frozen image" in prompt
    assert "Do not generate the fields independently as separate semantic solutions" in prompt
    assert "Do not choose `representation_family` before the concrete still has been authored" in prompt
    assert "never be used as a template selector" in prompt
    assert "Do not pre-allocate Beat counts to representation families" in prompt
    assert "strictly as a **post-hoc whole-plan audit**" in prompt
    assert "never alter a stronger visual solely to move a percentage" in prompt.lower()
    assert "A final field is invalid when its structure comes primarily from `representation_family`" in prompt
    assert "same physical/editorial frozen image at the same instant" in prompt
    assert "conceive the still -> identify which canonical identities are actually visible -> select refs" in prompt
    assert "mismatch invalidates the classification, not automatically the visual" in prompt
    assert "correct the classification first" in prompt
    assert "do not alter a semantically valid still merely to fit the existing family label" in prompt.lower()
    assert "Hard failures versus diagnostic warnings" in prompt
    assert "## PASS B - Frozen-image semantic integrity" in prompt
    assert "## PASS G - Whole-plan 54 / 13 / 33 calibration" in prompt
    assert "Author each final field independently from the same locked current Beat state" not in prompt


def test_semantic_progression_rejects_adjacent_duplicate_locked_state() -> None:
    raw = {
        "scenes": [
            {
                "beats": [
                    {
                        "meaning_now": "Branch enters.",
                        "visible_state_now": "Branch enters upstream.",
                        "delta_now": "Moving debris appears.",
                    },
                    {
                        "meaning_now": " branch enters ",
                        "visible_state_now": "branch enters upstream",
                        "delta_now": "moving debris appears",
                    },
                ]
            }
        ]
    }

    with pytest.raises(AppError) as exc_info:
        _validate_semantic_progression(raw)

    assert exc_info.value.error_code == "SCENE_BREAKDOWN_SEMANTIC_STATE_COLLAPSE"


def test_visual_semantics_rejects_field_collapse_and_out_of_range_ref() -> None:
    raw = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beat_plans": [
                    {
                        "beat_id": "beat_001",
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_999"],
                        "selected_location_refs": [],
                        "selected_prop_refs": [],
                        "selected_recurring_visual_subject_refs": [],
                        "selected_motif_refs": [],
                        "visual_concept": "A branch enters the river.",
                        "subject_action": "A branch enters the river",
                        "environment_emphasis": "Dark current around fixed stones.",
                        "composition": "Branch upstream, stones downstream.",
                        "previous_beat_image_reference_required": False,
                    }
                ],
            }
        ]
    }
    scene_plan = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beats": [
                    {
                        "beat_id": "beat_001",
                        "eligible_character_refs": ["char_001"],
                        "eligible_location_refs": [],
                        "eligible_prop_refs": [],
                        "eligible_recurring_visual_subject_refs": [],
                        "eligible_motif_refs": [],
                    }
                ],
            }
        ]
    }

    with pytest.raises(AppError) as exc_info:
        ExternalSceneBreakdownTwoStepService._validate_visual_semantics(
            raw,
            scene_plan,
            _locked_fragment("new_setup"),
        )

    assert exc_info.value.error_code == "SCENE_BREAKDOWN_VISUAL_SEMANTIC_INVALID"
    codes = {item["code"] for item in exc_info.value.details["errors"]}
    assert "visual_field_collapse" in codes
    assert "visual_reference_outside_locked_eligibility" in codes


def test_visual_semantics_rejects_visual_context_ref_mismatch() -> None:
    raw, scene_plan, locked = _reference_carry_case("same_setup")
    scene_plan["scenes"][0]["beats"][0]["visual_context_ref"] = "ctx_001"
    raw["scenes"][0]["beat_plans"][0]["visual_context_ref"] = "ctx_999"

    with pytest.raises(AppError) as exc_info:
        ExternalSceneBreakdownTwoStepService._validate_visual_semantics(
            raw,
            scene_plan,
            locked,
        )

    assert any(
        item["code"] == "visual_context_ref_mismatch"
        for item in exc_info.value.details["errors"]
    )


def test_visual_semantics_allows_exact_immediate_reference_carry() -> None:
    raw = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beat_plans": [
                    {
                        "beat_id": "beat_001",
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_001"],
                        "selected_location_refs": [],
                        "selected_prop_refs": [],
                        "selected_recurring_visual_subject_refs": [],
                        "selected_motif_refs": [],
                        "visual_concept": "The team stands beside the crossing.",
                        "subject_action": "The team studies the water.",
                        "environment_emphasis": "The crossing remains calm.",
                        "composition": "The team occupies the foreground beside the stream.",
                        "previous_beat_image_reference_required": False,
                    },
                    {
                        "beat_id": "beat_002",
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_001"],
                        "selected_location_refs": [],
                        "selected_prop_refs": [],
                        "selected_recurring_visual_subject_refs": [],
                        "selected_motif_refs": [],
                        "visual_concept": "The same team holds position as the current changes.",
                        "subject_action": "The team watches the faster current.",
                        "environment_emphasis": "The water darkens around the same crossing.",
                        "composition": "The same team remains foreground while the current fills the background.",
                        "previous_beat_image_reference_required": True,
                    },
                ],
            }
        ]
    }
    scene_plan = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beats": [
                    {
                        "beat_id": "beat_001",
                        "eligible_character_refs": ["char_001"],
                        "eligible_location_refs": [],
                        "eligible_prop_refs": [],
                        "eligible_recurring_visual_subject_refs": [],
                        "eligible_motif_refs": [],
                    },
                    {
                        "beat_id": "beat_002",
                        "eligible_character_refs": [],
                        "eligible_location_refs": [],
                        "eligible_prop_refs": [],
                        "eligible_recurring_visual_subject_refs": [],
                        "eligible_motif_refs": [],
                    },
                ],
            }
        ]
    }

    ExternalSceneBreakdownTwoStepService._validate_visual_semantics(
        raw,
        scene_plan,
        _locked_fragment("new_setup", "same_setup"),
    )


@pytest.mark.parametrize(
    ("relation", "allowed_fields"),
    [
        ("same_setup", {"selected_character_refs", "selected_location_refs", "selected_prop_refs"}),
        ("match_subject", {"selected_character_refs", "selected_prop_refs"}),
        ("match_location", {"selected_location_refs"}),
        ("match_composition", set()),
        ("graphic_sequence", set()),
        ("new_setup", set()),
    ],
)
def test_visual_semantics_scopes_reference_carry_by_relation(
    relation: str,
    allowed_fields: set[str],
) -> None:
    raw, scene_plan, locked = _reference_carry_case(relation)
    all_fields = {"selected_character_refs", "selected_location_refs", "selected_prop_refs"}

    if allowed_fields == all_fields:
        ExternalSceneBreakdownTwoStepService._validate_visual_semantics(raw, scene_plan, locked)
        return

    with pytest.raises(AppError) as exc_info:
        ExternalSceneBreakdownTwoStepService._validate_visual_semantics(raw, scene_plan, locked)

    unsupported_fields = {
        item["path"].rsplit(".", 1)[-1]
        for item in exc_info.value.details["errors"]
        if item["code"] == "visual_reference_outside_locked_eligibility"
    }
    assert unsupported_fields == all_fields - allowed_fields


@pytest.mark.parametrize(
    ("relation", "required"),
    [
        ("new_setup", True),
        ("match_composition", False),
    ],
)
def test_visual_semantics_rejects_previous_image_dependency_matrix_mismatch(
    relation: str,
    required: bool,
) -> None:
    raw, scene_plan, locked = _reference_carry_case(relation)
    for visual in raw["scenes"][0]["beat_plans"]:
        visual["selected_character_refs"] = []
        visual["selected_location_refs"] = []
        visual["selected_prop_refs"] = []
    raw["scenes"][0]["beat_plans"][1]["previous_beat_image_reference_required"] = required

    with pytest.raises(AppError) as exc_info:
        ExternalSceneBreakdownTwoStepService._validate_visual_semantics(raw, scene_plan, locked)

    assert any(
        item["code"] == "previous_beat_image_reference_relation_mismatch"
        for item in exc_info.value.details["errors"]
    )


@pytest.mark.parametrize(
    ("relation", "required"),
    [
        ("new_setup", False),
        ("same_setup", False),
        ("same_setup", True),
        ("match_subject", False),
        ("match_subject", True),
        ("match_location", False),
        ("match_location", True),
        ("graphic_sequence", False),
        ("graphic_sequence", True),
        ("match_composition", True),
    ],
)
def test_visual_semantics_accepts_valid_previous_image_dependency_matrix(
    relation: str,
    required: bool,
) -> None:
    raw, scene_plan, locked = _reference_carry_case(relation)
    for visual in raw["scenes"][0]["beat_plans"]:
        visual["selected_character_refs"] = []
        visual["selected_location_refs"] = []
        visual["selected_prop_refs"] = []
    raw["scenes"][0]["beat_plans"][1]["previous_beat_image_reference_required"] = required

    ExternalSceneBreakdownTwoStepService._validate_visual_semantics(raw, scene_plan, locked)


def test_visual_output_schema_requires_explicit_previous_image_dependency() -> None:
    raw = _valid_visual_output()
    del raw["scenes"][0]["beat_plans"][1]["previous_beat_image_reference_required"]

    with pytest.raises(AppError) as exc_info:
        V4ContractValidator().validate(
            "scene_breakdown_visual_llm_output_v2.schema.json",
            raw,
            error_code="TEST_SCHEMA_INVALID",
        )

    assert any(
        error["path"] == "$.scenes[0].beat_plans[1]"
        and "previous_beat_image_reference_required" in error["message"]
        for error in exc_info.value.details["errors"]
    )


def test_visual_output_composition_has_no_maximum_while_other_prose_limits_remain() -> None:
    schema = V4ContractValidator().load_schema(
        "scene_breakdown_visual_llm_output_v2.schema.json"
    )
    properties = schema["properties"]["scenes"]["items"]["properties"]["beat_plans"]["items"]["properties"]
    composition = properties["composition"]

    assert composition["minLength"] == 1
    assert "maxLength" not in composition
    assert properties["visual_concept"]["maxLength"] == 1200
    assert properties["subject_action"]["maxLength"] == 800
    assert properties["environment_emphasis"]["maxLength"] == 800
    assert properties["visible_text"]["maxItems"] == 12
    assert properties["visible_text"]["items"]["maxLength"] == 200
    assert properties["lighting_direction"]["maxLength"] == 160
    assert properties["color_emphasis"]["maxLength"] == 160


def test_visual_output_schema_accepts_long_composition_without_truncation() -> None:
    raw = _valid_visual_output()
    long_composition = _long_composition()
    raw["scenes"][0]["beat_plans"][0]["composition"] = long_composition

    validated = V4ContractValidator().validate(
        "scene_breakdown_visual_llm_output_v2.schema.json",
        raw,
        error_code="TEST_SCHEMA_INVALID",
    )

    assert len(long_composition) > 600
    assert validated["scenes"][0]["beat_plans"][0]["composition"] == long_composition


def test_visual_output_schema_still_rejects_empty_composition() -> None:
    raw = _valid_visual_output()
    raw["scenes"][0]["beat_plans"][0]["composition"] = ""

    with pytest.raises(AppError) as exc_info:
        V4ContractValidator().validate(
            "scene_breakdown_visual_llm_output_v2.schema.json",
            raw,
            error_code="TEST_SCHEMA_INVALID",
        )

    assert any(
        error["path"] == "$.scenes[0].beat_plans[0].composition"
        and "non-empty" in error["message"]
        for error in exc_info.value.details["errors"]
    )


def test_reusable_section_composition_has_no_maximum_and_accepts_long_value() -> None:
    validator = V4ContractValidator()
    schema = validator.load_schema("scene_breakdown_section_llm_output_v1.schema.json")
    properties = schema["properties"]["scenes"]["items"]["properties"]["beats"]["items"]["properties"]["visual"]["properties"]
    composition = properties["composition"]

    assert composition["minLength"] == 1
    assert "maxLength" not in composition

    step_two = _valid_visual_output()
    visual = step_two["scenes"][0]["beat_plans"][0]
    visual.pop("beat_id")
    visual.pop("visual_context_ref")
    visual["continuity_with_previous"] = "new_setup"
    long_composition = _long_composition()
    visual["composition"] = long_composition
    reusable = {"scenes": [{"beats": [{"script_text": "Alpha opens.", "visual": visual}]}]}

    validated = validator.validate(
        "scene_breakdown_section_llm_output_v1.schema.json",
        reusable,
        error_code="TEST_SCHEMA_INVALID",
    )

    assert validated["scenes"][0]["beats"][0]["visual"]["composition"] == long_composition


def test_visual_semantics_new_setup_breaks_reference_carry() -> None:
    raw = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beat_plans": [
                    {
                        "beat_id": "beat_001",
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_001"],
                        "selected_location_refs": [],
                        "selected_prop_refs": [],
                        "selected_recurring_visual_subject_refs": [],
                        "selected_motif_refs": [],
                        "visual_concept": "The team stands beside the crossing.",
                        "subject_action": "The team studies the water.",
                        "environment_emphasis": "The crossing remains calm.",
                        "composition": "The team occupies the foreground beside the stream.",
                        "previous_beat_image_reference_required": False,
                    },
                    {
                        "beat_id": "beat_002",
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_001"],
                        "selected_location_refs": [],
                        "selected_prop_refs": [],
                        "selected_recurring_visual_subject_refs": [],
                        "selected_motif_refs": [],
                        "visual_concept": "A different setup begins downstream.",
                        "subject_action": "The frame resets around a new subject.",
                        "environment_emphasis": "The downstream bank now dominates.",
                        "composition": "The new subject occupies the foreground with open bank behind.",
                        "previous_beat_image_reference_required": False,
                    },
                ],
            }
        ]
    }
    scene_plan = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beats": [
                    {
                        "beat_id": "beat_001",
                        "eligible_character_refs": ["char_001"],
                        "eligible_location_refs": [],
                        "eligible_prop_refs": [],
                        "eligible_recurring_visual_subject_refs": [],
                        "eligible_motif_refs": [],
                    },
                    {
                        "beat_id": "beat_002",
                        "eligible_character_refs": [],
                        "eligible_location_refs": [],
                        "eligible_prop_refs": [],
                        "eligible_recurring_visual_subject_refs": [],
                        "eligible_motif_refs": [],
                    },
                ],
            }
        ]
    }

    with pytest.raises(AppError) as exc_info:
        ExternalSceneBreakdownTwoStepService._validate_visual_semantics(
            raw,
            scene_plan,
            _locked_fragment("new_setup", "new_setup"),
        )

    assert exc_info.value.error_code == "SCENE_BREAKDOWN_VISUAL_SEMANTIC_INVALID"
    assert any(
        item["code"] == "visual_reference_outside_locked_eligibility"
        for item in exc_info.value.details["errors"]
    )


def test_visual_style_context_requires_and_forwards_editorial_language() -> None:
    snapshot = {
        "visual_identity": {
            "medium": ["meme-stick"],
            "linework": ["rough"],
            "rendering": ["flat"],
            "palette": ["muted"],
            "shape_language": ["simple"],
            "texture": ["minimal"],
            "editorial_language": [
                "Switch representation mode when it communicates the locked idea more clearly.",
                "Short editorial text may compress meaning without adding facts.",
            ],
        },
        "entity_styles": {
            "character": {"appearance": ["simple"]},
            "location": {"appearance": ["quiet"]},
            "prop": {"appearance": ["readable"]},
            "recurring_visual_subject": {"appearance": ["consistent"]},
        },
        "global_negative_constraints": [],
    }
    result = _visual_style_context({"visual_style_snapshot": snapshot})
    assert result["editorial_language"] == (
        "Switch representation mode when it communicates the locked idea more clearly.; "
        "Short editorial text may compress meaning without adding facts."
    )

    del snapshot["visual_identity"]["editorial_language"]
    with pytest.raises(AppError) as exc_info:
        _visual_style_context({"visual_style_snapshot": snapshot})
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_STYLE_BIBLE_INVALID"


def test_semantic_progression_allows_same_broad_state_when_source_owned_delta_changes() -> None:
    raw = {
        "scenes": [
            {
                "beats": [
                    {
                        "meaning_now": "The person's useful radius is smaller.",
                        "visible_state_now": "The same person remains beside camp.",
                        "delta_now": "The nearby task is still reachable.",
                    },
                    {
                        "meaning_now": "The missing distance now becomes someone else's burden.",
                        "visible_state_now": "The same person remains beside camp.",
                        "delta_now": "A distinct source-owned social/logistics consequence is introduced.",
                    },
                ]
            }
        ]
    }
    _validate_semantic_progression(raw)


def test_semantic_progression_rejects_duplicate_state_across_scene_boundary() -> None:
    raw = {
        "scenes": [
            {
                "beats": [
                    {
                        "meaning_now": "Same meaning",
                        "visible_state_now": "Same visible state",
                        "delta_now": "Same delta",
                    }
                ]
            },
            {
                "beats": [
                    {
                        "meaning_now": " same meaning ",
                        "visible_state_now": "same visible state",
                        "delta_now": "same delta",
                    }
                ]
            },
        ]
    }
    with pytest.raises(AppError) as exc_info:
        _validate_semantic_progression(raw)
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_SEMANTIC_STATE_COLLAPSE"


def test_visual_semantics_allows_reference_carry_across_scene_boundary() -> None:
    raw = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beat_plans": [
                    {
                        "beat_id": "beat_001",
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_001"],
                        "selected_location_refs": [],
                        "selected_prop_refs": [],
                        "selected_recurring_visual_subject_refs": [],
                        "selected_motif_refs": [],
                        "visual_concept": "The child waits beside the crossing.",
                        "subject_action": "The child watches the water.",
                        "environment_emphasis": "The crossing remains calm.",
                        "composition": "Child foreground, river behind.",
                        "previous_beat_image_reference_required": False,
                    }
                ],
            },
            {
                "scene_id": "scene_002",
                "beat_plans": [
                    {
                        "beat_id": "beat_002",
                        "representation_family": "character_environment_lived",
                        "selected_character_refs": ["char_001"],
                        "selected_location_refs": [],
                        "selected_prop_refs": [],
                        "selected_recurring_visual_subject_refs": [],
                        "selected_motif_refs": [],
                        "visual_concept": "The same child remains as the current changes.",
                        "subject_action": "The child keeps watching.",
                        "environment_emphasis": "The water becomes faster.",
                        "composition": "Same child foreground, faster river behind.",
                        "previous_beat_image_reference_required": True,
                    }
                ],
            },
        ]
    }
    scene_plan = {
        "scenes": [
            {
                "scene_id": "scene_001",
                "beats": [
                    {
                        "beat_id": "beat_001",
                        "eligible_character_refs": ["char_001"],
                        "eligible_location_refs": [],
                        "eligible_prop_refs": [],
                        "eligible_recurring_visual_subject_refs": [],
                        "eligible_motif_refs": [],
                    }
                ],
            },
            {
                "scene_id": "scene_002",
                "beats": [
                    {
                        "beat_id": "beat_002",
                        "eligible_character_refs": [],
                        "eligible_location_refs": [],
                        "eligible_prop_refs": [],
                        "eligible_recurring_visual_subject_refs": [],
                        "eligible_motif_refs": [],
                    }
                ],
            },
        ]
    }
    locked = [
        {
            "section_id": "sec_001",
            "scenes": [
                {"beats": [{"continuity_with_previous": "new_setup"}]},
                {"beats": [{"continuity_with_previous": "same_setup"}]},
            ],
        }
    ]
    ExternalSceneBreakdownTwoStepService._validate_visual_semantics(raw, scene_plan, locked)


def test_semantic_progression_rejects_duplicate_state_across_section_boundary() -> None:
    fragments = [
        {
            "scenes": [
                {
                    "beats": [
                        {
                            "meaning_now": "Same final meaning",
                            "visible_state_now": "Same final state",
                            "delta_now": "Same final delta",
                        }
                    ]
                }
            ]
        },
        {
            "scenes": [
                {
                    "beats": [
                        {
                            "meaning_now": " same final meaning ",
                            "visible_state_now": "same final state",
                            "delta_now": "same final delta",
                        }
                    ]
                }
            ]
        },
    ]
    with pytest.raises(AppError) as exc_info:
        validate_semantic_progression_across_fragments(fragments)
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_SEMANTIC_STATE_COLLAPSE"


def test_authoring_metrics_distinguish_scene_and_section_boundaries() -> None:
    locked = [
        {
            "section_id": "sec_001",
            "backend_qc": {
                "authoring_plan_metrics": {
                    "beat_count": 2,
                    "cross_scene_continuity_count": 1,
                    "cross_scene_new_setup_count": 0,
                    "cross_section_continuity_count": 0,
                    "cross_section_new_setup_count": 0,
                }
            },
        },
        {
            "section_id": "sec_002",
            "backend_qc": {
                "authoring_plan_metrics": {
                    "beat_count": 1,
                    "cross_scene_continuity_count": 0,
                    "cross_scene_new_setup_count": 0,
                    "cross_section_continuity_count": 1,
                    "cross_section_new_setup_count": 0,
                }
            },
        },
    ]

    summary = (
        ExternalSceneBreakdownTwoStepService
        ._scene_authoring_metrics_summary(locked)
    )
    assert summary["totals"]["cross_scene_continuity_count"] == 1
    assert summary["totals"]["cross_section_continuity_count"] == 1
    assert summary["totals"]["cross_scene_new_setup_count"] == 0
    assert summary["totals"]["cross_section_new_setup_count"] == 0


def test_step1_semantic_prose_contract_stays_bounded_and_concise() -> None:
    import json
    from pathlib import Path

    schema = json.loads(
        Path(
            "contracts/schemas/v4/scene_breakdown_scene_llm_output_v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    beat = (
        schema["properties"]["sections"]["items"]["properties"]["scenes"]
        ["items"]["properties"]["beats"]["items"]["properties"]
    )
    assert "narrative_role" not in beat
    assert beat["meaning_now"]["maxLength"] == 320
    assert beat["visible_state_now"]["maxLength"] == 360
    assert beat["delta_now"]["maxLength"] == 280
    assert "scene_purpose" not in schema["properties"]["sections"]["items"]["properties"]["scenes"]["items"]["properties"]

    prompt = Path(
        "contracts/prompts/v4/scene_breakdown_scene/system.md"
    ).read_text(encoding="utf-8")
    assert "shortest Beat-specific phrase or sentence" in prompt
    assert "do not restate the narration" in prompt
    assert "generic filler" in prompt


def test_scene_authoring_surface_hash_is_canonical_and_distinct_from_prompt_hash() -> None:
    first = {
        "sections": [
            {
                "section_id": "sec_001",
                "preferred_hold_seconds": {"min": 2.5, "max": 4.0},
            }
        ]
    }
    reordered = {
        "sections": [
            {
                "preferred_hold_seconds": {"max": 4.0, "min": 2.5},
                "section_id": "sec_001",
            }
        ]
    }
    changed = {
        "sections": [
            {
                "section_id": "sec_001",
                "preferred_hold_seconds": {"min": 2.5, "max": 4.1},
            }
        ]
    }

    assert ExternalSceneBreakdownTwoStepService._scene_authoring_input_sha256(first) == ExternalSceneBreakdownTwoStepService._scene_authoring_input_sha256(reordered)
    assert ExternalSceneBreakdownTwoStepService._scene_authoring_input_sha256(first) != ExternalSceneBreakdownTwoStepService._scene_authoring_input_sha256(changed)


def test_step1_schema_audit_hashes_are_canonical_across_field_order() -> None:
    package = {"prompt_version": "scene_breakdown_scene_external_v9", "prompt_text": "prompt"}
    output_schema = {"$id": "output", "type": "object", "properties": {"a": {"type": "string"}}}
    reordered_output_schema = {"properties": {"a": {"type": "string"}}, "type": "object", "$id": "output"}
    input_schema = {"$id": "input", "type": "object", "required": ["a"]}
    reordered_input_schema = {"required": ["a"], "type": "object", "$id": "input"}

    first = ExternalSceneBreakdownTwoStepService._scene_contract_audit(
        package,
        output_schema,
        input_schema,
    )
    reordered = ExternalSceneBreakdownTwoStepService._scene_contract_audit(
        package,
        reordered_output_schema,
        reordered_input_schema,
    )

    assert first == reordered
    assert set(first) == {
        "prompt_version",
        "prompt_sha256",
        "raw_schema_id",
        "raw_schema_sha256",
        "scene_input_schema_id",
        "scene_input_schema_sha256",
    }


def test_step1_output_contract_does_not_contain_preferred_hold_guidance() -> None:
    schema = V4ContractValidator().load_schema(
        "scene_breakdown_scene_llm_output_v2.schema.json"
    )
    assert "preferred_hold_seconds" not in str(schema)
