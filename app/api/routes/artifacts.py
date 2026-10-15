from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.schemas import ArtifactDownloadResponse, ArtifactListResponse, ArtifactModel
from app.core.config import get_settings
from app.db.dependencies import get_db_session
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService

router = APIRouter(prefix="/api/artifacts", tags=["Artifacts"])


def build_service(session: Session) -> ArtifactService:
    return ArtifactService(session, ArtifactRepository(session), get_settings())


def to_artifact_model(artifact) -> ArtifactModel:
    return ArtifactModel(
        artifact_id=artifact.artifact_id,
        logical_id=artifact.logical_id,
        version_no=artifact.version_no,
        is_current=artifact.is_current,
        artifact_type=artifact.artifact_type.value,
        status=artifact.status.value,
        artifact_role=artifact.artifact_role.value if artifact.artifact_role else None,
        created_at=artifact.created_at,
        path=artifact.path,
    )


@router.get("", response_model=ArtifactListResponse)
def list_artifacts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="created_at_desc"),
    artifact_type: str | None = Query(default=None),
    logical_id: str | None = Query(default=None),
    is_current: bool | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> ArtifactListResponse:
    repository = ArtifactRepository(session)
    offset = (page - 1) * page_size
    items = repository.list_all(
        artifact_type=artifact_type,
        logical_id=logical_id,
        is_current=is_current,
        sort=sort,
        offset=offset,
        limit=page_size,
    )
    total = repository.count_all(artifact_type=artifact_type, logical_id=logical_id, is_current=is_current)
    return ArtifactListResponse(items=[to_artifact_model(item) for item in items], page=page, page_size=page_size, total=total)


@router.get("/{artifact_id}", response_model=ArtifactModel)
def get_artifact(artifact_id: str, session: Session = Depends(get_db_session)) -> ArtifactModel:
    return to_artifact_model(build_service(session).get_required(artifact_id))


@router.get("/{artifact_id}/download", response_model=ArtifactDownloadResponse, response_model_exclude_none=True)
def get_artifact_download(artifact_id: str, session: Session = Depends(get_db_session)) -> ArtifactDownloadResponse:
    return ArtifactDownloadResponse.model_validate(build_service(session).build_download_descriptor(artifact_id))


@router.get("/{artifact_id}/content", include_in_schema=False)
def get_artifact_content(artifact_id: str, session: Session = Depends(get_db_session)) -> FileResponse:
    service = build_service(session)
    artifact = service.get_required(artifact_id)
    path = service.resolve_file_path(artifact)
    return FileResponse(path=path, media_type=artifact.mime_type or "application/octet-stream", filename=Path(path).name)
