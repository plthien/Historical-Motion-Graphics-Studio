from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType, JobStatus
from app.db.models import Artifact, Channel, Job, Project, VoiceGenerationOperation, VoiceSegmentTask
from app.db.session import get_session_factory
from app.modules.v4.voice_first_policy import VOICE_ARCHITECTURE_VERSION
from app.modules.v4.voice_pacing import (
    VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE,
    VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE,
    VOICE_PACING_POLICY_VERSION,
    VOICE_PACING_QUALITY_FENCE_RELATIVE,
    VOICE_PACING_SAMPLE_ROUNDING_EPSILON,
    VOICE_PACING_SCHEMA_VERSION,
    raw_segment_synthesis_identity_sha256,
    raw_synthesis_identity_sha256,
)
from app.modules.v4.voice_provider_action_controller import ProviderActionProgress, VoiceProviderActionController
from app.modules.v4.voice_provider_adapters import FakeAsyncProvider, FakeBlockingProvider, FakeImmediateProvider
from app.modules.v4.voice_provider_contracts import VoiceProviderAdapter, VoiceProviderProfile, VoiceProviderRegistry
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.modules.v4.voice_service import canonical_hash
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository


def _ids(provider_key: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex
    return {
        "channel": f"channel_{suffix}",
        "project": f"project_{suffix}",
        "job": f"job_{suffix}",
        "operation": f"voiceop_{suffix}",
        "task": f"voicetask_{suffix}",
        "segment": f"segment_{suffix}",
        "attempt": f"attempt_{provider_key}_{suffix}",
        "script": f"script_{suffix}",
    }


def _artifact(*, artifact_id: str, now: str) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"logical_{artifact_id}",
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.json",
        content_hash=hashlib.sha256(artifact_id.encode()).hexdigest(),
        sha256=hashlib.sha256(artifact_id.encode()).hexdigest(),
        mime_type="application/json",
        size_bytes=2,
        status=ArtifactStatus.READY,
        created_at=now,
    )


