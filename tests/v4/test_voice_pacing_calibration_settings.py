from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest

from kokoro.runtime_response_calibration import (
    KOKORO_FLOW_VERSION,
    validate_runtime_response_calibration,
)

from app.api.errors import AppError
from app.db.models import Setting
from app.modules.v4.voice_pacing import canonical_hash
from app.modules.v4.voice_pacing_calibration_settings import (
    KOKORO_LOCAL_MODEL_ID,
    KOKORO_PROVIDER_KEY,
    KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY,
    VOICE_PACING_CALIBRATION_OVERRIDES_SETTINGS_KEY,
    VoicePacingCalibrationSettingsService,
)
from app.modules.v4.voice_provider_adapters import KokoroProviderAdapter
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderPacingObservation,
    VoiceProviderPacingRequest,
    VoiceProviderProfile,
)
from app.modules.v4.voice_provider_registry import CalibratedVoiceProviderRegistry
from tests.support.kokoro_runtime_response import runtime_response_measurement


JUSTIN_VOICE_ID = "BzWc3iJ0MiRdqIo6RCvM"
UNKNOWN_VOICE_ID = "owner_voice_without_static_registry_entry"
EXECUTION_IDENTITY_A = "a" * 64
EXECUTION_IDENTITY_B = "b" * 64


@dataclass
class _FakeSession:
    commit_count: int = 0

    def commit(self) -> None:
        self.commit_count += 1


class _FakeSettingsRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Setting] = {}

    def get(self, key: str) -> Setting | None:
        return self.rows.get(key)

    def upsert(self, setting: Setting) -> Setting:
        self.rows[setting.key] = setting
        return setting


def _service() -> tuple[VoicePacingCalibrationSettingsService, _FakeSession, _FakeSettingsRepository]:
    session = _FakeSession()
    repository = _FakeSettingsRepository()
    return VoicePacingCalibrationSettingsService(session, repository), session, repository  # type: ignore[arg-type]


def test_runtime_response_validator_derives_beta_from_durations() -> None:
    measurement = runtime_response_measurement(0.72)

    validated = validate_runtime_response_calibration(measurement)

    assert validated == measurement
    assert validated["native_wpm_at_rate_1"] == pytest.approx(195.0)
    assert all(
        sample["native_wpm_at_rate_1"] == pytest.approx(195.0)
        for sample in validated["samples"]
    )


def test_runtime_response_validator_rejects_previous_payload_without_native_wpm() -> None:
    previous = runtime_response_measurement(0.72)
    del previous["native_wpm_at_rate_1"]

    assert validate_runtime_response_calibration(previous) is None


def test_runtime_response_validator_accepts_current_measured_beta() -> None:
    measurement = runtime_response_measurement(2.05)

    validated = validate_runtime_response_calibration(measurement)

    assert validated is not None
    assert validated["speed_response_exponent"] == pytest.approx(2.05)


def test_runtime_response_validator_rejects_tampered_pair_betas() -> None:
    measurement = runtime_response_measurement(0.72)
    tampered = deepcopy(measurement)
    tampered["samples"][0]["pair_beta_estimates"][0] += 0.1

    assert validate_runtime_response_calibration(tampered) is None


def test_runtime_response_validator_rejects_tampered_text_beta() -> None:
    measurement = runtime_response_measurement(0.72)
    tampered = deepcopy(measurement)
    tampered["samples"][0]["text_beta"] += 0.1

    assert validate_runtime_response_calibration(tampered) is None


def test_runtime_response_validator_rejects_tampered_runtime_beta() -> None:
    measurement = runtime_response_measurement(0.72)
    tampered = deepcopy(measurement)
    tampered["speed_response_exponent"] += 0.1

    assert validate_runtime_response_calibration(tampered) is None


def test_runtime_response_validator_rejects_wrong_canonical_text_hash() -> None:
    measurement = runtime_response_measurement(0.72)
    tampered = deepcopy(measurement)
    tampered["samples"][0]["text_sha256"] = "f" * 64

    assert validate_runtime_response_calibration(tampered) is None


