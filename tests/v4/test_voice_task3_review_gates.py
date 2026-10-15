from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import wave

import pytest
from sqlalchemy import select

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType, JobStatus
from app.db.models import Artifact, Channel, Job, Project, VoiceAlignmentTask, VoiceGenerationOperation
from app.db.session import get_session_factory
from app.modules.v4 import durable_voice_service as durable_voice_module
from app.modules.v4 import voice_audio as voice_audio_module
from app.modules.v4 import voice_task3 as task3_module
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.elevenlabs_client import ElevenLabsAmbiguousSubmission, ElevenLabsJsonResponse
from app.modules.v4.voice_audio import CANONICAL_SAMPLE_RATE, validate_canonical_wav_bytes
from app.modules.v4.voice_first_policy import FORCED_ALIGNMENT_POLICY_VERSION, VOICE_ARCHITECTURE_VERSION
from app.modules.v4.voice_service import canonical_hash
from app.modules.v4.voice_task3 import VoiceTask3AlignmentService
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService


def _canonical_wav(sample_count: int, sample_value: int = 120) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(int(sample_value).to_bytes(2, "little", signed=True) * sample_count)
    return output.getvalue()


class _AlignmentShared:
    def __init__(self, tmp_path: Path, suffix: str) -> None:
        self.project_id = f"project_alignment_{suffix}"
        self.operation_id = f"voice_operation_alignment_{suffix}"
        self.job_id = f"voice_job_alignment_{suffix}"
        self.task_id = f"voice_alignment_task_{suffix}"
        self.transcript = "Code D three four four four now."
        self.wav_bytes = _canonical_wav(12_000, 137)
        self.audio_info = validate_canonical_wav_bytes(self.wav_bytes)
        self.audio_path = tmp_path / f"{suffix}.wav"
        self.audio_path.write_bytes(self.wav_bytes)
        self.audio_artifact = SimpleNamespace(
            artifact_id=f"final_section_audio_{suffix}",
            status=ArtifactStatus.READY,
            deleted_at=None,
            sha256=self.audio_info.sha256,
        )
        self.final_section = {
            "section_id": "sec_001",
            "section_order": 1,
            "section_audio_artifact_id": self.audio_artifact.artifact_id,
            "audio_sha256": self.audio_info.sha256,
            "sample_rate_hz": CANONICAL_SAMPLE_RATE,
            "sample_count": self.audio_info.sample_count,
            "spoken_transcript": self.transcript,
            "spoken_transcript_sha256": hashlib.sha256(self.transcript.encode("utf-8")).hexdigest(),
            "final_section_identity_sha256": hashlib.sha256(f"final-section-{suffix}".encode()).hexdigest(),
        }
        self.project = SimpleNamespace(
            project_id=self.project_id,
            channel_id=f"channel_alignment_{suffix}",
            script_artifact_id=f"script_alignment_{suffix}",
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
        )
        self.operation = SimpleNamespace(
            operation_id=self.operation_id,
            job_id=self.job_id,
            project_id=self.project_id,
            status="finalizing",
            script_artifact_id=self.project.script_artifact_id,
            expected_narration_audio_artifact_id=None,
            expected_narration_word_timing_map_artifact_id=None,
            result_json={"final_sections": [self.final_section], "alignment_status": "queued"},
            next_reconcile_at=None,
            updated_at=utc_now_iso(),
        )
        self.job = SimpleNamespace(
            job_id=self.job_id,
            status=JobStatus.RUNNING,
            progress=80,
            error_code=None,
            error_message=None,
            updated_at=utc_now_iso(),
        )
        self.task = SimpleNamespace(
            voice_alignment_task_id=self.task_id,
            operation_id=self.operation_id,
            job_id=self.job_id,
            project_id=self.project_id,
            section_id="sec_001",
            section_order=1,
            section_audio_artifact_id=self.audio_artifact.artifact_id,
            section_audio_sha256=self.audio_info.sha256,
            section_audio_sample_count=self.audio_info.sample_count,
            sample_rate_hz=CANONICAL_SAMPLE_RATE,
            spoken_transcript=self.transcript,
            spoken_transcript_sha256=self.final_section["spoken_transcript_sha256"],
            provider="elevenlabs",
            provider_policy_version=FORCED_ALIGNMENT_POLICY_VERSION,
            alignment_policy_version=FORCED_ALIGNMENT_POLICY_VERSION,
            status="queued",
            attempt_no=1,
            submit_attempt_token=None,
            submit_started_at=None,
            execution_attempt_token=None,
            execution_started_at=None,
            provider_submitted_at=None,
            provider_status=None,
            retry_count=0,
            max_attempts=3,
            last_retry_at=None,
            next_reconcile_at=None,
            result_json=None,
            loss_telemetry_json=None,
            error_code=None,
            error_message=None,
            error_details_json=None,
            completed_at=None,
            updated_at=utc_now_iso(),
            state_revision=0,
        )
        self.commits = 0
        self.rollbacks = 0
        self.artifact_stage_calls = 0
        self.tts_calls = 0
        self.section_regeneration_calls = 0


