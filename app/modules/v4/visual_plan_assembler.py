from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.api.visual_plan_schemas_v4 import (
    VisualPlanModel,
    VisualPlanApprovedReferenceBinding,
)
from app.modules.v4.contracts import V4ContractValidator


class VisualPlanProjectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


_REPRESENTATION_FAMILIES = (
    "character_environment_lived",
    "object_evidence_closeup",
    "text_diagram_editorial",
)


def _representation_family_qc(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {family: 0 for family in _REPRESENTATION_FAMILIES}
    for scene in scenes:
        for beat in scene.get("beat_plans", []):
            family = str(beat.get("representation_family") or "")
            if family in counts:
                counts[family] += 1
    total = sum(counts.values())
    percentages = {
        family: round((count / total) * 100, 2) if total else 0.0
        for family, count in counts.items()
    }
    return {
        "total_beat_count": total,
        "counts": counts,
        "percentages": percentages,
        "complete_visual_plan": total > 0,
    }


def empty_visual_plan_report(*, scenes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    scenes = scenes or []
    return {
        "scene_coverage_status": "passed",
        "beat_coverage_status": "passed",
        "order_status": "passed",
        "source_identity_status": "passed",
        "referential_integrity_status": "passed",
        "treatment_validity_status": "passed",
        "continuity_status": "passed",
        "forbidden_field_status": "passed",
        "reasoning_leakage_status": "passed",
        "missing_scene_ids": [],
        "duplicate_scene_ids": [],
        "missing_beat_ids": [],
        "duplicate_beat_ids": [],
        "reordered_beat_ids": [],
        "unknown_ref_errors": [],
        "invalid_treatment_errors": [],
        "continuity_warnings": [],
        "forbidden_field_errors": [],
        "reasoning_leakage_errors": [],
        "representation_family_qc": _representation_family_qc(scenes),
        "warnings": [],
    }


def _beat_character_refs(beat: dict[str, Any]) -> list[str]:
    return [str(value) for value in beat.get("eligible_character_refs", [])]


def _beat_location_refs(beat: dict[str, Any]) -> list[str]:
    return [str(value) for value in beat.get("eligible_location_refs", [])]


def _validate_binding_registry(
    bindings: list[dict[str, Any]] | None,
    style_bible: dict[str, Any],
    scene_plan: dict[str, Any],
    approval_set_artifact_id: str | None,
) -> dict[str, dict[str, Any]]:
    allowed = {
        "character": {item["char_id"] for item in style_bible.get("characters", [])},
        "location": {item["loc_id"] for item in style_bible.get("locations", [])},
        "prop": {item["prop_id"] for item in style_bible.get("recurring_props", [])},
        "recurring_visual_subject": {
            item["subject_id"] for item in style_bible.get("recurring_visual_subjects", [])
        },
    }
    used: set[tuple[str, str]] = set()
    for scene in scene_plan.get("scenes", []):
        for beat in scene.get("beats", []):
            used.update(("character", identity) for identity in _beat_character_refs(beat))
            used.update(("location", identity) for identity in _beat_location_refs(beat))
            used.update(("prop", identity) for identity in beat.get("eligible_prop_refs", []))
            used.update(
                ("recurring_visual_subject", identity)
                for identity in beat.get("eligible_recurring_visual_subject_refs", [])
            )
    result_by_anchor: dict[tuple[str, str], dict[str, Any]] = {}
    result_by_reference: dict[str, dict[str, Any]] = {}
    for raw in deepcopy(bindings or []):
        binding = VisualPlanApprovedReferenceBinding.model_validate(raw)
        key = (binding.anchor_type, binding.anchor_id)
        if key in result_by_anchor:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_REFERENCE_BINDING_INVALID",
                "Reference bindings must be unique by exact anchor.",
            )
        if binding.reference_artifact_id in result_by_reference:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_REFERENCE_BINDING_INVALID",
                "Reference bindings must be unique by reference artifact.",
            )
        if approval_set_artifact_id is None or binding.approval_set_artifact_id != approval_set_artifact_id:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_REFERENCE_BINDING_LINEAGE_MISMATCH",
                "Reference bindings must come from the exact current approval set.",
            )
        if binding.anchor_id not in allowed[binding.anchor_type]:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_REFERENCE_BINDING_INVALID",
                "A reference binding targets an identity not present in Style Bible V4.",
            )
        if key not in used:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_REFERENCE_BINDING_INVALID",
                "A reference binding targets an identity unused by the approved Scene Plan.",
            )
        payload = binding.model_dump(mode="json")
        result_by_anchor[key] = payload
        result_by_reference[binding.reference_artifact_id] = payload
    if approval_set_artifact_id is None and result_by_reference:
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_REFERENCE_BINDING_LINEAGE_MISMATCH",
            "Reference bindings require an approval-set identity.",
        )
    return result_by_reference