def _kokoro_section(
    identity: str,
    *,
    lineage: str = "1",
    rate: float = 1.0,
    raw_duration: float = 60.0,
    fixed_duration: float = 0.0,
    words: int = 100,
    target_wpm: int = 150,
    section_id: str = "sec_001",
) -> dict[str, object]:
    return {
        "section_id": section_id,
        "section_acceptance_identity_sha256": identity,
        "actual_word_count": words,
        "target_wpm": target_wpm,
        "measured_raw_wpm": 60.0 * words / raw_duration,
        "raw_duration_seconds": raw_duration,
        "provider_fixed_duration_seconds": fixed_duration,
        "provider_pacing_value": rate,
        "response_lineage_sha256": lineage * 64,
    }


def test_static_registry_remains_effective_default_until_exact_override_is_saved() -> None:
    service, session, repository = _service()

    before = service.get_voice_snapshot(provider_key="elevenlabs", voice_id=JUSTIN_VOICE_ID)
    assert before["calibrations"]["economy"] == {
        "model_id": "eleven_flash_v2_5",
        "native_wpm_at_rate_1": 150.0,
        "source": "registry",
        "default_native_wpm_at_rate_1": 150.0,
    }
    assert before["calibrations"]["quality_cloned_voice"]["source"] == "registry"

    after = service.put_voice_preset_override(
        provider_key="elevenlabs",
        voice_id=JUSTIN_VOICE_ID,
        voice_preset="economy",
        native_wpm_at_rate_1=157.4,
    )

    assert session.commit_count == 1
    assert VOICE_PACING_CALIBRATION_OVERRIDES_SETTINGS_KEY in repository.rows
    assert after["calibrations"]["economy"] == {
        "model_id": "eleven_flash_v2_5",
        "native_wpm_at_rate_1": 157.4,
        "source": "override",
        "default_native_wpm_at_rate_1": 150.0,
    }
    assert after["calibrations"]["quality_cloned_voice"]["native_wpm_at_rate_1"] == 150.0
    assert after["calibrations"]["quality_cloned_voice"]["source"] == "registry"


def test_unknown_voice_can_be_calibrated_per_exact_active_model_without_inventing_other_preset() -> None:
    service, _, _ = _service()

    before = service.get_voice_snapshot(provider_key="elevenlabs", voice_id=UNKNOWN_VOICE_ID)
    assert before["calibrations"]["economy"]["source"] == "missing"
    assert before["calibrations"]["economy"]["native_wpm_at_rate_1"] is None
    assert before["calibrations"]["quality_cloned_voice"]["source"] == "missing"

    after = service.put_voice_preset_override(
        provider_key="elevenlabs",
        voice_id=UNKNOWN_VOICE_ID,
        voice_preset="economy",
        native_wpm_at_rate_1=153.2,
    )

    assert after["calibrations"]["economy"]["source"] == "override"
    assert after["calibrations"]["economy"]["native_wpm_at_rate_1"] == 153.2
    assert after["calibrations"]["quality_cloned_voice"]["source"] == "missing"
    assert after["calibrations"]["quality_cloned_voice"]["native_wpm_at_rate_1"] is None


def test_resolved_override_identity_is_stable_when_same_wpm_is_saved_again() -> None:
    service, session, _ = _service()
    service.put_voice_preset_override(
        provider_key="elevenlabs",
        voice_id=JUSTIN_VOICE_ID,
        voice_preset="economy",
        native_wpm_at_rate_1=156.0,
    )
    first = service.resolve("elevenlabs", JUSTIN_VOICE_ID, "eleven_flash_v2_5")
    assert first is not None

    service.put_voice_preset_override(
        provider_key="elevenlabs",
        voice_id=JUSTIN_VOICE_ID,
        voice_preset="economy",
        native_wpm_at_rate_1=156.0,
    )
    second = service.resolve("elevenlabs", JUSTIN_VOICE_ID, "eleven_flash_v2_5")
    assert second is not None

    assert session.commit_count == 2
    assert first.identity_sha256() == second.identity_sha256()
    assert second.native_wpm_at_rate_1 == 156.0
    assert second.metadata == {
        "source": "owner_persisted_override",
        "statistic": "configured_native_wpm_baseline",
    }


