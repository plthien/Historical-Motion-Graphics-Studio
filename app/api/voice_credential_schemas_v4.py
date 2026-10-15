from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ElevenLabsCredentialStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["elevenlabs"] = "elevenlabs"
    api_key_configured: bool
    source: Literal["stored", "environment", "none"]


class UpdateElevenLabsCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str


class ElevenLabsConnectionTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider: Literal["elevenlabs"] = "elevenlabs"
    status: Literal["ready"]
    model_id: Literal["eleven_multilingual_v2"]
    authentication_accepted: bool
    catalog_reachable: bool
    voice_count: int = Field(ge=0)


class ElevenLabsProfessionalVoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    language: str = Field(min_length=1)
    ready_presets: list[Literal["economy", "quality_cloned_voice"]]


class ElevenLabsProfessionalVoiceCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["elevenlabs"] = "elevenlabs"
    voices: list[ElevenLabsProfessionalVoiceModel]


class ElevenLabsVoicePacingCalibrationEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_id: Literal["eleven_flash_v2_5", "eleven_multilingual_v2"]
    native_wpm_at_rate_1: float | None = Field(default=None, ge=60.0, le=300.0)
    source: Literal["override", "registry", "missing"]
    default_native_wpm_at_rate_1: float | None = Field(default=None, ge=60.0, le=300.0)


class ElevenLabsVoicePacingCalibrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["elevenlabs"] = "elevenlabs"
    voice_id: str = Field(min_length=1)
    calibrations: dict[
        Literal["economy", "quality_cloned_voice"],
        ElevenLabsVoicePacingCalibrationEntryModel,
    ]


class UpdateElevenLabsVoicePacingCalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_preset: Literal["economy", "quality_cloned_voice"]
    native_wpm_at_rate_1: float = Field(ge=60.0, le=300.0)
