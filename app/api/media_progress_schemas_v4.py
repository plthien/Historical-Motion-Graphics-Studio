from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MediaGenerationProgressV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(pattern=r"^media_batch_[0-9a-f]{12}$")
    total: int = Field(ge=0)
    queued: int = Field(ge=0)
    submitted: int = Field(ge=0)
    provider_pending: int = Field(ge=0)
    provider_running: int = Field(ge=0)
    downloading: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    canceled: int = Field(ge=0)
    download_failed: int = Field(ge=0)
    effective_poll_interval_seconds: int = Field(ge=1)
    updated_at: str
