from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.voice_schemas_v4 import VoiceJobStatusResponse, VoiceJobSubmissionResponse
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType, JobStatus
from app.db.models import Channel, Job, VoiceAlignmentTask, VoiceGenerationOperation, VoiceSegmentTask
from app.modules.platform.invalidation import ProjectPipelineInvalidationService
from app.modules.v4.voice_first_policy import (
    ACTIVE_VOICE_PRESETS,
    DEFAULT_SIMILARITY_BOOST,
    DEFAULT_STABILITY,
    DEFAULT_USE_SPEAKER_BOOST,
    ELEVENLABS_SEED_POLICY_VERSION,
    VOICE_ARCHITECTURE_VERSION,
)
from app.modules.v4.elevenlabs_credentials import build_runtime_elevenlabs_credential_resolver
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from app.modules.v4.voice_first_elevenlabs_adapter import VoiceFirstElevenLabsProviderAdapter
from app.modules.v4.voice_job_projection import VoiceJobProjectionService
from app.modules.v4.voice_pacing import (
    VOICE_REMEDIATION_BLOCK_KOKORO_DURATION_MODEL_INVALID,
    VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID,
    VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED,
    build_voice_pacing_plan,
    classify_voice_pacing_remediation_actionability,
    pacing_section_for_segment,
    provider_control_value_for_segment,
    raw_segment_synthesis_identity_sha256,
    raw_segment_synthesis_material_identity_sha256,
    raw_synthesis_identity_sha256,
    validate_remediation_report_integrity,
    voice_pacing_policy_identity,
)
from app.modules.v4.voice_pacing_manual_acceptance import (
    VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY,
    carry_forward_manual_pacing_acceptances,
    exact_manual_pacing_acceptance_matches,
)
from app.modules.v4.voice_provider_contracts import VoiceProviderProfile, VoiceProviderReadiness
from app.modules.v4.voice_segmentation import SEGMENTATION_VERSION, segment_narration
from app.modules.v4.voice_spoken_text import (
    compile_voice_spoken_text,
    forbidden_spoken_boundaries_by_section,
    kokoro_pieces_for_segment,
    provider_text_for_segment,
)
from app.modules.v4.voice_service import VoiceService, canonical_hash
from app.repositories import VoiceOutputPairCommitGuard
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository

VOICE_ALIGNMENT_NOT_READY_CODE = "VOICE_ALIGNMENT_NOT_READY"
VOICE_ALIGNMENT_NOT_READY_MESSAGE = "Kokoro generation is ready, but ElevenLabs Forced Alignment is not configured."


def _stable_voice_segment_seed(
    *,
    provider_identity: dict[str, Any],
    voice_preset: str | None,
    segment: dict[str, Any],
    provider_text_sha256: str,
    spoken_compilation_sha256: str,
    authored_spoken_mapping_sha256: str,
    synthesis_context_sha256: str | None,
) -> int:
    """Freeze performance realization independently from pacing remediation.

    Pacing speed is deliberately excluded. A section remediation must change only
    the provider pacing directive while retaining the same performance seed. Raw
    synthesis identities still include the pacing directive, so audio generated
    at different speeds can never be reused as the same synthesis artifact.
    """
    seed_material = canonical_hash(
        {
            "schema_version": 2,
            "architecture_version": VOICE_ARCHITECTURE_VERSION,
            "seed_policy": ELEVENLABS_SEED_POLICY_VERSION,
            "provider_identity": provider_identity,
            "voice_preset": voice_preset,
            "segment_id": segment["segment_id"],
            "segment_text_sha256": segment["text_sha256"],
            "provider_text_sha256": provider_text_sha256,
            "spoken_compilation_sha256": spoken_compilation_sha256,
            "authored_spoken_mapping_sha256": authored_spoken_mapping_sha256,
            "synthesis_context_sha256": synthesis_context_sha256,
        }
    )
    return int(seed_material[:8], 16)


