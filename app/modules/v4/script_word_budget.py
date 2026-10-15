from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import re
from typing import Any, Sequence

from app.modules.platform.video_pacing import resolve_video_pacing_profile, VideoPacingError

# Defensive contract bounds only. They are intentionally broad and are not a
# creative recommendation or fixed narration policy. The external Script
# author chooses a planned narration WPM independently for each section.
MIN_PLANNED_AVERAGE_WPM = 80
MAX_PLANNED_AVERAGE_WPM = 300
MIN_PLANNED_NARRATION_WPM = MIN_PLANNED_AVERAGE_WPM
MAX_PLANNED_NARRATION_WPM = MAX_PLANNED_AVERAGE_WPM
_WORD_PATTERN = re.compile(r"[^\W_]+(?:[’'][^\W_]+)?", flags=re.UNICODE)
SCRIPT_SECTION_SEPARATOR = "\n\n"
SECTION_COUNT_MODE_BACKEND_PLAN = "backend_plan"
SECTION_COUNT_MODE_LLM_DECIDES = "llm_decides"
MIN_SECTION_COUNT = 3
MAX_SECTION_COUNT = 12
TARGET_SECTIONS_PER_MINUTE = Decimal("1.25")
MAX_SECTIONS_PER_MINUTE = Decimal("4")
LLM_SECTION_RANGE_DELTA = 3


class ScriptTimingError(ValueError):
    """Controlled pure-boundary error for Script timing/section-plan inputs."""


@dataclass(frozen=True, slots=True)
class SectionRuntimeDiagnostics:
    section_id: str
    planned_narration_wpm: int
    actual_word_count: int
    estimated_duration_minutes: float


