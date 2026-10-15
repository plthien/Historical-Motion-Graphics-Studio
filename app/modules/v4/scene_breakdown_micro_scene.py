from __future__ import annotations

from typing import Any

from app.api.errors import AppError
from app.modules.platform.video_pacing import VideoPacingError, validate_preferred_hold_seconds
from app.modules.v4.beat_timing_map import project_visual_intervals_for_source_spans
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection


def _timing_atoms(timing_section: dict[str, Any]) -> list[dict[str, Any]]:
    atoms = timing_section.get("timing_atoms")
    if not isinstance(atoms, list) or not atoms or any(not isinstance(item, dict) for item in atoms):
        raise AppError(
            "SCENE_BREAKDOWN_MICRO_SCENE_TIMING_INVALID",
            "Approved Voice timing atoms are unavailable for micro-Scene planning.",
            409,
            retryable=False,
        )
    return atoms


def _span_duration_seconds(
    *,
    source_start: int,
    source_end: int,
    word_timing_map: dict[str, Any],
) -> float:
    intervals = project_visual_intervals_for_source_spans(
        word_timing_map,
        [[source_start, source_end]],
    )
    sample_rate = int(word_timing_map.get("sample_rate_hz") or 0)
    if sample_rate <= 0:
        raise AppError(
            "SCENE_BREAKDOWN_MICRO_SCENE_TIMING_INVALID",
            "Approved Voice sample rate is invalid.",
            409,
            retryable=False,
        )
    interval = intervals[0]
    return (
        int(interval["visual_end_sample"]) - int(interval["visual_start_sample"])
    ) / sample_rate

