from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.schemas import JobListResponse, JobModel
from app.db.dependencies import get_db_session
from app.repositories import JobRepository
from app.services.jobs import JobLifecycleService

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


def build_service(session: Session) -> JobLifecycleService:
    return JobLifecycleService(session, JobRepository(session))


def to_job_model(job) -> JobModel:
    return JobModel(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status.value,
        progress=job.progress,
        artifact_type=job.artifact_type,
        target_type=job.target_type,
        target_id=job.target_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error_code=job.error_code,
        error_message=job.error_message,
    )


@router.get("", response_model=JobListResponse)
def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="created_at_desc"),
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> JobListResponse:
    result = build_service(session).list_jobs(
        page=page,
        page_size=page_size,
        sort=sort,
        status=status,
        job_type=job_type,
    )
    return JobListResponse(
        items=[to_job_model(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/{job_id}", response_model=JobModel)
def get_job(job_id: str, session: Session = Depends(get_db_session)) -> JobModel:
    return to_job_model(build_service(session).get_required(job_id))
