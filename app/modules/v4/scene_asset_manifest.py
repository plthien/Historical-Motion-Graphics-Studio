from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Callable

from app.api.errors import AppError
from app.api.scene_asset_schemas_v4 import SceneAssetManifestModel


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def flatten_visual_beats(visual_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [beat for scene in visual_plan.get("scenes", []) for beat in scene.get("beat_plans", [])]


def flatten_manifest_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for scene in manifest.get("scenes", []) for entry in scene.get("entries", [])]


def _exact_index(items: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        beat_id = str(item.get("beat_id", ""))
        if not beat_id or beat_id in result:
            raise AppError(
                "SCENE_ASSET_MANIFEST_INVALID",
                f"{label} must contain unique exact beat IDs.",
                409,
                retryable=False,
                details={"beat_id": beat_id},
            )
        result[beat_id] = item
    return result


def _treatment(beat: dict[str, Any]) -> dict[str, Any]:
    return {
        "visual_concept": beat["visual_concept"],
        "subject_action": beat["subject_action"],
        "environment_emphasis": beat["environment_emphasis"],
        "framing": beat["framing"],
        "camera_angle": beat["camera_angle"],
        "camera_movement": beat["camera_movement"],
        "composition": beat["composition"],
        "lighting_direction": beat["lighting_direction"],
        "color_emphasis": beat["color_emphasis"],
        "motion_guidance": beat["motion_guidance"],
        "continuity_with_previous": beat["continuity_with_previous"],
        "continuity_notes": beat["continuity_notes"],
        "production_notes": beat["production_notes"],
        "negative_requirements": list(beat.get("negative_requirements", [])),
        "visible_text": list(beat.get("visible_text", [])),
    }


def _lineage(
    *,
    operation_id: str,
    scene_plan_id: str,
    visual_plan_id: str,
    prompt_bundle_id: str,
    approval_set_id: str | None,
    prompt_item: dict[str, Any],
) -> dict[str, Any]:
    references = list(prompt_item.get("reference_bindings", []))
    settings = dict(prompt_item.get("generation_options", {}))
    return {
        "scene_asset_operation_id": operation_id,
        "scene_plan_artifact_id": scene_plan_id,
        "visual_plan_artifact_id": visual_plan_id,
        "prompt_bundle_artifact_id": prompt_bundle_id,
        "prompt_item_id": prompt_item["prompt_item_id"],
        "prompt_hash": prompt_item["prompt_hash"],
        "visual_reference_approval_set_artifact_id": approval_set_id,
        "reference_binding_snapshot_hash": canonical_hash(references),
        "generation_settings_snapshot_hash": canonical_hash(settings),
    }


def _base_entry(
    *,
    beat: dict[str, Any],
    prompt_item: dict[str, Any],
    operation_id: str,
    scene_plan_id: str,
    visual_plan_id: str,
    prompt_bundle_id: str,
    approval_set_id: str | None,
) -> dict[str, Any]:
    return {
        "scene_id": beat["scene_id"],
        "beat_id": beat["beat_id"],
        "beat_order": beat["beat_order"],
        "local_beat_order": beat["local_beat_order"],
        "continuity_group_id": beat["continuity_group_id"],
        "computed_duration_seconds": beat["computed_duration_seconds"],
        "resolution_status": "pending",
        "review_status": "unreviewed",
        "selected_asset_artifact_id": None,
        "current_draft_asset_artifact_id": None,
        "candidate_asset_artifact_ids": [],
        "source_prompt_item_id": prompt_item["prompt_item_id"],
        "source_prompt_hash": prompt_item["prompt_hash"],
        "media_task_ids": [],
        "width": None,
        "height": None,
        "mime_type": None,
        "sha256": None,
        "generation_lineage": _lineage(
            operation_id=operation_id,
            scene_plan_id=scene_plan_id,
            visual_plan_id=visual_plan_id,
            prompt_bundle_id=prompt_bundle_id,
            approval_set_id=approval_set_id,
            prompt_item=prompt_item,
        ),
        "continuity_source": None,
        "treatment": _treatment(beat),
        "reference_bindings": deepcopy(prompt_item.get("reference_bindings", [])),
        "error": None,
    }


def manifest_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "approved_asset_count": sum(item["resolution_status"] == "approved" for item in entries),
        "failed_beat_count": sum(item["resolution_status"] == "failed" for item in entries),
    }


