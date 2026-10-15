from __future__ import annotations
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.api.errors import AppError
from app.api.external_authoring_schemas import ExternalPromptDraftReadResponse, ExternalPromptPackageResponse, ExternalImportRequest, ExternalImportValidationResponse, ScriptAnalysisCombinedRevisionResponse
from app.core.external_authoring import validation_from_error
from app.api.schemas import ChannelDnaReadResponse, ChannelDnaValidateRequest, ChannelDnaValidateResponse, ChannelListResponse, ChannelModel, ChannelVoiceProfilePutRequest, ChannelVoiceProfileReadResponse, CreateChannelRequest, CreateProjectRequest, GenerateProjectScriptRequest, GenerateProjectTopicIdeasRequest, MediaImageGenerateRequest, MediaImageGenerateResponse, MediaActiveBatchesResponse, MediaTaskCardListResponse, MediaTaskResultResponse, MediaTaskStatusResponse, MediaTaskSummaryResponse, ProjectListResponse, ProjectModel, SelectTopicRequest, ScriptArtifactResponse, ScriptReadResponse, TopicIdeasArtifactResponse, TopicIdeasReadResponse
from app.db.dependencies import get_db_session
from app.modules.media import MediaTaskService
from app.modules.platform.services import AuthoringService, ChannelService, ProjectService
from app.modules.platform.external_script_analysis import ExternalScriptAnalysisService
router = APIRouter(prefix='/api/v3', tags=['Platform'])

def to_channel_model(channel) -> ChannelModel:
    return ChannelModel(channel_id=channel.channel_id, name=channel.name, language=channel.language, niche=channel.niche, audience=channel.audience, has_channel_dna=bool(channel.channel_dna_json), channel_context=channel.channel_context_json, created_at=channel.created_at, updated_at=channel.updated_at)

def to_project_model(project) -> ProjectModel:
    return ProjectModel(project_id=project.project_id, channel_id=project.channel_id, name=project.name, description=project.description, channel_context=project.channel_context_json, channel_dna_narration_wpm=project.channel_dna_json["narration_wpm"], topic_ideas_artifact_id=project.topic_ideas_artifact_id, selected_topic_ideas_artifact_id=project.selected_topic_ideas_artifact_id, selected_topic_idea_id=project.selected_topic_idea_id, script_artifact_id=project.script_artifact_id, script_analysis_artifact_id=project.script_analysis_artifact_id, style_bible_artifact_id=project.style_bible_artifact_id, scene_plan_artifact_id=project.scene_plan_artifact_id, visual_plan_artifact_id=project.visual_plan_artifact_id, prompt_bundle_artifact_id=project.prompt_bundle_artifact_id, style_bible_visual_reference_set_artifact_id=project.style_bible_visual_reference_set_artifact_id, style_bible_visual_reference_approval_set_artifact_id=project.style_bible_visual_reference_approval_set_artifact_id, scene_asset_manifest_artifact_id=project.scene_asset_manifest_artifact_id, narration_audio_artifact_id=project.narration_audio_artifact_id, narration_word_timing_map_artifact_id=project.narration_word_timing_map_artifact_id, beat_timing_map_artifact_id=project.beat_timing_map_artifact_id, assembly_plan_artifact_id=project.assembly_plan_artifact_id, subtitle_srt_artifact_id=project.subtitle_srt_artifact_id, subtitle_vtt_artifact_id=project.subtitle_vtt_artifact_id, final_video_artifact_id=project.final_video_artifact_id, created_at=project.created_at, updated_at=project.updated_at)

@router.get('/channels', response_model=ChannelListResponse)
def list_channels(page: int=Query(default=1, ge=1), page_size: int=Query(default=50, ge=1, le=100), session: Session=Depends(get_db_session)) -> ChannelListResponse:
    items, total = ChannelService(session).list_channel_cards(page=page, page_size=page_size)
    return ChannelListResponse(items=[ChannelModel.model_validate(item) for item in items], page=page, page_size=page_size, total=total)

