from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import ceil, isfinite
from statistics import mean, median
from typing import Any, Literal

from app.modules.platform.video_pacing import VideoPacingError, validate_preferred_hold_seconds
from app.modules.v4.beat_timing_map import project_visual_intervals_for_source_spans
from app.modules.v4.script_word_budget import count_narration_words, validate_planned_narration_wpm

TIMING_POLICY_VERSION = "approved_voice_static_image_v6"

@dataclass(frozen=True, slots=True)
class TimingEvaluation:
    status: Literal["passed", "warning"]
    warnings: tuple[str, ...]
    metrics: dict[str, Any]


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = max(1, ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _duration(beat: dict[str, Any]) -> float:
    value = beat.get("estimated_duration_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)) or float(value) <= 0:
        raise ValueError("estimated_duration_seconds must be finite and positive")
    return float(value)


def _range(guidance: dict[str, Any]) -> tuple[float, float]:
    try:
        hold = guidance["preferred_hold_seconds"]
    except (KeyError, TypeError) as exc:
        raise ValueError("visual cadence guidance is malformed") from exc
    try:
        return validate_preferred_hold_seconds(hold)
    except VideoPacingError as exc:
        raise ValueError("visual cadence guidance preferred hold range is invalid") from exc


def apply_approved_voice_durations(*, scenes: list[dict[str, Any]], word_timing_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Populate the historical duration field from realized approved-Voice visual holds."""
    result = deepcopy(scenes)
    beats = [beat for scene in result for beat in scene.get("beats", []) if isinstance(beat, dict)]
    if not beats:
        return result
    intervals = project_visual_intervals_for_source_spans(word_timing_map, [beat["resolved_script_span"] for beat in beats])
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    if sample_rate <= 0:
        raise ValueError("approved Voice timing sample rate is invalid")
    for beat, interval in zip(beats, intervals, strict=True):
        beat["estimated_duration_seconds"] = round((int(interval["visual_end_sample"]) - int(interval["visual_start_sample"])) / sample_rate, 4)
    return result


def evaluate_timing_diagnostics(*, scenes: list[dict[str, Any]], word_timing_map: dict[str, Any], planned_narration_wpm: int, visual_cadence_guidance: dict[str, Any]) -> TimingEvaluation:
    """Post-semantic cadence telemetry. Timing can trigger rescan, never segmentation."""
    pace_metadata = validate_planned_narration_wpm(planned_narration_wpm)
    minimum, maximum = _range(visual_cadence_guidance)
    beats = [beat for scene in scenes for beat in scene.get("beats", [])]
    visual = [_duration(beat) for beat in beats]
    spans = [beat["resolved_script_span"] for beat in beats]
    intervals = project_visual_intervals_for_source_spans(word_timing_map, spans) if spans else []
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    spoken = [(int(item["speech_end_sample"]) - int(item["speech_start_sample"])) / sample_rate for item in intervals] if sample_rate > 0 else []
    scene_durations = [sum(_duration(beat) for beat in scene.get("beats", [])) for scene in scenes]
    count = len(visual)
    below = sum(1 for value in visual if value < minimum)
    above = sum(1 for value in visual if value > maximum)
    within = max(0, count - below - above)
    denominator = count or 1
    below_ratio, above_ratio, within_ratio = below / denominator, above / denominator, within / denominator
    warnings: list[str] = []
    if count and above_ratio > 0.5:
        warnings.append("Most accepted semantic beats are longer than this section's preferred visual hold band under approved Voice timing; rescan for missed genuine drawable states, but do not split on duration alone.")
    elif count and below_ratio > 0.5:
        warnings.append("Most accepted semantic beats are shorter than this section's preferred visual hold band under approved Voice timing; rescan for same-dominant-still fragmentation, but do not merge across a genuine visual-state change.")
    status: Literal["passed", "warning"] = "warning" if warnings else "passed"
    metrics = {
        "planned_narration_wpm": pace_metadata,
        "beat_count": count,
        "scene_count": len(scenes),
        "estimated_section_duration_seconds": round(sum(visual), 1),
        "average_beat_duration_seconds": round(mean(visual), 1) if visual else 0.0,
        "average_narration_duration_seconds": round(mean(spoken), 1) if spoken else 0.0,
        "median_beat_duration_seconds": round(median(visual), 1) if visual else 0.0,
        "p90_beat_duration_seconds": round(_nearest_rank_percentile(visual, 0.90), 1),
        "max_beat_duration_seconds": round(max(visual), 1) if visual else 0.0,
        "average_scene_duration_seconds": round(mean(scene_durations), 1) if scene_durations else 0.0,
        "max_scene_duration_seconds": round(max(scene_durations), 1) if scene_durations else 0.0,
        "single_beat_scene_count": sum(1 for scene in scenes if len(scene.get("beats") or []) == 1),
        "preferred_hold_seconds": {"min": minimum, "max": maximum},
        "within_preferred_ratio": round(within_ratio, 4),
        "below_preferred_ratio": round(below_ratio, 4),
        "above_preferred_ratio": round(above_ratio, 4),
        "pacing_conformance": status,
    }
    return TimingEvaluation(status=status, warnings=tuple(warnings), metrics=metrics)


def section_timing_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return dict(metrics)

__all__ = ["TIMING_POLICY_VERSION", "TimingEvaluation", "apply_approved_voice_durations", "count_narration_words", "evaluate_timing_diagnostics", "section_timing_metrics"]
