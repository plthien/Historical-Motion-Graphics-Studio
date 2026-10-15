from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.visual_reference_v2_schemas import (
    VisualReferencePublicationRequestV2,
    VisualReferencePublicationResultV2,
)
from app.db.dependencies import get_db_session
from app.modules.v4.visual_reference_publication_v2 import (
    VisualReferencePublicationV2Service,
)


router = APIRouter(prefix="/api/v4")


@router.post(
    "/projects/{project_id}/visual-references/v2/publish",
    response_model=VisualReferencePublicationResultV2,
)
def publish_visual_references_v2(
    project_id: str,
    request: VisualReferencePublicationRequestV2,
    session: Session = Depends(get_db_session),
) -> dict:
    return VisualReferencePublicationV2Service(session).publish(
        project_id,
        expected_visual_reference_set_artifact_id=(
            request.expected_visual_reference_set_artifact_id
        ),
        expected_approval_set_artifact_id=request.expected_approval_set_artifact_id,
    )
