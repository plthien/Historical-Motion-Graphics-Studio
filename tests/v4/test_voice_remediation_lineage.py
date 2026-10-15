from __future__ import annotations

import inspect

from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.voice_segmentation import segment_narration


def test_segmentation_input_identity_has_one_owner() -> None:
    segmentation_source = inspect.getsource(segment_narration)
    submission_source = inspect.getsource(DurableVoiceService.submit_generation)

    assert 'payload["segmentation_input_sha256"]' in segmentation_source
    assert 'segmentation_material = {key: value for key, value in segmentation.items()' not in submission_source
    assert 'segmentation["segmentation_input_sha256"] = canonical_hash' not in submission_source


def test_stale_section_remediation_requires_fresh_voice_generation() -> None:
    remediation_source = inspect.getsource(DurableVoiceService._section_remediation_plan)
    assert "Generate a fresh Voice before regenerating individual sections." in remediation_source

def test_provider_pacing_policy_revision_is_frozen_and_fenced_for_section_remediation() -> None:
    submission_source = inspect.getsource(DurableVoiceService.submit_generation)
    remediation_source = inspect.getsource(DurableVoiceService._section_remediation_plan)

    assert '"voice_pacing_policy_identity_sha256"' in submission_source
    assert "pacing_policy_identity_sha256 = self._voice_pacing_policy_identity(provider_key, pacing_plan)" in submission_source
    assert 'source_settings_snapshot.get("voice_pacing_policy_identity_sha256")' in remediation_source
    assert "self._voice_pacing_policy_identity(provider_key, source_pacing_plan)" in remediation_source
    assert "resolve_voice_pacing_calibration" not in submission_source
