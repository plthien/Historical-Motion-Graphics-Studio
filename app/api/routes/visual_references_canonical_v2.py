from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.visual_reference_v2_schemas import (
    AnchorTypeV2,
    VisualReferenceAnchorV2,
    VisualReferenceApproveRequestV2,
    VisualReferenceClearRequestV2,
    VisualReferenceGenerateRequestV2,
    VisualReferenceMutationResultV2,
    VisualReferenceOperationSubmissionV2,
    VisualReferencePromptEditRequestV2,
    VisualReferenceWorkspaceV2,
)
from app.db.dependencies import get_db_session
from app.modules.media.image_upload_validation import MAX_IMAGE_UPLOAD_BYTES
from app.modules.v4.visual_reference_mutations_v2 import VisualReferenceMutationsV2
from app.modules.v4.visual_reference_runtime_v2 import VisualReferenceRuntimeV2
from app.modules.v4.visual_reference_workspace_v2 import VisualReferenceWorkspaceV2Service


router = APIRouter(prefix="/api/v4")


@router.get(
    "/projects/{project_id}/visual-references",
    response_model=VisualReferenceWorkspaceV2,
)
def canonical_visual_reference_workspace_v2(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> dict:
    return VisualReferenceWorkspaceV2Service(session).workspace(project_id)


@router.post(
    "/projects/{project_id}/visual-references/jobs",
    response_model=VisualReferenceOperationSubmissionV2,
    status_code=202,
)
def canonical_submit_visual_reference_v2(
    project_id: str,
    request: VisualReferenceGenerateRequestV2,
    session: Session = Depends(get_db_session),
) -> dict:
    # The historical path is retained only as an HTTP address. Its contract is
    # now the durable V2 operation contract; no Job row or RQ owner is created.
    return VisualReferenceRuntimeV2(session).submit_generation(
        project_id,
        request.model_dump(exclude_none=True),
    )


@router.post(
    "/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/regenerate/jobs",
    response_model=VisualReferenceOperationSubmissionV2,
    status_code=202,
)
def canonical_regenerate_visual_reference_v2(
    project_id: str,
    anchor_type: AnchorTypeV2,
    anchor_id: str,
    request: VisualReferenceGenerateRequestV2,
    session: Session = Depends(get_db_session),
) -> dict:
    return VisualReferenceRuntimeV2(session).submit_anchor_regeneration(
        project_id,
        anchor_type,
        anchor_id,
        request.model_dump(exclude_none=True),
    )


@router.get("/projects/{project_id}/visual-references/jobs/{operation_id}")
def canonical_visual_reference_operation_status_v2(
    project_id: str,
    operation_id: str,
    session: Session = Depends(get_db_session),
) -> dict:
    return VisualReferenceRuntimeV2(session).get_operation_status(project_id, operation_id)


@router.post(
    "/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/upload",
    response_model=VisualReferenceMutationResultV2,
)
async def canonical_upload_visual_reference_v2(
    project_id: str,
    anchor_type: AnchorTypeV2,
    anchor_id: str,
    expected_version: int = Form(..., ge=1),
    file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
) -> dict:
    content = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    return VisualReferenceMutationsV2(session).upload_reference(
        project_id,
        anchor_type,
        anchor_id,
        expected_version=expected_version,
        content=content,
        content_type=file.content_type,
    )


@router.put(
    "/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/prompt",
    response_model=VisualReferenceMutationResultV2,
)
def canonical_update_visual_reference_prompt_v2(
    project_id: str,
    anchor_type: AnchorTypeV2,
    anchor_id: str,
    request: VisualReferencePromptEditRequestV2,
    session: Session = Depends(get_db_session),
) -> dict:
    return VisualReferenceMutationsV2(session).update_prompt(
        project_id,
        anchor_type,
        anchor_id,
        expected_version=request.expected_version,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
    )


@router.post(
    "/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/approve",
    response_model=VisualReferenceMutationResultV2,
)
def canonical_approve_visual_reference_v2(
    project_id: str,
    anchor_type: AnchorTypeV2,
    anchor_id: str,
    request: VisualReferenceApproveRequestV2,
    session: Session = Depends(get_db_session),
) -> dict:
    return VisualReferenceMutationsV2(session).approve(
        project_id,
        anchor_type,
        anchor_id,
        expected_version=request.expected_version,
        candidate_id=request.candidate_id,
    )


@router.post(
    "/projects/{project_id}/visual-references/{anchor_type}/{anchor_id}/clear",
    response_model=VisualReferenceMutationResultV2,
)
def canonical_clear_visual_reference_approval_v2(
    project_id: str,
    anchor_type: AnchorTypeV2,
    anchor_id: str,
    request: VisualReferenceClearRequestV2,
    session: Session = Depends(get_db_session),
) -> dict:
    return VisualReferenceMutationsV2(session).clear_approval(
        project_id,
        anchor_type,
        anchor_id,
        expected_version=request.expected_version,
    )
