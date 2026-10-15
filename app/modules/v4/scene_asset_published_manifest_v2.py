from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.scene_asset_manifest_v2_schemas import SceneAssetPublishedManifestV2
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Project
from app.modules.v4.scene_asset_manifest import canonical_hash, flatten_manifest_entries, flatten_visual_beats
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService


class SceneAssetPublishedManifestV2Validator:
    """Consumer-side integrity boundary for the approval-only Manifest 4.0.

    Working-state tables are deliberately not consulted.  Once published, the
    manifest plus immutable Artifacts and approved upstream artifacts are the
    downstream authority.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.artifacts = ArtifactService(session, ArtifactRepository(session))

    def validate(
        self,
        *,
        project: Project,
        manifest: dict[str, Any],
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        prompt_bundle: dict[str, Any],
        require_approved: bool = True,
        validate_output_integrity: bool = True,
    ) -> dict[str, Any]:
        try:
            payload = SceneAssetPublishedManifestV2.model_validate(manifest).model_dump(mode="json")
        except ValidationError as exc:
            raise AppError(
                "SCENE_ASSET_MANIFEST_INVALID",
                "Published Scene Asset Manifest 4.0 failed contract validation.",
                409,
                retryable=False,
                details={"errors": exc.errors(include_url=False)},
            ) from exc

        if require_approved and payload.get("status") != "approved":
            self._invalid("SCENE_ASSET_MANIFEST_NOT_APPROVED", "Published Scene Asset Manifest must be approved.")
        self._validate_lineage(project, payload, scene_plan, visual_plan, prompt_bundle)

        visual_beats = flatten_visual_beats(visual_plan)
        entries = flatten_manifest_entries(payload)
        prompt_items = list(prompt_bundle.get("prompt_items", []))
        if len(entries) != len(visual_beats) or len(entries) != len(prompt_items):
            self._invalid(
                "SCENE_ASSET_MANIFEST_COVERAGE_INVALID",
                "Published Scene Asset Manifest must cover every approved Visual Plan and Prompt Bundle beat exactly once.",
            )
        prompt_by_beat = self._exact_index(prompt_items, "Prompt Bundle")
        entry_by_beat = self._exact_index(entries, "Published Scene Asset Manifest")

        for index, (visual_beat, entry) in enumerate(zip(visual_beats, entries, strict=True)):
            beat_id = str(visual_beat.get("beat_id") or "")
            if beat_id != entry.get("beat_id"):
                self._invalid(
                    "SCENE_ASSET_MANIFEST_BEAT_MISMATCH",
                    "Published Scene Asset beat order differs from the approved Visual Plan.",
                    beat_id=beat_id,
                    index=index,
                )
            for field in ("scene_id", "beat_order", "local_beat_order", "continuity_group_id", "computed_duration_seconds"):
                if entry.get(field) != visual_beat.get(field):
                    self._invalid(
                        "SCENE_ASSET_MANIFEST_BEAT_MISMATCH",
                        "Published Scene Asset beat identity differs from the approved Visual Plan.",
                        beat_id=beat_id,
                        field=field,
                    )
            expected_treatment = {
                "visual_concept": visual_beat["visual_concept"],
                "subject_action": visual_beat["subject_action"],
                "environment_emphasis": visual_beat["environment_emphasis"],
                "framing": visual_beat["framing"],
                "camera_angle": visual_beat["camera_angle"],
                "camera_movement": visual_beat["camera_movement"],
                "composition": visual_beat["composition"],
                "lighting_direction": visual_beat["lighting_direction"],
                "color_emphasis": visual_beat["color_emphasis"],
                "motion_guidance": visual_beat["motion_guidance"],
                "continuity_with_previous": visual_beat["continuity_with_previous"],
                "continuity_notes": visual_beat["continuity_notes"],
                "production_notes": visual_beat["production_notes"],
                "negative_requirements": list(visual_beat.get("negative_requirements", [])),
                "visible_text": list(visual_beat.get("visible_text", [])),
            }
            if entry.get("treatment") != expected_treatment:
                self._invalid(
                    "SCENE_ASSET_MANIFEST_TREATMENT_MISMATCH",
                    "Published Scene Asset treatment differs from the approved Visual Plan.",
                    beat_id=beat_id,
                )
            prompt_item = prompt_by_beat.get(beat_id)
            if prompt_item is None:
                self._invalid("SCENE_ASSET_PROMPT_BUNDLE_MISMATCH", "Published beat has no current Prompt Bundle item.", beat_id=beat_id)
            if (
                entry.get("source_prompt_bundle_artifact_id") != prompt_bundle.get("prompt_bundle_revision_id")
                or entry.get("source_prompt_item_id") != prompt_item.get("prompt_item_id")
                or entry.get("source_prompt_hash") != prompt_item.get("prompt_hash")
                or entry.get("reference_bindings") != list(prompt_item.get("reference_bindings", []))
            ):
                self._invalid(
                    "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                    "Published selected candidate does not match the current approved Prompt Bundle.",
                    beat_id=beat_id,
                )
            self._validate_generation_lineage(project, entry, prompt_item)
            self._validate_continuity(entry, entry_by_beat, prompt_item)
            if validate_output_integrity:
                self._validate_image(entry)

        qc = payload.get("backend_qc") or {}
        if qc.get("approval_eligibility_status") != "passed":
            self._invalid(
                "SCENE_ASSET_MANIFEST_NOT_APPROVED",
                "Published Scene Asset Manifest must record passed approval eligibility.",
            )
        return payload

    def _validate_lineage(
        self,
        project: Project,
        payload: dict[str, Any],
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        prompt_bundle: dict[str, Any],
    ) -> None:
        expected = {
            "project_id": project.project_id,
            "source_script_revision_id": project.script_artifact_id,
            "source_script_analysis_revision_id": project.script_analysis_artifact_id,
            "source_style_bible_revision_id": project.style_bible_artifact_id,
            "source_scene_plan_revision_id": project.scene_plan_artifact_id,
            "source_visual_plan_revision_id": project.visual_plan_artifact_id,
            "source_prompt_bundle_revision_id": project.prompt_bundle_artifact_id,
            "source_visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
        }
        for field, expected_value in expected.items():
            if payload.get(field) != expected_value:
                self._invalid(
                    "SCENE_ASSET_UPSTREAM_STALE",
                    "Published Scene Asset Manifest lineage no longer matches the Project.",
                    field=field,
                )
        if scene_plan.get("scene_plan_revision_id") != project.scene_plan_artifact_id:
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Approved Scene Plan identity is stale.")
        if visual_plan.get("visual_plan_revision_id") != project.visual_plan_artifact_id:
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Approved Visual Plan identity is stale.")
        if prompt_bundle.get("prompt_bundle_revision_id") != project.prompt_bundle_artifact_id:
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Approved Prompt Bundle identity is stale.")

    def _validate_generation_lineage(
        self,
        project: Project,
        entry: dict[str, Any],
        prompt_item: dict[str, Any],
    ) -> None:
        lineage = entry.get("generation_lineage")
        if entry.get("source_kind") == "uploaded":
            if lineage is not None or entry.get("media_task_ids"):
                self._invalid(
                    "SCENE_ASSET_MANIFEST_LINEAGE_INVALID",
                    "Uploaded Scene Asset candidate must not claim provider generation lineage.",
                    beat_id=entry.get("beat_id"),
                )
            return
        if not isinstance(lineage, dict):
            self._invalid(
                "SCENE_ASSET_MANIFEST_LINEAGE_INVALID",
                "Generated Scene Asset candidate requires generation lineage.",
                beat_id=entry.get("beat_id"),
            )
        expected = {
            "scene_plan_artifact_id": project.scene_plan_artifact_id,
            "visual_plan_artifact_id": project.visual_plan_artifact_id,
            "prompt_bundle_artifact_id": entry.get("source_prompt_bundle_artifact_id"),
            "prompt_item_id": prompt_item.get("prompt_item_id"),
            "prompt_hash": prompt_item.get("prompt_hash"),
            "visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
            "reference_binding_snapshot_hash": canonical_hash(prompt_item.get("reference_bindings", [])),
            "generation_settings_snapshot_hash": canonical_hash(prompt_item.get("generation_options", {})),
        }
        for field, expected_value in expected.items():
            if lineage.get(field) != expected_value:
                self._invalid(
                    "SCENE_ASSET_MANIFEST_LINEAGE_INVALID",
                    "Generated Scene Asset lineage differs from its frozen Prompt Bundle input.",
                    beat_id=entry.get("beat_id"),
                    field=field,
                )
        if not entry.get("media_task_ids"):
            self._invalid(
                "SCENE_ASSET_MANIFEST_LINEAGE_INVALID",
                "Generated Scene Asset candidate requires at least one media task identity.",
                beat_id=entry.get("beat_id"),
            )

    def _validate_continuity(
        self,
        entry: dict[str, Any],
        entry_by_beat: dict[str, dict[str, Any]],
        prompt_item: dict[str, Any],
    ) -> None:
        requirement = prompt_item.get("continuity_reference_requirement")
        continuity = entry.get("continuity_source")
        if requirement is None:
            if continuity is not None:
                self._invalid(
                    "SCENE_ASSET_CONTINUITY_INVALID",
                    "Published new-setup beat must not claim a continuity source.",
                    beat_id=entry.get("beat_id"),
                )
            return
        source_beat_id = str(requirement.get("source_beat_id") or "")
        source_entry = entry_by_beat.get(source_beat_id)
        if source_entry is None or not isinstance(continuity, dict):
            self._invalid(
                "SCENE_ASSET_CONTINUITY_INVALID",
                "Published continuity beat is missing its approved source beat.",
                beat_id=entry.get("beat_id"),
                source_beat_id=source_beat_id,
            )
        expected = {
            "relation": requirement.get("relation"),
            "source_beat_id": source_beat_id,
            "source_asset_artifact_id": source_entry.get("selected_asset_artifact_id"),
            "source_asset_sha256": source_entry.get("sha256"),
            "mime_type": source_entry.get("mime_type"),
            "source_kind": "approved",
        }
        for field, expected_value in expected.items():
            if continuity.get(field) != expected_value:
                self._invalid(
                    "SCENE_ASSET_CONTINUITY_INVALID",
                    "Published continuity source differs from the selected approved source candidate.",
                    beat_id=entry.get("beat_id"),
                    field=field,
                )

    def _validate_image(self, entry: dict[str, Any]) -> None:
        artifact_id = str(entry.get("selected_asset_artifact_id") or "")
        artifact = self.artifacts.get_required(artifact_id)
        if (
            artifact.status != ArtifactStatus.READY
            or artifact.deleted_at is not None
            or artifact.artifact_type != ArtifactType.IMAGE
        ):
            self._invalid(
                "SCENE_ASSET_IMAGE_INVALID",
                "Published Scene Asset image Artifact is unavailable.",
                beat_id=entry.get("beat_id"),
                artifact_id=artifact_id,
            )
        raw = self.artifacts.resolve_file_path(artifact).read_bytes()
        import hashlib

        digest = hashlib.sha256(raw).hexdigest()
        if (
            artifact.sha256 != digest
            or entry.get("sha256") != digest
            or entry.get("mime_type") != artifact.mime_type
            or entry.get("width") != artifact.width
            or entry.get("height") != artifact.height
        ):
            self._invalid(
                "SCENE_ASSET_IMAGE_INTEGRITY_INVALID",
                "Published Scene Asset image integrity does not match immutable Artifact bytes.",
                beat_id=entry.get("beat_id"),
                artifact_id=artifact_id,
            )

    @staticmethod
    def _exact_index(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            beat_id = str(item.get("beat_id") or "")
            if not beat_id or beat_id in result:
                raise AppError(
                    "SCENE_ASSET_MANIFEST_COVERAGE_INVALID",
                    f"{label} beat identities must be unique and non-empty.",
                    409,
                    retryable=False,
                    details={"beat_id": beat_id},
                )
            result[beat_id] = item
        return result

    @staticmethod
    def _invalid(code: str, message: str, **details: Any) -> None:
        raise AppError(code, message, 409, retryable=False, details=details)
