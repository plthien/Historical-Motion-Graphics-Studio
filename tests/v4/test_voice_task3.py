from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import httpx
import pytest

from app.api.errors import AppError
from app.db.enums import ArtifactStatus, ArtifactType
from app.modules.v4 import voice_audio as voice_audio_module
from app.modules.v4 import voice_task3 as task3_module
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.elevenlabs_client import ElevenLabsHttpClient
from app.modules.v4.voice_audio import CANONICAL_MIME, CANONICAL_SAMPLE_RATE, validate_canonical_wav_bytes
from app.modules.v4.voice_service import canonical_hash
from app.modules.v4.voice_spoken_text import compile_voice_spoken_text
from app.modules.v4.voice_task3 import (
    VoiceTask3AlignmentService,
    build_project_word_timing,
    seconds_to_samples,
    validate_character_alignment,
)


def _script(*, with_pause: bool = False) -> dict:
    return {
        "script_sections": [
            {
                "section_id": "sec_001",
                "narration": "Code D3444 now.",
                "spoken_overrides": [
                    {
                        "id": "override_001",
                        "source_start": 5,
                        "source_end": 10,
                        "spoken_text": "D three four four four",
                    }
                ],
                "delivery_cues": (
                    [
                        {
                            "id": "cue_001",
                            "kind": "pause_after",
                            "source_offset": 10,
                            "pause_strength": "medium",
                        }
                    ]
                    if with_pause
                    else []
                ),
            },
            {
                "section_id": "sec_002",
                "narration": "Second line.",
                "spoken_overrides": [],
                "delivery_cues": [],
            },
        ]
    }


def _canonical_wav(sample_count: int, sample_value: int) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(int(sample_value).to_bytes(2, "little", signed=True) * sample_count)
    return output.getvalue()


def _character_timing(text: str, *, samples_per_character: int = 20) -> list[dict]:
    return [
        {
            "character_index": index,
            "text": character,
            "spoken_start": index,
            "spoken_end": index + 1,
            "start_sample": index * samples_per_character,
            "end_sample": (index + 1) * samples_per_character,
        }
        for index, character in enumerate(text)
    ]


class _CandidateSession:
    def __init__(self, voice_profile: dict) -> None:
        self.voice_profile = voice_profile
        self.commits = 0
        self.rollbacks = 0

    def get(self, model, key):
        _ = (model, key)
        return SimpleNamespace(voice_profile_json=self.voice_profile)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _ArtifactRows:
    def __init__(self, rows: dict[str, object]) -> None:
        self.rows = rows

    def get(self, artifact_id: str):
        return self.rows.get(artifact_id)


class _CandidateArtifacts:
    def __init__(self, paths: dict[str, Path]) -> None:
        self.paths = paths
        self.staged: list[SimpleNamespace] = []

    def resolve_file_path(self, artifact) -> Path:
        return self.paths[artifact.artifact_id]

    def stage_artifact_revision(self, **kwargs):
        artifact_id = f"draft_artifact_{len(self.staged) + 1}"
        staged = SimpleNamespace(
            artifact_id=artifact_id,
            artifact=SimpleNamespace(duration_sec=None, mime_type=None),
            kwargs=kwargs,
        )
        self.staged.append(staged)
        return staged


class _CandidateService:
    def __init__(self, *, project, script, rows, artifacts) -> None:
        self.project = project
        self.script = script
        self.artifact_rows = rows
        self.artifacts = artifacts

    def _project_required(self, project_id: str):
        assert project_id == self.project.project_id
        return self.project

    def _load_sources(self, project):
        assert project is self.project
        return self.script, {}


