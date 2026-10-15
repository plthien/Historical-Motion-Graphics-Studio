from __future__ import annotations

import hashlib
from types import SimpleNamespace

from app.db.session import get_session_factory
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController
from app.modules.v4.voice_provider_contracts import VoiceProviderCapabilities, VoiceProviderRegistry, VoiceProviderSubmission
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.modules.v4.voice_service import VoiceService
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from app.repositories.voice_provider_execution import VoiceProviderTaskContext
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task, _wait_for_harvest


def _context_for(segment_index: int) -> VoiceProviderTaskContext:
    texts = ["A.", "B.", "C."]
    segments = [
        {
            "segment_id": f"segment_{index}",
            "segment_order": index + 1,
            "section_id": "sec",
            "source_start": index * 2,
            "source_end": index * 2 + len(text),
            "exact_text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        for index, text in enumerate(texts)
    ]
    current = segments[segment_index]
    return VoiceProviderTaskContext(
        operation_id="operation",
        segment_task_id="task",
        segment_id=str(current["segment_id"]),
        segment_order=int(current["segment_order"]),
        section_id=str(current["section_id"]),
        provider_key="elevenlabs",
        execution_attempt_token="attempt",
        exact_text=str(current["exact_text"]),
        voice_id="voice",
        language="en",
        speaking_rate=1.0,
        sample_rate_hz=24_000,
        output_format="wav_pcm_s16le",
        attempt_no=1,
        retry_count=0,
        max_attempts=3,
        provider_identity_json={"execution_identity": {"provider_key": "elevenlabs"}},
        generation_settings_json={"provider_options": {"model_id": "eleven_multilingual_v2", "narration_language": "en"}},
        operation_request_json={"segmentation": {"segments": segments}},
        cancel_requested_at=None,
        provider_task_id=None,
        provider_handle_json=None,
    )


def test_previous_next_context_comes_from_frozen_segmentation() -> None:
    first = VoiceProviderActionController._continuity_context(_context_for(0))
    middle = VoiceProviderActionController._continuity_context(_context_for(1))
    last = VoiceProviderActionController._continuity_context(_context_for(2))

    assert first == {"previous_text": None, "next_text": "B."}
    assert middle == {"previous_text": "A.", "next_text": "C."}
    assert last == {"previous_text": "B.", "next_text": None}


def test_neighbor_context_change_prevents_segment_artifact_reuse() -> None:
    provider_identity = {"execution_identity": {"provider_key": "elevenlabs", "continuity_mode": "previous_next_text_v1"}}

    def segmentation(previous: str) -> dict:
        return {
            "segments": [
                {"segment_id": "previous", "segment_order": 1, "section_id": "sec", "source_start": 0, "source_end": len(previous), "exact_text": previous, "text_sha256": hashlib.sha256(previous.encode()).hexdigest()},
                {"segment_id": "target", "segment_order": 2, "section_id": "sec", "source_start": 11, "source_end": 27, "exact_text": "Target sentence.", "text_sha256": hashlib.sha256(b"Target sentence.").hexdigest()},
                {"segment_id": "next", "segment_order": 3, "section_id": "sec", "source_start": 29, "source_end": 39, "exact_text": "Next text.", "text_sha256": hashlib.sha256(b"Next text.").hexdigest()},
            ]
        }

    old_context = VoiceService._synthesis_context_hashes(segmentation("Context A."), provider_identity)
    new_segmentation = segmentation("Context B.")
    new_context = VoiceService._synthesis_context_hashes(new_segmentation, provider_identity)
    assert old_context["target"] != new_context["target"]

    timing = {
        "generation_settings_snapshot_sha256": "settings_hash",
        "provider_identity_sha256": "provider_hash",
        "segments": [
            {
                "segment_id": "target",
                "text_sha256": hashlib.sha256(b"Target sentence.").hexdigest(),
                "source_start": 11,
                "source_end": 27,
                "segment_audio_artifact_id": "artifact_old",
                "segment_task_id": "task_old",
                "provider_identity_sha256": "provider_hash",
                "generation_settings_snapshot_sha256": "settings_hash",
                "synthesis_context_sha256": old_context["target"],
            }
        ],
    }

    class ReuseProbe:
        operations = SimpleNamespace(list_for_project=lambda _project_id: [])

        def _load_current_pair(self, project, *, validate_segment_outputs=True):
            return None, None, timing

        def _segment_artifact_valid(self, *, entry, expected_synthesis_context_hash, **_kwargs):
            return entry.get("synthesis_context_sha256") == expected_synthesis_context_hash

    reusable = VoiceService._reusable_segments(
        ReuseProbe(),
        SimpleNamespace(project_id="project", narration_word_timing_map_artifact_id="timing"),
        new_segmentation,
        "settings_hash",
        "provider_hash",
        new_context,
        {},
    )

    assert "target" not in reusable


def test_ambiguous_submit_result_parks_task_without_retry() -> None:
    class AmbiguousSubmitProvider:
        provider_key = "ambiguous_submit"

        def __init__(self) -> None:
            self.submit_calls = 0

        def capabilities(self) -> VoiceProviderCapabilities:
            return VoiceProviderCapabilities(execution_model="synchronous", supports_idempotency=False, supports_submission_lookup=False)

        def list_voices(self, profile=None):
            return ()

        def readiness(self, profile):
            raise AssertionError("readiness not used")

        def execution_identity(self, profile):
            return {"schema_version": 1, "provider_key": self.provider_key}

        def submit(self, request, control):
            self.submit_calls += 1
            return VoiceProviderSubmission(status="ambiguous")

        def poll(self, handle, control):
            raise AssertionError("poll not used")

        def reconcile_submission(self, request, handle, control):
            raise AssertionError("reconcile not used")

        def cancel(self, handle, control):
            raise AssertionError("cancel not used")

    provider = AmbiguousSubmitProvider()
    task_id, _ = _seed_submitting_task(provider)
    registry = VoiceProviderRegistry()
    registry.register(provider)
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=1),
    )
    try:
        controller.dispatch_submit(task_id)
        progress = _wait_for_harvest(lambda: controller.harvest_submit(task_id))

        assert progress.state == "submit_outcome_unknown"
        assert provider.submit_calls == 1
        with get_session_factory()() as session:
            task = VoiceDurableWorkingRepository(session).get_task(task_id)
            assert task is not None
            assert task.status == "submit_outcome_unknown"
            assert task.retry_count == 0
            assert task.next_reconcile_at is None
    finally:
        controller.close()


def test_elevenlabs_stale_submission_reconciles_ambiguous() -> None:
    eleven = ElevenLabsProviderAdapter()
    assert eleven.reconcile_submission(None, None, None).status == "ambiguous"  # type: ignore[arg-type]