@router.post('/channels', response_model=ChannelModel)
def create_channel(request: CreateChannelRequest, session: Session=Depends(get_db_session)) -> ChannelModel:
    channel = ChannelService(session).create_channel(request.model_dump())
    return to_channel_model(channel)

@router.get('/channels/{channel_id}', response_model=ChannelModel)
def get_channel(channel_id: str, session: Session=Depends(get_db_session)) -> ChannelModel:
    return ChannelModel.model_validate(ChannelService(session).get_channel_card(channel_id))

@router.get('/channels/{channel_id}/channel-dna', response_model=ChannelDnaReadResponse)
def get_channel_dna(channel_id: str, session: Session=Depends(get_db_session)) -> ChannelDnaReadResponse:
    channel_dna, updated_at = ChannelService(session).get_channel_dna(channel_id)
    return ChannelDnaReadResponse(channel_id=channel_id, has_channel_dna=channel_dna is not None, channel_dna=channel_dna, updated_at=updated_at)

@router.post('/channel-dna/validate', response_model=ChannelDnaValidateResponse)
def validate_channel_dna(request: ChannelDnaValidateRequest, session: Session=Depends(get_db_session)) -> ChannelDnaValidateResponse:
    channel_dna, channel_context = ChannelService(session).validate_channel_dna(request.channel_dna)
    return ChannelDnaValidateResponse(valid=True, channel_dna=channel_dna, channel_context=channel_context)

@router.post('/channels/{channel_id}/channel-dna', response_model=ChannelModel)
def save_channel_dna(channel_id: str, request: ChannelDnaValidateRequest, session: Session=Depends(get_db_session)) -> ChannelModel:
    return to_channel_model(ChannelService(session).save_channel_dna(channel_id, request.channel_dna))

@router.get('/channels/{channel_id}/voice-profile', response_model=ChannelVoiceProfileReadResponse)
def get_channel_voice_profile(channel_id: str, session: Session=Depends(get_db_session)) -> ChannelVoiceProfileReadResponse:
    voice_profile, updated_at = ChannelService(session).get_voice_profile(channel_id)
    return ChannelVoiceProfileReadResponse(channel_id=channel_id, voice_profile=voice_profile, updated_at=updated_at)

@router.put('/channels/{channel_id}/voice-profile', response_model=ChannelVoiceProfileReadResponse)
def put_channel_voice_profile(channel_id: str, request: ChannelVoiceProfilePutRequest, session: Session=Depends(get_db_session)) -> ChannelVoiceProfileReadResponse:
    channel = ChannelService(session).save_voice_profile(channel_id, request.voice_profile.model_dump(mode='json') if request.voice_profile is not None else None)
    return ChannelVoiceProfileReadResponse(channel_id=channel_id, voice_profile=channel.voice_profile_json, updated_at=channel.updated_at if channel.voice_profile_json is not None else None)

@router.get('/projects', response_model=ProjectListResponse)
def list_projects(channel_id: str | None=None, page: int=Query(default=1, ge=1), page_size: int=Query(default=50, ge=1, le=100), session: Session=Depends(get_db_session)) -> ProjectListResponse:
    items, total = ProjectService(session).list_projects(page=page, page_size=page_size, channel_id=channel_id)
    return ProjectListResponse(items=[to_project_model(item) for item in items], page=page, page_size=page_size, total=total)

@router.post('/projects', response_model=ProjectModel)
def create_project(request: CreateProjectRequest, session: Session=Depends(get_db_session)) -> ProjectModel:
    project = ProjectService(session).create_project(channel_id=request.channel_id, name=request.name, description=request.description)
    return to_project_model(project)

@router.get('/projects/{project_id}', response_model=ProjectModel)
def get_project(project_id: str, session: Session=Depends(get_db_session)) -> ProjectModel:
    return to_project_model(ProjectService(session).get_required(project_id))

