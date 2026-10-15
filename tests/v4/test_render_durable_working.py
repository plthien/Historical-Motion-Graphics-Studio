from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.repositories.render_durable_working import RenderDurableWorkingRepository


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE render_operations (
                    render_operation_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    next_reconcile_at TEXT,
                    reconcile_owner TEXT,
                    reconcile_token TEXT,
                    reconcile_lease_expires_at TEXT,
                    state_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO render_operations (
                    render_operation_id, status, next_reconcile_at,
                    reconcile_owner, reconcile_token, reconcile_lease_expires_at,
                    state_revision, created_at, updated_at
                ) VALUES (
                    'render_operation_1', 'queued', '2026-09-07T00:00:00+00:00',
                    NULL, NULL, NULL, 0,
                    '2026-09-07T00:00:00+00:00', '2026-09-07T00:00:00+00:00'
                )
                """
            )
        )
    return Session(engine, expire_on_commit=False)


def test_queued_render_requires_explicit_persisted_schedule() -> None:
    session = _session()
    session.execute(
        text(
            """
            UPDATE render_operations
            SET next_reconcile_at = NULL
            WHERE render_operation_id = 'render_operation_1'
            """
        )
    )
    session.commit()

    repository = RenderDurableWorkingRepository(session)
    assert repository.list_due_operation_ids(now_iso="2026-09-07T00:00:01+00:00") == []

    assert repository.schedule_operation(
        operation_id="render_operation_1",
        now_iso="2026-09-07T00:00:01+00:00",
    )
    session.commit()
    assert repository.list_due_operation_ids(now_iso="2026-09-07T00:00:01+00:00") == ["render_operation_1"]


def test_only_one_contender_can_claim_live_render_lease() -> None:
    session = _session()
    repository = RenderDurableWorkingRepository(session)

    assert repository.try_acquire_reconcile_lease(
        operation_id="render_operation_1",
        owner="host-a",
        token="token-a",
        now_iso="2026-09-07T00:00:01+00:00",
        expires_at="2026-09-07T00:01:01+00:00",
    )
    session.commit()

    assert not repository.try_acquire_reconcile_lease(
        operation_id="render_operation_1",
        owner="host-b",
        token="token-b",
        now_iso="2026-09-07T00:00:02+00:00",
        expires_at="2026-09-07T00:01:02+00:00",
    )


def test_expired_render_lease_is_reclaimable() -> None:
    session = _session()
    repository = RenderDurableWorkingRepository(session)

    assert repository.try_acquire_reconcile_lease(
        operation_id="render_operation_1",
        owner="host-a",
        token="token-a",
        now_iso="2026-09-07T00:00:01+00:00",
        expires_at="2026-09-07T00:00:05+00:00",
    )
    session.commit()

    assert repository.try_acquire_reconcile_lease(
        operation_id="render_operation_1",
        owner="host-b",
        token="token-b",
        now_iso="2026-09-07T00:00:06+00:00",
        expires_at="2026-09-07T00:01:06+00:00",
    )
    session.commit()

    row = repository.get_operation("render_operation_1")
    assert row is not None
    assert row.reconcile_owner == "host-b"
    assert row.reconcile_token == "token-b"


def test_stale_render_token_cannot_pass_fence_after_reclaim() -> None:
    session = _session()
    repository = RenderDurableWorkingRepository(session)

    assert repository.try_acquire_reconcile_lease(
        operation_id="render_operation_1",
        owner="host-a",
        token="token-a",
        now_iso="2026-09-07T00:00:01+00:00",
        expires_at="2026-09-07T00:00:05+00:00",
    )
    session.commit()
    assert repository.try_acquire_reconcile_lease(
        operation_id="render_operation_1",
        owner="host-b",
        token="token-b",
        now_iso="2026-09-07T00:00:06+00:00",
        expires_at="2026-09-07T00:01:06+00:00",
    )
    session.commit()

    assert not repository.fence_reconcile_write(
        operation_id="render_operation_1",
        token="token-a",
        now_iso="2026-09-07T00:00:07+00:00",
    )
    assert repository.fence_reconcile_write(
        operation_id="render_operation_1",
        token="token-b",
        now_iso="2026-09-07T00:00:07+00:00",
    )