def _selected_bindings_for_beat(
    beat: dict[str, Any], registry: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for reference_id in beat.get("selected_native_visual_reference_ids", []):
        binding = registry.get(reference_id)
        if binding is None:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_SELECTED_REFERENCE_INVALID",
                "Selected native visual reference is not in the approved eligible registry.",
            )
        result.append(deepcopy(binding))
    return result


def assemble_visual_plan(
    *,
    approved_scene_plan: dict[str, Any],
    text_approved_style_bible: dict[str, Any],
    ordered_scene_fragments: list[dict[str, Any]],
    visual_plan_revision_id: str,
    parent_visual_plan_revision_id: str | None,
    source_run_artifact_id: str,
    revision_operation: str,
    status: str,
    canonical_reference_bindings: list[dict[str, Any]] | None = None,
    source_visual_reference_approval_set_artifact_id: str | None = None,
) -> dict[str, Any]:
    validator = V4ContractValidator()
    try:
        scene_plan = validator.validate(
            "scene_plan_v1.schema.json", deepcopy(approved_scene_plan), error_code="VISUAL_PLAN_SOURCE_INVALID"
        )
        style = validator.validate(
            "project_style_bible_v1.schema.json", deepcopy(text_approved_style_bible), error_code="VISUAL_PLAN_STYLE_BIBLE_INVALID"
        )
    except Exception as exc:  # noqa: BLE001
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SOURCE_INVALID", "Visual Plan source artifacts are invalid."
        ) from exc
    if scene_plan.get("status") != "approved" or scene_plan.get("schema_version") != "3.0":
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SOURCE_NOT_APPROVED",
            "Visual Plan assembly requires an approved Scene Plan 2.0.",
        )
    if style.get("status") != "text_approved":
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_STYLE_BIBLE_NOT_TEXT_APPROVED",
            "Visual Plan assembly requires a text-approved Style Bible V4.",
        )
    if style.get("style_bible_revision_id") != scene_plan.get("source_style_bible_revision_id"):
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SOURCE_IDENTITY_MISMATCH",
            "Style Bible revision does not match the approved Scene Plan.",
        )

    fragments = deepcopy(ordered_scene_fragments)
    expected_scene_ids = [scene["scene_id"] for scene in scene_plan["scenes"]]
    actual_scene_ids = [fragment.get("scene_id") for fragment in fragments]
    if actual_scene_ids != expected_scene_ids or len(actual_scene_ids) != len(set(actual_scene_ids)):
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SCENE_ORDER_INVALID",
            "One unique normalized fragment is required for every source scene in exact order.",
        )

    registry = _validate_binding_registry(
        canonical_reference_bindings,
        style,
        scene_plan,
        source_visual_reference_approval_set_artifact_id,
    )
    source_scenes = {scene["scene_id"]: scene for scene in scene_plan["scenes"]}
    global_continuity_number = 0
    current_group_id: str | None = None
    has_previous_beat = False
    normalized_scenes: list[dict[str, Any]] = []

    for fragment in fragments:
        source_scene = source_scenes[fragment["scene_id"]]
        if (
            fragment.get("scene_order") != source_scene["scene_order"]
            or fragment.get("section_id") != source_scene["section_id"]
        ):
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_SOURCE_IDENTITY_MISMATCH",
                "Normalized scene fragment identity does not match Scene Plan 2.0.",
            )
        source_beats = source_scene["beats"]
        fragment_beats = fragment.get("beat_plans")
        if not isinstance(fragment_beats, list) or [beat.get("beat_id") for beat in fragment_beats] != [
            beat["beat_id"] for beat in source_beats
        ]:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_BEAT_ORDER_INVALID",
                "Normalized scene fragments must cover source beats in exact order.",
            )

        normalized_beats: list[dict[str, Any]] = []
        expected_local_group = 0
        for index, (source_beat, fragment_beat) in enumerate(
            zip(source_beats, fragment_beats, strict=True)
        ):
            relation = fragment_beat.get("continuity_with_previous")
            if not has_previous_beat and relation != "new_setup":
                raise VisualPlanProjectionError(
                    "VISUAL_PLAN_CONTINUITY_INVALID",
                    "The first Beat of the whole Visual Plan must start a new setup.",
                )
            if index == 0 or relation == "new_setup":
                expected_local_group += 1
            if current_group_id is None or relation == "new_setup":
                global_continuity_number += 1
                current_group_id = f"continuity_{global_continuity_number:03d}"
            if fragment_beat.get("local_continuity_group_index") != expected_local_group:
                raise VisualPlanProjectionError(
                    "VISUAL_PLAN_CONTINUITY_INVALID",
                    "Local continuity grouping is inconsistent with continuity relations.",
                )
            assert current_group_id is not None
            backend_facts = {
                "scene_id": source_scene["scene_id"],
                "beat_id": source_beat["beat_id"],
                "visual_context_ref": source_beat["visual_context_ref"],
                "beat_order": source_beat["beat_order"],
                "local_beat_order": source_beat["local_beat_order"],
                "script_quote": source_beat["script_quote"],
                "resolved_script_span": deepcopy(source_beat["resolved_script_span"]),
                "computed_duration_seconds": source_beat["estimated_duration_seconds"],
                "visual_priority": source_beat["visual_priority"],
                "is_narrative_marker": source_beat["is_narrative_marker"],
                "narrative_marker_refs": deepcopy(source_beat["narrative_marker_refs"]),
                "narrative_marker_types": deepcopy(source_beat["narrative_marker_types"]),
            }
            backend_owned = {
                "scene_id", "beat_id", "beat_order", "local_beat_order", "script_quote",
                "resolved_script_span", "computed_duration_seconds", "visual_priority",
                "is_narrative_marker", "narrative_marker_refs", "narrative_marker_types",
                "local_continuity_group_index", "continuity_group_id",
                "selected_reference_bindings",
            }
            decision_fields = {
                key: deepcopy(value)
                for key, value in fragment_beat.items()
                if key not in backend_owned
            }
            normalized_beats.append(
                {
                    **backend_facts,
                    **decision_fields,
                    "continuity_group_id": current_group_id,
                    "selected_reference_bindings": _selected_bindings_for_beat(
                        decision_fields, registry
                    ),
                }
            )
            has_previous_beat = True

        normalized_scenes.append(
            {
                "scene_id": source_scene["scene_id"],
                "scene_order": source_scene["scene_order"],
                "section_id": source_scene["section_id"],
                "scene_continuity_notes": fragment["scene_continuity_notes"],
                "beat_plans": normalized_beats,
            }
        )

    plan = {
        "artifact_type": "visual_plan",
        "schema_version": "3.0",
        "project_id": scene_plan["project_id"],
        "visual_plan_revision_id": visual_plan_revision_id,
        "parent_visual_plan_revision_id": parent_visual_plan_revision_id,
        "revision_operation": revision_operation,
        "source_script_revision_id": scene_plan["source_script_revision_id"],
        "source_script_analysis_revision_id": scene_plan[
            "source_script_analysis_revision_id"
        ],
        "source_style_bible_revision_id": scene_plan["source_style_bible_revision_id"],
        "source_scene_plan_revision_id": scene_plan["scene_plan_revision_id"],
        "source_visual_reference_approval_set_artifact_id": source_visual_reference_approval_set_artifact_id,
        "source_run_artifact_id": source_run_artifact_id,
        "status": status,
        "scene_count": len(normalized_scenes),
        "beat_count": sum(len(scene["beat_plans"]) for scene in normalized_scenes),
        "scenes": normalized_scenes,
        "backend_qc": empty_visual_plan_report(scenes=normalized_scenes),
    }
    return VisualPlanModel.model_validate(plan).model_dump(mode="json")


