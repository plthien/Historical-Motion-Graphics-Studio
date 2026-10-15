from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.scene_asset_schemas_v4 import SceneAssetManifestModel
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, MediaTask, Project
from app.modules.v4.scene_asset_manifest import canonical_hash, flatten_manifest_entries, flatten_visual_beats, refresh_manifest_derived_fields
from app.modules.v4.prompt_composer_loader import PromptBundleArtifactLoaderV4
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService


def _same_continuity_identity(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    identity_fields = ("relation", "source_beat_id", "source_asset_artifact_id", "source_asset_sha256", "mime_type")
    return all(left.get(field) == right.get(field) for field in identity_fields)


class SceneAssetManifestValidator:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.artifacts = ArtifactService(session, ArtifactRepository(session))
        self.prompt_bundles = PromptBundleArtifactLoaderV4(self.artifacts)
        self._prompt_bundle_cache: dict[str, dict[str, Any]] = {}

    def validate(
        self,
        *,
        project: Project,
        manifest: dict[str, Any],
        scene_plan: dict[str, Any],
        visual_plan: dict[str, Any],
        prompt_bundle: dict[str, Any],
        require_approved: bool = False,
        validate_output_integrity: bool = True,
    ) -> dict[str, Any]:
        try:
            payload = SceneAssetManifestModel.model_validate(manifest).model_dump(mode="json")
        except Exception as exc:
            raise AppError(
                "SCENE_ASSET_MANIFEST_INVALID",
                "Scene Asset Manifest failed contract validation.",
                409,
                retryable=False,
            ) from exc
        self._validate_lineage(project, payload, scene_plan, visual_plan, prompt_bundle)
        visual_beats = flatten_visual_beats(visual_plan)
        entries = flatten_manifest_entries(payload)
        prompt_items = list(prompt_bundle.get("prompt_items", []))
        if len(entries) != len(visual_beats) or len(prompt_items) != len(visual_beats):
            self._invalid(
                "SCENE_ASSET_MANIFEST_COVERAGE_INVALID",
                "Scene Asset Manifest and Prompt Bundle must each contain exactly one item per approved Visual Plan beat.",
            )
        prompt_by_beat: dict[str, dict[str, Any]] = {}
        for prompt_item in prompt_items:
            beat_id = str(prompt_item.get("beat_id", ""))
            if not beat_id or beat_id in prompt_by_beat:
                self._invalid(
                    "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                    "Prompt Bundle beat IDs must be unique and non-empty.",
                    beat_id=beat_id,
                )
            prompt_by_beat[beat_id] = prompt_item
        for index, (beat, entry) in enumerate(zip(visual_beats, entries, strict=True)):
            for key in (
                "scene_id",
                "beat_id",
                "beat_order",
                "local_beat_order",
                "continuity_group_id",
                "computed_duration_seconds",
            ):
                if entry[key] != beat[key]:
                    self._invalid(
                        "SCENE_ASSET_MANIFEST_BEAT_MISMATCH",
                        "Manifest beat identity differs from the approved Visual Plan.",
                        beat_id=beat["beat_id"],
                        field=key,
                        index=index,
                    )
            expected_treatment = {
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
            if entry["treatment"] != expected_treatment:
                self._invalid(
                    "SCENE_ASSET_MANIFEST_TREATMENT_MISMATCH",
                    "Manifest treatment differs from the approved Visual Plan.",
                    beat_id=beat["beat_id"],
                )
            prompt_item = prompt_by_beat.get(beat["beat_id"])
            if prompt_item is None:
                self._invalid(
                    "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                    "The exact Prompt Bundle item is missing.",
                    beat_id=beat["beat_id"],
                )
            self._validate_prompt_parity(project, entry)
            if validate_output_integrity:
                self._validate_generated_entry(project, entry, prompt_bundle, prompt_item)
        if require_approved:
            if payload["status"] != "approved" or payload["backend_qc"]["approval_eligibility_status"] != "passed":
                self._invalid("SCENE_ASSET_MANIFEST_NOT_APPROVED", "Scene Asset Manifest is not approved.")
        return payload

    def validate_incremental_revision(
        self,
        *,
        project: Project,
        parent_manifest: dict[str, Any],
        manifest: dict[str, Any],
        changed_beat_ids: list[str],
    ) -> dict[str, Any]:
        """Validate a one-beat immutable revision against a trusted parent Manifest."""
        try:
            payload = SceneAssetManifestModel.model_validate(manifest).model_dump(mode="json")
            parent = SceneAssetManifestModel.model_validate(parent_manifest).model_dump(mode="json")
        except Exception as exc:
            raise AppError(
                "SCENE_ASSET_MANIFEST_INVALID",
                "Scene Asset Manifest failed contract validation.",
                409,
                retryable=False,
            ) from exc
        changed = list(dict.fromkeys(str(beat_id) for beat_id in changed_beat_ids))
        self._validate_snapshot_lineage(project, payload)
        self._validate_incremental_parent_identity(parent, payload)
        self._validate_incremental_lineage_unchanged(parent, payload)
        self._validate_incremental_structure_unchanged(parent, payload)
        self._validate_incremental_derived_fields(payload)
        parent_entries = {str(entry["beat_id"]): entry for entry in flatten_manifest_entries(parent)}
        next_entries = {str(entry["beat_id"]): entry for entry in flatten_manifest_entries(payload)}
        if set(parent_entries) != set(next_entries):
            self._invalid("SCENE_ASSET_MANIFEST_COVERAGE_INVALID", "Incremental Manifest revision changed beat coverage.")
        actual_changed = [beat_id for beat_id in parent_entries if parent_entries[beat_id] != next_entries[beat_id]]
        if not changed:
            if actual_changed or payload.get("revision_operation") != "candidate_decision":
                self._invalid(
                    "SCENE_ASSET_MANIFEST_INCREMENTAL_INVALID",
                    "Zero-change incremental Manifest revisions are only allowed for candidate decisions.",
                    revision_operation=payload.get("revision_operation"),
                    actual_changed_beat_ids=actual_changed,
                )
        elif set(actual_changed) != set(changed):
            self._invalid(
                "SCENE_ASSET_MANIFEST_INCREMENTAL_CHANGED_SET_INVALID",
                "Incremental Manifest changed beats must exactly match changed_beat_ids.",
                changed_beat_ids=changed,
                actual_changed_beat_ids=actual_changed,
            )
        immutable_entry_fields = (
            "scene_id",
            "beat_id",
            "beat_order",
            "local_beat_order",
            "continuity_group_id",
            "computed_duration_seconds",
            "source_prompt_item_id",
            "source_prompt_hash",
            "generation_lineage",
            "treatment",
            "reference_bindings",
        )
        for beat_id in changed:
            before = parent_entries.get(beat_id)
            after = next_entries.get(beat_id)
            if before is None or after is None:
                self._invalid("SCENE_ASSET_MANIFEST_INCREMENTAL_CHANGED_SET_INVALID", "Incremental changed beat was not found.", beat_id=beat_id)
            for field in immutable_entry_fields:
                if before.get(field) != after.get(field):
                    self._invalid(
                        "SCENE_ASSET_MANIFEST_INCREMENTAL_IMMUTABLE_FIELD_CHANGED",
                        "Incremental beat mutation changed immutable Scene Asset fields.",
                        beat_id=beat_id,
                        field=field,
                    )
            lineage = after.get("generation_lineage") or {}
            bundle_id = str(lineage.get("prompt_bundle_artifact_id") or payload.get("source_prompt_bundle_revision_id") or "")
            bundle = self._historical_prompt_bundle(project, bundle_id)
            prompt_item = self._prompt_item_from_bundle(bundle, beat_id)
            self._validate_prompt_parity(project, after)
            self._validate_generated_entry(project, after, bundle, prompt_item)
        return payload

    def _validate_snapshot_lineage(self, project: Project, manifest: dict[str, Any]) -> None:
        expected = {
            "project_id": project.project_id,
            "source_script_revision_id": project.script_artifact_id,
            "source_script_analysis_revision_id": project.script_analysis_artifact_id,
            "source_style_bible_revision_id": project.style_bible_artifact_id,
            "source_scene_plan_revision_id": project.scene_plan_artifact_id,
            "source_visual_plan_revision_id": project.visual_plan_artifact_id,
            "source_visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
        }
        for field, value in expected.items():
            if manifest.get(field) != value:
                self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Scene Asset Manifest lineage no longer matches the Project.", field=field)
        bundle_id = manifest.get("source_prompt_bundle_revision_id")
        if not isinstance(bundle_id, str) or not bundle_id:
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Scene Asset Manifest Prompt Bundle lineage is missing.", field="source_prompt_bundle_revision_id")
        self._historical_prompt_bundle(project, bundle_id)

    def _validate_incremental_parent_identity(self, parent: dict[str, Any], manifest: dict[str, Any]) -> None:
        if manifest.get("parent_scene_asset_manifest_revision_id") != parent.get("scene_asset_manifest_revision_id"):
            self._invalid(
                "SCENE_ASSET_MANIFEST_INCREMENTAL_PARENT_INVALID",
                "Incremental Manifest revision does not extend the supplied parent Manifest.",
                parent_scene_asset_manifest_revision_id=manifest.get("parent_scene_asset_manifest_revision_id"),
                expected_parent_scene_asset_manifest_revision_id=parent.get("scene_asset_manifest_revision_id"),
            )

    def _validate_incremental_lineage_unchanged(self, parent: dict[str, Any], manifest: dict[str, Any]) -> None:
        fields = (
            "project_id",
            "source_script_revision_id",
            "source_script_analysis_revision_id",
            "source_style_bible_revision_id",
            "source_scene_plan_revision_id",
            "source_visual_plan_revision_id",
            "source_prompt_bundle_revision_id",
            "source_visual_reference_approval_set_artifact_id",
        )
        for field in fields:
            if manifest.get(field) != parent.get(field):
                self._invalid(
                    "SCENE_ASSET_MANIFEST_INCREMENTAL_LINEAGE_CHANGED",
                    "Incremental Manifest revision changed immutable source lineage.",
                    field=field,
                )

    def _validate_incremental_structure_unchanged(self, parent: dict[str, Any], manifest: dict[str, Any]) -> None:
        parent_shape = [
            (scene.get("scene_id"), scene.get("scene_order"), [entry.get("beat_id") for entry in scene.get("entries", [])])
            for scene in parent.get("scenes", [])
        ]
        child_shape = [
            (scene.get("scene_id"), scene.get("scene_order"), [entry.get("beat_id") for entry in scene.get("entries", [])])
            for scene in manifest.get("scenes", [])
        ]
        if parent_shape != child_shape:
            self._invalid(
                "SCENE_ASSET_MANIFEST_INCREMENTAL_STRUCTURE_CHANGED",
                "Incremental Manifest revision changed scene or beat structure.",
            )

    def _validate_incremental_derived_fields(self, manifest: dict[str, Any]) -> None:
        refreshed = refresh_manifest_derived_fields(deepcopy(manifest), approved=manifest.get("status") == "approved")
        for field in ("scene_count", "beat_count", "approved_asset_count", "failed_beat_count", "status"):
            if manifest.get(field) != refreshed.get(field):
                self._invalid(
                    "SCENE_ASSET_MANIFEST_DERIVED_FIELDS_INVALID",
                    "Scene Asset Manifest derived fields are stale.",
                    field=field,
                )
        actual_qc = manifest.get("backend_qc") or {}
        expected_qc = refreshed.get("backend_qc") or {}
        if actual_qc.get("approval_eligibility_status") != expected_qc.get("approval_eligibility_status"):
            self._invalid(
                "SCENE_ASSET_MANIFEST_DERIVED_FIELDS_INVALID",
                "Scene Asset Manifest backend QC derived fields are stale.",
                field="backend_qc.approval_eligibility_status",
            )

    def _historical_prompt_bundle(self, project: Project, artifact_id: str) -> dict[str, Any]:
        cached = self._prompt_bundle_cache.get(artifact_id)
        if cached is not None:
            return cached
        try:
            bundle = self.prompt_bundles.load(project, artifact_id, require_current=False)
        except AppError as exc:
            self._invalid(
                "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                "Scene Asset historical prompt lineage cannot be loaded.",
                prompt_bundle_artifact_id=artifact_id,
            )
            raise exc  # pragma: no cover
        if (
            bundle.get("status") != "approved"
            or bundle.get("source_script_revision_id") != project.script_artifact_id
            or bundle.get("source_script_analysis_revision_id") != project.script_analysis_artifact_id
            or bundle.get("source_style_bible_revision_id") != project.style_bible_artifact_id
            or bundle.get("source_scene_plan_revision_id") != project.scene_plan_artifact_id
            or bundle.get("source_visual_plan_revision_id") != project.visual_plan_artifact_id
            or bundle.get("source_visual_reference_approval_set_artifact_id")
            != project.style_bible_visual_reference_approval_set_artifact_id
        ):
            self._invalid(
                "SCENE_ASSET_UPSTREAM_STALE",
                "Scene Asset historical Prompt Bundle lineage is stale.",
                prompt_bundle_artifact_id=artifact_id,
            )
        self._prompt_bundle_cache[artifact_id] = bundle
        return bundle

    @staticmethod
    def _prompt_item_from_bundle(bundle: dict[str, Any], beat_id: str) -> dict[str, Any]:
        matches = [item for item in bundle.get("prompt_items", []) if item.get("beat_id") == beat_id]
        if len(matches) != 1:
            raise AppError(
                "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                "Historical Prompt Bundle must contain exactly one item for the Scene Asset beat.",
                409,
                retryable=False,
                details={"beat_id": beat_id, "prompt_bundle_artifact_id": bundle.get("prompt_bundle_revision_id")},
            )
        return matches[0]

    def _validate_prompt_parity(self, project: Project, entry: dict[str, Any]) -> None:
        lineage = entry["generation_lineage"]
        prompt_bundle_id = str(lineage.get("prompt_bundle_artifact_id") or "")
        if not prompt_bundle_id:
            self._invalid(
                "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                "Manifest entry is missing frozen Prompt Bundle lineage.",
                beat_id=entry["beat_id"],
            )
        bundle = self._historical_prompt_bundle(project, prompt_bundle_id)
        prompt_item = self._prompt_item_from_bundle(bundle, str(entry["beat_id"]))
        if (
            entry["source_prompt_item_id"] != prompt_item["prompt_item_id"]
            or entry["source_prompt_hash"] != prompt_item["prompt_hash"]
            or entry["reference_bindings"] != prompt_item.get("reference_bindings", [])
        ):
            self._invalid(
                "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                "Manifest prompt identity differs from its frozen historical Prompt Bundle.",
                beat_id=entry["beat_id"],
            )
        if (
            lineage["prompt_item_id"] != prompt_item["prompt_item_id"]
            or lineage["prompt_hash"] != prompt_item["prompt_hash"]
            or lineage["reference_binding_snapshot_hash"] != canonical_hash(prompt_item.get("reference_bindings", []))
            or lineage["generation_settings_snapshot_hash"] != canonical_hash(prompt_item.get("generation_options", {}))
        ):
            self._invalid(
                "SCENE_ASSET_PROMPT_BUNDLE_MISMATCH",
                "Manifest frozen prompt lineage is invalid.",
                beat_id=entry["beat_id"],
            )

    def _validate_lineage(
        self,
        project: Project,
        manifest: dict[str, Any],
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
            "source_visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
        }
        for field, value in expected.items():
            if manifest.get(field) != value:
                self._invalid(
                    "SCENE_ASSET_UPSTREAM_STALE",
                    "Scene Asset Manifest lineage no longer matches the Project.",
                    field=field,
                )
        if scene_plan.get("scene_plan_revision_id") != manifest["source_scene_plan_revision_id"] or scene_plan.get("status") != "approved":
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Scene Plan lineage is stale.")
        if visual_plan.get("visual_plan_revision_id") != manifest["source_visual_plan_revision_id"] or visual_plan.get("status") != "approved":
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Visual Plan lineage is stale.")
        historical_bundle = self._historical_prompt_bundle(project, str(manifest["source_prompt_bundle_revision_id"]))
        if historical_bundle.get("source_visual_plan_revision_id") != visual_plan.get("visual_plan_revision_id"):
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Scene Asset Manifest Prompt Bundle lineage is stale.")
        if prompt_bundle.get("status") != "approved" or prompt_bundle.get("prompt_bundle_revision_id") != project.prompt_bundle_artifact_id:
            self._invalid("SCENE_ASSET_UPSTREAM_STALE", "Current Prompt Bundle lineage is stale.")

    def _artifact(self, project: Project, artifact_id: str, *, candidate_beat_id: str) -> Artifact:
        artifact = self.artifacts.get_required(artifact_id)
        if artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None or artifact.artifact_type != ArtifactType.IMAGE:
            self._invalid(
                "SCENE_ASSET_ARTIFACT_INVALID",
                "Scene Asset Artifact is unavailable or not image-compatible.",
                artifact_id=artifact_id,
            )
        if not artifact.logical_id.startswith(f"project/{project.project_id}/"):
            self._invalid(
                "SCENE_ASSET_ARTIFACT_PROJECT_MISMATCH",
                "Scene Asset Artifact belongs to another project.",
                artifact_id=artifact_id,
            )
        self.artifacts.resolve_file_path(artifact)
        if not artifact.sha256 or not artifact.mime_type or not artifact.mime_type.startswith("image/"):
            self._invalid(
                "SCENE_ASSET_ARTIFACT_INVALID",
                "Scene Asset Artifact integrity metadata is invalid.",
                artifact_id=artifact_id,
            )
        if f"/{candidate_beat_id}/" not in f"/{artifact.logical_id}/":
            self._invalid(
                "SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID",
                "Generated candidate does not belong to the exact beat.",
                artifact_id=artifact_id,
                beat_id=candidate_beat_id,
            )
        return artifact

    def _validate_generated_entry(
        self,
        project: Project,
        entry: dict[str, Any],
        prompt_bundle: dict[str, Any],
        prompt_item: dict[str, Any],
    ) -> None:
        candidates = set(entry["candidate_asset_artifact_ids"])
        for artifact_id in candidates:
            self._artifact(project, artifact_id, candidate_beat_id=entry["beat_id"])
        completed_result_artifact_ids: set[str] = set()
        completed_result_continuity_sources: dict[str, dict[str, Any] | None] = {}
        for task_id in entry["media_task_ids"]:
            task = self.session.get(MediaTask, task_id)
            if (
                task is None
                or task.project_id != project.project_id
                or task.purpose != "scene_asset_generation"
                or task.entity_type != "beat"
                or task.entity_id != entry["beat_id"]
            ):
                self._invalid(
                    "SCENE_ASSET_MEDIA_TASK_OWNERSHIP_INVALID",
                    "MediaTask does not belong to the exact Scene Asset beat.",
                    media_task_id=task_id,
                )
            metadata = task.metadata_json or {}
            task_bundle_id = metadata.get("prompt_bundle_artifact_id")
            if not isinstance(task_bundle_id, str) or not task_bundle_id:
                self._invalid(
                    "SCENE_ASSET_MEDIA_TASK_LINEAGE_INVALID",
                    "MediaTask is missing frozen Prompt Bundle identity.",
                    media_task_id=task_id,
                )
            task_bundle = self._historical_prompt_bundle(project, task_bundle_id)
            task_prompt_item = self._prompt_item_from_bundle(task_bundle, str(entry["beat_id"]))
            options = task_prompt_item.get("generation_options", {})
            expected_reference_images = [
                {
                    "reference_tag": binding["anchor_id"],
                    "artifact_id": binding["media_artifact_id"],
                    "sha256": binding["media_sha256"],
                    "mime_type": binding["mime_type"],
                }
                for binding in task_prompt_item.get("reference_bindings", [])
            ]
            requirement = task_prompt_item.get("continuity_reference_requirement")
            task_continuity_source = None
            metadata_has_continuity = any(
                str(key).startswith("continuity_source_") or key == "continuity_relation"
                for key in metadata
            )
            if metadata_has_continuity:
                task_continuity_source = {
                    "relation": metadata.get("continuity_relation"),
                    "source_beat_id": metadata.get("continuity_source_beat_id"),
                    "source_asset_artifact_id": metadata.get("continuity_source_asset_artifact_id"),
                    "source_asset_sha256": metadata.get("continuity_source_sha256"),
                    "mime_type": metadata.get("continuity_source_mime_type"),
                    "source_kind": metadata.get("continuity_source_kind"),
                }
                if (
                    not isinstance(requirement, dict)
                    or any(not isinstance(value, str) or not value for value in task_continuity_source.values())
                    or metadata.get("continuity_source_snapshot_hash") != canonical_hash(task_continuity_source)
                ):
                    self._invalid(
                        "SCENE_ASSET_MEDIA_TASK_LINEAGE_INVALID",
                        "MediaTask continuity source metadata is invalid.",
                        media_task_id=task_id,
                    )
                expected_reference_images.append(
                    {
                        "reference_tag": "prev_beat",
                        "artifact_id": task_continuity_source["source_asset_artifact_id"],
                        "sha256": task_continuity_source["source_asset_sha256"],
                        "mime_type": task_continuity_source["mime_type"],
                    }
                )
            if (
                metadata.get("prompt_item_id") != task_prompt_item["prompt_item_id"]
                or metadata.get("prompt_hash") != task_prompt_item["prompt_hash"]
                or metadata.get("visual_reference_approval_set_artifact_id")
                != task_bundle.get("source_visual_reference_approval_set_artifact_id")
                or metadata.get("reference_binding_snapshot_hash")
                != canonical_hash(task_prompt_item.get("reference_bindings", []))
                or metadata.get("generation_settings_snapshot_hash") != canonical_hash(options)
                or task.prompt != task_prompt_item.get("image_prompt")
                or task.negative_prompt != task_prompt_item.get("negative_prompt")
                or task.aspect_ratio != options.get("aspect_ratio")
                or list(task.reference_images_json or []) != expected_reference_images
            ):
                self._invalid(
                    "SCENE_ASSET_MEDIA_TASK_LINEAGE_INVALID",
                    "MediaTask frozen prompt lineage is invalid.",
                    media_task_id=task_id,
                )
            if task.status == "completed":
                for result in task.result_json or []:
                    artifact_id = result.get("artifact_id") if isinstance(result, dict) else None
                    if isinstance(artifact_id, str):
                        completed_result_artifact_ids.add(artifact_id)
                        completed_result_continuity_sources[artifact_id] = task_continuity_source
        uploaded_candidate_ids: set[str] = set()
        for artifact_id in candidates:
            artifact = self.artifacts.get_required(artifact_id)
            if artifact.provider_source == "user_upload":
                expected_upload_fragment = f"/{entry['beat_id']}/uploads/"
                if expected_upload_fragment not in f"/{artifact.logical_id}/":
                    self._invalid(
                        "SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID",
                        "Uploaded Scene Asset candidate does not belong to the exact beat upload namespace.",
                        beat_id=entry["beat_id"],
                        artifact_id=artifact_id,
                    )
                uploaded_candidate_ids.add(artifact_id)
        generated_candidate_ids = candidates - uploaded_candidate_ids
        if not generated_candidate_ids.issubset(completed_result_artifact_ids):
            self._invalid(
                "SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID",
                "Every generated candidate must be owned by a completed MediaTask for the exact beat.",
                beat_id=entry["beat_id"],
            )
        continuity_source = entry.get("continuity_source")
        if generated_candidate_ids:
            for artifact_id in generated_candidate_ids:
                owner_continuity_source = completed_result_continuity_sources.get(artifact_id)
                if not _same_continuity_identity(owner_continuity_source, continuity_source):
                    self._invalid(
                        "SCENE_ASSET_MEDIA_TASK_LINEAGE_INVALID",
                        "Candidate continuity provenance does not match the Manifest entry.",
                        beat_id=entry["beat_id"],
                        artifact_id=artifact_id,
                    )
        selected = entry.get("selected_asset_artifact_id")
        if selected is not None:
            artifact = self._artifact(project, selected, candidate_beat_id=entry["beat_id"])
            if selected not in candidates:
                self._invalid(
                    "SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID",
                    "Selected asset is not an exact beat candidate.",
                    artifact_id=selected,
                )
            self._validate_metadata(entry, artifact)

    def _validate_metadata(self, entry: dict[str, Any], artifact: Artifact) -> None:
        if entry.get("sha256") != artifact.sha256 or entry.get("mime_type") != artifact.mime_type:
            self._invalid(
                "SCENE_ASSET_ARTIFACT_INTEGRITY_MISMATCH",
                "Manifest asset integrity metadata differs from the Artifact row.",
                artifact_id=artifact.artifact_id,
            )
        if entry.get("width") != artifact.width or entry.get("height") != artifact.height:
            self._invalid(
                "SCENE_ASSET_ARTIFACT_INTEGRITY_MISMATCH",
                "Manifest dimensions differ from the Artifact row.",
                artifact_id=artifact.artifact_id,
            )

    @staticmethod
    def _invalid(code: str, message: str, **details: Any) -> None:
        raise AppError(code, message, 409, retryable=False, details=details)