@router.get('/projects/{project_id}/topic-ideas', response_model=TopicIdeasReadResponse)
def get_project_topic_ideas(project_id: str, session: Session=Depends(get_db_session)) -> TopicIdeasReadResponse:
    return TopicIdeasReadResponse.model_validate(AuthoringService(session).get_topic_ideas(project_id))

@router.get('/projects/{project_id}/topic-ideas/prompt', response_model=ExternalPromptDraftReadResponse)
def get_pending_topic_ideas_prompt(project_id: str, session: Session=Depends(get_db_session)) -> ExternalPromptDraftReadResponse:
    return ExternalPromptDraftReadResponse.model_validate(AuthoringService(session).get_pending_topic_ideas_prompt(project_id))


@router.post('/projects/{project_id}/topic-ideas/prompt', response_model=ExternalPromptPackageResponse)
def build_topic_ideas_prompt(project_id: str, request: GenerateProjectTopicIdeasRequest, session: Session=Depends(get_db_session)) -> ExternalPromptPackageResponse:
    package = AuthoringService(session).build_topic_ideas_prompt(project_id, request.user_input.model_dump(mode='json'))
    return ExternalPromptPackageResponse.model_validate(package)


@router.post('/projects/{project_id}/topic-ideas/import/validate', response_model=ExternalImportValidationResponse)
def validate_topic_ideas_import(project_id: str, request: ExternalImportRequest, session: Session=Depends(get_db_session)) -> ExternalImportValidationResponse:
    try:
        result = AuthoringService(session).validate_topic_ideas_import(project_id, request.model_dump(mode='json'))
    except AppError as exc:
        result = validation_from_error(exc)
    return ExternalImportValidationResponse.model_validate(result)


@router.post('/projects/{project_id}/topic-ideas/import', response_model=TopicIdeasArtifactResponse)
def import_topic_ideas(project_id: str, request: ExternalImportRequest, session: Session=Depends(get_db_session)) -> TopicIdeasArtifactResponse:
    artifact, artifact_id = AuthoringService(session).import_topic_ideas(project_id, request.model_dump(mode='json'))
    return TopicIdeasArtifactResponse(artifact=artifact, artifact_id=artifact_id)

@router.put('/projects/{project_id}/selected-topic', response_model=ProjectModel)
def select_topic(project_id: str, request: SelectTopicRequest, session: Session=Depends(get_db_session)) -> ProjectModel:
    project = AuthoringService(session).select_topic(project_id, request.model_dump(mode='json'))
    return to_project_model(project)

@router.get('/projects/{project_id}/script', response_model=ScriptReadResponse)
def get_project_script(project_id: str, session: Session=Depends(get_db_session)) -> ScriptReadResponse:
    return ScriptReadResponse.model_validate(AuthoringService(session).get_script(project_id))

@router.get('/projects/{project_id}/script-analysis/prompt', response_model=ExternalPromptDraftReadResponse)
def get_pending_script_analysis_prompt(project_id: str, session: Session=Depends(get_db_session)) -> ExternalPromptDraftReadResponse:
    return ExternalPromptDraftReadResponse.model_validate(ExternalScriptAnalysisService(session).get_pending_prompt(project_id))


@router.post('/projects/{project_id}/script-analysis/prompt', response_model=ExternalPromptPackageResponse)
def build_script_analysis_prompt(project_id: str, request: GenerateProjectScriptRequest, session: Session=Depends(get_db_session)) -> ExternalPromptPackageResponse:
    package = ExternalScriptAnalysisService(session).build_prompt(project_id, request.model_dump(mode='json', exclude_none=True))
    return ExternalPromptPackageResponse.model_validate(package)


@router.post('/projects/{project_id}/script-analysis/import/validate', response_model=ExternalImportValidationResponse)
def validate_script_analysis_import(project_id: str, request: ExternalImportRequest, session: Session=Depends(get_db_session)) -> ExternalImportValidationResponse:
    try:
        result = ExternalScriptAnalysisService(session).validate_import(project_id, request.model_dump(mode='json'))
    except AppError as exc:
        result = validation_from_error(exc)
    return ExternalImportValidationResponse.model_validate(result)


