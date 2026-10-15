from __future__ import annotations

from copy import deepcopy
import io
import wave
from types import SimpleNamespace

import pytest

from kokoro.runtime_response_calibration import KOKORO_FLOW_VERSION
from sqlalchemy import select

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.time import utc_now_iso
from app.db.enums import ArtifactType, JobStatus
from app.db.models import Channel, Job, Project, Setting, VoiceGenerationOperation, VoiceSegmentTask
from app.db.session import build_session_factory
from app.modules.platform.external_script_analysis import ExternalScriptAnalysisService
from app.modules.v4 import durable_voice_service as durable_voice_module
from app.modules.v4 import voice_provider_adapters as voice_provider_adapters_module
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.voice_first_policy import ACTIVE_VOICE_PRESETS
from app.modules.v4.voice_audio import CANONICAL_SAMPLE_RATE, CANONICAL_SAMPLE_WIDTH, validate_canonical_wav_bytes
from app.modules.v4.voice_pacing import (
    VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED,
    analyze_section_pacing,
    canonical_hash,
    raw_segment_synthesis_identity_sha256,
    rehash_remediation_report,
    validate_remediation_report_integrity,
)
from app.modules.v4.voice_pacing_manual_acceptance import (
    VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY,
    VoicePacingManualAcceptanceService,
    apply_manual_pacing_acceptances,
)
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from app.modules.v4.voice_provider_registry import CalibratedVoiceProviderRegistry
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderPacingDirective,
    VoiceProviderPacingRequest,
    VoiceProviderPacingRecommendation,
    VoiceProviderProfile,
    VoiceProviderReadiness,
    VoiceProviderVoice,
)
from app.modules.v4.voice_pacing_calibration_settings import (
    KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY,
    VoicePacingCalibrationSettingsService,
)
from app.repositories import ArtifactRepository, SettingsRepository
from app.services.artifacts import ArtifactService
from tests.support.kokoro_runtime_response import runtime_response_measurement
from tests.support.external_authoring import (
    combined_script_analysis_output,
    create_channel_project,
    external_import_payload,
    import_and_select_topic,
)


def _script_request() -> dict:
    return {
        "user_input": {
            "target_duration_minutes": 1,
            "pov_style": "second person with measured explanatory passages",
            "avoid": [],
            "section_count_mode": "llm_decides",
            "pacing_mode": "medium",
            "narration_wpm": {"min": 145, "max": 165},
        },
        "selected_topic_idea_id": "idea_001",
    }


def _create_script_ready_project(session) -> str:
    _channel_id, project_id = create_channel_project(session)
    import_and_select_topic(session, project_id)
    service = ExternalScriptAnalysisService(session)
    package = service.build_prompt(project_id, _script_request())
    service.import_result(project_id, external_import_payload(package, combined_script_analysis_output()))
    return project_id


class _FakeKokoroRegistry:
    def __init__(
        self,
        *,
        readiness: VoiceProviderReadiness | None = None,
        pacing_value: float = 1.0,
        pacing_values_by_section: dict[str, float] | None = None,
    ) -> None:
        self.readiness_result = readiness or VoiceProviderReadiness(status="ready")
        self.pacing_value = pacing_value
        self.pacing_values_by_section = pacing_values_by_section or {}
        self.readiness_profiles: list[VoiceProviderProfile] = []
        self.execution_profiles: list[VoiceProviderProfile] = []

    def list_voices(self, provider_key: str, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]:
        assert provider_key == KokoroProviderAdapter.provider_key
        return (
            VoiceProviderVoice(voice_id="af_heart", display_name="AF Heart", language="en-us"),
            VoiceProviderVoice(voice_id="bf_alice", display_name="BF Alice", language="en-gb"),
        )

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        self.readiness_profiles.append(profile)
        return self.readiness_result

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, object]:
        self.execution_profiles.append(profile)
        return {
            "schema_version": 1,
            "provider_key": profile.provider_key,
            "test_runtime": "fake_kokoro",
        }

    def pacing_directive(
        self,
        provider_key: str,
        request: VoiceProviderPacingRequest,
    ) -> VoiceProviderPacingDirective:
        assert provider_key == KokoroProviderAdapter.provider_key
        value = self.pacing_values_by_section.get(request.section_id, self.pacing_value)
        return VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=value,
            target_narration_wpm=request.target_narration_wpm,
            policy_version=KOKORO_FLOW_VERSION,
            clamped=False,
            value_range=(0.5, 2.0),
        )


class _FakeWorkspaceGenerationRegistry(_FakeKokoroRegistry):
    def __init__(self, *, elevenlabs_by_model: dict[str, VoiceProviderReadiness]) -> None:
        super().__init__()
        self.elevenlabs_by_model = elevenlabs_by_model

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        self.readiness_profiles.append(profile)
        if profile.provider_key == "elevenlabs":
            return self.elevenlabs_by_model.get(str(profile.provider_options.get("model_id")), VoiceProviderReadiness(status="ready"))
        return self.readiness_result


def _alignment_resolver(*, ready: bool):
    def require_api_key() -> str:
        if ready:
            return "test-elevenlabs-key"
        raise AppError("ELEVENLABS_API_KEY_NOT_CONFIGURED", "ElevenLabs API key is not configured.", 409, retryable=False)

    return SimpleNamespace(require_api_key=require_api_key)


