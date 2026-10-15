from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.modules.v4.voice_first_elevenlabs_adapter import VoiceFirstElevenLabsProviderAdapter
from app.modules.v4.voice_provider_contracts import VoiceProviderVoice


ROOT = Path(__file__).resolve().parents[2]


def test_professional_voice_catalog_projects_channel_pvc_readiness_without_synthesis(monkeypatch) -> None:
    adapter = VoiceFirstElevenLabsProviderAdapter(Settings(root_dir=ROOT))
    action_context = object()
    client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(adapter, "_action_context", lambda *args, **kwargs: action_context)
    monkeypatch.setattr(adapter, "_client_for_action", lambda _context: client)
    monkeypatch.setattr(
        adapter,
        "_list_voices_for_action",
        lambda *args, **kwargs: (
  VoiceProviderVoice("pvc_both", "PVC Both", "en"),
  VoiceProviderVoice("pvc_quality", "PVC Quality", "en"),
  VoiceProviderVoice("premade", "Premade", "en"),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_cached_voices",
        lambda *args, **kwargs: (
  {
      "voice_id": "pvc_both",
      "name": "PVC Both",
      "category": "professional",
      "fine_tuning": {"state": {"eleven_flash_v2_5": "fine_tuned", "eleven_multilingual_v2": "fine_tuned"}},
  },
  {
      "voice_id": "pvc_quality",
      "name": "PVC Quality",
      "category": "professional",
      "fine_tuning": {"state": {"eleven_multilingual_v2": "fine_tuned"}},
  },
  {
      "voice_id": "premade",
      "name": "Premade",
      "category": "premade",
      "fine_tuning": {"state": {"eleven_flash_v2_5": "fine_tuned", "eleven_multilingual_v2": "fine_tuned"}},
  },
        ),
    )

    catalog = adapter.professional_voice_catalog()

    assert catalog == (
        {"voice_id": "pvc_both", "display_name": "PVC Both", "language": "en", "ready_presets": ["economy", "quality_cloned_voice"]},
        {"voice_id": "pvc_quality", "display_name": "PVC Quality", "language": "en", "ready_presets": ["quality_cloned_voice"]},
    )
