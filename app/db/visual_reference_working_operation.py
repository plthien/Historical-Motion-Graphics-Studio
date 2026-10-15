from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONText


class VisualReferenceWorkingOperation(Base):
    __tablename__ = "visual_reference_working_operations"
    __table_args__ = (
        UniqueConstraint("operation_id", "project_id", name="ux_visual_reference_working_operations_identity_project"),
        UniqueConstraint("reconcile_token", name="ux_visual_reference_working_operations_reconcile_token"),
        CheckConstraint(
            "operation_type IN ('generate_all', 'generate_anchor', 'regenerate_anchor', 'retry_failed')",
            name="ck_visual_reference_working_operations_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'finalizing', 'completed', 'partial', 'failed', 'cancelled', 'superseded')",
            name="ck_visual_reference_working_operations_status",
        ),
        CheckConstraint("total_anchor_count >= 1", name="ck_visual_reference_working_operations_total_positive"),
        CheckConstraint(
            "completed_count >= 0 AND failed_count >= 0 AND cancelled_count >= 0",
            name="ck_visual_reference_working_operations_counts_nonnegative",
        ),
        CheckConstraint(
            "completed_count + failed_count + cancelled_count <= total_anchor_count",
            name="ck_visual_reference_working_operations_counts_bounded",
        ),
        CheckConstraint(
            "(reconcile_owner IS NULL AND reconcile_token IS NULL AND reconcile_lease_expires_at IS NULL) OR "
            "(reconcile_owner IS NOT NULL AND reconcile_token IS NOT NULL AND reconcile_lease_expires_at IS NOT NULL)",
            name="ck_visual_reference_working_operations_reconcile_lease_complete",
        ),
        Index("ix_visual_reference_working_operations_project_status", "project_id", "status"),
        Index("ix_visual_reference_working_operations_due", "status", "next_reconcile_at"),
        Index("ix_visual_reference_working_operations_lease_expiry", "reconcile_lease_expires_at"),
    )

    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    operation_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    source_style_bible_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    expected_visual_reference_set_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=True)
    provider_settings_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    provider_settings_snapshot_hash: Mapped[str] = mapped_column(String, nullable=False)
    total_anchor_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    cancelled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    next_reconcile_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_progress_at: Mapped[str] = mapped_column(String, nullable=False)
    reconcile_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    reconcile_token: Mapped[str | None] = mapped_column(String, nullable=True)
    reconcile_lease_expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_requested_at: Mapped[str | None] = mapped_column(String, nullable=True)
    cancelled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONText(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText(), nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