class _AlignmentSession:
    def __init__(self, shared: _AlignmentShared) -> None:
        self.shared = shared

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def get(self, model, key):
        if model is VoiceAlignmentTask and key == self.shared.task.voice_alignment_task_id:
            return self.shared.task
        return None

    def commit(self) -> None:
        self.shared.commits += 1

    def rollback(self) -> None:
        self.shared.rollbacks += 1


class _OperationRepo:
    def __init__(self, shared: _AlignmentShared) -> None:
        self.shared = shared

    def get(self, operation_id: str):
        return self.shared.operation if operation_id == self.shared.operation_id else None

    def get_by_job_id(self, job_id: str):
        return self.shared.operation if job_id == self.shared.job_id else None


class _JobRepo:
    def __init__(self, shared: _AlignmentShared) -> None:
        self.shared = shared

    def get(self, job_id: str):
        return self.shared.job if job_id == self.shared.job_id else None


class _ArtifactRows:
    def __init__(self, shared: _AlignmentShared) -> None:
        self.shared = shared

    def get(self, artifact_id: str):
        return self.shared.audio_artifact if artifact_id == self.shared.audio_artifact.artifact_id else None


class _ArtifactStore:
    def __init__(self, shared: _AlignmentShared) -> None:
        self.shared = shared

    def resolve_file_path(self, artifact):
        assert artifact.artifact_id == self.shared.audio_artifact.artifact_id
        return self.shared.audio_path

    def stage_artifact_revision(self, **kwargs):
        self.shared.artifact_stage_calls += 1
        raise AssertionError(f"Alignment reconciliation must not stage Voice audio: {kwargs}")


class _AlignmentDurableService:
    def __init__(self, session: _AlignmentSession) -> None:
        self.shared = session.shared
        self.operations = _OperationRepo(self.shared)
        self.jobs = _JobRepo(self.shared)
        self.artifact_rows = _ArtifactRows(self.shared)
        self.artifacts = _ArtifactStore(self.shared)

    def _project_required(self, project_id: str):
        assert project_id == self.shared.project_id
        return self.shared.project

    def _cancellation_requested(self, job, operation) -> bool:
        _ = (job, operation)
        return False

    def _terminalize_cancelled(self, job, operation) -> None:
        _ = (job, operation)
        raise AssertionError("Cancellation is not expected in Task 3 review-gate tests")

    def submit_generation(self, *args, **kwargs):
        self.shared.tts_calls += 1
        raise AssertionError("Alignment reconciliation must not resynthesize TTS")

    def submit_section_regeneration(self, *args, **kwargs):
        self.shared.section_regeneration_calls += 1
        raise AssertionError("Alignment reconciliation must not regenerate a section")