def test_task3_finalizes_multi_section_candidate_without_publishing_project_pointers(tmp_path: Path) -> None:
    script = _script()
    compiled = compile_voice_spoken_text(script)
    voice_profile = {"provider": "elevenlabs", "voice_id": "voice_pvc", "clone_type": "professional"}
    session = _CandidateSession(voice_profile)
    downstream_sentinels = {
        "scene_plan_artifact_id": "scene_plan_sentinel",
        "visual_plan_artifact_id": "visual_plan_sentinel",
        "prompt_bundle_artifact_id": "prompt_bundle_sentinel",
        "beat_timing_map_artifact_id": "beat_timing_map_sentinel",
        "scene_asset_manifest_artifact_id": "scene_asset_manifest_sentinel",
        "assembly_plan_artifact_id": "assembly_plan_sentinel",
        "subtitle_srt_artifact_id": "subtitle_srt_sentinel",
        "subtitle_vtt_artifact_id": "subtitle_vtt_sentinel",
        "final_video_artifact_id": "final_video_sentinel",
    }
    project = SimpleNamespace(
        project_id="project_task3",
        channel_id="channel_task3",
        script_artifact_id="script_task3",
        narration_audio_artifact_id="approved_audio_old",
        narration_word_timing_map_artifact_id="approved_timing_old",
        **downstream_sentinels,
    )

    rows: dict[str, object] = {}
    paths: dict[str, Path] = {}
    final_sections: list[dict] = []
    tasks: list[SimpleNamespace] = []
    expected_total_samples = 0
    for index, compiled_section in enumerate(compiled.sections, 1):
        section_id = str(compiled_section["section_id"])
        wav_bytes = _canonical_wav(8_000 + index * 500, index * 100)
        info = validate_canonical_wav_bytes(wav_bytes)
        artifact_id = f"final_section_audio_{index}"
        path = tmp_path / f"{section_id}.wav"
        path.write_bytes(wav_bytes)
        rows[artifact_id] = SimpleNamespace(
            artifact_id=artifact_id,
            status=ArtifactStatus.READY,
            deleted_at=None,
            sha256=info.sha256,
        )
        paths[artifact_id] = path
        transcript = str(compiled_section["canonical_spoken_text"])
        transcript_sha = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        final_sections.append(
            {
                "section_id": section_id,
                "section_order": index,
                "section_audio_artifact_id": artifact_id,
                "audio_sha256": info.sha256,
                "sample_rate_hz": CANONICAL_SAMPLE_RATE,
                "sample_count": info.sample_count,
                "spoken_transcript": transcript,
                "spoken_transcript_sha256": transcript_sha,
                "final_section_identity_sha256": hashlib.sha256(f"section-{index}".encode()).hexdigest(),
            }
        )
        tasks.append(
            SimpleNamespace(
                section_id=section_id,
                section_order=index,
                status="completed",
                result_json={"characters": _character_timing(transcript)},
                spoken_transcript=transcript,
                spoken_transcript_sha256=transcript_sha,
                voice_alignment_task_id=f"alignment_task_{index}",
                loss_telemetry_json={"provider_loss": 0.01 * index},
            )
        )
        expected_total_samples += info.sample_count

    artifacts = _CandidateArtifacts(paths)
    service = _CandidateService(
        project=project,
        script=script,
        rows=_ArtifactRows(rows),
        artifacts=artifacts,
    )
    operation = SimpleNamespace(
        operation_id="voice_operation_task3",
        job_id="voice_job_task3",
        project_id=project.project_id,
        status="finalizing",
        script_artifact_id=project.script_artifact_id,
        request_json={"spoken_compilation_sha256": compiled.compilation_sha256},
        result_json={"final_sections": final_sections},
        generation_settings_snapshot_json={"channel_voice_profile_sha256": canonical_hash(voice_profile)},
        generation_settings_snapshot_sha256="a" * 64,
        provider_identity_sha256="b" * 64,
        voice_preset="quality_cloned_voice",
        error_code=None,
        error_message=None,
        error_details_json=None,
        completed_at=None,
        next_reconcile_at=None,
        reconcile_owner=None,
        reconcile_token=None,
        reconcile_lease_expires_at=None,
        updated_at="2026-08-30T00:00:00+00:00",
    )
    job = SimpleNamespace(
        status="running",
        retryable=False,
        progress=90,
        error_code=None,
        error_message=None,
        error_details_json=None,
        completed_at=None,
        finished_at=None,
        updated_at="2026-08-30T00:00:00+00:00",
        heartbeat_at=None,
    )

    owner = VoiceTask3AlignmentService(lambda: session)
    assert owner._finalize_candidate(session, service, operation, job, final_sections, tasks) is True

    assert project.narration_audio_artifact_id == "approved_audio_old"
    assert project.narration_word_timing_map_artifact_id == "approved_timing_old"
    assert {name: getattr(project, name) for name in downstream_sentinels} == downstream_sentinels
    assert operation.status == "completed"
    assert operation.result_json["task_3_status"] == "reviewable"
    candidate = operation.result_json["draft_candidate"]
    assert candidate["reviewable"] is True
    assert candidate["narration_audio_artifact_id"] == "draft_artifact_1"
    assert candidate["narration_word_timing_map_artifact_id"] == "draft_artifact_2"
    assert len(artifacts.staged) == 2
    assert artifacts.staged[0].artifact.mime_type == CANONICAL_MIME

    full_audio = artifacts.staged[0].kwargs["content"]
    full_info = validate_canonical_wav_bytes(full_audio)
    assert full_info.sample_count == expected_total_samples
    timing_map = json.loads(artifacts.staged[1].kwargs["content"].decode("utf-8"))
    assert timing_map["source_narration_audio_artifact_id"] == "draft_artifact_1"
    assert timing_map["total_sample_count"] == expected_total_samples
    assert [section["section_id"] for section in timing_map["sections"]] == ["sec_001", "sec_002"]
    serialized = json.dumps(timing_map, sort_keys=True)
    assert "source_scene_plan_revision_id" not in serialized
    assert '"scenes"' not in serialized
    assert '"beats"' not in serialized
    override_words = [word for word in timing_map["sections"][0]["words"] if word["text"] in {"D", "three", "four"}]
    assert override_words
    assert all(word["authored_start"] == 5 and word["authored_end"] == 10 for word in override_words)