def approve_visual_plan(current_plan: dict[str, Any], new_revision_id: str) -> dict[str, Any]:
    current = VisualPlanModel.model_validate(deepcopy(current_plan))
    if current.status != "draft_ready":
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_NOT_DRAFT_READY", "Only a draft-ready Visual Plan can be approved."
        )
    payload = current.model_dump(mode="json")
    payload["visual_plan_revision_id"] = new_revision_id
    payload["parent_visual_plan_revision_id"] = current.visual_plan_revision_id
    payload["revision_operation"] = "approval"
    payload["status"] = "approved"
    return VisualPlanModel.model_validate(payload).model_dump(mode="json")


def fragments_from_visual_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = VisualPlanModel.model_validate(deepcopy(plan))
    fragments: list[dict[str, Any]] = []
    for scene in parsed.scenes:
        local_group = 0
        current_global: str | None = None
        beats: list[dict[str, Any]] = []
        for beat in scene.beat_plans:
            if beat.continuity_group_id != current_global:
                local_group += 1
                current_global = beat.continuity_group_id
            value = beat.model_dump(mode="json")
            value.pop("continuity_group_id", None)
            value.pop("selected_reference_bindings", None)
            value["local_continuity_group_index"] = local_group
            beats.append(value)
        fragments.append(
            {
                "scene_id": scene.scene_id,
                "scene_order": scene.scene_order,
                "section_id": scene.section_id,
                "scene_continuity_notes": scene.scene_continuity_notes,
                "beat_plans": beats,
            }
        )
    return fragments

