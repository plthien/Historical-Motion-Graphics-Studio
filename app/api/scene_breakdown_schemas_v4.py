from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactId = str


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")






class ApproveSceneBreakdownRequestV4(_StrictModel):
    expected_scene_plan_artifact_id: ArtifactId
    expected_visual_plan_artifact_id: ArtifactId
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"]




class ValidateSceneBreakdownSectionEditRequestV4(_StrictModel):
    expected_scene_plan_artifact_id: ArtifactId
    expected_visual_plan_artifact_id: ArtifactId
    section: dict[str, Any]


class SaveSceneBreakdownSectionEditRequestV4(_StrictModel):
    expected_scene_plan_artifact_id: ArtifactId
    expected_visual_plan_artifact_id: ArtifactId
    validation_artifact_id: ArtifactId
    section: dict[str, Any]


class SceneBreakdownSectionEditValidationResponseV4(_StrictModel):
    valid: bool
    validation_artifact_id: ArtifactId | None
    errors: list[dict[str, str]]
    warnings: list[str]






class SceneBreakdownReadResponseV4(_StrictModel):
    project_id: str
    generated: bool
    scene_plan_artifact_id: ArtifactId | None
    scene_plan: dict[str, Any] | None
    visual_plan_artifact_id: ArtifactId | None
    visual_plan: dict[str, Any] | None


class SceneBreakdownRevisionResponseV4(_StrictModel):
    scene_plan_artifact_id: ArtifactId
    scene_plan: dict[str, Any]
    visual_plan_artifact_id: ArtifactId
    visual_plan: dict[str, Any]


