from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.assembly_schemas_v4 import AssemblyApproveRequest, AssemblyBuildRequest, AssemblyWorkspaceResponse
from app.api.render_schemas_v4 import RenderJobRequest, RenderJobStatusResponse, RenderJobSubmissionResponse, RenderWorkspaceResponse
from app.api.voice_schemas_v4 import (
    VoiceApproveRequest,
    VoiceGenerateRequest,
    VoiceJobStatusResponse,
    VoiceJobSubmissionResponse,
    VoiceRealignCurrentRequest,
    VoiceRealignCurrentResponse,
    VoiceSectionRegenerateRequest,
    VoiceWorkspaceResponse,
)
from app.db.dependencies import get_db_session
from app.db.session import get_session_factory
from app.modules.v4.assembly_service import AssemblyService
from app.modules.v4.durable_render_service import DurableRenderService as RenderService
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.voice_current_realignment import VoiceCurrentRealignmentService
from app.modules.v4.voice_section_audio import VoiceSectionAudioService
from app.modules.v4.voice_task3 import VoiceTask3AlignmentService


router = APIRouter(prefix="/api/v4", tags=["Production V4"])

# Scene Assets intentionally do not live in this router anymore.  Their V2
# runtime/workspace routers are the sole HTTP owners after the Phase E cutover.


@router.get('/projects/{project_id}/voice', response_model=VoiceWorkspaceResponse)
def get_voice_workspace(project_id: str, session: Session=Depends(get_db_session)) -> VoiceWorkspaceResponse:
    return VoiceWorkspaceResponse.model_validate(DurableVoiceService(session).get_workspace(project_id))


@router.post('/projects/{project_id}/voice/jobs', response_model=VoiceJobSubmissionResponse, status_code=202)
def submit_voice_job(project_id: str, request: VoiceGenerateRequest, session: Session=Depends(get_db_session)) -> VoiceJobSubmissionResponse:
    return VoiceJobSubmissionResponse.model_validate(DurableVoiceService(session).submit_generation(project_id, request.model_dump(mode='json')))


@router.get('/projects/{project_id}/voice/jobs/{job_id}', response_model=VoiceJobStatusResponse)
def get_voice_job(project_id: str, job_id: str, session: Session=Depends(get_db_session)) -> VoiceJobStatusResponse:
    return VoiceJobStatusResponse.model_validate(DurableVoiceService(session).job_status(project_id, job_id))


@router.post('/projects/{project_id}/voice/jobs/{job_id}/cancel', response_model=VoiceJobStatusResponse)
def cancel_voice_job(project_id: str, job_id: str, session: Session=Depends(get_db_session)) -> VoiceJobStatusResponse:
    return VoiceJobStatusResponse.model_validate(DurableVoiceService(session).cancel_job(project_id, job_id))


@router.post('/projects/{project_id}/voice/jobs/{job_id}/alignment/retry')
def retry_voice_alignment(project_id: str, job_id: str) -> dict:
    count = VoiceTask3AlignmentService(get_session_factory()).retry_unknown(project_id=project_id, job_id=job_id)
    return {"status": "queued", "retried_section_count": count}


@router.post(
    '/projects/{project_id}/voice/realign-current',
    response_model=VoiceRealignCurrentResponse,
)
def realign_current_voice(
    project_id: str,
    request: VoiceRealignCurrentRequest,
) -> VoiceRealignCurrentResponse:
    result = VoiceCurrentRealignmentService(get_session_factory()).realign_current(
        project_id=project_id,
        expected_narration_audio_artifact_id=request.expected_narration_audio_artifact_id,
        expected_narration_word_timing_map_artifact_id=request.expected_narration_word_timing_map_artifact_id,
    )
    return VoiceRealignCurrentResponse.model_validate(result)