def _seed_submitting_task(provider: VoiceProviderAdapter) -> tuple[str, str]:
    ids = _ids(provider.provider_key)
    now = utc_now_iso()
    exact_text = "The durable provider contract must preserve this exact narration text."
    settings_snapshot = {
        "tts_profile_id": f"profile_{provider.provider_key}",
        "voice_id": "voice_test",
        "language": "en",
        "speaking_rate": 1.0,
        "provider_key": provider.provider_key,
        "provider_options": {"model_id": "eleven_multilingual_v2", "narration_language": "en"} if provider.provider_key == "elevenlabs" else {},
        "sample_rate_hz": 48_000,
        "output_format": "wav_pcm_s16le",
    }
    provider_profile = VoiceProviderProfile(
        profile_id=f"profile_{provider.provider_key}",
        provider_key=provider.provider_key,
        enabled=True,
        display_name=f"Profile {provider.provider_key}",
        voice_id="voice_test",
        language="en",
        speaking_rate=1.0,
        requested_sample_rate_hz=48_000,
        requested_output_format="wav_pcm_s16le",
        provider_options=dict(settings_snapshot["provider_options"]),
    )
    provider_identity = {
        "provider_key": provider.provider_key,
        "provider": provider.provider_key,
        "profile_id": provider_profile.profile_id,
        "voice_id": provider_profile.voice_id,
        "language": provider_profile.language,
        "execution_identity": provider.execution_identity(provider_profile),
    }
    pacing_plan = {
        "schema_version": VOICE_PACING_SCHEMA_VERSION,
        "policy_version": VOICE_PACING_POLICY_VERSION,
        "source_script_revision_id": ids["script"],
        "control_model": "relative_multiplier",
        "base_native_speaking_rate": 1.0,
        "planned_average_wpm": 120,
        "quality_fence_relative": VOICE_PACING_QUALITY_FENCE_RELATIVE,
        "atempo_safety_fence_relative": VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE,
        "final_wpm_tolerance_relative": VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE,
        "sample_rounding_epsilon": VOICE_PACING_SAMPLE_ROUNDING_EPSILON,
        "sections": [
            {
                "section_id": "sec_001",
                "section_order": 1,
                "planned_narration_wpm": 120,
                "actual_word_count": 10,
                "target_duration_seconds": 5.0,
                "provider_pacing_control": "relative_multiplier",
                "provider_pacing_value": 1.0,
                "provider_pacing_policy_version": "fake_relative_multiplier_v1",
                "provider_pacing_value_range": [0.5, 2.0],
                "provider_pacing_clamped": False,
                "segment_ids": [ids["segment"]],
            }
        ],
    }
    pacing_plan["pacing_identity_sha256"] = canonical_hash(pacing_plan)
    settings_snapshot["pacing_identity_sha256"] = pacing_plan["pacing_identity_sha256"]
    segmentation = {
        "segmentation_version": "narration_segmentation_v2",
        "maximum_characters": 10_000,
        "segments": [
            {
                "segment_id": ids["segment"],
                "segment_order": 1,
                "section_id": "sec_001",
                "source_start": 0,
                "source_end": len(exact_text),
                "exact_text": exact_text,
                "text_sha256": hashlib.sha256(exact_text.encode()).hexdigest(),
            }
        ],
    }
    segmentation["segmentation_input_sha256"] = canonical_hash(segmentation)
    raw_identity = raw_synthesis_identity_sha256(
        provider_identity=provider_identity, settings_snapshot=settings_snapshot, pacing_plan=pacing_plan
    )
    settings_snapshot["raw_synthesis_identity_sha256"] = raw_identity
    settings_hash = canonical_hash(settings_snapshot)
    raw_segment_identity = raw_segment_synthesis_identity_sha256(
        provider_identity=provider_identity,
        settings_snapshot=settings_snapshot,
        pacing_plan=pacing_plan,
        segment=segmentation["segments"][0],
        synthesis_context_sha256=None,
    )
    voice_first_payloads = {}
    if provider.provider_key == "elevenlabs":
        voice_first_payloads[ids["segment"]] = {
            "provider_text": exact_text,
            "provider_text_sha256": hashlib.sha256(exact_text.encode()).hexdigest(),
            "seed": 123456,
            "canonical_spoken_text_sha256": hashlib.sha256(exact_text.encode()).hexdigest(),
            "spoken_compilation_sha256": "s" * 64,
            "authored_spoken_mapping_sha256": "m" * 64,
            "delivery_policy_version": "sparse_pause_cues_v1",
        }
    snapshot = {
        "segment_id": ids["segment"],
        "segment_order": 1,
        "section_id": "sec_001",
        "source_start": 0,
        "source_end": len(exact_text),
        "exact_text_sha256": hashlib.sha256(exact_text.encode()).hexdigest(),
        "source_kind": "synthesis_required",
        "segment_audio_artifact_id": None,
        "source_voice_segment_task_id": ids["task"],
        "sample_count": None,
        "duration_seconds": None,
        "sha256": None,
        "raw_synthesis_identity_sha256": raw_identity,
        "raw_segment_synthesis_identity_sha256": raw_segment_identity,
        "target_narration_wpm": 120,
        "provider_pacing_control": "relative_multiplier",
        "provider_pacing_value": 1.0,
        "provider_pacing_policy_version": "fake_relative_multiplier_v1",
        "provider_pacing_clamped": False,
    }
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            Channel(
                channel_id=ids["channel"],
                name=f"Voice lifecycle {provider.provider_key}",
                language="en",
                niche="test",
                audience="test",
                channel_dna_json={},
                channel_context_json={},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(_artifact(artifact_id=ids["script"], now=now))
        session.flush()
        session.add(
            Project(
                project_id=ids["project"],
                channel_id=ids["channel"],
                name=f"Voice lifecycle {provider.provider_key}",
                channel_context_json={},
                channel_dna_json={},
                script_artifact_id=ids["script"],
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            Job(
                job_id=ids["job"],
                job_type="voice_generation_v4",
                status=JobStatus.RUNNING,
                progress=0,
                target_type="project",
                target_id=ids["project"],
                attempt=1,
                retryable=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            VoiceGenerationOperation(
                operation_id=ids["operation"],
                job_id=ids["job"],
                project_id=ids["project"],
                operation_type="generate_all",
                status="running",
                script_artifact_id=ids["script"],
                architecture_version=VOICE_ARCHITECTURE_VERSION,
                expected_narration_audio_artifact_id=None,
                expected_narration_word_timing_map_artifact_id=None,
                voice_preset="economy",
                tts_profile_id=f"profile_{provider.provider_key}",
                voice_id="voice_test",
                language="en",
                speaking_rate=1.0,
                provider_identity_json=provider_identity,
                provider_identity_sha256=canonical_hash(provider_identity),
                generation_settings_snapshot_json=settings_snapshot,
                generation_settings_snapshot_sha256=settings_hash,
                segmentation_version="narration_segmentation_v2",
                segmentation_input_sha256=segmentation["segmentation_input_sha256"],
                segment_count=1,
                request_json={
                    "schema_version": "4.0",
                    "segmentation": segmentation,
                    "scheduled_segment_ids": [ids["segment"]],
                    "target_section_id": None,
                    "script_section_pacing": pacing_plan,
                    **({"voice_first_segment_payloads": voice_first_payloads} if voice_first_payloads else {}),
                    "complete_segments": [dict(snapshot)],
                },
                result_json={"complete_segments": [dict(snapshot)]},
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            VoiceSegmentTask(
                voice_segment_task_id=ids["task"],
                operation_id=ids["operation"],
                job_id=ids["job"],
                project_id=ids["project"],
                segment_id=ids["segment"],
                segment_order=1,
                section_id="sec_001",
                source_start=0,
                source_end=len(exact_text),
                text_sha256=hashlib.sha256(exact_text.encode()).hexdigest(),
                exact_text=exact_text,
                status="queued",
                attempt_no=1,
                provider=provider.provider_key,
                provider_task_id=None,
                provider_status=None,
                provider_submitted_at=None,
                voice_id="voice_test",
                language="en",
                speaking_rate=1.0,
                sample_rate_hz=48_000,
                output_format="wav_pcm_s16le",
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
        session.flush()
        repository = VoiceDurableWorkingRepository(session)
        assert repository.begin_task_submission(task_id=ids["task"], attempt_token=ids["attempt"], now_iso=now)
        session.commit()
    return ids["task"], ids["attempt"]


def _wait_for_harvest(harvest: Callable[[], ProviderActionProgress]) -> ProviderActionProgress:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        progress = harvest()
        if progress.state not in {"running", "not_owned"}:
            return progress
        time.sleep(0.01)
    raise AssertionError("Voice provider action did not settle within the test deadline.")


class RuntimeSwitchingProvider(FakeImmediateProvider):
    provider_key = "runtime_switching"

    def __init__(self) -> None:
        self.runtime_version = "v1"
        self.submit_calls = 0
        self.reconcile_calls = 0

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, object]:
        _ = profile
        return {"schema_version": 1, "provider_key": self.provider_key, "runtime_version": self.runtime_version}

    def submit(self, request, control):
        self.submit_calls += 1
        return super().submit(request, control)

    def reconcile_submission(self, request, handle, control):
        self.reconcile_calls += 1
        return super().reconcile_submission(request, handle, control)


class IdentityCheckFailureProvider(FakeImmediateProvider):
    provider_key = "identity_check_failure"

    def __init__(self) -> None:
        self.runtime_version = "v1"
        self.identity_error: AppError | None = None
        self.malformed_identity = False
        self.reconcile_calls = 0

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, object]:
        _ = profile
        if self.identity_error is not None:
            raise self.identity_error
        if self.malformed_identity:
            return {"schema_version": 1, "provider_key": self.provider_key, "bad": float("nan")}
        return {"schema_version": 1, "provider_key": self.provider_key, "runtime_version": self.runtime_version}

    def reconcile_submission(self, request, handle, control):
        self.reconcile_calls += 1
        return super().reconcile_submission(request, handle, control)


def test_provider_runtime_change_fails_before_adapter_submit() -> None:
    provider = RuntimeSwitchingProvider()
    task_id, _execution_attempt_token = _seed_submitting_task(provider)
    provider.runtime_version = "v2"

    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1),
        poll_interval_seconds=1,
    )
    try:
        dispatched = controller.dispatch_submit(task_id)
        assert dispatched.state == "running"
        failed = _wait_for_harvest(lambda: controller.harvest_submit(task_id))

        assert failed.state == "failed"
        assert provider.submit_calls == 0
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    "SELECT t.status, t.error_code, o.status AS operation_status, j.status AS job_status "
                    "FROM voice_segment_tasks t "
                    "JOIN voice_generation_operations o ON o.operation_id = t.operation_id "
                    "JOIN jobs j ON j.job_id = t.job_id "
                    "WHERE t.voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert row["status"] == "failed"
            assert row["error_code"] == "VOICE_PROVIDER_RUNTIME_CHANGED"
            assert row["operation_status"] == "failed"
            assert row["job_status"] == JobStatus.FAILED.value
    finally:
        controller.close()


def test_provider_runtime_change_fails_before_adapter_reconcile() -> None:
    provider = RuntimeSwitchingProvider()
    task_id, _execution_attempt_token = _seed_submitting_task(provider)
    provider.runtime_version = "v2"

    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1),
        poll_interval_seconds=1,
    )
    try:
        dispatched = controller.dispatch_reconcile_submission(task_id)
        assert dispatched.state == "running"
        failed = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))

        assert failed.state == "failed"
        assert provider.reconcile_calls == 0
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    "SELECT t.status, t.error_code, o.status AS operation_status, j.status AS job_status "
                    "FROM voice_segment_tasks t "
                    "JOIN voice_generation_operations o ON o.operation_id = t.operation_id "
                    "JOIN jobs j ON j.job_id = t.job_id "
                    "WHERE t.voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert row["status"] == "failed"
            assert row["error_code"] == "VOICE_PROVIDER_RUNTIME_CHANGED"
            assert row["operation_status"] == "failed"
            assert row["job_status"] == JobStatus.FAILED.value
    finally:
        controller.close()