def test_forced_alignment_posts_canonical_spoken_text_without_ssml() -> None:
    compiled = compile_voice_spoken_text(_script(with_pause=True))
    section = compiled.sections[0]
    canonical = str(section["canonical_spoken_text"])
    assert "D three four four four" in canonical
    assert "<" not in canonical and ">" not in canonical
    assert '<break time="0.55s" />' in str(section["provider_text"])

    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        observed["path"] = request.url.path
        observed["content_type"] = request.headers.get("content-type")
        observed["body"] = body
        return httpx.Response(200, json={"characters": [], "words": [], "loss": 0.1}, request=request)

    transport_client = httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    )
    client = ElevenLabsHttpClient(api_key="secret", client=transport_client)
    try:
        response = client.forced_alignment(audio_bytes=b"RIFF-task3-test", text=canonical)
    finally:
        transport_client.close()

    assert response.payload["loss"] == 0.1
    assert observed["path"] == "/v1/forced-alignment"
    assert str(observed["content_type"]).startswith("multipart/form-data;")
    wire = bytes(observed["body"]).decode("latin-1")
    assert 'name="file"; filename="section.wav"' in wire
    assert 'name="text"' in wire
    assert canonical in wire
    assert "<break" not in wire


def test_alignment_integer_sample_rounding_monotonicity_and_range_fences() -> None:
    assert seconds_to_samples(0.5 / CANONICAL_SAMPLE_RATE) == 1
    characters, _telemetry = validate_character_alignment(
        {
            "characters": [
                {"text": "A", "start": 0.0, "end": 1 / CANONICAL_SAMPLE_RATE},
                {"text": "B", "start": 1 / CANONICAL_SAMPLE_RATE, "end": 2 / CANONICAL_SAMPLE_RATE},
            ],
            "words": [{"text": "vendor-token", "loss": 0.25}],
            "loss": 0.5,
        },
        transcript="AB",
        sample_count=100,
    )
    assert [(item["start_sample"], item["end_sample"]) for item in characters] == [(0, 1), (1, 2)]

    with pytest.raises(AppError) as non_monotonic:
        validate_character_alignment(
            {"characters": [{"text": "A", "start": 0.002, "end": 0.001}]},
            transcript="A",
            sample_count=1_000,
        )
    assert non_monotonic.value.error_code == "VOICE_ALIGNMENT_OUT_OF_RANGE"

    with pytest.raises(AppError) as out_of_range:
        validate_character_alignment(
            {"characters": [{"text": "A", "start": 0.0, "end": 0.01}]},
            transcript="A",
            sample_count=100,
        )
    assert out_of_range.value.error_code == "VOICE_ALIGNMENT_OUT_OF_RANGE"


def _single_word_character_timing(*, start_sample: int, end_sample: int) -> list[dict]:
    return [
        {
            "character_index": 0,
            "text": "Alpha",
            "spoken_start": 0,
            "spoken_end": 5,
            "start_sample": start_sample,
            "end_sample": end_sample,
        }
    ]


