from __future__ import annotations

from typing import Any

from app.modules.media.durable_workflow.provider_executor import (
    DurableMediaProviderExecutor,
    ProviderTransport,
)


NOW = "2026-08-17T12:00:00+00:00"


class FakeDriver:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.authorized = True
        self.transport: ProviderTransport | None = ProviderTransport(
            operation_task_id="task_1",
            media_task_id="media_1",
            batch_id="batch_1",
            provider="provider_1",
            payload={"frozen": True},
        )

    def preflight(self, operation_task_id: str) -> None:
        self.events.append(("preflight", operation_task_id))

    def prepare_submission(self, lease: Any, *, operation_task_id: str, now_iso: str) -> str:
        self.events.append(("prepare", lease, operation_task_id, now_iso))
        return operation_task_id

    def ensure_transport(self, operation_task_id: str, *, now_iso: str) -> ProviderTransport:
        self.events.append(("ensure_transport", operation_task_id, now_iso))
        assert self.transport is not None
        return self.transport

    def load_transport(self, operation_task_id: str) -> ProviderTransport | None:
        self.events.append(("load_transport", operation_task_id))
        return self.transport

    def release_terminal_capacity(self, *, provider: str, now_iso: str) -> None:
        self.events.append(("release_capacity", provider, now_iso))

    def submission_authorized(
        self,
        operation_task_id: str,
        media_task_id: str,
        *,
        allow_waiting: bool,
    ) -> bool:
        self.events.append(("authorized", operation_task_id, media_task_id, allow_waiting))
        return self.authorized

    def cancel_queued_transport(self, media_task_id: str, *, now_iso: str) -> None:
        self.events.append(("cancel_transport", media_task_id, now_iso))

    def assert_transport_owner(self, operation_task_id: str, media_task_id: str) -> None:
        self.events.append(("assert_owner", operation_task_id, media_task_id))

    def advance_transport(self, transport: ProviderTransport, *, client: Any | None = None) -> None:
        self.events.append(("advance", transport.media_task_id, transport.batch_id, client))

    def reconcile_task(self, operation_task_id: str) -> dict[str, str]:
        self.events.append(("reconcile", operation_task_id))
        return {"result": "reconciled"}

    def recover_unbridged_submission(self, operation_task_id: str, *, now_iso: str) -> dict[str, str]:
        self.events.append(("recover_unbridged", operation_task_id, now_iso))
        return {"result": "recovered"}

    def snapshot_result(self, operation_task_id: str) -> dict[str, str]:
        self.events.append(("snapshot", operation_task_id))
        return {"result": "snapshot"}


def test_submit_uses_fixed_fenced_provider_order() -> None:
    driver = FakeDriver()
    executor = DurableMediaProviderExecutor(driver)

    result = executor.submit_task(
        "lease_1",
        operation_task_id="task_1",
        now_iso=NOW,
        client="client_1",
    )

    assert result == {"result": "reconciled"}
    assert driver.events == [
        ("preflight", "task_1"),
        ("prepare", "lease_1", "task_1", NOW),
        ("ensure_transport", "task_1", NOW),
        ("release_capacity", "provider_1", NOW),
        ("authorized", "task_1", "media_1", False),
        ("assert_owner", "task_1", "media_1"),
        ("advance", "media_1", "batch_1", "client_1"),
        ("reconcile", "task_1"),
    ]


def test_submit_cancels_queued_transport_when_authorization_is_lost() -> None:
    driver = FakeDriver()
    driver.authorized = False
    executor = DurableMediaProviderExecutor(driver)

    result = executor.submit_task("lease_1", operation_task_id="task_1", now_iso=NOW)

    assert result == {"result": "snapshot"}
    assert driver.events[-3:] == [
        ("authorized", "task_1", "media_1", False),
        ("cancel_transport", "media_1", NOW),
        ("snapshot", "task_1"),
    ]
    assert not any(event[0] == "advance" for event in driver.events)
    assert not any(event[0] == "reconcile" for event in driver.events)


def test_resume_reuses_transport_and_allows_waiting_owner() -> None:
    driver = FakeDriver()
    executor = DurableMediaProviderExecutor(driver)

    result = executor.resume_or_advance_task(
        operation_task_id="task_1",
        now_iso=NOW,
        client="client_2",
    )

    assert result == {"result": "reconciled"}
    assert driver.events == [
        ("load_transport", "task_1"),
        ("release_capacity", "provider_1", NOW),
        ("authorized", "task_1", "media_1", True),
        ("assert_owner", "task_1", "media_1"),
        ("advance", "media_1", "batch_1", "client_2"),
        ("reconcile", "task_1"),
    ]


def test_resume_recovers_missing_transport_without_provider_io() -> None:
    driver = FakeDriver()
    driver.transport = None
    executor = DurableMediaProviderExecutor(driver)

    result = executor.resume_or_advance_task(operation_task_id="task_1", now_iso=NOW)

    assert result == {"result": "recovered"}
    assert driver.events == [
        ("load_transport", "task_1"),
        ("recover_unbridged", "task_1", NOW),
    ]
