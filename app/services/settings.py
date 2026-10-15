from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.time import utc_now_iso
from app.modules.v4.voice_pacing_calibration_settings import (
    KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY,
    VOICE_PACING_CALIBRATION_OVERRIDES_SETTINGS_KEY,
)
from app.repositories import SettingsRepository


_SERVER_ONLY_SETTING_KEYS = frozenset({
    "voice_provider_api_keys",
    KOKORO_VOICE_PACING_HISTORY_SETTINGS_KEY,
    VOICE_PACING_CALIBRATION_OVERRIDES_SETTINGS_KEY,
})


class SettingsService:
    def __init__(
        self,
        session: Session,
        settings_repository: SettingsRepository,
    ) -> None:
        self.session = session
        self.settings_repository = settings_repository

    def get_settings(self) -> dict[str, object]:
        rows = self.settings_repository.list_all()
        return {
            row.key: row.value_json
            for row in rows
            if row.key not in _SERVER_ONLY_SETTING_KEYS
        }

    def update_settings(
        self,
        settings_payload: dict[str, object],
    ) -> dict[str, object]:
        rows = self.settings_repository.list_all()
        server_only = {
            row.key: row.value_json
            for row in rows
            if row.key in _SERVER_ONLY_SETTING_KEYS
        }
        public_payload = {
            key: value
            for key, value in settings_payload.items()
            if key not in _SERVER_ONLY_SETTING_KEYS
        }
        self.settings_repository.replace_all(
            {**public_payload, **server_only},
            utc_now_iso(),
        )
        self.session.commit()
        return self.get_settings()
