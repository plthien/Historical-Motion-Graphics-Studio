from __future__ import annotations

from typing import Any

from app.api.errors import AppError
from app.modules.platform.video_pacing import VideoPacingError, validate_preferred_hold_seconds
from app.modules.v4.beat_timing_map import project_visual_intervals_for_source_spans
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection


def _fail(code: str, message: str, *, path: str, details: dict[str, Any] | None = None) -> None:
    payload = {"path": path, "code": code, "message": message}
    if details:
        payload.update(details)
    raise AppError(
        "SCENE_BREAKDOWN_SCENE_MATERIALIZATION_INVALID",
        "Scene & Beat Plan does not match the backend-owned authoring plan.",
        422,
        retryable=False,
        details={"errors": [payload]},
    )


def _selected_boundaries(
    *,
    scene_plan: dict[str, Any],
    output_scene: dict[str, Any],
    path: str,
) -> list[str]:
    partition = output_scene.get("partition")
    if not isinstance(partition, dict):
        _fail("partition_missing", "Every supplied Scene must select Beat boundary IDs.", path=f"{path}.partition")
    legal_ids = [
        str(item["boundary_id"])
        for item in scene_plan.get("legal_boundaries", [])
        if isinstance(item, dict)
    ]
    legal_set = set(legal_ids)
    boundary_ids = partition.get("boundary_ids")
    if not isinstance(boundary_ids, list) or any(not isinstance(value, str) for value in boundary_ids):
        _fail(
            "boundary_ids_invalid",
            "Partition must return an ordered boundary_ids array.",
            path=f"{path}.partition.boundary_ids",
        )
    if len(boundary_ids) != len(set(boundary_ids)):
        _fail(
            "duplicate_boundary_id",
            "Custom boundary IDs must be unique.",
            path=f"{path}.partition.boundary_ids",
        )
    unknown = [value for value in boundary_ids if value not in legal_set]
    if unknown:
        _fail(
            "unknown_boundary_id",
            "Selected boundary ID is not owned by this Scene.",
            path=f"{path}.partition.boundary_ids",
            details={"boundary_ids": unknown},
        )
    positions = [legal_ids.index(value) for value in boundary_ids]
    if positions != sorted(positions):
        _fail(
            "unordered_boundary_ids",
            "Custom boundary IDs must remain in canonical source order.",
            path=f"{path}.partition.boundary_ids",
        )
    return list(boundary_ids)