class _AlignmentClient:
    def __init__(self, *, ambiguous: bool = False) -> None:
        self.ambiguous = ambiguous
        self.calls: list[tuple[bytes, str]] = []
        self.close_calls = 0

    def forced_alignment(self, *, audio_bytes: bytes, text: str) -> ElevenLabsJsonResponse:
        self.calls.append((audio_bytes, text))
        if self.ambiguous:
            raise ElevenLabsAmbiguousSubmission()
        characters = [
            {
                "text": character,
                "start": (index * 8) / CANONICAL_SAMPLE_RATE,
                "end": ((index + 1) * 8) / CANONICAL_SAMPLE_RATE,
            }
            for index, character in enumerate(text)
        ]
        return ElevenLabsJsonResponse(
            200,
            {},
            {
                "characters": characters,
                "words": [{"text": "vendor-token", "loss": 0.125}],
                "loss": 0.25,
            },
        )

    def close(self) -> None:
        self.close_calls += 1


def _alignment_owner(
    shared: _AlignmentShared,
    client: _AlignmentClient,
    monkeypatch: pytest.MonkeyPatch,
) -> VoiceTask3AlignmentService:
    monkeypatch.setattr(task3_module, "DurableVoiceService", _AlignmentDurableService)
    owner = VoiceTask3AlignmentService(
        lambda: _AlignmentSession(shared),
        client_factory=lambda: client,
    )
    monkeypatch.setattr(owner, "_tasks", lambda _session, operation_id: [shared.task])
    return owner


def test_alignment_reconcile_uses_exact_final_audio_and_fences_identity_and_stale_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = _AlignmentShared(tmp_path, "authority")
    client = _AlignmentClient()
    owner = _alignment_owner(shared, client, monkeypatch)

    assert owner.reconcile_operation(shared.operation_id) is True
    assert len(client.calls) == 1
    provider_audio, provider_text = client.calls[0]
    assert provider_audio == shared.wav_bytes
    assert hashlib.sha256(provider_audio).hexdigest() == shared.task.section_audio_sha256
    assert validate_canonical_wav_bytes(provider_audio).sample_count == shared.task.section_audio_sample_count
    assert provider_text == shared.transcript
    assert "<break" not in provider_text
    assert "<" not in provider_text and ">" not in provider_text
    assert shared.task.status == "completed"
    assert shared.task.result_json["section_audio_sha256"] == shared.task.section_audio_sha256
    assert shared.task.result_json["spoken_transcript_sha256"] == shared.task.spoken_transcript_sha256

    shared.task.status = "submitting"
    shared.task.execution_attempt_token = "new-token"
    shared.task.provider_status = "submitting"
    shared.task.result_json = {"owner": "new-attempt"}
    shared.task.loss_telemetry_json = {"provider_loss": 0.5}
    shared.task.completed_at = None
    shared.task.next_reconcile_at = "2099-01-01T00:00:00+00:00"
    shared.operation.result_json = {**shared.operation.result_json, "candidate_state": "new-attempt"}
    task_before = (
        shared.task.status,
        dict(shared.task.result_json),
        dict(shared.task.loss_telemetry_json),
        shared.task.execution_attempt_token,
        shared.task.next_reconcile_at,
    )
    operation_before = json.loads(json.dumps(shared.operation.result_json))
    owner._accept_success(
        shared.task_id,
        "old-token",
        result={"owner": "stale-worker"},
        loss={"provider_loss": 9.9},
    )
    assert (
        shared.task.status,
        shared.task.result_json,
        shared.task.loss_telemetry_json,
        shared.task.execution_attempt_token,
        shared.task.next_reconcile_at,
    ) == task_before
    assert shared.operation.result_json == operation_before

    current_result = {
        "schema_version": 1,
        "section_audio_sha256": shared.task.section_audio_sha256,
        "spoken_transcript_sha256": shared.task.spoken_transcript_sha256,
        "characters": [{"text": "C", "start_sample": 0, "end_sample": 8}],
    }
    owner._accept_success(
        shared.task_id,
        "new-token",
        result=current_result,
        loss={"provider_loss": 0.2},
    )
    assert shared.task.status == "completed"
    assert shared.task.result_json == current_result
    assert shared.task.loss_telemetry_json == {"provider_loss": 0.2}

    corrupt = _AlignmentShared(tmp_path, "identity_mismatch")
    corrupt.audio_path.write_bytes(corrupt.wav_bytes[:-2] + b"\x01\x02")
    corrupt_client = _AlignmentClient()
    corrupt_owner = _alignment_owner(corrupt, corrupt_client, monkeypatch)
    assert corrupt_owner.reconcile_operation(corrupt.operation_id) is True
    assert corrupt_client.calls == []
    assert corrupt.task.status == "failed"
    assert corrupt.task.error_code == "VOICE_ALIGNMENT_FAILED"
    assert corrupt.task.error_details_json["cause_error_code"] == "VOICE_ALIGNMENT_NOT_READY"


