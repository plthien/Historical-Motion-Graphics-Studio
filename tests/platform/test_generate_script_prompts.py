from __future__ import annotations

import json
from pathlib import Path

from app.modules.platform.contracts import PlatformContractValidator

ROOT = Path(__file__).resolve().parents[2]
PROMPT_ROOT = ROOT / "contracts" / "prompts" / "platform" / "generate_script_and_analysis"
SCHEMA_NAME = "script_and_analysis_external_output_v1.schema.json"


def _text(name: str) -> str:
    return (PROMPT_ROOT / name).read_text(encoding="utf-8")


def test_combined_script_analysis_prompts_have_no_encoding_corruption() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    for marker in ("Ã", "Â", "â€™", "ðŸ"):
        assert marker not in text


def test_prompt_requires_section_pacing_plan_then_frozen_analysis() -> None:
    system = _text("system.md")
    assert "Plan the complete Script architecture before prose" in system
    assert "choose and lock one exact `planned_narration_wpm` for every Script section" in system
    assert "pre-freeze realized visual-density gate" in system
    assert "Freeze the completed Script" in system
    assert "Analyze only that exact frozen Script" in system
    assert system.index("Plan the complete Script architecture before prose") < system.index("Freeze the completed Script") < system.index("Analyze only that exact frozen Script")


def test_prompt_allows_web_search_but_keeps_research_outside_output() -> None:
    system = _text("system.md")
    assert "Use web search" in system
    assert "current or time-sensitive" in system
    assert "Return no Markdown" in system
    assert "research notes" in system
    assert "citations" in system
    assert "outside the supplied schema" in system


def test_prompt_freezes_section_ids_and_exact_quotes() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    assert "sec_001" in text and "sec_002" in text
    assert "Every analysis quote and context quote must occur verbatim" in text
    assert "section_count_mode" in text
    assert "llm_decides" in text
    assert "backend_plan" in text
    assert "exactly one Script section for every item in `authoring_constraints.section_plan`" in text


def test_prompt_forbids_backend_owned_analysis_fields() -> None:
    system = _text("system.md")
    for field in ("artifact IDs", "resolved spans", "stable entity IDs", "QC fields", "source tracking"):
        assert field in system


def test_combined_output_schema_is_strict_and_reuses_raw_script_shape() -> None:
    schema = PlatformContractValidator(ROOT).load_schema(SCHEMA_NAME)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["artifact_ids", "script", "analysis"]
    assert schema["properties"]["script"]["additionalProperties"] is False
    assert schema["properties"]["analysis"]["additionalProperties"] is False
    serialized = json.dumps(schema, ensure_ascii=False)
    for backend_field in ("source_script_revision_id", "project_id", "resolved_span", "marker_id"):
        assert backend_field not in schema["properties"]["analysis"]["properties"]
    assert "script_sections" in serialized
    assert "continuity_catalog" in serialized


def test_user_prompt_contains_only_one_input_placeholder() -> None:
    user = _text("user.md")
    assert user.count("{{input_json}}") == 1


def test_section_count_guidance_is_runtime_based_and_wpm_independent() -> None:
    from app.modules.v4.script_word_budget import build_authoring_constraints, build_runtime_diagnostics

    one_minute = build_authoring_constraints(1, section_count_mode="llm_decides")
    four_minutes = build_authoring_constraints(4, section_count_mode="llm_decides")
    eight_minutes = build_authoring_constraints(8, section_count_mode="llm_decides")

    assert (one_minute["recommended_section_count"], one_minute["minimum_section_count"], one_minute["maximum_section_count"]) == (3, 3, 4)
    assert (four_minutes["recommended_section_count"], four_minutes["minimum_section_count"], four_minutes["maximum_section_count"]) == (5, 3, 8)
    assert (eight_minutes["recommended_section_count"], eight_minutes["minimum_section_count"], eight_minutes["maximum_section_count"]) == (10, 7, 12)

    slow_sections = [{"section_id": "sec_001", "planned_narration_wpm": 155, "narration": " ".join(["word"] * 1550)}]
    fast_sections = [{"section_id": "sec_001", "planned_narration_wpm": 175, "narration": " ".join(["word"] * 1750)}]
    slow = build_runtime_diagnostics(target_duration_minutes=10, script_sections=slow_sections)
    fast = build_runtime_diagnostics(target_duration_minutes=10, script_sections=fast_sections)
    assert slow.estimated_duration_minutes == 10.0
    assert fast.estimated_duration_minutes == 10.0
    assert slow.planned_average_wpm == 155
    assert fast.planned_average_wpm == 175