def materialize_scene_section(
    *,
    section: ExtractedSection,
    authoring_section: dict[str, Any],
    output_section: dict[str, Any],
    require_first_new_setup: bool = False,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Materialize exact Script Beat spans from backend Scene/boundary authority."""

    if str(output_section.get("section_id") or "") != section.section_id:
        _fail(
            "section_id_mismatch",
            "Step 1 section_id does not match the authoritative section.",
            path="$.section_id",
        )
    planned_scenes = authoring_section.get("scenes")
    output_scenes = output_section.get("scenes")
    if not isinstance(planned_scenes, list) or not isinstance(output_scenes, list):
        _fail("scene_list_invalid", "Scene collections are invalid.", path="$.scenes")
    expected_keys = [str(item.get("scene_key") or "") for item in planned_scenes]
    actual_keys = [str(item.get("scene_key") or "") for item in output_scenes]
    if actual_keys != expected_keys:
        _fail(
            "scene_order_mismatch",
            "Every backend-owned Scene must appear exactly once and in order.",
            path="$.scenes",
            details={"expected": expected_keys, "actual": actual_keys},
        )

    result = {"scenes": []}
    first_relation: str | None = None
    previous_relation: str | None = None
    cross_scene_continuity_count = 0
    cross_scene_new_setup_count = 0

    for scene_index, (planned, authored) in enumerate(zip(planned_scenes, output_scenes, strict=True)):
        path = f"$.scenes[{scene_index}]"
        boundary_ids = _selected_boundaries(scene_plan=planned, output_scene=authored, path=path)
        boundary_by_id = {
            str(item["boundary_id"]): item
            for item in planned["legal_boundaries"]
            if isinstance(item, dict)
        }
        offsets = [int(planned["source_start"])]
        offsets.extend(int(boundary_by_id[value]["source_offset"]) for value in boundary_ids)
        offsets.append(int(planned["source_end"]))

        beat_meta = authored.get("beats")
        if not isinstance(beat_meta, list) or len(beat_meta) != len(offsets) - 1:
            _fail(
                "beat_metadata_count_mismatch",
                "Beat metadata count must exactly match the selected partition.",
                path=f"{path}.beats",
                details={"expected": len(offsets) - 1, "actual": len(beat_meta) if isinstance(beat_meta, list) else None},
            )

        materialized_beats: list[dict[str, Any]] = []
        section_start = section.resolved_script_span[0]
        for beat_index, (start, end, metadata) in enumerate(
            zip(offsets[:-1], offsets[1:], beat_meta, strict=True)
        ):
            if end <= start:
                _fail("beat_span_invalid", "Materialized Beat span must be positive.", path=f"{path}.beats[{beat_index}]")
            local_start = start - section_start
            local_end = end - section_start
            if local_start < 0 or local_end > len(section.script_excerpt):
                _fail("beat_span_out_of_bounds", "Materialized Beat span is outside its Script section.", path=f"{path}.beats[{beat_index}]")
            relation = str(metadata.get("continuity_with_previous") or "")
            if first_relation is None:
                first_relation = relation
            elif beat_index == 0:
                if relation == "new_setup":
                    cross_scene_new_setup_count += 1
                else:
                    cross_scene_continuity_count += 1
            previous_relation = relation
            script_quote = section.script_excerpt[local_start:local_end]
            materialized_beats.append(
                {
                    "local_beat_order": beat_index + 1,
                    "resolved_script_span": [start, end],
                    "script_quote": script_quote,
                    "meaning_now": str(metadata["meaning_now"]),
                    "visible_state_now": str(metadata["visible_state_now"]),
                    "delta_now": str(metadata["delta_now"]),
                    "continuity_with_previous": relation,
                    "visual_context_ref": str(planned["visual_context_ref"]),
                }
            )
        result["scenes"].append(
            {
                "local_scene_order": scene_index + 1,
                "resolved_script_span": [offsets[0], offsets[-1]],
                "start_quote": materialized_beats[0]["script_quote"],
                "end_quote": materialized_beats[-1]["script_quote"],
                "beats": materialized_beats,
            }
        )

    del previous_relation
    if require_first_new_setup and first_relation != "new_setup":
        _fail(
            "plan_first_beat_requires_new_setup",
            "The first Beat of the whole Step-1 plan must use new_setup.",
            path="$.scenes[0].beats[0].continuity_with_previous",
        )
    return result, {
        "cross_scene_continuity_count": cross_scene_continuity_count,
        "cross_scene_new_setup_count": cross_scene_new_setup_count,
    }


def authoring_metrics(
    *,
    authoring_section: dict[str, Any],
    materialized: dict[str, Any],
    selection_metrics: dict[str, int],
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    """Describe the selected/materialized partition, never option availability."""

    scenes = materialized["scenes"]
    beat_count = sum(len(scene["beats"]) for scene in scenes)
    single = sum(1 for scene in scenes if len(scene["beats"]) == 1)
    preferred = authoring_section.get("preferred_hold_seconds")
    if not isinstance(preferred, dict):
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_GUIDANCE_INVALID",
            "Preferred Beat hold guidance is unavailable for selected-plan QC.",
            422,
            retryable=False,
        )
    try:
        minimum, maximum = validate_preferred_hold_seconds(preferred)
    except VideoPacingError as exc:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_GUIDANCE_INVALID",
            "Preferred Beat hold guidance is invalid for selected-plan QC.",
            422,
            retryable=False,
        ) from exc
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    if sample_rate <= 0:
        raise AppError(
            "SCENE_BREAKDOWN_PARTITION_TIMING_INVALID",
            "Approved Voice sample rate is invalid for selected-plan QC.",
            409,
            retryable=False,
        )

    all_in_band = 0
    timing_exception = 0
    selected_out_of_band_beats = 0
    for scene in scenes:
        spans = [
            list(beat["resolved_script_span"])
            for beat in scene.get("beats", [])
            if isinstance(beat, dict)
        ]
        if not spans:
            raise AppError(
                "SCENE_BREAKDOWN_SCENE_MATERIALIZATION_INVALID",
                "Every materialized Scene must contain at least one Beat.",
                422,
                retryable=False,
            )
        intervals = project_visual_intervals_for_source_spans(
            word_timing_map,
            spans,
        )
        durations = [
            (
                int(interval["visual_end_sample"])
                - int(interval["visual_start_sample"])
            )
            / sample_rate
            for interval in intervals
        ]
        out_of_band = sum(
            1
            for duration in durations
            if duration < minimum or duration > maximum
        )
        selected_out_of_band_beats += out_of_band
        if out_of_band == 0:
            all_in_band += 1
        else:
            timing_exception += 1

    return {
        "micro_scene_count": len(scenes),
        "beat_count": beat_count,
        "single_beat_scene_count": single,
        "multi_beat_scene_count": len(scenes) - single,
        **selection_metrics,
        "all_in_band_scene_count": all_in_band,
        "scene_with_timing_exception_count": timing_exception,
        "selected_out_of_band_beat_count": selected_out_of_band_beats,
    }


__all__ = ["authoring_metrics", "materialize_scene_section"]
