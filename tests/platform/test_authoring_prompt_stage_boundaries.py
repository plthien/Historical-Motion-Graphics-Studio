from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.external_authoring import bundle_prompt_schema


ROOT = Path(__file__).resolve().parents[2]
TOPIC = ROOT / "contracts" / "prompts" / "platform" / "generate_topic_ideas"
SCRIPT = ROOT / "contracts" / "prompts" / "platform" / "generate_script_and_analysis"
SCENE = ROOT / "contracts" / "prompts" / "v4" / "scene_breakdown_full"
SCENE_STEP_1 = ROOT / "contracts" / "prompts" / "v4" / "scene_breakdown_scene"
SCENE_STEP_2 = ROOT / "contracts" / "prompts" / "v4" / "scene_breakdown_visual"

ACTIVE_PROMPT_ROOTS = (TOPIC, SCRIPT, SCENE_STEP_1, SCENE_STEP_2)
ACTIVE_PROMPT_FACING_SCHEMA_FILES = (
    ROOT / "contracts" / "schemas" / "platform" / "topic_ideas_llm_output.schema.json",
    ROOT / "contracts" / "schemas" / "platform" / "script_and_analysis_external_output_v2.schema.json",
    ROOT / "contracts" / "schemas" / "platform" / "generate_script_llm_raw_output.schema.json",
    ROOT / "contracts" / "schemas" / "platform" / "script_and_analysis_external_output_v1.schema.json",
    ROOT / "contracts" / "schemas" / "v4" / "scene_breakdown_scene_authoring_input_v2.schema.json",
    ROOT / "contracts" / "schemas" / "v4" / "scene_breakdown_scene_llm_output_v2.schema.json",
    ROOT / "contracts" / "schemas" / "v4" / "scene_breakdown_visual_llm_output_v2.schema.json",
)


def _prompt(root: Path) -> str:
    return (root / "system.md").read_text(encoding="utf-8") + "\n" + (
        root / "user.md"
    ).read_text(encoding="utf-8")


def _schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_each_authoring_prompt_declares_task_contract_vs_skill_ownership() -> None:
    for root in ACTIVE_PROMPT_ROOTS:
        text = _prompt(root)
        lower = text.lower()
        assert "stage contract" in lower or "prompt owns" in lower
        assert "skill" in text.lower()
        assert "how" in text.lower()
        assert "authoritative" in text.lower()


def test_active_authoring_prompts_do_not_expose_channel_dna_provenance() -> None:
    for root in ACTIVE_PROMPT_ROOTS:
        text = _prompt(root)
        assert "Channel DNA" not in text
        assert "frozen Channel DNA" not in text
        assert "Project-snapshotted" not in text
        assert "Zenn" not in text


def test_legacy_combined_scene_prompt_is_not_classified_active_runtime() -> None:
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(scene_breakdown_two_step_router)" in main
    assert "_disable_legacy_combined_scene_authoring()" in main
    assert "get_pending_scene_breakdown_prompt_v4" in main
    assert SCENE not in ACTIVE_PROMPT_ROOTS


def test_active_prompt_facing_schemas_do_not_expose_channel_dna_provenance() -> None:
    for path in ACTIVE_PROMPT_FACING_SCHEMA_FILES:
        text = path.read_text(encoding="utf-8")
        assert "Channel DNA" not in text
        assert "frozen Channel DNA" not in text
        assert "Project-snapshotted" not in text
        assert "Zenn" not in text


