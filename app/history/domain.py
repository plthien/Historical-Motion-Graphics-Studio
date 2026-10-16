"""P1-only typed History contracts. No old Scene/Beat DTOs."""
from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


StageKey = Literal["topic", "script_analysis", "voice", "assets", "visual_director", "motion_timeline", "final_video"]
STAGE_KEYS = ("topic", "script_analysis", "voice", "assets", "visual_director", "motion_timeline", "final_video")
STAGE_LABELS = ("Topic Ideas", "Script & Historical Analysis", "Voice & Alignment", "Historical Assets", "Visual Director", "Motion Timeline", "Render & Delivery")
STAGE_BLOCKERS = ("NOT_IMPLEMENTED_P2", "NOT_IMPLEMENTED_P2", "NOT_IMPLEMENTED_P3", "NOT_IMPLEMENTED_P4", "NOT_IMPLEMENTED_P5", "NOT_IMPLEMENTED_P7", "NOT_IMPLEMENTED_P8")


class ChannelCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    language: str = Field(min_length=2, max_length=32)
    niche: str = Field(min_length=1, max_length=120)
    history_dna: dict[str, Any] = Field(default_factory=dict)


class ChannelResponse(ChannelCreate):
    channel_id: str
    revision: int
    created_at: str


class ProjectCreate(StrictModel):
    channel_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=120)
    history_context: dict[str, Any] = Field(default_factory=dict)


class ProjectResponse(ProjectCreate):
    project_id: str
    created_at: str


class StageResponse(StrictModel):
    key: StageKey
    label: str
    status: Literal["blocked", "approved"]
    blocked_reason: str | None
    revision: int


class ProjectStatusResponse(StrictModel):
    project_id: str
    channel_id: str
    stages: list[StageResponse]


class HealthResponse(StrictModel):
    status: Literal["ok"]
    product: Literal["historical-motion-graphics-studio"]
    schema_version: Literal[1]
