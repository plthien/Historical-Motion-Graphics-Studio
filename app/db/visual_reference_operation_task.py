from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONText


class VisualReferenceOperationTask(Base):
    __tablename__ = "visual_reference_operation_tasks"
    __table_args__ = (
        UniqueConstraint("operation_anchor_id", "candidate_index", name="ux_visual_reference_operation_tasks_anchor_candidate"),
        UniqueConstraint("operation_task_id", "operation_anchor_id", name="ux_visual_reference_operation_tasks_identity_anchor"),
        ForeignKeyConstraint(
            ["operation_anchor_id", "candidate_count"],
            ["visual_reference_operation_anchors.operation_anchor_id", "visual_reference_operation_anchors.candidate_count"],
            name="fk_visual_reference_operation_tasks_candidate_count_owner",
        ),
        UniqueConstraint("media_task_id", name="ux_visual_reference_operation_tasks_media_task_id"),
        UniqueConstraint("submit_attempt_token", name="ux_visual_reference_operation_tasks_submit_attempt_token"),
        CheckConstraint("candidate_count >= 1 AND candidate_count <= 5", name="ck_visual_reference_operation_tasks_candidate_count"),
        CheckConstraint("candidate_index >= 1 AND candidate_index <= candidate_count", name="ck_visual_reference_operation_tasks_candidate_index"),
        CheckConstraint("attempt_no >= 1", name="ck_visual_reference_operation_tasks_attempt_positive"),
        CheckConstraint(
            "status IN ('queued', 'submitting', 'submit_outcome_unknown', 'waiting_provider', 'downloading', 'materializing', 'completed', 'failed', 'cancelled')",
            name="ck_visual_reference_operation_tasks_status",
        ),
        Index("ix_visual_reference_operation_tasks_anchor_status", "operation_anchor_id", "status"),
        Index("ix_visual_reference_operation_tasks_due", "status", "next_reconcile_at"),
    )

    operation_task_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_anchor_id: Mapped[str] = mapped_column(String, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    media_task_id: Mapped[str | None] = mapped_column(ForeignKey("media_tasks.media_task_id"), nullable=True)
    submit_attempt_token: Mapped[str | None] = mapped_column(String, nullable=True)
    submit_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    next_reconcile_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
