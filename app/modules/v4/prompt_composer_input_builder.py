from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from app.api.prompt_composer_schemas_v4 import (
    PromptComposerInput,
    PromptComposerOptions,
)
from app.api.visual_plan_schemas_v4 import VisualPlanModel
from app.modules.v4.contracts import V4ContractValidator


class PromptComposerError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _critical_visual_qc_passed(payload: dict[str, Any]) -> bool:
    qc = payload.get("backend_qc")
    return isinstance(qc, dict) and all(
        value == "passed" for key, value in qc.items() if key.endswith("_status")
    )


def _index_exact(items: Any, key: str, *, code: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise PromptComposerError(code, f"{key} source list is invalid.")
    ids = [str(item.get(key)) for item in items]
    duplicates = _duplicates(ids)
    if duplicates:
        raise PromptComposerError(code, f"Duplicate {key} values are not allowed.", details={"duplicate_ids": duplicates})
    return {str(item[key]): item for item in items}


def build_prompt_composer_input(
    *,
    approved_visual_plan: dict[str, Any],
    text_approved_style_bible: dict[str, Any],
    canonical_script_analysis: dict[str, Any],
    options: dict[str, Any],
    prompt_bundle_revision_id: str,
) -> dict[str, Any]:
    """Build Prompt Composer input from every beat in approved Visual Plan 3.0 + Style Bible V4."""
    try:
        visual_plan = VisualPlanModel.model_validate(deepcopy(approved_visual_plan))
        style_payload = V4ContractValidator().validate(
            "project_style_bible_v1.schema.json",
            deepcopy(text_approved_style_bible),
            error_code="PROMPT_COMPOSER_SOURCE_INVALID",
        )
        parsed_options = PromptComposerOptions.model_validate(deepcopy(options))
        analysis_payload = V4ContractValidator().validate(
            "script_analysis_v1.schema.json",
            deepcopy(canonical_script_analysis),
            error_code="PROMPT_COMPOSER_SOURCE_INVALID",
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, PromptComposerError):
            raise
        raise PromptComposerError("PROMPT_COMPOSER_SOURCE_INVALID", "Prompt Composer source contract is invalid.") from exc

    if visual_plan.status != "approved":
        raise PromptComposerError("PROMPT_COMPOSER_VISUAL_PLAN_NOT_APPROVED", "An approved Visual Plan is required.")
    if style_payload.get("status") != "text_approved":
        raise PromptComposerError("PROMPT_COMPOSER_STYLE_BIBLE_NOT_TEXT_APPROVED", "A text-approved Style Bible is required.")
    if visual_plan.source_style_bible_revision_id != style_payload.get("style_bible_revision_id"):
        raise PromptComposerError("PROMPT_COMPOSER_SOURCE_LINEAGE_MISMATCH", "Visual Plan and Style Bible lineage do not match.")
    if (
        analysis_payload.get("source_script_revision_id") != visual_plan.source_script_revision_id
        or analysis_payload.get("project_id") != visual_plan.project_id
    ):
        raise PromptComposerError("PROMPT_COMPOSER_SOURCE_LINEAGE_MISMATCH", "Visual Plan and Script Analysis lineage do not match.")
    lineage = style_payload.get("lineage") or {}
    if lineage.get("script_analysis_artifact_id") != visual_plan.source_script_analysis_revision_id:
        raise PromptComposerError("PROMPT_COMPOSER_SOURCE_LINEAGE_MISMATCH", "Visual Plan and Style Bible Script Analysis lineage do not match.")
    if not _critical_visual_qc_passed(approved_visual_plan):
        raise PromptComposerError("PROMPT_COMPOSER_VISUAL_PLAN_QA_FAILED", "Visual Plan critical QA must pass before prompt composition.")

    character_index = _index_exact(style_payload.get("characters"), "char_id", code="PROMPT_COMPOSER_DUPLICATE_STYLE_ID")
    location_index = _index_exact(style_payload.get("locations"), "loc_id", code="PROMPT_COMPOSER_DUPLICATE_STYLE_ID")
    prop_index = _index_exact(style_payload.get("recurring_props"), "prop_id", code="PROMPT_COMPOSER_DUPLICATE_STYLE_ID")
    subject_index = _index_exact(style_payload.get("recurring_visual_subjects"), "subject_id", code="PROMPT_COMPOSER_DUPLICATE_STYLE_ID")
    motif_index = _index_exact(style_payload.get("motifs"), "motif_id", code="PROMPT_COMPOSER_DUPLICATE_STYLE_ID")

    plan_payload = visual_plan.model_dump(mode="json")
    all_beats = [
        beat
        for scene in sorted(plan_payload["scenes"], key=lambda item: item["scene_order"])
        for beat in sorted(scene["beat_plans"], key=lambda item: item["beat_order"])
    ]
    beat_ids = [beat["beat_id"] for beat in all_beats]
    beat_orders = [beat["beat_order"] for beat in all_beats]
    if _duplicates(beat_ids) or _duplicates([str(value) for value in beat_orders]):
        raise PromptComposerError("PROMPT_COMPOSER_DUPLICATE_BEAT_ID", "Visual Plan beat identity and order must be unique.")
    if beat_orders != sorted(beat_orders):
        raise PromptComposerError("PROMPT_COMPOSER_BEAT_ORDER_INVALID", "Visual Plan beats must preserve global order.")

    generated = all_beats
    character_refs = _ordered_unique([ref for beat in generated for ref in beat["selected_character_refs"]])
    location_refs = _ordered_unique([ref for beat in generated for ref in beat["selected_location_refs"]])
    prop_refs = _ordered_unique([ref for beat in generated for ref in beat["selected_prop_refs"]])
    subject_refs = _ordered_unique([ref for beat in generated for ref in beat["selected_recurring_visual_subject_refs"]])
    motif_refs = _ordered_unique([ref for beat in generated for ref in beat["selected_motif_refs"]])

    for ref, index, code, label in (
        *[(ref, character_index, "PROMPT_COMPOSER_UNKNOWN_CHARACTER_REF", "character") for ref in character_refs],
        *[(ref, location_index, "PROMPT_COMPOSER_UNKNOWN_LOCATION_REF", "location") for ref in location_refs],
        *[(ref, prop_index, "PROMPT_COMPOSER_UNKNOWN_PROP_REF", "prop") for ref in prop_refs],
        *[(ref, subject_index, "PROMPT_COMPOSER_UNKNOWN_RECURRING_SUBJECT_REF", "recurring visual subject") for ref in subject_refs],
        *[(ref, motif_index, "PROMPT_COMPOSER_UNKNOWN_MOTIF_REF", "motif") for ref in motif_refs],
    ):
        if ref not in index:
            raise PromptComposerError(code, f"Visual Plan references an unknown {label}.", details={"id": ref})

    def pick(index: dict[str, dict[str, Any]], ref: str, fields: tuple[str, ...]) -> dict[str, Any]:
        item = index[ref]
        return {field: deepcopy(item[field]) for field in fields}

    visual_fields = (
        "scene_id", "beat_id", "beat_order", "local_beat_order", "continuity_group_id", "visual_priority",
        "is_narrative_marker", "visual_context_ref", "representation_family", "selected_character_refs", "selected_location_refs", "selected_prop_refs",
        "selected_recurring_visual_subject_refs", "selected_motif_refs", "selected_native_visual_reference_ids",
        "visual_concept", "subject_action",
        "environment_emphasis", "framing", "camera_angle", "camera_movement", "composition",
        "lighting_direction", "color_emphasis", "motion_guidance", "continuity_with_previous",
        "previous_beat_image_reference_required",
        "continuity_notes", "production_notes", "negative_requirements", "visible_text",
        "selected_reference_bindings",
    )
    bounded_beats = [{field: deepcopy(beat[field]) for field in visual_fields} for beat in generated]
    payload = {
        "schema_version": "3.0",
        "project_id": visual_plan.project_id,
        "source_script_revision_id": visual_plan.source_script_revision_id,
        "source_script_analysis_revision_id": visual_plan.source_script_analysis_revision_id,
        "source_style_bible_revision_id": visual_plan.source_style_bible_revision_id,
        "source_scene_plan_revision_id": visual_plan.source_scene_plan_revision_id,
        "source_visual_plan_revision_id": visual_plan.visual_plan_revision_id,
        "source_visual_reference_approval_set_artifact_id": visual_plan.source_visual_reference_approval_set_artifact_id,
        "prompt_bundle_revision_id": prompt_bundle_revision_id,
        "options": parsed_options.model_dump(mode="json"),
        "visual_style_snapshot": deepcopy(style_payload["visual_style_snapshot"]),
        "visual_context_catalog": {
            str(item["context_id"]): {
                "mode": item["mode"],
                "timeframe": item["timeframe"],
                "setting": item["setting"],
                "constraints": deepcopy(item["constraints"]),
            }
            for item in analysis_payload["visual_context_catalog"]
        },
        "style_entities": {
            "characters": [pick(character_index, ref, ("char_id", "label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints")) for ref in character_refs],
            "locations": [pick(location_index, ref, ("loc_id", "label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints")) for ref in location_refs],
            "recurring_props": [pick(prop_index, ref, ("prop_id", "label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints")) for ref in prop_refs],
            "recurring_visual_subjects": [pick(subject_index, ref, ("subject_id", "label", "description", "visual_type", "explicit_visual_traits", "continuity_notes", "physical_constraints")) for ref in subject_refs],
            "motifs": [pick(motif_index, ref, ("motif_id", "description")) for ref in motif_refs],
        },
        "visual_beats": bounded_beats,
    }
    try:
        return PromptComposerInput.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise PromptComposerError("PROMPT_COMPOSER_INPUT_INVALID", "Bounded Prompt Composer input is invalid.") from exc