def test_active_prompt_input_contracts_explain_runtime_fields_outside_input_json() -> None:
    expectations = {
        TOPIC: (
            "`channel_context.name`: The channel display name only",
            "`channel_context.language`",
            "`channel_context.niche`",
            "`channel_context.audience`",
            "`narration_identity`",
            "`pacing_profiles`",
            "`high`, `medium`, and `low` are visual-density bands",
            "`preferred_hold_seconds.min/max`",
            "`user_input.seed_topic`",
            "`user_input.topic_count`",
            "`user_input.focus_keywords`",
            "`user_input.focus`",
            "`user_input.avoid_topics`",
        ),
        SCRIPT: (
            "`channel_context.name`: The channel display name only",
            "`channel_context.language`",
            "`channel_context.niche`",
            "`channel_context.audience`",
            "`narration_identity`",
            "`topic_idea`",
            "`topic_idea.tone`",
            "`title`, `core_question`, `viewer_hook`, and `topic_angle`",
            "`content_tags`",
            "`risk_notes`",
            "Current choices override Topic-level recommendations",
            "`user_input.target_duration_minutes`",
            "`user_input.pov_style`",
            "`user_input.avoid`",
            "`user_input.section_count_mode`",
            "`user_input.pacing_mode`",
            "`user_input.narration_wpm`",
            "`video_pacing_profile.mode`",
            "`video_pacing_profile.profile_name`",
            "`video_pacing_profile.policy`",
            "`video_pacing_profile.density_profiles.high|medium|low`",
            "`authoring_constraints`",
            "`user_input.narration_wpm` is the single model-facing narration-speed **range** authority",
        ),
        SCENE_STEP_1: (
            "`artifact_id`",
            "`title`",
            "`sections`",
            "`section_id`",
            "`section_order`",
            "`section_title`",
            "`section_role`",
            "`preferred_hold_seconds`",
            "`scene_authoring_plan.sections[].section_id`",
            "`scene_key`",
            "`source_segments`",
            "`source_segments[].text`",
            "`source_segments[].boundary_id_after`",
            "Backend supplies fixed micro-Scenes and technically legal Beat boundaries only",
            "`boundary_ids`",
            "`boundary_ids: []`",
        ),
        SCENE_STEP_2: (
            "`scene_plan_revision_id`",
            "read-only linkage/source metadata",
            "`scene_count`: Coverage total",
            "It is not a creative target or quota",
            "`beat_count`: Coverage total",
            "`locked_scenes`",
            "`locked_scenes[].scene_id`",
            "`locked_scenes[].section_id`",
            "`beat_id`",
            "`script_quote`",
            "`meaning_now`",
            "`visible_state_now`",
            "`delta_now`",
            "`continuity_with_previous`",
            "`eligible_character_refs`",
            "`visual_context.style.medium`",
            "`visual_context.style.linework`",
            "`visual_context.style.rendering`",
            "`visual_context.style.palette`",
            "`visual_context.style.editorial_language`",
            "`visual_context.reference_catalog`",
            "For motifs, use `label` and `description`",
            "`visual_traits` to preserve its supplied stable visible characteristics",
            "`evidence_script_texts` only as direct supplied anchors for motif eligibility",
            "Motif descriptors do not authorize a new story fact or character/location/prop/subject presence",
            "motif eligibility does not force selection",
        ),
    }
    for root, required_phrases in expectations.items():
        text = _prompt(root)
        instruction_text = text.split("# Input JSON", 1)[0].split("# Locked Scene Plan Candidate and Visual Context", 1)[0]
        for phrase in required_phrases:
            assert phrase in instruction_text


def test_active_prompts_have_current_run_source_authority() -> None:
    for root in ACTIVE_PROMPT_ROOTS:
        text = _prompt(root)
        if root == SCENE_STEP_1:
            assert "Use only the supplied task inputs and the current authoring Skill." in text
            assert "External research, remembered facts, prior chats, and outside knowledge do not authorize Beat meaning" in text
            assert "current task inputs and fresh external research performed during this run" not in text
            assert "prior chats" in text
        else:
            assert "current task inputs and fresh external research performed during this run" in text
            assert (
                "prior chats/sessions" in text
                or "previous-chat factual conclusions" in text
                or "previous-chat findings" in text
            )


def test_active_model_facing_text_avoids_unnecessary_hidden_provenance() -> None:
    forbidden = (
        "Channel DNA",
        "Project-snapshotted",
        "frozen Channel",
        "Style Bible",
        "Zenn",
        "NarrationWordTimingMap",
        "Narration Audio",
        "upstream authorities",
        "backend authority",
        "approved pair",
        "application assigns",
        "application derives",
        "application resolves",
        "application-owned",
        "after import",
    )
    prompt_text = "\n".join(_prompt(root) for root in ACTIVE_PROMPT_ROOTS)
    bundled_schema_text = "\n".join(
        json.dumps(bundle_prompt_schema(_schema(path)), ensure_ascii=False)
        for path in ACTIVE_PROMPT_FACING_SCHEMA_FILES
    )
    combined = prompt_text + "\n" + bundled_schema_text
    for leaked in forbidden:
        assert leaked not in combined


def test_topic_prompt_activates_channel_skill_only_for_topic_stage() -> None:
    text = _prompt(TOPIC)
    assert "active channel authoring skill for the Topic Ideas stage" in text
    assert "Script Storytelling" not in text
    assert "Narration Performance" not in text
    assert "Scene Directing" not in text


