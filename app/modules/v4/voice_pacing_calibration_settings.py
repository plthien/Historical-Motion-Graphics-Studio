from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.time import utc_now_iso
from kokoro.runtime_response_calibration import (
    KOKORO_FLOW_VERSION,
    MAX_NATIVE_WPM_AT_RATE_1,
    MIN_NATIVE_WPM_AT_RATE_1,
)
from app.db.models import Setting
from app.modules.v4.voice_first_policy import ACTIVE_VOICE_PRESETS
from app.modules.v4.voice_pacing_calibration import VoicePacingCalibration, find_voice_pacing_calibration
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderPacingObservation,
    VoiceProviderPacingRecommendation,
)
from app.repositories import SettingsRepository


VOICE_PACING_CALIBRATION_OVERRIDES_SETTINGS_KEY = "voice_pacing_calibration_overrides_v1"
VOICE_PACING_CALIBRATION_OVERRIDES_SCHEMA_VERSION = 1
KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY = "kokoro_voice_pacing_history"
KOKORO_PROVIDER_KEY = "kokoro_local"
KOKORO_LOCAL_MODEL_ID = "kokoro_local"
KOKORO_HISTORY_MAX_OBSERVATIONS_PER_VOICE = 50


class VoicePacingCalibrationSettingsService:
    """Persist owner-entered native WPM overrides per exact provider/voice/model.

    Static registry entries remain the default/fail-safe baseline. Persisted
    overrides take precedence only for the exact identity they name, and the
    runtime calibration metadata intentionally excludes timestamps so saving the
    same effective value again does not create a new pacing lineage identity.
    """

    def __init__(self, session: Session, repository: SettingsRepository) -> None:
        self.session = session
        self.repository = repository

    @staticmethod
    def _empty_payload() -> dict[str, Any]:
        return {
            "schema_version": VOICE_PACING_CALIBRATION_OVERRIDES_SCHEMA_VERSION,
            "entries": [],
        }

    @staticmethod
    def _normalize_identity(provider_key: str, voice_id: str, model_id: str) -> tuple[str, str, str]:
        identity = (provider_key.strip(), voice_id.strip(), model_id.strip())
        if not all(identity):
            raise AppError(
                "VOICE_PACING_CALIBRATION_IDENTITY_INVALID",
                "Voice pacing calibration identity must include provider, voice, and model.",
                422,
                retryable=False,
            )
        return identity

    def _stored_payload(self) -> dict[str, Any]:
        row = self.repository.get(VOICE_PACING_CALIBRATION_OVERRIDES_SETTINGS_KEY)
        if row is None:
            return self._empty_payload()
        payload = deepcopy(row.value_json)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != VOICE_PACING_CALIBRATION_OVERRIDES_SCHEMA_VERSION
            or not isinstance(payload.get("entries"), list)
        ):
            raise AppError(
                "VOICE_PACING_CALIBRATION_SETTINGS_INVALID",
                "Stored Voice pacing calibration overrides are invalid.",
                500,
                retryable=False,
            )
        for entry in payload["entries"]:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"provider", "voice_id", "model_id", "native_wpm_at_rate_1"}
                or not isinstance(entry.get("provider"), str)
                or not isinstance(entry.get("voice_id"), str)
                or not isinstance(entry.get("model_id"), str)
                or not isinstance(entry.get("native_wpm_at_rate_1"), (int, float))
                or isinstance(entry.get("native_wpm_at_rate_1"), bool)
            ):
                raise AppError(
                    "VOICE_PACING_CALIBRATION_SETTINGS_INVALID",
                    "Stored Voice pacing calibration overrides are invalid.",
                    500,
                    retryable=False,
                )
            native_wpm = float(entry["native_wpm_at_rate_1"])
            if not MIN_NATIVE_WPM_AT_RATE_1 <= native_wpm <= MAX_NATIVE_WPM_AT_RATE_1:
                raise AppError(
                    "VOICE_PACING_CALIBRATION_SETTINGS_INVALID",
                    "Stored Voice pacing calibration overrides are outside the supported WPM range.",
                    500,
                    retryable=False,
                )
        return payload

    @staticmethod
    def _empty_kokoro_history_payload() -> dict[str, Any]:
        return {
            "flow_version": KOKORO_FLOW_VERSION,
            "entries": [],
        }

    def _stored_kokoro_history_payload(self) -> dict[str, Any]:
        row = self.repository.get(KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY)
        if row is None:
            return self._empty_kokoro_history_payload()
        payload = deepcopy(row.value_json)
        if (
            not isinstance(payload, dict)
            or payload.get("flow_version") != KOKORO_FLOW_VERSION
            or not isinstance(payload.get("entries"), list)
        ):
            return self._empty_kokoro_history_payload()
        for entry in payload["entries"]:
            if (
                not isinstance(entry, dict)
                or entry.get("provider") != KOKORO_PROVIDER_KEY
                or not isinstance(entry.get("voice_id"), str)
                or not entry.get("voice_id")
                or entry.get("model_id") != KOKORO_LOCAL_MODEL_ID
                or not isinstance(entry.get("execution_identity_sha256"), str)
                or len(entry["execution_identity_sha256"]) != 64
                or not isinstance(entry.get("observations"), list)
            ):
                return self._empty_kokoro_history_payload()
            for observation in entry["observations"]:
                if not self._valid_kokoro_observation(observation) or observation.get("execution_identity_sha256") != entry["execution_identity_sha256"]:
                    return self._empty_kokoro_history_payload()
        return payload

    @staticmethod
    def _matching_entry(
        payload: dict[str, Any],
        *,
        provider_key: str,
        voice_id: str,
        model_id: str,
        execution_identity_sha256: str | None = None,
    ) -> dict[str, Any] | None:
        return next(
            (
                entry
                for entry in payload["entries"]
                if entry["provider"] == provider_key
                and entry["voice_id"] == voice_id
                and entry["model_id"] == model_id
                and (execution_identity_sha256 is None or entry.get("execution_identity_sha256") == execution_identity_sha256)
            ),
            None,
        )

    def resolve(
        self,
        provider_key: str,
        voice_id: str,
        model_id: str,
    ) -> VoicePacingCalibration | None:
        provider_key, voice_id, model_id = self._normalize_identity(provider_key, voice_id, model_id)
        payload = self._stored_payload()
        override = self._matching_entry(
            payload,
            provider_key=provider_key,
            voice_id=voice_id,
            model_id=model_id,
        )
        if override is not None:
            return VoicePacingCalibration(
                provider_key=provider_key,
                voice_id=voice_id,
                model_id=model_id,
                native_wpm_at_rate_1=float(override["native_wpm_at_rate_1"]),
                metadata={
                    "source": "owner_persisted_override",
                    "statistic": "configured_native_wpm_baseline",
                },
            )
        return find_voice_pacing_calibration(
            provider_key=provider_key,
            voice_id=voice_id,
            model_id=model_id,
        )

    def kokoro_empirical_pacing_recommendation(
        self,
        *,
        voice_id: str,
        execution_identity_sha256: str,
        observation: VoiceProviderPacingObservation,
    ) -> VoiceProviderPacingRecommendation | None:
        current = self._kokoro_observation_candidate_from_feedback(
            observation,
            execution_identity_sha256=execution_identity_sha256,
        )
        if current is None:
            return None
        lineage = current["response_lineage_sha256"]
        payload = self._stored_kokoro_history_payload()
        entry = self._matching_entry(
            payload,
            provider_key=KOKORO_PROVIDER_KEY,
            voice_id=voice_id,
            model_id=KOKORO_LOCAL_MODEL_ID,
            execution_identity_sha256=execution_identity_sha256,
        )
        stored = entry.get("observations") if isinstance(entry, dict) else []
        candidates = [
            dict(item)
            for item in stored
            if self._valid_kokoro_observation(item)
            and item["response_lineage_sha256"] == lineage
            and int(item["actual_word_count"]) == int(current["actual_word_count"])
            and abs(float(item["target_wpm"]) - float(current["target_wpm"])) <= 1e-9
        ] if isinstance(stored, list) else []
        candidates.append(current)

        target_wpm = float(current["target_wpm"])
        below = [item for item in candidates if float(item["measured_raw_wpm"]) < target_wpm]
        above = [item for item in candidates if float(item["measured_raw_wpm"]) > target_wpm]
        brackets: list[tuple[float, float, dict[str, Any], dict[str, Any]]] = []
        for low in below:
            for high in above:
                low_rate = float(low["provider_pacing_value"])
                high_rate = float(high["provider_pacing_value"])
                low_wpm = float(low["measured_raw_wpm"])
                high_wpm = float(high["measured_raw_wpm"])
                if high_rate <= low_rate or high_wpm <= low_wpm:
                    continue
                brackets.append((high_wpm - low_wpm, high_rate - low_rate, low, high))
        if not brackets:
            return None

        _wpm_span, _rate_span, low, high = min(brackets, key=lambda item: (item[0], item[1]))
        low_rate = float(low["provider_pacing_value"])
        high_rate = float(high["provider_pacing_value"])
        low_wpm = float(low["measured_raw_wpm"])
        high_wpm = float(high["measured_raw_wpm"])
        fraction = (target_wpm - low_wpm) / (high_wpm - low_wpm)
        recommended = low_rate + fraction * (high_rate - low_rate)
        if not math.isfinite(recommended) or not low_rate < recommended < high_rate:
            return None
        if observation.provider_value_range is not None:
            minimum, maximum = map(float, observation.provider_value_range)
            if not minimum <= recommended <= maximum:
                return None

        identity_payload = {
            "flow_version": KOKORO_FLOW_VERSION,
            "provider": KOKORO_PROVIDER_KEY,
            "voice_id": voice_id,
            "execution_identity_sha256": execution_identity_sha256,
            "response_lineage_sha256": lineage,
            "target_wpm": target_wpm,
            "low": {
                "observation_id": low["observation_id"],
                "provider_pacing_value": low_rate,
                "measured_raw_wpm": low_wpm,
            },
            "high": {
                "observation_id": high["observation_id"],
                "provider_pacing_value": high_rate,
                "measured_raw_wpm": high_wpm,
            },
        }
        response_model_identity = hashlib.sha256(
            json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        rounded = round(recommended, 6)
        current_rate = float(observation.current_provider_pacing_value)
        if abs(rounded - current_rate) <= 0.000001:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model="relative_multiplier",
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                clamped=False,
                value_range=observation.provider_value_range,
                reason="provider_pacing_no_progress",
                response_model_source="empirical_same_section_bracket",
                response_model_identity=response_model_identity,
            )
        return VoiceProviderPacingRecommendation(
            status="supported",
            control_model="relative_multiplier",
            recommended_value=rounded,
            policy_version=KOKORO_FLOW_VERSION,
            clamped=False,
            value_range=observation.provider_value_range,
            response_model_source="empirical_same_section_bracket",
            response_model_identity=response_model_identity,
        )

    @staticmethod
    def _valid_kokoro_observation(observation: Any) -> bool:
        if not isinstance(observation, dict):
            return False
        required_string_keys = ("observation_id", "operation_id", "section_id", "execution_identity_sha256", "observed_at")
        if any(not isinstance(observation.get(key), str) or not observation.get(key) for key in required_string_keys):
            return False
        if len(observation["execution_identity_sha256"]) != 64 or not isinstance(observation.get("response_lineage_sha256"), str) or len(observation["response_lineage_sha256"]) != 64:
            return False
        if observation.get("flow_version") != KOKORO_FLOW_VERSION:
            return False
        measured_raw_wpm = observation.get("measured_raw_wpm")
        provider_pacing_value = observation.get("provider_pacing_value")
        target_wpm = observation.get("target_wpm")
        raw_duration_seconds = observation.get("raw_duration_seconds")
        provider_fixed_duration_seconds = observation.get("provider_fixed_duration_seconds")
        actual_word_count = observation.get("actual_word_count")
        if not isinstance(actual_word_count, int) or actual_word_count <= 0:
            return False
        for value in (measured_raw_wpm, provider_pacing_value, target_wpm, raw_duration_seconds, provider_fixed_duration_seconds):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                return False
        return (
            float(measured_raw_wpm) > 0
            and float(provider_pacing_value) > 0
            and float(target_wpm) > 0
            and float(raw_duration_seconds) > float(provider_fixed_duration_seconds)
            and float(provider_fixed_duration_seconds) >= 0
        )

    @staticmethod
    def _kokoro_observation_candidate_from_feedback(
        observation: VoiceProviderPacingObservation,
        *,
        execution_identity_sha256: str,
    ) -> dict[str, Any] | None:
        if (
            observation.current_provider_pacing_value is None
            or observation.actual_word_count is None
            or observation.measured_raw_duration_seconds is None
            or observation.target_duration_seconds is None
        ):
            return None
        try:
            measured_raw_wpm = float(observation.measured_raw_wpm)
            raw_duration = float(observation.measured_raw_duration_seconds)
            fixed_duration = float(observation.fixed_duration_seconds)
            provider_pacing_value = float(observation.current_provider_pacing_value)
            speech_duration = raw_duration - fixed_duration
            if speech_duration <= 0 or provider_pacing_value <= 0:
                return None
            candidate = {
                "observation_id": "current_feedback_candidate",
                "flow_version": KOKORO_FLOW_VERSION,
                "operation_id": "current_feedback_candidate",
                "section_id": observation.section_id,
                "execution_identity_sha256": execution_identity_sha256,
                "actual_word_count": int(observation.actual_word_count),
                "target_wpm": float(observation.target_narration_wpm),
                "measured_raw_wpm": measured_raw_wpm,
                "raw_duration_seconds": raw_duration,
                "provider_fixed_duration_seconds": fixed_duration,
                "provider_pacing_value": provider_pacing_value,
                "response_lineage_sha256": observation.response_lineage_sha256,
                "observed_at": "current_feedback_candidate",
            }
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return candidate if VoicePacingCalibrationSettingsService._valid_kokoro_observation(candidate) else None

    @staticmethod
    def _kokoro_observation_from_section(
        *,
        operation_id: str,
        execution_identity_sha256: str,
        observed_at: str,
        section: dict[str, Any],
    ) -> dict[str, Any] | None:
        measured_raw_wpm = section.get("measured_raw_wpm")
        provider_pacing_value = section.get("provider_pacing_value")
        target_wpm = section.get("target_wpm", section.get("planned_narration_wpm"))
        section_id = section.get("section_id")
        acceptance_id = section.get("section_acceptance_identity_sha256")
        response_lineage = section.get("response_lineage_sha256")
        if not isinstance(section_id, str) or not section_id:
            return None
        if not isinstance(acceptance_id, str) or not acceptance_id:
            return None
        if not isinstance(response_lineage, str) or len(response_lineage) != 64:
            return None
        try:
            candidate = {
                "observation_id": acceptance_id,
                "flow_version": KOKORO_FLOW_VERSION,
                "operation_id": operation_id,
                "section_id": section_id,
                "execution_identity_sha256": execution_identity_sha256,
                "actual_word_count": int(section.get("actual_word_count")),
                "target_wpm": float(target_wpm),
                "measured_raw_wpm": float(measured_raw_wpm),
                "raw_duration_seconds": float(section.get("raw_duration_seconds")),
                "provider_fixed_duration_seconds": float(section.get("provider_fixed_duration_seconds", 0.0)),
                "provider_pacing_value": float(provider_pacing_value),
                "response_lineage_sha256": response_lineage,
                "observed_at": observed_at,
            }
        except (TypeError, ValueError):
            return None
        return candidate if VoicePacingCalibrationSettingsService._valid_kokoro_observation(candidate) else None

    def record_kokoro_pacing_observations(
        self,
        *,
        voice_id: str,
        operation_id: str,
        execution_identity_sha256: str,
        analysis_report: dict[str, Any],
        observed_at: str | None = None,
        commit: bool = True,
    ) -> None:
        voice_id = voice_id.strip()
        operation_id = operation_id.strip()
        execution_identity_sha256 = execution_identity_sha256.strip()
        if not voice_id or not operation_id or len(execution_identity_sha256) != 64:
            raise AppError(
                "VOICE_PACING_CALIBRATION_IDENTITY_INVALID",
                "Kokoro pacing history requires voice, operation, and execution identities.",
                422,
                retryable=False,
            )
        sections = analysis_report.get("sections") if isinstance(analysis_report, dict) else None
        if not isinstance(sections, list):
            return None
        now = observed_at or utc_now_iso()
        observations = [
            observation
            for section in sections
            if isinstance(section, dict)
            for observation in [self._kokoro_observation_from_section(
                operation_id=operation_id,
                execution_identity_sha256=execution_identity_sha256,
                observed_at=now,
                section=section,
            )]
            if observation is not None
        ]
        if not observations:
            return None

        payload = self._stored_kokoro_history_payload()
        entries = [
            entry
            for entry in payload["entries"]
            if not (
                entry["provider"] == KOKORO_PROVIDER_KEY
                and entry["voice_id"] == voice_id
                and entry["model_id"] == KOKORO_LOCAL_MODEL_ID
                and entry.get("execution_identity_sha256") == execution_identity_sha256
            )
        ]
        existing = self._matching_entry(
            payload,
            provider_key=KOKORO_PROVIDER_KEY,
            voice_id=voice_id,
            model_id=KOKORO_LOCAL_MODEL_ID,
            execution_identity_sha256=execution_identity_sha256,
        )
        by_id: dict[str, dict[str, Any]] = {}
        if isinstance(existing, dict):
            for item in existing.get("observations", []):
                if self._valid_kokoro_observation(item):
                    by_id[str(item["observation_id"])] = dict(item)
        for item in observations:
            by_id[item["observation_id"]] = item
        history = sorted(by_id.values(), key=lambda item: str(item["observed_at"]))[-KOKORO_HISTORY_MAX_OBSERVATIONS_PER_VOICE:]
        entries.append(
            {
                "provider": KOKORO_PROVIDER_KEY,
                "voice_id": voice_id,
                "model_id": KOKORO_LOCAL_MODEL_ID,
                "execution_identity_sha256": execution_identity_sha256,
                "observations": history,
            }
        )
        entries.sort(key=lambda entry: (entry["provider"], entry["voice_id"], entry["model_id"], entry["execution_identity_sha256"]))
        stored = {
            "flow_version": KOKORO_FLOW_VERSION,
            "entries": entries,
        }
        self.repository.upsert(
            Setting(
                key=KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY,
                value_json=stored,
                updated_at=now,
            )
        )
        if commit:
            self.session.commit()
        return None

    def get_voice_snapshot(self, *, provider_key: str, voice_id: str) -> dict[str, Any]:
        provider_key = provider_key.strip()
        voice_id = voice_id.strip()
        if not provider_key or not voice_id:
            raise AppError(
                "VOICE_PACING_CALIBRATION_IDENTITY_INVALID",
                "Voice pacing calibration identity must include provider and voice.",
                422,
                retryable=False,
            )
        payload = self._stored_payload()
        calibrations: dict[str, Any] = {}
        for preset, model_id in ACTIVE_VOICE_PRESETS.items():
            override = self._matching_entry(
                payload,
                provider_key=provider_key,
                voice_id=voice_id,
                model_id=model_id,
            )
            registry = find_voice_pacing_calibration(
                provider_key=provider_key,
                voice_id=voice_id,
                model_id=model_id,
            )
            effective = override if override is not None else registry
            calibrations[preset] = {
                "model_id": model_id,
                "native_wpm_at_rate_1": (
                    float(override["native_wpm_at_rate_1"])
                    if override is not None
                    else (float(registry.native_wpm_at_rate_1) if registry is not None else None)
                ),
                "source": "override" if override is not None else ("registry" if registry is not None else "missing"),
                "default_native_wpm_at_rate_1": float(registry.native_wpm_at_rate_1) if registry is not None else None,
            }
        return {
            "provider": provider_key,
            "voice_id": voice_id,
            "calibrations": calibrations,
        }

    def put_voice_preset_override(
        self,
        *,
        provider_key: str,
        voice_id: str,
        voice_preset: str,
        native_wpm_at_rate_1: float,
    ) -> dict[str, Any]:
        provider_key = provider_key.strip()
        voice_id = voice_id.strip()
        model_id = ACTIVE_VOICE_PRESETS.get(voice_preset)
        if provider_key != "elevenlabs" or model_id is None or not voice_id:
            raise AppError(
                "VOICE_PACING_CALIBRATION_IDENTITY_INVALID",
                "Voice pacing calibration must target an active ElevenLabs voice preset.",
                422,
                retryable=False,
            )
        native_wpm = float(native_wpm_at_rate_1)
        if not MIN_NATIVE_WPM_AT_RATE_1 <= native_wpm <= MAX_NATIVE_WPM_AT_RATE_1:
            raise AppError(
                "VOICE_PACING_CALIBRATION_WPM_INVALID",
                f"Native WPM must be between {MIN_NATIVE_WPM_AT_RATE_1:g} and {MAX_NATIVE_WPM_AT_RATE_1:g}.",
                422,
                retryable=False,
            )

        payload = self._stored_payload()
        replacement = {
            "provider": provider_key,
            "voice_id": voice_id,
            "model_id": model_id,
            "native_wpm_at_rate_1": native_wpm,
        }
        entries = [
            entry
            for entry in payload["entries"]
            if not (
                entry["provider"] == provider_key
                and entry["voice_id"] == voice_id
                and entry["model_id"] == model_id
            )
        ]
        entries.append(replacement)
        entries.sort(key=lambda entry: (entry["provider"], entry["voice_id"], entry["model_id"]))
        stored = {
            "schema_version": VOICE_PACING_CALIBRATION_OVERRIDES_SCHEMA_VERSION,
            "entries": entries,
        }
        self.repository.upsert(
            Setting(
                key=VOICE_PACING_CALIBRATION_OVERRIDES_SETTINGS_KEY,
                value_json=stored,
                updated_at=utc_now_iso(),
            )
        )
        self.session.commit()
        return self.get_voice_snapshot(provider_key=provider_key, voice_id=voice_id)