def test_prompt_defines_canonical_character_entity_kind_taxonomy() -> None:
    system = _text("system.md")
    assert "analysis.continuity_catalog.characters[*].entity_kind" in system
    assert "`human_character`: one recurring individual human identity" in system
    assert "`human_group`: a recurring plural human identity" in system
    assert "Do not output free-form alternatives" in system
    assert "An unnamed incidental helper does not become a canonical character" in system


def test_external_output_schema_locks_character_entity_kind_taxonomy() -> None:
    schema = PlatformContractValidator(ROOT).load_schema(SCHEMA_NAME)
    entity_kind = schema["$defs"]["character_raw"]["properties"]["entity_kind"]
    assert entity_kind["enum"] == ["human_character", "human_group"]


def test_script_analysis_prompt_defines_presence_ranges_as_canonical_scope() -> None:
    system = _text("system.md")
    user = _text("user.md")
    assert "`occurrences` are direct or representative verbatim anchors" in system
    assert "`presence_ranges` are the authoritative narrative spans" in system
    assert "generic, comparative, evidentiary" in system
    assert "Do not create dozens of occurrences" in system
    assert "presence_ranges` start/end boundary" in user


def test_external_output_schema_requires_presence_ranges_for_canonical_entities_only() -> None:
    schema = PlatformContractValidator(ROOT).load_schema(SCHEMA_NAME)
    for definition in ("character_raw", "location_raw", "prop_raw", "recurring_visual_subject_raw"):
        assert "presence_ranges" in schema["$defs"][definition]["required"]
        assert schema["$defs"][definition]["properties"]["presence_ranges"]["minItems"] == 1
    assert "presence_ranges" not in schema["$defs"]["visual_motif_raw"]["required"]


def test_prompt_uses_selected_mode_and_section_wpm_without_global_authoring_wpm() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    assert "user_input.pacing_mode" in text
    assert "video_pacing_profile" in text
    assert "planned_narration_wpm" in text
    assert "single model-facing narration-speed **range** authority" in text
    assert "planned_average_wpm_range" not in text
    assert "script.planned_average_wpm" not in text


def test_hook_is_contractually_high_density() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    assert "Its realized visual pacing density is always `high`" in text
    assert "`sec_001` is contractually `high`" in text
    assert "enough genuine drawable progression to earn `high`" in text

def test_prompt_v11_is_domain_agnostic_and_analysis_complete() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    assert "D3444" not in text
    assert "archaeological individual" not in text
    assert "active channel authoring skill" in text
    assert "intentional perceptual space" in text
    assert "Do not optimize for the minimum cue count" in text
    assert "`section_guidance`" in text
    assert "`narrative_markers`" in text
    for field in (
        "`continuity_catalog.characters`",
        "`continuity_catalog.locations`",
        "`continuity_catalog.recurring_props`",
        "`continuity_catalog.recurring_visual_subjects`",
        "`continuity_catalog.visual_motifs`",
    ):
        assert field in text
    assert "Do not copy any WPM field into Analysis" in text


def test_final_human_performance_is_last_prose_gate_before_freeze() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    final_pass = "Final exact-text Human Performance / Virtual Table Read"
    freeze = "Freeze every section's narration text"
    assert final_pass in text
    assert "last prose-quality gate" in text
    assert "If it rewrites any prose, rerun every dependent pre-freeze gate" in text
    assert text.index(final_pass) < text.index(freeze)


def test_script_analysis_prompt_requires_production_ready_character_descriptions_without_schema_growth() -> None:
    user = _text("user.md")
    schema = PlatformContractValidator(ROOT).load_schema(SCHEMA_NAME)

    assert "# Character production-readiness contract" in user
    assert "minimum sufficient production specificity" in user
    for field in ("`description`", "`explicit_visual_traits`", "`identity_markers`", "`continuity_notes`"):
        assert field in user
    for requirement in (
        "approximate visual age or age band",
        "historical era, modern/other context",
        "relative body scale",
        "hair/grooming silhouette",
        "garment silhouette and material/context family",
        "recurring color assignment",
    ):
        assert requirement in user
    assert "production/reconstruction choices rather than recovered historical facts" in user
    assert "After Script freeze, Analysis may not convert several generic adults or children into one canonical person" in user
    assert "For `human_group`, describe it as a recurring group/social-unit continuity entity" in user
    assert "Character Analysis must not become a global rendering or image-style specification" in user
    assert "later visual-authoring stages receive their own visual-style guidance" in user
    assert "Character description is the authority for **who the character is" in user

    character_schema = schema["$defs"]["character_raw"]
    assert set(character_schema["properties"]) >= {
        "description",
        "explicit_visual_traits",
        "identity_markers",
        "continuity_notes",
    }


