from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.script_analysis_schemas import (
    ScriptAnalysisReadResponse,
    ScriptAnalysisContinuityReviewMutationRequest,
    ScriptAnalysisContinuityReviewResponse,
)
from app.db.dependencies import get_db_session
from app.api.errors import AppError
from app.api.external_authoring_schemas import ExternalPromptDraftReadResponse, ExternalPromptPackageResponse, ExternalImportRequest, ExternalImportValidationResponse
from app.core.external_authoring import validation_from_error
from app.api.style_bible_schemas_v4 import (
    GenerateStyleBibleRequestV4,
    StyleBibleArtifactResponseV4,
    StyleBibleReadResponseV4,
    StyleBibleTextApproveRequestV4,
    StyleBibleTextUpdateRequestV4,
)
from app.modules.v4.style_bible_service import StyleBibleServiceV4
from app.modules.v4.script_analysis_service import ScriptAnalysisService
from app.modules.platform.script_analysis_continuity_review import ScriptAnalysisContinuityReviewService

from app.api.scene_breakdown_schemas_v4 import (
    ApproveSceneBreakdownRequestV4,
    SaveSceneBreakdownSectionEditRequestV4,
    SceneBreakdownSectionEditValidationResponseV4,
    ValidateSceneBreakdownSectionEditRequestV4,
    SceneBreakdownRevisionResponseV4,
    SceneBreakdownReadResponseV4,
)
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService

from app.api.visual_plan_schemas_v4 import VisualPlanReadResponse
from app.api.prompt_composer_schemas_v4 import (
    PromptBundleApproveRequest,
    PromptBundleBeatRecompileRequest,
    PromptBundleCompileRequest,
    PromptBundleManualEditRequest,
    PromptBundleProjectReadResponse,
    PromptBundleRecompileRequest,
    PromptBundleRevisionResponse,
)
from app.modules.v4.prompt_composer_service import PromptComposerService


router = APIRouter(prefix="/api/v4", tags=["V4 Script Analysis and Production Planning"])