def test_project_owned_word_requires_positive_authored_and_sample_ranges() -> None:
    words = build_project_word_timing(
        transcript="Alpha",
        character_timing=_single_word_character_timing(start_sample=100, end_sample=200),
        authored_spoken_mapping=[],
        authored_section_start=0,
        authored_length=5,
        section_id="sec_001",
    )
    assert words[0]["authored_start"] == 0
    assert words[0]["authored_end"] == 5
    assert words[0]["start_sample"] == 100
    assert words[0]["end_sample"] == 200

    for start_sample, end_sample in ((100, 100), (200, 100)):
        with pytest.raises(AppError) as exc_info:
            build_project_word_timing(
                transcript="Alpha",
                character_timing=_single_word_character_timing(
                    start_sample=start_sample,
                    end_sample=end_sample,
                ),
                authored_spoken_mapping=[],
                authored_section_start=0,
                authored_length=5,
                section_id="sec_001",
            )
        assert exc_info.value.error_code == "VOICE_ALIGNMENT_OUT_OF_RANGE"
        assert exc_info.value.details["section_id"] == "sec_001"
        assert exc_info.value.details["section_word_index"] == 0


@pytest.mark.parametrize(
    ("transcript", "mapping", "character_timing"),
    [
        (
            "Alpha",
            [{"section_authored_start": 0, "section_authored_end": 0, "spoken_start": 0, "spoken_end": 5}],
            _single_word_character_timing(start_sample=100, end_sample=200),
        ),
        (
            " Alpha",
            [{"section_authored_start": 2, "section_authored_end": 1, "spoken_start": 0, "spoken_end": 6}],
            [{"text": "Alpha", "spoken_start": 1, "spoken_end": 6, "start_sample": 100, "end_sample": 200}],
        ),
    ],
)
def test_project_owned_word_rejects_zero_or_reversed_authored_range(
    transcript: str,
    mapping: list[dict],
    character_timing: list[dict],
) -> None:
    with pytest.raises(AppError) as exc_info:
        build_project_word_timing(
            transcript=transcript,
            character_timing=character_timing,
            authored_spoken_mapping=mapping,
            authored_section_start=0,
            authored_length=6,
            section_id="sec_001",
        )
    assert exc_info.value.error_code == "VOICE_ALIGNMENT_OUT_OF_RANGE"


def test_spoken_override_words_may_share_one_positive_authored_owner() -> None:
    words = build_project_word_timing(
        transcript="one two",
        character_timing=[
            {"text": "one", "spoken_start": 0, "spoken_end": 3, "start_sample": 100, "end_sample": 200},
            {"text": "two", "spoken_start": 4, "spoken_end": 7, "start_sample": 300, "end_sample": 400},
        ],
        authored_spoken_mapping=[
            {"section_authored_start": 0, "section_authored_end": 20, "spoken_start": 0, "spoken_end": 7}
        ],
        authored_section_start=100,
        authored_length=20,
        section_id="sec_001",
    )
    assert [(word["authored_start"], word["authored_end"]) for word in words] == [
        (100, 120),
        (100, 120),
    ]


def test_zero_duration_word_uses_only_provider_measured_adjacent_separator_cell() -> None:
    words = build_project_word_timing(
        transcript="for a project",
        character_timing=[
            {"text": "f", "spoken_start": 0, "spoken_end": 1, "start_sample": 0, "end_sample": 100},
            {"text": "o", "spoken_start": 1, "spoken_end": 2, "start_sample": 100, "end_sample": 200},
            {"text": "r", "spoken_start": 2, "spoken_end": 3, "start_sample": 200, "end_sample": 300},
            {"text": " ", "spoken_start": 3, "spoken_end": 4, "start_sample": 300, "end_sample": 400},
            {"text": "a", "spoken_start": 4, "spoken_end": 5, "start_sample": 400, "end_sample": 400},
            {"text": " ", "spoken_start": 5, "spoken_end": 6, "start_sample": 400, "end_sample": 500},
            {"text": "project", "spoken_start": 6, "spoken_end": 13, "start_sample": 500, "end_sample": 1_200},
        ],
        authored_spoken_mapping=[],
        authored_section_start=0,
        authored_length=13,
        section_id="sec_001",
    )
    assert [(word["text"], word["start_sample"], word["end_sample"]) for word in words] == [
        ("for", 0, 300),
        ("a", 400, 500),
        ("project", 500, 1_200),
    ]


