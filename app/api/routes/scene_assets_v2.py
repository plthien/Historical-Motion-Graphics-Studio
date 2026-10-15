from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from app.api.scene_asset_runtime_schemas_v4 import (
    SceneAssetV2BeatSubmitRequest,
    SceneAssetV2CancelResponse,
    SceneAssetV2ExecutionStatusResponse,
    SceneAssetV2OperationStatusResponse,
    SceneAssetV2OperationSubmissionResponse,
    SceneAssetV2OperationSubmitRequest,
)
from app.db.dependencies import get_db_session
from app.modules.v4.scene_asset_execution_projection import SceneAssetExecutionProjection
from app.modules.v4.scene_asset_provider_execution import SceneAssetProviderExecutionService
from app.modules.v4.scene_asset_runtime_v2 import SceneAssetRuntimeV2


router = APIRouter(prefix="/api/v4", tags=["Scene Assets V2 Runtime"])


@router.post(
    "/projects/{project_id}/scene-assets/jobs",
    response_model=SceneAssetV2OperationSubmissionResponse,
    status_code=202,
)
def submit_scene_asset_operation_v2(
    project_id: str,
    request: SceneAssetV2OperationSubmitRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2OperationSubmissionResponse:
    return SceneAssetV2OperationSubmissionResponse.model_validate(
        SceneAssetRuntimeV2(session).submit_generation(project_id, request.model_dump(mode="json"))
    )


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/generate/jobs",
    response_model=SceneAssetV2OperationSubmissionResponse,
    status_code=202,
)
def generate_scene_asset_beat_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2BeatSubmitRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2OperationSubmissionResponse:
    return SceneAssetV2OperationSubmissionResponse.model_validate(
        SceneAssetRuntimeV2(session).submit_generation(
            project_id,
            request.model_dump(mode="json"),
            target_beat_id=beat_id,
            target_mode="generate_beat",
        )
    )


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/retry/jobs",
    response_model=SceneAssetV2OperationSubmissionResponse,
    status_code=202,
)
def retry_scene_asset_beat_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2BeatSubmitRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2OperationSubmissionResponse:
    return SceneAssetV2OperationSubmissionResponse.model_validate(
        SceneAssetRuntimeV2(session).submit_generation(
            project_id,
            request.model_dump(mode="json"),
            target_beat_id=beat_id,
            target_mode="retry_beat",
        )
    )


@router.post(
    "/projects/{project_id}/scene-assets/beats/{beat_id}/regenerate/jobs",
    response_model=SceneAssetV2OperationSubmissionResponse,
    status_code=202,
)
def regenerate_scene_asset_beat_v2(
    project_id: str,
    beat_id: str,
    request: SceneAssetV2BeatSubmitRequest,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2OperationSubmissionResponse:
    return SceneAssetV2OperationSubmissionResponse.model_validate(
        SceneAssetRuntimeV2(session).submit_generation(
            project_id,
            request.model_dump(mode="json"),
            target_beat_id=beat_id,
            target_mode="regenerate_beat",
        )
    )


@router.get(
    "/projects/{project_id}/scene-assets/jobs/{operation_id}",
    response_model=SceneAssetV2OperationStatusResponse,
)
def get_scene_asset_operation_v2(
    project_id: str,
    operation_id: str,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2OperationStatusResponse:
    return SceneAssetV2OperationStatusResponse.model_validate(
        SceneAssetExecutionProjection(session).get_operation_status(project_id, operation_id)
    )


@router.post(
    "/projects/{project_id}/scene-assets/jobs/{operation_id}/cancel",
    response_model=SceneAssetV2CancelResponse,
)
def cancel_scene_asset_operation_v2(
    project_id: str,
    operation_id: str,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2CancelResponse:
    result = SceneAssetRuntimeV2(session).cancel_operation(project_id, operation_id)
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False, expire_on_commit=False)
    SceneAssetProviderExecutionService(factory).release_terminal_v2_provider_capacity()
    return SceneAssetV2CancelResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/scene-assets/execution-status",
    response_model=SceneAssetV2ExecutionStatusResponse,
)
def get_scene_asset_execution_status_v2(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> SceneAssetV2ExecutionStatusResponse:
    return SceneAssetV2ExecutionStatusResponse.model_validate(
        SceneAssetExecutionProjection(session).get_execution_status(project_id)
    )
