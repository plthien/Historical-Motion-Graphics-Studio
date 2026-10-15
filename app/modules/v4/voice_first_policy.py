from __future__ import annotations

VOICE_ARCHITECTURE_VERSION = "voice_generation_providers_v1"
SPOKEN_TEXT_POLICY_VERSION = "spoken_text_overrides_v1"
DELIVERY_POLICY_VERSION = "sparse_pause_cues_v1"
ELEVENLABS_SYNTHESIS_POLICY_VERSION = "elevenlabs_pvc_presets_v1"
ELEVENLABS_SEED_POLICY_VERSION = "stable_segment_performance_identity_u32_v2"
FORCED_ALIGNMENT_POLICY_VERSION = "elevenlabs_forced_alignment_v1"
BEAT_TIMING_POLICY_VERSION = "voice_first_beat_timing_v1"

ACTIVE_VOICE_PRESETS = {
    "economy": "eleven_flash_v2_5",
    "quality_cloned_voice": "eleven_multilingual_v2",
}

PAUSE_SECONDS_BY_STRENGTH = {
    "short": 0.25,
    "medium": 0.55,
    "long": 0.90,
}

ELEVENLABS_STYLE = 0.0
DEFAULT_STABILITY = 0.50
DEFAULT_SIMILARITY_BOOST = 0.75
DEFAULT_USE_SPEAKER_BOOST = True
ELEVENLABS_SPEED_MIN = 0.70
ELEVENLABS_SPEED_MAX = 1.20