def test_prompt_uses_one_supplied_wpm_authority_and_never_hard_codes_a_band() -> None:
    system = _text("system.md")
    user = _text("user.md")
    text = system + "\n" + user
    assert "user_input.narration_wpm" in system
    assert "user_input.narration_wpm" in user
    assert "single model-facing narration-speed **range** authority" in system
    assert "inclusive section-WPM envelope" in user
    assert "145-180 WPM" not in text
    assert ">190 WPM" not in text
    assert "one exact integer `planned_narration_wpm`" in user
    assert "do not mechanically derive it from `fast | medium | slow` video pacing" in user


def test_script_runtime_input_rejects_reversed_wpm_range() -> None:
    from types import SimpleNamespace
    import pytest

    from app.api.errors import AppError
    from app.modules.platform.services import AuthoringService

    project = SimpleNamespace(
        channel_context_json={"name": "Test", "language": "English", "niche": "test", "audience": "test"},
        channel_dna_json={
            "narration_identity": {
                "voice": ["clear"],
                "delivery": ["direct"],
                "explanation_style": ["concrete"],
                "tone_boundaries": ["accurate"],
                "avoid": ["unsupported claims"],
            },
            "pacing_profiles": {
                mode: {
                    density: {"preferred_hold_seconds": {"min": low, "max": high}}
                    for density, (low, high) in {
                        "high": (2.0, 3.0),
                        "medium": (3.0, 4.0),
                        "low": (4.0, 5.0),
                    }.items()
                }
                for mode in ("fast", "medium", "slow")
            },
        },
    )
    with pytest.raises(AppError) as exc_info:
        AuthoringService._build_script_runtime_input(
            project,
            {"title": "Topic"},
            {
                "target_duration_minutes": 8,
                "pov_style": "second person",
                "avoid": [],
                "section_count_mode": "llm_decides",
                "pacing_mode": "fast",
                "narration_wpm": {"min": 240, "max": 200},
            },
        )
    assert exc_info.value.error_code == "SCRIPT_REQUEST_INVALID"


def test_prompt_preserves_channel_personality_human_translation_and_visual_refresh_semantics() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    assert "must not silently neutralize that supplied personality" in text
    assert "Human translation and entertainment fidelity" in text
    assert "ordinary-human translation" in text
    assert "independently drawable source-owned explanatory/editorial facets" in text
    assert "visual refresh opportunity, not narrative/emotional intensity" in text
    assert "Do not fabricate historical facts, motives, dialogue, behavior, or certainty" in text


def test_script_prompt_does_not_invent_current_visual_style_authority() -> None:
    text = _text("system.md") + "\n" + _text("user.md")
    assert "Supplied visual style guidance controls **how images are drawn**" not in text
    assert "upstream authorities" not in text
    assert "global rendering or image-style specification" in text


def test_script_prompt_explains_topic_idea_tone_authority() -> None:
    text = _text("user.md")
    assert "`topic_idea.tone`" in text
    assert "recommended tonal modulation inside the supplied `narration_identity`" in text
    assert "shape this Script's local mood or attitude" in text
    assert "does not replace the narration identity" in text
    assert "override its `tone_boundaries` or `avoid` guidance" in text
    assert "create factual authority" in text


def test_script_prompt_contract_has_exactly_one_model_facing_wpm_field() -> None:
    validator = PlatformContractValidator(ROOT)
    input_schema = validator.load_schema("generate_script_llm_input.schema.json")
    raw_output_schema = validator.load_schema("generate_script_llm_raw_output.schema.json")
    assert "narration_wpm" in input_schema["properties"]["user_input"]["properties"]
    assert "planned_narration_wpm_range" not in input_schema["properties"]["authoring_constraints"]["properties"]
    assert input_schema["properties"]["user_input"]["properties"]["narration_wpm"]["required"] == ["min", "max"]
    serialized_output = json.dumps(raw_output_schema, ensure_ascii=False)
    assert "planned_narration_wpm" in serialized_output
