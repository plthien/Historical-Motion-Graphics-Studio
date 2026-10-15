from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType
from app.modules.v4.beat_timing_map import compile_beat_timing_map
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.elevenlabs_client import (
    ElevenLabsAmbiguousSubmission,
    ElevenLabsHttpClient,
)
from app.modules.v4.elevenlabs_credentials import (
    build_runtime_elevenlabs_credential_resolver,
)
from app.modules.v4.invalidation import ProjectPipelineInvalidationService
from app.modules.v4.production_artifact_loader import ProductionArtifactLoaderV4
from app.modules.v4.script_word_budget import SCRIPT_SECTION_SEPARATOR
from app.modules.v4.voice_audio import (
    CANONICAL_SAMPLE_RATE,
    assemble_canonical_wav,
    validate_canonical_wav_bytes,
)
from app.modules.v4.voice_first_policy import (
    FORCED_ALIGNMENT_POLICY_VERSION,
    VOICE_ARCHITECTURE_VERSION,
)
from app.modules.v4.voice_spoken_text import compile_voice_spoken_text
from app.modules.v4.voice_task3 import (
    ALIGNMENT_PROVIDER,
    ALIGNMENT_SAMPLE_ROUNDING_POLICY,
    NARRATION_WORD_TIMING_MAP_SCHEMA_VERSION,
    _validate_published_word_timing_map,
    build_project_word_timing,
    validate_character_alignment,
)
from app.repositories import VoiceRealignmentCommitGuard
from app.services.artifacts import (
    StagedArtifact,
    cleanup_staged_artifact_safely,
    rollback_safely,
)


logger = logging.getLogger(__name__)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