def test_ambiguous_alignment_submission_is_parked_without_paid_resubmit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = _AlignmentShared(tmp_path, "ambiguous")
    client = _AlignmentClient(ambiguous=True)
    owner = _alignment_owner(shared, client, monkeypatch)

    assert owner.reconcile_operation(shared.operation_id) is True
    assert len(client.calls) == 1
    assert shared.task.status == "submit_outcome_unknown"
    assert shared.task.next_reconcile_at is None
    assert shared.task.error_code == "VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN"
    assert shared.operation.next_reconcile_at is None
    assert shared.operation.result_json["alignment_status"] == "submit_outcome_unknown"
    assert shared.tts_calls == 0
    assert shared.section_regeneration_calls == 0
    assert shared.artifact_stage_calls == 0
    assert shared.audio_path.read_bytes() == shared.wav_bytes

    assert owner.reconcile_operation(shared.operation_id) is True
    assert len(client.calls) == 1
    assert shared.task.status == "submit_outcome_unknown"
    assert shared.task.next_reconcile_at is None
    assert shared.tts_calls == 0
    assert shared.section_regeneration_calls == 0
    assert shared.artifact_stage_calls == 0

    assert owner.retry_unknown(project_id=shared.project_id, job_id=shared.job_id) == 1
    assert shared.task.status == "queued"
    assert shared.task.retry_count == 1
    assert shared.task.next_reconcile_at is not None
    assert shared.operation.result_json["alignment_status"] == "queued"
    assert len(client.calls) == 1