def test_kokoro_history_stores_raw_evidence_without_pretending_single_speed_native_wpm_is_learned() -> None:
    service, session, repository = _service()

    learned = service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_001",
        analysis_report={
            "sections": [
                _kokoro_section("a" * 64, lineage="1", rate=1.0, raw_duration=60.0),
                _kokoro_section("b" * 64, lineage="2", rate=1.1, raw_duration=60.0, section_id="sec_002"),
            ],
        },
        observed_at="2026-09-20T00:00:00+00:00",
    )

    assert session.commit_count == 1
    assert KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY in repository.rows
    assert learned is None


def test_stale_kokoro_history_is_empty_cache_for_recommendation_and_current_recording() -> None:
    service, session, repository = _service()
    repository.upsert(
        Setting(
            key=KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY,
            value_json={
                "flow_version": "not-current",
                "entries": [{"stale": "history-must-not-be-reused"}],
            },
            updated_at="2026-01-01T00:00:00Z",
        )
    )

    observation = VoiceProviderPacingObservation(
        section_id="sec_001",
        target_narration_wpm=180,
        measured_raw_wpm=150.0,
        current_control_model="relative_multiplier",
        current_provider_pacing_value=1.0,
        provider_value_range=(0.5, 2.0),
        actual_word_count=100,
        measured_raw_duration_seconds=40.0,
        target_duration_seconds=60.0 * 100 / 180.0,
        fixed_duration_seconds=0.0,
        response_lineage_sha256="1" * 64,
    )
    recommendation = service.kokoro_empirical_pacing_recommendation(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        observation=observation,
    )

    assert recommendation is None
    assert KokoroProviderAdapter().recommend_pacing(observation).status == "supported"

    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_001",
        analysis_report={"sections": [_kokoro_section("a" * 64)]},
        observed_at="2026-09-20T00:00:00+00:00",
    )

    payload = repository.rows[KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY].value_json
    assert session.commit_count == 1
    assert payload["flow_version"] == KOKORO_FLOW_VERSION
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["observations"][0]["observation_id"] == "a" * 64
    assert "stale" not in payload["entries"][0]


def test_kokoro_history_dedupes_same_operation_section_observation() -> None:
    service, _, repository = _service()

    report = {
        "sections": [
                _kokoro_section("c" * 64)
        ],
    }
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_001",
        analysis_report=report,
        observed_at="2026-09-20T00:00:00+00:00",
    )
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_001",
        analysis_report=report,
        observed_at="2026-09-20T00:01:00+00:00",
    )

    payload = repository.rows[KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY].value_json
    observations = payload["entries"][0]["observations"]
    assert len(observations) == 1
    assert observations[0]["observation_id"] == "c" * 64
    assert observations[0]["flow_version"] == KOKORO_FLOW_VERSION


def test_kokoro_history_dedupes_reused_audio_by_section_acceptance_identity() -> None:
    service, _, repository = _service()

    report = {
        "sections": [
                _kokoro_section("d" * 64, fixed_duration=5.0)
        ],
    }
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_001",
        analysis_report=report,
        observed_at="2026-09-20T00:00:00+00:00",
    )
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_002",
        analysis_report=report,
        observed_at="2026-09-20T00:01:00+00:00",
    )

    observations = repository.rows[KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY].value_json["entries"][0]["observations"]
    assert len(observations) == 1
    assert observations[0]["operation_id"] == "operation_002"


def test_kokoro_history_records_new_observation_for_new_section_acceptance_identity() -> None:
    service, _, repository = _service()

    def section(identity: str) -> dict[str, object]:
        return _kokoro_section(identity, fixed_duration=5.0)

    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_001",
        analysis_report={"sections": [section("e" * 64)]},
        observed_at="2026-09-20T00:00:00+00:00",
    )
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_003",
        analysis_report={"sections": [section("f" * 64)]},
        observed_at="2026-09-20T00:02:00+00:00",
    )

    observations = repository.rows[KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY].value_json["entries"][0]["observations"]
    assert [item["observation_id"] for item in observations] == ["e" * 64, "f" * 64]



