from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.prompt_composer_schemas_v4 import PromptHash, PromptReferenceBinding
from app.api.scene_asset_schemas_v4 import SceneAssetContinuitySource, SceneAssetError, SceneAssetGenerationLineage, SceneAssetTreatment
from app.api.visual_plan_schemas_v4 import ArtifactId, BeatId, ProjectId, SceneId

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
PositiveFloat = Annotated[float, Field(gt=0)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CandidateId = Annotated[str, Field(pattern=r"^scene_asset_candidate_[0-9a-f]{12}$")]
PromptItemId = Annotated[str, Field(pattern=r"^prompt_[0-9]{3,}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneAssetPublishedBeatV2(StrictModel):
    scene_id: SceneId
    beat_id: BeatId
    beat_order: PositiveInt
    local_beat_order: PositiveInt
    continuity_group_id: str
    computed_duration_seconds: PositiveFloat
    resolution_status: Literal["approved"]
    review_status: Literal["approved"]
    selected_candidate_id: CandidateId
    selected_asset_artifact_id: ArtifactId
    candidate_asset_artifact_ids: list[ArtifactId]
    source_kind: Literal["generated", "uploaded"]
    source_prompt_bundle_artifact_id: ArtifactId
    source_prompt_item_id: PromptItemId
    source_prompt_hash: PromptHash
    media_task_ids: list[str]
    width: NonNegativeInt | None
    height: NonNegativeInt | None
    mime_type: str
    sha256: Sha256
    generation_lineage: SceneAssetGenerationLineage | None
    continuity_source: SceneAssetContinuitySource | None
    treatment: SceneAssetTreatment
    reference_bindings: list[PromptReferenceBinding]
    error: SceneAssetError | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "SceneAssetPublishedBeatV2":
        if self.selected_asset_artifact_id not in self.candidate_asset_artifact_ids:
            raise ValueError("selected asset must belong to the beat candidate set")
        if self.source_kind == "generated" and self.generation_lineage is None:
            raise ValueError("generated selected candidate requires generation lineage")
        if self.source_kind == "uploaded" and self.generation_lineage is not None:
            raise ValueError("uploaded selected candidate must not claim generation lineage")
        return self


class SceneAssetPublishedSceneV2(StrictModel):
    scene_id: SceneId
    scene_order: PositiveInt
    entries: Annotated[list[SceneAssetPublishedBeatV2], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_entries(self) -> "SceneAssetPublishedSceneV2":
        if any(entry.scene_id != self.scene_id for entry in self.entries):
            raise ValueError("scene entry identity mismatch")
        if [entry.local_beat_order for entry in self.entries] != list(range(1, len(self.entries) + 1)):
            raise ValueError("local beat order must be contiguous")
        return self


class SceneAssetPublishedManifestV2(StrictModel):
    artifact_type: Literal["scene_asset_manifest"]
    schema_version: Literal["4.0"]
    project_id: ProjectId
    scene_asset_manifest_revision_id: ArtifactId
    parent_scene_asset_manifest_revision_id: ArtifactId | None
    revision_operation: Literal["approval"]
    source_script_revision_id: ArtifactId
    source_script_analysis_revision_id: ArtifactId
    source_style_bible_revision_id: ArtifactId
    source_scene_plan_revision_id: ArtifactId
    source_visual_plan_revision_id: ArtifactId
    source_prompt_bundle_revision_id: ArtifactId
    source_visual_reference_approval_set_artifact_id: ArtifactId | None
    source_operation_id: None = None
    status: Literal["approved"]
    scene_count: NonNegativeInt
    beat_count: NonNegativeInt
    approved_asset_count: NonNegativeInt
    failed_beat_count: Literal[0]
    scenes: list[SceneAssetPublishedSceneV2]
    backend_qc: dict[str, Any]

    @model_validator(mode="after")
    def validate_manifest(self) -> "SceneAssetPublishedManifestV2":
        if self.scene_count != len(self.scenes):
            raise ValueError("scene_count must equal scenes length")
        if [scene.scene_order for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("scene order must be contiguous")
        entries = [entry for scene in self.scenes for entry in scene.entries]
        if self.beat_count != len(entries) or self.approved_asset_count != len(entries):
            raise ValueError("approved manifest counts must match entries")
        if [entry.beat_order for entry in entries] != list(range(1, len(entries) + 1)):
            raise ValueError("beat order must be contiguous")
        beat_ids = [entry.beat_id for entry in entries]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError("manifest beat IDs must be unique")
        return self
