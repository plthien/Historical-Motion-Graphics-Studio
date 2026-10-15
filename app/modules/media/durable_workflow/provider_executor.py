from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ProviderTransport:
    """Opaque transport identity used by the shared durable provider envelope.

    ``payload`` is deliberately domain-owned. The shared executor only needs the
    durable task identity, the transport identity/batch, and the provider key so
    it can sequence capacity release, authorization fences, provider I/O, and
    reconciliation without learning any domain workflow semantics.
    """

    operation_task_id: str
    media_task_id: str
    batch_id: str
    provider: str
    payload: Any = None


class DurableProviderDriver(Protocol):
    """Domain adapter required by :class:`DurableMediaProviderExecutor`.

    The driver owns domain validation, durable state transitions, transport
    ownership, and result materialization. The shared executor owns only the
    cross-domain ordering envelope around those operations.
    """

    def preflight(self, operation_task_id: str) -> None: ...

    def prepare_submission(
        self,
        lease: Any,
        *,
        operation_task_id: str,
        now_iso: str,
    ) -> str: ...

    def ensure_transport(self, operation_task_id: str, *, now_iso: str) -> ProviderTransport: ...

    def load_transport(self, operation_task_id: str) -> ProviderTransport | None: ...

    def release_terminal_capacity(self, *, provider: str, now_iso: str) -> None: ...

    def submission_authorized(
        self,
        operation_task_id: str,
        media_task_id: str,
        *,
        allow_waiting: bool,
    ) -> bool: ...

    def cancel_queued_transport(self, media_task_id: str, *, now_iso: str) -> None: ...

    def assert_transport_owner(self, operation_task_id: str, media_task_id: str) -> None: ...

    def advance_transport(self, transport: ProviderTransport, *, client: Any | None = None) -> None: ...

    def reconcile_task(self, operation_task_id: str) -> Any: ...

    def recover_unbridged_submission(self, operation_task_id: str, *, now_iso: str) -> Any: ...

    def snapshot_result(self, operation_task_id: str) -> Any: ...


class DurableMediaProviderExecutor:
    """Reusable provider-I/O envelope for DB-backed durable media workflows.

    Submission order is intentionally fixed:

    preflight -> fenced submit intent -> transport bridge -> capacity cleanup
    -> authorization fence -> ownership fence -> provider I/O -> reconcile.

    Resume order starts from the already-linked transport and applies the same
    capacity/authorization/ownership fences before provider I/O. Domain state
    machines stay entirely behind ``DurableProviderDriver``.
    """

    def __init__(self, driver: DurableProviderDriver) -> None:
        self.driver = driver

    def submit_task(
        self,
        lease: Any,
        *,
        operation_task_id: str,
        now_iso: str,
        client: Any | None = None,
    ) -> Any:
        self.driver.preflight(operation_task_id)
        prepared_task_id = self.driver.prepare_submission(
            lease,
            operation_task_id=operation_task_id,
            now_iso=now_iso,
        )
        transport = self.driver.ensure_transport(prepared_task_id, now_iso=now_iso)
        self.driver.release_terminal_capacity(provider=transport.provider, now_iso=now_iso)
        if not self.driver.submission_authorized(
            prepared_task_id,
            transport.media_task_id,
            allow_waiting=False,
        ):
            self.driver.cancel_queued_transport(transport.media_task_id, now_iso=now_iso)
            return self.driver.snapshot_result(prepared_task_id)
        self.driver.assert_transport_owner(prepared_task_id, transport.media_task_id)
        self.driver.advance_transport(transport, client=client)
        return self.driver.reconcile_task(prepared_task_id)

    def resume_or_advance_task(
        self,
        *,
        operation_task_id: str,
        now_iso: str,
        client: Any | None = None,
    ) -> Any:
        transport = self.driver.load_transport(operation_task_id)
        if transport is None:
            return self.driver.recover_unbridged_submission(operation_task_id, now_iso=now_iso)
        self.driver.release_terminal_capacity(provider=transport.provider, now_iso=now_iso)
        if not self.driver.submission_authorized(
            operation_task_id,
            transport.media_task_id,
            allow_waiting=True,
        ):
            return self.driver.snapshot_result(operation_task_id)
        self.driver.assert_transport_owner(operation_task_id, transport.media_task_id)
        self.driver.advance_transport(transport, client=client)
        return self.driver.reconcile_task(operation_task_id)
