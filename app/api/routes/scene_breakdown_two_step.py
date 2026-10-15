from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.external_authoring_schemas import (
    ExternalImportRequest,
    ExternalImportValidationResponse,
    ExternalPromptDraftReadResponse,
    ExternalPromptPackageResponse,
)
from app.api.scene_breakdown_schemas_v4 import SceneBreakdownRevisionResponseV4
from app.api.scene_breakdown_two_step_schemas_v4 import ScenePlanCandidateResponseV4
from app.core.external_authoring import validation_from_error
from app.db.dependencies import get_db_session
from app.modules.v4.scene_breakdown_two_step_external_service import ExternalSceneBreakdownTwoStepService


router = APIRouter(prefix="/api/v4", tags=["V4 Scene Breakdown Two-Step Authoring"])


@router.get(
    "/projects/{project_id}/scene-breakdown/scene/prompt",
    response_model=ExternalPromptDraftReadResponse,
)
def get_pending_scene_plan_prompt_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ExternalPromptDraftReadResponse:
    return ExternalPromptDraftReadResponse.model_validate(
        ExternalSceneBreakdownTwoStepService(session).get_pending_scene_prompt(project_id)
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/scene/prompt",
    response_model=ExternalPromptPackageResponse,
)
def build_scene_plan_prompt_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ExternalPromptPackageResponse:
    return ExternalPromptPackageResponse.model_validate(
        ExternalSceneBreakdownTwoStepService(session).build_scene_prompt(project_id)
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/scene/import/validate",
    response_model=ExternalImportValidationResponse,
)
def validate_scene_plan_import_v4(
    project_id: str,
    request: ExternalImportRequest,
    session: Session = Depends(get_db_session),
) -> ExternalImportValidationResponse:
    try:
        result = ExternalSceneBreakdownTwoStepService(session).validate_scene_import(
            project_id, request.model_dump(mode="json")
        )
    except AppError as exc:
        result = validation_from_error(exc)
    return ExternalImportValidationResponse.model_validate(result)


@router.post(
    "/projects/{project_id}/scene-breakdown/scene/import",
    response_model=ScenePlanCandidateResponseV4,
)
def import_scene_plan_candidate_v4(
    project_id: str,
    request: ExternalImportRequest,
    session: Session = Depends(get_db_session),
) -> ScenePlanCandidateResponseV4:
    return ScenePlanCandidateResponseV4.model_validate(
        ExternalSceneBreakdownTwoStepService(session).import_scene_result(
            project_id, request.model_dump(mode="json")
        )
    )


@router.get(
    "/projects/{project_id}/scene-breakdown/visual/prompt",
    response_model=ExternalPromptDraftReadResponse,
)
def get_pending_visual_plan_prompt_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ExternalPromptDraftReadResponse:
    return ExternalPromptDraftReadResponse.model_validate(
        ExternalSceneBreakdownTwoStepService(session).get_pending_visual_prompt(project_id)
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/visual/prompt",
    response_model=ExternalPromptPackageResponse,
)
def build_visual_plan_prompt_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ExternalPromptPackageResponse:
    return ExternalPromptPackageResponse.model_validate(
        ExternalSceneBreakdownTwoStepService(session).build_visual_prompt(project_id)
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/visual/import/validate",
    response_model=ExternalImportValidationResponse,
)
def validate_visual_plan_import_v4(
    project_id: str,
    request: ExternalImportRequest,
    session: Session = Depends(get_db_session),
) -> ExternalImportValidationResponse:
    try:
        result = ExternalSceneBreakdownTwoStepService(session).validate_visual_import(
            project_id, request.model_dump(mode="json")
        )
    except AppError as exc:
        result = validation_from_error(exc)
    return ExternalImportValidationResponse.model_validate(result)


@router.post(
    "/projects/{project_id}/scene-breakdown/visual/import",
    response_model=SceneBreakdownRevisionResponseV4,
)
def import_visual_plan_v4(
    project_id: str,
    request: ExternalImportRequest,
    session: Session = Depends(get_db_session),
) -> SceneBreakdownRevisionResponseV4:
    return SceneBreakdownRevisionResponseV4.model_validate(
        ExternalSceneBreakdownTwoStepService(session).import_visual_result(
            project_id, request.model_dump(mode="json")
        )
    )