@router.post('/projects/{project_id}/script-analysis/import', response_model=ScriptAnalysisCombinedRevisionResponse)
def import_script_analysis(project_id: str, request: ExternalImportRequest, session: Session=Depends(get_db_session)) -> ScriptAnalysisCombinedRevisionResponse:
    result = ExternalScriptAnalysisService(session).import_result(project_id, request.model_dump(mode='json'))
    return ScriptAnalysisCombinedRevisionResponse.model_validate(result)

@router.post('/media/images/generate', response_model=MediaImageGenerateResponse)
def generate_media_images(request: MediaImageGenerateRequest, session: Session=Depends(get_db_session)) -> MediaImageGenerateResponse:
    return MediaImageGenerateResponse(**MediaTaskService(session).submit_image_generation(request.model_dump(mode='json')))

@router.get('/media/batches/active', response_model=MediaActiveBatchesResponse)
def get_active_media_batches(project_id: str=Query(..., min_length=1), session: Session=Depends(get_db_session)) -> MediaActiveBatchesResponse:
    return MediaActiveBatchesResponse(**MediaTaskService(session).get_active_batches(project_id=project_id))

@router.get('/media/tasks', response_model=MediaTaskCardListResponse)
def list_media_tasks(response: Response, batch_id: str | None=Query(default=None), status: str | None=Query(default=None), view: str=Query(default='cards'), offset: int=Query(default=0, ge=0), limit: int=Query(default=1000, ge=1, le=1000), session: Session=Depends(get_db_session)) -> MediaTaskCardListResponse:
    if view != 'cards':
        raise AppError('MEDIA_TASK_VIEW_UNSUPPORTED', "Only the 'cards' view is supported in V1.", 422, retryable=False)
    service = MediaTaskService(session)
    payload = service.list_tasks(batch_id=batch_id, status=status, offset=offset, limit=limit)
    total = service.count_tasks(batch_id=batch_id, status=status)
    response.headers['X-Total-Count'] = str(total)
    response.headers['X-Page-Offset'] = str(offset)
    response.headers['X-Page-Limit'] = str(limit)
    response.headers['X-Has-More'] = str(offset + len(payload['tasks']) < total).lower()
    return MediaTaskCardListResponse(**payload)

@router.get('/media/tasks/summary', response_model=MediaTaskSummaryResponse)
def get_media_task_summary(batch_id: str=Query(..., min_length=1), session: Session=Depends(get_db_session)) -> MediaTaskSummaryResponse:
    return MediaTaskSummaryResponse(**MediaTaskService(session).get_summary(batch_id=batch_id))

@router.get('/media/tasks/{media_task_id}/status', response_model=MediaTaskStatusResponse)
def get_media_task_status(media_task_id: str, session: Session=Depends(get_db_session)) -> MediaTaskStatusResponse:
    return MediaTaskStatusResponse(**MediaTaskService(session).get_task_status(media_task_id))

@router.get('/media/tasks/{media_task_id}/result', response_model=MediaTaskResultResponse, response_model_exclude_none=True)
def get_media_task_result(media_task_id: str, session: Session=Depends(get_db_session)) -> MediaTaskResultResponse:
    return MediaTaskResultResponse(**MediaTaskService(session).get_task_result(media_task_id))

@router.get('/media/tasks/{media_task_id}/file')
def get_media_task_file(media_task_id: str, result_index: int=Query(default=1, ge=1), session: Session=Depends(get_db_session)) -> FileResponse:
    service = MediaTaskService(session)
    path = service.resolve_task_file_path(media_task_id, result_index=result_index)
    media_type = service.resolve_task_file_mime_type(media_task_id, result_index=result_index)
    return FileResponse(path=path, media_type=media_type, filename=path.name)