NO_BEAT_LIGHTING_OVERRIDE = "No beat-specific lighting override."
NO_BEAT_COLOR_OVERRIDE = "No beat-specific color override."
SCENE_CONTINUITY_PROJECTION_NOTE = "Beat continuity is represented by continuity_with_previous."


def _combined_style_notes(style_bible: dict[str, Any]) -> tuple[str, list[str]]:
    snapshot = style_bible.get("visual_style_snapshot")
    identity = snapshot.get("visual_identity") if isinstance(snapshot, dict) else None
    if not isinstance(identity, dict):
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_STYLE_BIBLE_INVALID",
            "The text-approved Style Bible does not contain a valid visual style snapshot.",
        )
    parts: list[str] = []
    for field in ("medium", "linework", "rendering"):
        values = identity.get(field)
        if isinstance(values, list):
            parts.extend(str(value).strip() for value in values if str(value).strip())
    if not parts:
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_STYLE_BIBLE_INVALID",
            "The text-approved Style Bible does not contain production style instructions.",
        )
    negatives = [
        str(value).strip()
        for value in snapshot.get("global_negative_constraints", [])
        if str(value).strip()
    ]
    return "; ".join(parts), negatives


def _combined_binding_index(
    *,
    canonical_reference_bindings: list[dict[str, Any]] | None,
    approval_set_artifact_id: str | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    seen_refs: set[str] = set()
    for raw in deepcopy(canonical_reference_bindings or []):
        binding = VisualPlanApprovedReferenceBinding.model_validate(raw)
        if approval_set_artifact_id is None or binding.approval_set_artifact_id != approval_set_artifact_id:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_REFERENCE_BINDING_LINEAGE_MISMATCH",
                "Approved reference bindings must come from the exact current approval set.",
            )
        key = (binding.anchor_type, binding.anchor_id)
        if key in result or binding.reference_artifact_id in seen_refs:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_REFERENCE_BINDING_INVALID",
                "Approved reference bindings must be unique by anchor and reference artifact.",
            )
        result[key] = binding.model_dump(mode="json")
        seen_refs.add(binding.reference_artifact_id)
    if approval_set_artifact_id is None and result:
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_REFERENCE_BINDING_LINEAGE_MISMATCH",
            "Approved reference bindings require an approval-set identity.",
        )
    return result


