from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.core.contracts import ContractPathResolver
from app.core.time import utc_now_iso
from app.db.models import Setting
from app.modules.v4.voice_pacing_calibration_settings import VoicePacingCalibrationSettingsService
from app.modules.v4.voice_provider_contracts import VoiceProviderProfile, VoiceProviderRegistry, VoiceProviderVoice
from app.modules.v4.voice_provider_registry import build_default_voice_settings, build_voice_provider_registry
from app.repositories import SettingsRepository

SETTINGS_KEY = "tts_settings_v3"


def default_tts_settings(
    runtime: Settings | None = None,
    *,
    registry: VoiceProviderRegistry | None = None,
) -> dict[str, Any]:
    return build_default_voice_settings(runtime, registry=registry)


class TTSSettingsService:
    def __init__(
        self,
        session: Session,
        repository: SettingsRepository,
        *,
        runtime_settings: Settings | None = None,
        registry: VoiceProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.runtime_settings = runtime_settings or get_settings()
        self.registry = registry or build_voice_provider_registry(
            self.runtime_settings,
            pacing_calibration_resolver=VoicePacingCalibrationSettingsService(
                session,
                repository,
            ).resolve,
        )
        self.resolver = ContractPathResolver.from_project_root()
        self._voice_catalog_cache: dict[str, tuple[bool, tuple[VoiceProviderVoice, ...] | AppError]] = {}

    def _schema(self) -> dict[str, Any]:
        import json
        path = self.resolver.schemas_dir / "settings" / "tts_settings.schema.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _validate(self, payload: Any) -> None:
        errors = sorted(Draft202012Validator(self._schema()).iter_errors(payload), key=lambda error: list(error.absolute_path))
        if errors:
            raise AppError(
                "TTS_SETTINGS_INVALID", "TTS settings do not match the current contract.", 422, retryable=False,
                details={"errors": [{"path": "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path), "message": error.message} for error in errors]},
            )
        profiles = payload.get("profiles", [])
        ids = [item.get("profile_id") for item in profiles if isinstance(item, dict)]
        if len(ids) != len(set(ids)):
            raise AppError("TTS_SETTINGS_INVALID", "TTS profile IDs must be unique.", 422, retryable=False)
        if payload.get("active_profile_id") not in ids:
            raise AppError("TTS_SETTINGS_INVALID", "The active TTS profile does not exist.", 422, retryable=False)

    def get_stored_settings(self) -> dict[str, Any]:
        row = self.repository.get(SETTINGS_KEY)
        payload = (
            deepcopy(row.value_json)
            if row is not None
            else default_tts_settings(self.runtime_settings, registry=self.registry)
        )
        self._validate(payload)
        return payload

    def get_settings(self) -> dict[str, Any]:
        payload = self.get_stored_settings()
        resolved = deepcopy(payload)
        for profile in resolved.get("profiles", []):
            if not isinstance(profile, dict):
                continue
            profile["voices"] = [
                {
                    "voice_id": voice.voice_id,
                    "display_name": voice.display_name,
                    "language": voice.language,
                }
                for voice in self._list_profile_voices(profile, suppress_errors=True)
            ]
        return resolved

    def put_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = deepcopy(payload)
        self._validate(clean)
        self._voice_catalog_cache.clear()
        self.repository.upsert(Setting(key=SETTINGS_KEY, value_json=clean, updated_at=utc_now_iso()))
        self.session.commit()
        return self.get_settings()

    def resolve_profile(self, profile_id: str, voice_id: str, speaking_rate: float) -> tuple[dict[str, Any], dict[str, Any]]:
        settings = self.get_stored_settings()
        profiles = {item["profile_id"]: item for item in settings["profiles"]}
        profile = profiles.get(profile_id)
        if profile is None or not profile.get("enabled"):
            raise AppError("TTS_PROFILE_NOT_READY", "The selected TTS profile is missing or disabled.", 409, retryable=False)
        voices = self._list_profile_voices(profile)
        voice = next((item for item in voices if item.voice_id == voice_id), None)
        if voice is None:
            raise AppError("TTS_VOICE_INVALID", "The selected voice does not belong to the selected TTS profile.", 422, retryable=False)
        if not 0.5 <= float(speaking_rate) <= 2.0:
            raise AppError("TTS_SPEAKING_RATE_INVALID", "Speaking rate must be between 0.5 and 2.0.", 422, retryable=False)
        resolved_profile = deepcopy(profile)
        resolved_profile["voices"] = [
            {"voice_id": item.voice_id, "display_name": item.display_name, "language": item.language}
            for item in voices
        ]
        resolved_voice = {"voice_id": voice.voice_id, "display_name": voice.display_name, "language": voice.language}
        return resolved_profile, resolved_voice

    def readiness(self, profile_id: str | None = None, voice_id: str | None = None) -> dict[str, Any]:
        settings = self.get_stored_settings()
        selected_id = profile_id or settings["active_profile_id"]
        profile = next((item for item in settings["profiles"] if item["profile_id"] == selected_id), None)
        issues: list[dict[str, str]] = []
        provider_key = str(profile.get("provider", "")) if profile is not None else None
        requires_ffmpeg = False

        if profile is None:
            issues.append({"error_code": "TTS_PROFILE_NOT_FOUND", "message": "The selected TTS profile does not exist."})
        elif not profile.get("enabled") or provider_key == "disabled":
            issues.append({"error_code": "TTS_PROVIDER_DISABLED", "message": "The selected TTS provider is disabled."})
        else:
            voices: tuple[VoiceProviderVoice, ...] = ()
            try:
                voices = self._list_profile_voices(profile)
            except AppError as exc:
                issues.append({"error_code": exc.error_code, "message": exc.message})
            selected_voice_id = voice_id or profile.get("default_voice_id")
            voice = next((item for item in voices if item.voice_id == selected_voice_id), None)
            if issues:
                pass
            elif not voices:
                issues.append({"error_code": "TTS_VOICE_CATALOG_EMPTY", "message": "The selected TTS provider did not report any voices."})
            elif selected_voice_id is None:
                voice = voices[0]
            elif voices and voice is None:
                issues.append({"error_code": "TTS_VOICE_INVALID", "message": "The selected voice does not belong to the selected profile."})

            if not issues and voice is not None:
                provider_profile = VoiceProviderProfile(
                    profile_id=str(profile["profile_id"]),
                    provider_key=str(provider_key),
                    enabled=bool(profile.get("enabled")),
                    display_name=str(profile.get("display_name", profile["profile_id"])),
                    voice_id=voice.voice_id,
                    language=voice.language,
                    speaking_rate=float(profile.get("default_speaking_rate", 1.0)),
                    requested_sample_rate_hz=int(profile["sample_rate_hz"]),
                    requested_output_format=str(profile["output_format"]),
                    provider_options=deepcopy(profile.get("provider_options") or {}),
                    maximum_segment_characters=int(profile["maximum_segment_characters"]),
                )
                try:
                    provider_readiness = self.registry.readiness(provider_profile)
                except AppError as exc:
                    issues.append({"error_code": exc.error_code, "message": exc.message})
                else:
                    requires_ffmpeg = bool(provider_readiness.details.get("requires_ffmpeg", False))
                    if not provider_readiness.ready:
                        issues.append(
                            {
                                "error_code": provider_readiness.issue_code or "VOICE_PROVIDER_NOT_READY",
                                "message": provider_readiness.message or "The selected Voice provider is not ready.",
                            }
                        )

        return {
            "status": "ready" if not issues else "not_configured",
            "issues": issues,
            "profile_id": selected_id if profile is not None else None,
            "provider": provider_key,
            "voice_id": voice_id or (profile.get("default_voice_id") if profile is not None else None),
            "requires_ffmpeg": requires_ffmpeg,
        }

    def _profile_context(self, profile: dict[str, Any]) -> VoiceProviderProfile:
        return VoiceProviderProfile(
            profile_id=str(profile["profile_id"]),
            provider_key=str(profile["provider"]),
            enabled=bool(profile.get("enabled")),
            display_name=str(profile.get("display_name", profile["profile_id"])),
            voice_id=str(profile.get("default_voice_id") or ""),
            language="",
            speaking_rate=float(profile.get("default_speaking_rate", 1.0)),
            requested_sample_rate_hz=int(profile["sample_rate_hz"]),
            requested_output_format=str(profile["output_format"]),
            provider_options=deepcopy(profile.get("provider_options") or {}),
            maximum_segment_characters=int(profile["maximum_segment_characters"]),
        )

    @staticmethod
    def _catalog_cache_key(profile: VoiceProviderProfile) -> str:
        return json.dumps(
            {
                "profile_id": profile.profile_id,
                "provider_key": profile.provider_key,
                "enabled": profile.enabled,
                "display_name": profile.display_name,
                "voice_id": profile.voice_id,
                "language": profile.language,
                "speaking_rate": profile.speaking_rate,
                "requested_sample_rate_hz": profile.requested_sample_rate_hz,
                "requested_output_format": profile.requested_output_format,
                "provider_options": profile.provider_options,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def _list_profile_voices(
        self,
        profile: dict[str, Any],
        *,
        suppress_errors: bool = False,
    ) -> tuple[VoiceProviderVoice, ...]:
        provider_key = str(profile.get("provider") or "")
        if not profile.get("enabled") or provider_key == "disabled":
            return ()
        profile_context = self._profile_context(profile)
        cache_key = self._catalog_cache_key(profile_context)
        cached = self._voice_catalog_cache.get(cache_key)
        if cached is not None:
            succeeded, value = cached
            if succeeded:
                assert not isinstance(value, AppError)
                return value
            assert isinstance(value, AppError)
            if suppress_errors:
                return ()
            raise value
        try:
            voices = self.registry.list_voices(provider_key, profile_context)
        except AppError as exc:
            self._voice_catalog_cache[cache_key] = (False, exc)
            if suppress_errors:
                return ()
            raise
        self._voice_catalog_cache[cache_key] = (True, voices)
        return voices
