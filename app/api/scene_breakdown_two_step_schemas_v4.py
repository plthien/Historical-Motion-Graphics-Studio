from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.api.external_authoring_schemas import ExternalPromptPackageResponse


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenePlanCandidateResponseV4(_StrictModel):
    scene_plan_artifact_id: str
    scene_plan: dict[str, Any]
    visual_prompt_package: ExternalPromptPackageResponse


class SceneBreakdownAuthoringStateResponseV4(_StrictModel):
    step: str
    scene_prompt_package: ExternalPromptPackageResponse | None = None
    scene_plan_artifact_id: str | None = None
    scene_plan: dict[str, Any] | None = None
    visual_prompt_package: ExternalPromptPackageResponse | None = None