def assemble_visual_plan_from_scene_breakdown(
    *,
    scene_plan: dict[str, Any],
    text_approved_style_bible: dict[str, Any],
    ordered_sections: list[dict[str, Any]],
    visual_plan_revision_id: str,
    parent_visual_plan_revision_id: str | None,
    source_run_artifact_id: str,
    revision_operation: str,
    status: str,
    canonical_reference_bindings: list[dict[str, Any]] | None = None,
    source_visual_reference_approval_set_artifact_id: str | None = None,
) -> dict[str, Any]:
    """Project validated combined Scene Breakdown decisions into Visual Plan 3.0.

    This function performs no creative generation. It copies model-owned visual
    decisions, derives legacy convenience fields deterministically, binds current
    approved reference artifacts and assigns continuity groups.
    """
    validator = V4ContractValidator()
    try:
        source_plan = validator.validate(
            "scene_plan_v1.schema.json",
            deepcopy(scene_plan),
            error_code="VISUAL_PLAN_SOURCE_INVALID",
        )
        style = validator.validate(
            "project_style_bible_v1.schema.json",
            deepcopy(text_approved_style_bible),
            error_code="VISUAL_PLAN_STYLE_BIBLE_INVALID",
        )
    except Exception as exc:  # noqa: BLE001
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SOURCE_INVALID",
            "Combined Visual Plan source artifacts are invalid.",
        ) from exc
    if source_plan.get("schema_version") != "2.0" or source_plan.get("status") not in {"draft_ready", "approved"}:
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SOURCE_INVALID",
            "Combined Visual Plan assembly requires a current Scene Plan 2.0 draft or approval.",
        )
    if style.get("status") != "text_approved":
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_STYLE_BIBLE_NOT_TEXT_APPROVED",
            "Combined Visual Plan assembly requires a text-approved Style Bible.",
        )
    if style.get("style_bible_revision_id") != source_plan.get("source_style_bible_revision_id"):
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SOURCE_IDENTITY_MISMATCH",
            "Style Bible revision does not match Scene Plan lineage.",
        )

    production_notes, negative_requirements = _combined_style_notes(style)
    bindings = _combined_binding_index(
        canonical_reference_bindings=canonical_reference_bindings,
        approval_set_artifact_id=source_visual_reference_approval_set_artifact_id,
    )
    source_scenes = source_plan["scenes"]
    fragment_scenes = [scene for section in ordered_sections for scene in section["scenes"]]
    if len(source_scenes) != len(fragment_scenes):
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SCENE_ORDER_INVALID",
            "Combined section fragments must cover every Scene Plan scene exactly once.",
        )

    global_group = 0
    current_group: str | None = None
    has_previous_beat = False
    scenes: list[dict[str, Any]] = []
    for source_scene, fragment_scene in zip(source_scenes, fragment_scenes, strict=True):
        if source_scene["local_scene_order"] != fragment_scene["local_scene_order"]:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_SCENE_ORDER_INVALID",
                "Combined scene order does not match the Scene Plan.",
            )
        source_beats = source_scene["beats"]
        fragment_beats = fragment_scene["beats"]
        if len(source_beats) != len(fragment_beats):
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_BEAT_ORDER_INVALID",
                "Combined beat coverage does not match the Scene Plan.",
            )
        beat_plans: list[dict[str, Any]] = []
        for index, (source_beat, fragment_beat) in enumerate(zip(source_beats, fragment_beats, strict=True)):
            visual = deepcopy(fragment_beat["visual"])
            relation = visual["continuity_with_previous"]
            if not has_previous_beat and relation != "new_setup":
                raise VisualPlanProjectionError(
                    "VISUAL_PLAN_CONTINUITY_INVALID",
                    "The first Beat of the whole Visual Plan must start a new setup.",
                )
            if current_group is None or relation == "new_setup":
                global_group += 1
                current_group = f"continuity_{global_group:03d}"
            assert current_group is not None

            selected_keys: list[tuple[str, str]] = []
            selected_keys.extend(("character", identity) for identity in visual["selected_character_refs"])
            selected_keys.extend(("location", identity) for identity in visual["selected_location_refs"])
            selected_keys.extend(("prop", identity) for identity in visual["selected_prop_refs"])
            selected_keys.extend(
                ("recurring_visual_subject", identity)
                for identity in visual["selected_recurring_visual_subject_refs"]
            )
            selected_bindings = [deepcopy(bindings[key]) for key in selected_keys if key in bindings]
            selected_native_ids = [item["reference_artifact_id"] for item in selected_bindings]
            beat_plans.append(
                {
                    "scene_id": source_scene["scene_id"],
                    "beat_id": source_beat["beat_id"],
                    "visual_context_ref": source_beat["visual_context_ref"],
                    "beat_order": source_beat["beat_order"],
                    "local_beat_order": source_beat["local_beat_order"],
                    "script_quote": source_beat["script_quote"],
                    "resolved_script_span": deepcopy(source_beat["resolved_script_span"]),
                    "computed_duration_seconds": source_beat["estimated_duration_seconds"],
                    "visual_priority": source_beat["visual_priority"],
                    "is_narrative_marker": source_beat["is_narrative_marker"],
                    "narrative_marker_refs": deepcopy(source_beat["narrative_marker_refs"]),
                    "narrative_marker_types": deepcopy(source_beat["narrative_marker_types"]),
                    "representation_family": visual["representation_family"],
                    "selected_character_refs": deepcopy(visual["selected_character_refs"]),
                    "selected_location_refs": deepcopy(visual["selected_location_refs"]),
                    "selected_prop_refs": deepcopy(visual["selected_prop_refs"]),
                    "selected_recurring_visual_subject_refs": deepcopy(visual["selected_recurring_visual_subject_refs"]),
                    "selected_motif_refs": deepcopy(visual["selected_motif_refs"]),
                    "selected_native_visual_reference_ids": selected_native_ids,
                    "visual_concept": visual["visual_concept"],
                    "subject_action": visual["subject_action"],
                    "environment_emphasis": visual["environment_emphasis"],
                    "framing": visual["framing"],
                    "camera_angle": visual["camera_angle"],
                    "camera_movement": visual["camera_movement"],
                    "composition": visual["composition"],
                    "lighting_direction": visual["lighting_direction"] or NO_BEAT_LIGHTING_OVERRIDE,
                    "color_emphasis": visual["color_emphasis"] or NO_BEAT_COLOR_OVERRIDE,
                    "motion_guidance": f"Camera movement: {visual['camera_movement']}.",
                    "continuity_with_previous": relation,
                    "previous_beat_image_reference_required": visual["previous_beat_image_reference_required"],
                    "continuity_notes": f"Continuity relation: {relation}.",
                    "production_notes": production_notes,
                    "negative_requirements": deepcopy(negative_requirements),
                    "visible_text": deepcopy(visual["visible_text"]),
                    "continuity_group_id": current_group,
                    "selected_reference_bindings": selected_bindings,
                }
            )
            has_previous_beat = True
        scenes.append(
            {
                "scene_id": source_scene["scene_id"],
                "scene_order": source_scene["scene_order"],
                "section_id": source_scene["section_id"],
                "scene_continuity_notes": SCENE_CONTINUITY_PROJECTION_NOTE,
                "beat_plans": beat_plans,
            }
        )

    payload = {
        "artifact_type": "visual_plan",
        "schema_version": "3.0",
        "project_id": source_plan["project_id"],
        "visual_plan_revision_id": visual_plan_revision_id,
        "parent_visual_plan_revision_id": parent_visual_plan_revision_id,
        "revision_operation": revision_operation,
        "source_script_revision_id": source_plan["source_script_revision_id"],
        "source_script_analysis_revision_id": source_plan["source_script_analysis_revision_id"],
        "source_style_bible_revision_id": source_plan["source_style_bible_revision_id"],
        "source_scene_plan_revision_id": source_plan["scene_plan_revision_id"],
        "source_visual_reference_approval_set_artifact_id": source_visual_reference_approval_set_artifact_id,
        "source_run_artifact_id": source_run_artifact_id,
        "status": status,
        "scene_count": len(scenes),
        "beat_count": sum(len(scene["beat_plans"]) for scene in scenes),
        "scenes": scenes,
        "backend_qc": empty_visual_plan_report(scenes=scenes),
    }
    return VisualPlanModel.model_validate(payload).model_dump(mode="json")


