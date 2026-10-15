from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import JobStatus
from app.db.models import Job
from app.repositories import JobRepository
JOB_STALE_QUEUED_STATE = "JOB_STALE_QUEUED"
JOB_STALE_RUNNING_STATE = "JOB_STALE_RUNNING"

logger = logging.getLogger(__name__)

_SECRET_KEY_TOKENS = (
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "secret",
    "password",
    "token",
    "credential",
    "cookie",
    "set-cookie",
    "x-api-key",
)
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_API_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)")
_OPENAI_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")


@dataclass(slots=True)
class PagedResult:
    items: list[Any]
    page: int
    page_size: int
    total: int


@dataclass(slots=True)
class WorkerJobPayload:
    job_id: str
    job_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "payload": dict(self.payload),
        }
        if self.timeout_seconds is not None:
            data["timeout_seconds"] = self.timeout_seconds
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkerJobPayload":
        payload = raw.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("WorkerJobPayload.payload must be an object.")
        timeout_raw = raw.get("timeout_seconds")
        return cls(
            job_id=str(raw["job_id"]),
            job_type=str(raw["job_type"]),
            payload=payload,
            timeout_seconds=int(timeout_raw) if timeout_raw is not None else None,
        )


@dataclass(slots=True)
class JobExecutionContext:
    session: Session
    payload: WorkerJobPayload
    job: Job
    lifecycle: "JobLifecycleService"


JobHandler = Callable[[JobExecutionContext], Any]


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _staleness_reference_timestamp(job: Job) -> tuple[str, datetime] | None:
    heartbeat = _parse_iso_timestamp(job.heartbeat_at)
    if heartbeat is not None and job.heartbeat_at is not None:
        return job.heartbeat_at, heartbeat

    for value in (job.updated_at, job.started_at, job.created_at):
        parsed = _parse_iso_timestamp(value)
        if parsed is not None and value is not None:
            return value, parsed

    return None


def _looks_secret_key(key: str | None) -> bool:
    if not key:
        return False
    lowered = key.lower()
    return any(token in lowered for token in _SECRET_KEY_TOKENS)