def test_reconcile_identity_lookup_failure_retries_without_ambiguous_submission() -> None:
    provider = IdentityCheckFailureProvider()
    task_id, _execution_attempt_token = _seed_submitting_task(provider)
    provider.identity_error = AppError(
        "TTS_PROVIDER_UNAVAILABLE",
        "Provider runtime is temporarily unavailable.",
        503,
        retryable=True,
    )

    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1),
        poll_interval_seconds=1,
    )
    try:
        dispatched = controller.dispatch_reconcile_submission(task_id)
        assert dispatched.state == "running"
        queued = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))

        assert queued.state == "queued"
        assert provider.reconcile_calls == 0
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    "SELECT t.status, t.error_code, t.next_reconcile_at, o.status AS operation_status "
                    "FROM voice_segment_tasks t "
                    "JOIN voice_generation_operations o ON o.operation_id = t.operation_id "
                    "WHERE t.voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert row["status"] == "queued"
            assert row["error_code"] == "VOICE_PROVIDER_RUNTIME_UNAVAILABLE"
            assert row["next_reconcile_at"] is not None
            assert row["operation_status"] == "running"

            repository = VoiceDurableWorkingRepository(session)
            assert repository.begin_task_submission(
                task_id=task_id,
                attempt_token=f"identity_recovered_{uuid.uuid4().hex}",
                now_iso=utc_now_iso(),
            )
            session.commit()

        provider.identity_error = None
        recovered = controller.dispatch_reconcile_submission(task_id)
        assert recovered.state == "running"
        replay = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))

        assert replay.state == "queued"
        assert provider.reconcile_calls == 1
    finally:
        controller.close()