def test_zero_duration_word_without_measured_separator_cell_is_rejected() -> None:
    characters, _telemetry = validate_character_alignment(
        {"characters": [{"text": "A", "start": 0.001, "end": 0.001}]},
        transcript="A",
        sample_count=100,
    )
    assert characters[0]["start_sample"] == characters[0]["end_sample"]
    with pytest.raises(AppError) as exc_info:
        build_project_word_timing(
            transcript="A",
            character_timing=characters,
            authored_spoken_mapping=[],
            authored_section_start=0,
            authored_length=1,
            section_id="sec_001",
        )
    assert exc_info.value.error_code == "VOICE_ALIGNMENT_OUT_OF_RANGE"


def test_ambiguous_alignment_submission_hard_parks_until_explicit_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    task = SimpleNamespace(
        status="submitting",
        provider_status="submitting",
        error_code=None,
        error_message=None,
        error_details_json=None,
        next_reconcile_at="2026-08-30T00:01:00+00:00",
        updated_at="2026-08-30T00:00:00+00:00",
        state_revision=3,
        section_id="sec_001",
        retry_count=0,
        last_retry_at=None,
        submit_attempt_token="submit-token",
        execution_attempt_token="execution-token",
        submit_started_at="2026-08-30T00:00:00+00:00",
        execution_started_at="2026-08-30T00:00:00+00:00",
    )
    VoiceTask3AlignmentService._mark_unknown(task, reason="transport_outcome_ambiguous")
    assert task.status == "submit_outcome_unknown"
    assert task.next_reconcile_at is None
    assert task.error_code == "VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN"

    operation = SimpleNamespace(
        operation_id="voice_operation_unknown",
        project_id="project_unknown",
        status="finalizing",
        result_json={"alignment_status": "submit_outcome_unknown"},
        next_reconcile_at=None,
        updated_at=None,
    )
    job = SimpleNamespace(
        status="running",
        progress=86,
        error_code=None,
        error_message=None,
        updated_at=None,
    )

    class _Session:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    class _Operations:
        def get_by_job_id(self, job_id: str):
            assert job_id == "job_unknown"
            return operation

    class _Jobs:
        def get(self, job_id: str):
            assert job_id == "job_unknown"
            return job

    class _Service:
        def __init__(self, session) -> None:
            self.operations = _Operations()
            self.jobs = _Jobs()

    monkeypatch.setattr(task3_module, "DurableVoiceService", _Service)
    session = _Session()
    owner = VoiceTask3AlignmentService(lambda: session)
    monkeypatch.setattr(owner, "_tasks", lambda _session, operation_id: [task])

    assert operation.next_reconcile_at is None
    assert owner.retry_unknown(project_id="project_unknown", job_id="job_unknown") == 1
    assert task.status == "queued"
    assert task.retry_count == 1
    assert task.next_reconcile_at is not None
    assert operation.next_reconcile_at == task.next_reconcile_at
    assert operation.result_json["alignment_status"] == "queued"


