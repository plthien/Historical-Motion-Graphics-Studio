from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


ACTIVE_RENDER_STATUSES = (
    "queued",
    "running",
    "rendering",
    "probing",
    "validating",
    "finalizing",
)


@dataclass(frozen=True, slots=True)
class DurableRenderOperationRow:
    render_operation_id: str
    status: str
    next_reconcile_at: str | None
    reconcile_owner: str | None
    reconcile_token: str | None
    reconcile_lease_expires_at: str | None
    state_revision: int


class RenderDurableWorkingRepository:
    """Durable scheduling and fenced reconcile ownership for Render.

    Production Render work is runnable only when an explicit persisted
    ``next_reconcile_at`` makes the operation due. Submit, retry, and recovery
    own that scheduling write; the coordinator never infers runnable work from
    a missing timestamp.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]:
        rows = self.session.execute(
            text(
                """
                SELECT render_operation_id
                FROM render_operations
                WHERE status IN ('queued', 'running', 'rendering', 'probing', 'validating', 'finalizing')
                  AND next_reconcile_at IS NOT NULL
                  AND next_reconcile_at <= :now_iso
                ORDER BY COALESCE(next_reconcile_at, created_at), created_at, render_operation_id
                LIMIT :limit
                """
            ),
            {"now_iso": now_iso, "limit": int(limit)},
        ).all()
        return [str(row[0]) for row in rows]

    def get_operation(self, operation_id: str) -> DurableRenderOperationRow | None:
        row = self.session.execute(
            text(
                """
                SELECT render_operation_id, status, next_reconcile_at,
                       reconcile_owner, reconcile_token, reconcile_lease_expires_at,
                       state_revision
                FROM render_operations
                WHERE render_operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        return DurableRenderOperationRow(
            render_operation_id=str(row["render_operation_id"]),
            status=str(row["status"]),
            next_reconcile_at=(str(row["next_reconcile_at"]) if row["next_reconcile_at"] is not None else None),
            reconcile_owner=(str(row["reconcile_owner"]) if row["reconcile_owner"] is not None else None),
            reconcile_token=(str(row["reconcile_token"]) if row["reconcile_token"] is not None else None),
            reconcile_lease_expires_at=(
                str(row["reconcile_lease_expires_at"])
                if row["reconcile_lease_expires_at"] is not None
                else None
            ),
            state_revision=int(row["state_revision"]),
        )

    def try_acquire_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE render_operations
                SET reconcile_owner = :owner,
                    reconcile_token = :token,
                    reconcile_lease_expires_at = :expires_at,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE render_operation_id = :operation_id
                  AND status IN ('queued', 'running', 'rendering', 'probing', 'validating', 'finalizing')
                  AND (
                        reconcile_token IS NULL
                        OR reconcile_lease_expires_at IS NULL
                        OR reconcile_lease_expires_at <= :now_iso
                      )
                """
            ),
            {
                "operation_id": operation_id,
                "owner": owner,
                "token": token,
                "now_iso": now_iso,
                "expires_at": expires_at,
            },
        )
        return int(result.rowcount or 0) == 1

    def renew_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE render_operations
                SET reconcile_lease_expires_at = :expires_at,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE render_operation_id = :operation_id
                  AND reconcile_owner = :owner
                  AND reconcile_token = :token
                  AND reconcile_lease_expires_at IS NOT NULL
                  AND reconcile_lease_expires_at > :now_iso
                """
            ),
            {
                "operation_id": operation_id,
                "owner": owner,
                "token": token,
                "now_iso": now_iso,
                "expires_at": expires_at,
            },
        )
        return int(result.rowcount or 0) == 1

    def release_reconcile_lease(
        self,
        *,
        operation_id: str,
        owner: str,
        token: str,
        now_iso: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE render_operations
                SET reconcile_owner = NULL,
                    reconcile_token = NULL,
                    reconcile_lease_expires_at = NULL,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE render_operation_id = :operation_id
                  AND reconcile_owner = :owner
                  AND reconcile_token = :token
                """
            ),
            {"operation_id": operation_id, "owner": owner, "token": token, "now_iso": now_iso},
        )
        return int(result.rowcount or 0) == 1

    def fence_reconcile_write(self, *, operation_id: str, token: str, now_iso: str) -> bool:
        value = self.session.execute(
            text(
                """
                SELECT 1
                FROM render_operations
                WHERE render_operation_id = :operation_id
                  AND status IN ('queued', 'running', 'rendering', 'probing', 'validating', 'finalizing')
                  AND reconcile_token = :token
                  AND reconcile_lease_expires_at IS NOT NULL
                  AND reconcile_lease_expires_at > :now_iso
                """
            ),
            {"operation_id": operation_id, "token": token, "now_iso": now_iso},
        ).scalar_one_or_none()
        return value is not None

    def schedule_operation(self, *, operation_id: str, now_iso: str) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE render_operations
                SET next_reconcile_at = :now_iso,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE render_operation_id = :operation_id
                  AND status IN ('queued', 'running', 'rendering', 'probing', 'validating', 'finalizing')
                """
            ),
            {"operation_id": operation_id, "now_iso": now_iso},
        )
        return int(result.rowcount or 0) == 1