def test_reconcile_identity_lookup_recovery_to_different_runtime_fails_before_provider_call() -> None:
    provider = IdentityCheckFailureProvider()
    task_id, _execution_attempt_token = _seed_submitting_task(provider)
    provider.identity_error = AppError(
        "TTS_PROVIDER_UNAVAILABLE",
        "Provider runtime is temporarily unavailable.",
        503,
        retryable=True,
    )

    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1),
        poll_interval_seconds=1,
    )
    try:
        assert controller.dispatch_reconcile_submission(task_id).state == "running"
        queued = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))
        assert queued.state == "queued"
        assert provider.reconcile_calls == 0

        with get_session_factory()() as session:
            repository = VoiceDurableWorkingRepository(session)
            assert repository.begin_task_submission(
                task_id=task_id,
                attempt_token=f"identity_changed_{uuid.uuid4().hex}",
                now_iso=utc_now_iso(),
            )
            session.commit()

        provider.identity_error = None
        provider.runtime_version = "v2"
        assert controller.dispatch_reconcile_submission(task_id).state == "running"
        failed = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))

        assert failed.state == "failed"
        assert provider.reconcile_calls == 0
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    "SELECT status, error_code FROM voice_segment_tasks "
                    "WHERE voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert row["status"] == "failed"
            assert row["error_code"] == "VOICE_PROVIDER_RUNTIME_CHANGED"
    finally:
        controller.close()


