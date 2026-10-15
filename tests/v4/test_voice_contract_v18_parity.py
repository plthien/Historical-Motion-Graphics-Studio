from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.api.voice_schemas_v4 import VoiceWorkspaceResponse
from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]


def _runtime_components() -> dict:
    return create_app().openapi()["components"]["schemas"]


def _static_components() -> dict:
    payload = yaml.safe_load((ROOT / "contracts/openapi/youtube_ai_studio_api.openapi.yaml").read_text(encoding="utf-8"))
    return payload["components"]["schemas"]


def test_tts_profile_contract_is_provider_neutral_everywhere() -> None:
    runtime = _runtime_components()["TTSStoredProfileModel"]
    static = _static_components()["TTSStoredProfileModel"]
    json_schema = json.loads((ROOT / "contracts/schemas/settings/tts_settings.schema.json").read_text(encoding="utf-8"))["$defs"]["TTSStoredProfileModel"]

    for schema in (runtime, static, json_schema):
        provider = schema["properties"]["provider"]
        assert provider["type"] == "string"
        assert provider.get("minLength") == 1
        assert "enum" not in provider
        assert "voices" not in schema["properties"]
        options = schema["properties"]["provider_options"]
        assert options["type"] == "object"

    resolved = _runtime_components()["TTSProfileModel"]
    assert "voices" in resolved["properties"]

    public_client = (ROOT / "src/api/client.ts").read_text(encoding="utf-8")
    assert "VoiceGenerationProvider" in public_client
    assert "kokoro_local" in public_client
    assert "provider: string" in public_client
    assert "provider_options: Record<string, unknown>" in public_client


def test_rich_voice_job_status_matches_runtime_static_and_typescript_contracts() -> None:
    runtime = _runtime_components()["VoiceJobStatusResponse"]
    static = _static_components()["VoiceJobStatusResponse"]
    expected = {
        "current_segment_id",
        "provider",
        "action",
        "retry_count",
        "max_attempts",
        "next_retry_at",
        "cancel_requested",
        "problem",
    }
    assert expected <= set(runtime["properties"])
    assert expected <= set(static["properties"])

    public_client = (ROOT / "src/api/client.ts").read_text(encoding="utf-8")
    for field in expected:
        assert field in public_client


def test_voice_client_exposes_cancel_and_no_provider_specific_endpoint_logic() -> None:
    public_client = (ROOT / "src/api/client.ts").read_text(encoding="utf-8")
    assert "cancelVoiceJob" in public_client
    assert "/voice/jobs/${jobId}/cancel" in public_client
    assert "KOKORO_COMMAND" not in public_client
    assert "subprocess" not in public_client


def test_voice_workspace_generation_provider_status_is_structured_contract() -> None:
    for components in (_runtime_components(), _static_components()):
        workspace = components["VoiceWorkspaceResponse"]
        providers = workspace["properties"]["available_generation_providers"]
        assert providers["items"]["$ref"].endswith("/VoiceGenerationProviderStatusModel")

        provider_status = components["VoiceGenerationProviderStatusModel"]
        assert provider_status["additionalProperties"] is False
        assert provider_status["properties"]["generation_provider"]["enum"] == ["elevenlabs", "kokoro_local"]
        assert provider_status["properties"]["alignment_provider"]["const"] == "elevenlabs"
        assert {"generation_ready", "generation_issue", "alignment_ready", "alignment_issue"} <= set(provider_status["required"])

        presets = components["VoiceGenerationPresetsModel"]
        assert presets["additionalProperties"] is False
        assert presets["required"] == ["economy", "quality_cloned_voice"]

        preset_status = components["VoiceGenerationPresetStatusModel"]
        assert preset_status["additionalProperties"] is False
        assert preset_status["required"] == ["ready", "issue", "model_id"]

        issue = components["VoiceReadinessIssueModel"]
        assert issue["additionalProperties"] is False
        assert "cause_error_code" in issue["properties"]

    public_client = (ROOT / "src/api/client.ts").read_text(encoding="utf-8")
    assert "VoiceGenerationProviderStatus[]" in public_client
    assert "generation_presets?: Record<VoicePreset, VoiceGenerationPresetStatus> | null" in public_client
    assert "cause_error_code?: string | null" in public_client