def test_approval_second_artifact_cas_failure_rolls_back_before_project_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {"provider": "elevenlabs", "voice_id": "voice_pvc"}
    project = SimpleNamespace(
        project_id="project_approval",
        channel_id="channel_approval",
        script_artifact_id="script_current",
        narration_audio_artifact_id=None,
        narration_word_timing_map_artifact_id=None,
        scene_plan_artifact_id="scene_should_survive_failed_cas",
    )
    audio_bytes = b"candidate-audio-bytes"
    audio_sha = hashlib.sha256(audio_bytes).hexdigest()
    timing_payload = {
        "source_script_artifact_id": "script_current",
        "source_narration_audio_artifact_id": "draft_audio",
        "sample_rate_hz": CANONICAL_SAMPLE_RATE,
        "total_sample_count": 123,
        "sections": [],
    }
    timing_bytes = (json.dumps(timing_payload, sort_keys=True) + "\n").encode("utf-8")
    timing_sha = hashlib.sha256(timing_bytes).hexdigest()
    audio_path = tmp_path / "draft.wav"
    timing_path = tmp_path / "timing.json"
    audio_path.write_bytes(audio_bytes)
    timing_path.write_bytes(timing_bytes)

    audio = SimpleNamespace(
        artifact_id="draft_audio",
        logical_id="project/project_approval/voice/narration-audio",
        status=ArtifactStatus.READY,
        deleted_at=None,
        artifact_type=ArtifactType.AUDIO,
        sha256=audio_sha,
    )
    timing = SimpleNamespace(
        artifact_id="draft_timing",
        logical_id="project/project_approval/voice/narration-word-timing-map",
        status=ArtifactStatus.READY,
        deleted_at=None,
        artifact_type=ArtifactType.JSON,
        sha256=timing_sha,
    )
    candidate = {
        "reviewable": True,
        "source_script_artifact_id": "script_current",
        "channel_voice_profile_sha256": canonical_hash(profile),
        "narration_audio_artifact_id": audio.artifact_id,
        "narration_audio_sha256": audio_sha,
        "narration_word_timing_map_artifact_id": timing.artifact_id,
        "narration_word_timing_map_sha256": timing_sha,
        "total_sample_count": 123,
    }
    operation = SimpleNamespace(
        status="completed",
        script_artifact_id="script_current",
        expected_narration_audio_artifact_id=None,
        expected_narration_word_timing_map_artifact_id=None,
        result_json={"draft_candidate": candidate},
    )

    class _Session:
        def __init__(self) -> None:
            self.rollbacks = 0
            self.commits = 0

        def get(self, model, key):
            _ = (model, key)
            return SimpleNamespace(voice_profile_json=profile)

        def rollback(self):
            self.rollbacks += 1

        def commit(self):
            self.commits += 1

    class _Rows:
        def __init__(self) -> None:
            self.cas_calls: list[tuple[str, str, str | None]] = []

        def get(self, artifact_id: str):
            return {audio.artifact_id: audio, timing.artifact_id: timing}.get(artifact_id)

        def get_current_by_logical_id(self, logical_id: str):
            if logical_id == audio.logical_id:
                return None
            if logical_id == timing.logical_id:
                return None
            raise AssertionError(f"unexpected logical_id: {logical_id}")

        def compare_and_set_current_artifact(self, *, logical_id: str, new_artifact_id: str, expected_current_artifact_id: str | None) -> bool:
            self.cas_calls.append((logical_id, new_artifact_id, expected_current_artifact_id))
            return len(self.cas_calls) == 1

    class _Artifacts:
        def resolve_file_path(self, artifact):
            return {audio.artifact_id: audio_path, timing.artifact_id: timing_path}[artifact.artifact_id]

    class _Operations:
        def list_for_project(self, project_id: str):
            assert project_id == project.project_id
            return [operation]

    class _Projects:
        def __init__(self) -> None:
            self.cas_calls = 0

        def compare_and_set_voice_output_pair(self, **kwargs):
            self.cas_calls += 1
            return True

    session = _Session()
    rows = _Rows()
    projects = _Projects()
    service = object.__new__(DurableVoiceService)
    service.session = session
    service.operations = _Operations()
    service.artifact_rows = rows
    service.artifacts = _Artifacts()
    service.projects = projects
    service._project_required = lambda project_id: project

    monkeypatch.setattr(
        voice_audio_module,
        "validate_canonical_wav_bytes",
        lambda data: SimpleNamespace(sha256=audio_sha, sample_count=123, sample_rate_hz=CANONICAL_SAMPLE_RATE),
    )

    with pytest.raises(AppError) as exc_info:
        service.approve(
            project.project_id,
            {
                "candidate_narration_audio_artifact_id": audio.artifact_id,
                "candidate_narration_word_timing_map_artifact_id": timing.artifact_id,
                "expected_narration_audio_artifact_id": None,
                "expected_narration_word_timing_map_artifact_id": None,
            },
        )
    assert exc_info.value.error_code == "VOICE_APPROVAL_CONFLICT"
    assert len(rows.cas_calls) == 2
    assert session.rollbacks == 1
    assert session.commits == 0
    assert projects.cas_calls == 0
    assert project.narration_audio_artifact_id is None
    assert project.narration_word_timing_map_artifact_id is None
    assert project.scene_plan_artifact_id == "scene_should_survive_failed_cas"
