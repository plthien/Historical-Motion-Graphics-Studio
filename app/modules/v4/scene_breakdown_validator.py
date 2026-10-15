from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from app.api.errors import AppError
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.scene_breakdown_anchor_evidence import (
    build_anchor_assignment_spans,
    refs_for_absolute_span,
)
from app.modules.v4.scene_breakdown_input_builder import ExtractedSection
from app.modules.v4.scene_breakdown_timing import (
    TIMING_POLICY_VERSION,
    apply_approved_voice_durations,
    count_narration_words,
    evaluate_timing_diagnostics,
)

EMPHASIS_CAP_MARKER_TYPES = frozenset(
    {"turning_point", "reveal", "payoff", "emphasis"}
)
_TOKEN_RE = re.compile(r"\S+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class _SourceToken:
    text: str
    start: int
    end: int


@dataclass(slots=True)
class SceneBreakdownValidationError(Exception):
    failure_kind: str
    report: dict[str, Any]
    raw_output: dict[str, Any] | None

    def __str__(self) -> str:
        return f"Scene Breakdown section validation failed: {self.failure_kind}"


def empty_report() -> dict[str, Any]:
    return {
        "coverage_status": "passed",
        "semantic_partition_status": "passed",
        "order_status": "passed",
        "source_integrity_status": "passed",
        "timing_status": "passed",
        "forbidden_field_status": "passed",
        "reasoning_leakage_status": "passed",
        "semantic_errors": [],
        "errors": [],
        "coverage_gaps": [],
        "coverage_overlaps": [],
        "timing_warnings": [],
        "warnings": [],
        "timing_policy_version": None,
        "timing_metrics": None,
    }


def _issue(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _failure(
    report: dict[str, Any],
    kind: str,
    raw: dict[str, Any] | None,
) -> SceneBreakdownValidationError:
    if report.get("semantic_errors") and not report.get("errors"):
        report["errors"] = deepcopy(report["semantic_errors"])
    return SceneBreakdownValidationError(kind, report, deepcopy(raw))


def _schema_issues(exc: AppError) -> list[dict[str, str]]:
    errors = exc.details.get("errors") if isinstance(exc.details, dict) else None
    result: list[dict[str, str]] = []
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, dict):
                continue
            result.append(
                _issue(
                    str(item.get("path") or "$"),
                    "raw_schema_invalid",
                    str(item.get("message") or "Raw output failed schema validation."),
                )
            )
    return result or [_issue("$", "raw_schema_invalid", "Raw output failed schema validation.")]


def beat_json_path(scene_index: int, beat_index: int, field: str | None = None) -> str:
    if scene_index < 0 or beat_index < 0:
        raise ValueError("JSON path indexes must be non-negative")
    path = f"$.scenes[{scene_index}].beats[{beat_index}]"
    return f"{path}.{field}" if field else path


def scene_index_from_json_path(path: str) -> int | None:
    match = re.match(r"^\$\.scenes\[(\d+)\](?:$|\.beats\[\d+\](?:$|\.[A-Za-z_][A-Za-z0-9_]*)$)", path)
    if not match:
        return None
    return int(match.group(1))


def _tokens_with_spans(text: str) -> list[_SourceToken]:
    return [_SourceToken(match.group(0), match.start(), match.end()) for match in _TOKEN_RE.finditer(text)]


def _token_texts(text: str) -> list[str]:
    return [match.group(0) for match in _TOKEN_RE.finditer(text)]


def _overlap(left: list[int] | tuple[int, int], right: list[int] | tuple[int, int]) -> bool:
    return int(left[0]) < int(right[1]) and int(left[1]) > int(right[0])


def _contains(outer: list[int], inner: list[int]) -> bool:
    return int(outer[0]) <= int(inner[0]) and int(outer[1]) >= int(inner[1])


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _visual_context_ranges(
    *,
    script_analysis: dict[str, Any],
    section: ExtractedSection,
) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    catalog = script_analysis.get("visual_context_catalog")
    if not isinstance(catalog, list):
        return ranges
    section_start, section_end = section.resolved_script_span
    for context in catalog:
        if not isinstance(context, dict):
            continue
        context_id = context.get("context_id")
        if not isinstance(context_id, str) or not context_id:
            continue
        presence_ranges = context.get("presence_ranges")
        if not isinstance(presence_ranges, list):
            continue
        for presence_range in presence_ranges:
            if (
                not isinstance(presence_range, dict)
                or presence_range.get("section_id") != section.section_id
            ):
                continue
            span = presence_range.get("resolved_script_span")
            if not isinstance(span, list) or len(span) != 2:
                continue
            start, end = int(span[0]), int(span[1])
            start, end = max(start, section_start), min(end, section_end)
            if start < end:
                ranges.append((start, end, context_id))
    return sorted(ranges)


def _resolve_visual_context_ref(
    *,
    absolute_span: list[int],
    script_analysis: dict[str, Any],
    section: ExtractedSection,
    path: str,
    report: dict[str, Any],
    locked_ref: Any = None,
) -> str:
    ranges = _visual_context_ranges(script_analysis=script_analysis, section=section)
    if not ranges:
        report["source_integrity_status"] = "failed"
        report["semantic_errors"].append(
            _issue(
                f"{path}.visual_context_ref",
                "visual_context_authority_missing",
                "Every Scene Breakdown Beat requires canonical visual-context authority.",
            )
        )
        raise _failure(report, "source_integrity_failed", None)
    start, end = absolute_span
    local_start = start - section.resolved_script_span[0]
    local_end = end - section.resolved_script_span[0]
    refs: set[str] = set()
    for offset, character in enumerate(section.script_excerpt[local_start:local_end]):
        if character.isspace():
            continue
        absolute = start + offset
        owners = {
            context_id
            for range_start, range_end, context_id in ranges
            if range_start <= absolute < range_end
        }
        if len(owners) != 1:
            report["source_integrity_status"] = "failed"
            report["semantic_errors"].append(
                _issue(
                    f"{path}.visual_context_ref",
                    "visual_context_authority_invalid",
                    "Every non-whitespace Beat source character must have exactly one visual context.",
                )
            )
            raise _failure(report, "source_integrity_failed", None)
        refs.update(owners)
    if len(refs) != 1:
        report["source_integrity_status"] = "failed"
        report["semantic_errors"].append(
            _issue(
                f"{path}.visual_context_ref",
                "visual_context_boundary_crossed",
                "A Scene Breakdown Beat may not cross visual-context authority.",
            )
        )
        raise _failure(report, "source_integrity_failed", None)
    resolved_ref = next(iter(refs))
    if locked_ref is not None and locked_ref != resolved_ref:
        report["source_integrity_status"] = "failed"
        report["semantic_errors"].append(
            _issue(
                f"{path}.visual_context_ref",
                "visual_context_ref_mismatch",
                "The locked Beat visual-context reference no longer matches canonical Script Analysis authority.",
            )
        )
        raise _failure(report, "source_integrity_failed", None)
    return resolved_ref


def _section_markers(
    *,
    script_analysis: dict[str, Any],
    section: ExtractedSection,
) -> list[dict[str, Any]]:
    start, end = section.resolved_script_span
    result: list[dict[str, Any]] = []
    for marker in script_analysis.get("narrative_markers", []):
        if not isinstance(marker, dict) or marker.get("section_id") != section.section_id:
            continue
        span = marker.get("resolved_script_span")
        if not isinstance(span, list) or len(span) != 2:
            continue
        marker_start = int(span[0])
        marker_end = int(span[1])
        if marker_start < start or marker_end > end or marker_start >= marker_end:
            continue
        result.append(
            {
                "marker_id": str(marker["marker_id"]),
                "marker_type": str(marker["marker_type"]),
                "importance": str(marker.get("importance") or "medium"),
                "resolved_script_span": [marker_start, marker_end],
            }
        )
    return result


def _markers_for_span(
    markers: list[dict[str, Any]],
    span: list[int],
) -> tuple[list[str], list[str], bool, bool, bool]:
    overlapping = [marker for marker in markers if _overlap(span, marker["resolved_script_span"])]
    marker_refs = [str(marker["marker_id"]) for marker in overlapping]
    marker_types = _ordered_unique([str(marker["marker_type"]) for marker in overlapping])
    return (
        marker_refs,
        marker_types,
        "turning_point" in marker_types,
        "reveal" in marker_types,
        "contrast" in marker_types,
    )


def _priority(marker_types: list[str]) -> str:
    if set(marker_types) & EMPHASIS_CAP_MARKER_TYPES:
        return "critical"
    if marker_types:
        return "high"
    return "normal"


def _eligibility_evidence(
    *,
    style_bible: dict[str, Any],
    section: ExtractedSection,
) -> dict[str, dict[str, list[list[int]]]]:
    section_start = section.resolved_script_span[0]
    local = build_anchor_assignment_spans(
        style_bible=style_bible,
        section_id=section.section_id,
        section_excerpt=section.script_excerpt,
        section_absolute_start=section_start,
    )
    return {
        category: {
            anchor_id: [
                [section_start + int(span[0]), section_start + int(span[1])]
                for span in spans
            ]
            for anchor_id, spans in by_id.items()
        }
        for category, by_id in local.items()
    }


def _source_partition_failure(
    *,
    report: dict[str, Any],
    raw: dict[str, Any],
    path: str,
    code: str,
    message: str,
) -> None:
    report["coverage_status"] = "failed"
    report["semantic_errors"].append(_issue(path, code, message))
    raise _failure(report, "source_partition_invalid", raw)


def _reused_fragment_failure(
    *,
    report: dict[str, Any],
    kind: str,
    path: str,
    code: str,
    message: str,
    status_key: str,
) -> None:
    report[status_key] = "failed"
    report["semantic_errors"].append(_issue(path, code, message))
    raise _failure(report, kind, None)



def _visual_selected_sets(visual: dict[str, Any] | None) -> dict[str, set[str]]:
    value = visual if isinstance(visual, dict) else {}
    return {
        "characters": {str(item) for item in value.get("selected_character_refs", [])},
        "locations": {str(item) for item in value.get("selected_location_refs", [])},
        "props": {str(item) for item in value.get("selected_prop_refs", [])},
        "subjects": {str(item) for item in value.get("selected_recurring_visual_subject_refs", [])},
        "motifs": {str(item) for item in value.get("selected_motif_refs", [])},
    }


def _validate_visual_decision(
    *,
    visual: Any,
    materialized_beat: dict[str, Any],
    section_input: dict[str, Any],
    scene_index: int,
    beat_index: int,
    previous_visual: dict[str, Any] | None,
    report: dict[str, Any],
    raw: dict[str, Any] | None,
) -> dict[str, Any]:
    path = beat_json_path(scene_index, beat_index, "visual")
    if not isinstance(visual, dict):
        report["semantic_partition_status"] = "failed"
        report["semantic_errors"].append(_issue(path, "visual_missing", "Every beat must contain one complete visual object."))
        raise _failure(report, "visual_validation_failed", raw)

    relation = visual.get("continuity_with_previous")
    if previous_visual is None and relation != "new_setup":
        report["semantic_partition_status"] = "failed"
        report["semantic_errors"].append(
            _issue(
                f"{path}.continuity_with_previous",
                "plan_first_beat_requires_new_setup",
                "Only the first Beat of the whole Scene Plan must use new_setup; Scene boundaries do not imply a reset.",
            )
        )
        raise _failure(report, "visual_validation_failed", raw)

    catalog = section_input["visual_context"]["reference_catalog"]
    selected = _visual_selected_sets(visual)
    previous = _visual_selected_sets(previous_visual)
    eligible = {
        "characters": set(materialized_beat["eligible_character_refs"]),
        "locations": set(materialized_beat["eligible_location_refs"]),
        "props": set(materialized_beat["eligible_prop_refs"]),
        "subjects": set(materialized_beat["eligible_recurring_visual_subject_refs"]),
        "motifs": set(materialized_beat["eligible_motif_refs"]),
    }
    field_by_category = {
        "characters": "selected_character_refs",
        "locations": "selected_location_refs",
        "props": "selected_prop_refs",
        "subjects": "selected_recurring_visual_subject_refs",
        "motifs": "selected_motif_refs",
    }
    carry_by_relation = {
        "same_setup": {"characters", "locations", "props", "subjects", "motifs"},
        "match_subject": {"characters", "props", "subjects"},
        "match_location": {"locations"},
        "match_composition": set(),
        "graphic_sequence": set(),
    }
    carry_categories = carry_by_relation.get(str(relation), set()) if previous_visual is not None else set()
    issues: list[dict[str, str]] = []
    for category, values in selected.items():
        known = set(catalog.get(category, {}))
        for identity in values:
            field_path = f"{path}.{field_by_category[category]}"
            if identity not in known:
                issues.append(_issue(field_path, "visual_reference_unknown", "Selected reference ID is not present in the supplied reference catalog category."))
            elif identity not in eligible[category] and not (category in carry_categories and identity in previous[category]):
                issues.append(_issue(field_path, "visual_reference_unsupported", "Selected reference is not supported by direct Script evidence, an authoritative canonical presence range, or immediate continuity."))

    for field in ("lighting_direction", "color_emphasis"):
        value = visual.get(field)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            generic = {
                "use approved lighting",
                "use readable lighting",
                "use the approved palette",
                "use approved palette",
                "use project colors",
                "use the approved style",
            }
            if normalized in generic:
                issues.append(_issue(f"{path}.{field}", "generic_creative_override_forbidden", "Beat-level lighting and color must be specific or null, not a generic fallback."))
    if issues:
        report["semantic_partition_status"] = "failed"
        report["semantic_errors"].extend(issues)
        raise _failure(report, "visual_validation_failed", raw)
    return deepcopy(visual)

def _materialize_from_raw(
    *,
    raw: dict[str, Any],
    section: ExtractedSection,
    markers: list[dict[str, Any]],
    evidence: dict[str, dict[str, list[list[int]]]],
    section_input: dict[str, Any],
    report: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> list[dict[str, Any]]:
    section_start, section_end = section.resolved_script_span
    if section_end - section_start != len(section.script_excerpt):
        report["source_integrity_status"] = "failed"
        raise _failure(report, "source_integrity_failed", raw)

    source_tokens = _tokens_with_spans(section.script_excerpt)
    if not source_tokens:
        report["source_integrity_status"] = "failed"
        report["semantic_errors"].append(_issue("$.section_context.script_text", "empty_source", "Section source text contains no words."))
        raise _failure(report, "source_integrity_failed", raw)

    token_cursor = 0
    previous_local_end = 0
    normalized_scenes: list[dict[str, Any]] = []
    last_beat: dict[str, Any] | None = None
    previous_visual = section_input.get("visual_context", {}).get("previous_visual_tail")
    if not isinstance(previous_visual, dict):
        previous_visual = None

    for scene_index, raw_scene in enumerate(raw["scenes"], start=1):
        raw_beats = raw_scene.get("beats")
        if not isinstance(raw_beats, list) or not raw_beats:
            _source_partition_failure(
                report=report,
                raw=raw,
                path=f"$.scenes[{scene_index - 1}].beats",
                code="empty_scene",
                message="Scene contains no beats.",
            )
        scene_start = previous_local_end
        materialized_beats: list[dict[str, Any]] = []

        for beat_index, raw_beat in enumerate(raw_beats, start=1):
            path = beat_json_path(scene_index - 1, beat_index - 1, "script_text")
            beat_text = str(raw_beat.get("script_text") or "")
            beat_tokens = _token_texts(beat_text)
            if not beat_tokens:
                _source_partition_failure(
                    report=report,
                    raw=raw,
                    path=path,
                    code="empty_beat",
                    message="Beat script_text contains no source words.",
                )
            if token_cursor + len(beat_tokens) > len(source_tokens):
                _source_partition_failure(
                    report=report,
                    raw=raw,
                    path=path,
                    code="script_text_added",
                    message="Beat script_text contains text beyond the section source.",
                )
            for offset, beat_token in enumerate(beat_tokens):
                source_token = source_tokens[token_cursor + offset]
                if source_token.text != beat_token:
                    _source_partition_failure(
                        report=report,
                        raw=raw,
                        path=path,
                        code="script_text_mismatch",
                        message="Beat script_text must match the next source tokens exactly; only whitespace differences are allowed.",
                    )

            local_start = previous_local_end
            local_end = source_tokens[token_cursor + len(beat_tokens) - 1].end
            absolute_span = [section_start + local_start, section_start + local_end]
            quote = section.script_excerpt[local_start:local_end]
            marker_refs, marker_types, turning_point, reveal, contrast = _markers_for_span(
                markers,
                absolute_span,
            )
            refs = refs_for_absolute_span(evidence=evidence, absolute_span=absolute_span)
            materialized = {
                "local_beat_order": beat_index,
                "script_quote": quote,
                "resolved_script_span": absolute_span,
                "word_count": count_narration_words(quote),
                "estimated_duration_seconds": 0.0001,
                "narrative_marker_refs": marker_refs,
                "narrative_marker_types": marker_types,
                "is_narrative_marker": bool(marker_refs),
                "turning_point": turning_point,
                "reveal": reveal,
                "contrast": contrast,
                "visual_priority": _priority(marker_types),
                "eligible_character_refs": refs["characters"],
                "eligible_location_refs": refs["locations"],
                "eligible_prop_refs": refs["recurring_props"],
                "eligible_recurring_visual_subject_refs": refs["recurring_visual_subjects"],
                "eligible_motif_refs": refs["motifs"],
                "pacing_density": section.pacing_density,
            }
            normalized_visual = _validate_visual_decision(
                visual=raw_beat.get("visual"),
                materialized_beat=materialized,
                section_input=section_input,
                scene_index=scene_index - 1,
                beat_index=beat_index - 1,
                previous_visual=previous_visual,
                report=report,
                raw=raw,
            )
            materialized["visual"] = normalized_visual
            materialized_beats.append(materialized)
            previous_visual = normalized_visual
            last_beat = materialized
            token_cursor += len(beat_tokens)
            previous_local_end = local_end

        scene_end = previous_local_end
        normalized_scenes.append(
            {
                "local_scene_order": scene_index,
                "resolved_script_span": [section_start + scene_start, section_start + scene_end],
                "start_quote": section.script_excerpt[scene_start : materialized_beats[0]["resolved_script_span"][1] - section_start],
                "end_quote": materialized_beats[-1]["script_quote"],
                "beats": materialized_beats,
            }
        )

    if token_cursor != len(source_tokens):
        next_token = source_tokens[token_cursor]
        report["coverage_status"] = "failed"
        report["semantic_errors"].append(
            _issue(
                "$.scenes",
                "section_not_fully_covered",
                "Returned beat script_text values do not cover the full section source.",
            )
        )
        report["coverage_gaps"].append(
            {
                "section_id": section.section_id,
                "span": [section_start + next_token.start, section_end],
                "text": section.script_excerpt[next_token.start:],
            }
        )
        raise _failure(report, "source_partition_invalid", raw)

    if last_beat is not None and previous_local_end < len(section.script_excerpt):
        trailing_start = last_beat["resolved_script_span"][0] - section_start
        last_beat["resolved_script_span"][1] = section_end
        last_beat["script_quote"] = section.script_excerpt[trailing_start:]
        last_beat["word_count"] = count_narration_words(last_beat["script_quote"])
        last_beat["estimated_duration_seconds"] = 0.0001
        normalized_scenes[-1]["resolved_script_span"][1] = section_end
        normalized_scenes[-1]["end_quote"] = last_beat["script_quote"]
        previous_local_end = len(section.script_excerpt)

    if previous_local_end != len(section.script_excerpt):
        report["coverage_status"] = "failed"
        raise _failure(report, "source_partition_invalid", raw)

    return apply_approved_voice_durations(scenes=normalized_scenes, word_timing_map=word_timing_map)


def _collect_timing_diagnostics(
    *,
    normalized_scenes: list[dict[str, Any]],
    word_timing_map: dict[str, Any],
    planned_narration_wpm: int,
    visual_cadence_guidance: dict[str, Any],
    report: dict[str, Any],
) -> None:
    timing = evaluate_timing_diagnostics(
        scenes=normalized_scenes,
        word_timing_map=word_timing_map,
        planned_narration_wpm=planned_narration_wpm,
        visual_cadence_guidance=visual_cadence_guidance,
    )
    report["timing_policy_version"] = TIMING_POLICY_VERSION
    report["timing_metrics"] = deepcopy(timing.metrics)
    report["timing_status"] = timing.status
    report["timing_warnings"].extend(timing.warnings)
    if report["timing_warnings"]:
        report["warnings"].append("Non-critical Scene Breakdown timing warnings are present.")

def validate_and_normalize_section(
    *,
    raw_output: Any,
    section: ExtractedSection,
    section_input: dict[str, Any],
    script_analysis: dict[str, Any] | None = None,
    style_bible: dict[str, Any] | None = None,
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    report = empty_report()
    if not isinstance(raw_output, dict):
        report["semantic_partition_status"] = "failed"
        raise _failure(report, "raw_schema_invalid", None)
    try:
        raw = V4ContractValidator().validate(
            "scene_breakdown_section_llm_output_v1.schema.json",
            deepcopy(raw_output),
            error_code="SCENE_BREAKDOWN_RAW_OUTPUT_INVALID",
        )
    except AppError as exc:
        report["semantic_partition_status"] = "failed"
        report["semantic_errors"].extend(_schema_issues(exc))
        raise _failure(report, "raw_schema_invalid", raw_output) from exc

    section_context = section_input.get("section_context") if isinstance(section_input, dict) else None
    if not isinstance(section_context, dict) or section_context.get("section_id") != section.section_id or not isinstance(section_input.get("visual_cadence_guidance"), dict):
        report["source_integrity_status"] = "failed"
        report["semantic_errors"].append(_issue("$.section_context", "scene_input_source_mismatch", "Scene Breakdown section identity or resolved visual cadence guidance no longer matches the authoritative input."))
        raise _failure(report, "source_integrity_failed", raw)

    markers = _section_markers(script_analysis=script_analysis or {}, section=section)
    evidence = _eligibility_evidence(
        style_bible=style_bible or {"status": "text_approved"},
        section=section,
    )
    normalized_scenes = _materialize_from_raw(
        raw=raw,
        section=section,
        markers=markers,
        evidence=evidence,
        section_input=section_input,
        report=report,
        word_timing_map=word_timing_map,
    )
    for scene_index, scene in enumerate(normalized_scenes):
        for beat_index, beat in enumerate(scene["beats"]):
            context_ref = _resolve_visual_context_ref(
                absolute_span=beat["resolved_script_span"],
                script_analysis=script_analysis or {},
                section=section,
                path=beat_json_path(scene_index, beat_index),
                report=report,
            )
            beat["visual_context_ref"] = context_ref
    _collect_timing_diagnostics(
        normalized_scenes=normalized_scenes,
        word_timing_map=word_timing_map,
        planned_narration_wpm=section.planned_narration_wpm,
        visual_cadence_guidance=section_input["visual_cadence_guidance"],
        report=report,
    )

    return {
        "section_id": section.section_id,
        "section_order": section.section_order,
        "section_title": section.section_title,
        "section_role": section.section_role,
        "resolved_script_span": list(section.resolved_script_span),
        "pacing_density": section.pacing_density,
        "scenes": normalized_scenes,
        "backend_qc": report,
    }


def validate_reusable_section_fragment(
    *,
    fragment: dict[str, Any],
    section: ExtractedSection,
    section_input: dict[str, Any],
    script_analysis: dict[str, Any] | None = None,
    style_bible: dict[str, Any] | None = None,
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    report = empty_report()
    section_context = section_input.get("section_context") if isinstance(section_input, dict) else None
    if not isinstance(section_context, dict) or section_context.get("section_id") != section.section_id or not isinstance(section_input.get("visual_cadence_guidance"), dict):
        _reused_fragment_failure(
            report=report,
            kind="source_integrity_failed",
            path="$.section_context",
            code="scene_input_source_mismatch",
            message="Reusable Scene Breakdown section identity or resolved visual cadence guidance no longer matches the authoritative input.",
            status_key="source_integrity_status",
        )
    if (
        not isinstance(fragment, dict)
        or fragment.get("section_id") != section.section_id
        or fragment.get("section_order") != section.section_order
        or fragment.get("resolved_script_span") != list(section.resolved_script_span)
        or not isinstance(fragment.get("scenes"), list)
        or not fragment["scenes"]
    ):
        _reused_fragment_failure(
            report=report,
            kind="source_integrity_failed",
            path="$",
            code="fragment_identity_mismatch",
            message="Reusable Scene Breakdown fragment identity no longer matches the authoritative section.",
            status_key="source_integrity_status",
        )

    section_start, section_end = section.resolved_script_span
    expected_absolute_start = section_start
    markers = _section_markers(script_analysis=script_analysis or {}, section=section)
    evidence = _eligibility_evidence(
        style_bible=style_bible or {"status": "text_approved"},
        section=section,
    )
    rebuilt_scenes: list[dict[str, Any]] = []
    previous_visual = section_input.get("visual_context", {}).get("previous_visual_tail")
    if not isinstance(previous_visual, dict):
        previous_visual = None

    for scene_index, scene in enumerate(fragment["scenes"], start=1):
        if not isinstance(scene, dict) or not isinstance(scene.get("beats"), list) or not scene["beats"]:
            _reused_fragment_failure(
                report=report,
                kind="source_integrity_failed",
                path=f"$.scenes[{scene_index - 1}].beats",
                code="fragment_scene_invalid",
                message="Reusable Scene Breakdown scene is missing beats.",
                status_key="source_integrity_status",
            )
        if scene.get("local_scene_order") != scene_index:
            _reused_fragment_failure(
                report=report,
                kind="source_integrity_failed",
                path=f"$.scenes[{scene_index - 1}].local_scene_order",
                code="fragment_scene_order_invalid",
                message="Reusable Scene Breakdown scene order is invalid.",
                status_key="order_status",
            )

        rebuilt_beats: list[dict[str, Any]] = []
        for beat_index, beat in enumerate(scene["beats"], start=1):
            path = beat_json_path(scene_index - 1, beat_index - 1)
            if not isinstance(beat, dict) or beat.get("local_beat_order") != beat_index:
                _reused_fragment_failure(
                    report=report,
                    kind="source_integrity_failed",
                    path=f"{path}.local_beat_order",
                    code="fragment_beat_order_invalid",
                    message="Reusable Scene Breakdown beat order is invalid.",
                    status_key="order_status",
                )
            span = beat.get("resolved_script_span")
            if (
                not isinstance(span, list)
                or len(span) != 2
            ):
                _reused_fragment_failure(
                    report=report,
                    kind="source_integrity_failed",
                    path=f"{path}.resolved_script_span",
                    code="fragment_span_invalid",
                    message="Reusable Scene Breakdown beat span is invalid.",
                    status_key="source_integrity_status",
                )
            try:
                absolute_start = int(span[0])
                absolute_end = int(span[1])
            except (TypeError, ValueError) as exc:
                _reused_fragment_failure(
                    report=report,
                    kind="source_integrity_failed",
                    path=f"{path}.resolved_script_span",
                    code="fragment_span_invalid",
                    message="Reusable Scene Breakdown beat span must contain integers.",
                    status_key="source_integrity_status",
                )
                raise AssertionError("unreachable") from exc
            if absolute_end <= absolute_start or absolute_start < section_start or absolute_end > section_end:
                _reused_fragment_failure(
                    report=report,
                    kind="source_integrity_failed",
                    path=f"{path}.resolved_script_span",
                    code="fragment_span_out_of_bounds",
                    message="Reusable Scene Breakdown beat span is outside the authoritative section source.",
                    status_key="source_integrity_status",
                )
            if absolute_start != expected_absolute_start:
                if absolute_start > expected_absolute_start:
                    report["coverage_gaps"].append(
                        {
                            "section_id": section.section_id,
                            "span": [expected_absolute_start, absolute_start],
                            "text": section.script_excerpt[
                                expected_absolute_start - section_start : absolute_start - section_start
                            ],
                        }
                    )
                    code = "fragment_gap"
                else:
                    report["coverage_overlaps"].append(
                        {
                            "section_id": section.section_id,
                            "span": [absolute_start, expected_absolute_start],
                            "text": section.script_excerpt[
                                absolute_start - section_start : expected_absolute_start - section_start
                            ],
                        }
                    )
                    code = "fragment_overlap"
                _reused_fragment_failure(
                    report=report,
                    kind="source_partition_invalid",
                    path=f"{path}.resolved_script_span",
                    code=code,
                    message="Reusable Scene Breakdown beat spans must form one complete contiguous section partition.",
                    status_key="coverage_status",
                )
            quote = section.script_excerpt[absolute_start - section_start : absolute_end - section_start]
            stored_quote = beat.get("script_quote")
            if not isinstance(stored_quote, str) or stored_quote != quote:
                _reused_fragment_failure(
                    report=report,
                    kind="source_integrity_failed",
                    path=f"{path}.script_quote",
                    code="fragment_quote_mismatch",
                    message="Reusable Scene Breakdown beat quote no longer matches the authoritative section source.",
                    status_key="source_integrity_status",
                )
            marker_refs, marker_types, turning_point, reveal, contrast = _markers_for_span(
                markers,
                [absolute_start, absolute_end],
            )
            refs = refs_for_absolute_span(evidence=evidence, absolute_span=[absolute_start, absolute_end])
            rebuilt = {
                "local_beat_order": beat_index,
                "script_quote": quote,
                "resolved_script_span": [absolute_start, absolute_end],
                "word_count": count_narration_words(quote),
                "estimated_duration_seconds": 0.0001,
                "narrative_marker_refs": marker_refs,
                "narrative_marker_types": marker_types,
                "is_narrative_marker": bool(marker_refs),
                "turning_point": turning_point,
                "reveal": reveal,
                "contrast": contrast,
                "visual_priority": _priority(marker_types),
                "eligible_character_refs": refs["characters"],
                "eligible_location_refs": refs["locations"],
                "eligible_prop_refs": refs["recurring_props"],
                "eligible_recurring_visual_subject_refs": refs["recurring_visual_subjects"],
                "eligible_motif_refs": refs["motifs"],
                "pacing_density": section.pacing_density,
            }
            if not isinstance(beat.get("visual_context_ref"), str):
                _reused_fragment_failure(
                    report=report,
                    kind="source_integrity_failed",
                    path=f"{path}.visual_context_ref",
                    code="visual_context_ref_missing",
                    message="Reusable Scene Breakdown Beats require their locked visual_context_ref.",
                    status_key="source_integrity_status",
                )
            context_ref = _resolve_visual_context_ref(
                absolute_span=[absolute_start, absolute_end],
                script_analysis=script_analysis or {},
                section=section,
                path=path,
                report=report,
                locked_ref=beat.get("visual_context_ref"),
            )
            rebuilt["visual_context_ref"] = context_ref
            normalized_visual = _validate_visual_decision(
                visual=beat.get("visual"),
                materialized_beat=rebuilt,
                section_input=section_input,
                scene_index=scene_index - 1,
                beat_index=beat_index - 1,
                previous_visual=previous_visual,
                report=report,
                raw=None,
            )
            rebuilt["visual"] = normalized_visual
            rebuilt_beats.append(rebuilt)
            previous_visual = normalized_visual
            expected_absolute_start = absolute_end

        rebuilt_scenes.append(
            {
                "local_scene_order": scene_index,
                "resolved_script_span": [
                    rebuilt_beats[0]["resolved_script_span"][0],
                    rebuilt_beats[-1]["resolved_script_span"][1],
                ],
                "start_quote": rebuilt_beats[0]["script_quote"],
                "end_quote": rebuilt_beats[-1]["script_quote"],
                "beats": rebuilt_beats,
            }
        )

    if expected_absolute_start != section_end:
        report["coverage_gaps"].append(
            {
                "section_id": section.section_id,
                "span": [expected_absolute_start, section_end],
                "text": section.script_excerpt[expected_absolute_start - section_start :],
            }
        )
        _reused_fragment_failure(
            report=report,
            kind="source_partition_invalid",
            path="$.scenes",
            code="fragment_incomplete",
            message="Reusable Scene Breakdown fragment does not cover the full authoritative section source.",
            status_key="coverage_status",
        )

    rebuilt_scenes = apply_approved_voice_durations(scenes=rebuilt_scenes, word_timing_map=word_timing_map)
    _collect_timing_diagnostics(
        normalized_scenes=rebuilt_scenes,
        word_timing_map=word_timing_map,
        planned_narration_wpm=section.planned_narration_wpm,
        visual_cadence_guidance=section_input["visual_cadence_guidance"],
        report=report,
    )
    return {
        "section_id": section.section_id,
        "section_order": section.section_order,
        "section_title": section.section_title,
        "section_role": section.section_role,
        "resolved_script_span": list(section.resolved_script_span),
        "pacing_density": section.pacing_density,
        "scenes": rebuilt_scenes,
        "backend_qc": report,
    }


__all__ = [
    "EMPHASIS_CAP_MARKER_TYPES",
    "SceneBreakdownValidationError",
    "beat_json_path",
    "empty_report",
    "scene_index_from_json_path",
    "validate_and_normalize_section",
    "validate_reusable_section_fragment",
]
