from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from app.api.errors import AppError
from app.api.scene_asset_manifest_v2_schemas import SceneAssetPublishedManifestV2
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Project
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.v4.script_word_budget import ScriptTimingError, validate_persisted_script_integrity
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.prompt_composer_loader import PromptBundleArtifactLoaderV4
from app.modules.v4.scene_breakdown_loader import SceneBreakdownArtifactLoaderV4
from app.modules.v4.visual_plan_loader import VisualPlanArtifactLoaderV4
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService


class ProductionArtifactLoaderV4:
    """Strict native V4 consumer boundary for production stages.

    Scene Assets V2 has one additional publication rule: mutable BeatState never
    crosses this consumer boundary. Downstream stages consume only the immutable
    approval-only Scene Asset Manifest 4.0 snapshot.
    """

    def __init__(
        self,
        artifacts: ArtifactService,
        validator: V4ContractValidator | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.repository = ArtifactRepository(artifacts.session)
        self.validator = validator or V4ContractValidator()
        self.script_validator = PlatformContractValidator()
        self.scene_plans = SceneBreakdownArtifactLoaderV4(
            artifacts, self.repository, self.validator
        )
        self.visual_plans = VisualPlanArtifactLoaderV4(artifacts)
        self.prompt_bundles = PromptBundleArtifactLoaderV4(artifacts)

    def _read_json(
        self,
        project: Project,
        artifact_id: str,
        *,
        logical_id: str,
        schema_name: str,
        error_prefix: str,
        require_current: bool = False,
        expected_types: tuple[ArtifactType, ...] = (ArtifactType.JSON, ArtifactType.PLAN_JSON),
        expected_role: ArtifactRole | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        row = self.artifacts.get_required(artifact_id)
        if (
            row.logical_id != logical_id
            or row.artifact_type not in expected_types
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
            or (expected_role is not None and row.artifact_role != expected_role)
        ):
            raise AppError(
                f"{error_prefix}_INVALID",
                "Stored production artifact is unavailable or has invalid ownership.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        if require_current and (
            not row.is_current or row.superseded_by_artifact_id is not None
        ):
            raise AppError(
                f"{error_prefix}_NOT_CURRENT",
                "Stored production artifact is not current.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError(
                f"{error_prefix}_CORRUPT",
                "Stored production artifact size is invalid.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError(
                f"{error_prefix}_CORRUPT",
                "Stored production artifact hash is invalid.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(
                f"{error_prefix}_CORRUPT",
                "Stored production artifact is not valid JSON.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            ) from exc
        if not isinstance(payload, dict):
            raise AppError(
                f"{error_prefix}_INVALID",
                "Stored production artifact must be a JSON object.",
                409,
                retryable=False,
            )
        value = self.validator.validate(
            schema_name, payload, error_code=f"{error_prefix}_INVALID"
        )
        return row, value

    def load_script(
        self,
        project: Project,
        artifact_id: str,
        *,
        require_current: bool = False,
        require_downstream_ready: bool = False,
    ) -> dict[str, Any]:
        row = self.artifacts.get_required(artifact_id)
        if (
            row.logical_id != f"project/{project.project_id}/script"
            or row.artifact_type != ArtifactType.JSON
            or row.artifact_role != ArtifactRole.SCRIPT
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
        ):
            raise AppError("SCRIPT_ARTIFACT_INVALID", "Stored Script is invalid.", 409, retryable=False)
        if require_current and (
            project.script_artifact_id != artifact_id
            or not row.is_current
            or row.superseded_by_artifact_id is not None
        ):
            raise AppError("SCRIPT_ARTIFACT_NOT_CURRENT", "Stored Script is not current.", 409, retryable=False)
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError("SCRIPT_ARTIFACT_CORRUPT", "Stored Script size is invalid.", 409, retryable=False)
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError("SCRIPT_ARTIFACT_CORRUPT", "Stored Script hash is invalid.", 409, retryable=False)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("SCRIPT_ARTIFACT_CORRUPT", "Stored Script is not valid JSON.", 409, retryable=False) from exc
        value = self.script_validator.validate(
            "script.schema.json", payload, error_code="SCRIPT_ARTIFACT_INVALID"
        )
        channel_dna = project.channel_dna_json
        pacing_profiles = channel_dna.get("pacing_profiles") if isinstance(channel_dna, dict) else None
        try:
            validate_persisted_script_integrity(value, pacing_profiles=pacing_profiles)
        except ScriptTimingError as exc:
            raise AppError("SCRIPT_ARTIFACT_INVALID", "Stored Script derived data is invalid.", 409, retryable=False) from exc
        return value

    def load_scene_plan(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        value = self.scene_plans.load_scene_plan(project, artifact_id, require_current=require_current)
        if value.get("schema_version") != "2.0":
            raise AppError("SCENE_PLAN_VERSION_UNSUPPORTED", "Production stages require Scene Plan 2.0.", 409, retryable=False)
        return value

    def load_approved_scene_plan(self, project: Project, artifact_id: str | None = None) -> dict[str, Any]:
        return self.scene_plans.load_approved_scene_plan_for_consumer(project, artifact_id)

    def load_visual_plan(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        value = self.visual_plans.load_visual_plan(project, artifact_id, require_current=require_current)
        if value.get("schema_version") != "3.0":
            raise AppError("VISUAL_PLAN_VERSION_UNSUPPORTED", "Production stages require Visual Plan 3.0.", 409, retryable=False)
        return value

    def load_approved_visual_plan(self, project: Project, artifact_id: str | None = None) -> dict[str, Any]:
        return self.visual_plans.load_approved_visual_plan_for_consumer(project, artifact_id)

    def load_prompt_bundle(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        value = self.prompt_bundles.load(project, artifact_id, require_current=require_current)
        if value.get("schema_version") != "3.0":
            raise AppError("PROMPT_BUNDLE_VERSION_UNSUPPORTED", "Production stages require Prompt Bundle 3.0.", 409, retryable=False)
        return value

    def load_approved_prompt_bundle(self, project: Project, artifact_id: str | None = None) -> dict[str, Any]:
        selected = artifact_id or project.prompt_bundle_artifact_id
        if selected is None:
            raise AppError("PROMPT_BUNDLE_NOT_FOUND", "An approved current Prompt Bundle is required.", 409, retryable=False)
        value = self.load_prompt_bundle(project, selected, require_current=True)
        if value.get("status") != "approved":
            raise AppError("PROMPT_BUNDLE_NOT_APPROVED", "Production stages require an approved Prompt Bundle.", 409, retryable=False)
        return value

    def _read_scene_asset_manifest_payload(
        self,
        project: Project,
        artifact_id: str,
        *,
        require_current: bool = False,
    ) -> dict[str, Any]:
        row = self.artifacts.get_required(artifact_id)
        if (
            row.logical_id != f"project/{project.project_id}/scene_asset_manifest"
            or row.artifact_type not in (ArtifactType.JSON, ArtifactType.PLAN_JSON)
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
        ):
            raise AppError(
                "SCENE_ASSET_MANIFEST_INVALID",
                "Stored Scene Asset Manifest is unavailable or has invalid ownership.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        if require_current and (not row.is_current or row.superseded_by_artifact_id is not None):
            raise AppError(
                "SCENE_ASSET_MANIFEST_NOT_CURRENT",
                "Stored Scene Asset Manifest is not current.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError("SCENE_ASSET_MANIFEST_CORRUPT", "Stored Scene Asset Manifest size is invalid.", 409, retryable=False)
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError("SCENE_ASSET_MANIFEST_CORRUPT", "Stored Scene Asset Manifest hash is invalid.", 409, retryable=False)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("SCENE_ASSET_MANIFEST_CORRUPT", "Stored Scene Asset Manifest is not valid JSON.", 409, retryable=False) from exc
        if not isinstance(payload, dict):
            raise AppError("SCENE_ASSET_MANIFEST_INVALID", "Stored Scene Asset Manifest must be a JSON object.", 409, retryable=False)
        # No compatibility/backfill path is retained after V2 publication cutover.
        # Existing legacy 3.0 manifests must be re-approved from V2 working state.
        if payload.get("schema_version") != "4.0":
            raise AppError(
                "SCENE_ASSET_MANIFEST_VERSION_UNSUPPORTED",
                "Production consumers require approval-only Scene Asset Manifest 4.0.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id, "schema_version": payload.get("schema_version")},
            )
        try:
            value = SceneAssetPublishedManifestV2.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise AppError(
                "SCENE_ASSET_MANIFEST_INVALID",
                "Stored Scene Asset Manifest 4.0 failed contract validation.",
                409,
                retryable=False,
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        if value.get("project_id") != project.project_id or value.get("scene_asset_manifest_revision_id") != artifact_id:
            raise AppError("SCENE_ASSET_MANIFEST_INVALID", "Scene Asset Manifest identity is invalid.", 409, retryable=False)
        if require_current and project.scene_asset_manifest_artifact_id != artifact_id:
            raise AppError("SCENE_ASSET_MANIFEST_NOT_CURRENT", "Scene Asset Manifest is not current.", 409, retryable=False)
        return value

    def load_scene_asset_manifest_for_asset_lookup(
        self,
        project: Project,
        artifact_id: str,
        *,
        require_current: bool = False,
    ) -> dict[str, Any]:
        return self._read_scene_asset_manifest_payload(project, artifact_id, require_current=require_current)

    def load_scene_asset_manifest_snapshot(
        self,
        project: Project,
        artifact_id: str,
        *,
        require_current: bool = False,
    ) -> dict[str, Any]:
        value = self._read_scene_asset_manifest_payload(project, artifact_id, require_current=require_current)
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
            if value.get(field) != expected_value:
                raise AppError(
                    "SCENE_ASSET_UPSTREAM_STALE",
                    "Scene Asset Manifest snapshot lineage no longer matches the Project.",
                    409,
                    retryable=False,
                    details={"field": field},
                )
        return value

    def load_scene_asset_manifest(
        self,
        project: Project,
        artifact_id: str,
        *,
        require_current: bool = False,
        require_approved: bool = False,
        validate_output_integrity: bool = True,
    ) -> dict[str, Any]:
        value = self._read_scene_asset_manifest_payload(project, artifact_id, require_current=require_current)
        from app.modules.v4.scene_asset_published_manifest_v2 import SceneAssetPublishedManifestV2Validator

        scene_plan = self.load_approved_scene_plan(project)
        visual_plan = self.load_approved_visual_plan(project)
        prompt_bundle = self.load_approved_prompt_bundle(project)
        return SceneAssetPublishedManifestV2Validator(self.artifacts.session).validate(
            project=project,
            manifest=value,
            scene_plan=scene_plan,
            visual_plan=visual_plan,
            prompt_bundle=prompt_bundle,
            require_approved=require_approved,
            validate_output_integrity=validate_output_integrity,
        )

    def load_approved_narration_audio(self, project: Project):
        artifact_id = project.narration_audio_artifact_id
        if artifact_id is None:
            raise AppError("SCENE_BREAKDOWN_VOICE_NOT_APPROVED", "Approved Narration Audio and NarrationWordTimingMap are required.", 409, retryable=False)
        row = self.artifacts.get_required(str(artifact_id))
        if (
            row.logical_id != f"project/{project.project_id}/voice/narration-audio"
            or row.artifact_type != ArtifactType.AUDIO
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
            or not row.is_current
            or row.superseded_by_artifact_id is not None
        ):
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "Approved Narration Audio is not the exact current Voice artifact.", 409, retryable=False)
        self.artifacts.resolve_file_path(row)
        return row

    def load_narration_word_timing_map(
        self, project: Project, artifact_id: str, *, require_current: bool = False
    ) -> dict[str, Any]:
        row = self.artifacts.get_required(artifact_id)
        if (
            row.logical_id != f"project/{project.project_id}/voice/narration-word-timing-map"
            or row.artifact_type != ArtifactType.JSON
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
        ):
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "NarrationWordTimingMap is unavailable or has invalid ownership.", 409, retryable=False)
        if require_current and (project.narration_word_timing_map_artifact_id != artifact_id or not row.is_current or row.superseded_by_artifact_id is not None):
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "NarrationWordTimingMap is not the exact approved current Voice artifact.", 409, retryable=False)
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "NarrationWordTimingMap size is invalid.", 409, retryable=False)
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "NarrationWordTimingMap hash is invalid.", 409, retryable=False)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "NarrationWordTimingMap is not valid JSON.", 409, retryable=False) from exc
        if not isinstance(value, dict):
            raise AppError("SCENE_BREAKDOWN_VOICE_STALE", "NarrationWordTimingMap must be a JSON object.", 409, retryable=False)
        return value

    def load_beat_timing_map(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        row, value = self._read_json(
            project, artifact_id,
            logical_id=f"project/{project.project_id}/beat_timing_map",
            schema_name="beat_timing_map_v1.schema.json",
            error_prefix="BEAT_TIMING_MAP",
            require_current=require_current,
        )
        if value.get("project_id") != project.project_id:
            raise AppError("BEAT_TIMING_MAP_INVALID", "BeatTimingMap project identity is invalid.", 409, retryable=False)
        if require_current and project.beat_timing_map_artifact_id != artifact_id:
            raise AppError("BEAT_TIMING_LINEAGE_STALE", "BeatTimingMap is not current.", 409, retryable=False)
        if require_current and (
            value.get("source_script_revision_id") != project.script_artifact_id
            or value.get("source_scene_plan_revision_id") != project.scene_plan_artifact_id
            or value.get("source_narration_audio_artifact_id") != project.narration_audio_artifact_id
            or value.get("source_narration_word_timing_map_artifact_id") != project.narration_word_timing_map_artifact_id
        ):
            raise AppError("BEAT_TIMING_LINEAGE_STALE", "BeatTimingMap lineage does not match current Script, Scene, and Voice authorities.", 409, retryable=False)
        return value

    def load_narration_timing_map(
        self, project: Project, artifact_id: str, *, require_current: bool = False, require_approved: bool = False
    ) -> dict[str, Any]:
        _ = (project, artifact_id, require_current, require_approved)
        raise AppError(
            "VOICE_ALIGNMENT_NOT_READY",
            "Narration timing is produced by Task 3 Forced Alignment and is not available in Task 2.",
            409,
            retryable=False,
        )

    def load_assembly_plan(
        self, project: Project, artifact_id: str, *, require_current: bool = False, require_approved: bool = False
    ) -> dict[str, Any]:
        row, value = self._read_json(
            project,
            artifact_id,
            logical_id=f"project/{project.project_id}/assembly_plan",
            schema_name="assembly_plan_v1.schema.json",
            error_prefix="ASSEMBLY_PLAN",
            require_current=require_current,
            expected_types=(ArtifactType.JSON, ArtifactType.PLAN_JSON),
        )
        if value.get("project_id") != project.project_id or value.get("assembly_plan_revision_id") != artifact_id:
            raise AppError("ASSEMBLY_PLAN_INVALID", "Assembly Plan identity is invalid.", 409, retryable=False)
        if require_current and project.assembly_plan_artifact_id != artifact_id:
            raise AppError("ASSEMBLY_PLAN_NOT_CURRENT", "Assembly Plan is not current.", 409, retryable=False)
        if require_approved and value.get("status") != "approved":
            raise AppError("ASSEMBLY_PLAN_NOT_APPROVED", "Approved Assembly Plan is required.", 409, retryable=False)
        expected = {
            "source_script_artifact_id": project.script_artifact_id,
            "source_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "source_scene_asset_manifest_artifact_id": project.scene_asset_manifest_artifact_id,
            "source_narration_audio_artifact_id": project.narration_audio_artifact_id,
            "source_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            "source_beat_timing_map_artifact_id": project.beat_timing_map_artifact_id,
            "source_subtitle_srt_artifact_id": project.subtitle_srt_artifact_id,
            "source_subtitle_vtt_artifact_id": project.subtitle_vtt_artifact_id,
        }
        if any(value.get(field) != expected_value for field, expected_value in expected.items()):
            raise AppError("ASSEMBLY_UPSTREAM_STALE", "Assembly Plan lineage is stale.", 409, retryable=False)
        return value