def test_reconcile_invalid_execution_identity_fails_without_ambiguous_submission() -> None:
    provider = IdentityCheckFailureProvider()
    task_id, _execution_attempt_token = _seed_submitting_task(provider)
    provider.malformed_identity = True

    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1),
        poll_interval_seconds=1,
    )
    try:
        dispatched = controller.dispatch_reconcile_submission(task_id)
        assert dispatched.state == "running"
        failed = _wait_for_harvest(lambda: controller.harvest_reconcile_submission(task_id))

        assert failed.state == "failed"
        assert provider.reconcile_calls == 0
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    "SELECT status, error_code FROM voice_segment_tasks "
                    "WHERE voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert row["status"] == "failed"
            assert row["error_code"] == "VOICE_PROVIDER_RUNTIME_UNAVAILABLE"
    finally:
        controller.close()


@pytest.mark.parametrize(
    "provider_factory",
    [FakeImmediateProvider, FakeBlockingProvider, FakeAsyncProvider],
    ids=["immediate", "blocking", "async"],
)
def test_same_durable_provider_lifecycle_contract(provider_factory: type[VoiceProviderAdapter]) -> None:
    provider = provider_factory()
    task_id, execution_attempt_token = _seed_submitting_task(provider)

    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=2),
        poll_interval_seconds=1,
    )
    try:
        submitted = controller.dispatch_submit(task_id)
        assert submitted.action == "submit"
        submission = _wait_for_harvest(lambda: controller.harvest_submit(task_id))

        if provider.capabilities().execution_model == "asynchronous":
            assert submission.state == "waiting_provider"
            with get_session_factory()() as session:
                waiting = VoiceDurableWorkingRepository(session).get_task(task_id)
                assert waiting is not None
                assert waiting.status == "waiting_provider"
                assert waiting.provider_task_id
            polled = controller.dispatch_poll(task_id)
            assert polled.action == "poll"
            completion = _wait_for_harvest(lambda: controller.harvest_poll(task_id))
        else:
            completion = submission

        assert completion.state == "materializing"
        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "materializing"
            assert task.execution_attempt_token == execution_attempt_token
            metadata = session.execute(
                text(
                    "SELECT provider_result_metadata_json FROM voice_segment_tasks "
                    "WHERE voice_segment_task_id = :task_id"
                ),
                {"task_id": task_id},
            ).scalar_one()
            assert execution_attempt_token in str(metadata)
            assert provider.provider_key in str(metadata)
    finally:
        controller.close()