class VoiceCurrentRealignmentService:
    """Temporary timing-only repair for the current approved Voice audio.

    The service deliberately has no TTS dependency. It reuses immutable final
    section WAVs, republishes only timing authorities, and preserves all visual
    authorities when the current Scene Plan recompiles against the new timing.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        client_factory: Callable[[], ElevenLabsHttpClient] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory

    def realign_current(
        self,
        *,
        project_id: str,
        expected_narration_audio_artifact_id: str,
        expected_narration_word_timing_map_artifact_id: str,
    ) -> dict[str, Any]:
        snapshot = self._snapshot(
            project_id=project_id,
            expected_audio_id=expected_narration_audio_artifact_id,
            expected_timing_id=expected_narration_word_timing_map_artifact_id,
        )
        alignments = self._align_sections(snapshot["sections"])
        return self._publish(snapshot=snapshot, alignments=alignments)

    def _snapshot(
        self,
        *,
        project_id: str,
        expected_audio_id: str,
        expected_timing_id: str,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            service = DurableVoiceService(session)
            project = service._project_required(project_id)
            if service.operations.list_active_for_project(project_id):
                raise AppError(
                    "VOICE_OPERATION_ACTIVE",
                    "Current Voice cannot be realigned while another Voice operation is active.",
                    409,
                    retryable=False,
                )
            if (
                project.narration_audio_artifact_id != expected_audio_id
                or project.narration_word_timing_map_artifact_id != expected_timing_id
            ):
                raise AppError(
                    "VOICE_REALIGNMENT_CONFLICT",
                    "The approved Voice pair changed before realignment started.",
                    409,
                    retryable=False,
                )

            loader = ProductionArtifactLoaderV4(service.artifacts)
            audio = loader.load_approved_narration_audio(project)
            old_timing = loader.load_narration_word_timing_map(
                project, expected_timing_id, require_current=True
            )
            script, _analysis = service._load_sources(project)
            compiled = compile_voice_spoken_text(script)
            script_sections = script.get("script_sections")
            timing_sections = old_timing.get("sections")
            if (
                not isinstance(script_sections, list)
                or not isinstance(timing_sections, list)
                or not script_sections
                or len(script_sections) != len(timing_sections)
                or len(compiled.sections) != len(script_sections)
            ):
                raise AppError(
                    "VOICE_REALIGNMENT_SOURCE_INVALID",
                    "Current Voice section lineage does not match the current Script.",
                    409,
                    retryable=False,
                )
            if (
                old_timing.get("source_script_artifact_id") != project.script_artifact_id
                or old_timing.get("source_narration_audio_artifact_id") != audio.artifact_id
                or old_timing.get("sample_rate_hz") != CANONICAL_SAMPLE_RATE
                or old_timing.get("spoken_compilation_sha256")
                != compiled.compilation_sha256
            ):
                raise AppError(
                    "VOICE_REALIGNMENT_SOURCE_INVALID",
                    "Current NarrationWordTimingMap lineage is stale.",
                    409,
                    retryable=False,
                )

            full_audio_bytes = service.artifacts.resolve_file_path(audio).read_bytes()
            full_audio_info = validate_canonical_wav_bytes(full_audio_bytes)
            if (
                full_audio_info.sha256 != audio.sha256
                or int(old_timing.get("total_sample_count") or -1)
                != full_audio_info.sample_count
            ):
                raise AppError(
                    "VOICE_REALIGNMENT_SOURCE_INVALID",
                    "Current narration audio does not match its timing authority.",
                    409,
                    retryable=False,
                )

            frozen_sections: list[dict[str, Any]] = []
            section_wavs: list[bytes] = []
            for order, (script_section, compiled_section, timing_section) in enumerate(
                zip(script_sections, compiled.sections, timing_sections, strict=True), 1
            ):
                if not all(
                    isinstance(item, dict)
                    for item in (script_section, compiled_section, timing_section)
                ):
                    raise AppError(
                        "VOICE_REALIGNMENT_SOURCE_INVALID",
                        "Current Voice section lineage is malformed.",
                        409,
                        retryable=False,
                    )
                section_id = str(script_section.get("section_id") or "")
                transcript = str(compiled_section.get("canonical_spoken_text") or "")
                section_artifact_id = timing_section.get("section_audio_artifact_id")
                expected_section_sha = timing_section.get("section_audio_sha256")
                expected_sample_count = timing_section.get("sample_count")
                if (
                    not section_id
                    or timing_section.get("section_id") != section_id
                    or compiled_section.get("section_id") != section_id
                    or int(timing_section.get("section_order") or -1) != order
                    or timing_section.get("canonical_spoken_text") != transcript
                    or not isinstance(section_artifact_id, str)
                    or not isinstance(expected_section_sha, str)
                    or not isinstance(expected_sample_count, int)
                    or expected_sample_count <= 0
                ):
                    raise AppError(
                        "VOICE_REALIGNMENT_SOURCE_INVALID",
                        "Current Voice section identity is invalid.",
                        409,
                        retryable=False,
                    )
                section_artifact = service.artifact_rows.get(section_artifact_id)
                if (
                    section_artifact is None
                    or section_artifact.status != ArtifactStatus.READY
                    or section_artifact.deleted_at is not None
                    or section_artifact.artifact_type != ArtifactType.AUDIO
                    or section_artifact.sha256 != expected_section_sha
                ):
                    raise AppError(
                        "VOICE_REALIGNMENT_SOURCE_MISSING",
                        "A frozen final section audio Artifact is unavailable.",
                        409,
                        retryable=False,
                        details={"section_id": section_id},
                    )
                section_bytes = service.artifacts.resolve_file_path(
                    section_artifact
                ).read_bytes()
                section_info = validate_canonical_wav_bytes(section_bytes)
                if (
                    section_info.sha256 != expected_section_sha
                    or section_info.sample_rate_hz != CANONICAL_SAMPLE_RATE
                    or section_info.sample_count != expected_sample_count
                ):
                    raise AppError(
                        "VOICE_REALIGNMENT_SOURCE_INVALID",
                        "Frozen final section audio identity is invalid.",
                        409,
                        retryable=False,
                        details={"section_id": section_id},
                    )
                frozen_sections.append(
                    {
                        "section_id": section_id,
                        "section_order": order,
                        "authored_narration": str(script_section.get("narration") or ""),
                        "spoken_transcript": transcript,
                        "spoken_transcript_sha256": hashlib.sha256(
                            transcript.encode("utf-8")
                        ).hexdigest(),
                        "authored_spoken_mapping": list(
                            compiled_section.get("authored_spoken_mapping") or []
                        ),
                        "section_audio_artifact_id": section_artifact_id,
                        "section_audio_sha256": section_info.sha256,
                        "sample_count": section_info.sample_count,
                        "audio_bytes": section_bytes,
                        "final_section_identity_sha256": str(
                            timing_section.get("final_section_identity_sha256") or ""
                        ),
                    }
                )
                section_wavs.append(section_bytes)

            assembled, assembled_info = assemble_canonical_wav(section_wavs)
            if (
                assembled_info.sample_count != full_audio_info.sample_count
                or hashlib.sha256(assembled).hexdigest() != hashlib.sha256(full_audio_bytes).hexdigest()
            ):
                raise AppError(
                    "VOICE_REALIGNMENT_SOURCE_INVALID",
                    "Frozen section audio no longer reconstructs the approved narration audio exactly.",
                    409,
                    retryable=False,
                )

            return {
                "project_id": project_id,
                "script_artifact_id": str(project.script_artifact_id),
                "audio_artifact_id": audio.artifact_id,
                "old_timing_artifact_id": expected_timing_id,
                "audio_sha256": full_audio_info.sha256,
                "total_sample_count": full_audio_info.sample_count,
                "spoken_compilation_sha256": compiled.compilation_sha256,
                "canonical_spoken_narration": str(
                    compiled.spoken_script.get("full_script") or ""
                ),
                "generation_provider": old_timing.get("generation_provider"),
                "generation_voice_id": old_timing.get("generation_voice_id"),
                "sections": frozen_sections,
                "guard": VoiceRealignmentCommitGuard(
                    script_artifact_id=str(project.script_artifact_id),
                    narration_audio_artifact_id=audio.artifact_id,
                    previous_narration_word_timing_map_artifact_id=expected_timing_id,
                    scene_plan_artifact_id=project.scene_plan_artifact_id,
                    visual_plan_artifact_id=project.visual_plan_artifact_id,
                    prompt_bundle_artifact_id=project.prompt_bundle_artifact_id,
                    scene_asset_manifest_artifact_id=project.scene_asset_manifest_artifact_id,
                    previous_beat_timing_map_artifact_id=project.beat_timing_map_artifact_id,
                    previous_assembly_plan_artifact_id=project.assembly_plan_artifact_id,
                    previous_subtitle_srt_artifact_id=project.subtitle_srt_artifact_id,
                    previous_subtitle_vtt_artifact_id=project.subtitle_vtt_artifact_id,
                    previous_final_video_artifact_id=project.final_video_artifact_id,
                ),
            }

    def _align_sections(
        self, sections: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        client = self._client()
        results: list[dict[str, Any]] = []
        try:
            for section in sections:
                try:
                    response = client.forced_alignment(
                        audio_bytes=section["audio_bytes"],
                        text=section["spoken_transcript"],
                    )
                except ElevenLabsAmbiguousSubmission as exc:
                    raise AppError(
                        "VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN",
                        "Forced Alignment submission outcome is unknown; the approved Voice remains unchanged.",
                        409,
                        retryable=False,
                        details={"section_id": section["section_id"]},
                    ) from exc
                characters, loss = validate_character_alignment(
                    response.payload,
                    transcript=section["spoken_transcript"],
                    sample_count=section["sample_count"],
                    sample_rate_hz=CANONICAL_SAMPLE_RATE,
                )
                results.append({"characters": characters, "loss": loss})
        finally:
            client.close()
        return results

    def _publish(
        self,
        *,
        snapshot: dict[str, Any],
        alignments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        staged: list[StagedArtifact] = []
        with self.session_factory() as session:
            try:
                service = DurableVoiceService(session)
                project = service._project_required(snapshot["project_id"])
                if service.operations.list_active_for_project(project.project_id):
                    raise AppError(
                        "VOICE_OPERATION_ACTIVE",
                        "Another Voice operation started before realignment publication.",
                        409,
                        retryable=False,
                    )
                script, _analysis = service._load_sources(project)
                compiled = compile_voice_spoken_text(script)
                if (
                    project.script_artifact_id != snapshot["script_artifact_id"]
                    or project.narration_audio_artifact_id != snapshot["audio_artifact_id"]
                    or project.narration_word_timing_map_artifact_id
                    != snapshot["old_timing_artifact_id"]
                    or compiled.compilation_sha256
                    != snapshot["spoken_compilation_sha256"]
                ):
                    raise AppError(
                        "VOICE_REALIGNMENT_CONFLICT",
                        "Voice or Script lineage changed during realignment.",
                        409,
                        retryable=False,
                    )

                timing_artifact_id = service.artifacts.preallocate_artifact_id()
                timing_map = self._build_timing_map(
                    snapshot=snapshot,
                    alignments=alignments,
                )
                timing_bytes = _json_bytes(timing_map)
                timing_staged = service.artifacts.stage_artifact_revision(
                    logical_id=f"project/{project.project_id}/voice/narration-word-timing-map",
                    artifact_type=ArtifactType.JSON,
                    filename="narration_word_timing_map.json",
                    content=timing_bytes,
                    artifact_id=timing_artifact_id,
                    provider_source="voice_current_realignment",
                    content_hash=hashlib.sha256(timing_bytes).hexdigest(),
                )
                staged.append(timing_staged)

                beat_staged: StagedArtifact | None = None
                new_beat_timing_id: str | None = None
                if project.scene_plan_artifact_id is not None:
                    loader = ProductionArtifactLoaderV4(service.artifacts)
                    scene_plan = loader.load_scene_plan(
                        project, str(project.scene_plan_artifact_id), require_current=True
                    )
                    if scene_plan.get("status") == "approved":
                        new_beat_timing_id = service.artifacts.preallocate_artifact_id()
                        beat_timing_map = compile_beat_timing_map(
                            project_id=project.project_id,
                            script_artifact_id=str(project.script_artifact_id),
                            scene_plan_artifact_id=str(project.scene_plan_artifact_id),
                            narration_audio_artifact_id=str(
                                project.narration_audio_artifact_id
                            ),
                            narration_word_timing_map_artifact_id=timing_artifact_id,
                            scene_plan=scene_plan,
                            word_timing_map=timing_map,
                        )
                        V4ContractValidator().validate(
                            "beat_timing_map_v1.schema.json",
                            beat_timing_map,
                            error_code="BEAT_TIMING_MAP_INVALID",
                        )
                        beat_staged = service.artifacts.stage_artifact_revision(
                            logical_id=f"project/{project.project_id}/beat_timing_map",
                            artifact_type=ArtifactType.JSON,
                            filename="beat_timing_map.json",
                            content=_json_bytes(beat_timing_map),
                            artifact_id=new_beat_timing_id,
                            provider_source="voice_current_realignment",
                        )
                        staged.append(beat_staged)
                    elif project.beat_timing_map_artifact_id is not None:
                        raise AppError(
                            "VOICE_REALIGNMENT_SOURCE_INVALID",
                            "A draft Scene Plan cannot own a current BeatTimingMap.",
                            409,
                            retryable=False,
                        )

                old_timing_current = service.artifact_rows.get_current_by_logical_id(
                    timing_staged.logical_id
                )
                if (
                    old_timing_current is None
                    or old_timing_current.artifact_id
                    != snapshot["old_timing_artifact_id"]
                    or not service.artifact_rows.compare_and_set_current_artifact(
                        logical_id=timing_staged.logical_id,
                        new_artifact_id=timing_staged.artifact_id,
                        expected_current_artifact_id=old_timing_current.artifact_id,
                    )
                ):
                    raise AppError(
                        "VOICE_REALIGNMENT_CONFLICT",
                        "Current timing Artifact changed before publication.",
                        409,
                        retryable=False,
                    )

                if beat_staged is not None:
                    old_beat_current = service.artifact_rows.get_current_by_logical_id(
                        beat_staged.logical_id
                    )
                    expected_old_beat = snapshot["guard"].previous_beat_timing_map_artifact_id
                    actual_old_beat = (
                        old_beat_current.artifact_id if old_beat_current is not None else None
                    )
                    if (
                        actual_old_beat != expected_old_beat
                        or not service.artifact_rows.compare_and_set_current_artifact(
                            logical_id=beat_staged.logical_id,
                            new_artifact_id=beat_staged.artifact_id,
                            expected_current_artifact_id=actual_old_beat,
                        )
                    ):
                        raise AppError(
                            "VOICE_REALIGNMENT_CONFLICT",
                            "Current BeatTimingMap changed before publication.",
                            409,
                            retryable=False,
                        )

                ProjectPipelineInvalidationService(session).demote_assembly_outputs(
                    project
                )
                if not service.projects.compare_and_set_voice_realignment(
                    project_id=project.project_id,
                    new_narration_word_timing_map_artifact_id=timing_staged.artifact_id,
                    new_beat_timing_map_artifact_id=new_beat_timing_id,
                    guard=snapshot["guard"],
                    updated_at=utc_now_iso(),
                ):
                    raise AppError(
                        "VOICE_REALIGNMENT_CONFLICT",
                        "Project lineage changed before timing-only publication.",
                        409,
                        retryable=False,
                    )
                session.commit()
                staged.clear()
                guard: VoiceRealignmentCommitGuard = snapshot["guard"]
                return {
                    "status": "realigned",
                    "narration_audio_artifact_id": snapshot["audio_artifact_id"],
                    "previous_narration_word_timing_map_artifact_id": snapshot[
                        "old_timing_artifact_id"
                    ],
                    "narration_word_timing_map_artifact_id": timing_artifact_id,
                    "beat_timing_map_artifact_id": new_beat_timing_id,
                    "preserved_scene_plan_artifact_id": guard.scene_plan_artifact_id,
                    "preserved_visual_plan_artifact_id": guard.visual_plan_artifact_id,
                    "preserved_prompt_bundle_artifact_id": guard.prompt_bundle_artifact_id,
                    "preserved_scene_asset_manifest_artifact_id": guard.scene_asset_manifest_artifact_id,
                }
            except Exception:
                rollback_safely(session, logger=logger)
                for item in reversed(staged):
                    cleanup_staged_artifact_safely(item, logger=logger)
                raise

    @staticmethod
    def _build_timing_map(
        *,
        snapshot: dict[str, Any],
        alignments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sections = snapshot["sections"]
        if len(sections) != len(alignments):
            raise AppError(
                "VOICE_REALIGNMENT_SOURCE_INVALID",
                "Forced Alignment did not cover every frozen Voice section.",
                409,
                retryable=False,
            )
        timing_sections: list[dict[str, Any]] = []
        global_sample_cursor = 0
        authored_section_start = 0
        global_word_index = 0
        alignment_ids: list[str] = []
        losses: list[dict[str, Any]] = []
        for section, alignment in zip(sections, alignments, strict=True):
            characters = alignment["characters"]
            alignment_id = generate_id("voice_realignment")
            words = build_project_word_timing(
                transcript=section["spoken_transcript"],
                character_timing=characters,
                authored_spoken_mapping=section["authored_spoken_mapping"],
                authored_section_start=authored_section_start,
                authored_length=len(section["authored_narration"]),
                section_id=section["section_id"],
            )
            for word in words:
                word["word_index"] = global_word_index
                word["section_id"] = section["section_id"]
                word["global_start_sample"] = global_sample_cursor + int(
                    word["start_sample"]
                )
                word["global_end_sample"] = global_sample_cursor + int(
                    word["end_sample"]
                )
                global_word_index += 1
            authored_end = authored_section_start + len(section["authored_narration"])
            timing_sections.append(
                {
                    "section_id": section["section_id"],
                    "section_order": section["section_order"],
                    "authored_section_start": authored_section_start,
                    "authored_section_end": authored_end,
                    "section_audio_artifact_id": section["section_audio_artifact_id"],
                    "section_audio_sha256": section["section_audio_sha256"],
                    "final_section_identity_sha256": section[
                        "final_section_identity_sha256"
                    ],
                    "sample_count": section["sample_count"],
                    "global_start_sample": global_sample_cursor,
                    "global_end_sample": global_sample_cursor
                    + section["sample_count"],
                    "canonical_spoken_text": section["spoken_transcript"],
                    "canonical_spoken_text_sha256": section[
                        "spoken_transcript_sha256"
                    ],
                    "authored_spoken_mapping": section["authored_spoken_mapping"],
                    "character_timing": [
                        {
                            **item,
                            "global_start_sample": global_sample_cursor
                            + int(item["start_sample"]),
                            "global_end_sample": global_sample_cursor
                            + int(item["end_sample"]),
                        }
                        for item in characters
                    ],
                    "words": words,
                    "alignment_task_id": alignment_id,
                    "alignment_loss_telemetry": alignment["loss"],
                }
            )
            alignment_ids.append(alignment_id)
            losses.append(alignment["loss"])
            global_sample_cursor += section["sample_count"]
            authored_section_start = authored_end + len(SCRIPT_SECTION_SEPARATOR)

        timing_map = {
            "schema_version": NARRATION_WORD_TIMING_MAP_SCHEMA_VERSION,
            "architecture_version": VOICE_ARCHITECTURE_VERSION,
            "source_script_artifact_id": snapshot["script_artifact_id"],
            "source_narration_audio_artifact_id": snapshot["audio_artifact_id"],
            "sample_rate_hz": CANONICAL_SAMPLE_RATE,
            "total_sample_count": snapshot["total_sample_count"],
            "canonical_spoken_narration": snapshot["canonical_spoken_narration"],
            "canonical_spoken_narration_sha256": hashlib.sha256(
                snapshot["canonical_spoken_narration"].encode("utf-8")
            ).hexdigest(),
            "spoken_compilation_sha256": snapshot["spoken_compilation_sha256"],
            "sections": timing_sections,
            "generation_provider": snapshot["generation_provider"],
            "generation_voice_id": snapshot["generation_voice_id"],
            "alignment_provider": ALIGNMENT_PROVIDER,
            "alignment_policy_version": FORCED_ALIGNMENT_POLICY_VERSION,
            "sample_rounding_policy": ALIGNMENT_SAMPLE_ROUNDING_POLICY,
            "alignment_summary": {
                "section_count": len(timing_sections),
                "completed_alignment_task_ids": alignment_ids,
                "loss_telemetry": losses,
            },
        }
        if global_sample_cursor != snapshot["total_sample_count"]:
            raise AppError(
                "VOICE_REALIGNMENT_SOURCE_INVALID",
                "Realigned sections do not cover the approved narration timeline.",
                409,
                retryable=False,
            )
        _validate_published_word_timing_map(timing_map)
        return timing_map

    def _client(self) -> ElevenLabsHttpClient:
        if self.client_factory is not None:
            return self.client_factory()
        settings = get_settings()
        api_key = build_runtime_elevenlabs_credential_resolver(settings).require_api_key()
        return ElevenLabsHttpClient(
            api_key=api_key,
            api_base_url=settings.elevenlabs_api_base_url,
            max_response_bytes=max(
                settings.tts_provider_max_output_bytes * 2 + 1_048_576,
                1_048_576,
            ),
            timeout_seconds=float(settings.tts_provider_timeout_seconds),
        )
