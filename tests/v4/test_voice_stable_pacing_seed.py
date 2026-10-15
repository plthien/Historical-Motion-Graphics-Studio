from __future__ import annotations

from copy import deepcopy

from app.modules.v4.durable_voice_service import _stable_voice_segment_seed
from app.modules.v4.voice_first_policy import ELEVENLABS_SEED_POLICY_VERSION
from app.modules.v4.voice_pacing import raw_segment_synthesis_identity_sha256


def _segment() -> dict:
    return {
        "segment_id": "seg_001_001",
        "segment_order": 1,
        "section_id": "sec_001",
        "source_start": 0,
        "source_end": 20,
        "text_sha256": "text-hash",
        "exact_text": "A controlled pacing test.",
        "voice_first": {"seed": 1234},
    }


def _pacing_plan(speed: float) -> dict:
    return {
        "sections": [
            {
                "section_id": "sec_001",
                "provider_pacing_control": "relative_multiplier",
                "provider_pacing_value": speed,
                "provider_pacing_policy_version": "elevenlabs_calibrated_native_wpm_v1:test",
                "provider_pacing_value_range": [0.7, 1.2],
                "provider_pacing_clamped": False,
            }
        ]
    }


def test_pacing_remediation_keeps_performance_seed_but_changes_raw_synthesis_identity() -> None:
    provider_identity = {
        "provider": "elevenlabs",
        "voice_id": "voice-A",
        "execution_identity": {
            "model_id": "eleven_flash_v2_5",
            "seed_policy": ELEVENLABS_SEED_POLICY_VERSION,
        },
    }
    segment = _segment()
    seed_before = _stable_voice_segment_seed(
        provider_identity=provider_identity,
        voice_preset="economy",
        segment=segment,
        provider_text_sha256="provider-text-hash",
        spoken_compilation_sha256="spoken-hash",
        authored_spoken_mapping_sha256="mapping-hash",
        synthesis_context_sha256="context-hash",
    )
    seed_after = _stable_voice_segment_seed(
        provider_identity=deepcopy(provider_identity),
        voice_preset="economy",
        segment=deepcopy(segment),
        provider_text_sha256="provider-text-hash",
        spoken_compilation_sha256="spoken-hash",
        authored_spoken_mapping_sha256="mapping-hash",
        synthesis_context_sha256="context-hash",
    )
    assert seed_before == seed_after

    settings = {"voice_preset": "economy", "seed_policy": ELEVENLABS_SEED_POLICY_VERSION}
    identity_before = raw_segment_synthesis_identity_sha256(
        provider_identity=provider_identity,
        settings_snapshot=settings,
        pacing_plan=_pacing_plan(1.066),
        segment=segment,
        synthesis_context_sha256="context-hash",
    )
    identity_after = raw_segment_synthesis_identity_sha256(
        provider_identity=provider_identity,
        settings_snapshot=settings,
        pacing_plan=_pacing_plan(1.143),
        segment=segment,
        synthesis_context_sha256="context-hash",
    )
    assert identity_before != identity_after


def test_performance_seed_changes_when_true_performance_identity_changes() -> None:
    base_provider_identity = {
        "provider": "elevenlabs",
        "voice_id": "voice-A",
        "execution_identity": {
            "model_id": "eleven_flash_v2_5",
            "seed_policy": ELEVENLABS_SEED_POLICY_VERSION,
        },
    }
    common = dict(
        voice_preset="economy",
        segment=_segment(),
        provider_text_sha256="provider-text-hash",
        spoken_compilation_sha256="spoken-hash",
        authored_spoken_mapping_sha256="mapping-hash",
        synthesis_context_sha256="context-hash",
    )
    base = _stable_voice_segment_seed(provider_identity=base_provider_identity, **common)

    changed_voice = deepcopy(base_provider_identity)
    changed_voice["voice_id"] = "voice-B"
    assert _stable_voice_segment_seed(provider_identity=changed_voice, **common) != base

    changed_context = dict(common)
    changed_context["synthesis_context_sha256"] = "context-hash-2"
    assert _stable_voice_segment_seed(provider_identity=base_provider_identity, **changed_context) != base

    changed_text = dict(common)
    changed_text["provider_text_sha256"] = "provider-text-hash-2"
    assert _stable_voice_segment_seed(provider_identity=base_provider_identity, **changed_text) != base
