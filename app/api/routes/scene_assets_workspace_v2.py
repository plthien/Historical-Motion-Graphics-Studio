from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.scene_asset_workspace_v2_schemas import (
    SceneAssetV2BeatResponse,
    SceneAssetV2DecisionRequest,
    SceneAssetV2MutationResponse,
    SceneAssetV2PromptEditRequest,
    SceneAssetV2PromptRestoreRequest,
    SceneAssetV2PublishResponse,
    SceneAssetV2WorkspaceResponse,
)
from app.db.dependencies import get_db_session
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.modules.media.image_upload_validation import MAX_IMAGE_UPLOAD_BYTES
from app.modules.v4.scene_asset_prompt_v2 import SceneAssetPromptV2Service
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService


router = APIRouter(prefix="/api/v4", tags=["Scene Assets V2 Workspace"])


@router.get(
    "/projects/{project_id}/scene-assets",
    response_model=SceneAssetV2WorkspaceResponse,
)
def get_scene_assets_workspace_v2(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2WorkspaceResponse:
    return SceneAssetV2WorkspaceResponse.model_validate(
        SceneAssetWorkspaceV2Service(session).workspace(project_id)
    )


@router.get(
    "/projects/{project_id}/scene-assets/beats/{beat_id}",
    response_model=SceneAssetV2BeatResponse,
)
def get_scene_asset_beat_workspace_v2(
    project_id: str,
    beat_id: str,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2BeatResponse:
    return SceneAssetV2BeatResponse.model_validate(
        SceneAssetWorkspaceV2Service(session).beat(project_id, beat_id)
    )


@router.put("/projects/{project_id}/scene-assets/beats/{beat_id}/prompt")
def edit_scene_asset_prompt_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2PromptEditRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return SceneAssetPromptV2Service(session).edit(
        project_id,
        beat_id,
        expected_beat_version=request.expected_beat_version,
        expected_prompt_revision_id=request.expected_prompt_revision_id,
        image_prompt=request.image_prompt,
        negative_prompt=request.negative_prompt,
    )


@router.post("/projects/{project_id}/scene-assets/beats/{beat_id}/prompt/restore-compiled")
def restore_scene_asset_prompt_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2PromptRestoreRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return SceneAssetPromptV2Service(session).restore(
        project_id,
        beat_id,
        expected_beat_version=request.expected_beat_version,
        expected_prompt_revision_id=request.expected_prompt_revision_id,
    )


@router.get("/projects/{project_id}/scene-assets/{artifact_id}/content")
def get_scene_asset_candidate_content_v2(
    project_id: str,
    artifact_id: str,
    session: Session = Depends(get_db_session),
) -> FileResponse:
    candidate = session.scalar(
        select(SceneAssetCandidate).where(
            SceneAssetCandidate.project_id == project_id,
            SceneAssetCandidate.artifact_id == artifact_id,
        )
    )
    if candidate is None:
        raise AppError(
            "SCENE_ASSET_ARTIFACT_NOT_FOUND",
            "Scene Asset candidate Artifact does not belong to this project.",
            404,
            retryable=False,
            details={"artifact_id": artifact_id},
        )
    artifact = session.get(Artifact, artifact_id)
    if (
        artifact is None
        or artifact.status != ArtifactStatus.READY
        or artifact.deleted_at is not None
        or artifact.artifact_type != ArtifactType.IMAGE
        or not artifact.mime_type
    ):
        raise AppError("SCENE_ASSET_ARTIFACT_INVALID", "Scene Asset candidate Artifact is unavailable.", 409, retryable=False)
    service = ArtifactService(session, ArtifactRepository(session))
    path = service.resolve_file_path(artifact)
    return FileResponse(
        path=path,
        media_type=artifact.mime_type,
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/upload",
    response_model=SceneAssetV2MutationResponse,
)
async def upload_scene_asset_candidate_v2(
    project_id: str,
    beat_id: str,
    expected_beat_version: int = Form(..., ge=1),
    file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
) -> SceneAssetV2MutationResponse:
    try:
        content = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        content_type = file.content_type
    finally:
        await file.close()
    return SceneAssetV2MutationResponse.model_validate(
        SceneAssetWorkspaceV2Service(session).upload_candidate(
            project_id,
            beat_id,
            content,
            declared_content_type=content_type,
            expected_beat_version=expected_beat_version,
        )
    )


def _decision(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2DecisionRequest,
    decision: str,
    session: Session,
) -> SceneAssetV2MutationResponse:
    return SceneAssetV2MutationResponse.model_validate(
        SceneAssetWorkspaceV2Service(session).decide_candidate(
            project_id,
            beat_id,
            candidate_id=request.candidate_id,
            decision=decision,
            expected_beat_version=request.expected_beat_version,
        )
    )


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/candidates/set-current-draft",
    response_model=SceneAssetV2MutationResponse,
)
def set_scene_asset_current_draft_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2DecisionRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2MutationResponse:
    return _decision(project_id, beat_id, request, "set-current-draft", session)


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/candidates/approve",
    response_model=SceneAssetV2MutationResponse,
)
def approve_scene_asset_candidate_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2DecisionRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2MutationResponse:
    return _decision(project_id, beat_id, request, "approve", session)


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/candidates/reject",
    response_model=SceneAssetV2MutationResponse,
)
def reject_scene_asset_candidate_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2DecisionRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2MutationResponse:
    return _decision(project_id, beat_id, request, "reject", session)


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/candidates/clear-approval",
    response_model=SceneAssetV2MutationResponse,
)
def clear_scene_asset_candidate_approval_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2DecisionRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2MutationResponse:
    return _decision(project_id, beat_id, request, "clear-approval", session)


@router.post(
    "/projects/{project_id}/scene-assets/approve",
    response_model=SceneAssetV2PublishResponse,
)
def publish_scene_asset_manifest_v2(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2PublishResponse:
    return SceneAssetV2PublishResponse.model_validate(
        SceneAssetWorkspaceV2Service(session).publish_approved_manifest(project_id)
    )