def _seed_approval_case() -> dict[str, object]:
    factory = get_session_factory()
    suffix = uuid4().hex[:10]
    project_id = f"project_task3_approval_{suffix}"
    channel_id = f"channel_task3_approval_{suffix}"
    job_id = f"job_task3_approval_{suffix}"
    operation_id = f"voiceop_task3_approval_{suffix}"
    profile = {"provider": "elevenlabs", "voice_id": "voice_pvc", "clone_type": "professional"}
    now = utc_now_iso()

    with factory() as session:
        session.add(
            Channel(
                channel_id=channel_id,
                name=f"Task 3 Approval {suffix}",
                language="en",
                niche="testing",
                audience="testing",
                channel_dna_json={},
                channel_context_json={},
                voice_profile_json=profile,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        rows = ArtifactRepository(session)
        artifacts = ArtifactService(session, rows)

        script = artifacts.stage_artifact_revision(
            logical_id=f"project/{project_id}/script",
            artifact_type=ArtifactType.JSON,
            filename="script.json",
            content=b"{\"task\":\"3\"}\n",
            provider_source="task3_test",
        )

        old_audio = artifacts.stage_artifact_revision(
            logical_id=f"project/{project_id}/voice/narration-audio",
            artifact_type=ArtifactType.AUDIO,
            filename="narration.wav",
            content=_canonical_wav(3_000, 10),
            provider_source="task3_test_old",
        )
        assert rows.compare_and_set_current_artifact(
            logical_id=old_audio.logical_id,
            new_artifact_id=old_audio.artifact_id,
            expected_current_artifact_id=None,
        )
        old_timing = artifacts.stage_artifact_revision(
            logical_id=f"project/{project_id}/voice/narration-word-timing-map",
            artifact_type=ArtifactType.JSON,
            filename="narration_word_timing_map.json",
            content=b"{\"status\":\"old\"}\n",
            provider_source="task3_test_old",
        )
        assert rows.compare_and_set_current_artifact(
            logical_id=old_timing.logical_id,
            new_artifact_id=old_timing.artifact_id,
            expected_current_artifact_id=None,
        )

        candidate_audio = artifacts.stage_artifact_revision(
            logical_id=old_audio.logical_id,
            artifact_type=ArtifactType.AUDIO,
            filename="narration.wav",
            content=_canonical_wav(4_800, 25),
            provider_source="voice_task3_candidate",
        )
        candidate_info = validate_canonical_wav_bytes(candidate_audio.filepath.read_bytes())
        timing_payload = {
            "schema_version": "narration_word_timing_map_v1",
            "architecture_version": VOICE_ARCHITECTURE_VERSION,
            "source_script_artifact_id": script.artifact_id,
            "source_narration_audio_artifact_id": candidate_audio.artifact_id,
            "sample_rate_hz": CANONICAL_SAMPLE_RATE,
            "total_sample_count": candidate_info.sample_count,
            "sections": [],
        }
        timing_bytes = (json.dumps(timing_payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        candidate_timing = artifacts.stage_artifact_revision(
            logical_id=old_timing.logical_id,
            artifact_type=ArtifactType.JSON,
            filename="narration_word_timing_map.json",
            content=timing_bytes,
            provider_source="voice_task3_candidate",
        )

        downstream: dict[str, str] = {}
        for field_name in (
            "scene_plan_artifact_id",
            "visual_plan_artifact_id",
            "prompt_bundle_artifact_id",
            "beat_timing_map_artifact_id",
            "scene_asset_manifest_artifact_id",
            "assembly_plan_artifact_id",
            "subtitle_srt_artifact_id",
            "subtitle_vtt_artifact_id",
            "final_video_artifact_id",
        ):
            staged = artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/task3-sentinel/{field_name}",
                artifact_type=ArtifactType.JSON,
                filename=f"{field_name}.json",
                content=(json.dumps({"sentinel": field_name}) + "\n").encode("utf-8"),
                provider_source="task3_test_sentinel",
            )
            downstream[field_name] = staged.artifact_id

        project = Project(
            project_id=project_id,
            channel_id=channel_id,
            name=f"Task 3 Approval {suffix}",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            script_artifact_id=script.artifact_id,
            narration_audio_artifact_id=old_audio.artifact_id,
            narration_word_timing_map_artifact_id=old_timing.artifact_id,
            created_at=now,
            updated_at=now,
            **downstream,
        )
        session.add(project)
        session.add(
            Job(
                job_id=job_id,
                job_type="voice_generation_v4",
                status=JobStatus.COMPLETED,
                progress=100,
                attempt=1,
                retryable=False,
                created_at=now,
                updated_at=now,
                completed_at=now,
                finished_at=now,
            )
        )
        candidate = {
            "candidate_identity_sha256": "d" * 64,
            "narration_audio_artifact_id": candidate_audio.artifact_id,
            "narration_audio_sha256": candidate_audio.artifact.sha256,
            "narration_word_timing_map_artifact_id": candidate_timing.artifact_id,
            "narration_word_timing_map_sha256": candidate_timing.artifact.sha256,
            "source_script_artifact_id": script.artifact_id,
            "sample_rate_hz": CANONICAL_SAMPLE_RATE,
            "total_sample_count": candidate_info.sample_count,
            "channel_voice_profile_sha256": canonical_hash(profile),
            "voice_preset": "quality_cloned_voice",
            "provider_identity_sha256": "a" * 64,
            "generation_settings_snapshot_sha256": "b" * 64,
            "spoken_compilation_sha256": "c" * 64,
            "final_section_identity_sha256": ["e" * 64],
            "reviewable": True,
        }
        session.add(
            VoiceGenerationOperation(
                operation_id=operation_id,
                job_id=job_id,
                project_id=project_id,
                operation_type="generate_all",
                status="completed",
                script_artifact_id=script.artifact_id,
                architecture_version=VOICE_ARCHITECTURE_VERSION,
                expected_narration_audio_artifact_id=old_audio.artifact_id,
                expected_narration_word_timing_map_artifact_id=old_timing.artifact_id,
                voice_preset="quality_cloned_voice",
                tts_profile_id="channel_elevenlabs_pvc",
                voice_id="voice_pvc",
                language="en",
                speaking_rate=1.0,
                provider_identity_json={"provider": "elevenlabs"},
                provider_identity_sha256="a" * 64,
                generation_settings_snapshot_json={"channel_voice_profile_sha256": canonical_hash(profile)},
                generation_settings_snapshot_sha256="b" * 64,
                segmentation_version="narration_segmentation_v2",
                segmentation_input_sha256="f" * 64,
                segment_count=1,
                request_json={},
                result_json={"draft_candidate": candidate},
                created_at=now,
                updated_at=now,
                completed_at=now,
            )
        )
        session.commit()

    return {
        "project_id": project_id,
        "old_audio_id": old_audio.artifact_id,
        "old_timing_id": old_timing.artifact_id,
        "candidate_audio_id": candidate_audio.artifact_id,
        "candidate_timing_id": candidate_timing.artifact_id,
        "downstream": downstream,
    }


def test_voice_approval_uses_logical_current_artifacts_after_project_voice_invalidation() -> None:
    seeded = _seed_approval_case()
    factory = get_session_factory()

    with factory() as session:
        project = session.get(Project, str(seeded["project_id"]))
        old_audio = session.get(Artifact, str(seeded["old_audio_id"]))
        old_timing = session.get(Artifact, str(seeded["old_timing_id"]))
        assert project is not None
        assert old_audio is not None and old_audio.is_current is True
        assert old_timing is not None and old_timing.is_current is True

        project.narration_audio_artifact_id = None
        project.narration_word_timing_map_artifact_id = None
        operation = session.scalar(
            select(VoiceGenerationOperation).where(
                VoiceGenerationOperation.project_id == str(seeded["project_id"])
            )
        )
        assert operation is not None
        operation.expected_narration_audio_artifact_id = None
        operation.expected_narration_word_timing_map_artifact_id = None
        session.commit()

    with factory() as session:
        service = DurableVoiceService(session)
        result = service.approve(
            str(seeded["project_id"]),
            {
                "candidate_narration_audio_artifact_id": seeded["candidate_audio_id"],
                "candidate_narration_word_timing_map_artifact_id": seeded["candidate_timing_id"],
                "expected_narration_audio_artifact_id": None,
                "expected_narration_word_timing_map_artifact_id": None,
            },
        )
        assert result == {
            "narration_audio_artifact_id": seeded["candidate_audio_id"],
            "narration_word_timing_map_artifact_id": seeded["candidate_timing_id"],
            "status": "approved",
        }

    with factory() as session:
        project = session.get(Project, str(seeded["project_id"]))
        old_audio = session.get(Artifact, str(seeded["old_audio_id"]))
        old_timing = session.get(Artifact, str(seeded["old_timing_id"]))
        candidate_audio = session.get(Artifact, str(seeded["candidate_audio_id"]))
        candidate_timing = session.get(Artifact, str(seeded["candidate_timing_id"]))

        assert project is not None
        assert project.narration_audio_artifact_id == seeded["candidate_audio_id"]
        assert project.narration_word_timing_map_artifact_id == seeded["candidate_timing_id"]
        assert old_audio is not None and old_audio.is_current is False
        assert old_timing is not None and old_timing.is_current is False
        assert old_audio.superseded_by_artifact_id == seeded["candidate_audio_id"]
        assert old_timing.superseded_by_artifact_id == seeded["candidate_timing_id"]
        assert candidate_audio is not None and candidate_audio.is_current is True
        assert candidate_timing is not None and candidate_timing.is_current is True


@pytest.mark.parametrize("project_cas_wins", [True, False], ids=["success", "project-race"])
def test_voice_approval_cas_publishes_atomically_and_rolls_back_project_race(
    project_cas_wins: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_approval_case()
    factory = get_session_factory()
    forbidden_calls = {"tts": 0, "pacing": 0, "alignment": 0, "audio_transform": 0}

    def forbidden(name: str):
        def _call(*args, **kwargs):
            forbidden_calls[name] += 1
            raise AssertionError(f"Voice approval must not execute {name}: {args!r} {kwargs!r}")
        return _call

    monkeypatch.setattr(durable_voice_module, "build_voice_pacing_plan", forbidden("pacing"))
    monkeypatch.setattr(task3_module.VoiceTask3AlignmentService, "reconcile_operation", forbidden("alignment"))
    monkeypatch.setattr(voice_audio_module, "assemble_canonical_wav", forbidden("audio_transform"))

    with factory() as session:
        service = DurableVoiceService(session)
        service.provider = SimpleNamespace(synthesize=forbidden("tts"))
        if not project_cas_wins:
            monkeypatch.setattr(
                service.projects,
                "compare_and_set_voice_output_pair",
                lambda **kwargs: False,
            )
        request = {
            "candidate_narration_audio_artifact_id": seeded["candidate_audio_id"],
            "candidate_narration_word_timing_map_artifact_id": seeded["candidate_timing_id"],
            "expected_narration_audio_artifact_id": seeded["old_audio_id"],
            "expected_narration_word_timing_map_artifact_id": seeded["old_timing_id"],
        }
        if project_cas_wins:
            result = service.approve(str(seeded["project_id"]), request)
            assert result == {
                "narration_audio_artifact_id": seeded["candidate_audio_id"],
                "narration_word_timing_map_artifact_id": seeded["candidate_timing_id"],
                "status": "approved",
            }
        else:
            with pytest.raises(AppError) as exc_info:
                service.approve(str(seeded["project_id"]), request)
            assert exc_info.value.error_code == "VOICE_APPROVAL_CONFLICT"

    assert forbidden_calls == {"tts": 0, "pacing": 0, "alignment": 0, "audio_transform": 0}

    with factory() as session:
        project = session.get(Project, str(seeded["project_id"]))
        assert project is not None
        old_audio = session.get(Artifact, str(seeded["old_audio_id"]))
        old_timing = session.get(Artifact, str(seeded["old_timing_id"]))
        candidate_audio = session.get(Artifact, str(seeded["candidate_audio_id"]))
        candidate_timing = session.get(Artifact, str(seeded["candidate_timing_id"]))
        assert old_audio is not None and old_timing is not None
        assert candidate_audio is not None and candidate_timing is not None
        downstream = dict(seeded["downstream"])

        if project_cas_wins:
            assert project.narration_audio_artifact_id == seeded["candidate_audio_id"]
            assert project.narration_word_timing_map_artifact_id == seeded["candidate_timing_id"]
            assert candidate_audio.is_current is True
            assert candidate_timing.is_current is True
            assert old_audio.is_current is False
            assert old_timing.is_current is False
            assert old_audio.superseded_by_artifact_id == seeded["candidate_audio_id"]
            assert old_timing.superseded_by_artifact_id == seeded["candidate_timing_id"]
            assert {name: getattr(project, name) for name in downstream} == {name: None for name in downstream}
        else:
            assert project.narration_audio_artifact_id == seeded["old_audio_id"]
            assert project.narration_word_timing_map_artifact_id == seeded["old_timing_id"]
            assert old_audio.is_current is True
            assert old_timing.is_current is True
            assert old_audio.superseded_by_artifact_id is None
            assert old_timing.superseded_by_artifact_id is None
            assert candidate_audio.is_current is False
            assert candidate_timing.is_current is False
            assert {name: getattr(project, name) for name in downstream} == downstream
