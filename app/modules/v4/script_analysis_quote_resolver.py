from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from app.api.errors import AppError


SCRIPT_ANALYSIS_SHORT_QUOTE_RECOVERED_WARNING_CODE = (
    "SCRIPT_ANALYSIS_SHORT_QUOTE_RECOVERED_FROM_CONTEXT"
)
SCRIPT_ANALYSIS_CONTEXT_QUOTE_RECOVERED_WARNING_CODE = (
    "SCRIPT_ANALYSIS_CONTEXT_QUOTE_RECOVERED_FROM_SHORT"
)
_CONTINUITY_CATEGORIES = (
    "characters",
    "locations",
    "recurring_props",
    "recurring_visual_subjects",
    "visual_motifs",
)
_PRESENCE_RANGE_CATEGORIES = (
    "characters",
    "locations",
    "recurring_props",
    "recurring_visual_subjects",
)


@dataclass(frozen=True)
class ResolvedQuoteReference:
    span: list[int]
    effective_short_quote: str
    effective_context_quote: str
    recovery_direction: Literal["short_from_context", "context_from_short"] | None = None
    original_short_quote: str | None = None
    original_context_quote: str | None = None


@dataclass(frozen=True)
class QuoteRecoveryEvent:
    target_type: Literal["narrative_marker", "continuity_occurrence"]
    target_ref: dict[str, Any]
    recovery_direction: Literal["short_from_context", "context_from_short"]
    section_id: str
    recovered_quote: str
    original_short_quote: str | None = None
    original_context_quote: str | None = None
    candidate_category: str | None = None