def test_voice_workspace_section_recommendation_reason_matches_runtime_static_and_typescript_contracts() -> None:
    runtime = _runtime_components()["VoiceSectionWorkspaceModel"]
    static = _static_components()["VoiceSectionWorkspaceModel"]
    json_schema = json.loads((ROOT / "contracts/schemas/v4/voice_workspace_response_v1.schema.json").read_text(encoding="utf-8"))["$defs"]["VoiceSectionWorkspaceModel"]

    for schema in (runtime, static, json_schema):
        assert "recommendation_reason" in schema["properties"]
        reason = schema["properties"]["recommendation_reason"]
        assert {"type": "string"} in reason["anyOf"]
        assert {"type": "null"} in reason["anyOf"]
        for field in ("regeneration_supported", "can_accept_pacing_deviation"):
            assert schema["properties"][field]["type"] == "boolean"
            assert field in schema["required"]
        block_reason = schema["properties"]["remediation_block_reason"]
        assert {"type": "string"} in block_reason["anyOf"]
        assert {"type": "null"} in block_reason["anyOf"]

    public_client = (ROOT / "src/api/client.ts").read_text(encoding="utf-8")
    assert "recommendation_reason: string | null" in public_client
    assert "regeneration_supported: boolean" in public_client
    assert "can_accept_pacing_deviation: boolean" in public_client
    assert "remediation_block_reason: string | null" in public_client


def test_voice_workspace_active_operation_target_section_matches_runtime_static_and_typescript_contracts() -> None:
    runtime = _runtime_components()["VoiceActiveOperationModel"]
    static = _static_components()["VoiceActiveOperationModel"]
    json_schema = json.loads((ROOT / "contracts/schemas/v4/voice_workspace_response_v1.schema.json").read_text(encoding="utf-8"))["$defs"]["VoiceActiveOperationModel"]

    for schema in (runtime, static, json_schema):
        assert "target_section_id" in schema["properties"]
        target = schema["properties"]["target_section_id"]
        assert {"type": "string"} in target["anyOf"]
        assert {"type": "null"} in target["anyOf"]

    public_client = (ROOT / "src/api/client.ts").read_text(encoding="utf-8")
    assert "target_section_id: string | null" in public_client


def _voice_workspace_payload() -> dict:
    return {
        "project_id": "project_001",
        "readiness": {"status": "ready", "issues": [], "requires_ffmpeg": True},
        "script_artifact_id": "script_001",
        "selected_tts_profile_id": "channel_elevenlabs_pvc",
        "selected_generation_provider": None,
        "selected_voice_id": "voice_001",
        "selected_voice_label": "voice_001",
        "speaking_rate": None,
        "available_generation_providers": [
            {
                "generation_provider": "elevenlabs",
                "display_name": "ElevenLabs",
                "ready": False,
                "issue": {"error_code": "VOICE_CHANNEL_PROFILE_REQUIRED", "message": "Channel Voice Profile is required."},
                "generation_ready": False,
                "generation_issue": {"error_code": "VOICE_CHANNEL_PROFILE_REQUIRED", "message": "Channel Voice Profile is required."},
                "generation_presets": {
                    "economy": {"ready": False, "issue": None, "model_id": "eleven_flash_v2_5"},
                    "quality_cloned_voice": {"ready": False, "issue": None, "model_id": "eleven_multilingual_v2"},
                },
                "alignment_ready": False,
                "alignment_issue": {
                    "error_code": "VOICE_ALIGNMENT_NOT_READY",
                    "message": "ElevenLabs Forced Alignment is not configured.",
                    "cause_error_code": "ELEVENLABS_API_KEY_MISSING",
                },
                "alignment_provider": "elevenlabs",
            },
            {
                "generation_provider": "kokoro_local",
                "display_name": "Kokoro Local",
                "ready": False,
                "issue": None,
                "generation_ready": True,
                "generation_issue": None,
                "alignment_ready": False,
                "alignment_issue": {
                    "error_code": "VOICE_ALIGNMENT_NOT_READY",
                    "message": "ElevenLabs Forced Alignment is not configured.",
                    "cause_error_code": "ELEVENLABS_API_KEY_MISSING",
                },
                "alignment_provider": "elevenlabs",
            },
        ],
        "kokoro_voice_catalog": [{"voice_id": "af_heart", "display_name": "AF Heart", "language": "en-us"}],
        "active_operation": None,
        "current_narration_audio_artifact_id": None,
        "audio_duration_seconds": None,
        "audio_mime_type": None,
        "audio_sha256": None,
        "audio_url": None,
        "current_narration_word_timing_map_artifact_id": None,
        "timing_map_status": None,
        "timing_source": None,
        "sections": [],
        "segments": [],
        "approval_eligible": False,
        "available_actions": [],
    }


def test_voice_workspace_rejects_ad_hoc_generation_provider_fields() -> None:
    payload = _voice_workspace_payload()
    payload["available_generation_providers"][0]["backend_authority"] = "leak"

    with pytest.raises(ValidationError):
        VoiceWorkspaceResponse.model_validate(payload)


def test_voice_workspace_rejects_ad_hoc_generation_preset_fields() -> None:
    payload = _voice_workspace_payload()
    payload["available_generation_providers"][0]["generation_presets"]["economy"]["unexpected"] = "leak"

    with pytest.raises(ValidationError):
        VoiceWorkspaceResponse.model_validate(payload)
