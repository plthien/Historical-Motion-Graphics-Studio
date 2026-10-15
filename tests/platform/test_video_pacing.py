from __future__ import annotations

from copy import deepcopy

import pytest

from app.modules.platform.video_pacing import (
    VideoPacingError,
    preferred_hold_range,
    resolve_video_pacing_profile,
    validate_pacing_profiles,
    validate_preferred_hold_seconds,
)
from app.modules.v4.script_word_budget import ScriptTimingError, build_runtime_diagnostics, validate_persisted_script_integrity
from tests.support.fixtures import sample_pacing_profiles, sample_script_artifact


def test_same_mode_resolves_differently_for_different_channel_profiles() -> None:
    channel_a = sample_pacing_profiles()
    channel_b = deepcopy(channel_a)
    channel_b["fast"]["high"]["preferred_hold_seconds"] = {"min": 4.0, "max": 5.0}
    fast_a = resolve_video_pacing_profile(channel_a, "fast")
    fast_b = resolve_video_pacing_profile(channel_b, "fast")
    assert preferred_hold_range(fast_a, "high") == (2.5, 4.0)
    assert preferred_hold_range(fast_b, "high") == (4.0, 5.0)
    assert fast_a["mode"] == fast_b["mode"] == "fast"


def test_profile_resolution_is_deterministic_and_returns_independent_copy() -> None:
    source = sample_pacing_profiles()
    first = resolve_video_pacing_profile(source, "fast")
    second = resolve_video_pacing_profile(source, "fast")
    assert first == second
    first["density_profiles"]["high"]["preferred_hold_seconds"]["min"] = 999
    assert resolve_video_pacing_profile(source, "fast") == second


def test_invalid_mode_profile_shape_or_range_fails_closed() -> None:
    profiles = sample_pacing_profiles()
    with pytest.raises(VideoPacingError):
        resolve_video_pacing_profile(profiles, "high")
    missing = deepcopy(profiles)
    del missing["slow"]
    with pytest.raises(VideoPacingError):
        validate_pacing_profiles(missing)
    invalid = deepcopy(profiles)
    invalid["fast"]["high"]["preferred_hold_seconds"] = {"min": 5.0, "max": 4.0}
    with pytest.raises(VideoPacingError):
        validate_pacing_profiles(invalid)


@pytest.mark.parametrize(
    "value",
    [
        {"min": 2.0, "max": 2.0},
        {"min": 3.0, "max": 2.0},
        {"min": 0.0, "max": 2.0},
        {"min": -1.0, "max": 2.0},
        {"min": True, "max": 2.0},
        {"min": 2.0, "max": False},
        {"min": float("nan"), "max": 2.0},
        {"min": 2.0, "max": float("inf")},
        {"min": 2.0, "max": 3.0, "unit": "seconds"},
    ],
)
def test_preferred_hold_runtime_invariant_rejects_invalid_values(value: dict) -> None:
    with pytest.raises(VideoPacingError):
        validate_preferred_hold_seconds(value)


@pytest.mark.parametrize("value", [{"min": 1.9, "max": 2.4}, {"min": 2.5, "max": 3.1}])
def test_preferred_hold_runtime_invariant_accepts_valid_values(value: dict) -> None:
    assert validate_preferred_hold_seconds(value) == (value["min"], value["max"])


def test_section_wpm_runtime_is_summed_and_whole_video_average_is_derived() -> None:
    sections = [
        {"section_id": "sec_001", "planned_narration_wpm": 180, "narration": " ".join(["hook"] * 180)},
        {"section_id": "sec_002", "planned_narration_wpm": 120, "narration": " ".join(["ending"] * 120)},
    ]
    diagnostics = build_runtime_diagnostics(target_duration_minutes=2, script_sections=sections)
    assert diagnostics.estimated_duration_minutes == 2.0
    assert diagnostics.actual_word_count == 300
    assert diagnostics.planned_average_wpm == 150


def test_persisted_script_profile_must_match_project_channel_profile_and_selected_mode() -> None:
    profiles = sample_pacing_profiles()
    script = sample_script_artifact()
    validate_persisted_script_integrity(script, pacing_profiles=profiles)
    other_channel = deepcopy(profiles)
    other_channel["medium"]["high"]["preferred_hold_seconds"] = {"min": 9.0, "max": 10.0}
    with pytest.raises(ScriptTimingError, match="does not match"):
        validate_persisted_script_integrity(script, pacing_profiles=other_channel)
    tampered = deepcopy(script)
    tampered["video_pacing_profile"] = resolve_video_pacing_profile(profiles, "slow")
    with pytest.raises(ScriptTimingError, match="does not match"):
        validate_persisted_script_integrity(tampered, pacing_profiles=profiles)
