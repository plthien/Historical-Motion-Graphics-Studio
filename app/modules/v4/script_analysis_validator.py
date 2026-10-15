from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from app.api.errors import AppError
from app.modules.v4.script_analysis_quote_resolver import (
    QuoteRecoveryEvent,
    SCRIPT_ANALYSIS_CONTEXT_QUOTE_RECOVERED_WARNING_CODE,
    SCRIPT_ANALYSIS_SHORT_QUOTE_RECOVERED_WARNING_CODE,
    ScriptAnalysisQuoteResolver,
)


_CATEGORIES = (
    ("characters", "char", "char_id"),
    ("locations", "loc", "loc_id"),
    ("recurring_props", "prop", "prop_id"),
    ("recurring_visual_subjects", "subject", "subject_id"),
    ("visual_motifs", "motif", "motif_id"),
)


class ScriptAnalysisSemanticValidator:
    def __init__(self) -> None:
        self.quote_resolver = ScriptAnalysisQuoteResolver()

    def validate_raw_identity_and_sections(
        self,
        *,
        llm_input: dict[str, Any],
        raw_output: dict[str, Any],
    ) -> None:
        for field in ("project_id", "source_script_revision_id"):
            if raw_output.get(field) != llm_input.get(field):
                raise AppError(
                    "SCRIPT_ANALYSIS_IDENTITY_MISMATCH",
                    "Script Analysis provider output identity does not match its request.",
                    422,
                    retryable=False,
                    details={"field": field, "expected": llm_input.get(field), "actual": raw_output.get(field)},
                )
        expected = [item["section_id"] for item in llm_input["script"]["sections"]]
        actual = [item["section_id"] for item in raw_output["section_guidance"]]
        if actual != expected:
            raise AppError(
                "SCRIPT_ANALYSIS_SECTION_GUIDANCE_INVALID",
                "section_guidance must contain every Script section exactly once and in Script order.",
                422,
                retryable=False,
                details={"expected_section_ids": expected, "actual_section_ids": actual},
            )

    def build_canonical(
        self,
        *,
        llm_input: dict[str, Any],
        raw_output: dict[str, Any],
        section_ranges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.validate_raw_identity_and_sections(llm_input=llm_input, raw_output=raw_output)
        resolved, quote_recovery_events = self.quote_resolver.resolve(
            llm_input=llm_input,
            raw_output=raw_output,
            section_ranges=section_ranges,
        )
        self._validate_and_order_markers(resolved)
        self._validate_continuity(resolved)
        self._validate_visual_contexts(
            resolved,
            llm_input=llm_input,
            section_ranges=section_ranges,
        )
        self._normalize_strings(resolved)
        self._assign_ids(resolved)
        quote_warnings = self._materialize_quote_recovery_warnings(
            resolved,
            quote_recovery_events,
        )
        resolved["artifact_type"] = "script_analysis"
        resolved["backend_qc"] = {
            "section_coverage_status": "passed",
            "quote_resolution_status": "warning" if quote_warnings else "passed",
            "entity_deduplication_status": "passed",
            "marker_validation_status": "passed",
            "visual_context_validation_status": "passed",
            "warnings": quote_warnings,
        }
        return resolved

    def validate_canonical(
        self,
        *,
        llm_input: dict[str, Any],
        canonical: dict[str, Any],
        section_ranges: list[dict[str, Any]],
    ) -> None:
        raw_projection = self._raw_projection(canonical)
        recomputed = self.build_canonical(
            llm_input=llm_input,
            raw_output=raw_projection,
            section_ranges=section_ranges,
        )
        if canonical != recomputed:
            raise AppError(
                "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                "Stored Script Analysis does not match deterministic backend normalization.",
                422,
                retryable=False,
            )

    @staticmethod
    def _validate_and_order_markers(payload: dict[str, Any]) -> None:
        markers = payload["narrative_markers"]
        seen: set[tuple[str, int, int]] = set()
        for marker in markers:
            span = marker["resolved_script_span"]
            key = (marker["marker_type"], int(span[0]), int(span[1]))
            if key in seen:
                raise AppError(
                    "SCRIPT_ANALYSIS_DUPLICATE_MARKER",
                    "Script Analysis contains a duplicate narrative marker.",
                    422,
                    retryable=False,
                    details={"marker_type": marker["marker_type"], "resolved_script_span": span},
                )
            seen.add(key)
        markers.sort(key=lambda item: (item["resolved_script_span"][0], item["resolved_script_span"][1], item["marker_type"]))

    def _validate_continuity(self, payload: dict[str, Any]) -> None:
        catalog = payload["continuity_catalog"]
        cross_category: dict[str, str] = {}
        for category, _, _ in _CATEGORIES:
            category_keys: set[str] = set()
            candidates = catalog[category]
            for index, candidate in enumerate(candidates):
                identity_key = self._identity_key(candidate.get("label", ""))
                if not identity_key:
                    raise AppError(
                        "SCRIPT_ANALYSIS_CONTINUITY_IDENTITY_INVALID",
                        "Continuity candidates require a non-empty normalized label.",
                        422,
                        retryable=False,
                        details={"category": category, "candidate_index": index},
                    )
                if identity_key in category_keys:
                    raise AppError(
                        "SCRIPT_ANALYSIS_DUPLICATE_CONTINUITY_CANDIDATE",
                        "Duplicate continuity candidates are not allowed within one category.",
                        422,
                        retryable=False,
                        details={"category": category, "label": candidate.get("label")},
                    )
                category_keys.add(identity_key)
                prior_category = cross_category.get(identity_key)
                if prior_category is not None:
                    raise AppError(
                        "SCRIPT_ANALYSIS_CROSS_CATEGORY_DUPLICATE",
                        "The same continuity identity must not be emitted in multiple categories.",
                        422,
                        retryable=False,
                        details={"label": candidate.get("label"), "categories": [prior_category, category]},
                    )
                cross_category[identity_key] = category

                positions = [tuple(item["resolved_script_span"]) for item in candidate["occurrences"]]
                if len(set(positions)) < 2:
                    raise AppError(
                        "SCRIPT_ANALYSIS_INSUFFICIENT_DISTINCT_OCCURRENCES",
                        "Recurring continuity candidates require at least two distinct source occurrences.",
                        422,
                        retryable=False,
                        details={"category": category, "label": candidate.get("label")},
                    )
                if positions != sorted(positions):
                    raise AppError(
                        "SCRIPT_ANALYSIS_OCCURRENCE_ORDER_INVALID",
                        "Continuity occurrences must be ordered by Script position.",
                        422,
                        retryable=False,
                        details={"category": category, "label": candidate.get("label")},
                    )

                if category != "visual_motifs":
                    ranges = candidate.get("presence_ranges")
                    if not isinstance(ranges, list) or not ranges:
                        raise AppError(
                            "SCRIPT_ANALYSIS_PRESENCE_RANGES_REQUIRED",
                            "Canonical continuity entities require at least one authoritative presence range.",
                            422,
                            retryable=False,
                            details={"category": category, "label": candidate.get("label")},
                        )
                    resolved_ranges = [tuple(item["resolved_script_span"]) for item in ranges]
                    if resolved_ranges != sorted(resolved_ranges):
                        raise AppError(
                            "SCRIPT_ANALYSIS_PRESENCE_RANGE_ORDER_INVALID",
                            "Presence ranges must be ordered by Script position.",
                            422,
                            retryable=False,
                            details={"category": category, "label": candidate.get("label")},
                        )
                    previous_end: int | None = None
                    seen_ranges: set[tuple[int, int]] = set()
                    for range_index, (range_start, range_end) in enumerate(resolved_ranges):
                        if range_start >= range_end:
                            raise AppError(
                                "SCRIPT_ANALYSIS_PRESENCE_RANGE_ORDER_INVALID",
                                "Presence ranges must have a positive resolved span.",
                                422,
                                retryable=False,
                                details={"category": category, "label": candidate.get("label"), "range_index": range_index},
                            )
                        if (range_start, range_end) in seen_ranges:
                            raise AppError(
                                "SCRIPT_ANALYSIS_DUPLICATE_PRESENCE_RANGE",
                                "Duplicate presence ranges are not allowed for one canonical entity.",
                                422,
                                retryable=False,
                                details={"category": category, "label": candidate.get("label"), "range_index": range_index},
                            )
                        if previous_end is not None and range_start < previous_end:
                            raise AppError(
                                "SCRIPT_ANALYSIS_PRESENCE_RANGE_OVERLAP",
                                "Presence ranges for one canonical entity must not overlap; merge continuous scope or split only at a real narrative boundary.",
                                422,
                                retryable=False,
                                details={"category": category, "label": candidate.get("label"), "range_index": range_index},
                            )
                        seen_ranges.add((range_start, range_end))
                        previous_end = range_end

                    for occurrence_index, occurrence_span in enumerate(positions):
                        occurrence_start, occurrence_end = occurrence_span
                        if not any(
                            range_start <= occurrence_start and occurrence_end <= range_end
                            for range_start, range_end in resolved_ranges
                        ):
                            raise AppError(
                                "SCRIPT_ANALYSIS_OCCURRENCE_OUTSIDE_PRESENCE_RANGE",
                                "Every direct continuity occurrence must fall inside an authoritative presence range for the same canonical entity.",
                                422,
                                retryable=False,
                                details={
                                    "category": category,
                                    "label": candidate.get("label"),
                                    "occurrence_index": occurrence_index,
                                    "resolved_script_span": list(occurrence_span),
                                },
                            )
            candidates.sort(
                key=lambda item: (
                    item["occurrences"][0]["resolved_script_span"][0],
                    self._identity_key(item.get("label", "")),
                )
            )

    def _validate_visual_contexts(
        self,
        payload: dict[str, Any],
        *,
        llm_input: dict[str, Any],
        section_ranges: list[dict[str, Any]],
    ) -> None:
        contexts = payload.get("visual_context_catalog")
        if not isinstance(contexts, list) or not contexts:
            raise AppError(
                "SCRIPT_ANALYSIS_VISUAL_CONTEXT_REQUIRED",
                "Script Analysis requires at least one visual context with authoritative source ranges.",
                422,
                retryable=False,
            )

        seen_identities: set[tuple[Any, ...]] = set()
        occupied: list[tuple[int, int, int]] = []
        for context_index, context in enumerate(contexts):
            identity = (
                self._identity_key(context.get("label", "")),
                context.get("mode"),
                self._identity_key(context.get("timeframe") or ""),
                self._identity_key(context.get("setting") or ""),
                tuple(self._identity_key(item) for item in context.get("constraints", [])),
            )
            if identity in seen_identities:
                raise AppError(
                    "SCRIPT_ANALYSIS_VISUAL_CONTEXT_DUPLICATE",
                    "Duplicate normalized visual contexts are not allowed.",
                    422,
                    retryable=False,
                    details={"context_index": context_index, "label": context.get("label")},
                )
            seen_identities.add(identity)

            ranges = context.get("presence_ranges")
            if not isinstance(ranges, list) or not ranges:
                raise AppError(
                    "SCRIPT_ANALYSIS_VISUAL_CONTEXT_REQUIRED",
                    "Every visual context requires at least one authoritative source range.",
                    422,
                    retryable=False,
                    details={"context_index": context_index, "label": context.get("label")},
                )
            resolved_ranges = [tuple(item["resolved_script_span"]) for item in ranges]
            if resolved_ranges != sorted(resolved_ranges):
                raise AppError(
                    "SCRIPT_ANALYSIS_VISUAL_CONTEXT_RANGE_INVALID",
                    "Visual context ranges must be ordered by Script position.",
                    422,
                    retryable=False,
                    details={"context_index": context_index, "label": context.get("label")},
                )
            previous_end: int | None = None
            for range_index, (range_start, range_end) in enumerate(resolved_ranges):
                if range_start >= range_end or (
                    previous_end is not None and range_start < previous_end
                ):
                    raise AppError(
                        "SCRIPT_ANALYSIS_VISUAL_CONTEXT_RANGE_INVALID",
                        "Visual context ranges must be positive, unique, and non-overlapping.",
                        422,
                        retryable=False,
                        details={
                            "context_index": context_index,
                            "range_index": range_index,
                            "resolved_script_span": [range_start, range_end],
                        },
                    )
                previous_end = range_end
                occupied.append((range_start, range_end, context_index))

        occupied.sort()
        for index, (range_start, range_end, context_index) in enumerate(occupied[1:], start=1):
            previous_start, previous_end, previous_context_index = occupied[index - 1]
            if range_start < previous_end and context_index != previous_context_index:
                raise AppError(
                    "SCRIPT_ANALYSIS_VISUAL_CONTEXT_OVERLAP",
                    "A Script source span cannot belong to incompatible visual contexts.",
                    422,
                    retryable=False,
                    details={
                        "context_indexes": [previous_context_index, context_index],
                        "overlap": [range_start, min(previous_end, range_end)],
                        "previous_range_start": previous_start,
                    },
                )

        ranges_by_section = {item["section_id"]: item for item in section_ranges}
        uncovered: list[dict[str, Any]] = []
        multiply_covered: list[dict[str, Any]] = []
        for section in llm_input["script"]["sections"]:
            section_id = section["section_id"]
            section_range = ranges_by_section[section_id]
            section_start = int(section_range["start"])
            narration = str(section["narration"])
            for local_index, character in enumerate(narration):
                if character.isspace():
                    continue
                absolute_index = section_start + local_index
                coverage_count = sum(
                    1
                    for range_start, range_end, _ in occupied
                    if range_start <= absolute_index < range_end
                )
                if coverage_count == 0:
                    uncovered.append({"section_id": section_id, "offset": local_index})
                elif coverage_count > 1:
                    multiply_covered.append({"section_id": section_id, "offset": local_index})
                if len(uncovered) >= 5 or len(multiply_covered) >= 5:
                    break
        if uncovered or multiply_covered:
            raise AppError(
                "SCRIPT_ANALYSIS_VISUAL_CONTEXT_COVERAGE_INVALID",
                "Every non-whitespace character of the frozen Script must belong to exactly one visual context.",
                422,
                retryable=False,
                details={
                    "uncovered_samples": uncovered[:5],
                    "multiply_covered_samples": multiply_covered[:5],
                },
            )

        contexts.sort(
            key=lambda item: (
                item["presence_ranges"][0]["resolved_script_span"][0],
                self._identity_key(item.get("label", "")),
                str(item.get("mode", "")),
            )
        )

    @staticmethod
    def _candidate_id_field(category: str) -> str:
        for candidate_category, _, id_field in _CATEGORIES:
            if candidate_category == category:
                return id_field
        raise AppError(
            "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
            "Stored Script Analysis recovery warning references an unknown continuity category.",
            422,
            retryable=False,
            details={"category": category},
        )

    @staticmethod
    def _find_identity_index(items: list[dict[str, Any]], target: dict[str, Any]) -> int | None:
        for index, item in enumerate(items):
            if item is target:
                return index
        return None

    @classmethod
    def _materialize_quote_recovery_warnings(
        cls,
        payload: dict[str, Any],
        events: list[QuoteRecoveryEvent],
    ) -> list[str]:
        warning_records: list[dict[str, Any]] = []
        for event in events:
            if event.target_type == "narrative_marker":
                target_index = cls._find_identity_index(payload["narrative_markers"], event.target_ref)
                if target_index is None:
                    raise AppError(
                        "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                        "Script Analysis recovery target was lost during marker normalization.",
                        422,
                        retryable=False,
                    )
                target_id = event.target_ref.get("marker_id")
                path = f"$.narrative_markers[{target_index}]"
                extra: dict[str, Any] = {}
            elif event.target_type == "continuity_occurrence" and event.candidate_category is not None:
                candidate_id_field = cls._candidate_id_field(event.candidate_category)
                candidate_index = None
                occurrence_index = None
                candidate_id = None
                for current_candidate_index, candidate in enumerate(
                    payload["continuity_catalog"][event.candidate_category]
                ):
                    for current_occurrence_index, occurrence in enumerate(candidate["occurrences"]):
                        if occurrence is event.target_ref:
                            candidate_index = current_candidate_index
                            occurrence_index = current_occurrence_index
                            candidate_id = candidate.get(candidate_id_field)
                            break
                    if occurrence_index is not None:
                        break
                if candidate_index is None or occurrence_index is None:
                    raise AppError(
                        "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                        "Script Analysis recovery target was lost during continuity normalization.",
                        422,
                        retryable=False,
                    )
                target_id = event.target_ref.get("occurrence_id")
                path = (
                    f"$.continuity_catalog.{event.candidate_category}[{candidate_index}]"
                    f".occurrences[{occurrence_index}]"
                )
                extra = {
                    "candidate_category": event.candidate_category,
                    "candidate_id": candidate_id,
                    "occurrence_index": occurrence_index,
                }
            else:
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Script Analysis recovery event has an invalid target type.",
                    422,
                    retryable=False,
                )
            if not isinstance(target_id, str) or not target_id:
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Script Analysis recovery target is missing a stable backend ID.",
                    422,
                    retryable=False,
                )
            if event.target_type == "continuity_occurrence" and not isinstance(extra.get("candidate_id"), str):
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Script Analysis recovery occurrence is missing a stable candidate ID.",
                    422,
                    retryable=False,
                )
            warning = {
                "code": cls._quote_recovery_warning_code(event),
                "target_type": event.target_type,
                "target_id": target_id,
                "path": path,
                "section_id": event.section_id,
                **extra,
            }
            if event.recovery_direction == "short_from_context":
                if not isinstance(event.original_short_quote, str) or not event.original_short_quote:
                    raise AppError(
                        "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                        "Script Analysis short-quote recovery event is missing original quote metadata.",
                        422,
                        retryable=False,
                    )
                warning["original_short_quote"] = event.original_short_quote
                warning["recovered_short_quote"] = event.recovered_quote
            elif event.recovery_direction == "context_from_short":
                if not isinstance(event.original_context_quote, str) or not event.original_context_quote:
                    raise AppError(
                        "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                        "Script Analysis context-quote recovery event is missing original context metadata.",
                        422,
                        retryable=False,
                    )
                warning["original_context_quote"] = event.original_context_quote
                warning["recovered_context_quote"] = event.recovered_quote
            else:
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Script Analysis recovery event has an invalid recovery direction.",
                    422,
                    retryable=False,
                )
            warning_records.append(warning)
        return [
            json.dumps(
                warning,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for warning in sorted(warning_records, key=cls._quote_recovery_warning_sort_key)
        ]

    @staticmethod
    def _quote_recovery_warning_sort_key(warning: dict[str, Any]) -> tuple[Any, ...]:
        target_type = warning["target_type"]
        if target_type == "narrative_marker":
            return (
                0,
                str(warning["target_id"]),
            )
        return (
            1,
            str(warning.get("candidate_category", "")),
            str(warning.get("candidate_id", "")),
            int(warning.get("occurrence_index", 0)),
            str(warning["target_id"]),
        )

    @staticmethod
    def _quote_recovery_warning_code(event: QuoteRecoveryEvent) -> str:
        if event.recovery_direction == "short_from_context":
            return SCRIPT_ANALYSIS_SHORT_QUOTE_RECOVERED_WARNING_CODE
        if event.recovery_direction == "context_from_short":
            return SCRIPT_ANALYSIS_CONTEXT_QUOTE_RECOVERED_WARNING_CODE
        raise AppError(
            "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
            "Script Analysis recovery event has an invalid recovery direction.",
            422,
            retryable=False,
        )

    @staticmethod
    def _identity_key(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()

    @classmethod
    def _normalize_strings(cls, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and key not in {"anchor_quote", "quote", "context_quote", "start_quote", "end_quote"}:
                    value[key] = child.strip()
                else:
                    cls._normalize_strings(child)
        elif isinstance(value, list):
            for child in value:
                cls._normalize_strings(child)

    @staticmethod
    def _ordered_id(prefix: str, index: int) -> str:
        return f"{prefix}_{index:03d}"

    @classmethod
    def _assign_ids(cls, payload: dict[str, Any]) -> None:
        for index, marker in enumerate(payload["narrative_markers"], start=1):
            marker["marker_id"] = cls._ordered_id("marker", index)

        for category, prefix, id_field in _CATEGORIES:
            occurrence_index = 1
            for candidate_index, candidate in enumerate(payload["continuity_catalog"][category], start=1):
                candidate[id_field] = cls._ordered_id(prefix, candidate_index)
                for occurrence in candidate["occurrences"]:
                    occurrence["occurrence_id"] = cls._ordered_id(f"{prefix}_occ", occurrence_index)
                    occurrence_index += 1

        for index, context in enumerate(payload["visual_context_catalog"], start=1):
            context["context_id"] = cls._ordered_id("ctx", index)

    @staticmethod
    def _raw_projection(canonical: dict[str, Any]) -> dict[str, Any]:
        projected = deepcopy(canonical)
        projected["artifact_type"] = "script_analysis_llm_output"
        qc = projected.get("backend_qc")
        warnings = qc.get("warnings", []) if isinstance(qc, dict) else []
        projected.pop("backend_qc", None)
        ScriptAnalysisSemanticValidator._replay_quote_recovery_warnings(
            projected,
            warnings=warnings,
        )
        for marker in projected["narrative_markers"]:
            marker.pop("marker_id", None)
            marker.pop("resolved_script_span", None)
        for category, _, id_field in _CATEGORIES:
            for candidate in projected["continuity_catalog"][category]:
                candidate.pop(id_field, None)
                for occurrence in candidate["occurrences"]:
                    occurrence.pop("occurrence_id", None)
                    occurrence.pop("resolved_script_span", None)
                if category != "visual_motifs":
                    for presence_range in candidate["presence_ranges"]:
                        presence_range.pop("resolved_script_span", None)
        for context in projected["visual_context_catalog"]:
            context.pop("context_id", None)
            for presence_range in context["presence_ranges"]:
                presence_range.pop("resolved_script_span", None)
        return projected

    @staticmethod
    def _replay_quote_recovery_warnings(
        projected: dict[str, Any],
        *,
        warnings: Any,
    ) -> None:
        if not isinstance(warnings, list):
            return
        for warning in warnings:
            if not isinstance(warning, str):
                continue
            try:
                parsed = json.loads(warning)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            code = parsed.get("code")
            if code not in {
                SCRIPT_ANALYSIS_SHORT_QUOTE_RECOVERED_WARNING_CODE,
                SCRIPT_ANALYSIS_CONTEXT_QUOTE_RECOVERED_WARNING_CODE,
            }:
                continue
            target_type = parsed.get("target_type")
            target_id = parsed.get("target_id")
            section_id = parsed.get("section_id")
            if not all(isinstance(item, str) and item for item in (target_type, target_id, section_id)):
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Stored Script Analysis recovery warning is missing stable target metadata.",
                    422,
                    retryable=False,
                    details={"warning": warning},
                )
            target = ScriptAnalysisSemanticValidator._quote_target_for_warning(
                projected,
                target_type=target_type,
                target_id=target_id,
                candidate_category=parsed.get("candidate_category"),
            )
            if target is None:
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Stored Script Analysis recovery warning target could not be resolved.",
                    422,
                    retryable=False,
                    details={"target_type": target_type, "target_id": target_id},
                )
            field_name = "anchor_quote" if target_type == "narrative_marker" else "quote"
            if code == SCRIPT_ANALYSIS_SHORT_QUOTE_RECOVERED_WARNING_CODE:
                original = parsed.get("original_short_quote")
                recovered = parsed.get("recovered_short_quote")
                if not all(isinstance(item, str) and item for item in (original, recovered)):
                    raise AppError(
                        "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                        "Stored Script Analysis short-quote recovery warning is missing quote metadata.",
                        422,
                        retryable=False,
                        details={"warning": warning},
                    )
                if not (
                    target.get("section_id") == section_id
                    and target.get(field_name) == recovered
                    and target.get("context_quote") == recovered
                ):
                    raise AppError(
                        "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                        "Stored Script Analysis recovery warning does not match its target quote.",
                        422,
                        retryable=False,
                        details={"target_type": target_type, "target_id": target_id},
                    )
                target[field_name] = original
                continue

            original_context = parsed.get("original_context_quote")
            recovered_context = parsed.get("recovered_context_quote")
            if not all(isinstance(item, str) and item for item in (original_context, recovered_context)):
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Stored Script Analysis context-quote recovery warning is missing quote metadata.",
                    422,
                    retryable=False,
                    details={"warning": warning},
                )
            if not (
                target.get("section_id") == section_id
                and target.get(field_name) == recovered_context
                and target.get("context_quote") == recovered_context
            ):
                raise AppError(
                    "SCRIPT_ANALYSIS_CANONICAL_MISMATCH",
                    "Stored Script Analysis recovery warning does not match its target quote.",
                    422,
                    retryable=False,
                    details={"target_type": target_type, "target_id": target_id},
                )
            target["context_quote"] = original_context

    @staticmethod
    def _quote_target_for_warning(
        projected: dict[str, Any],
        *,
        target_type: str,
        target_id: str,
        candidate_category: Any,
    ) -> dict[str, Any] | None:
        if target_type == "narrative_marker":
            markers = projected.get("narrative_markers")
            if isinstance(markers, list):
                for marker in markers:
                    if isinstance(marker, dict) and marker.get("marker_id") == target_id:
                        return marker
            return None

        if target_type != "continuity_occurrence" or not isinstance(candidate_category, str):
            return None
        catalog = projected.get("continuity_catalog")
        if not isinstance(catalog, dict):
            return None
        candidates = catalog.get(candidate_category)
        if not isinstance(candidates, list):
            return None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            occurrences = candidate.get("occurrences")
            if not isinstance(occurrences, list):
                continue
            for occurrence in occurrences:
                if isinstance(occurrence, dict) and occurrence.get("occurrence_id") == target_id:
                    return occurrence
        return None