def _redact_secret_string(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = _BEARER_PATTERN.sub("[redacted bearer token]", value)
    redacted = _API_KEY_PATTERN.sub(r"\1[redacted]", redacted)
    redacted = _OPENAI_KEY_PATTERN.sub("[redacted api key]", redacted)
    return redacted


def _sanitize_error_details(value: Any, *, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _looks_secret_key(key):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_error_details(item, parent_key=key)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_error_details(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        if _looks_secret_key(parent_key):
            return "[redacted]"
        return _redact_secret_string(value)
    return value


class JobLifecycleService:
    def __init__(self, session: Session, repository: JobRepository) -> None:
        self.session = session
        self.repository = repository

    def list_jobs(
        self,
        *,
        page: int,
        page_size: int,
        sort: str,
        status: str | None,
        job_type: str | None,
    ) -> PagedResult:
        offset = (page - 1) * page_size
        items = self.repository.list_all(
            status=status,
            job_type=job_type,
            sort=sort,
            offset=offset,
            limit=page_size,
        )
        total = self.repository.count_all(status=status, job_type=job_type)
        return PagedResult(items=items, page=page, page_size=page_size, total=total)

    def get_required(self, job_id: str) -> Job:
        job = self.repository.get(job_id)
        if job is None:
            raise AppError(
                "JOB_NOT_FOUND",
                f"Job '{job_id}' was not found.",
                404,
                retryable=False,
                details={"job_id": job_id},
            )
        return job

    def create_job(
        self,
        *,
        job_type: str,
        artifact_type: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        attempt: int = 1,
        retryable: bool = False,
    ) -> Job:
        if attempt < 1:
            raise AppError(
                "JOB_ATTEMPT_INVALID",
                "Job attempt must be at least 1.",
                400,
                retryable=False,
                details={"attempt": attempt},
            )
        now = utc_now_iso()
        job = Job(
            job_id=generate_id("job"),
            job_type=job_type,
            status=JobStatus.QUEUED,
            progress=0,
            artifact_type=artifact_type,
            target_type=target_type,
            target_id=target_id,
            attempt=attempt,
            retryable=retryable,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            completed_at=None,
        )
        self.repository.create(job)
        self.session.commit()
        self.session.refresh(job)
        logger.info("Created job %s type=%s", job.job_id, job.job_type)
        return job

    def build_worker_payload(
        self,
        job: Job,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> WorkerJobPayload:
        return WorkerJobPayload(
            job_id=job.job_id,
            job_type=job.job_type,
            payload=payload or {},
            timeout_seconds=timeout_seconds,
        )

    def claim_for_execution(self, job_id: str) -> Job:
        job = self.get_required(job_id)
        if job.status == JobStatus.RUNNING:
            raise AppError("JOB_ALREADY_RUNNING", "Job is already running.", 409, retryable=False, details={"job_id": job_id})
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STALE_FAILED}:
            raise AppError(
                "JOB_NOT_EXECUTABLE",
                "Only queued jobs may be executed by the worker.",
                409,
                retryable=False,
                details={"job_id": job_id, "status": job.status.value},
            )
        logger.info("Claimed job %s for execution", job.job_id)
        return self.mark_running(job)

    def mark_running(self, job: Job) -> Job:
        now = utc_now_iso()
        job.status = JobStatus.RUNNING
        job.updated_at = now
        job.started_at = job.started_at or now
        job.finished_at = None
        job.heartbeat_at = now
        job.completed_at = None
        job.retryable = False
        job.error_code = None
        job.error_message = None
        job.error_details_json = None
        self.session.flush()
        logger.info("Started job %s", job.job_id)
        return job

    def mark_heartbeat(self, job: Job) -> Job:
        now = utc_now_iso()
        job.heartbeat_at = now
        job.updated_at = now
        self.session.flush()
        logger.info("Heartbeat job %s", job.job_id)
        return job

    def mark_progress(self, job: Job, progress: int) -> Job:
        if progress < 0 or progress > 100:
            raise AppError(
                "JOB_PROGRESS_INVALID",
                "Job progress must be between 0 and 100.",
                400,
                retryable=False,
                details={"progress": progress},
            )
        job.progress = progress
        self.mark_heartbeat(job)
        logger.info("Progress job %s -> %s", job.job_id, progress)
        return job

    def mark_succeeded(self, job: Job, *, progress: int = 100) -> Job:
        self.mark_progress(job, progress)
        now = utc_now_iso()
        job.status = JobStatus.COMPLETED
        job.retryable = False
        job.updated_at = now
        job.heartbeat_at = now
        job.finished_at = now
        job.completed_at = now
        self.session.flush()
        logger.info("Completed job %s", job.job_id)
        return job

    def mark_completed(self, job: Job, *, progress: int = 100) -> Job:
        return self.mark_succeeded(job, progress=progress)

    def mark_failed(self, job: Job, error: Exception) -> Job:
        if isinstance(error, AppError):
            error_code = error.error_code
            message = error.message
            retryable = error.retryable
            details: dict[str, Any] | list[Any] = error.details
        else:
            error_code = "JOB_EXECUTION_FAILED"
            message = str(error) or error.__class__.__name__
            retryable = False
            details = {"exception_type": error.__class__.__name__}

        return self.mark_failed_details(
            job,
            error_code=error_code,
            error_message=message,
            retryable=retryable,
            error_details=details,
        )

    def mark_failed_details(
        self,
        job: Job,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        error_details: dict[str, Any] | list[Any] | None = None,
    ) -> Job:
        now = utc_now_iso()
        job.status = JobStatus.FAILED
        job.retryable = retryable
        job.updated_at = now
        job.heartbeat_at = now
        job.finished_at = now
        job.completed_at = now
        job.error_code = error_code
        job.error_message = _redact_secret_string(error_message)
        job.error_details_json = _sanitize_error_details(error_details)
        self.session.flush()
        logger.info("Failed job %s code=%s retryable=%s", job.job_id, error_code, retryable)
        return job

    def mark_cancelled(self, job: Job, reason: str | None = None) -> Job:
        now = utc_now_iso()
        job.status = JobStatus.CANCELLED
        job.retryable = False
        job.updated_at = now
        job.heartbeat_at = now
        job.finished_at = now
        job.completed_at = now
        job.error_code = "JOB_CANCELLED"
        job.error_message = reason
        job.error_details_json = {"reason": reason} if reason else None
        self.session.flush()
        logger.info("Cancelled job %s", job.job_id)
        return job

    def mark_stale_failed(
        self,
        job: Job,
        *,
        max_age_seconds: int,
        last_observed_at: str | None,
        cutoff_timestamp: str,
    ) -> Job:
        recovered = self.mark_stale_failed_details(
            job,
            error_code=JOB_STALE_RUNNING_STATE,
            error_message="Job was left running beyond the platform stale recovery threshold.",
            error_details={
                "job_id": job.job_id,
                "last_observed_at": last_observed_at,
                "heartbeat_at": job.heartbeat_at,
                "updated_at": job.updated_at,
                "max_age_seconds": max_age_seconds,
                "cutoff_timestamp": cutoff_timestamp,
            },
        )
        logger.info("Recovered stale running job %s", job.job_id)
        return recovered

    def mark_stale_queued_failed(
        self,
        job: Job,
        *,
        threshold_seconds: int,
        last_observed_at: str | None,
        cutoff_timestamp: str,
    ) -> Job:
        recovered = self.mark_stale_failed_details(
            job,
            error_code=JOB_STALE_QUEUED_STATE,
            error_message="Job remained queued beyond the platform stale recovery threshold and is assumed orphaned.",
            error_details={
                "job_id": job.job_id,
                "last_observed_at": last_observed_at,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "threshold_seconds": threshold_seconds,
                "cutoff_timestamp": cutoff_timestamp,
            },
        )
        logger.info("Recovered stale queued job %s", job.job_id)
        return recovered

    def mark_stale_failed_details(
        self,
        job: Job,
        *,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | list[Any] | None = None,
    ) -> Job:
        now = utc_now_iso()
        job.status = JobStatus.STALE_FAILED
        job.retryable = True
        job.updated_at = now
        job.finished_at = now
        job.completed_at = now
        job.error_code = error_code
        job.error_message = _redact_secret_string(error_message)
        job.error_details_json = _sanitize_error_details(error_details)
        self.session.flush()
        return job

    def recover_stale_running_jobs(
        self,
        *,
        max_age_seconds: int,
        job_types: list[str] | tuple[str, ...] | None = None,
        now_iso: str | None = None,
    ) -> list[Job]:
        if max_age_seconds <= 0:
            raise AppError(
                "JOB_STALE_THRESHOLD_INVALID",
                "Stale job recovery threshold must be greater than 0 seconds.",
                400,
                retryable=False,
                details={"max_age_seconds": max_age_seconds},
            )

        current_now_iso = now_iso or utc_now_iso()
        now_dt = _parse_iso_timestamp(current_now_iso)
        if now_dt is None:
            raise AppError(
                "JOB_STALE_TIMESTAMP_INVALID",
                "Stale job recovery requires a valid ISO timestamp.",
                400,
                retryable=False,
                details={"now_iso": current_now_iso},
            )

        cutoff = now_dt - timedelta(seconds=max_age_seconds)
        cutoff_iso = cutoff.isoformat()
        recovered: list[Job] = []
        for job in self.repository.list_running(job_types=job_types):
            observed = _staleness_reference_timestamp(job)
            if observed is None:
                recovered.append(
                    self.mark_stale_failed(
                        job,
                        max_age_seconds=max_age_seconds,
                        last_observed_at=None,
                        cutoff_timestamp=cutoff_iso,
                    )
                )
                continue
            observed_raw, observed_dt = observed
            if observed_dt <= cutoff:
                recovered.append(
                    self.mark_stale_failed(
                        job,
                        max_age_seconds=max_age_seconds,
                        last_observed_at=observed_raw,
                        cutoff_timestamp=cutoff_iso,
                    )
                )
        return recovered

    def recover_stale_queued_jobs(
        self,
        *,
        max_age_seconds: int,
        job_types: list[str] | tuple[str, ...] | None = None,
        now_iso: str | None = None,
    ) -> list[Job]:
        if max_age_seconds <= 0:
            raise AppError(
                "JOB_STALE_THRESHOLD_INVALID",
                "Stale job recovery threshold must be greater than 0 seconds.",
                400,
                retryable=False,
                details={"max_age_seconds": max_age_seconds},
            )

        current_now_iso = now_iso or utc_now_iso()
        now_dt = _parse_iso_timestamp(current_now_iso)
        if now_dt is None:
            raise AppError(
                "JOB_STALE_TIMESTAMP_INVALID",
                "Stale job recovery requires a valid ISO timestamp.",
                400,
                retryable=False,
                details={"now_iso": current_now_iso},
            )

        cutoff = now_dt - timedelta(seconds=max_age_seconds)
        cutoff_iso = cutoff.isoformat()
        recovered: list[Job] = []
        for job in self.repository.list_queued(job_types=job_types):
            observed = _staleness_reference_timestamp(job)
            if observed is None:
                recovered.append(
                    self.mark_stale_queued_failed(
                        job,
                        threshold_seconds=max_age_seconds,
                        last_observed_at=None,
                        cutoff_timestamp=cutoff_iso,
                    )
                )
                continue
            observed_raw, observed_dt = observed
            if observed_dt <= cutoff:
                recovered.append(
                    self.mark_stale_queued_failed(
                        job,
                        threshold_seconds=max_age_seconds,
                        last_observed_at=observed_raw,
                        cutoff_timestamp=cutoff_iso,
                    )
                )
        return recovered


class JobHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> JobHandler:
        handler = self._handlers.get(job_type)
        if handler is None:
            raise AppError(
                "JOB_HANDLER_NOT_FOUND",
                "No worker handler is registered for this job type.",
                500,
                retryable=False,
                details={"job_type": job_type},
            )
        return handler

    def registered_job_types(self) -> list[str]:
        return sorted(self._handlers)


class LocalWorkerRunner:
    def __init__(self, session_factory: sessionmaker[Session], registry: JobHandlerRegistry | None = None) -> None:
        self.session_factory = session_factory
        self.registry = registry or JobHandlerRegistry()

    def run(self, payload: WorkerJobPayload) -> Job:
        with self.session_factory() as session:
            lifecycle = JobLifecycleService(session, JobRepository(session))
            lifecycle.claim_for_execution(payload.job_id)
            session.commit()

        try:
            with self.session_factory() as session:
                lifecycle = JobLifecycleService(session, JobRepository(session))
                job = lifecycle.get_required(payload.job_id)
                self._validate_payload_matches_job(job, payload)
                handler = self.registry.get(job.job_type)
                handler(JobExecutionContext(session=session, payload=payload, job=job, lifecycle=lifecycle))
                session.refresh(job)
                if job.status == JobStatus.RUNNING:
                    lifecycle.mark_succeeded(job)
                session.commit()
                session.refresh(job)
                return job
        except Exception as exc:
            with self.session_factory() as session:
                lifecycle = JobLifecycleService(session, JobRepository(session))
                job = lifecycle.get_required(payload.job_id)
                lifecycle.mark_failed(job, exc)
                session.commit()
                session.refresh(job)
                return job

    @staticmethod
    def _validate_payload_matches_job(job: Job, payload: WorkerJobPayload) -> None:
        if job.job_type != payload.job_type:
            raise AppError(
                "JOB_TYPE_MISMATCH",
                "Worker payload job_type does not match the stored job.",
                409,
                retryable=False,
                details={"job_id": payload.job_id, "stored_job_type": job.job_type, "payload_job_type": payload.job_type},
            )