@router.post('/projects/{project_id}/voice/sections/{section_id}/regenerate/jobs', response_model=VoiceJobSubmissionResponse, status_code=202)
def regenerate_voice_section(project_id: str, section_id: str, request: VoiceSectionRegenerateRequest, session: Session=Depends(get_db_session)) -> VoiceJobSubmissionResponse:
    return VoiceJobSubmissionResponse.model_validate(DurableVoiceService(session).submit_generation(project_id, request.model_dump(mode='json'), target_section_id=section_id))


@router.post('/projects/{project_id}/voice/approve')
def approve_voice(project_id: str, request: VoiceApproveRequest, session: Session=Depends(get_db_session)) -> dict:
    return DurableVoiceService(session).approve(project_id, request.model_dump(mode='json'))


@router.get('/projects/{project_id}/voice/audio')
def get_voice_audio(project_id: str, artifact_id: str | None = None, session: Session=Depends(get_db_session)) -> FileResponse:
    service = DurableVoiceService(session)
    artifact = service.resolve_review_audio_artifact(project_id, artifact_id)
    return FileResponse(path=service.artifacts.resolve_file_path(artifact), media_type=artifact.mime_type or 'audio/wav', filename='narration.wav', headers={'ETag': f'"{artifact.sha256}"', 'Accept-Ranges': 'bytes', 'Content-Length': str(artifact.size_bytes or 0)})


@router.get('/projects/{project_id}/voice/sections/{section_id}/audio')
def get_voice_section_audio(project_id: str, section_id: str, session: Session=Depends(get_db_session)) -> Response:
    rendered = VoiceSectionAudioService(session).render(project_id, section_id)
    return Response(
        content=rendered.data,
        media_type='audio/wav',
        headers={
            'ETag': f'"{rendered.info.sha256}"',
            'Cache-Control': 'no-store, max-age=0',
            'Content-Length': str(rendered.info.size_bytes),
            'Content-Disposition': f'inline; filename="{section_id}.wav"',
        },
    )


@router.get('/projects/{project_id}/voice/segments/{segment_id}/audio')
def get_voice_segment_audio(project_id: str, segment_id: str, session: Session=Depends(get_db_session)) -> FileResponse:
    service = DurableVoiceService(session)
    artifact = service.resolve_audio_artifact(project_id, segment_id=segment_id)
    return FileResponse(path=service.artifacts.resolve_file_path(artifact), media_type=artifact.mime_type or 'audio/wav', filename=f'{segment_id}.wav', headers={'ETag': f'"{artifact.sha256}"', 'Accept-Ranges': 'bytes', 'Content-Length': str(artifact.size_bytes or 0)})


@router.get('/projects/{project_id}/assembly', response_model=AssemblyWorkspaceResponse)
def get_assembly_workspace(project_id: str, session: Session=Depends(get_db_session)) -> AssemblyWorkspaceResponse:
    return AssemblyWorkspaceResponse.model_validate(AssemblyService(session).get_workspace(project_id))


@router.post('/projects/{project_id}/assembly/build', response_model=AssemblyWorkspaceResponse)
def build_assembly(project_id: str, request: AssemblyBuildRequest, session: Session=Depends(get_db_session)) -> AssemblyWorkspaceResponse:
    return AssemblyWorkspaceResponse.model_validate(AssemblyService(session).build(project_id, request.model_dump(mode='json')))


@router.post('/projects/{project_id}/assembly/approve', response_model=AssemblyWorkspaceResponse)
def approve_assembly(project_id: str, request: AssemblyApproveRequest, session: Session=Depends(get_db_session)) -> AssemblyWorkspaceResponse:
    return AssemblyWorkspaceResponse.model_validate(AssemblyService(session).approve(project_id, request.model_dump(mode='json')))


@router.get('/projects/{project_id}/assembly/subtitles.srt')
def get_assembly_srt(project_id: str, session: Session=Depends(get_db_session)) -> FileResponse:
    service = AssemblyService(session)
    artifact = service.resolve_subtitle(project_id, 'srt')
    return FileResponse(path=service.artifacts.resolve_file_path(artifact), media_type='application/x-subrip', filename='subtitles.srt', headers={'ETag': f'"{artifact.sha256}"', 'Content-Length': str(artifact.size_bytes or 0)})


