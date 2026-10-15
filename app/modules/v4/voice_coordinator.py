from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from app.core.ids import generate_id
from app.modules.media.durable_workflow.contracts import PreparedSubmission, ReconcileAction, ReconcileLease
from app.modules.media.durable_workflow.coordinator import DurableMediaCoordinator
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver, VoiceDurableWorkflowErrors
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from app.repositories.voice_workflow_adapter import VoiceWorkflowRepositoryAdapter


class VoiceCoordinator:
    """Voice facade over the shared durable-media coordinator."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._shared = DurableMediaCoordinator(
            session_factory,
            repository_factory=lambda session: VoiceWorkflowRepositoryAdapter(
                VoiceDurableWorkingRepository(session)
            ),
            driver_factory=lambda session: VoiceDurableWorkflowDriver(
                VoiceDurableWorkingRepository(session)
            ),
            reconcile_token_factory=lambda: generate_id("voice_reconcile"),
            submit_token_factory=lambda: generate_id("voice_submit"),
            errors=VoiceDurableWorkflowErrors(),
        )

    def claim_due_operation(self, **kwargs) -> ReconcileLease | None:
        return self._shared.claim_due_operation(**kwargs)

    def renew_lease(self, lease: ReconcileLease, **kwargs) -> ReconcileLease:
        return self._shared.renew_lease(lease, **kwargs)

    def release_lease(self, lease: ReconcileLease, **kwargs) -> bool:
        return self._shared.release_lease(lease, **kwargs)

    def plan_next_action(self, lease: ReconcileLease, **kwargs) -> ReconcileAction:
        return self._shared.plan_next_action(lease, **kwargs)

    def prepare_submission(self, lease: ReconcileLease, **kwargs) -> PreparedSubmission:
        return self._shared.prepare_submission(lease, **kwargs)

    def refresh_local_aggregate(self, lease: ReconcileLease, **kwargs) -> str:
        return self._shared.refresh_local_aggregate(lease, **kwargs)

    def recover_stale_submissions(self, lease: ReconcileLease, **kwargs) -> int:
        return self._shared.recover_stale_submissions(lease, **kwargs)

    def cancel_operation(self, **kwargs) -> dict[str, object]:
        return self._shared.cancel_operation(**kwargs)

    def fail_operation(self, lease: ReconcileLease, **kwargs) -> None:
        now_iso = kwargs.pop("now_iso", None)
        from app.modules.media.durable_workflow.timing import utc_now_iso

        now = now_iso or utc_now_iso()
        with self._shared.session_factory() as session:
            driver = VoiceDurableWorkflowDriver(VoiceDurableWorkingRepository(session))
            driver.fail_operation(
                operation_id=lease.operation_id,
                reconcile_owner=lease.owner,
                reconcile_token=lease.token,
                now_iso=now,
                **kwargs,
            )
            session.commit()