def test_kokoro_empirical_bracket_interpolates_same_section_production_pattern() -> None:
    service, _, _repository = _service()
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_fast",
        analysis_report={
            "sections": [
                _kokoro_section(
                    "a" * 64,
                    lineage="f",
                    rate=0.818,
                    raw_duration=60.0 * 100 / 178.0,
                    target_wpm=164,
                )
            ],
        },
        observed_at="2026-09-23T00:00:00+00:00",
    )

    recommendation = service.kokoro_empirical_pacing_recommendation(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        observation=VoiceProviderPacingObservation(
            section_id="sec_001",
            target_narration_wpm=164,
            measured_raw_wpm=153.0,
            current_control_model="relative_multiplier",
            current_provider_pacing_value=0.760,
            provider_value_range=(0.5, 2.0),
            actual_word_count=100,
            measured_raw_duration_seconds=60.0 * 100 / 153.0,
            target_duration_seconds=60.0 * 100 / 164.0,
            fixed_duration_seconds=0.0,
            response_lineage_sha256="f" * 64,
        ),
    )

    assert recommendation is not None
    assert recommendation.policy_version == KOKORO_FLOW_VERSION
    assert recommendation.response_model_source == "empirical_same_section_bracket"
    assert recommendation.recommended_value == pytest.approx(0.78552, abs=1e-5)
    assert 0.760 < float(recommendation.recommended_value) < 0.818


def test_kokoro_empirical_bracket_rejects_rounded_no_progress() -> None:
    service, _, _repository = _service()
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_fast",
        analysis_report={
            "sections": [
                _kokoro_section(
                    "9" * 64,
                    lineage="d",
                    rate=1.000001,
                    raw_duration=60.0 * 100 / 180.0,
                    target_wpm=164,
                )
            ],
        },
        observed_at="2026-09-23T00:00:00+00:00",
    )

    recommendation = service.kokoro_empirical_pacing_recommendation(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        observation=VoiceProviderPacingObservation(
            section_id="sec_001",
            target_narration_wpm=164,
            measured_raw_wpm=150.0,
            current_control_model="relative_multiplier",
            current_provider_pacing_value=1.0,
            provider_value_range=(0.5, 2.0),
            actual_word_count=100,
            measured_raw_duration_seconds=40.0,
            target_duration_seconds=60.0 * 100 / 164.0,
            fixed_duration_seconds=0.0,
            response_lineage_sha256="d" * 64,
        ),
    )

    assert recommendation is not None
    assert recommendation.status == "unsupported"
    assert recommendation.recommended_value is None
    assert recommendation.reason == "provider_pacing_no_progress"


def test_kokoro_empirical_bracket_never_mixes_different_response_lineage() -> None:
    service, _, _repository = _service()
    service.record_kokoro_pacing_observations(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        operation_id="operation_other_material",
        analysis_report={
            "sections": [
                _kokoro_section(
                    "b" * 64,
                    lineage="e",
                    rate=0.818,
                    raw_duration=60.0 * 100 / 178.0,
                    target_wpm=164,
                )
            ],
        },
        observed_at="2026-09-23T00:00:00+00:00",
    )

    recommendation = service.kokoro_empirical_pacing_recommendation(
        voice_id="af_heart",
        execution_identity_sha256=EXECUTION_IDENTITY_A,
        observation=VoiceProviderPacingObservation(
            section_id="sec_001",
            target_narration_wpm=164,
            measured_raw_wpm=153.0,
            current_control_model="relative_multiplier",
            current_provider_pacing_value=0.760,
            provider_value_range=(0.5, 2.0),
            actual_word_count=100,
            measured_raw_duration_seconds=60.0 * 100 / 153.0,
            target_duration_seconds=60.0 * 100 / 164.0,
            fixed_duration_seconds=0.0,
            response_lineage_sha256="f" * 64,
        ),
    )

    assert recommendation is None


