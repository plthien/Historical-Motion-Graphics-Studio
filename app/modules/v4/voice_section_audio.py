from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import get_settings
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.voice_audio import WavInfo, assemble_canonical_wav, validate_canonical_wav_bytes


@dataclass(frozen=True, slots=True)
class VoiceSectionAudio:
    data: bytes
    info: WavInfo


class VoiceSectionAudioService:
    """Read-only Task-2 section playback over immutable Voice artifacts."""

    def __init__(self, session: Session) -> None:
        self.voice = DurableVoiceService(session)

    def render(self, project_id: str, section_id: str) -> VoiceSectionAudio:
        project = self.voice._project_required(project_id)
        remediation = self.voice._latest_pacing_remediation_report(project)
        if remediation is not None:
            source_operation_id = remediation.get("source_operation_id")
            if not isinstance(source_operation_id, str) or not source_operation_id:
                raise AppError(
                    "VOICE_SECTION_AUDIO_INVALID",
                    "Voice section remediation audio lineage is unavailable.",
                    409,
                    retryable=False,
                )
            operation = self.voice._operation_required(source_operation_id)
            segmentation = operation.request_json.get("segmentation") if isinstance(operation.request_json, dict) else None
            segments = segmentation.get("segments") if isinstance(segmentation, dict) else None
            if not isinstance(segments, list):
                raise AppError("VOICE_SECTION_AUDIO_INVALID", "Voice section segmentation is unavailable.", 409, retryable=False)
            ordered_bytes, _ = self.voice._ordered_segment_outputs(operation)
            selected = [
                data
                for segment, data in zip(segments, ordered_bytes, strict=True)
                if isinstance(segment, dict) and segment.get("section_id") == section_id
            ]
            return self._assemble(section_id, selected)

        operation = next(
            (
                item
                for item in self.voice.operations.list_for_project(project_id)
                if item.status == "completed"
                and item.script_artifact_id == project.script_artifact_id
                and item.expected_narration_audio_artifact_id == project.narration_audio_artifact_id
                and item.expected_narration_word_timing_map_artifact_id == project.narration_word_timing_map_artifact_id
            ),
            None,
        )
        if operation is None or not isinstance(operation.result_json, dict):
            raise AppError("VOICE_SECTION_NOT_FOUND", "Voice section audio was not found.", 404, retryable=False)
        final_sections = operation.result_json.get("final_sections")
        matches = [
            item for item in final_sections
            if isinstance(item, dict) and item.get("section_id") == section_id
        ] if isinstance(final_sections, list) else []
        if len(matches) != 1:
            raise AppError("VOICE_SECTION_NOT_FOUND", "Voice section audio was not found.", 404, retryable=False)
        entry = matches[0]
        artifact_id = entry.get("section_audio_artifact_id")
        if not isinstance(artifact_id, str):
            raise AppError("VOICE_SECTION_AUDIO_INVALID", "Final Voice section Artifact identity is missing.", 409, retryable=False)
        artifact = self.voice.artifacts.get_required(artifact_id)
        data = self.voice.artifacts.resolve_file_path(artifact).read_bytes()
        info = validate_canonical_wav_bytes(data, maximum_bytes=get_settings().tts_provider_max_output_bytes)
        if (
            info.sha256 != entry.get("audio_sha256")
            or info.sample_count != entry.get("sample_count")
            or info.sample_rate_hz != entry.get("sample_rate_hz")
            or artifact.sha256 != info.sha256
        ):
            raise AppError("VOICE_SECTION_AUDIO_INVALID", "Final Voice section Artifact integrity check failed.", 409, retryable=False)
        return VoiceSectionAudio(data=data, info=info)

    @staticmethod
    def _assemble(section_id: str, segment_bytes: list[bytes]) -> VoiceSectionAudio:
        if not segment_bytes:
            raise AppError("VOICE_SECTION_NOT_FOUND", "Voice section audio was not found.", 404, retryable=False)
        data, info = assemble_canonical_wav(segment_bytes)
        return VoiceSectionAudio(data=data, info=info)