def _silent_wav(sample_count: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(CANONICAL_SAMPLE_WIDTH)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(b"\x00" * sample_count * CANONICAL_SAMPLE_WIDTH)
    return output.getvalue()


def _pacing_report(operation: VoiceGenerationOperation, *, target_section_id: str) -> dict:
    pacing = operation.request_json["script_section_pacing"]
    report_sections = []
    for section in pacing["sections"]:
        is_target = section["section_id"] == target_section_id
        report_sections.append(
            {
                "section_id": section["section_id"],
                "section_order": section["section_order"],
                "segment_ids": list(section["segment_ids"]),
                "planned_narration_wpm": section["planned_narration_wpm"],
                "status": "regeneration_required" if is_target else "pass",
                "feedback_supported": is_target,
                "raw_wpm": float(section["planned_narration_wpm"]) * (1.10 if is_target else 1.0),
                "target_wpm": section["planned_narration_wpm"],
                "correction_relative": 0.08 if is_target else 0.0,
                "wpm_deviation_percent": 10.0 if is_target else 0.0,
                "provider_pacing_control": section["provider_pacing_control"],
                "provider_pacing_value": section["provider_pacing_value"],
                "provider_pacing_policy_version": section["provider_pacing_policy_version"],
                "provider_pacing_clamped": section["provider_pacing_clamped"],
                "current_provider_pacing_control": section["provider_pacing_control"],
                "current_provider_pacing_value": section["provider_pacing_value"],
                "recommended_provider_pacing_value": 0.91 if is_target else section["provider_pacing_value"],
                "recommendation_policy_version": KOKORO_FLOW_VERSION if is_target else section["provider_pacing_policy_version"],
                "recommendation_clamped": False,
                "recommendation_reason": None,
                "speed_response_exponent": section.get("speed_response_exponent", 1.0),
                "speed_response_exponent_used": section.get("speed_response_exponent", 1.0) if is_target else None,
                "response_model_source": section.get("response_model_source") if is_target else None,
                "response_model_identity": section.get("response_model_identity") if is_target else None,
                "response_lineage_sha256": "f" * 64,
                "issue_code": "VOICE_PACING_WPM_OUT_OF_RANGE" if is_target else None,
            }
        )
    report = {
        "schema_version": "voice_section_pacing_analysis_v2",
        "status": "regeneration_required",
        "source_operation_id": operation.operation_id,
        "source_script_revision_id": str(operation.script_artifact_id),
        "segmentation_input_sha256": operation.segmentation_input_sha256,
        "provider_identity_sha256": operation.provider_identity_sha256,
        "generation_settings_snapshot_sha256": operation.generation_settings_snapshot_sha256,
        "pacing_identity_sha256": pacing["pacing_identity_sha256"],
        "policy_version": pacing["policy_version"],
        "sections": report_sections,
    }
    return rehash_remediation_report(report)


def _materialize_completed_source_operation(
    session,
    operation: VoiceGenerationOperation,
    *,
    target_section_id: str,
    corrupt_sibling_identity: bool = False,
    native_wpm_at_rate_1: float | None = None,
) -> dict:
    artifacts = ArtifactService(session, ArtifactRepository(session))
    now = utc_now_iso()
    tasks = {
        task.segment_id: task
        for task in session.scalars(
            select(VoiceSegmentTask).where(VoiceSegmentTask.operation_id == operation.operation_id)
        ).all()
    }
    segments_by_id = {
        segment["segment_id"]: segment
        for segment in operation.request_json["segmentation"]["segments"]
    }
    segment_counts_by_section: dict[str, int] = {}
    for segment in segments_by_id.values():
        section_id = str(segment["section_id"])
        segment_counts_by_section[section_id] = segment_counts_by_section.get(section_id, 0) + 1
    pacing_by_section = {
        str(section["section_id"]): section
        for section in operation.request_json["script_section_pacing"]["sections"]
    }
    source_entries = []
    for entry in operation.request_json["complete_segments"]:
        segment_id = entry["segment_id"]
        task = tasks.get(segment_id)
        if task is None:
            source_entries.append(dict(entry))
            continue
        if native_wpm_at_rate_1 is None:
            samples = CANONICAL_SAMPLE_RATE // 8 + int(entry["segment_order"])
        else:
            pacing_section = pacing_by_section[str(entry["section_id"])]
            speech_at_rate_1 = 60.0 * int(pacing_section["actual_word_count"]) / native_wpm_at_rate_1
            rate = float(pacing_section["provider_pacing_value"])
            beta = float(pacing_section.get("speed_response_exponent", 1.0))
            total_seconds = float(pacing_section.get("provider_fixed_duration_seconds", 0.0)) + speech_at_rate_1 / (rate ** beta)
            samples = max(1, round(CANONICAL_SAMPLE_RATE * total_seconds / segment_counts_by_section[str(entry["section_id"])]))
        wav_bytes = _silent_wav(samples)
        wav_info = validate_canonical_wav_bytes(wav_bytes)
        staged = artifacts.stage_artifact_revision(
            logical_id=f"project/{operation.project_id}/voice/operations/{operation.operation_id}/segments/{segment_id}/raw",
            artifact_type=ArtifactType.AUDIO,
            filename=f"{segment_id}.wav",
            content=wav_bytes,
            source_job_id=operation.job_id,
            provider_source="fake_kokoro",
        )
        staged.artifact.duration_sec = wav_info.duration_seconds
        task.status = "completed"
        task.segment_audio_artifact_id = staged.artifact_id
        task.duration_seconds = wav_info.duration_seconds
        task.sample_count = wav_info.sample_count
        task.sha256 = wav_info.sha256
        task.mime_type = "audio/wav"
        task.completed_at = now
        task.updated_at = now
        raw_identity = entry["raw_segment_synthesis_identity_sha256"]
        source_entries.append(
            {
                **entry,
                "segment_audio_artifact_id": staged.artifact_id,
                "source_voice_segment_task_id": task.voice_segment_task_id,
                "sample_count": wav_info.sample_count,
                "duration_seconds": wav_info.duration_seconds,
                "sha256": wav_info.sha256,
                "raw_segment_synthesis_identity_sha256": raw_identity,
            }
        )
    if corrupt_sibling_identity:
        sibling = next(item for item in source_entries if item["section_id"] != target_section_id)
        sibling["raw_segment_synthesis_identity_sha256"] = "0" * 64
    report = _pacing_report(operation, target_section_id=target_section_id)
    # request_json is the immutable submission snapshot. Completed artifact
    # ownership is projected only into result_json as tasks finish.
    operation.result_json = {**operation.result_json, "complete_segments": source_entries, "pacing_remediation_report": report}
    operation.status = "failed"
    operation.error_code = "VOICE_PACING_REGENERATION_REQUIRED"
    operation.error_message = "One or more Voice sections require explicit provider resynthesis."
    operation.error_details_json = report
    operation.completed_at = now
    operation.updated_at = now
    job = session.get(Job, operation.job_id)
    assert job is not None
    job.status = JobStatus.FAILED
    job.error_code = operation.error_code
    job.error_message = operation.error_message
    job.error_details_json = report
    job.completed_at = now
    job.finished_at = now
    job.updated_at = now
    session.flush()
    return report


def _submit_completed_kokoro_source(
    session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    corrupt_sibling_identity: bool = False,
    registry: _FakeKokoroRegistry | None = None,
):
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    project_id = _create_script_ready_project(session)
    project = session.get(Project, project_id)
    assert project is not None
    service = DurableVoiceService(session)
    service.tts_settings.registry = registry or _FakeKokoroRegistry()
    submitted = service.submit_generation(
        project_id,
        {
            "operation_type": "generate_all",
            "generation_provider": "kokoro_local",
            "generation_voice_id": "af_heart",
            "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
            "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
        },
    )
    operation = session.get(VoiceGenerationOperation, submitted["operation_id"])
    assert operation is not None
    assert len(operation.request_json["script_section_pacing"]["sections"]) >= 3
    target_section_id = operation.request_json["script_section_pacing"]["sections"][1]["section_id"]
    report = _materialize_completed_source_operation(
        session,
        operation,
        target_section_id=target_section_id,
        corrupt_sibling_identity=corrupt_sibling_identity,
    )
    session.commit()
    return project_id, target_section_id, operation.operation_id, report


def test_kokoro_material_identity_tamper_fails_operation_integrity(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _project_id, _target_section_id, source_operation_id, _report = _submit_completed_kokoro_source(session, monkeypatch)
        operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert operation is not None
        entries = [dict(item) for item in operation.result_json["complete_segments"]]
        entries[0]["raw_segment_material_identity_sha256"] = "0" * 64
        operation.result_json = {**operation.result_json, "complete_segments": entries}
        session.flush()

        with pytest.raises(AppError) as exc_info:
            DurableVoiceService(session)._validate_operation_integrity(operation)

        assert exc_info.value.error_code == "VOICE_OPERATION_INTEGRITY_INVALID"


def test_kokoro_production_segment_records_feed_response_lineage_and_history(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        analyses = []
        learned = None
        execution_sha = None

        for rate in (1.0, 1.44):
            service = DurableVoiceService(session)
            service.tts_settings.registry = _FakeKokoroRegistry(pacing_value=rate)
            submitted = service.submit_generation(
                project_id,
                {
                    "operation_type": "generate_all",
                    "generation_provider": "kokoro_local",
                    "generation_voice_id": "af_heart",
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
            )
            operation = session.get(VoiceGenerationOperation, submitted["operation_id"])
            assert operation is not None
            target_section_id = operation.request_json["script_section_pacing"]["sections"][1]["section_id"]
            _materialize_completed_source_operation(
                session,
                operation,
                target_section_id=target_section_id,
                native_wpm_at_rate_1=180.0,
            )
            service._validate_operation_integrity(operation)
            raw_bytes, records = service._ordered_segment_outputs(operation)
            assert [record["raw_segment_material_identity_sha256"] for record in records] == [
                entry["raw_segment_material_identity_sha256"] for entry in operation.result_json["complete_segments"]
            ]
            analysis = analyze_section_pacing(
                segment_bytes=raw_bytes,
                segment_records=records,
                segmentation=operation.request_json["segmentation"],
                pacing_plan=operation.request_json["script_section_pacing"],
                source_operation_id=operation.operation_id,
                source_script_revision_id=str(operation.script_artifact_id),
                provider_identity_sha256=operation.provider_identity_sha256,
                generation_settings_snapshot_sha256=operation.generation_settings_snapshot_sha256,
            )
            assert all(
                isinstance(section["response_lineage_sha256"], str)
                and len(section["response_lineage_sha256"]) == 64
                for section in analysis.report["sections"]
            )
            execution_sha = canonical_hash(operation.provider_identity_json["execution_identity"])
            recorded = VoicePacingCalibrationSettingsService(
                session,
                SettingsRepository(session),
            ).record_kokoro_pacing_observations(
                voice_id=operation.voice_id,
                operation_id=operation.operation_id,
                execution_identity_sha256=execution_sha,
                analysis_report=analysis.report,
            )
            analyses.append(analysis.report)

        assert {
            section["section_id"]: section["response_lineage_sha256"]
            for section in analyses[0]["sections"]
        } == {
            section["section_id"]: section["response_lineage_sha256"]
            for section in analyses[1]["sections"]
        }
        assert recorded is None
        stored = session.get(Setting, KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY)
        assert stored is not None
        assert stored.value_json["flow_version"] == KOKORO_FLOW_VERSION
        assert all(
            observation["execution_identity_sha256"] == execution_sha
            for observation in stored.value_json["entries"][0]["observations"]
        )


def test_fresh_kokoro_submit_uses_frozen_runtime_default_without_calibration_synthesis(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    monkeypatch.setattr(
        voice_provider_adapters_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("submit path must not launch calibration subprocesses")),
    )
    measurement = runtime_response_measurement(0.72)

    class FrozenRuntimeAdapter(KokoroProviderAdapter):
        def __init__(self) -> None:
            super().__init__(get_settings())
            self.identity = {
                "schema_version": 1,
                "provider_key": self.provider_key,
                "flow_version": KOKORO_FLOW_VERSION,
                "runtime": "frozen-marker-test",
                "runtime_response_calibration_sha256": canonical_hash(measurement),
            }
            self.identity_reads: list[dict[str, object]] = []

        def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
            _ = profile
            return VoiceProviderReadiness(status="ready")

        def list_voices(self, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]:
            _ = profile
            return (VoiceProviderVoice(voice_id="af_heart", display_name="AF Heart", language="en-us"),)

        def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, object]:
            _ = profile
            self.identity_reads.append(self.identity)
            return self.identity

        def runtime_response_calibration(self, profile: VoiceProviderProfile | None = None) -> dict[str, object]:
            _ = profile
            return measurement

        def measure_runtime_response(self) -> dict[str, object]:
            raise AssertionError("application-time calibration measurement must not run")

    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        registry = CalibratedVoiceProviderRegistry()
        adapter = FrozenRuntimeAdapter()
        registry.register(adapter)
        service = DurableVoiceService(session)
        service.tts_settings.registry = registry

        submitted = service.submit_generation(
            project_id,
            {
                "operation_type": "generate_all",
                "generation_provider": "kokoro_local",
                "generation_voice_id": "af_heart",
                "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            },
        )

        operation = session.get(VoiceGenerationOperation, submitted["operation_id"])
        assert operation is not None
        assert len(adapter.identity_reads) >= 2
        assert all(identity == adapter.identity for identity in adapter.identity_reads)
        assert operation.provider_identity_json["execution_identity"] == adapter.identity
        assert all(
            section["response_model_source"] == "runtime_calibrated_default"
            and section["speed_response_exponent"] == pytest.approx(0.72)
            for section in operation.request_json["script_section_pacing"]["sections"]
        )


def test_kokoro_submit_rejects_identity_change_during_calibration_planning(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    measurement = runtime_response_measurement(0.72)
    identity_a = {
        "schema_version": 1,
        "provider_key": "kokoro_local",
        "flow_version": KOKORO_FLOW_VERSION,
        "runtime": "runtime-a",
        "runtime_response_calibration_sha256": canonical_hash(measurement),
    }
    identity_b = {**identity_a, "runtime": "runtime-b"}

    class ChangingRuntimeAdapter(KokoroProviderAdapter):
        def __init__(self) -> None:
            super().__init__(get_settings())
            self.identities = [identity_a, identity_b]
            self.identity_calls = 0

        def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
            _ = profile
            return VoiceProviderReadiness(status="ready")

        def list_voices(self, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]:
            _ = profile
            return (VoiceProviderVoice(voice_id="af_heart", display_name="AF Heart", language="en-us"),)

        def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, object]:
            _ = profile
            identity = self.identities[min(self.identity_calls, len(self.identities) - 1)]
            self.identity_calls += 1
            return identity

        def runtime_response_calibration(self, profile: VoiceProviderProfile | None = None) -> dict[str, object]:
            _ = profile
            return measurement

    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        registry = CalibratedVoiceProviderRegistry()
        registry.register(ChangingRuntimeAdapter())
        service = DurableVoiceService(session)
        service.tts_settings.registry = registry

        with pytest.raises(AppError) as exc_info:
            service.submit_generation(
                project_id,
                {
                    "operation_type": "generate_all",
                    "generation_provider": "kokoro_local",
                    "generation_voice_id": "af_heart",
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
            )

        assert exc_info.value.error_code == "VOICE_PACING_CALIBRATION_IDENTITY_INVALID"
        assert service.operations.list_active_for_project(project_id) == []


@pytest.mark.parametrize(
    ("voice_id", "expected_language"),
    [("af_heart", "en-us"), ("bf_alice", "en-gb")],
)
def test_kokoro_generation_freezes_selected_catalog_voice_language(monkeypatch, voice_id: str, expected_language: str) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        registry = _FakeKokoroRegistry()
        service = DurableVoiceService(session)
        service.tts_settings.registry = registry

        submitted = service.submit_generation(
            project_id,
            {
                "operation_type": "generate_all",
                "generation_provider": "kokoro_local",
                "generation_voice_id": voice_id,
                "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            },
        )

        operation = session.get(VoiceGenerationOperation, submitted["operation_id"])
        assert operation is not None
        assert operation.voice_id == voice_id
        assert operation.language == expected_language
        assert operation.provider_identity_json["voice_id"] == voice_id
        assert operation.provider_identity_json["language"] == expected_language
        assert operation.generation_settings_snapshot_json["voice_id"] == voice_id
        assert operation.generation_settings_snapshot_json["language"] == expected_language
        for section in operation.request_json["script_section_pacing"]["sections"]:
            assert section["provider_duration_model"] == VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED
            assert section["provider_fixed_duration_seconds"] >= 0
        assert registry.readiness_profiles[-1].voice_id == voice_id
        assert registry.readiness_profiles[-1].language == expected_language
        task_languages = set(
            session.scalars(
                select(VoiceSegmentTask.language).where(VoiceSegmentTask.operation_id == operation.operation_id)
            ).all()
        )
        assert task_languages == {expected_language}


def test_kokoro_regenerate_section_reuses_siblings_and_resynthesizes_only_target(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        source_entries = {
            item["segment_id"]: item
            for item in source_operation.result_json["complete_segments"]
        }
        target_segment_ids = {
            segment["segment_id"]
            for segment in source_operation.request_json["segmentation"]["segments"]
            if segment["section_id"] == target_section_id
        }
        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        project = session.get(Project, project_id)
        assert project is not None

        submitted = service.submit_generation(
            project_id,
            {
                "operation_type": "regenerate_section",
                "source_remediation_operation_id": source_operation_id,
                "expected_remediation_report_sha256": report["remediation_report_sha256"],
                "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            },
            target_section_id=target_section_id,
        )

        regenerated = session.get(VoiceGenerationOperation, submitted["operation_id"])
        assert regenerated is not None
        service._validate_operation_integrity(regenerated)
        workspace = service.get_workspace(project_id)
        assert workspace["active_operation"]["target_section_id"] == target_section_id
        assert submitted["submitted_count"] == len(target_segment_ids)
        assert submitted["reused_count"] == len(source_entries) - len(target_segment_ids)
        assert set(regenerated.request_json["scheduled_segment_ids"]) == target_segment_ids

        regenerated_entries = {
            item["segment_id"]: item
            for item in regenerated.request_json["complete_segments"]
        }
        for segment in regenerated.request_json["segmentation"]["segments"]:
            segment_id = segment["segment_id"]
            source_entry = source_entries[segment_id]
            regenerated_entry = regenerated_entries[segment_id]
            if segment_id in target_segment_ids:
                assert regenerated_entry["source_kind"] == "regenerated_target"
                assert regenerated_entry["source_voice_segment_task_id"] != source_entry["source_voice_segment_task_id"]
                assert regenerated_entry["segment_audio_artifact_id"] is None
                assert regenerated_entry["raw_segment_synthesis_identity_sha256"] != source_entry["raw_segment_synthesis_identity_sha256"]
                continue
            assert regenerated_entry["source_kind"] == "reused_segment_artifact"
            assert regenerated_entry["source_voice_segment_task_id"] == source_entry["source_voice_segment_task_id"]
            assert regenerated_entry["segment_audio_artifact_id"] == source_entry["segment_audio_artifact_id"]
            assert regenerated_entry["raw_segment_synthesis_identity_sha256"] == source_entry["raw_segment_synthesis_identity_sha256"]
            source_task = session.get(VoiceSegmentTask, regenerated_entry["source_voice_segment_task_id"])
            assert source_task is not None
            assert source_task.operation_id == source_operation_id


def test_kokoro_regenerated_section_can_pass_followup_pacing_analysis(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        project = session.get(Project, project_id)
        assert project is not None

        submitted = service.submit_generation(
            project_id,
            {
                "operation_type": "regenerate_section",
                "source_remediation_operation_id": source_operation_id,
                "expected_remediation_report_sha256": report["remediation_report_sha256"],
                "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            },
            target_section_id=target_section_id,
        )

        regenerated = session.get(VoiceGenerationOperation, submitted["operation_id"])
        assert regenerated is not None
        target_segment_ids = {
            segment["segment_id"]
            for segment in regenerated.request_json["segmentation"]["segments"]
            if segment["section_id"] == target_section_id
        }
        assert set(regenerated.request_json["scheduled_segment_ids"]) == target_segment_ids
        assert submitted["reused_count"] > 0

        pacing = regenerated.request_json["script_section_pacing"]
        samples_by_segment_id: dict[str, int] = {}
        for section in pacing["sections"]:
            segment_ids = list(section["segment_ids"])
            target_samples = round(float(section["target_duration_seconds"]) * CANONICAL_SAMPLE_RATE)
            base_samples = max(1, target_samples // len(segment_ids))
            remaining = target_samples
            for segment_id in segment_ids[:-1]:
                samples_by_segment_id[str(segment_id)] = base_samples
                remaining -= base_samples
            samples_by_segment_id[str(segment_ids[-1])] = max(1, remaining)

        segments = regenerated.request_json["segmentation"]["segments"]
        segment_bytes = [_silent_wav(samples_by_segment_id[str(segment["segment_id"])]) for segment in segments]
        segment_records = [
            {
                "segment_audio_artifact_id": f"raw_{segment['segment_id']}",
                "raw_segment_synthesis_identity_sha256": regenerated_entry["raw_segment_synthesis_identity_sha256"],
                "sample_count": samples_by_segment_id[str(segment["segment_id"])],
            }
            for segment, regenerated_entry in zip(segments, regenerated.request_json["complete_segments"], strict=True)
        ]

        analysis = analyze_section_pacing(
            segment_bytes=segment_bytes,
            segment_records=segment_records,
            segmentation=regenerated.request_json["segmentation"],
            pacing_plan=pacing,
            source_operation_id=regenerated.operation_id,
            source_script_revision_id=str(regenerated.script_artifact_id),
            provider_identity_sha256=regenerated.provider_identity_sha256,
            generation_settings_snapshot_sha256=regenerated.generation_settings_snapshot_sha256,
            recommendation_resolver=lambda observation: service.tts_settings.registry.recommend_pacing("kokoro_local", observation),
        )

        target_section = next(section for section in analysis.report["sections"] if section["section_id"] == target_section_id)
        assert target_section["status"] == "pass"
        assert target_section["feedback_supported"] is False


def test_voice_workspace_projects_terminal_pacing_reason_and_blocks_regeneration_action(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        sections = []
        for section in report["sections"]:
            if section["section_id"] == target_section_id:
                section = {
                    **section,
                    "feedback_supported": False,
                    "recommended_provider_pacing_value": None,
                    "recommendation_reason": "provider_pacing_limit_reached",
                }
            sections.append(section)
        report = rehash_remediation_report({**report, "sections": sections})
        source_operation.result_json = {**source_operation.result_json, "pacing_remediation_report": report}
        source_operation.error_details_json = report
        session.flush()

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        workspace = service.get_workspace(project_id)

        target = next(section for section in workspace["sections"] if section["section_id"] == target_section_id)
        assert target["pacing_status"] == "regeneration_required"
        assert target["can_regenerate"] is False
        assert target["recommendation_reason"] == "provider_pacing_limit_reached"
        assert "regenerate_section" not in workspace["available_actions"]


def test_voice_workspace_keeps_regenerate_action_when_any_section_is_actionable(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, _source_operation_id, _report = _submit_completed_kokoro_source(session, monkeypatch)

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        workspace = service.get_workspace(project_id)

        target = next(section for section in workspace["sections"] if section["section_id"] == target_section_id)
        assert target["pacing_status"] == "regeneration_required"
        assert target["can_regenerate"] is True
        assert "regenerate_section" in workspace["available_actions"]


def test_noncurrent_kokoro_pacing_flow_is_invalid(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        source_plan = source_operation.request_json["script_section_pacing"]
        target_plan_section = next(section for section in source_plan["sections"] if section["section_id"] == target_section_id)
        target_plan_section["provider_pacing_policy_version"] = "kokoro_old_flow"
        plan_payload = {key: value for key, value in source_plan.items() if key != "pacing_identity_sha256"}
        source_plan["pacing_identity_sha256"] = canonical_hash(plan_payload)
        report = rehash_remediation_report({**report, "pacing_identity_sha256": source_plan["pacing_identity_sha256"]})
        source_operation.request_json = {**source_operation.request_json, "script_section_pacing": source_plan}
        source_operation.result_json = {**source_operation.result_json, "pacing_remediation_report": report}
        source_operation.error_details_json = report
        session.commit()

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        workspace = service.get_workspace(project_id)

        target = next(section for section in workspace["sections"] if section["section_id"] == target_section_id)
        assert target["remediation_block_reason"] == "source_pacing_plan_invalid"
        assert target["regeneration_supported"] is False
        assert workspace["current_output_problem"]["error_code"] == "VOICE_PACING_REMEDIATION_UNSUPPORTED"
        project = session.get(Project, project_id)
        assert project is not None
        with pytest.raises(AppError) as exc_info:
            service.submit_generation(
                project_id,
                {
                    "operation_type": "regenerate_section",
                    "source_remediation_operation_id": source_operation_id,
                    "expected_remediation_report_sha256": report["remediation_report_sha256"],
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
                target_section_id=target_section_id,
            )
        assert exc_info.value.error_code == "VOICE_PACING_REMEDIATION_STALE"


def test_kokoro_regenerate_section_still_rejects_malformed_current_pacing_plan(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        source_plan = source_operation.request_json["script_section_pacing"]
        source_plan["pacing_identity_sha256"] = "0" * 64
        source_operation.request_json = {**source_operation.request_json, "script_section_pacing": source_plan}
        session.commit()

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        project = session.get(Project, project_id)
        assert project is not None

        with pytest.raises(AppError) as exc_info:
            service.submit_generation(
                project_id,
                {
                    "operation_type": "regenerate_section",
                    "source_remediation_operation_id": source_operation_id,
                    "expected_remediation_report_sha256": report["remediation_report_sha256"],
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
                target_section_id=target_section_id,
            )

        assert exc_info.value.error_code == "VOICE_PACING_REMEDIATION_STALE"
        assert exc_info.value.message == "The source Voice frozen pacing plan is invalid."


def test_current_kokoro_manual_pacing_acceptance_resumes_finalization(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        workspace = service.get_workspace(project_id)
        target = next(section for section in workspace["sections"] if section["section_id"] == target_section_id)
        assert target["can_accept_pacing_deviation"] is True

        result = VoicePacingManualAcceptanceService(session).accept_section(
            project_id=project_id,
            section_id=target_section_id,
            source_remediation_operation_id=source_operation_id,
            expected_remediation_report_sha256=report["remediation_report_sha256"],
            expected_remediation_identity=str(target["remediation_identity"]),
        )

        session.refresh(source_operation)
        job = session.get(Job, source_operation.job_id)
        assert result["status"] == "resuming"
        assert source_operation.status == "finalizing"
        assert job is not None
        assert job.status == JobStatus.RUNNING
        records = source_operation.result_json[VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY]
        assert [item["section_id"] for item in records] == [target_section_id]


def test_carried_acceptance_rehashes_report_and_keeps_target_actionable(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        sibling = next(section for section in report["sections"] if section["section_id"] != target_section_id)
        sibling.update({
            "status": "regeneration_required",
            "severity": "regeneration_required",
            "feedback_supported": True,
            "issue_code": "VOICE_PACING_WPM_OUT_OF_RANGE",
            "raw_wpm": float(sibling["target_wpm"]) * 1.10,
            "correction_relative": 0.08,
            "wpm_deviation_percent": 10.0,
            "recommended_provider_pacing_value": 1.1,
            "recommendation_policy_version": KOKORO_FLOW_VERSION,
        })
        report = rehash_remediation_report(report)
        source_operation.result_json = {**source_operation.result_json, "pacing_remediation_report": report}
        source_operation.error_details_json = report
        source_job = session.get(Job, source_operation.job_id)
        assert source_job is not None
        source_job.error_details_json = report
        session.flush()

        VoicePacingManualAcceptanceService(session).accept_section(
            project_id=project_id,
            section_id=str(sibling["section_id"]),
            source_remediation_operation_id=source_operation_id,
            expected_remediation_report_sha256=report["remediation_report_sha256"],
            expected_remediation_identity=str(sibling["remediation_identity"]),
        )

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        project = session.get(Project, project_id)
        assert project is not None
        submitted = service.submit_generation(
            project_id,
            {
                "operation_type": "regenerate_section",
                "source_remediation_operation_id": source_operation_id,
                "expected_remediation_report_sha256": report["remediation_report_sha256"],
                "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            },
            target_section_id=target_section_id,
        )
        regenerated = session.get(VoiceGenerationOperation, submitted["operation_id"])
        assert regenerated is not None
        _materialize_completed_source_operation(session, regenerated, target_section_id=target_section_id)
        service._validate_operation_integrity(regenerated)
        raw_bytes, records = service._ordered_segment_outputs(regenerated)

        def recommendation(observation):
            return VoiceProviderPacingRecommendation(
                status="supported",
                control_model="relative_multiplier",
                recommended_value=min(2.0, float(observation.current_provider_pacing_value) * 1.1),
                policy_version=KOKORO_FLOW_VERSION,
                value_range=(0.5, 2.0),
                speed_response_exponent_used=observation.speed_response_exponent,
            )

        analysis = analyze_section_pacing(
            segment_bytes=raw_bytes,
            segment_records=records,
            segmentation=regenerated.request_json["segmentation"],
            pacing_plan=regenerated.request_json["script_section_pacing"],
            source_operation_id=regenerated.operation_id,
            source_script_revision_id=str(regenerated.script_artifact_id),
            provider_identity_sha256=regenerated.provider_identity_sha256,
            generation_settings_snapshot_sha256=regenerated.generation_settings_snapshot_sha256,
            recommendation_resolver=recommendation,
        )
        analysis = apply_manual_pacing_acceptances(
            analysis,
            regenerated.result_json.get(VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY),
        )
        assert analysis.report["status"] == "regeneration_required"
        assert validate_remediation_report_integrity(analysis.report) is True

        regenerated.status = "failed"
        regenerated.error_code = "VOICE_PACING_REGENERATION_REQUIRED"
        regenerated.error_details_json = analysis.report
        regenerated.result_json = {**regenerated.result_json, "pacing_remediation_report": analysis.report}
        job = session.get(Job, regenerated.job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.error_code = regenerated.error_code
        job.error_details_json = analysis.report
        session.commit()

        workspace = service.get_workspace(project_id)
        sibling_workspace = next(item for item in workspace["sections"] if item["section_id"] == sibling["section_id"])
        target_workspace = next(item for item in workspace["sections"] if item["section_id"] == target_section_id)
        assert sibling_workspace["pacing_status"] == "pass"
        assert target_workspace["pacing_status"] == "regeneration_required"
        assert target_workspace["can_regenerate"] is True
        assert target_workspace["remediation_block_reason"] is None


@pytest.mark.parametrize(
    "tamper_field",
    [
        "recommended_provider_pacing_value",
        "feedback_supported",
        "correction_relative",
        "remediation_identity",
    ],
)
def test_pacing_remediation_report_tamper_blocks_workspace_regenerate_and_accept(monkeypatch, tamper_field: str) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        acceptance_identity = "a" * 64
        accepted_sections = []
        for section in report["sections"]:
            section = dict(section)
            if section["section_id"] == target_section_id:
                section["section_acceptance_identity_sha256"] = acceptance_identity
            accepted_sections.append(section)
        report = rehash_remediation_report({**report, "sections": accepted_sections})
        accepted_target = next(section for section in report["sections"] if section["section_id"] == target_section_id)
        source_operation.result_json = {
            **source_operation.result_json,
            "pacing_remediation_report": report,
            VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY: [{
                "section_id": target_section_id,
                "remediation_identity": accepted_target["remediation_identity"],
                "remediation_report_sha256": report["remediation_report_sha256"],
                "section_acceptance_identity_sha256": acceptance_identity,
            }],
        }
        source_operation.error_details_json = report
        original_hash = report["remediation_report_sha256"]
        original_identity = accepted_target["remediation_identity"]
        tampered_sections = []
        for section in report["sections"]:
            if section["section_id"] == target_section_id:
                section = dict(section)
                if tamper_field == "recommended_provider_pacing_value":
                    section["recommended_provider_pacing_value"] = 1.234
                elif tamper_field == "feedback_supported":
                    section["feedback_supported"] = False
                elif tamper_field == "correction_relative":
                    section["correction_relative"] = 0.01
                elif tamper_field == "remediation_identity":
                    section["remediation_identity"] = "0" * 64
            tampered_sections.append(section)
        tampered_report = {**report, "sections": tampered_sections}
        assert tampered_report["remediation_report_sha256"] == original_hash
        assert validate_remediation_report_integrity(tampered_report) is False
        source_operation.result_json = {**source_operation.result_json, "pacing_remediation_report": tampered_report}
        source_operation.error_details_json = tampered_report
        session.flush()

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        workspace = service.get_workspace(project_id)
        target = next(section for section in workspace["sections"] if section["section_id"] == target_section_id)
        assert target["pacing_status"] != "pass"
        assert target["can_regenerate"] is False
        assert target["can_accept_pacing_deviation"] is False
        assert target["regeneration_supported"] is False

        project = session.get(Project, project_id)
        assert project is not None
        with pytest.raises(AppError):
            service.submit_generation(
                project_id,
                {
                    "operation_type": "regenerate_section",
                    "source_remediation_operation_id": source_operation_id,
                    "expected_remediation_report_sha256": original_hash,
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
                target_section_id=target_section_id,
            )
        with pytest.raises(AppError):
            VoicePacingManualAcceptanceService(session).accept_section(
                project_id=project_id,
                section_id=target_section_id,
                source_remediation_operation_id=source_operation_id,
                expected_remediation_report_sha256=original_hash,
                expected_remediation_identity=original_identity,
            )


def test_kokoro_regenerate_section_rejects_source_plan_without_duration_model(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(session, monkeypatch)
        source_operation = session.get(VoiceGenerationOperation, source_operation_id)
        assert source_operation is not None
        source_plan = source_operation.request_json["script_section_pacing"]
        for section in source_plan["sections"]:
            section.pop("provider_duration_model", None)
            section.pop("provider_fixed_duration_seconds", None)
        plan_payload = {key: value for key, value in source_plan.items() if key != "pacing_identity_sha256"}
        source_plan["pacing_identity_sha256"] = canonical_hash(plan_payload)
        source_operation.request_json = {**source_operation.request_json, "script_section_pacing": source_plan}
        report = rehash_remediation_report({**report, "pacing_identity_sha256": source_plan["pacing_identity_sha256"]})
        source_operation.result_json = {**source_operation.result_json, "pacing_remediation_report": report}
        source_operation.error_details_json = report
        session.flush()

        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        project = session.get(Project, project_id)
        assert project is not None

        with pytest.raises(AppError) as exc_info:
            service.submit_generation(
                project_id,
                {
                    "operation_type": "regenerate_section",
                    "source_remediation_operation_id": source_operation_id,
                    "expected_remediation_report_sha256": report["remediation_report_sha256"],
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
                target_section_id=target_section_id,
            )

        assert exc_info.value.error_code == "VOICE_PACING_REMEDIATION_STALE"


def test_kokoro_regenerate_section_still_rejects_invalid_sibling_reuse_identity(monkeypatch) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id, target_section_id, source_operation_id, report = _submit_completed_kokoro_source(
            session,
            monkeypatch,
            corrupt_sibling_identity=True,
        )
        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()
        project = session.get(Project, project_id)
        assert project is not None

        with pytest.raises(AppError) as exc_info:
            service.submit_generation(
                project_id,
                {
                    "operation_type": "regenerate_section",
                    "source_remediation_operation_id": source_operation_id,
                    "expected_remediation_report_sha256": report["remediation_report_sha256"],
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
                target_section_id=target_section_id,
            )

        assert exc_info.value.error_code == "VOICE_SEGMENT_AUDIO_INVALID"
        assert exc_info.value.message == "Reusable Voice segment identity is missing."


def test_kokoro_workspace_separates_generation_and_alignment_readiness(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=False),
    )
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()

        workspace = service.get_workspace(project_id)

        assert workspace["selected_generation_provider"] is None
        elevenlabs = next(item for item in workspace["available_generation_providers"] if item["generation_provider"] == "elevenlabs")
        kokoro = next(item for item in workspace["available_generation_providers"] if item["generation_provider"] == "kokoro_local")
        assert elevenlabs["issue"]["error_code"] == "VOICE_CHANNEL_PROFILE_REQUIRED"
        assert kokoro["generation_ready"] is True
        assert kokoro["alignment_ready"] is False
        assert kokoro["ready"] is False
        assert kokoro["issue"]["error_code"] == "VOICE_ALIGNMENT_NOT_READY"
        assert kokoro["issue"]["message"] == "Kokoro generation is ready, but ElevenLabs Forced Alignment is not configured."


def test_elevenlabs_workspace_reports_generation_readiness_per_exact_preset(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        channel = session.get(Channel, project.channel_id)
        assert channel is not None
        channel.voice_profile_json = {
            "provider": "elevenlabs",
            "voice_id": "pvc_voice",
            "calibration": {
                "economy": {},
                "quality_cloned_voice": {},
            },
        }
        session.commit()
        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeWorkspaceGenerationRegistry(
            elevenlabs_by_model={
                ACTIVE_VOICE_PRESETS["economy"]: VoiceProviderReadiness(status="ready"),
                ACTIVE_VOICE_PRESETS["quality_cloned_voice"]: VoiceProviderReadiness(
                    status="blocked",
                    issue_code="VOICE_PVC_NOT_READY",
                    message="The selected Channel voice is not ready for this exact model.",
                ),
            }
        )

        workspace = service.get_workspace(project_id)

        elevenlabs = next(item for item in workspace["available_generation_providers"] if item["generation_provider"] == "elevenlabs")
        assert elevenlabs["ready"] is False
        assert elevenlabs["generation_ready"] is False
        assert elevenlabs["alignment_ready"] is True
        assert elevenlabs["generation_presets"]["economy"]["ready"] is True
        assert elevenlabs["generation_presets"]["economy"]["issue"] is None
        assert elevenlabs["generation_presets"]["quality_cloned_voice"]["ready"] is False
        assert elevenlabs["generation_presets"]["quality_cloned_voice"]["issue"]["error_code"] == "VOICE_PVC_NOT_READY"
        model_profiles = {
            str(profile.provider_options.get("model_id")): profile
            for profile in service.tts_settings.registry.readiness_profiles
            if profile.provider_key == "elevenlabs"
        }
        assert set(model_profiles) == set(ACTIVE_VOICE_PRESETS.values())
        assert model_profiles[ACTIVE_VOICE_PRESETS["economy"]].maximum_segment_characters == 40_000
        assert model_profiles[ACTIVE_VOICE_PRESETS["quality_cloned_voice"]].maximum_segment_characters == 10_000


def test_elevenlabs_workspace_blocks_when_channel_profile_voice_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        channel = session.get(Channel, project.channel_id)
        assert channel is not None
        channel.voice_profile_json = {"provider": "elevenlabs", "calibration": {}}
        session.commit()
        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeWorkspaceGenerationRegistry(elevenlabs_by_model={})

        workspace = service.get_workspace(project_id)

        elevenlabs = next(item for item in workspace["available_generation_providers"] if item["generation_provider"] == "elevenlabs")
        assert elevenlabs["generation_ready"] is False
        assert {status["issue"]["error_code"] for status in elevenlabs["generation_presets"].values()} == {
            "VOICE_CHANNEL_PROFILE_REQUIRED"
        }


def test_elevenlabs_submit_enforces_selected_preset_generation_readiness(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=True),
    )
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        channel = session.get(Channel, project.channel_id)
        assert channel is not None
        channel.voice_profile_json = {
            "provider": "elevenlabs",
            "voice_id": "missing_voice",
            "calibration": {
                "economy": {},
                "quality_cloned_voice": {},
            },
        }
        session.commit()
        registry = _FakeWorkspaceGenerationRegistry(
            elevenlabs_by_model={
                ACTIVE_VOICE_PRESETS["economy"]: VoiceProviderReadiness(status="ready"),
                ACTIVE_VOICE_PRESETS["quality_cloned_voice"]: VoiceProviderReadiness(
                    status="blocked",
                    issue_code="TTS_VOICE_INVALID",
                    message="The selected ElevenLabs voice is not available for the exact preset.",
                ),
            }
        )
        service = DurableVoiceService(session)
        service.tts_settings.registry = registry

        with pytest.raises(AppError) as exc_info:
            service.submit_generation(
                project_id,
                {
                    "operation_type": "generate_all",
                    "generation_provider": "elevenlabs",
                    "voice_preset": "quality_cloned_voice",
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
            )

        assert exc_info.value.error_code == "TTS_VOICE_INVALID"
        assert registry.readiness_profiles[-1].provider_key == "elevenlabs"
        assert registry.readiness_profiles[-1].provider_options["model_id"] == ACTIVE_VOICE_PRESETS["quality_cloned_voice"]


def test_kokoro_submit_is_fenced_when_alignment_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        durable_voice_module,
        "build_runtime_elevenlabs_credential_resolver",
        lambda settings=None: _alignment_resolver(ready=False),
    )
    factory = build_session_factory(get_settings())
    with factory() as session:
        project_id = _create_script_ready_project(session)
        project = session.get(Project, project_id)
        assert project is not None
        service = DurableVoiceService(session)
        service.tts_settings.registry = _FakeKokoroRegistry()

        with pytest.raises(AppError) as exc_info:
            service.submit_generation(
                project_id,
                {
                    "operation_type": "generate_all",
                    "generation_provider": "kokoro_local",
                    "generation_voice_id": "bf_alice",
                    "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
                    "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                },
            )

        assert exc_info.value.error_code == "VOICE_ALIGNMENT_NOT_READY"
        assert exc_info.value.details["alignment_provider"] == "elevenlabs"
