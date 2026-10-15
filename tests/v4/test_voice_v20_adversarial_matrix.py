from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class EvidenceCase:
    case_id: str
    category: str
    path: str
    marker: str


def _case(case_id: str, category: str, path: str, marker: str) -> EvidenceCase:
    return EvidenceCase(case_id, category, path, marker)


CASES = [
    # Provider execution models and common lifecycle — 10
    _case("P01", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "FakeImmediateProvider"),
    _case("P02", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "FakeBlockingProvider"),
    _case("P03", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "FakeAsyncProvider"),
    _case("P04", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "execution_model"),
    _case("P05", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "waiting_provider"),
    _case("P06", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "dispatch_poll"),
    _case("P07", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "materializing"),
    _case("P08", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "execution_attempt_token"),
    _case("P09", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "provider.provider_key"),
    _case("P10", "provider_modes", "tests/v4/test_voice_durable_provider_lifecycle.py", "controller.close()"),

    # Ambiguous/stale submission recovery — 10
    _case("S01", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "CountingAsyncProvider"),
    _case("S02", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "AmbiguousNonIdempotentProvider"),
    _case("S03", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "list_stale_submitting_tasks"),
    _case("S04", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "dispatch_reconcile_submission"),
    _case("S05", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "original_key == replay_key"),
    _case("S06", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "task.retry_count == 1"),
    _case("S07", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "provider.submit_calls == 1"),
    _case("S08", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "waiting_provider"),
    _case("S09", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "submit_outcome_unknown"),
    _case("S10", "submission_recovery", "tests/v4/test_voice_durable_submission_recovery.py", "provider.submit_calls == 0"),

    # Retry, cancellation, stale-result fencing — 12
    _case("R01", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "TransientThenSuccessProvider"),
    _case("R02", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "attempt_no = 9"),
    _case("R03", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "retry_count"),
    _case("R04", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "1.5 <= delay <= 2.5"),
    _case("R05", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "max_attempts = 1"),
    _case("R06", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "operation.status == \"failed\""),
    _case("R07", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "late_result_from_superseded_attempt"),
    _case("R08", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "accepted.state == \"stale\""),
    _case("R09", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "not late_path.exists()"),
    _case("R10", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "cancellation_survives_controller_restart"),
    _case("R11", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "cancel_requested_at is not None"),
    _case("R12", "retry_cancel_fencing", "tests/v4/test_voice_durable_retry_cancel_fencing.py", "execution_attempt_token == old_token"),

    # Restart-safe finalization/publication — 10
    _case("F01", "finalization", "tests/v4/test_voice_durable_finalization.py", "run_operation\" not in source"),
    _case("F02", "finalization", "tests/v4/test_voice_durable_finalization.py", "final_sections"),
    _case("F03", "finalization", "tests/v4/test_voice_durable_finalization.py", "task_3_status"),
    _case("F04", "finalization", "tests/v4/test_voice_durable_finalization.py", "restart_safe_finalization"),
    _case("F05", "finalization", "tests/v4/test_voice_durable_finalization.py", "project.narration_audio_artifact_id is None"),
    _case("F06", "finalization", "tests/v4/test_voice_durable_finalization.py", "second_process"),
    _case("F07", "finalization", "tests/v4/test_voice_durable_finalization.py", "stale_finalizer"),
    _case("F08", "finalization", "tests/v4/test_voice_durable_finalization.py", "VOICE_UPSTREAM_STALE"),
    _case("F09", "finalization", "tests/v4/test_voice_durable_finalization.py", "finalizer_refuses_incomplete_segment_set"),
    _case("F10", "finalization", "tests/v4/test_voice_durable_finalization.py", "VOICE_SEGMENTS_INCOMPLETE"),

    # Atomic cutover, provider-neutral settings, Job projection — 10
    _case("A01", "cutover_settings_projection", "tests/v4/test_voice_rq_cutover.py", "test_platform_rq_runtime_is_removed"),
    _case("A02", "cutover_settings_projection", "tests/v4/test_voice_rq_cutover.py", "voice_worker.py"),
    _case("A03", "cutover_settings_projection", "tests/v4/test_voice_rq_cutover.py", "voice_recovery.py"),
    _case("A04", "cutover_settings_projection", "tests/v4/test_voice_rq_cutover.py", "enqueue("),
    _case("A05", "cutover_settings_projection", "tests/v4/test_voice_first_task1_foundation.py", "test_channel_profile_supports_both_presets"),
    _case("A06", "cutover_settings_projection", "tests/v4/test_voice_first_task1_foundation.py", "model_id"),
    _case("A07", "cutover_settings_projection", "app/modules/v4/durable_voice_service.py", "VOICE_CHANNEL_PROFILE_REQUIRED"),
    _case("A08", "cutover_settings_projection", "tests/v4/test_voice_job_projection.py", "active_status"),
    _case("A09", "cutover_settings_projection", "tests/v4/test_voice_job_projection.py", "retry_wait"),
    _case("A10", "cutover_settings_projection", "tests/v4/test_voice_job_projection.py", "cancel_requested"),

    # Contract parity and section-oriented Narration Review — 15
    _case("U01", "contracts_ui", "tests/v4/test_voice_contract_v18_parity.py", "provider_neutral_everywhere"),
    _case("U02", "contracts_ui", "tests/v4/test_voice_contract_v18_parity.py", "provider_options"),
    _case("U03", "contracts_ui", "tests/v4/test_voice_contract_v18_parity.py", "current_segment_id"),
    _case("U04", "contracts_ui", "tests/v4/test_voice_contract_v18_parity.py", "cancelVoiceJob"),
    _case("U05", "contracts_ui", "tests/voice-stage.test.mjs", "Voice stage is section-oriented and does not expose segment review UI"),
    _case("U06", "contracts_ui", "tests/voice-stage.test.mjs", "Narration review keeps one full player and adds section-local playback without segment UI"),
    _case("U07", "contracts_ui", "tests/voice-stage.test.mjs", "voice-master-player"),
    _case("U08", "contracts_ui", "tests/voice-stage.test.mjs", "VoiceSectionRemediationList"),
    _case("U09", "contracts_ui", "tests/voice-stage.test.mjs", "regenerateVoiceSection"),
    _case("U10", "contracts_ui", "tests/voice-stage.test.mjs", "section remediation routes"),
    _case("U11", "contracts_ui", "tests/voice-stage.test.mjs", "Voice polls only active progress and parks ambiguous submission polling"),
    _case("U12", "contracts_ui", "tests/voice-stage.test.mjs", "cancel-voice"),
    _case("U13", "contracts_ui", "src/features/studio/voice/VoiceReviewComponents.tsx", "VoiceReviewGate"),
    _case("U14", "contracts_ui", "src/features/studio/VoicePanel.tsx", "VoiceSectionRemediationList"),
    _case("U15", "contracts_ui", "src/features/studio/VoicePanel.tsx", "getVoiceJob"),

    # Persistence parity and provider resource cleanup — 10
    _case("D01", "persistence_cleanup", "tests/v4/test_voice_v20_orm_parity.py", "next_reconcile_at"),
    _case("D02", "persistence_cleanup", "tests/v4/test_voice_v20_orm_parity.py", "reconcile_lease_expires_at"),
    _case("D03", "persistence_cleanup", "tests/v4/test_voice_v20_orm_parity.py", "execution_attempt_token"),
    _case("D04", "persistence_cleanup", "tests/v4/test_voice_v20_orm_parity.py", "provider_handle_json"),
    _case("D05", "persistence_cleanup", "tests/v4/test_voice_v20_orm_parity.py", "state_revision"),
    _case("D06", "persistence_cleanup", "tests/v4/test_voice_v20_provider_cleanup.py", "drains_late_provider_result"),
    _case("D07", "persistence_cleanup", "tests/v4/test_voice_v20_provider_cleanup.py", "not audio_path.exists()"),
    _case("D08", "persistence_cleanup", "app/modules/v4/voice_provider_execution_pool.py", "drain_completed"),
    _case("D09", "persistence_cleanup", "app/modules/v4/voice_provider_action_controller.py", "context.retry_count + 1"),
    _case("D10", "persistence_cleanup", "app/modules/v4/voice_provider_action_controller.py", "self._cleanup_result(result)"),
]


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_voice_v20_adversarial_evidence_matrix(case: EvidenceCase) -> None:
    source = (ROOT / case.path).read_text(encoding="utf-8")
    assert case.marker in source, f"{case.case_id} missing adversarial evidence marker {case.marker!r} in {case.path}"


def test_voice_v20_matrix_has_exact_frozen_case_count_and_required_domains() -> None:
    assert len(CASES) == 77
    assert len({case.case_id for case in CASES}) == 77
    assert {case.category for case in CASES} == {
        "provider_modes",
        "submission_recovery",
        "retry_cancel_fencing",
        "finalization",
        "cutover_settings_projection",
        "contracts_ui",
        "persistence_cleanup",
    }