class ScriptAnalysisQuoteResolver:
    """Resolve section-scoped exact quotes into global Script spans."""

    def resolve(
        self,
        *,
        llm_input: dict[str, Any],
        raw_output: dict[str, Any],
        section_ranges: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[QuoteRecoveryEvent]]:
        resolved = deepcopy(raw_output)
        recovery_events: list[QuoteRecoveryEvent] = []
        sections = {
            item["section_id"]: item
            for item in llm_input["script"]["sections"]
        }
        ranges = {item["section_id"]: item for item in section_ranges}

        for index, marker in enumerate(resolved["narrative_markers"]):
            reference = self._resolve_reference(
                sections=sections,
                ranges=ranges,
                section_id=marker["section_id"],
                short_quote=marker["anchor_quote"],
                context_quote=marker["context_quote"],
                path=f"$.narrative_markers[{index}]",
            )
            marker["anchor_quote"] = reference.effective_short_quote
            marker["context_quote"] = reference.effective_context_quote
            marker["resolved_script_span"] = reference.span
            if reference.recovery_direction is not None:
                recovery_events.append(
                    QuoteRecoveryEvent(
                        target_type="narrative_marker",
                        target_ref=marker,
                        recovery_direction=reference.recovery_direction,
                        section_id=marker["section_id"],
                        original_short_quote=reference.original_short_quote,
                        original_context_quote=reference.original_context_quote,
                        recovered_quote=reference.effective_short_quote,
                    )
                )

        for category in _CONTINUITY_CATEGORIES:
            for candidate_index, candidate in enumerate(resolved["continuity_catalog"][category]):
                for occurrence_index, occurrence in enumerate(candidate["occurrences"]):
                    reference = self._resolve_reference(
                        sections=sections,
                        ranges=ranges,
                        section_id=occurrence["section_id"],
                        short_quote=occurrence["quote"],
                        context_quote=occurrence["context_quote"],
                        path=(
                            f"$.continuity_catalog.{category}[{candidate_index}]"
                            f".occurrences[{occurrence_index}]"
                        ),
                    )
                    occurrence["quote"] = reference.effective_short_quote
                    occurrence["context_quote"] = reference.effective_context_quote
                    occurrence["resolved_script_span"] = reference.span
                    if reference.recovery_direction is not None:
                        recovery_events.append(
                            QuoteRecoveryEvent(
                                target_type="continuity_occurrence",
                                target_ref=occurrence,
                                recovery_direction=reference.recovery_direction,
                                section_id=occurrence["section_id"],
                                original_short_quote=reference.original_short_quote,
                                original_context_quote=reference.original_context_quote,
                                recovered_quote=reference.effective_short_quote,
                                candidate_category=category,
                            )
                        )
        for category in _PRESENCE_RANGE_CATEGORIES:
            for candidate_index, candidate in enumerate(resolved["continuity_catalog"][category]):
                for range_index, presence_range in enumerate(candidate["presence_ranges"]):
                    presence_range["resolved_script_span"] = self._resolve_presence_range(
                        sections=sections,
                        ranges=ranges,
                        section_id=presence_range["section_id"],
                        start_quote=presence_range["start_quote"],
                        end_quote=presence_range["end_quote"],
                        path=(
                            f"$.continuity_catalog.{category}[{candidate_index}]"
                            f".presence_ranges[{range_index}]"
                        ),
                    )

        for context_index, context in enumerate(resolved["visual_context_catalog"]):
            for range_index, presence_range in enumerate(context["presence_ranges"]):
                presence_range["resolved_script_span"] = self._resolve_presence_range(
                    sections=sections,
                    ranges=ranges,
                    section_id=presence_range["section_id"],
                    start_quote=presence_range["start_quote"],
                    end_quote=presence_range["end_quote"],
                    path=(
                        f"$.visual_context_catalog[{context_index}]"
                        f".presence_ranges[{range_index}]"
                    ),
                )

        return resolved, recovery_events

    @staticmethod
    def _resolve_presence_range(
        *,
        sections: dict[str, dict[str, Any]],
        ranges: dict[str, dict[str, Any]],
        section_id: str,
        start_quote: str,
        end_quote: str,
        path: str,
    ) -> list[int]:
        section = sections.get(section_id)
        section_range = ranges.get(section_id)
        if section is None or section_range is None:
            raise AppError(
                "SCRIPT_ANALYSIS_UNKNOWN_SECTION",
                "Script Analysis presence range references an unknown Script section.",
                422,
                retryable=False,
                details={"path": path, "section_id": section_id},
            )
        narration = str(section["narration"])
        if not isinstance(start_quote, str) or not start_quote.strip() or not isinstance(end_quote, str) or not end_quote.strip():
            raise AppError(
                "SCRIPT_ANALYSIS_PRESENCE_RANGE_QUOTE_INVALID",
                "Presence-range start_quote and end_quote must be non-empty exact Script text.",
                422,
                retryable=False,
                details={"path": path, "section_id": section_id},
            )
        start_count = narration.count(start_quote)
        end_count = narration.count(end_quote)
        if start_count != 1 or end_count != 1:
            raise AppError(
                "SCRIPT_ANALYSIS_PRESENCE_RANGE_QUOTE_AMBIGUOUS",
                "Presence-range boundary quotes must each occur exactly once in the declared section narration.",
                422,
                retryable=False,
                details={
                    "path": path,
                    "section_id": section_id,
                    "start_match_count": start_count,
                    "end_match_count": end_count,
                },
            )
        local_start = narration.find(start_quote)
        local_end_start = narration.find(end_quote)
        local_end = local_end_start + len(end_quote)
        if local_start > local_end_start or local_start >= local_end:
            raise AppError(
                "SCRIPT_ANALYSIS_PRESENCE_RANGE_ORDER_INVALID",
                "Presence-range start_quote must occur at or before end_quote in the declared section narration.",
                422,
                retryable=False,
                details={"path": path, "section_id": section_id},
            )
        absolute_start = int(section_range["start"]) + local_start
        absolute_end = int(section_range["start"]) + local_end
        return [absolute_start, absolute_end]

    @staticmethod
    def _resolve_reference(
        *,
        sections: dict[str, dict[str, Any]],
        ranges: dict[str, dict[str, Any]],
        section_id: str,
        short_quote: str,
        context_quote: str,
        path: str,
    ) -> ResolvedQuoteReference:
        section = sections.get(section_id)
        section_range = ranges.get(section_id)
        if section is None or section_range is None:
            raise AppError(
                "SCRIPT_ANALYSIS_UNKNOWN_SECTION",
                "Script Analysis quote references an unknown Script section.",
                422,
                retryable=False,
                details={"path": path, "section_id": section_id},
            )
        narration = str(section["narration"])
        if not short_quote or not short_quote.strip() or not context_quote or not context_quote.strip():
            raise AppError(
                "SCRIPT_ANALYSIS_QUOTE_CONTEXT_INVALID",
                "context_quote must contain the shorter quote exactly once.",
                422,
                retryable=False,
                details={"path": path, "section_id": section_id},
            )
        short_match_count_in_narration = narration.count(short_quote)
        context_match_count = narration.count(context_quote)
        short_match_count_in_context = context_quote.count(short_quote)
        if context_match_count == 1 and short_match_count_in_context == 1:
            effective_short_quote = short_quote
            effective_context_quote = context_quote
            recovery_direction = None
            original_short_quote = None
            original_context_quote = None
        elif context_match_count == 1:
            effective_short_quote = context_quote
            effective_context_quote = context_quote
            recovery_direction = "short_from_context"
            original_short_quote = short_quote
            original_context_quote = None
        elif short_match_count_in_narration == 1:
            effective_short_quote = short_quote
            effective_context_quote = short_quote
            recovery_direction = "context_from_short"
            original_short_quote = None
            original_context_quote = context_quote
        else:
            raise AppError(
                "SCRIPT_ANALYSIS_QUOTE_CONTEXT_AMBIGUOUS",
                "context_quote must occur exactly once in the declared section narration.",
                422,
                retryable=False,
                details={
                    "path": path,
                    "section_id": section_id,
                    "match_count": context_match_count,
                    "short_match_count": short_match_count_in_narration,
                },
            )
        local_context_start = narration.find(effective_context_quote)
        local_short_start = effective_context_quote.find(effective_short_quote)
        global_start = int(section_range["start"]) + local_context_start + local_short_start
        return ResolvedQuoteReference(
            span=[global_start, global_start + len(effective_short_quote)],
            effective_short_quote=effective_short_quote,
            effective_context_quote=effective_context_quote,
            recovery_direction=recovery_direction,
            original_short_quote=original_short_quote,
            original_context_quote=original_context_quote,
        )
