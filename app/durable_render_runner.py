from __future__ import annotations

import logging
import signal
from threading import Event

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.v4.render_process_diagnostics import install_render_process_diagnostics
from app.modules.v4.windows_subprocess_guard import (
    block_and_terminate_owned_processes,
    install_windows_subprocess_job_ownership,
)
from app.render_coordinator_runner import RenderCoordinatorRunner


logger = logging.getLogger(__name__)


def production_lease_seconds(sqlite_busy_timeout_ms: int) -> int:
    """Keep the Render lease comfortably above one SQLite writer wait window."""

    busy_seconds = max(1, (int(sqlite_busy_timeout_ms) + 999) // 1000)
    return max(60, busy_seconds * 4)


def build_runner(*, windows_process_guard_active: bool = False) -> RenderCoordinatorRunner:
    runtime = get_settings()
    return RenderCoordinatorRunner(
        get_session_factory(),
        lease_seconds=production_lease_seconds(runtime.sqlite_busy_timeout_ms),
        fatal_execution_loss=(
            (lambda _operation_id: block_and_terminate_owned_processes())
            if windows_process_guard_active
            else None
        ),
    )


def _latest_failed_job() -> dict[str, object] | None:
    with get_session_factory()() as session:
        row = session.execute(
            text(
                """
                SELECT ro.render_operation_id,
                       ro.job_id,
                       ro.attempt_no,
                       j.error_code,
                       j.error_message,
                       j.error_details_json,
                       COALESCE(ro.completed_at, ro.updated_at) AS failed_at
                FROM render_operations ro
                JOIN jobs j ON j.job_id = ro.job_id
                WHERE ro.status = 'failed'
                ORDER BY COALESCE(ro.completed_at, ro.updated_at) DESC,
                         ro.render_operation_id DESC
                LIMIT 1
                """
            )
        ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _failure_signature(failure: dict[str, object] | None) -> tuple[str, int, str] | None:
    if failure is None:
        return None
    return (
        str(failure["job_id"]),
        int(failure["attempt_no"]),
        str(failure["failed_at"]),
    )


def _log_persisted_failure(failure: dict[str, object] | None) -> None:
    if failure is None:
        return
    logger.warning(
        "Latest persisted Render failure operation_id=%s job_id=%s attempt_no=%s code=%s message=%s details=%s",
        failure["render_operation_id"],
        failure["job_id"],
        failure["attempt_no"],
        failure["error_code"],
        failure["error_message"],
        failure["error_details_json"],
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_render_process_diagnostics()
    windows_process_guard_active = install_windows_subprocess_job_ownership()
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)

    runtime = get_settings()
    runner = build_runner(windows_process_guard_active=windows_process_guard_active)
    latest_failure = _latest_failed_job()
    last_logged_failure_signature = _failure_signature(latest_failure)
    logger.info(
        "Durable Render host started owner=%s lease_seconds=%s windows_process_guard=%s process_diagnostics=True database=%s artifact_root=%s",
        runner.owner,
        runner.lease_seconds,
        windows_process_guard_active,
        runtime.sqlite_file_path,
        runtime.resolve_configured_path(runtime.artifact_root),
    )
    _log_persisted_failure(latest_failure)
    while not stop.is_set():
        if not runner.run_once():
            stop.wait(0.5)
            continue
        failure = _latest_failed_job()
        failure_signature = _failure_signature(failure)
        if failure is None or failure_signature == last_logged_failure_signature:
            continue
        last_logged_failure_signature = failure_signature
        logger.error(
            "Render failed operation_id=%s job_id=%s attempt_no=%s code=%s message=%s details=%s",
            failure["render_operation_id"],
            failure["job_id"],
            failure["attempt_no"],
            failure["error_code"],
            failure["error_message"],
            failure["error_details_json"],
        )
    logger.info("Durable Render host stopped")


if __name__ == "__main__":
    main()