@router.get(
    "/projects/{project_id}/script-analysis",
    response_model=ScriptAnalysisReadResponse,
)
def get_script_analysis(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ScriptAnalysisReadResponse:
    return ScriptAnalysisReadResponse.model_validate(
        ScriptAnalysisService(session).get(project_id)
    )


@router.get(
    "/projects/{project_id}/script-analysis/continuity-review",
    response_model=ScriptAnalysisContinuityReviewResponse,
)
def get_script_analysis_continuity_review(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ScriptAnalysisContinuityReviewResponse:
    return ScriptAnalysisContinuityReviewResponse.model_validate(
        ScriptAnalysisContinuityReviewService(session).get(project_id)
    )


@router.patch(
    "/projects/{project_id}/script-analysis/continuity-review",
    response_model=ScriptAnalysisContinuityReviewResponse,
)
def mutate_script_analysis_continuity_review(
    project_id: str,
    request: ScriptAnalysisContinuityReviewMutationRequest,
    session: Session = Depends(get_db_session),
) -> ScriptAnalysisContinuityReviewResponse:
    return ScriptAnalysisContinuityReviewResponse.model_validate(
        ScriptAnalysisContinuityReviewService(session).mutate(project_id, request.model_dump(mode="json"))
    )


@router.post(
    "/projects/{project_id}/style-bible/generate",
    response_model=StyleBibleArtifactResponseV4,
)
def generate_style_bible_v4(
    project_id: str,
    request: GenerateStyleBibleRequestV4,
    session: Session = Depends(get_db_session),
) -> StyleBibleArtifactResponseV4:
    artifact, artifact_id = StyleBibleServiceV4(session).generate(
        project_id, request.script_analysis_artifact_id
    )
    return StyleBibleArtifactResponseV4(artifact_id=artifact_id, artifact=artifact)


@router.get(
    "/projects/{project_id}/style-bible",
    response_model=StyleBibleReadResponseV4,
)
def get_style_bible_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> StyleBibleReadResponseV4:
    return StyleBibleReadResponseV4.model_validate(StyleBibleServiceV4(session).get(project_id))


@router.put(
    "/projects/{project_id}/style-bible/text",
    response_model=StyleBibleArtifactResponseV4,
)
def update_style_bible_text_v4(
    project_id: str,
    request: StyleBibleTextUpdateRequestV4,
    session: Session = Depends(get_db_session),
) -> StyleBibleArtifactResponseV4:
    artifact, artifact_id = StyleBibleServiceV4(session).update_text(
        project_id, request.model_dump(mode="json")
    )
    return StyleBibleArtifactResponseV4(artifact_id=artifact_id, artifact=artifact)


@router.post(
    "/projects/{project_id}/style-bible/text/approve",
    response_model=StyleBibleArtifactResponseV4,
)
def approve_style_bible_text_v4(
    project_id: str,
    request: StyleBibleTextApproveRequestV4,
    session: Session = Depends(get_db_session),
) -> StyleBibleArtifactResponseV4:
    artifact, artifact_id = StyleBibleServiceV4(session).approve_text(
        project_id, request.model_dump(mode="json")
    )
    return StyleBibleArtifactResponseV4(artifact_id=artifact_id, artifact=artifact)


@router.get(
    "/projects/{project_id}/scene-breakdown/prompt",
    response_model=ExternalPromptDraftReadResponse,
)
def get_pending_scene_breakdown_prompt_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ExternalPromptDraftReadResponse:
    return ExternalPromptDraftReadResponse.model_validate(
        ExternalSceneBreakdownService(session).get_pending_prompt(project_id)
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/prompt",
    response_model=ExternalPromptPackageResponse,
)
def build_scene_breakdown_prompt_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> ExternalPromptPackageResponse:
    return ExternalPromptPackageResponse.model_validate(
        ExternalSceneBreakdownService(session).build_prompt(project_id)
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/import/validate",
    response_model=ExternalImportValidationResponse,
)
def validate_scene_breakdown_import_v4(
    project_id: str,
    request: ExternalImportRequest,
    session: Session = Depends(get_db_session),
) -> ExternalImportValidationResponse:
    try:
        result = ExternalSceneBreakdownService(session).validate_import(
            project_id, request.model_dump(mode="json")
        )
    except AppError as exc:
        result = validation_from_error(exc)
    return ExternalImportValidationResponse.model_validate(result)


@router.post(
    "/projects/{project_id}/scene-breakdown/import",
    response_model=SceneBreakdownRevisionResponseV4,
)
def import_scene_breakdown_v4(
    project_id: str,
    request: ExternalImportRequest,
    session: Session = Depends(get_db_session),
) -> SceneBreakdownRevisionResponseV4:
    return SceneBreakdownRevisionResponseV4.model_validate(
        ExternalSceneBreakdownService(session).import_result(
            project_id, request.model_dump(mode="json")
        )
    )


@router.get(
    "/projects/{project_id}/scene-breakdown",
    response_model=SceneBreakdownReadResponseV4,
)
def get_scene_breakdown_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> SceneBreakdownReadResponseV4:
    return SceneBreakdownReadResponseV4.model_validate(
        SceneBreakdownServiceV4(session).get_current_scene_plan(project_id)
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/review/sections/{section_id}/validate",
    response_model=SceneBreakdownSectionEditValidationResponseV4,
)
def validate_scene_breakdown_section_edit_v4(
    project_id: str,
    section_id: str,
    request: ValidateSceneBreakdownSectionEditRequestV4,
    session: Session = Depends(get_db_session),
) -> SceneBreakdownSectionEditValidationResponseV4:
    return SceneBreakdownSectionEditValidationResponseV4.model_validate(
        SceneBreakdownServiceV4(session).validate_section_edit(
            project_id, section_id, request.model_dump(mode="json")
        )
    )


@router.put(
    "/projects/{project_id}/scene-breakdown/review/sections/{section_id}",
    response_model=SceneBreakdownRevisionResponseV4,
)
def save_scene_breakdown_section_edit_v4(
    project_id: str,
    section_id: str,
    request: SaveSceneBreakdownSectionEditRequestV4,
    session: Session = Depends(get_db_session),
) -> SceneBreakdownRevisionResponseV4:
    return SceneBreakdownRevisionResponseV4.model_validate(
        SceneBreakdownServiceV4(session).save_section_edit(
            project_id, section_id, request.model_dump(mode="json")
        )
    )


@router.post(
    "/projects/{project_id}/scene-breakdown/approve",
    response_model=SceneBreakdownRevisionResponseV4,
)
def approve_scene_breakdown_v4(
    project_id: str,
    request: ApproveSceneBreakdownRequestV4,
    session: Session = Depends(get_db_session),
) -> SceneBreakdownRevisionResponseV4:
    return SceneBreakdownRevisionResponseV4.model_validate(
        SceneBreakdownServiceV4(session).approve(
            project_id, request.model_dump(mode="json")
        )
    )


@router.get(
    "/projects/{project_id}/visual-plan",
    response_model=VisualPlanReadResponse,
)
def get_visual_plan_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> VisualPlanReadResponse:
    return VisualPlanReadResponse.model_validate(
        SceneBreakdownServiceV4(session).get_current_visual_plan(project_id)
    )


@router.get(
    "/projects/{project_id}/prompt-bundle",
    response_model=PromptBundleProjectReadResponse,
)
def get_prompt_bundle_v4(
    project_id: str,
    session: Session = Depends(get_db_session),
) -> PromptBundleProjectReadResponse:
    return PromptBundleProjectReadResponse.model_validate(
        PromptComposerService(session).get_current_prompt_bundle(project_id)
    )


@router.post(
    "/projects/{project_id}/prompt-bundle/compile",
    response_model=PromptBundleRevisionResponse,
)
def compile_prompt_bundle_v4(
    project_id: str,
    request: PromptBundleCompileRequest,
    session: Session = Depends(get_db_session),
) -> PromptBundleRevisionResponse:
    return PromptBundleRevisionResponse.model_validate(
        PromptComposerService(session).compile_initial(project_id, request.model_dump(mode="json"))
    )


@router.post(
    "/projects/{project_id}/prompt-bundle/recompile",
    response_model=PromptBundleRevisionResponse,
)
def recompile_prompt_bundle_v4(
    project_id: str,
    request: PromptBundleRecompileRequest,
    session: Session = Depends(get_db_session),
) -> PromptBundleRevisionResponse:
    return PromptBundleRevisionResponse.model_validate(
        PromptComposerService(session).recompile_full(project_id, request.model_dump(mode="json"))
    )


@router.post(
    "/projects/{project_id}/prompt-bundle/beats/{beat_id}/recompile",
    response_model=PromptBundleRevisionResponse,
)
def recompile_prompt_bundle_beat_v4(
    project_id: str,
    beat_id: str,
    request: PromptBundleBeatRecompileRequest,
    session: Session = Depends(get_db_session),
) -> PromptBundleRevisionResponse:
    return PromptBundleRevisionResponse.model_validate(
        PromptComposerService(session).recompile_beat(
            project_id, beat_id, request.model_dump(mode="json")
        )
    )


@router.put(
    "/projects/{project_id}/prompt-bundle/beats/{beat_id}",
    response_model=PromptBundleRevisionResponse,
)
def edit_prompt_bundle_beat_v4(
    project_id: str,
    beat_id: str,
    request: PromptBundleManualEditRequest,
    session: Session = Depends(get_db_session),
) -> PromptBundleRevisionResponse:
    return PromptBundleRevisionResponse.model_validate(
        PromptComposerService(session).edit_beat(
            project_id, beat_id, request.model_dump(mode="json")
        )
    )


@router.post(
    "/projects/{project_id}/prompt-bundle/approve",
    response_model=PromptBundleRevisionResponse,
)
def approve_prompt_bundle_v4(
    project_id: str,
    request: PromptBundleApproveRequest,
    session: Session = Depends(get_db_session),
) -> PromptBundleRevisionResponse:
    return PromptBundleRevisionResponse.model_validate(
        PromptComposerService(session).approve(project_id, request.model_dump(mode="json"))
    )