@dataclass(frozen=True, slots=True)
class ScriptRuntimeDiagnostics:
    target_duration_minutes: float
    planned_average_wpm: int
    actual_word_count: int
    estimated_duration_minutes: float
    runtime_delta_minutes: float
    runtime_delta_seconds: int
    runtime_delta_percent: float
    section_runtime_diagnostics: tuple[SectionRuntimeDiagnostics, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["section_runtime_diagnostics"] = [
            asdict(item) for item in self.section_runtime_diagnostics
        ]
        return value


def canonicalize_script_section_narration(narration: Any) -> str:
    if not isinstance(narration, str):
        raise ScriptTimingError("script section narration must be a string")
    return narration.replace("\r\n", "\n").replace("\r", "\n").strip()


def build_full_script_from_sections(script_sections: Sequence[dict[str, Any]]) -> str:
    if not isinstance(script_sections, (list, tuple)):
        raise ScriptTimingError("script_sections must be an ordered sequence")
    narrations: list[str] = []
    for section in script_sections:
        if not isinstance(section, dict):
            raise ScriptTimingError("script section must be an object")
        narration = canonicalize_script_section_narration(section.get("narration"))
        if not narration:
            raise ScriptTimingError("script section narration must not be empty")
        narrations.append(narration)
    if not narrations:
        raise ScriptTimingError("script must contain at least one section")
    return SCRIPT_SECTION_SEPARATOR.join(narrations)


def count_narration_words(text: str) -> int:
    if not isinstance(text, str):
        raise ScriptTimingError("narration text must be a string")
    return len(_WORD_PATTERN.findall(text))


def build_script_section_word_counts(script_sections: Sequence[dict[str, Any]]) -> list[dict[str, int | str]]:
    if not isinstance(script_sections, (list, tuple)):
        raise ScriptTimingError("script_sections must be an ordered sequence")
    counts: list[dict[str, int | str]] = []
    seen: set[str] = set()
    for section in script_sections:
        if not isinstance(section, dict):
            raise ScriptTimingError("script section must be an object")
        section_id = section.get("section_id")
        if not isinstance(section_id, str) or not section_id:
            raise ScriptTimingError("script section_id must be a non-empty string")
        if section_id in seen:
            raise ScriptTimingError("script section_id values must be unique")
        seen.add(section_id)
        narration = canonicalize_script_section_narration(section.get("narration"))
        counts.append({"section_id": section_id, "actual_word_count": count_narration_words(narration)})
    if not counts:
        raise ScriptTimingError("script must contain at least one section")
    return counts


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _duration_decimal(value: int | float | Decimal) -> Decimal:
    try:
        duration = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise ScriptTimingError("target_duration_minutes must be numeric") from exc
    if not duration.is_finite() or duration <= 0:
        raise ScriptTimingError("target_duration_minutes must be greater than zero")
    return duration


def validate_planned_narration_wpm(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScriptTimingError("planned_narration_wpm must be an integer")
    if not MIN_PLANNED_NARRATION_WPM <= value <= MAX_PLANNED_NARRATION_WPM:
        raise ScriptTimingError(
            "planned_narration_wpm must be between "
            f"{MIN_PLANNED_NARRATION_WPM} and {MAX_PLANNED_NARRATION_WPM}"
        )
    return value


def validate_planned_average_wpm(value: Any) -> int:
    """Validate the derived whole-video average for persisted diagnostics."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScriptTimingError("planned_average_wpm must be an integer")
    if not MIN_PLANNED_AVERAGE_WPM <= value <= MAX_PLANNED_AVERAGE_WPM:
        raise ScriptTimingError(
            f"planned_average_wpm must be between {MIN_PLANNED_AVERAGE_WPM} and {MAX_PLANNED_AVERAGE_WPM}"
        )
    return value


def build_section_runtime_diagnostics(
    script_sections: Sequence[dict[str, Any]],
) -> tuple[SectionRuntimeDiagnostics, ...]:
    if not isinstance(script_sections, (list, tuple)) or not script_sections:
        raise ScriptTimingError("script_sections must be a non-empty ordered sequence")
    result: list[SectionRuntimeDiagnostics] = []
    seen: set[str] = set()
    for section in script_sections:
        if not isinstance(section, dict):
            raise ScriptTimingError("script section must be an object")
        section_id = section.get("section_id")
        if not isinstance(section_id, str) or not section_id or section_id in seen:
            raise ScriptTimingError("script section_id values must be unique non-empty strings")
        seen.add(section_id)
        narration = canonicalize_script_section_narration(section.get("narration"))
        if not narration:
            raise ScriptTimingError("script section narration must not be empty")
        pace = validate_planned_narration_wpm(section.get("planned_narration_wpm"))
        words = count_narration_words(narration)
        estimated = (Decimal(words) / Decimal(pace)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result.append(
            SectionRuntimeDiagnostics(
                section_id=section_id,
                planned_narration_wpm=pace,
                actual_word_count=words,
                estimated_duration_minutes=float(estimated),
            )
        )
    return tuple(result)


def build_runtime_diagnostics(
    *,
    target_duration_minutes: int | float | Decimal,
    script_sections: Sequence[dict[str, Any]],
) -> ScriptRuntimeDiagnostics:
    duration = _duration_decimal(target_duration_minutes)
    section_diagnostics = build_section_runtime_diagnostics(script_sections)
    actual_word_count = sum(item.actual_word_count for item in section_diagnostics)
    exact_estimated = sum(
        Decimal(item.actual_word_count) / Decimal(item.planned_narration_wpm)
        for item in section_diagnostics
    )
    if exact_estimated <= 0:
        raise ScriptTimingError("planned Script duration must be greater than zero")
    derived_average = validate_planned_average_wpm(
        _round_half_up(Decimal(actual_word_count) / exact_estimated)
    )
    estimated = exact_estimated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    delta = (estimated - duration).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    delta_seconds = _round_half_up((exact_estimated - duration) * Decimal("60"))
    delta_percent = ((exact_estimated - duration) * Decimal("100") / duration).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return ScriptRuntimeDiagnostics(
        target_duration_minutes=float(duration),
        planned_average_wpm=derived_average,
        actual_word_count=actual_word_count,
        estimated_duration_minutes=float(estimated),
        runtime_delta_minutes=float(delta),
        runtime_delta_seconds=delta_seconds,
        runtime_delta_percent=float(delta_percent),
        section_runtime_diagnostics=section_diagnostics,
    )


def derive_section_roles(target_duration_minutes: int | float | Decimal) -> tuple[str, ...]:
    duration = _duration_decimal(target_duration_minutes)
    section_count = max(MIN_SECTION_COUNT, min(MAX_SECTION_COUNT, _round_half_up(duration * TARGET_SECTIONS_PER_MINUTE)))
    if section_count == 3:
        return ("hook", "development", "resolution")
    roles: list[str] = ["hook", "context"]
    middle_count = section_count - 4
    for index in range(middle_count):
        roles.append("evidence" if index % 2 else "development")
    roles.append("turning_point")
    roles.append("resolution")
    return tuple(roles[:section_count])


def _section_count_bounds(
    target_duration_minutes: int | float | Decimal,
    *,
    section_count_mode: str,
) -> tuple[tuple[str, ...], int, int, int]:
    duration = _duration_decimal(target_duration_minutes)
    recommended_roles = derive_section_roles(duration)
    recommended_count = len(recommended_roles)
    if section_count_mode == SECTION_COUNT_MODE_BACKEND_PLAN:
        return recommended_roles, recommended_count, recommended_count, recommended_count
    if section_count_mode == SECTION_COUNT_MODE_LLM_DECIDES:
        minimum_count = max(MIN_SECTION_COUNT, recommended_count - LLM_SECTION_RANGE_DELTA)
        maximum_by_runtime = max(
            MIN_SECTION_COUNT,
            int((duration * MAX_SECTIONS_PER_MINUTE).to_integral_value(rounding=ROUND_FLOOR)),
        )
        maximum_count = min(MAX_SECTION_COUNT, recommended_count + LLM_SECTION_RANGE_DELTA, maximum_by_runtime)
        return recommended_roles, recommended_count, minimum_count, max(minimum_count, maximum_count)
    raise ScriptTimingError("unsupported section_count_mode")


def build_authoring_constraints(
    target_duration_minutes: int | float | Decimal,
    *,
    section_count_mode: str,
) -> dict[str, object]:
    recommended_roles, recommended_count, minimum_count, maximum_count = _section_count_bounds(
        target_duration_minutes, section_count_mode=section_count_mode
    )
    return {
        "planned_narration_wpm_range": {
            "minimum": MIN_PLANNED_NARRATION_WPM,
            "maximum": MAX_PLANNED_NARRATION_WPM,
        },
        "section_count_mode": section_count_mode,
        "recommended_section_count": recommended_count,
        "minimum_section_count": minimum_count,
        "maximum_section_count": maximum_count,
        "section_plan": [
            {"section_index": index, "section_role": role}
            for index, role in enumerate(recommended_roles, start=1)
        ] if section_count_mode == SECTION_COUNT_MODE_BACKEND_PLAN else [],
    }


def build_computed_constraints(
    target_duration_minutes: int | float | Decimal,
    derived_planned_average_wpm: int,
    *,
    section_count_mode: str = SECTION_COUNT_MODE_BACKEND_PLAN,
    section_roles: Sequence[str] | None = None,
    planned_narration_wpm_range: dict[str, int] | None = None,
) -> dict[str, object]:
    pace = validate_planned_average_wpm(derived_planned_average_wpm)
    _recommended_roles, recommended_count, minimum_count, maximum_count = _section_count_bounds(
        target_duration_minutes, section_count_mode=section_count_mode
    )
    planned_roles = (
        tuple(str(role).strip() for role in section_roles)
        if section_roles is not None
        else tuple()
    )
    if section_roles is not None and (not planned_roles or any(not role for role in planned_roles)):
        raise ScriptTimingError("script section roles must be non-empty")
    selected_range = planned_narration_wpm_range or {"min": MIN_PLANNED_NARRATION_WPM, "max": MAX_PLANNED_NARRATION_WPM}
    minimum_wpm = validate_planned_narration_wpm(selected_range.get("min"))
    maximum_wpm = validate_planned_narration_wpm(selected_range.get("max"))
    if minimum_wpm >= maximum_wpm:
        raise ScriptTimingError("planned narration WPM range must satisfy min < max")
    return {
        "derived_planned_average_wpm": pace,
        "planned_narration_wpm_range": {"min": minimum_wpm, "max": maximum_wpm},
        "section_count_mode": section_count_mode,
        "recommended_section_count": recommended_count,
        "minimum_section_count": minimum_count,
        "maximum_section_count": maximum_count,
    }


def validation_details_for_sections(
    script_sections: Sequence[dict[str, Any]],
    *,
    target_duration_minutes: int | float | Decimal,
) -> ScriptRuntimeDiagnostics:
    return build_runtime_diagnostics(
        target_duration_minutes=target_duration_minutes,
        script_sections=script_sections,
    )


def validate_persisted_script_integrity(script_artifact: dict[str, object], *, pacing_profiles: object) -> ScriptRuntimeDiagnostics:
    if not isinstance(script_artifact, dict):
        raise ScriptTimingError("script artifact must be an object")
    source_user_input = script_artifact.get("source_user_input")
    if not isinstance(source_user_input, dict) or "target_duration_minutes" not in source_user_input:
        raise ScriptTimingError("script source duration is missing")
    try:
        expected_pacing_profile = resolve_video_pacing_profile(pacing_profiles, source_user_input.get("pacing_mode"))
    except VideoPacingError as exc:
        raise ScriptTimingError("script source pacing mode is invalid") from exc
    if script_artifact.get("video_pacing_profile") != expected_pacing_profile:
        raise ScriptTimingError("script video_pacing_profile does not match the selected pacing mode")
    sections = script_artifact.get("script_sections")
    if not isinstance(sections, list):
        raise ScriptTimingError("script sections are missing")
    section_count_mode = source_user_input.get("section_count_mode")
    section_roles = [section.get("section_role") for section in sections if isinstance(section, dict)]
    if len(section_roles) != len(sections):
        raise ScriptTimingError("script section roles are invalid")
    diagnostics = build_runtime_diagnostics(
        target_duration_minutes=source_user_input["target_duration_minutes"],
        script_sections=sections,
    )
    planned_average_wpm = validate_planned_average_wpm(script_artifact.get("planned_average_wpm"))
    if planned_average_wpm != diagnostics.planned_average_wpm:
        raise ScriptTimingError("script planned_average_wpm is not the backend-derived aggregate")
    expected_constraints = build_computed_constraints(
        source_user_input["target_duration_minutes"],
        diagnostics.planned_average_wpm,
        section_count_mode=str(section_count_mode),
        section_roles=section_roles,
        planned_narration_wpm_range=source_user_input.get("narration_wpm"),
    )
    if script_artifact.get("computed_constraints") != expected_constraints:
        raise ScriptTimingError("script computed constraints do not match canonical timing/section planning")
    canonical_full_script = build_full_script_from_sections(sections)
    full_script = script_artifact.get("full_script")
    if not isinstance(full_script, str) or full_script.replace("\r\n", "\n").replace("\r", "\n") != canonical_full_script:
        raise ScriptTimingError("script full_script does not match ordered section narration")
    expected_section_counts = build_script_section_word_counts(sections)
    if script_artifact.get("section_word_counts") != expected_section_counts:
        raise ScriptTimingError("script section_word_counts do not match backend-counted narration")
    actual = count_narration_words(canonical_full_script)
    if script_artifact.get("word_count") != actual:
        raise ScriptTimingError("script word_count does not match backend-counted narration")
    if script_artifact.get("runtime_diagnostics") != diagnostics.to_dict():
        raise ScriptTimingError("script runtime diagnostics are inconsistent")
    return diagnostics


__all__ = [
    "MAX_PLANNED_AVERAGE_WPM",
    "MAX_PLANNED_NARRATION_WPM",
    "MIN_PLANNED_AVERAGE_WPM",
    "MIN_PLANNED_NARRATION_WPM",
    "MAX_SECTION_COUNT",
    "MIN_SECTION_COUNT",
    "SECTION_COUNT_MODE_BACKEND_PLAN",
    "SECTION_COUNT_MODE_LLM_DECIDES",
    "SCRIPT_SECTION_SEPARATOR",
    "SectionRuntimeDiagnostics",
    "ScriptRuntimeDiagnostics",
    "ScriptTimingError",
    "build_authoring_constraints",
    "build_computed_constraints",
    "build_full_script_from_sections",
    "build_runtime_diagnostics",
    "build_script_section_word_counts",
    "build_section_runtime_diagnostics",
    "canonicalize_script_section_narration",
    "count_narration_words",
    "derive_section_roles",
    "validate_persisted_script_integrity",
    "validate_planned_average_wpm",
    "validate_planned_narration_wpm",
    "validation_details_for_sections",
]