def test_script_prompt_has_explicit_stage_role_switches_and_freeze_order() -> None:
    text = _prompt(SCRIPT)
    assert "active channel authoring skill" in text
    assert "Narration Performance" in text
    assert "Voice Annotation" in text
    assert "Script Analysis" in text
    assert "downstream Scene directing as a Script rewriting authority" in text
    composition = text.index("Narration Performance composition/rehearsal pass")
    final_pass = text.index("Final exact-text Human Performance / Virtual Table Read")
    freeze = text.index("Freeze every section's narration text")
    voice_annotation = text.index("create `spoken_overrides` and `delivery_cues`")
    analysis = text.index("Analyze only that exact frozen Script")
    assert composition < final_pass < freeze < voice_annotation < analysis
    assert "last prose-quality gate" in text


def test_script_prompt_preserves_canonical_authored_notation_before_spoken_overrides() -> None:
    text = _prompt(SCRIPT)
    assert "Canonical Script narration is **authored-text truth**" in text
    assert "Model X204 costs $19.50." in text
    assert "Model X two zero four costs nineteen dollars and fifty cents." in text
    assert "D3444" not in text
    assert "Do NOT spell such values out merely to improve TTS pronunciation" in text
    assert "do not pre-normalize those tokens out of authored narration" in text


def test_script_prompt_requires_deliberate_pause_audit_without_mandatory_cues() -> None:
    text = _prompt(SCRIPT)
    assert "explicit deliberate-pause audit for every section" in text
    assert "even when it is legitimately empty" in text
    assert "delivery-cue candidates, **not automatic pauses**" in text
    assert "Do not output milliseconds, seconds" in text


def test_script_prompt_requires_human_narration_performance_before_freeze() -> None:
    text = _prompt(SCRIPT)
    for phrase in (
        "natural human rhythm",
        "syntactic and rhythmic variation",
        "thought grouping",
        "breathability",
        "anti-robotic cadence review",
    ):
        assert phrase in text


def test_scene_prompt_activates_channel_directing_after_script_and_voice_are_frozen() -> None:
    text = _prompt(SCENE)
    assert "single current authoring skill already present in the current context" in text
    assert "Do not trim, normalize, recreate, add, remove, rewrite" in text
    assert "Narration delivery is not evidence" in text
    assert "approved Narration Audio + NarrationWordTimingMap pair" in text
    assert "visual_cadence_guidance" in text
    assert "Timing must never directly create, remove, split, merge" in text
    assert "Beat count and scene count are semantic outcomes" in text
    assert "upstream Script-composition guidance" in text
    assert "A Beat may legitimately remain shorter or longer than the preferred hold guidance" in text
    assert "There is no cadence-compliance quota" in text
    assert "pacing-conformance" not in text
    assert "previous_scene_purpose" not in text
    assert "previous_visual_tail" not in text
    for leaked in ("planned_narration_wpm", "video_pacing_profile", "pacing_mode", "pacing_density"):
        assert leaked not in text


def test_scene_prompt_delegates_channel_directing_craft_but_keeps_contract_authority() -> None:
    text = _prompt(SCENE)
    normalized = text.replace("**", "")
    assert "single current authoring skill already present in the current context" in text
    assert "owns HOW to perform the creative Scene Breakdown work" in normalized
    assert "Canonical reference authority" in text
    assert "The application owns final Beat timing arithmetic" in text
    assert "Every Beat must contain exactly one complete generated-still `visual` object" in text
    assert "Do not search for, load, infer, retrieve, or substitute any other authoring skill" in text


def test_scene_prompt_restores_bidirectional_semantic_parity_without_numeric_targets() -> None:
    text = _prompt(SCENE)
    assert "smallest consecutive narration span" in text
    assert "two or more independently drawable state changes" in text
    assert "strongest still on both sides would be materially the same" in text
    assert "The rescan is mandatory when materially indicated; a structural change is not." in text
    assert "The existence of `graphic_layout` in the schema does not authorize using it to avoid a genuine semantic Beat boundary." in text
    assert "Several consecutive Beats must not collapse into repeated branch diagrams" in text
    assert "Canonical identity continuity alone does not justify a non-`new_setup` relation." in text
    assert "This prompt does not supply a separate `evidence_mode` field." in text
    assert "target_beat_count_for_section" not in text
    assert "section duration ÷ preferred seconds" not in text
