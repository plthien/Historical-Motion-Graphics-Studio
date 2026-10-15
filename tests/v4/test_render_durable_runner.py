from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.errors import AppError
from app.render_coordinator_runner import RenderCoordinatorRunner, _RenderLeaseKeepalive


def _factory(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'render-runner.db'}", future=True)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
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
                    'render_operation_1', 'queued', '2000-01-01T00:00:00+00:00',
                    NULL, NULL, NULL, 0,
                    '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00'
                )
                """
            )
        )
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_dedicated_render_runner_claims_due_operation(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    executed: list[tuple[str, str]] = []
    runner = RenderCoordinatorRunner(
        factory,
        owner="host-a",
        executor=lambda operation_id, token: executed.append((operation_id, token)),
    )

    assert runner.run_once()
    assert len(executed) == 1
    assert executed[0][0] == "render_operation_1"
    assert executed[0][1]


def test_second_render_host_cannot_execute_while_first_lease_is_live(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    second_executed: list[str] = []
    second = RenderCoordinatorRunner(
        factory,
        owner="host-b",
        executor=lambda operation_id, _token: second_executed.append(operation_id),
    )

    def first_executor(_operation_id: str, _token: str) -> None:
        assert not second.run_once()

    first = RenderCoordinatorRunner(factory, owner="host-a", executor=first_executor)
    assert first.run_once()
    assert second_executed == []


def test_render_runner_renews_lease_during_long_execution(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    second_executed: list[str] = []
    second = RenderCoordinatorRunner(
        factory,
        owner="host-b",
        lease_seconds=1,
        executor=lambda operation_id, _token: second_executed.append(operation_id),
    )

    def first_executor(_operation_id: str, _token: str) -> None:
        time.sleep(1.2)
        assert not second.run_once()

    first = RenderCoordinatorRunner(
        factory,
        owner="host-a",
        lease_seconds=1,
        executor=first_executor,
    )
    assert first.run_once()
    assert second_executed == []


def test_runner_rejects_stale_execution_token_before_execution(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    runner = RenderCoordinatorRunner(factory, owner="host-a", executor=lambda *_args: None)
    lease = runner._claim_due_operation()
    assert lease is not None

    with factory() as session:
        session.execute(
            text(
                """
                UPDATE render_operations
                SET reconcile_owner = 'host-b', reconcile_token = 'replacement-token'
                WHERE render_operation_id = :operation_id
                """
            ),
            {"operation_id": lease.operation_id},
        )
        session.commit()

    try:
        runner._fence_required(lease.operation_id, lease.token)
    except AppError as exc:
        assert exc.error_code == "RENDER_EXECUTION_FENCE_LOST"
    else:
        raise AssertionError("stale Render execution token must be fenced")


def test_stale_render_token_cannot_pass_commit_fence(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    runner = RenderCoordinatorRunner(factory, owner="host-a", executor=lambda *_args: None)
    lease = runner._claim_due_operation()
    assert lease is not None
    keepalive = _RenderLeaseKeepalive(runner, lease)

    with factory() as session:
        session.execute(
            text(
                """
                UPDATE render_operations
                SET reconcile_owner = 'host-b', reconcile_token = 'replacement-token'
                WHERE render_operation_id = :operation_id
                """
            ),
            {"operation_id": lease.operation_id},
        )
        session.commit()

    with factory() as session:
        try:
            runner._fence_session_commit(session, lease, keepalive)
        except AppError as exc:
            assert exc.error_code == "RENDER_EXECUTION_FENCE_LOST"
        else:
            raise AssertionError("stale Render execution token must not pass commit fence")
