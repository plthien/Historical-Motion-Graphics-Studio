from __future__ import annotations

from threading import Event

from app.core.config import Settings
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderExecutionResult,
    VoiceProviderRegistry,
    VoiceProviderSubmission,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool


def test_shutdown_drains_late_provider_result_and_deletes_attempt_audio(tmp_path) -> None:
    started = Event()
    gate = Event()
    audio_path = tmp_path / "attempt" / "voice.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"late-provider-audio")
    result = VoiceProviderExecutionResult(
        operation_id="voice_operation_v20",
        segment_task_id="voice_segment_task_v20",
        execution_attempt_token="execution_token_v20",
        provider_key="fake_non_cooperative",
        audio_file_path=audio_path,
        source_mime_type="audio/wav",
        sample_rate_hz=48_000,
        channels=1,
        sample_width_bytes=2,
        duration_seconds=1.0,
    )
    submission = VoiceProviderSubmission(status="completed", result=result)
    pool: VoiceProviderExecutionPool[object] = VoiceProviderExecutionPool(max_workers=1)

    def late_result() -> VoiceProviderSubmission:
        started.set()
        gate.wait(timeout=5)
        return submission

    assert pool.dispatch("submit:late", late_result) is True
    assert started.wait(timeout=5)
    controller = VoiceProviderActionController(
        session_factory=lambda: None,
        registry=VoiceProviderRegistry(),
        pool=pool,
        settings=Settings(root_dir=tmp_path),
    )

    gate.set()
    controller.close()

    assert not audio_path.exists()
    assert pool.drain_completed() == []


def test_runtime_controller_retry_backoff_uses_next_retry_count() -> None:
    import inspect

    source = inspect.getsource(VoiceProviderActionController._record_provider_failure)
    assert "context.retry_count + 1" in source