def _kokoro_profile() -> VoiceProviderProfile:
    return VoiceProviderProfile(
        profile_id="kokoro_local",
        provider_key=KOKORO_PROVIDER_KEY,
        enabled=True,
        display_name="Kokoro Local",
        voice_id="af_heart",
        language="en-us",
        speaking_rate=1.0,
        requested_sample_rate_hz=48_000,
        requested_output_format="wav_pcm_s16le",
        provider_options={"runtime": "local_kokoro_cli"},
        maximum_segment_characters=800,
    )


class _FrozenMarkerKokoroAdapter(KokoroProviderAdapter):
    def __init__(
        self,
        *,
        measurement: dict[str, object],
        identities: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__()
        current = {
            "schema_version": 1,
            "provider_key": KOKORO_PROVIDER_KEY,
            "flow_version": KOKORO_FLOW_VERSION,
            "runtime": "frozen-marker",
            "runtime_response_calibration_sha256": canonical_hash(measurement),
        }
        self.measurement = measurement
        self.identities = identities or [current]
        self.identity_calls = 0

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, object]:
        _ = profile
        identity = self.identities[min(self.identity_calls, len(self.identities) - 1)]
        self.identity_calls += 1
        return identity

    def runtime_response_calibration(self, profile: VoiceProviderProfile) -> dict[str, object]:
        _ = profile
        return self.measurement


def test_current_marker_calibration_drives_first_generation_duration_model() -> None:
    measurement = runtime_response_measurement(0.72, native_wpm_at_rate_1=195.0)
    registry = CalibratedVoiceProviderRegistry()
    registry.register(_FrozenMarkerKokoroAdapter(measurement=measurement))

    identity = registry.execution_identity(_kokoro_profile())
    calibration = registry._pacing_calibration.get()

    assert identity["flow_version"] == KOKORO_FLOW_VERSION
    assert calibration is not None
    assert calibration.native_wpm_at_rate_1 == pytest.approx(195.0)
    assert calibration.metadata["speed_response_exponent"] == pytest.approx(0.72)
    target_wpm = 235
    word_count = 100
    fixed_duration = 5.0
    target_speech_duration = word_count * 60.0 / target_wpm
    directive = registry.pacing_directive(
        KOKORO_PROVIDER_KEY,
        VoiceProviderPacingRequest(
            section_id="sec_001",
            target_narration_wpm=target_wpm,
            planned_average_wpm=220,
            base_native_speaking_rate=1.0,
            actual_word_count=word_count,
            target_duration_seconds=target_speech_duration + fixed_duration,
            fixed_duration_seconds=fixed_duration,
        ),
    )

    expected_rate = ((word_count * 60.0 / 195.0) / target_speech_duration) ** (1.0 / 0.72)
    assert directive.value == pytest.approx(expected_rate, abs=0.000001)
    assert directive.value > 1.0
    assert directive.policy_version == KOKORO_FLOW_VERSION


def test_registry_rejects_runtime_identity_change_while_reading_marker_calibration() -> None:
    measurement = runtime_response_measurement(0.72)
    identity_a = {
        "schema_version": 1,
        "provider_key": KOKORO_PROVIDER_KEY,
        "flow_version": KOKORO_FLOW_VERSION,
        "runtime": "marker-a",
        "runtime_response_calibration_sha256": canonical_hash(measurement),
    }
    identity_b = {**identity_a, "runtime": "marker-b"}
    registry = CalibratedVoiceProviderRegistry()
    registry.register(
        _FrozenMarkerKokoroAdapter(
            measurement=measurement,
            identities=[identity_a, identity_b],
        )
    )

    with pytest.raises(AppError) as exc_info:
        registry.execution_identity(_kokoro_profile())

    assert exc_info.value.error_code == "VOICE_PACING_CALIBRATION_IDENTITY_INVALID"