@router.get('/projects/{project_id}/assembly/subtitles.vtt')
def get_assembly_vtt(project_id: str, session: Session=Depends(get_db_session)) -> FileResponse:
    service = AssemblyService(session)
    artifact = service.resolve_subtitle(project_id, 'vtt')
    return FileResponse(path=service.artifacts.resolve_file_path(artifact), media_type='text/vtt', filename='subtitles.vtt', headers={'ETag': f'"{artifact.sha256}"', 'Content-Length': str(artifact.size_bytes or 0)})


@router.get('/projects/{project_id}/render', response_model=RenderWorkspaceResponse)
def get_render_workspace(project_id: str, session: Session=Depends(get_db_session)) -> RenderWorkspaceResponse:
    return RenderWorkspaceResponse.model_validate(RenderService(session).get_workspace(project_id))


@router.post('/projects/{project_id}/render/jobs', response_model=RenderJobSubmissionResponse, status_code=202)
def submit_render_job(project_id: str, request: RenderJobRequest, session: Session=Depends(get_db_session)) -> RenderJobSubmissionResponse:
    return RenderJobSubmissionResponse.model_validate(RenderService(session).submit(project_id, request.model_dump(mode='json')))


@router.get('/projects/{project_id}/render/jobs/{job_id}', response_model=RenderJobStatusResponse)
def get_render_job(project_id: str, job_id: str, session: Session=Depends(get_db_session)) -> RenderJobStatusResponse:
    return RenderJobStatusResponse.model_validate(RenderService(session).get_status(project_id, job_id))


@router.post('/projects/{project_id}/render/jobs/{job_id}/cancel', response_model=RenderJobStatusResponse)
def cancel_render_job(project_id: str, job_id: str, session: Session=Depends(get_db_session)) -> RenderJobStatusResponse:
    return RenderJobStatusResponse.model_validate(RenderService(session).cancel(project_id, job_id))


@router.post('/projects/{project_id}/render/jobs/{job_id}/retry', response_model=RenderJobSubmissionResponse, status_code=202)
def retry_render_job(project_id: str, job_id: str, session: Session=Depends(get_db_session)) -> RenderJobSubmissionResponse:
    return RenderJobSubmissionResponse.model_validate(RenderService(session).retry(project_id, job_id))


def _parse_single_byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if not value.startswith('bytes=') or ',' in value:
        raise ValueError('unsupported range')
    spec = value[6:].strip()
    if '-' not in spec:
        raise ValueError('invalid range')
    left, right = spec.split('-', 1)
    if not left:
        suffix = int(right)
        if suffix <= 0:
            raise ValueError('invalid suffix')
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(left)
        end = int(right) if right else size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError('unsatisfiable range')
    return (start, min(end, size - 1))


def _stream_file_range(path, start: int, end: int, chunk_size: int=1024 * 1024) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open('rb') as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get('/projects/{project_id}/render/video')
def get_render_video(project_id: str, range_header: str | None=Header(default=None, alias='Range'), session: Session=Depends(get_db_session)):
    artifact, path = RenderService(session).current_video(project_id)
    size = int(artifact.size_bytes or path.stat().st_size)
    common = {'Accept-Ranges': 'bytes', 'ETag': f'"{artifact.sha256}"', 'Content-Type': 'video/mp4', 'Content-Disposition': 'inline; filename="final_video.mp4"'}
    try:
        selected = _parse_single_byte_range(range_header, size)
    except (ValueError, TypeError):
        return Response(status_code=416, headers={**common, 'Content-Range': f'bytes */{size}', 'Content-Length': '0'})
    if selected is None:
        return StreamingResponse(_stream_file_range(path, 0, size - 1), status_code=200, media_type='video/mp4', headers={**common, 'Content-Length': str(size)})
    start, end = selected
    return StreamingResponse(_stream_file_range(path, start, end), status_code=206, media_type='video/mp4', headers={**common, 'Content-Range': f'bytes {start}-{end}/{size}', 'Content-Length': str(end - start + 1)})
