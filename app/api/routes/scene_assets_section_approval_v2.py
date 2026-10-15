from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.scene_asset_section_approval_v2_schemas import (
    SceneAssetV2SectionApprovalRequest,
    SceneAssetV2SectionApprovalResponse,
)
from app.db.dependencies import get_db_session
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service


router = APIRouter(prefix="/api/v4", tags=["Scene Assets V2 Workspace"])


@router.post(
    "/projects/{project_id}/scene-assets/sections/{section_id}/approve-ready",
    response_model=SceneAssetV2SectionApprovalResponse,
)
def approve_scene_asset_section_ready_v2(
    project_id: str,
    section_id: str,
    request: SceneAssetV2SectionApprovalRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2SectionApprovalResponse:
    return SceneAssetV2SectionApprovalResponse.model_validate(
        SceneAssetWorkspaceV2Service(session).approve_section_ready(
            project_id,
            section_id,
            beats=[item.model_dump(mode="python") for item in request.beats],
        )
    )