def approve_visual_plan_for_scene_plan(
    current_plan: dict[str, Any],
    *,
    new_revision_id: str,
    approved_scene_plan_revision_id: str,
) -> dict[str, Any]:
    """Approve and rebind a paired Visual Plan, then rerun canonical validation."""
    current = VisualPlanModel.model_validate(deepcopy(current_plan))
    if current.status != "draft_ready":
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_NOT_DRAFT_READY",
            "Only a draft-ready paired Visual Plan can be approved.",
        )
    payload = current.model_dump(mode="json")
    payload["visual_plan_revision_id"] = new_revision_id
    payload["parent_visual_plan_revision_id"] = current.visual_plan_revision_id
    payload["revision_operation"] = "approval"
    payload["source_scene_plan_revision_id"] = approved_scene_plan_revision_id
    payload["status"] = "approved"
    return VisualPlanModel.model_validate(payload).model_dump(mode="json")


def combined_fragments_from_paired_plans(
    scene_plan: dict[str, Any],
    visual_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rebuild reusable combined section fragments from an exact paired draft."""
    scene_payload = V4ContractValidator().validate(
        "scene_plan_v1.schema.json",
        deepcopy(scene_plan),
        error_code="SCENE_PLAN_INVALID",
    )
    visual_payload = VisualPlanModel.model_validate(deepcopy(visual_plan)).model_dump(mode="json")
    if visual_payload["source_scene_plan_revision_id"] != scene_payload["scene_plan_revision_id"]:
        raise VisualPlanProjectionError(
            "VISUAL_PLAN_SOURCE_IDENTITY_MISMATCH",
            "Visual Plan is not paired with the supplied Scene Plan.",
        )
    visual_scenes = {scene["scene_id"]: scene for scene in visual_payload["scenes"]}
    visual_beats = {
        beat["beat_id"]: beat
        for scene in visual_payload["scenes"]
        for beat in scene["beat_plans"]
    }
    scenes_by_section: dict[str, list[dict[str, Any]]] = {}
    for scene in scene_payload["scenes"]:
        visual_scene = visual_scenes.get(scene["scene_id"])
        if visual_scene is None:
            raise VisualPlanProjectionError(
                "VISUAL_PLAN_SCENE_ORDER_INVALID",
                "Paired Visual Plan is missing a Scene Plan scene.",
            )
        local_beats: list[dict[str, Any]] = []
        for beat in scene["beats"]:
            visual_beat = visual_beats.get(beat["beat_id"])
            if visual_beat is None or visual_beat["scene_id"] != scene["scene_id"]:
                raise VisualPlanProjectionError(
                    "VISUAL_PLAN_BEAT_ORDER_INVALID",
                    "Paired Visual Plan is missing a Scene Plan beat.",
                )
            local_beats.append(
                {
                    "local_beat_order": beat["local_beat_order"],
                    "script_quote": beat["script_quote"],
                    "resolved_script_span": deepcopy(beat["resolved_script_span"]),
                    "visual_context_ref": beat["visual_context_ref"],
                    "word_count": beat["word_count"],
                    "estimated_duration_seconds": beat["estimated_duration_seconds"],
                    "narrative_marker_refs": deepcopy(beat["narrative_marker_refs"]),
                    "narrative_marker_types": deepcopy(beat["narrative_marker_types"]),
                    "is_narrative_marker": beat["is_narrative_marker"],
                    "turning_point": beat["turning_point"],
                    "reveal": beat["reveal"],
                    "contrast": beat["contrast"],
                    "visual_priority": beat["visual_priority"],
                    "eligible_character_refs": deepcopy(beat["eligible_character_refs"]),
                    "eligible_location_refs": deepcopy(beat["eligible_location_refs"]),
                    "eligible_prop_refs": deepcopy(beat["eligible_prop_refs"]),
                    "eligible_recurring_visual_subject_refs": deepcopy(
                        beat["eligible_recurring_visual_subject_refs"]
                    ),
                    "eligible_motif_refs": deepcopy(beat["eligible_motif_refs"]),
                    "pacing_density": beat["pacing_density"],
                    "visual": {
                        "representation_family": visual_beat["representation_family"],
                        "selected_character_refs": deepcopy(visual_beat["selected_character_refs"]),
                        "selected_location_refs": deepcopy(visual_beat["selected_location_refs"]),
                        "selected_prop_refs": deepcopy(visual_beat["selected_prop_refs"]),
                        "selected_recurring_visual_subject_refs": deepcopy(
                            visual_beat["selected_recurring_visual_subject_refs"]
                        ),
                        "selected_motif_refs": deepcopy(visual_beat["selected_motif_refs"]),
                        "visual_concept": visual_beat["visual_concept"],
                        "subject_action": visual_beat["subject_action"],
                        "environment_emphasis": visual_beat["environment_emphasis"],
                        "visible_text": deepcopy(visual_beat["visible_text"]),
                        "framing": visual_beat["framing"],
                        "camera_angle": visual_beat["camera_angle"],
                        "camera_movement": visual_beat["camera_movement"],
                        "composition": visual_beat["composition"],
                        "lighting_direction": (
                            None
                            if visual_beat["lighting_direction"] == NO_BEAT_LIGHTING_OVERRIDE
                            else visual_beat["lighting_direction"]
                        ),
                        "color_emphasis": (
                            None
                            if visual_beat["color_emphasis"] == NO_BEAT_COLOR_OVERRIDE
                            else visual_beat["color_emphasis"]
                        ),
                        "continuity_with_previous": visual_beat["continuity_with_previous"],
                        "previous_beat_image_reference_required": visual_beat["previous_beat_image_reference_required"],
                    },
                }
            )
        scenes_by_section.setdefault(scene["section_id"], []).append(
            {
                "local_scene_order": scene["local_scene_order"],
                "resolved_script_span": deepcopy(scene["resolved_script_span"]),
                "start_quote": scene["start_quote"],
                "end_quote": scene["end_quote"],
                "beats": local_beats,
            }
        )
    return [
        {
            "section_id": section["section_id"],
            "section_order": section["section_order"],
            "section_title": section["section_title"],
            "section_role": section["section_role"],
            "resolved_script_span": deepcopy(section["resolved_script_span"]),
            "pacing_density": section["pacing_density"],
            "scenes": scenes_by_section.get(section["section_id"], []),
            "backend_qc": deepcopy(scene_payload["backend_qc"]),
        }
        for section in scene_payload["sections"]
    ]