class DurableVoiceService(VoiceService):
    """Public Voice API service whose execution authority is durable DB state."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    @staticmethod
    def _kokoro_provider_profile(*, voice_id: str, language: str) -> VoiceProviderProfile:
        return VoiceProviderProfile(
            profile_id="kokoro_local",
            provider_key=KokoroProviderAdapter.provider_key,
            enabled=True,
            display_name="Kokoro Local",
            voice_id=voice_id,
            language=language,
            speaking_rate=1.0,
            requested_sample_rate_hz=48_000,
            requested_output_format="wav_pcm_s16le",
            provider_options={"runtime": "local_kokoro_cli"},
            maximum_segment_characters=800,
        )

    @staticmethod
    def _provider_readiness_issue(readiness: VoiceProviderReadiness, *, fallback_code: str, fallback_message: str) -> dict[str, str] | None:
        if readiness.ready:
            return None
        return {
            "error_code": readiness.issue_code or fallback_code,
            "message": readiness.message or fallback_message,
        }

    @staticmethod
    def _elevenlabs_generation_provider_profile(
        *,
        project: Any,
        profile_payload: dict[str, Any],
        voice_preset: str,
    ) -> VoiceProviderProfile:
        model_id = ACTIVE_VOICE_PRESETS.get(voice_preset)
        if model_id is None:
            raise AppError("VOICE_PRESET_UNSUPPORTED", "The requested Voice preset is not supported.", 422, retryable=False)
        voice_id = profile_payload.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id.strip():
            raise AppError("VOICE_CHANNEL_PROFILE_REQUIRED", "Channel Voice Profile is required for ElevenLabs generation.", 409, retryable=False)
        calibration = profile_payload.get("calibration") if isinstance(profile_payload.get("calibration"), dict) else {}
        preset_calibration = calibration.get(voice_preset) if isinstance(calibration.get(voice_preset), dict) else {}
        return VoiceProviderProfile(
            profile_id="channel_elevenlabs_pvc",
            provider_key=VoiceFirstElevenLabsProviderAdapter.provider_key,
            enabled=True,
            display_name="Channel ElevenLabs PVC",
            voice_id=voice_id,
            language=str(project.channel_context_json.get("language", "English")) if isinstance(project.channel_context_json, dict) else "English",
            speaking_rate=1.0,
            requested_sample_rate_hz=24_000,
            requested_output_format="wav_pcm_s16le",
            provider_options={
                "model_id": model_id,
                "narration_language": "en",
                "stability": float(preset_calibration.get("stability", DEFAULT_STABILITY)),
                "similarity_boost": float(preset_calibration.get("similarity_boost", DEFAULT_SIMILARITY_BOOST)),
                "use_speaker_boost": bool(preset_calibration.get("use_speaker_boost", DEFAULT_USE_SPEAKER_BOOST)),
            },
            maximum_segment_characters=40_000 if voice_preset == "economy" else 10_000,
        )

    def _resolve_kokoro_catalog_voice(self, requested_voice_id: str) -> dict[str, str]:
        voice_id = requested_voice_id.strip()
        catalog = self.tts_settings.registry.list_voices(
            KokoroProviderAdapter.provider_key,
            self._kokoro_provider_profile(voice_id=voice_id or "catalog_probe", language="en"),
        )
        matches = [voice for voice in catalog if voice.voice_id == voice_id]
        if len(matches) != 1:
            raise AppError(
                "TTS_VOICE_INVALID",
                "The selected Kokoro voice is not available in the runtime catalog.",
                422,
                retryable=False,
                details={"voice_id": voice_id},
            )
        selected = matches[0]
        return {"voice_id": selected.voice_id, "language": selected.language}

    @staticmethod
    def _elevenlabs_alignment_readiness_issue() -> dict[str, str] | None:
        try:
            build_runtime_elevenlabs_credential_resolver(get_settings()).require_api_key()
        except AppError as exc:
            return {
                "error_code": VOICE_ALIGNMENT_NOT_READY_CODE,
                "message": VOICE_ALIGNMENT_NOT_READY_MESSAGE,
                "cause_error_code": exc.error_code,
            }
        return None

    def _require_elevenlabs_alignment_ready_for_kokoro(self) -> None:
        issue = self._elevenlabs_alignment_readiness_issue()
        if issue is None:
            return
        raise AppError(
            issue["error_code"],
            issue["message"],
            409,
            retryable=False,
            details={
                "alignment_provider": "elevenlabs",
                "cause_error_code": issue["cause_error_code"],
            },
        )

    @staticmethod
    def _operation_generation_provider(operation: VoiceGenerationOperation | None) -> str | None:
        provider_identity = getattr(operation, "provider_identity_json", None)
        if operation is None or not isinstance(provider_identity, dict):
            return None
        value = provider_identity.get("provider_key") or provider_identity.get("provider")
        return str(value) if isinstance(value, str) and value else None

    @staticmethod
    def _candidate_requires_channel_voice_profile(operation: VoiceGenerationOperation | None) -> bool:
        provider = DurableVoiceService._operation_generation_provider(operation)
        return provider is None or provider == VoiceFirstElevenLabsProviderAdapter.provider_key

    @staticmethod
    def _operation_generation_voice_id(operation: VoiceGenerationOperation | None) -> str | None:
        provider_identity = getattr(operation, "provider_identity_json", None)
        if operation is None or not isinstance(provider_identity, dict):
            return None
        value = provider_identity.get("voice_id")
        return str(value) if isinstance(value, str) and value else None

    def submit_generation(
        self,
        project_id: str,
        request: dict[str, Any],
        *,
        target_section_id: str | None = None,
    ) -> dict[str, Any]:
        project = self._project_required(project_id)
        if self.operations.list_active_for_project(project_id):
            raise AppError("VOICE_OPERATION_ACTIVE", "A Voice operation is already active for this project.", 409, retryable=False)
        script, _scene_plan = self._load_sources(project)
        expected_audio = request.get("expected_narration_audio_artifact_id")
        expected_timing = request.get("expected_narration_word_timing_map_artifact_id")
        if project.narration_audio_artifact_id != expected_audio or project.narration_word_timing_map_artifact_id != expected_timing:
            raise AppError("VOICE_PUBLICATION_CONFLICT", "The current Voice output pair changed before generation.", 409, retryable=False)

        channel = self.session.get(Channel, project.channel_id)
        source_operation = None
        if target_section_id:
            source_operation_id = request.get("source_remediation_operation_id")
            source_operation = self.operations.get(str(source_operation_id)) if isinstance(source_operation_id, str) else None
            if source_operation is None:
                raise AppError("VOICE_PACING_REMEDIATION_STALE", "Source Voice remediation operation is unavailable.", 409, retryable=False)
            generation_provider = str(source_operation.provider_identity_json.get("provider_key") or source_operation.provider_identity_json.get("provider") or "")
            voice_preset = source_operation.voice_preset
            profile = {
                "profile_id": source_operation.tts_profile_id,
                "provider": generation_provider,
                "enabled": True,
                "display_name": source_operation.tts_profile_id,
                "sample_rate_hz": int(source_operation.generation_settings_snapshot_json.get("sample_rate_hz")),
                "output_format": str(source_operation.generation_settings_snapshot_json.get("output_format")),
                "maximum_segment_characters": int(source_operation.generation_settings_snapshot_json.get("maximum_segment_characters")),
                "provider_options": deepcopy(source_operation.generation_settings_snapshot_json.get("provider_options") or {}),
            }
            voice = {"voice_id": source_operation.voice_id, "language": source_operation.language}
            speaking_rate = float(source_operation.speaking_rate)
            profile_payload = channel.voice_profile_json if channel is not None else None
            channel_profile_sha256 = (
                str(source_operation.generation_settings_snapshot_json.get("channel_voice_profile_sha256"))
                if generation_provider == VoiceFirstElevenLabsProviderAdapter.provider_key
                else None
            )
        else:
            generation_provider = str(request.get("generation_provider") or "")
            if generation_provider == VoiceFirstElevenLabsProviderAdapter.provider_key:
                voice_preset = str(request.get("voice_preset") or "")
                model_id = ACTIVE_VOICE_PRESETS.get(voice_preset)
                if model_id is None:
                    raise AppError("VOICE_PRESET_UNSUPPORTED", "The requested Voice preset is not supported.", 422, retryable=False)
                if request.get("generation_voice_id") is not None:
                    raise AppError("VOICE_GENERATION_IDENTITY_INVALID", "ElevenLabs generation uses the Channel Professional Voice Clone, not a request voice ID.", 422, retryable=False)
                profile_payload = channel.voice_profile_json if channel is not None else None
                if not isinstance(profile_payload, dict):
                    raise AppError("VOICE_CHANNEL_PROFILE_REQUIRED", "Channel Voice Profile is required before ElevenLabs Voice generation.", 409, retryable=False)
                calibration = profile_payload.get("calibration") if isinstance(profile_payload.get("calibration"), dict) else {}
                preset_calibration = calibration.get(voice_preset) if isinstance(calibration.get(voice_preset), dict) else {}
                provider_options = {
                    "model_id": model_id,
                    "narration_language": "en",
                    "stability": float(preset_calibration.get("stability", DEFAULT_STABILITY)),
                    "similarity_boost": float(preset_calibration.get("similarity_boost", DEFAULT_SIMILARITY_BOOST)),
                    "use_speaker_boost": bool(preset_calibration.get("use_speaker_boost", DEFAULT_USE_SPEAKER_BOOST)),
                }
                profile = {
                    "profile_id": "channel_elevenlabs_pvc",
                    "provider": generation_provider,
                    "enabled": True,
                    "display_name": "Channel ElevenLabs PVC",
                    "sample_rate_hz": 24_000,
                    "output_format": "wav_pcm_s16le",
                    "maximum_segment_characters": 40_000 if voice_preset == "economy" else 10_000,
                    "provider_options": provider_options,
                }
                voice = {
                    "voice_id": str(profile_payload["voice_id"]),
                    "language": str(project.channel_context_json.get("language", "English")),
                }
                channel_profile_sha256 = canonical_hash(profile_payload)
            elif generation_provider == KokoroProviderAdapter.provider_key:
                voice_preset = None
                generation_voice_id = request.get("generation_voice_id")
                if not isinstance(generation_voice_id, str) or not generation_voice_id.strip():
                    raise AppError("TTS_VOICE_INVALID", "Select a Kokoro voice before generation.", 422, retryable=False)
                if request.get("voice_preset") is not None:
                    raise AppError("VOICE_GENERATION_IDENTITY_INVALID", "Kokoro generation must not carry an ElevenLabs voice preset.", 422, retryable=False)
                self._require_elevenlabs_alignment_ready_for_kokoro()
                profile_payload = None
                provider_options = {"runtime": "local_kokoro_cli"}
                profile = {
                    "profile_id": "kokoro_local",
                    "provider": generation_provider,
                    "enabled": True,
                    "display_name": "Kokoro Local",
                    "sample_rate_hz": 48_000,
                    "output_format": "wav_pcm_s16le",
                    "maximum_segment_characters": 800,
                    "provider_options": provider_options,
                }
                voice = self._resolve_kokoro_catalog_voice(generation_voice_id)
                channel_profile_sha256 = None
            else:
                raise AppError("VOICE_PROVIDER_UNKNOWN", "The requested Voice generation provider is not registered.", 422, retryable=False)
            speaking_rate = 1.0

        provider_key = str(profile["provider"])
        provider_options = deepcopy(profile.get("provider_options") or {})
        provider_profile = VoiceProviderProfile(
            profile_id=str(profile["profile_id"]),
            provider_key=provider_key,
            enabled=bool(profile.get("enabled")),
            display_name=str(profile.get("display_name", profile["profile_id"])),
            voice_id=str(voice["voice_id"]),
            language=str(voice["language"]),
            speaking_rate=speaking_rate,
            requested_sample_rate_hz=int(profile["sample_rate_hz"]),
            requested_output_format=str(profile["output_format"]),
            provider_options=deepcopy(provider_options),
            maximum_segment_characters=int(profile["maximum_segment_characters"]),
        )
        provider_readiness = self.tts_settings.registry.readiness(provider_profile)
        if not provider_readiness.ready:
            raise AppError(
                provider_readiness.issue_code or "VOICE_PROVIDER_NOT_READY",
                provider_readiness.message or "The selected Voice generation provider is not ready.",
                409,
                retryable=False,
                details=provider_readiness.details,
            )
        execution_identity = self.tts_settings.registry.execution_identity(provider_profile)
        provider_identity = {
            "provider": provider_key,
            "provider_key": provider_key,
            "profile_id": profile["profile_id"],
            "voice_id": voice["voice_id"],
            "language": voice["language"],
            "execution_identity": execution_identity,
        }
        settings_snapshot = {
            "tts_profile_id": profile["profile_id"],
            "voice_id": voice["voice_id"],
            "language": voice["language"],
            "speaking_rate": speaking_rate,
            "voice_preset": voice_preset,
            "generation_provider": provider_key,
            "architecture_version": VOICE_ARCHITECTURE_VERSION,
            "sample_rate_hz": profile["sample_rate_hz"],
            "output_format": profile["output_format"],
            "maximum_segment_characters": profile["maximum_segment_characters"],
            "provider_options": provider_options,
        }
        if channel_profile_sha256 is not None:
            settings_snapshot["channel_voice_profile_sha256"] = channel_profile_sha256
        runtime = get_settings()
        compiled = compile_voice_spoken_text(script)
        full_script = str(compiled.spoken_script.get("full_script", ""))
        if len(full_script) > runtime.tts_max_narration_characters:
            raise AppError("VOICE_NARRATION_TOO_LARGE", "The current narration exceeds the configured Voice size limit.", 413, retryable=False)
        segmentation = segment_narration(
            compiled.spoken_script,
            maximum_characters=int(profile["maximum_segment_characters"]),
            forbidden_boundaries_by_section=forbidden_spoken_boundaries_by_section(compiled),
        )
        if len(segmentation["segments"]) > runtime.tts_max_segment_count:
            raise AppError("VOICE_SEGMENT_COUNT_EXCEEDED", "Narration segmentation exceeds the configured segment-count limit.", 413, retryable=False)
        settings_snapshot["spoken_compilation_sha256"] = compiled.compilation_sha256
        settings_snapshot["spoken_text_policy_version"] = str(compiled.sections[0].get("spoken_text_policy_version"))
        settings_snapshot["delivery_policy_version"] = str(compiled.sections[0].get("delivery_policy_version"))
        provider_hash = canonical_hash(provider_identity)
        operation_type = "regenerate_section" if target_section_id else str(request.get("operation_type", "generate_all"))
        synthesis_context_hashes = self._synthesis_context_hashes(segmentation, provider_identity)
        compiled_by_section = {str(item["section_id"]): item for item in compiled.sections}
        voice_first_segment_payloads: dict[str, dict[str, Any]] = {}
        fixed_duration_by_section: dict[str, float] = {}
        for segment in segmentation["segments"]:
            section_compilation = compiled_by_section[str(segment["section_id"])]
            provider_text = provider_text_for_segment(compiled, segment)
            if provider_key == KokoroProviderAdapter.provider_key:
                kokoro_pieces = kokoro_pieces_for_segment(compiled, segment)
                provider_rendering_payload: dict[str, Any] = {
                    "rendering": "kokoro_piecewise_silence_v2",
                    "pieces": kokoro_pieces,
                }
                fixed_duration_by_section[str(segment["section_id"])] = fixed_duration_by_section.get(str(segment["section_id"]), 0.0) + sum(
                    float(piece.get("duration_seconds") or 0.0)
                    for piece in kokoro_pieces
                    if isinstance(piece, dict) and piece.get("kind") == "silence"
                )
                provider_text_sha256 = canonical_hash(provider_rendering_payload)
            else:
                kokoro_pieces = None
                provider_rendering_payload = {
                    "rendering": "elevenlabs_break_tags_v1",
                    "provider_text": provider_text,
                }
                provider_text_sha256 = hashlib.sha256(provider_text.encode("utf-8")).hexdigest()
            seed = _stable_voice_segment_seed(
                provider_identity=provider_identity,
                voice_preset=voice_preset,
                segment=segment,
                provider_text_sha256=provider_text_sha256,
                spoken_compilation_sha256=compiled.compilation_sha256,
                authored_spoken_mapping_sha256=section_compilation["authored_spoken_mapping_sha256"],
                synthesis_context_sha256=synthesis_context_hashes.get(segment["segment_id"]),
            )
            segment["voice_first"] = {
                "canonical_spoken_text_sha256": segment["text_sha256"],
                "provider_text_sha256": provider_text_sha256,
                "provider_rendering": provider_rendering_payload["rendering"],
                "spoken_compilation_sha256": compiled.compilation_sha256,
                "authored_spoken_mapping_sha256": section_compilation["authored_spoken_mapping_sha256"],
                "delivery_policy_version": section_compilation["delivery_policy_version"],
                "seed": seed,
            }
            voice_first_segment_payloads[segment["segment_id"]] = {
                "provider_text": provider_text,
                "provider_text_sha256": provider_text_sha256,
                **({"kokoro_pieces": kokoro_pieces} if kokoro_pieces is not None else {}),
                "seed": seed,
                "canonical_spoken_text_sha256": segment["text_sha256"],
                "spoken_compilation_sha256": compiled.compilation_sha256,
                "authored_spoken_mapping_sha256": section_compilation["authored_spoken_mapping_sha256"],
                "delivery_policy_version": section_compilation["delivery_policy_version"],
            }
        provider_duration_models_by_section = None
        if provider_key == KokoroProviderAdapter.provider_key:
            kokoro_section_ids = {
                str(section.get("section_id"))
                for section in compiled.sections
                if isinstance(section.get("section_id"), str) and section.get("section_id")
            }
            provider_duration_models_by_section = {
                section_id: {
                    "provider_duration_model": VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED,
                    "provider_fixed_duration_seconds": round(fixed_duration_by_section.get(section_id, 0.0), 6),
                }
                for section_id in kokoro_section_ids
            }
        if target_section_id:
            pacing_plan = self._section_remediation_plan(
                project=project,
                request=request,
                target_section_id=target_section_id,
                provider_key=provider_key,
                provider_hash=provider_hash,
                segmentation=segmentation,
                tts_profile_id=str(profile["profile_id"]),
                voice_id=str(voice["voice_id"]),
                language=str(voice["language"]),
            )
        else:
            pacing_plan = build_voice_pacing_plan(
                script=compiled.spoken_script,
                segmentation=segmentation,
                base_native_speaking_rate=speaking_rate,
                pacing_directive_resolver=lambda pacing_request: self.tts_settings.registry.pacing_directive(provider_key, pacing_request),
                source_script_revision_id=str(project.script_artifact_id),
                provider_duration_models_by_section=provider_duration_models_by_section,
            )
        pacing_policy_identity_sha256 = self._voice_pacing_policy_identity(provider_key, pacing_plan)
        settings_snapshot["voice_pacing_policy_identity_sha256"] = pacing_policy_identity_sha256
        target_section_segment_ids = [
            item["segment_id"] for item in segmentation["segments"] if item.get("section_id") == target_section_id
        ] if target_section_id else []
        if target_section_id and not target_section_segment_ids:
            raise AppError("VOICE_PACING_REMEDIATION_INVALID", "The requested Voice section was not found.", 404, retryable=False)
        # segmentation_input_sha256 is the immutable segmentation-only identity
        # produced by segment_narration(). Voice-first spoken/pacing material is
        # fenced independently by the raw synthesis identities below.
        raw_synthesis_identity = raw_synthesis_identity_sha256(
            provider_identity=provider_identity,
            settings_snapshot=settings_snapshot,
            pacing_plan=pacing_plan,
        )
        settings_snapshot["raw_synthesis_identity_sha256"] = raw_synthesis_identity
        settings_snapshot["pacing_identity_sha256"] = pacing_plan["pacing_identity_sha256"]
        settings_hash = canonical_hash(settings_snapshot)
        raw_segment_synthesis_hashes = {
            segment["segment_id"]: raw_segment_synthesis_identity_sha256(
                provider_identity=provider_identity,
                settings_snapshot=settings_snapshot,
                pacing_plan=pacing_plan,
                segment=segment,
                synthesis_context_sha256=synthesis_context_hashes.get(segment["segment_id"]),
            )
            for segment in segmentation["segments"]
        }
        raw_segment_material_hashes = {
            segment["segment_id"]: raw_segment_synthesis_material_identity_sha256(
                provider_identity=provider_identity,
                settings_snapshot=settings_snapshot,
                pacing_plan=pacing_plan,
                segment=segment,
                synthesis_context_sha256=synthesis_context_hashes.get(segment["segment_id"]),
            )
            for segment in segmentation["segments"]
        }

        reusable = (
            self._reusable_segments(project, segmentation, raw_synthesis_identity, provider_hash, synthesis_context_hashes, raw_segment_synthesis_hashes)
            if operation_type in {"generate_missing", "regenerate_section"}
            else {}
        )
        if target_section_id:
            target_set = set(target_section_segment_ids)
            reusable = {segment_id: entry for segment_id, entry in reusable.items() if segment_id not in target_set}
        scheduled_ids = [item["segment_id"] for item in segmentation["segments"] if item["segment_id"] not in reusable]
        if operation_type == "regenerate_section":
            scheduled_ids = target_section_segment_ids

        task_identity: dict[str, tuple[str, int]] = {}
        for segment in segmentation["segments"]:
            if segment["segment_id"] not in scheduled_ids:
                continue
            history = self.segment_tasks.list_for_project_segment(project_id, segment["segment_id"])
            task_identity[segment["segment_id"]] = (
                generate_id("voice_segment_task"),
                max((item.attempt_no for item in history), default=0) + 1,
            )

        complete_segments: list[dict[str, Any]] = []
        for segment in segmentation["segments"]:
            segment_id = segment["segment_id"]
            pacing_section = pacing_section_for_segment(pacing_plan, segment)
            if segment_id in scheduled_ids:
                task_id, _ = task_identity[segment_id]
                complete_segments.append(
                    self._with_synthesis_context_hash(
                        {
                            "segment_id": segment_id,
                            "segment_order": segment["segment_order"],
                            "section_id": segment["section_id"],
                            "source_start": segment["source_start"],
                            "source_end": segment["source_end"],
                            "exact_text_sha256": segment["text_sha256"],
                            "source_kind": "regenerated_target" if operation_type == "regenerate_section" else "synthesis_required",
                            "segment_audio_artifact_id": None,
                            "source_voice_segment_task_id": task_id,
                            "sample_count": None,
                            "duration_seconds": None,
                            "sha256": None,
                            "raw_synthesis_identity_sha256": raw_synthesis_identity,
                            "raw_segment_synthesis_identity_sha256": raw_segment_synthesis_hashes[segment_id],
                            "raw_segment_material_identity_sha256": raw_segment_material_hashes[segment_id],
                            "target_narration_wpm": pacing_section["planned_narration_wpm"],
                            "provider_pacing_control": pacing_section["provider_pacing_control"],
                            "provider_pacing_value": pacing_section["provider_pacing_value"],
                            "provider_pacing_policy_version": pacing_section["provider_pacing_policy_version"],
                            "provider_pacing_clamped": pacing_section["provider_pacing_clamped"],
                        },
                        synthesis_context_hashes.get(segment_id),
                    )
                )
                continue
            reused = reusable.get(segment_id)
            if not isinstance(reused, dict):
                raise AppError("VOICE_SEGMENT_AUDIO_INVALID", "Reusable Voice segment identity is missing.", 409, retryable=False)
            source_task_id = reused.get("source_voice_segment_task_id") or reused.get("segment_task_id")
            artifact_id = reused.get("segment_audio_artifact_id")
            source_task = self.segment_tasks.get(str(source_task_id)) if isinstance(source_task_id, str) else None
            artifact = self.artifact_rows.get(str(artifact_id)) if isinstance(artifact_id, str) else None
            if source_task is None or artifact is None:
                raise AppError("VOICE_SEGMENT_AUDIO_INVALID", "Reusable Voice segment ownership is missing.", 409, retryable=False)
            complete_segments.append(
                self._with_synthesis_context_hash(
                    {
                        "segment_id": segment_id,
                        "segment_order": segment["segment_order"],
                        "section_id": segment["section_id"],
                        "source_start": segment["source_start"],
                        "source_end": segment["source_end"],
                        "exact_text_sha256": segment["text_sha256"],
                        "source_kind": "reused_segment_artifact",
                        "segment_audio_artifact_id": artifact.artifact_id,
                        "source_voice_segment_task_id": source_task.voice_segment_task_id,
                        "sample_count": source_task.sample_count,
                        "duration_seconds": source_task.duration_seconds,
                        "sha256": source_task.sha256,
                        "raw_synthesis_identity_sha256": raw_synthesis_identity,
                        "raw_segment_synthesis_identity_sha256": raw_segment_synthesis_hashes[segment_id],
                        "raw_segment_material_identity_sha256": raw_segment_material_hashes[segment_id],
                        "target_narration_wpm": pacing_section["planned_narration_wpm"],
                        "provider_pacing_control": pacing_section["provider_pacing_control"],
                        "provider_pacing_value": pacing_section["provider_pacing_value"],
                        "provider_pacing_policy_version": pacing_section["provider_pacing_policy_version"],
                        "provider_pacing_clamped": pacing_section["provider_pacing_clamped"],
                    },
                    synthesis_context_hashes.get(segment_id),
                )
            )

        carried_manual_pacing_acceptances: list[dict[str, Any]] = []
        if target_section_id:
            source_operation_id = request.get("source_remediation_operation_id")
            source_operation = self.operations.get(str(source_operation_id)) if isinstance(source_operation_id, str) else None
            source_report = (
                source_operation.result_json.get("pacing_remediation_report")
                if source_operation is not None and isinstance(source_operation.result_json, dict)
                else None
            )
            source_records = (
                source_operation.result_json.get(VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY)
                if source_operation is not None and isinstance(source_operation.result_json, dict)
                else None
            )
            if source_operation is not None:
                carried_manual_pacing_acceptances = carry_forward_manual_pacing_acceptances(
                    source_report=source_report if isinstance(source_report, dict) else None,
                    accepted_records=source_records if isinstance(source_records, list) else None,
                    complete_segments=self._snapshot_entries(source_operation, result=True),
                    source_script_revision_id=str(source_operation.script_artifact_id),
                    provider_identity_sha256=source_operation.provider_identity_sha256,
                    exclude_section_id=target_section_id,
                )

        now = utc_now_iso()
        job = Job(
            job_id=generate_id("job"),
            job_type="voice_generation_v4",
            status=JobStatus.QUEUED,
            progress=0,
            artifact_type="narration_audio",
            target_type="project",
            target_id=project_id,
            attempt=1,
            retryable=False,
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            completed_at=None,
        )
        self.jobs.create(job)
        operation_id = generate_id("voice_operation")
        operation = VoiceGenerationOperation(
            operation_id=operation_id,
            job_id=job.job_id,
            project_id=project_id,
            operation_type=operation_type,
            status="queued",
            script_artifact_id=str(project.script_artifact_id),
            architecture_version=VOICE_ARCHITECTURE_VERSION,
            expected_narration_audio_artifact_id=expected_audio,
            expected_narration_word_timing_map_artifact_id=expected_timing,
            voice_preset=voice_preset,
            tts_profile_id=profile["profile_id"],
            voice_id=voice["voice_id"],
            language=voice["language"],
            speaking_rate=speaking_rate,
            provider_identity_json=provider_identity,
            provider_identity_sha256=provider_hash,
            generation_settings_snapshot_json=settings_snapshot,
            generation_settings_snapshot_sha256=settings_hash,
            segmentation_version=SEGMENTATION_VERSION,
            segmentation_input_sha256=segmentation["segmentation_input_sha256"],
            segment_count=len(segmentation["segments"]),
            request_json={
                "schema_version": "4.0",
                "segmentation": segmentation,
                "scheduled_segment_ids": scheduled_ids,
                "target_section_id": target_section_id,
                "source_remediation_operation_id": request.get("source_remediation_operation_id") if target_section_id else None,
                "expected_remediation_report_sha256": request.get("expected_remediation_report_sha256") if target_section_id else None,
                "script_section_pacing": pacing_plan,
                "voice_first_segment_payloads": deepcopy(voice_first_segment_payloads),
                "spoken_compilation_sha256": compiled.compilation_sha256,
                "complete_segments": deepcopy(complete_segments),
            },
            result_json={
                "submitted_count": len(scheduled_ids),
                "reused_count": len(segmentation["segments"]) - len(scheduled_ids),
                "complete_segments": deepcopy(complete_segments),
                **(
                    {VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY: carried_manual_pacing_acceptances}
                    if carried_manual_pacing_acceptances
                    else {}
                ),
            },
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        self.operations.create(operation)
        for segment in segmentation["segments"]:
            if segment["segment_id"] not in scheduled_ids:
                continue
            task_id, attempt_no = task_identity[segment["segment_id"]]
            self.segment_tasks.create(
                VoiceSegmentTask(
                    voice_segment_task_id=task_id,
                    operation_id=operation_id,
                    job_id=job.job_id,
                    project_id=project_id,
                    segment_id=segment["segment_id"],
                    segment_order=segment["segment_order"],
                    section_id=segment["section_id"],
                    source_start=segment["source_start"],
                    source_end=segment["source_end"],
                    text_sha256=segment["text_sha256"],
                    exact_text=segment["exact_text"],
                    status="queued",
                    attempt_no=attempt_no,
                    provider=provider_key,
                    provider_task_id=None,
                    provider_status=None,
                    provider_submitted_at=None,
                    voice_id=voice["voice_id"],
                    language=voice["language"],
                    speaking_rate=provider_control_value_for_segment(pacing_plan, segment),
                    sample_rate_hz=int(profile["sample_rate_hz"]),
                    output_format=str(profile["output_format"]),
                    provider_timing_json=None,
                    provider_result_metadata_json=None,
                    segment_audio_artifact_id=None,
                    duration_seconds=None,
                    sample_count=None,
                    sha256=None,
                    mime_type=None,
                    error_code=None,
                    error_message=None,
                    error_details_json=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
            )

        self._validate_operation_integrity(operation)
        self.session.flush()
        VoiceDurableWorkingRepository(self.session).schedule_operation(operation_id=operation_id, now_iso=now)
        self.session.commit()
        return VoiceJobSubmissionResponse(
            job_id=job.job_id,
            operation_id=operation_id,
            status="queued",
            segment_count=len(segmentation["segments"]),
            submitted_count=len(scheduled_ids),
            reused_count=len(segmentation["segments"]) - len(scheduled_ids),
        ).model_dump(mode="json")

    def cancel_job(self, project_id: str, job_id: str) -> dict[str, Any]:
        operation = self.operations.get_by_job_id(job_id)
        job = self.jobs.get(job_id)
        if operation is None or job is None or operation.project_id != project_id or job.target_id != project_id:
            raise AppError("VOICE_JOB_NOT_FOUND", "Voice Job was not found for this project.", 404, retryable=False)
        if operation.status in {"completed", "failed", "superseded", "cancelled"}:
            return self.job_status(project_id, job_id)
        now = utc_now_iso()
        repository = VoiceDurableWorkingRepository(self.session)
        repository.request_cancel(operation_id=operation.operation_id, now_iso=now)
        VoiceJobProjectionService(self.session).project_operation(operation_id=operation.operation_id, now_iso=now)
        self.session.commit()
        return self.job_status(project_id, job_id)

    def job_status(self, project_id: str, job_id: str) -> dict[str, Any]:
        operation = self.operations.get_by_job_id(job_id)
        job = self.jobs.get(job_id)
        if operation is None or job is None or operation.project_id != project_id:
            raise AppError("VOICE_JOB_NOT_FOUND", "Voice generation Job was not found.", 404, retryable=False)

        tasks = self.segment_tasks.list_for_operation(operation.operation_id)
        complete_entries = self._snapshot_entries(operation, result=True)
        completed_count = sum(isinstance(item.get("segment_audio_artifact_id"), str) for item in complete_entries)
        synthesis_progress = min(89, int(completed_count * 89 / max(1, operation.segment_count)))
        progress = 100 if operation.status == "completed" else synthesis_progress

        active = VoiceJobProjectionService(self.session).active_status(operation_id=operation.operation_id)
        provider = active.provider
        action = active.action
        retry_count = active.retry_count
        max_attempts = active.max_attempts
        next_retry_at = active.next_retry_at
        problem = active.problem

        # Synthesis task state is no longer authoritative once Task 2/3 begins.
        # Project finalization/alignment explicitly so the UI does not report a
        # completed regeneration as still "Regenerating" or sit at a synthetic 90%.
        if operation.status == "finalizing":
            result_json = operation.result_json if isinstance(operation.result_json, dict) else {}
            final_sections = result_json.get("final_sections")
            if not isinstance(final_sections, list) or not final_sections:
                progress = 90
                provider = None
                action = "finalize"
                retry_count = 0
                max_attempts = 0
                next_retry_at = None
            else:
                alignment_tasks = list(
                    self.session.scalars(
                        select(VoiceAlignmentTask)
                        .where(VoiceAlignmentTask.operation_id == operation.operation_id)
                        .order_by(VoiceAlignmentTask.section_order)
                    )
                )
                if not alignment_tasks:
                    progress = 91
                    provider = "elevenlabs"
                    action = "alignment_queue"
                    retry_count = 0
                    max_attempts = 0
                    next_retry_at = None
                else:
                    alignment_total = len(alignment_tasks)
                    alignment_completed = sum(task.status == "completed" for task in alignment_tasks)
                    progress = min(98, 91 + int(alignment_completed * 8 / max(1, alignment_total)))
                    failed_alignment = next((task for task in alignment_tasks if task.status == "failed"), None)
                    unknown_alignment = next((task for task in alignment_tasks if task.status == "submit_outcome_unknown"), None)
                    submitting_alignment = next((task for task in alignment_tasks if task.status == "submitting"), None)
                    retry_wait_alignment = next(
                        (
                            task
                            for task in alignment_tasks
                            if task.status == "queued" and int(task.retry_count or 0) > 0 and task.next_reconcile_at
                        ),
                        None,
                    )
                    queued_alignment = next((task for task in alignment_tasks if task.status == "queued"), None)
                    focus_alignment = (
                        failed_alignment
                        or unknown_alignment
                        or submitting_alignment
                        or retry_wait_alignment
                        or queued_alignment
                    )
                    if focus_alignment is not None:
                        provider = str(focus_alignment.provider or "elevenlabs")
                        retry_count = int(focus_alignment.retry_count or 0)
                        max_attempts = int(focus_alignment.max_attempts or 0)
                        next_retry_at = (
                            str(focus_alignment.next_reconcile_at)
                            if focus_alignment.status == "queued" and focus_alignment.next_reconcile_at
                            else None
                        )

                    if failed_alignment is not None:
                        action = "alignment_failed"
                        problem = {
                            "error_code": str(failed_alignment.error_code or "VOICE_ALIGNMENT_FAILED"),
                            "message": str(failed_alignment.error_message or "Forced Alignment failed."),
                            "details": failed_alignment.error_details_json,
                        }
                    elif unknown_alignment is not None:
                        action = "submission_outcome_unknown"
                        problem = {
                            "error_code": str(unknown_alignment.error_code or "VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN"),
                            "message": str(
                                unknown_alignment.error_message
                                or "Forced Alignment may have been submitted; automatic resubmission is disabled."
                            ),
                            "details": unknown_alignment.error_details_json,
                        }
                    elif alignment_completed == alignment_total:
                        progress = 99
                        provider = None
                        action = "candidate_finalize"
                        retry_count = 0
                        max_attempts = 0
                        next_retry_at = None
                    elif retry_wait_alignment is not None:
                        action = "alignment_retry_wait"
                    elif submitting_alignment is not None:
                        action = "forced_alignment"
                    else:
                        action = "alignment_queue"

        elif operation.status in {"failed", "superseded", "cancelled"}:
            progress = max(synthesis_progress, min(99, int(job.progress or 0)))

        return VoiceJobStatusResponse(
            job_id=job.job_id,
            operation_id=operation.operation_id,
            operation_type=operation.operation_type,
            status=operation.status,
            progress=progress,
            segment_count=operation.segment_count,
            completed_segment_count=completed_count,
            failed_segment_count=sum(item.status == "failed" for item in tasks),
            error_code=operation.error_code,
            error_message=operation.error_message,
            current_segment_id=active.current_segment_id,
            provider=provider,
            action=action,
            retry_count=retry_count,
            max_attempts=max_attempts,
            next_retry_at=next_retry_at,
            cancel_requested=active.cancel_requested,
            problem=problem,
        ).model_dump(mode="json")

    @staticmethod
    def _synthesis_context_hashes(
        segmentation: dict[str, Any],
        provider_identity: dict[str, Any],
    ) -> dict[str, str | None]:
        segments = segmentation.get("segments")
        if not isinstance(segments, list):
            return {}
        execution_identity = provider_identity.get("execution_identity") if isinstance(provider_identity, dict) else None
        continuity_mode = execution_identity.get("continuity_mode") if isinstance(execution_identity, dict) else None
        if continuity_mode not in {"previous_next_text_v1", "canonical_spoken_previous_next_text_v1"}:
            return {str(item.get("segment_id")): None for item in segments if isinstance(item, dict)}
        hashes: dict[str, str | None] = {}
        for index, item in enumerate(segments):
            if not isinstance(item, dict):
                continue
            previous_item = segments[index - 1] if index > 0 else None
            next_item = segments[index + 1] if index + 1 < len(segments) else None
            hashes[str(item.get("segment_id"))] = canonical_hash({
                "schema_version": 1,
                "continuity_mode": continuity_mode,
                "previous_text": previous_item.get("exact_text") if isinstance(previous_item, dict) else None,
                "next_text": next_item.get("exact_text") if isinstance(next_item, dict) else None,
            })
        return hashes

    def _section_remediation_plan(
        self,
        *,
        project,
        request: dict[str, Any],
        target_section_id: str,
        provider_key: str,
        provider_hash: str,
        segmentation: dict[str, Any],
        tts_profile_id: str,
        voice_id: str,
        language: str,
    ) -> dict[str, Any]:
        source_operation_id = request.get("source_remediation_operation_id")
        expected_report_hash = request.get("expected_remediation_report_sha256")
        if not isinstance(source_operation_id, str) or not source_operation_id:
            raise AppError("VOICE_PACING_REMEDIATION_INVALID", "Voice section regeneration requires a source remediation operation.", 409, retryable=False)
        source_operation = self.operations.get(source_operation_id)
        if (
            source_operation is None
            or source_operation.project_id != project.project_id
            or source_operation.status != "failed"
            or source_operation.error_code != "VOICE_PACING_REGENERATION_REQUIRED"
        ):
            raise AppError("VOICE_PACING_REMEDIATION_STALE", "The source Voice remediation operation is not actionable.", 409, retryable=False)
        report = source_operation.result_json.get("pacing_remediation_report") if isinstance(source_operation.result_json, dict) else None
        if not isinstance(report, dict):
            report = source_operation.error_details_json
        if (
            not isinstance(report, dict)
            or report.get("status") != "regeneration_required"
            or report.get("remediation_report_sha256") != expected_report_hash
        ):
            raise AppError("VOICE_PACING_REMEDIATION_STALE", "The Voice remediation report identity changed.", 409, retryable=False)
        source_pacing_plan = source_operation.request_json.get("script_section_pacing") if isinstance(source_operation.request_json, dict) else None
        source_actionability = classify_voice_pacing_remediation_actionability(
            provider_key=provider_key,
            source_pacing_plan=source_pacing_plan,
            expected_script_revision_id=str(project.script_artifact_id),
            segmentation=segmentation,
            report=report,
            section=None,
        )
        if source_actionability.remediation_block_reason == VOICE_REMEDIATION_BLOCK_KOKORO_DURATION_MODEL_INVALID:
            raise AppError(
                "VOICE_PACING_REMEDIATION_STALE",
                source_actionability.message or "The source Voice remediation plan uses an incompatible Kokoro duration model. Generate a fresh Voice before regenerating individual sections.",
                409,
                retryable=False,
            )
        if (
            source_actionability.remediation_block_reason == VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID
            or not isinstance(source_actionability.source_pacing_identity_sha256, str)
        ):
            raise AppError("VOICE_PACING_REMEDIATION_STALE", "The source Voice frozen pacing plan is invalid.", 409, retryable=False)
        source_pacing_identity = source_actionability.source_pacing_identity_sha256
        source_settings_snapshot = (
            source_operation.generation_settings_snapshot_json
            if isinstance(source_operation.generation_settings_snapshot_json, dict)
            else {}
        )
        if (
            source_operation.script_artifact_id != project.script_artifact_id
            or source_operation.expected_narration_audio_artifact_id != project.narration_audio_artifact_id
            or source_operation.expected_narration_word_timing_map_artifact_id != project.narration_word_timing_map_artifact_id
            or source_operation.tts_profile_id != tts_profile_id
            or source_operation.voice_id != voice_id
            or source_operation.language != language
            or source_operation.provider_identity_sha256 != provider_hash
            or source_settings_snapshot.get("voice_pacing_policy_identity_sha256")
            != self._voice_pacing_policy_identity(provider_key, source_pacing_plan)
            or source_operation.segmentation_input_sha256 != segmentation.get("segmentation_input_sha256")
            or report.get("segmentation_input_sha256") != segmentation.get("segmentation_input_sha256")
            or report.get("provider_identity_sha256") != provider_hash
            or report.get("pacing_identity_sha256") != source_pacing_identity
        ):
            raise AppError("VOICE_PACING_REMEDIATION_STALE", "The Voice remediation lineage is stale. Start a fresh Voice generation before regenerating a section.", 409, retryable=False)
        sections = report.get("sections")
        matches = [item for item in sections if isinstance(item, dict) and item.get("section_id") == target_section_id] if isinstance(sections, list) else []
        section = matches[0] if len(matches) == 1 else None
        section_actionability = classify_voice_pacing_remediation_actionability(
            provider_key=provider_key,
            source_pacing_plan=source_pacing_plan,
            expected_script_revision_id=str(project.script_artifact_id),
            segmentation=segmentation,
            report=report,
            section=section,
        )
        if section is None or not section_actionability.regeneration_supported:
            raise AppError("VOICE_PACING_REMEDIATION_UNSUPPORTED", "The target Voice section has no supported provider pacing recommendation.", 409, retryable=False)
        from app.modules.v4.voice_pacing import apply_section_pacing_recommendation
        return apply_section_pacing_recommendation(
            source_pacing_plan,
            section_id=target_section_id,
            recommended_provider_pacing_value=float(section["recommended_provider_pacing_value"]),
            recommendation_policy_version=str(section["recommendation_policy_version"]),
            recommendation_clamped=bool(section.get("recommendation_clamped")),
            speed_response_exponent_used=(
                float(section["speed_response_exponent_used"])
                if isinstance(section.get("speed_response_exponent_used"), (int, float))
                else None
            ),
            response_model_source=section.get("response_model_source") if isinstance(section.get("response_model_source"), str) else None,
            response_model_identity=section.get("response_model_identity") if isinstance(section.get("response_model_identity"), str) else None,
        )

    @staticmethod
    def _voice_pacing_policy_identity(provider_key: str, pacing_plan: dict[str, Any]) -> str:
        return voice_pacing_policy_identity(provider_key, pacing_plan)

    def _latest_pacing_remediation_report(self, project) -> dict[str, Any] | None:
        """Project only the current remediation head for the approved Voice lineage.

        Operations are returned newest-first. Once a newer operation on the same
        Script/approved-Voice lineage exists, an older failed remediation must not
        resurface in the workspace. Manual pacing acceptance deliberately resumes
        the exact failed operation as ``finalizing`` while retaining its report,
        so that report remains the transient projection authority until the resumed
        operation completes.
        """
        for operation in self.operations.list_for_project(project.project_id):
            if operation.script_artifact_id != project.script_artifact_id:
                continue
            if (
                operation.expected_narration_audio_artifact_id != project.narration_audio_artifact_id
                or operation.expected_narration_word_timing_map_artifact_id != project.narration_word_timing_map_artifact_id
            ):
                continue

            # A cancelled remediation attempt did not advance the candidate, so
            # the previous actionable report may still be offered to the owner.
            if operation.status == "cancelled":
                continue

            report = operation.result_json.get("pacing_remediation_report") if isinstance(operation.result_json, dict) else None
            if not isinstance(report, dict):
                report = operation.error_details_json
            actionable_report = isinstance(report, dict) and report.get("status") == "regeneration_required"

            if operation.status == "finalizing" and actionable_report:
                return report
            if (
                operation.status == "failed"
                and operation.error_code == "VOICE_PACING_REGENERATION_REQUIRED"
                and actionable_report
            ):
                return report

            # A live single-section regeneration has not yet produced its new
            # pacing report. Keep projecting the exact source remediation report
            # so accepted/pass sibling sections do not collapse to "pending"
            # during provider work. The target section remains operation-owned.
            if (
                operation.operation_type == "regenerate_section"
                and operation.status in {"queued", "running", "finalizing"}
                and isinstance(operation.request_json, dict)
            ):
                source_operation_id = operation.request_json.get("source_remediation_operation_id")
                source_operation = self.operations.get(str(source_operation_id)) if isinstance(source_operation_id, str) else None
                source_report = (
                    source_operation.result_json.get("pacing_remediation_report")
                    if source_operation is not None and isinstance(source_operation.result_json, dict)
                    else None
                )
                if (
                    source_operation is not None
                    and source_operation.project_id == project.project_id
                    and source_operation.script_artifact_id == project.script_artifact_id
                    and source_operation.expected_narration_audio_artifact_id == project.narration_audio_artifact_id
                    and source_operation.expected_narration_word_timing_map_artifact_id == project.narration_word_timing_map_artifact_id
                    and isinstance(source_report, dict)
                    and source_report.get("status") == "regeneration_required"
                ):
                    return source_report

            # Any other newer operation on this exact lineage supersedes older
            # remediation reports. Falling through would resurrect stale warnings.
            return None
        return None
    def _completed_task_output_identity_valid(self, operation, task, segment: dict[str, Any]) -> bool:
        if (
            task.status != "completed"
            or not task.segment_audio_artifact_id
            or task.sample_count is None
            or task.duration_seconds is None
            or task.sha256 is None
            or task.segment_id != segment.get("segment_id")
            or task.segment_order != segment.get("segment_order")
            or task.section_id != segment.get("section_id")
            or task.source_start != segment.get("source_start")
            or task.source_end != segment.get("source_end")
            or task.text_sha256 != segment.get("text_sha256")
            or task.exact_text != segment.get("exact_text")
            or task.voice_id != operation.voice_id
            or task.language != operation.language
            or task.speaking_rate != self._expected_segment_speaking_rate(operation, segment)
            or task.sample_rate_hz != int(operation.generation_settings_snapshot_json.get("sample_rate_hz", 0))
            or task.output_format != str(operation.generation_settings_snapshot_json.get("output_format", ""))
            or task.provider != operation.provider_identity_json.get("provider")
        ):
            return False
        source_operation = self.operations.get(task.operation_id)
        if source_operation is None or source_operation.project_id != operation.project_id or source_operation.script_artifact_id != operation.script_artifact_id:
            return False
        source_entries = self._snapshot_entries(source_operation, result=True)
        source_entry = next((entry for entry in source_entries if entry.get("segment_id") == segment.get("segment_id")), None)
        source_segmentation = source_operation.request_json.get("segmentation") if isinstance(source_operation.request_json, dict) else None
        source_segments = source_segmentation.get("segments") if isinstance(source_segmentation, dict) else None
        source_segment = next(
            (
                item
                for item in source_segments
                if isinstance(item, dict) and item.get("segment_id") == segment.get("segment_id")
            ),
            None,
        ) if isinstance(source_segments, list) else None
        source_context_hash = source_entry.get("synthesis_context_sha256") if isinstance(source_entry, dict) and isinstance(source_entry.get("synthesis_context_sha256"), str) else None
        current_segmentation = operation.request_json.get("segmentation") if isinstance(operation.request_json, dict) else None
        current_context_hash = (
            self._synthesis_context_hashes(current_segmentation, operation.provider_identity_json).get(str(segment.get("segment_id")))
            if isinstance(current_segmentation, dict)
            else None
        )
        current_expected_identity = self._expected_raw_segment_synthesis_identity(
            operation,
            segment,
            current_context_hash,
        )
        if (
            not isinstance(source_entry, dict)
            or not isinstance(source_segment, dict)
            or source_operation.tts_profile_id != operation.tts_profile_id
            or source_operation.voice_id != operation.voice_id
            or source_operation.language != operation.language
            or source_operation.provider_identity_sha256 != operation.provider_identity_sha256
            or source_operation.segmentation_version != operation.segmentation_version
            or source_operation.segmentation_input_sha256 != operation.segmentation_input_sha256
            or (current_context_hash is not None and source_context_hash != current_context_hash)
            or not self._source_segment_identity_matches_current(
                source_operation=source_operation,
                source_segment=source_segment,
                source_context_sha256=source_context_hash,
                stored_identity=source_entry.get("raw_segment_synthesis_identity_sha256"),
                current_expected_identity=current_expected_identity,
            )
        ):
            return False
        artifact = self.artifact_rows.get(task.segment_audio_artifact_id)
        return bool(
            artifact is not None
            and artifact.status == ArtifactStatus.READY
            and artifact.deleted_at is None
            and artifact.artifact_type == ArtifactType.AUDIO
            and artifact.sha256 == task.sha256
            and artifact.logical_id.startswith(f"project/{operation.project_id}/voice/operations/")
            and f"/segments/{task.segment_id}/" in artifact.logical_id
        )

    def _completed_task_output_valid(self, operation, task, segment: dict[str, Any]) -> bool:
        """Validate persisted segment bytes after the durable identity fence."""
        if not self._completed_task_output_identity_valid(operation, task, segment):
            return False
        artifact = self.artifact_rows.get(str(task.segment_audio_artifact_id))
        if artifact is None:
            return False
        from app.modules.v4.voice_audio import CANONICAL_SAMPLE_RATE, validate_canonical_wav_bytes

        try:
            info = validate_canonical_wav_bytes(
                self.artifacts.resolve_file_path(artifact).read_bytes(),
                maximum_bytes=get_settings().tts_provider_max_output_bytes,
            )
        except (AppError, OSError):
            return False
        return (
            info.sha256 == task.sha256
            and info.sample_count == task.sample_count
            and abs(info.duration_seconds - task.duration_seconds) <= (1 / CANONICAL_SAMPLE_RATE)
        )

    def _reusable_segments(
        self,
        project,
        segmentation: dict[str, Any],
        raw_synthesis_hash: str,
        provider_hash: str,
        synthesis_context_hashes: dict[str, str | None],
        raw_segment_synthesis_hashes: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        del raw_synthesis_hash
        reusable: dict[str, dict[str, Any]] = {}
        segments_by_id = {str(item["segment_id"]): item for item in segmentation.get("segments", []) if isinstance(item, dict)}
        for source_operation in self.operations.list_for_project(project.project_id):
            if source_operation.script_artifact_id != project.script_artifact_id:
                continue
            if source_operation.provider_identity_sha256 != provider_hash or source_operation.segmentation_input_sha256 != segmentation.get("segmentation_input_sha256"):
                continue
            source_segmentation = source_operation.request_json.get("segmentation") if isinstance(source_operation.request_json, dict) else None
            source_segments = source_segmentation.get("segments") if isinstance(source_segmentation, dict) else None
            source_segments_by_id = {
                str(item["segment_id"]): item
                for item in source_segments
                if isinstance(item, dict) and isinstance(item.get("segment_id"), str)
            } if isinstance(source_segments, list) else {}
            entries = self._snapshot_entries(source_operation, result=True)
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                segment_id = entry.get("segment_id")
                segment = segments_by_id.get(str(segment_id))
                source_segment = source_segments_by_id.get(str(segment_id))
                if segment is None or str(segment_id) in reusable:
                    continue
                expected_context = synthesis_context_hashes.get(str(segment_id))
                source_context = entry.get("synthesis_context_sha256") if isinstance(entry.get("synthesis_context_sha256"), str) else None
                stored_identity = entry.get("raw_segment_synthesis_identity_sha256")
                current_identity = raw_segment_synthesis_hashes.get(str(segment_id))
                if not isinstance(source_segment, dict):
                    continue
                source_identity_matches_current = self._source_segment_identity_matches_current(
                    source_operation=source_operation,
                    source_segment=source_segment,
                    source_context_sha256=source_context,
                    stored_identity=stored_identity,
                    current_expected_identity=current_identity,
                )
                if not source_identity_matches_current:
                    continue
                if expected_context is not None and entry.get("synthesis_context_sha256") != expected_context:
                    continue
                task_id = entry.get("source_voice_segment_task_id")
                artifact_id = entry.get("segment_audio_artifact_id")
                task = self.segment_tasks.get(str(task_id)) if isinstance(task_id, str) else None
                artifact = self.artifact_rows.get(str(artifact_id)) if isinstance(artifact_id, str) else None
                if (
                    task is None or artifact is None or task.status != "completed"
                    or task.segment_audio_artifact_id != artifact.artifact_id
                    or task.text_sha256 != segment.get("text_sha256") or task.exact_text != segment.get("exact_text")
                    or artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None
                    or artifact.artifact_type != ArtifactType.AUDIO or artifact.sha256 != task.sha256
                ):
                    continue
                reusable[str(segment_id)] = deepcopy(entry)
            if len(reusable) == len(segments_by_id):
                break
        return reusable

    def approve(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        project = self._project_required(project_id)
        candidate_audio_id = request.get("candidate_narration_audio_artifact_id")
        candidate_timing_id = request.get("candidate_narration_word_timing_map_artifact_id")
        expected_audio_id = request.get("expected_narration_audio_artifact_id")
        expected_timing_id = request.get("expected_narration_word_timing_map_artifact_id")
        if not isinstance(candidate_audio_id, str) or not isinstance(candidate_timing_id, str):
            raise AppError("VOICE_CANDIDATE_NOT_FOUND", "An exact draft Voice candidate pair is required.", 404, retryable=False)
        if (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id) != (expected_audio_id, expected_timing_id):
            raise AppError("VOICE_APPROVAL_CONFLICT", "The approved Voice pair changed before approval.", 409, retryable=False)

        operation = next(
            (
                item
                for item in self.operations.list_for_project(project_id)
                if item.status == "completed"
                and isinstance(item.result_json, dict)
                and isinstance(item.result_json.get("draft_candidate"), dict)
                and item.result_json["draft_candidate"].get("narration_audio_artifact_id") == candidate_audio_id
                and item.result_json["draft_candidate"].get("narration_word_timing_map_artifact_id") == candidate_timing_id
            ),
            None,
        )
        if operation is None:
            raise AppError("VOICE_CANDIDATE_NOT_FOUND", "The requested draft Voice candidate pair was not found.", 404, retryable=False)
        candidate = operation.result_json["draft_candidate"]
        if candidate.get("reviewable") is not True:
            raise AppError("VOICE_CANDIDATE_STALE", "The requested Voice candidate is not reviewable.", 409, retryable=False)
        if operation.script_artifact_id != project.script_artifact_id or candidate.get("source_script_artifact_id") != project.script_artifact_id:
            raise AppError("VOICE_CANDIDATE_STALE", "The Voice candidate Script lineage is stale.", 409, retryable=False)
        if (operation.expected_narration_audio_artifact_id, operation.expected_narration_word_timing_map_artifact_id) != (expected_audio_id, expected_timing_id):
            raise AppError("VOICE_CANDIDATE_STALE", "The Voice candidate was generated against a different approved Voice pair.", 409, retryable=False)
        channel = self.session.get(Channel, project.channel_id)
        current_profile = channel.voice_profile_json if channel is not None and isinstance(channel.voice_profile_json, dict) else None
        if self._candidate_requires_channel_voice_profile(operation) and (
            current_profile is None or canonical_hash(current_profile) != candidate.get("channel_voice_profile_sha256")
        ):
            raise AppError("VOICE_CANDIDATE_STALE", "The Channel Voice configuration changed after candidate generation.", 409, retryable=False)

        audio = self.artifact_rows.get(candidate_audio_id)
        timing = self.artifact_rows.get(candidate_timing_id)
        if (
            audio is None
            or timing is None
            or audio.status != ArtifactStatus.READY
            or timing.status != ArtifactStatus.READY
            or audio.deleted_at is not None
            or timing.deleted_at is not None
            or audio.artifact_type != ArtifactType.AUDIO
            or timing.artifact_type != ArtifactType.JSON
            or audio.logical_id != f"project/{project_id}/voice/narration-audio"
            or timing.logical_id != f"project/{project_id}/voice/narration-word-timing-map"
            or audio.sha256 != candidate.get("narration_audio_sha256")
            or timing.sha256 != candidate.get("narration_word_timing_map_sha256")
        ):
            raise AppError("VOICE_CANDIDATE_STALE", "The draft Voice candidate Artifact lineage is invalid.", 409, retryable=False)
        audio_bytes = self.artifacts.resolve_file_path(audio).read_bytes()
        from app.modules.v4.voice_audio import validate_canonical_wav_bytes
        audio_info = validate_canonical_wav_bytes(audio_bytes)
        if audio_info.sha256 != audio.sha256 or audio_info.sample_count != int(candidate.get("total_sample_count") or -1):
            raise AppError("VOICE_CANDIDATE_STALE", "The draft Voice candidate audio identity is invalid.", 409, retryable=False)
        timing_bytes = self.artifacts.resolve_file_path(timing).read_bytes()
        if hashlib.sha256(timing_bytes).hexdigest() != timing.sha256:
            raise AppError("VOICE_CANDIDATE_STALE", "The draft Voice timing bytes do not match their frozen identity.", 409, retryable=False)
        try:
            timing_payload = json.loads(timing_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("VOICE_CANDIDATE_STALE", "The draft Voice timing Artifact is malformed.", 409, retryable=False) from exc
        if (
            not isinstance(timing_payload, dict)
            or timing_payload.get("source_script_artifact_id") != project.script_artifact_id
            or timing_payload.get("source_narration_audio_artifact_id") != candidate_audio_id
            or timing_payload.get("sample_rate_hz") != 48_000
            or timing_payload.get("total_sample_count") != audio_info.sample_count
            or self._contains_scene_timing_ownership(timing_payload)
        ):
            raise AppError("VOICE_CANDIDATE_STALE", "The draft NarrationWordTimingMap lineage is invalid.", 409, retryable=False)

        current_audio_artifact = self.artifact_rows.get_current_by_logical_id(audio.logical_id)
        current_timing_artifact = self.artifact_rows.get_current_by_logical_id(timing.logical_id)
        expected_audio_artifact_current_id = (
            current_audio_artifact.artifact_id if current_audio_artifact is not None else None
        )
        expected_timing_artifact_current_id = (
            current_timing_artifact.artifact_id if current_timing_artifact is not None else None
        )

        updated_at = utc_now_iso()
        if not self.artifact_rows.compare_and_set_current_artifact(
            logical_id=audio.logical_id,
            new_artifact_id=audio.artifact_id,
            expected_current_artifact_id=expected_audio_artifact_current_id,
        ):
            self.session.rollback()
            raise AppError("VOICE_APPROVAL_CONFLICT", "Narration Audio current Artifact changed before approval.", 409, retryable=False)
        if not self.artifact_rows.compare_and_set_current_artifact(
            logical_id=timing.logical_id,
            new_artifact_id=timing.artifact_id,
            expected_current_artifact_id=expected_timing_artifact_current_id,
        ):
            self.session.rollback()
            raise AppError("VOICE_APPROVAL_CONFLICT", "NarrationWordTimingMap current Artifact changed before approval.", 409, retryable=False)
        guard = VoiceOutputPairCommitGuard(
            script_artifact_id=str(project.script_artifact_id),
            previous_narration_audio_artifact_id=expected_audio_id,
            previous_narration_word_timing_map_artifact_id=expected_timing_id,
        )
        if not self.projects.compare_and_set_voice_output_pair(
            project_id=project_id,
            new_narration_audio_artifact_id=audio.artifact_id,
            new_narration_word_timing_map_artifact_id=timing.artifact_id,
            guard=guard,
            updated_at=updated_at,
        ):
            self.session.rollback()
            raise AppError("VOICE_APPROVAL_CONFLICT", "Voice approval lost its Project CAS.", 409, retryable=False)

        ProjectPipelineInvalidationService(self.session).invalidate_from_voice_approval(project)
        self.session.commit()
        return {
            "narration_audio_artifact_id": audio.artifact_id,
            "narration_word_timing_map_artifact_id": timing.artifact_id,
            "status": "approved",
        }

    def resolve_review_audio_artifact(self, project_id: str, artifact_id: str | None = None):
        project = self._project_required(project_id)
        allowed: set[str] = set()
        if project.narration_audio_artifact_id:
            allowed.add(str(project.narration_audio_artifact_id))
        draft = self._latest_reviewable_candidate(project_id, project.script_artifact_id)
        if isinstance(draft, tuple):
            _operation, candidate = draft
            candidate_audio = candidate.get("narration_audio_artifact_id")
            if isinstance(candidate_audio, str):
                allowed.add(candidate_audio)
        selected = artifact_id or project.narration_audio_artifact_id
        if not isinstance(selected, str) or selected not in allowed:
            raise AppError("VOICE_CANDIDATE_NOT_FOUND", "The requested Voice review audio is not an approved or current draft candidate.", 404, retryable=False)
        artifact = self.artifact_rows.get(selected)
        if artifact is None or artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None or artifact.artifact_type != ArtifactType.AUDIO:
            raise AppError("VOICE_CANDIDATE_NOT_FOUND", "The requested Voice review audio is unavailable.", 404, retryable=False)
        return artifact

    def get_workspace(self, project_id: str) -> dict[str, Any]:
        project = self._project_required(project_id)
        channel = self.session.get(Channel, project.channel_id)
        voice_profile = channel.voice_profile_json if channel is not None and isinstance(channel.voice_profile_json, dict) else None
        selected_generation_provider: str | None = None
        selected_voice_label: str | None = None
        kokoro_voice_catalog: list[dict[str, str]] = []
        kokoro_readiness_issue: dict[str, str] | None = None
        kokoro_probe_profile = self._kokoro_provider_profile(voice_id="catalog_probe", language="en")
        try:
            catalog = self.tts_settings.registry.list_voices(KokoroProviderAdapter.provider_key, kokoro_probe_profile)
            kokoro_voice_catalog = [
                {
                    "voice_id": voice.voice_id,
                    "display_name": voice.display_name,
                    "language": voice.language,
                }
                for voice in catalog
            ]
            if not catalog:
                kokoro_readiness_issue = {
                    "error_code": "TTS_KOKORO_VOICE_CATALOG_EMPTY",
                    "message": "Kokoro did not report any available voices.",
                }
                kokoro_readiness = None
            else:
                selected_probe_profile = VoiceProviderProfile(
                    profile_id=kokoro_probe_profile.profile_id,
                    provider_key=kokoro_probe_profile.provider_key,
                    enabled=kokoro_probe_profile.enabled,
                    display_name=kokoro_probe_profile.display_name,
                    voice_id=catalog[0].voice_id,
                    language=catalog[0].language,
                    speaking_rate=kokoro_probe_profile.speaking_rate,
                    requested_sample_rate_hz=kokoro_probe_profile.requested_sample_rate_hz,
                    requested_output_format=kokoro_probe_profile.requested_output_format,
                    provider_options=deepcopy(kokoro_probe_profile.provider_options),
                    maximum_segment_characters=kokoro_probe_profile.maximum_segment_characters,
                )
                kokoro_readiness = self.tts_settings.registry.readiness(selected_probe_profile)
            if kokoro_readiness is not None and not kokoro_readiness.ready:
                kokoro_readiness_issue = {
                    "error_code": kokoro_readiness.issue_code or "VOICE_PROVIDER_NOT_READY",
                    "message": kokoro_readiness.message or "Kokoro Local voice generation is not ready.",
                }
        except AppError as exc:
            kokoro_readiness_issue = {"error_code": exc.error_code, "message": exc.message}
        alignment_readiness_issue = self._elevenlabs_alignment_readiness_issue()
        issues: list[dict[str, str]] = []
        if not project.script_artifact_id:
            issues.append({"error_code": "VOICE_SCRIPT_REQUIRED", "message": "Current Script is required."})
        active_rows = self.operations.list_active_for_project(project_id)
        active = active_rows[0] if active_rows else None
        if active is not None:
            issues.append({"error_code": "VOICE_OPERATION_ACTIVE", "message": "A Voice operation is already active."})

        report = self._latest_pacing_remediation_report(project)
        draft_pair = self._latest_reviewable_candidate(project_id, project.script_artifact_id)
        draft_operation = draft_pair[0] if isinstance(draft_pair, tuple) else None
        draft_candidate = draft_pair[1] if isinstance(draft_pair, tuple) else None
        source_operation = None
        if isinstance(report, dict) and isinstance(report.get("source_operation_id"), str):
            source_operation = self.operations.get(str(report["source_operation_id"]))
        if source_operation is None and active is not None and active.script_artifact_id == project.script_artifact_id:
            source_operation = active
        if source_operation is None:
            source_operation = draft_operation
        if source_operation is None:
            source_operation = next((item for item in self.operations.list_for_project(project_id) if item.status in {"finalizing", "completed"} and item.script_artifact_id == project.script_artifact_id), None)
        selected_generation_provider = self._operation_generation_provider(source_operation)
        if selected_generation_provider == KokoroProviderAdapter.provider_key:
            selected_voice_label = self._operation_generation_voice_id(source_operation)
        elif voice_profile and voice_profile.get("voice_id"):
            selected_voice_label = str(voice_profile["voice_id"])

        segmentation = source_operation.request_json.get("segmentation") if source_operation is not None and isinstance(source_operation.request_json, dict) else None
        pacing = source_operation.request_json.get("script_section_pacing") if source_operation is not None and isinstance(source_operation.request_json, dict) else None
        segment_rows = segmentation.get("segments", []) if isinstance(segmentation, dict) else []
        report_sections = {str(item.get("section_id")): item for item in report.get("sections", []) if isinstance(item, dict)} if isinstance(report, dict) else {}
        manual_acceptance_records = (
            source_operation.result_json.get(VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY)
            if source_operation is not None and isinstance(source_operation.result_json, dict)
            else None
        )
        manual_acceptance_records = (
            [item for item in manual_acceptance_records if isinstance(item, dict)]
            if isinstance(manual_acceptance_records, list)
            else []
        )
        report_integrity_valid = validate_remediation_report_integrity(report if isinstance(report, dict) else None)
        final_sections = source_operation.result_json.get("final_sections") if source_operation is not None and isinstance(source_operation.result_json, dict) else None
        final_sections_ready = isinstance(final_sections, list) and bool(final_sections)
        sections: list[dict[str, Any]] = []
        for item in pacing.get("sections", []) if isinstance(pacing, dict) else []:
            if not isinstance(item, dict):
                continue
            remediation = report_sections.get(str(item.get("section_id")))
            manually_accepted = report_integrity_valid and exact_manual_pacing_acceptance_matches(
                remediation,
                report.get("remediation_report_sha256") if isinstance(report, dict) else None,
                manual_acceptance_records,
            )
            actionability = classify_voice_pacing_remediation_actionability(
                provider_key=selected_generation_provider,
                source_pacing_plan=pacing,
                expected_script_revision_id=str(project.script_artifact_id),
                segmentation=segmentation,
                report=report if isinstance(report, dict) else None,
                section=remediation if isinstance(remediation, dict) else None,
                manually_accepted=manually_accepted,
            )
            provider_recommendation_visible = (
                isinstance(remediation, dict)
                and actionability.remediation_block_reason is None
            )
            regeneration_supported = actionability.regeneration_supported
            sections.append({
                "section_id": str(item["section_id"]),
                "section_order": int(item.get("section_order") or len(sections) + 1),
                "segment_ids": list(item.get("segment_ids") or []),
                "target_wpm": int(item.get("planned_narration_wpm")) if item.get("planned_narration_wpm") is not None else None,
                "raw_wpm": float(remediation.get("raw_wpm")) if isinstance(remediation, dict) and remediation.get("raw_wpm") is not None else None,
                "pacing_status": "pass" if manually_accepted else ("regeneration_required" if isinstance(remediation, dict) and remediation.get("status") == "regeneration_required" else ("pass" if final_sections_ready or (source_operation and source_operation.status == "completed") else "pending")),
                "correction_relative": remediation.get("correction_relative") if isinstance(remediation, dict) else None,
                "current_provider_pacing_value": remediation.get("current_provider_pacing_value") if isinstance(remediation, dict) else item.get("provider_pacing_value"),
                "recommended_provider_pacing_value": remediation.get("recommended_provider_pacing_value") if provider_recommendation_visible else None,
                "recommendation_policy_version": remediation.get("recommendation_policy_version") if provider_recommendation_visible else None,
                "recommendation_reason": remediation.get("recommendation_reason") if provider_recommendation_visible else None,
                "remediation_identity": remediation.get("remediation_identity") if isinstance(remediation, dict) else None,
                "remediation_report_sha256": report.get("remediation_report_sha256") if isinstance(remediation, dict) and isinstance(report, dict) else None,
                "source_remediation_operation_id": report.get("source_operation_id") if isinstance(remediation, dict) and isinstance(report, dict) else None,
                "regeneration_supported": regeneration_supported,
                "can_regenerate": bool(regeneration_supported and active is None),
                "can_accept_pacing_deviation": actionability.manual_acceptance_supported,
                "remediation_block_reason": actionability.remediation_block_reason,
                "issue": remediation.get("issue") if isinstance(remediation, dict) else None,
            })

        result_entries = self._snapshot_entries(source_operation, result=True) if source_operation is not None else []
        entries_by_id = {str(item.get("segment_id")): item for item in result_entries if isinstance(item, dict)}
        segments = []
        for item in segment_rows:
            if not isinstance(item, dict):
                continue
            entry = entries_by_id.get(str(item.get("segment_id")))
            segments.append({
                **{key: item[key] for key in ("segment_id", "segment_order", "section_id", "source_start", "source_end", "exact_text", "text_sha256")},
                "status": "completed" if isinstance(entry, dict) and isinstance(entry.get("segment_audio_artifact_id"), str) else "pending",
                "segment_task_id": entry.get("source_voice_segment_task_id") if isinstance(entry, dict) else None,
                "segment_audio_artifact_id": entry.get("segment_audio_artifact_id") if isinstance(entry, dict) else None,
                "duration_seconds": entry.get("duration_seconds") if isinstance(entry, dict) else None,
                "sha256": entry.get("sha256") if isinstance(entry, dict) else None,
                "audio_url": None,
                "timing_source": None,
                "error": None,
                "can_regenerate": False,
            })

        alignment_operation = active if active is not None and active.script_artifact_id == project.script_artifact_id else draft_operation
        alignment_tasks = []
        if alignment_operation is not None:
            alignment_tasks = list(self.session.scalars(select(VoiceAlignmentTask).where(VoiceAlignmentTask.operation_id == alignment_operation.operation_id).order_by(VoiceAlignmentTask.section_order)))
        alignment_status = None
        unknown_sections: list[str] = []
        if alignment_tasks:
            unknown_sections = [task.section_id for task in alignment_tasks if task.status == "submit_outcome_unknown"]
            if unknown_sections:
                alignment_status = "submit_outcome_unknown"
            elif all(task.status == "completed" for task in alignment_tasks):
                alignment_status = "completed"
            elif any(task.status == "failed" for task in alignment_tasks):
                alignment_status = "failed"
            elif any(task.status == "submitting" for task in alignment_tasks):
                alignment_status = "submitting"
            elif any(task.status == "completed" for task in alignment_tasks):
                alignment_status = "running"
            else:
                alignment_status = "queued"
        elif source_operation is not None and isinstance(source_operation.result_json, dict):
            raw_alignment_status = source_operation.result_json.get("alignment_status")
            if isinstance(raw_alignment_status, str):
                alignment_status = raw_alignment_status

        review_artifact_id = draft_candidate.get("narration_audio_artifact_id") if isinstance(draft_candidate, dict) else project.narration_audio_artifact_id
        review_artifact = self.artifact_rows.get(str(review_artifact_id)) if isinstance(review_artifact_id, str) else None
        review_duration = float(review_artifact.duration_sec) if review_artifact is not None and review_artifact.duration_sec is not None else None
        review_sha = review_artifact.sha256 if review_artifact is not None else None
        unresolved_remediation_sections = [
            section for section in sections if section.get("pacing_status") == "regeneration_required"
        ]
        current_problem = None
        if unknown_sections:
            current_problem = {"error_code": "VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN", "message": "Forced Alignment may have been submitted; automatic resubmission is disabled."}
        elif unresolved_remediation_sections:
            if any(section.get("regeneration_supported") is True for section in unresolved_remediation_sections):
                current_problem = {"error_code": "VOICE_PACING_REGENERATION_REQUIRED", "message": "One or more Voice sections require explicit provider resynthesis."}
            elif any(section.get("can_accept_pacing_deviation") is True for section in unresolved_remediation_sections):
                current_problem = {"error_code": "VOICE_PACING_REVIEW_REQUIRED", "message": "One or more Voice sections can be accepted without provider resynthesis."}
            else:
                current_problem = {"error_code": "VOICE_PACING_REMEDIATION_UNSUPPORTED", "message": "No unresolved Voice section has an actionable provider remediation. Generate a fresh Voice."}

        approval_eligible = bool(
            active is None
            and isinstance(draft_candidate, dict)
            and draft_candidate.get("reviewable") is True
            and draft_operation is not None
            and draft_operation.script_artifact_id == project.script_artifact_id
            and (draft_operation.expected_narration_audio_artifact_id, draft_operation.expected_narration_word_timing_map_artifact_id) == (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id)
            and (
                not self._candidate_requires_channel_voice_profile(draft_operation)
                or (
                    voice_profile is not None
                    and canonical_hash(voice_profile) == draft_candidate.get("channel_voice_profile_sha256")
                )
            )
        )
        actions = ["generate_all", "generate_missing"]
        if any(section.get("can_regenerate") is True for section in unresolved_remediation_sections):
            actions.append("regenerate_section")
        if unknown_sections:
            actions.append("retry_alignment")
        if approval_eligible:
            actions.append("approve")
        elevenlabs_generation_presets: dict[str, dict[str, Any]] = {}
        missing_voice_profile_issue = {
            "error_code": "VOICE_CHANNEL_PROFILE_REQUIRED",
            "message": "Channel Voice Profile is required for ElevenLabs generation.",
        }
        for preset, model_id in ACTIVE_VOICE_PRESETS.items():
            issue: dict[str, str] | None = missing_voice_profile_issue
            ready = False
            if voice_profile is not None:
                try:
                    preset_profile = self._elevenlabs_generation_provider_profile(
                        project=project,
                        profile_payload=voice_profile,
                        voice_preset=preset,
                    )
                    preset_readiness = self.tts_settings.registry.readiness(preset_profile)
                    issue = self._provider_readiness_issue(
                        preset_readiness,
                        fallback_code="VOICE_PROVIDER_NOT_READY",
                        fallback_message="The selected ElevenLabs generation preset is not ready.",
                    )
                    ready = issue is None
                except AppError as exc:
                    issue = {"error_code": exc.error_code, "message": exc.message}
                    ready = False
            elevenlabs_generation_presets[preset] = {
                "ready": ready,
                "issue": issue,
                "model_id": model_id,
            }
        elevenlabs_generation_ready = all(item["ready"] for item in elevenlabs_generation_presets.values())
        elevenlabs_generation_issue = None if elevenlabs_generation_ready else next(
            (item["issue"] for item in elevenlabs_generation_presets.values() if item.get("issue")),
            missing_voice_profile_issue,
        )
        kokoro_generation_ready = kokoro_readiness_issue is None and bool(kokoro_voice_catalog)
        kokoro_issue = (
            alignment_readiness_issue
            if kokoro_generation_ready and alignment_readiness_issue is not None
            else kokoro_readiness_issue
        )

        return {
            "project_id": project_id,
            "readiness": {
                "status": "ready" if not issues else "not_ready",
                "issues": issues,
                "profile_id": "channel_elevenlabs_pvc" if voice_profile else None,
                "provider": "elevenlabs" if voice_profile else None,
                "voice_id": str(voice_profile.get("voice_id")) if voice_profile and voice_profile.get("voice_id") else None,
                "requires_ffmpeg": True,
            },
            "script_artifact_id": project.script_artifact_id,
            "selected_generation_provider": selected_generation_provider,
            "selected_tts_profile_id": "channel_elevenlabs_pvc" if voice_profile else None,
            "selected_voice_id": str(voice_profile.get("voice_id")) if voice_profile and voice_profile.get("voice_id") else None,
            "selected_voice_label": selected_voice_label,
            "available_generation_providers": [
                {
                    "generation_provider": VoiceFirstElevenLabsProviderAdapter.provider_key,
                    "display_name": "ElevenLabs",
                    "ready": elevenlabs_generation_ready,
                    "issue": elevenlabs_generation_issue,
                    "generation_ready": elevenlabs_generation_ready,
                    "generation_issue": elevenlabs_generation_issue,
                    "generation_presets": elevenlabs_generation_presets,
                    "alignment_ready": alignment_readiness_issue is None,
                    "alignment_issue": alignment_readiness_issue,
                    "alignment_provider": "elevenlabs",
                },
                {
                    "generation_provider": KokoroProviderAdapter.provider_key,
                    "display_name": "Kokoro Local",
                    "ready": kokoro_generation_ready and alignment_readiness_issue is None,
                    "issue": kokoro_issue,
                    "generation_ready": kokoro_generation_ready,
                    "generation_issue": kokoro_readiness_issue,
                    "alignment_ready": alignment_readiness_issue is None,
                    "alignment_issue": alignment_readiness_issue,
                    "alignment_provider": "elevenlabs",
                },
            ],
            "kokoro_voice_catalog": kokoro_voice_catalog,
            "speaking_rate": None,
            "active_operation": ({
                "operation_id": active.operation_id,
                "job_id": active.job_id,
                "status": active.status,
                "operation_type": active.operation_type,
                "target_section_id": (
                    active.request_json.get("target_section_id")
                    if active.operation_type == "regenerate_section" and isinstance(active.request_json, dict)
                    else None
                ),
            } if active else None),
            "current_narration_audio_artifact_id": project.narration_audio_artifact_id,
            "audio_duration_seconds": review_duration,
            "audio_mime_type": "audio/wav" if review_artifact is not None else None,
            "audio_sha256": review_sha,
            "audio_url": None,
            "current_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            "draft_narration_audio_artifact_id": draft_candidate.get("narration_audio_artifact_id") if isinstance(draft_candidate, dict) else None,
            "draft_narration_word_timing_map_artifact_id": draft_candidate.get("narration_word_timing_map_artifact_id") if isinstance(draft_candidate, dict) else None,
            "draft_operation_id": draft_operation.operation_id if draft_operation is not None else None,
            "draft_generation_provider": self._operation_generation_provider(draft_operation),
            "draft_generation_voice_id": self._operation_generation_voice_id(draft_operation),
            "draft_voice_preset": draft_operation.voice_preset if draft_operation is not None else None,
            "draft_candidate_identity_sha256": draft_candidate.get("candidate_identity_sha256") if isinstance(draft_candidate, dict) else None,
            "draft_audio_url": (f"/api/v4/projects/{project_id}/voice/audio?artifact_id={draft_candidate.get('narration_audio_artifact_id')}" if isinstance(draft_candidate, dict) else None),
            "alignment_status": alignment_status,
            "alignment_completed_section_count": len([task for task in alignment_tasks if task.status == "completed"]),
            "alignment_total_section_count": len(alignment_tasks),
            "alignment_submit_outcome_unknown_section_ids": unknown_sections,
            "timing_map_status": "draft_candidate" if isinstance(draft_candidate, dict) else ("approved_current" if project.narration_word_timing_map_artifact_id else None),
            "timing_source": "elevenlabs_forced_alignment_v1" if isinstance(draft_candidate, dict) or project.narration_word_timing_map_artifact_id else None,
            "current_output_problem": current_problem,
            "sections": sections,
            "segments": segments,
            "approval_eligible": approval_eligible,
            "available_actions": actions,
        }

    def _latest_reviewable_candidate(self, project_id: str, script_artifact_id: str | None):
        for operation in self.operations.list_for_project(project_id):
            candidate = operation.result_json.get("draft_candidate") if isinstance(operation.result_json, dict) else None
            if (
                operation.status == "completed"
                and operation.script_artifact_id == script_artifact_id
                and isinstance(candidate, dict)
                and candidate.get("reviewable") is True
            ):
                return operation, candidate
        return None

    @staticmethod
    def _contains_scene_timing_ownership(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                "scene" in str(key).lower()
                or "beat" in str(key).lower()
                or DurableVoiceService._contains_scene_timing_ownership(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(DurableVoiceService._contains_scene_timing_ownership(item) for item in value)
        return False
