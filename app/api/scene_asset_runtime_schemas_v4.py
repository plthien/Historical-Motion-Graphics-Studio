from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.visual_plan_schemas_v4 import BeatId, ProjectId

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
OperationId = Annotated[str, Field(pattern=r"^scene_asset_operation_[0-9a-f]{12}$")]
OperationBeatId = Annotated[str, Field(pattern=r"^scene_asset_operation_beat_[0-9a-f]{12}$")]
OperationTaskId = Annotated[str, Field(pattern=r"^scene_asset_operation_task_[0-9a-f]{12}$")]
OperationType = Literal[
    "generate_all", "generate_section", "generate_beat", "generate_missing",
    "retry_failed", "retry_beat", "regenerate_beat", "regenerate_section",
]
OperationStatus = Literal[
    "queued", "running", "finalizing", "completed", "partial", "failed", "cancelled", "superseded",
]
OperationBeatStatus = Literal["queued", "running", "candidate_ready", "failed", "cancelled"]
OperationTaskStatus = Literal[
    "queued", "submitting", "submit_outcome_unknown", "waiting_provider", "downloading",
    "materializing", "completed", "failed", "cancelled",
]
ExecutionHealth = Literal["healthy", "stalled", "terminal"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneAssetV2OperationSubmitRequest(StrictModel):
    operation_type: Literal["generate_all", "generate_section", "generate_missing", "retry_failed", "regenerate_section"] = "generate_all"
    section_id: str | None = None
    candidate_count: Annotated[int, Field(ge=1, le=5)] = 1


class SceneAssetV2BeatSubmitRequest(StrictModel):
    candidate_count: Annotated[int, Field(ge=1, le=5)] = 1


class SceneAssetV2OperationSubmissionResponse(StrictModel):
    project_id: ProjectId
    operation_id: OperationId
    operation_type: OperationType
    status: Literal["queued"]
    total_beat_count: PositiveInt
    scheduled_beat_ids: list[BeatId]
    blocked_beat_ids: list[BeatId]
    next_reconcile_at: str


class SceneAssetV2TaskStatus(StrictModel):
    operation_task_id: OperationTaskId
    candidate_index: PositiveInt
    status: OperationTaskStatus
    media_task_id: str | None
    next_reconcile_at: str | None
    error_code: str | None
    error_message: str | None


class SceneAssetV2BeatOperationStatus(StrictModel):
    operation_beat_id: OperationBeatId
    beat_id: BeatId
    status: OperationBeatStatus
    health: ExecutionHealth
    stalled: bool
    stalled_reason: str | None
    last_progress_at: str | None
    candidate_count: PositiveInt
    tasks: list[SceneAssetV2TaskStatus]
    error_code: str | None
    error_message: str | None


class SceneAssetV2OperationStatusResponse(StrictModel):
    project_id: ProjectId
    operation_id: OperationId
    operation_type: OperationType
    status: OperationStatus
    health: ExecutionHealth
    stalled: bool
    stalled_reason: str | None
    last_progress_at: str
    total_beat_count: PositiveInt
    completed_count: NonNegativeInt
    failed_count: NonNegativeInt
    cancelled_count: NonNegativeInt
    next_reconcile_at: str | None
    cancel_requested_at: str | None
    completed_at: str | None
    error_code: str | None
    error_message: str | None
    beats: list[SceneAssetV2BeatOperationStatus]


class SceneAssetV2ExecutionStatusResponse(StrictModel):
    project_id: ProjectId
    active: bool
    active_operation_count: NonNegativeInt
    stalled_operation_count: NonNegativeInt
    operations: list[SceneAssetV2OperationStatusResponse]


class SceneAssetV2CancelResponse(StrictModel):
    project_id: ProjectId
    operation_id: OperationId
    status: Literal["cancelled"]
    already_cancelled: bool
    cancelled_task_count: NonNegativeInt = 0
    cancelled_beat_count: NonNegativeInt = 0
    completed_count: NonNegativeInt = 0
    failed_count: NonNegativeInt = 0
    cancelled_count: NonNegativeInt = 0