def derive_manifest_status(entries: list[dict[str, Any]], *, approved: bool = False) -> str:
    if approved:
        return "approved"
    if not entries:
        return "generating"
    statuses = [item["resolution_status"] for item in entries]
    if all(status == "failed" for status in statuses):
        return "failed"
    if any(status in {"pending", "generating"} for status in statuses):
        return "partial" if any(status in {"candidate_ready", "approved", "failed"} for status in statuses) else "generating"
    if any(status == "failed" for status in statuses):
        return "partial"
    return "draft_ready"


def build_scene_asset_manifest(
    *,
    project_id: str,
    manifest_revision_id: str,
    parent_manifest_revision_id: str | None,
    revision_operation: str,
    operation_id: str,
    script_artifact_id: str,
    script_analysis_artifact_id: str,
    style_bible_artifact_id: str,
    scene_plan: dict[str, Any],
    visual_plan: dict[str, Any],
    prompt_bundle: dict[str, Any],
    approval_set_artifact_id: str | None,
    previous_manifest: dict[str, Any] | None = None,
    operation_type: str = "generate_all",
    target_beat_id: str | None = None,
    scheduled_beat_ids: set[str] | None = None,
    valid_output_resolver: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    if scene_plan.get("status") != "approved" or visual_plan.get("status") != "approved" or prompt_bundle.get("status") != "approved":
        raise AppError(
            "SCENE_ASSET_UPSTREAM_NOT_APPROVED",
            "Approved Scene Plan, Visual Plan, and Prompt Bundle are required.",
            409,
            retryable=False,
        )
    if visual_plan.get("source_scene_plan_revision_id") != scene_plan.get("scene_plan_revision_id"):
        raise AppError("SCENE_ASSET_UPSTREAM_STALE", "Visual Plan does not match the approved Scene Plan.", 409, retryable=False)
    if prompt_bundle.get("source_visual_plan_revision_id") != visual_plan.get("visual_plan_revision_id"):
        raise AppError("SCENE_ASSET_UPSTREAM_STALE", "Prompt Bundle does not match the approved Visual Plan.", 409, retryable=False)
    if prompt_bundle.get("source_visual_reference_approval_set_artifact_id") != approval_set_artifact_id:
        raise AppError("SCENE_ASSET_UPSTREAM_STALE", "Prompt Bundle reference approval-set identity is stale.", 409, retryable=False)

    visual_beats = flatten_visual_beats(visual_plan)
    prompt_items = list(prompt_bundle.get("prompt_items", []))
    prompt_by_beat = _exact_index(prompt_items, label="Prompt Bundle prompt items")
    previous_by_beat = _exact_index(
        flatten_manifest_entries(previous_manifest or {}),
        label="Previous Scene Asset Manifest entries",
    )
    visual_ids = [str(beat.get("beat_id", "")) for beat in visual_beats]
    prompt_ids = [str(item.get("beat_id", "")) for item in prompt_items]
    if visual_ids != prompt_ids:
        raise AppError(
            "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
            "Prompt Bundle must contain exactly one ordered prompt for every Visual Plan beat.",
            409,
            retryable=False,
        )

    if operation_type in {"generate_missing", "retry_failed", "retry_beat", "regenerate_section"} and previous_manifest is None:
        action_name = "Regenerate Section" if operation_type == "regenerate_section" else "Retry Failed" if operation_type == "retry_failed" else "Generate Missing"
        raise AppError("SCENE_ASSET_MANIFEST_MISSING", f"{action_name} requires a current Scene Asset Manifest.", 409, retryable=False)
    if operation_type == "regenerate_beat":
        if previous_manifest is None or not target_beat_id or target_beat_id not in previous_by_beat:
            raise AppError(
                "SCENE_ASSET_BEAT_NOT_FOUND",
                "The exact Scene Asset beat was not found for regeneration.",
                404,
                retryable=False,
                details={"beat_id": target_beat_id},
            )

    scenes: list[dict[str, Any]] = []
    all_entries: list[dict[str, Any]] = []
    for visual_scene in visual_plan["scenes"]:
        scene_entries: list[dict[str, Any]] = []
        for beat in visual_scene["beat_plans"]:
            prompt_item = prompt_by_beat.get(beat["beat_id"])
            if prompt_item is None:
                raise AppError(
                    "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                    "The exact approved Prompt Bundle item is missing.",
                    409,
                    retryable=False,
                    details={"beat_id": beat["beat_id"]},
                )
            entry = _base_entry(
                beat=beat,
                prompt_item=prompt_item,
                operation_id=operation_id,
                scene_plan_id=scene_plan["scene_plan_revision_id"],
                visual_plan_id=visual_plan["visual_plan_revision_id"],
                prompt_bundle_id=prompt_bundle["prompt_bundle_revision_id"],
                approval_set_id=approval_set_artifact_id,
            )
            previous = previous_by_beat.get(beat["beat_id"])
            scheduled = scheduled_beat_ids is None or beat["beat_id"] in scheduled_beat_ids
            if previous is not None:
                entry = deepcopy(previous)
                if scheduled:
                    entry["generation_lineage"] = _lineage(
                        operation_id=operation_id,
                        scene_plan_id=scene_plan["scene_plan_revision_id"],
                        visual_plan_id=visual_plan["visual_plan_revision_id"],
                        prompt_bundle_id=prompt_bundle["prompt_bundle_revision_id"],
                        approval_set_id=approval_set_artifact_id,
                        prompt_item=prompt_item,
                    )
                    entry["source_prompt_item_id"] = prompt_item["prompt_item_id"]
                    entry["source_prompt_hash"] = prompt_item["prompt_hash"]
                    entry["reference_bindings"] = deepcopy(prompt_item.get("reference_bindings", []))
                    entry["error"] = None
            scene_entries.append(entry)
            all_entries.append(entry)
        scenes.append(
            {
                "scene_id": visual_scene["scene_id"],
                "scene_order": visual_scene["scene_order"],
                "entries": scene_entries,
            }
        )

    payload = {
        "artifact_type": "scene_asset_manifest",
        "schema_version": "3.0",
        "project_id": project_id,
        "scene_asset_manifest_revision_id": manifest_revision_id,
        "parent_scene_asset_manifest_revision_id": parent_manifest_revision_id,
        "revision_operation": revision_operation,
        "source_script_revision_id": script_artifact_id,
        "source_script_analysis_revision_id": script_analysis_artifact_id,
        "source_style_bible_revision_id": style_bible_artifact_id,
        "source_scene_plan_revision_id": scene_plan["scene_plan_revision_id"],
        "source_visual_plan_revision_id": visual_plan["visual_plan_revision_id"],
        "source_prompt_bundle_revision_id": prompt_bundle["prompt_bundle_revision_id"],
        "source_visual_reference_approval_set_artifact_id": approval_set_artifact_id,
        "source_operation_id": operation_id,
        "status": derive_manifest_status(all_entries),
        "scene_count": len(scenes),
        "beat_count": len(all_entries),
        **manifest_counts(all_entries),
        "scenes": scenes,
        "backend_qc": {
            "coverage_status": "passed",
            "ordering_status": "passed",
            "prompt_bundle_parity_status": "passed",
            "asset_integrity_status": "passed",
            "lineage_status": "passed",
            "approval_eligibility_status": "failed",
            "issues": [],
            "warnings": [],
        },
    }
    return SceneAssetManifestModel.model_validate(payload).model_dump(mode="json")


def refresh_manifest_derived_fields(manifest: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
    result = deepcopy(manifest)
    entries = flatten_manifest_entries(result)
    result.update(manifest_counts(entries))
    eligible = bool(entries) and all(
        entry["resolution_status"] == "approved"
        and entry["review_status"] == "approved"
        and bool(entry["selected_asset_artifact_id"])
        for entry in entries
    )
    result["backend_qc"]["approval_eligibility_status"] = "passed" if eligible else "failed"
    if approved and not eligible:
        raise AppError(
            "SCENE_ASSET_MANIFEST_NOT_APPROVABLE",
            "Every beat must have one approved generated image before manifest approval.",
            409,
            retryable=False,
        )
    result["status"] = derive_manifest_status(entries, approved=approved)
    return SceneAssetManifestModel.model_validate(result).model_dump(mode="json")


def replace_manifest_entry(manifest: dict[str, Any], beat_id: str, update: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    result = deepcopy(manifest)
    matches = [entry for scene in result["scenes"] for entry in scene["entries"] if entry["beat_id"] == beat_id]
    if len(matches) != 1:
        raise AppError(
            "SCENE_ASSET_BEAT_NOT_FOUND",
            "Scene Asset beat was not found.",
            404,
            retryable=False,
            details={"beat_id": beat_id},
        )
    update(matches[0])
    return refresh_manifest_derived_fields(result)