def _safe_timing_atom_endpoints(
    *,
    section: ExtractedSection,
    timing_section: dict[str, Any],
) -> set[int]:
    section_start, section_end = section.resolved_script_span
    endpoints: set[int] = set()
    for atom in _timing_atoms(timing_section):
        if atom.get("end_boundary_safe") is not True:
            continue
        try:
            endpoint = int(atom["source_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if section_start < endpoint < section_end:
            endpoints.add(endpoint)
    return endpoints


def _natural_endpoints(
    *,
    section: ExtractedSection,
    timing_section: dict[str, Any],
    mandatory_endpoints: set[int] | None = None,
) -> list[int]:
    """Return natural Scene endpoints that are also legal timing-atom ends."""

    section_start, section_end = section.resolved_script_span
    safe_atom_ends = _safe_timing_atom_endpoints(
        section=section,
        timing_section=timing_section,
    )
    clauses = timing_section.get("clauses")
    values: list[int] = []
    if isinstance(clauses, list):
        for clause in clauses:
            if (
                not isinstance(clause, dict)
                or clause.get("end_boundary_safe") is not True
            ):
                continue
            try:
                endpoint = int(clause["source_end"])
            except (KeyError, TypeError, ValueError):
                continue
            if (
                section_start < endpoint < section_end
                and endpoint in safe_atom_ends
            ):
                values.append(endpoint)
    values.append(section_end)
    values.extend(mandatory_endpoints or set())
    return sorted(set(values))


def _context_authority(
    *,
    section: ExtractedSection,
    visual_context_catalog: list[dict[str, Any]],
) -> tuple[set[int], list[tuple[int, int, str]]]:
    section_start, section_end = section.resolved_script_span
    ranges: list[tuple[int, int, str]] = []
    for context in visual_context_catalog:
        context_id = str(context.get("context_id") or "")
        for presence_range in context.get("presence_ranges", []):
            if presence_range.get("section_id") != section.section_id:
                continue
            start, end = map(int, presence_range["resolved_script_span"])
            clipped_start = max(start, section_start)
            clipped_end = min(end, section_end)
            if clipped_start < clipped_end:
                ranges.append((clipped_start, clipped_end, context_id))
    ranges.sort()
    if not ranges:
        raise AppError(
            "SCENE_BREAKDOWN_VISUAL_CONTEXT_BOUNDARY_INVALID",
            "The Script section has no resolved visual-context authority.",
            422,
            retryable=False,
            details={"section_id": section.section_id},
        )
    endpoints: set[int] = set()
    for (_previous_start, previous_end, previous_ref), (
        current_start,
        _current_end,
        current_ref,
    ) in zip(ranges[:-1], ranges[1:], strict=True):
        if previous_ref == current_ref:
            continue
        boundary = current_start
        if previous_end < current_start:
            gap_start = previous_end - section_start
            gap_end = current_start - section_start
            if section.script_excerpt[gap_start:gap_end].isspace():
                boundary = previous_end
        if section_start < boundary < section_end:
            endpoints.add(boundary)
    return endpoints, ranges


def _context_ref_for_span(
    *,
    source_start: int,
    source_end: int,
    section: ExtractedSection,
    context_ranges: list[tuple[int, int, str]],
) -> str:
    local_start = source_start - section.resolved_script_span[0]
    local_end = source_end - section.resolved_script_span[0]
    source = section.script_excerpt[local_start:local_end]
    refs: set[str] = set()
    for offset, character in enumerate(source):
        if character.isspace():
            continue
        absolute = source_start + offset
        owners = {
            context_id
            for start, end, context_id in context_ranges
            if start <= absolute < end
        }
        if len(owners) != 1:
            raise AppError(
                "SCENE_BREAKDOWN_VISUAL_CONTEXT_BOUNDARY_INVALID",
                "Every non-whitespace micro-Scene source character must have exactly one visual context.",
                422,
                retryable=False,
                details={
                    "section_id": section.section_id,
                    "source_offset": absolute,
                    "context_refs": sorted(owners),
                },
            )
        refs.update(owners)
    if len(refs) != 1:
        raise AppError(
            "SCENE_BREAKDOWN_VISUAL_CONTEXT_BOUNDARY_INVALID",
            "A backend micro-Scene may not cross visual-context authority.",
            422,
            retryable=False,
            details={
                "section_id": section.section_id,
                "source_span": [source_start, source_end],
                "context_refs": sorted(refs),
            },
        )
    return next(iter(refs))


def build_micro_scenes(
    *,
    section: ExtractedSection,
    timing_section: dict[str, Any],
    word_timing_map: dict[str, Any],
    preferred_hold_seconds: dict[str, Any],
    visual_context_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build deterministic punctuation/clause-derived planning Scenes.

    Scene boundaries are backend planning containers only. No semantic or visual
    interpretation occurs here. Tiny natural clauses are accumulated left to
    right until one preferred Beat can fit; a final tiny remainder merges into
    the preceding Scene. Long natural clauses are never split arbitrarily.
    """

    if str(timing_section.get("section_id") or "") != section.section_id:
        raise AppError(
            "SCENE_BREAKDOWN_MICRO_SCENE_SOURCE_MISMATCH",
            "Scene timing section does not match the authoritative Script section.",
            409,
            retryable=False,
            details={"section_id": section.section_id},
        )
    section_start, section_end = section.resolved_script_span
    if (
        int(timing_section.get("source_start", -1)) != section_start
        or int(timing_section.get("source_end", -1)) != section_end
        or section_end - section_start != len(section.script_excerpt)
    ):
        raise AppError(
            "SCENE_BREAKDOWN_MICRO_SCENE_SOURCE_MISMATCH",
            "Scene timing source span does not match the frozen Script section.",
            409,
            retryable=False,
            details={"section_id": section.section_id},
        )
    try:
        minimum_hold, maximum_hold = validate_preferred_hold_seconds(
            preferred_hold_seconds
        )
    except VideoPacingError as exc:
        raise AppError(
            "SCENE_BREAKDOWN_MICRO_SCENE_GUIDANCE_INVALID",
            "Preferred Beat hold guidance is invalid.",
            422,
            retryable=False,
        ) from exc
    mandatory_endpoints, context_ranges = _context_authority(
        section=section,
        visual_context_catalog=visual_context_catalog,
    )
    safe_atom_ends = _safe_timing_atom_endpoints(
        section=section,
        timing_section=timing_section,
    )
    unsafe_context_endpoints = sorted(mandatory_endpoints - safe_atom_ends)
    if unsafe_context_endpoints:
        raise AppError(
            "SCENE_BREAKDOWN_VISUAL_CONTEXT_BOUNDARY_INVALID",
            "A visual-context transition is not an exact valid source/timing boundary.",
            422,
            retryable=False,
            details={
                "section_id": section.section_id,
                "source_offsets": unsafe_context_endpoints,
            },
        )

    spans: list[list[int]] = []
    current_start = section_start
    timing_atoms = _timing_atoms(timing_section)
    for endpoint in _natural_endpoints(
        section=section,
        timing_section=timing_section,
        mandatory_endpoints=mandatory_endpoints,
    ):
        if endpoint <= current_start:
            continue
        has_timing = any(
            int(atom["source_end"]) > current_start
            and int(atom["source_start"]) < endpoint
            for atom in timing_atoms
        )
        if not has_timing:
            # Trailing whitespace/punctuation without spoken authority belongs to
            # the preceding Scene. Never manufacture a timing-only empty Scene.
            continue
        duration = _span_duration_seconds(
            source_start=current_start,
            source_end=endpoint,
            word_timing_map=word_timing_map,
        )
        if (
            endpoint in mandatory_endpoints
            or current_start in mandatory_endpoints
            or duration >= minimum_hold
        ):
            spans.append([current_start, endpoint])
            current_start = endpoint

    if current_start < section_end:
        if spans:
            spans[-1][1] = section_end
        else:
            spans.append([section_start, section_end])
    if not spans:
        spans = [[section_start, section_end]]

    undersized = []
    for source_start, source_end in spans:
        duration = _span_duration_seconds(
            source_start=source_start,
            source_end=source_end,
            word_timing_map=word_timing_map,
        )
        if duration < minimum_hold and source_start not in mandatory_endpoints and source_end not in mandatory_endpoints:
            undersized.append(
                {
                    "source_start": source_start,
                    "source_end": source_end,
                    "duration_seconds": round(duration, 3),
                }
            )
    if undersized:
        raise AppError(
            "SCENE_BREAKDOWN_MICRO_SCENE_MINIMUM_HOLD_UNSATISFIABLE",
            "The approved Script section cannot be partitioned into micro-Scenes that meet the preferred minimum hold.",
            422,
            retryable=False,
            details={
                "section_id": section.section_id,
                "preferred_min_seconds": minimum_hold,
                "undersized_micro_scenes": undersized,
            },
        )

    for _source_start, source_end in spans[:-1]:
        if source_end not in safe_atom_ends:
            raise AppError(
                "SCENE_BREAKDOWN_MICRO_SCENE_BOUNDARY_INVALID",
                "An internal micro-Scene boundary is not a safe canonical timing-atom endpoint.",
                500,
                retryable=False,
                details={
                    "section_id": section.section_id,
                    "source_end": source_end,
                },
            )

    scenes: list[dict[str, Any]] = []
    expected_start = section_start
    reconstructed: list[str] = []
    for order, (source_start, source_end) in enumerate(spans, 1):
        if source_start != expected_start or source_end <= source_start:
            raise AppError(
                "SCENE_BREAKDOWN_MICRO_SCENE_COVERAGE_INVALID",
                "Micro-Scenes must form one contiguous, ordered section partition.",
                500,
                retryable=False,
                details={"section_id": section.section_id},
            )
        local_start = source_start - section_start
        local_end = source_end - section_start
        script_text = section.script_excerpt[local_start:local_end]
        if not script_text.strip():
            raise AppError(
                "SCENE_BREAKDOWN_MICRO_SCENE_COVERAGE_INVALID",
                "Micro-Scenes may not own only whitespace or non-content source.",
                500,
                retryable=False,
                details={
                    "section_id": section.section_id,
                    "source_start": source_start,
                    "source_end": source_end,
                },
            )
        reconstructed.append(script_text)
        scenes.append(
            {
                "scene_key": f"{section.section_id}_scene_{order:03d}",
                "local_scene_order": order,
                "source_start": source_start,
                "source_end": source_end,
                "script_text": script_text,
                "visual_context_ref": _context_ref_for_span(
                    source_start=source_start,
                    source_end=source_end,
                    section=section,
                    context_ranges=context_ranges,
                ),
                "duration_seconds": round(
                    _span_duration_seconds(
                        source_start=source_start,
                        source_end=source_end,
                        word_timing_map=word_timing_map,
                    ),
                    3,
                ),
            }
        )
        expected_start = source_end

    if expected_start != section_end or "".join(reconstructed) != section.script_excerpt:
        raise AppError(
            "SCENE_BREAKDOWN_MICRO_SCENE_COVERAGE_INVALID",
            "Micro-Scenes do not reconstruct the frozen Script section exactly.",
            500,
            retryable=False,
            details={"section_id": section.section_id},
        )
    return scenes


__all__ = ["build_micro_scenes"]
