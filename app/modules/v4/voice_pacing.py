from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import io
import json
import math
from statistics import median
import subprocess
import tempfile
import wave
from typing import Any, Callable

from app.api.errors import AppError
from kokoro.runtime_response_calibration import KOKORO_FLOW_VERSION
from app.core.config import get_settings
from app.modules.v4.script_word_budget import count_narration_words, validate_planned_average_wpm, validate_planned_narration_wpm
from app.modules.v4.voice_audio import (
    CANONICAL_CHANNELS,
    CANONICAL_SAMPLE_RATE,
    CANONICAL_SAMPLE_WIDTH,
    WavInfo,
    validate_canonical_wav_bytes,
)
from app.modules.v4.voice_provider_contracts import (
    VoiceProviderPacingDirective,
    VoiceProviderPacingObservation,
    VoiceProviderPacingRecommendation,
    VoiceProviderPacingRequest,
)


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


VOICE_PACING_POLICY_VERSION = "script_section_wpm_pacing_v5"
VOICE_PACING_SCHEMA_VERSION = 2
# Product QC is expressed in the same unit the user reviews: measured raw WPM
# relative to the Script section target.  Provider synthesis must land within 5%
# before deterministic final pacing is allowed.
VOICE_PACING_QUALITY_FENCE_RELATIVE = 0.05
# Keep the prior atempo bound as an independent hard safety fence.  In normal
# operation the +/-5% WPM gate is tighter, but this remains explicit defense in
# depth rather than conflating tempo correction with WPM deviation.
VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE = 0.15
VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE = 0.02
# Only sample-rounding noise may be cropped/padded. At 48 kHz this is <= 0.042 ms.
VOICE_PACING_SAMPLE_ROUNDING_EPSILON = 2
VOICE_PACING_SECTION_ACCEPTANCE_IDENTITY_VERSION = "voice_section_pacing_acceptance_v1"
VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED = "inverse_speed_plus_fixed_v1"
VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID = "source_pacing_plan_invalid"
VOICE_REMEDIATION_BLOCK_KOKORO_DURATION_MODEL_INVALID = "kokoro_duration_model_invalid"
RAW_SEGMENT_SYNTHESIS_IDENTITY_SCHEMA_VERSION = 2
_RAW_SYNTHESIS_IDENTITY_IGNORED_SETTINGS = {
    "raw_synthesis_identity_sha256",
    "pacing_identity_sha256",
}
_RAW_SEGMENT_SYNTHESIS_IDENTITY_IGNORED_SETTINGS = {
    *_RAW_SYNTHESIS_IDENTITY_IGNORED_SETTINGS,
    "voice_pacing_policy_identity_sha256",
}


def section_pacing_acceptance_identity_sha256(
    *,
    section: dict[str, Any],
    complete_segments: list[dict[str, Any]],
    source_script_revision_id: str | None,
    provider_identity_sha256: str | None,
) -> str:
    """Hash only the exact section audio/pacing lineage relevant to human acceptance.

    This identity deliberately excludes operation ID and whole-report / whole-plan
    hashes so regenerating a sibling section cannot invalidate an unchanged
    section. Regenerating this section, changing its provider pacing directive,
    Script lineage, provider identity, or exact segment audio does invalidate it.
    """
    section_id = section.get("section_id")
    segment_ids = section.get("segment_ids")
    if not isinstance(section_id, str) or not section_id or not isinstance(segment_ids, list) or not segment_ids:
        raise AppError(
            "VOICE_PACING_ACCEPTANCE_IDENTITY_INVALID",
            "Voice section acceptance identity is missing section coverage.",
            409,
            retryable=False,
        )
    entries_by_segment = {
        str(item.get("segment_id")): item
        for item in complete_segments
        if isinstance(item, dict) and isinstance(item.get("segment_id"), str)
    }
    segment_lineage: list[dict[str, Any]] = []
    for segment_id in [str(value) for value in segment_ids]:
        entry = entries_by_segment.get(segment_id)
        artifact_id = entry.get("segment_audio_artifact_id") if isinstance(entry, dict) else None
        raw_identity = entry.get("raw_segment_synthesis_identity_sha256") if isinstance(entry, dict) else None
        sample_count = entry.get("sample_count") if isinstance(entry, dict) else None
        if (
            not isinstance(artifact_id, str)
            or not artifact_id
            or not isinstance(raw_identity, str)
            or not raw_identity
            or not isinstance(sample_count, int)
            or sample_count <= 0
        ):
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_IDENTITY_INVALID",
                "Voice section acceptance identity is missing exact segment audio lineage.",
                409,
                retryable=False,
                details={"section_id": section_id, "segment_id": segment_id},
            )
        segment_lineage.append({
            "segment_id": segment_id,
            "segment_audio_artifact_id": artifact_id,
            "raw_segment_synthesis_identity_sha256": raw_identity,
            "sample_count": sample_count,
        })
    return canonical_hash({
        "schema_version": 1,
        "policy_version": VOICE_PACING_SECTION_ACCEPTANCE_IDENTITY_VERSION,
        "source_script_revision_id": source_script_revision_id,
        "provider_identity_sha256": provider_identity_sha256,
        "section_id": section_id,
        "planned_narration_wpm": section.get("planned_narration_wpm", section.get("target_wpm")),
        "provider_pacing_control": section.get("provider_pacing_control", section.get("current_provider_pacing_control")),
        "provider_pacing_value": section.get("provider_pacing_value", section.get("current_provider_pacing_value")),
        "provider_pacing_policy_version": section.get("provider_pacing_policy_version"),
        "provider_pacing_clamped": section.get("provider_pacing_clamped"),
        "segments": segment_lineage,
    })


@dataclass(frozen=True, slots=True)
class PacedSegmentOutputs:
    segment_bytes: list[bytes]
    segment_records: list[dict[str, Any]]
    pacing_summary: dict[str, Any]
    full_info: WavInfo


@dataclass(frozen=True, slots=True)
class SectionPacingAnalysis:
    report: dict[str, Any]
    raw_frames_by_index: list[bytes]
    raw_sample_counts: list[int]


@dataclass(frozen=True, slots=True)
class VoicePacingRemediationActionability:
    regeneration_supported: bool
    manual_acceptance_supported: bool
    remediation_block_reason: str | None
    source_pacing_identity_sha256: str | None
    message: str | None = None


def voice_pacing_policy_identity(provider_key: str, pacing_plan: dict[str, Any]) -> str:
    sections = pacing_plan.get("sections") if isinstance(pacing_plan, dict) else None
    return canonical_hash({
        "schema_version": 1,
        "provider": provider_key,
        "policy_versions": sorted({
            str(section["provider_pacing_policy_version"])
            for section in sections
            if isinstance(section, dict)
        }) if isinstance(sections, list) else [],
        "control_models": sorted({
            str(section["provider_pacing_control"])
            for section in sections
            if isinstance(section, dict)
        }) if isinstance(sections, list) else [],
    })


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _remediation_lineage_present(report: dict[str, Any] | None, section: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict) or not isinstance(section, dict):
        return False
    report_hash = report.get("remediation_report_sha256")
    source_operation_id = report.get("source_operation_id")
    remediation_identity = section.get("remediation_identity")
    return (
        isinstance(report_hash, str)
        and len(report_hash) == 64
        and isinstance(source_operation_id, str)
        and bool(source_operation_id)
        and isinstance(remediation_identity, str)
        and bool(remediation_identity)
    )


def remediation_report_sha256(report: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in report.items() if key != "remediation_report_sha256"})


def section_remediation_identity_sha256(
    *,
    report: dict[str, Any],
    section: dict[str, Any],
) -> str:
    return canonical_hash(
        {
            "schema_version": 1,
            "source_operation_id": report.get("source_operation_id"),
            "source_script_revision_id": report.get("source_script_revision_id"),
            "provider_identity_sha256": report.get("provider_identity_sha256"),
            "generation_settings_snapshot_sha256": report.get("generation_settings_snapshot_sha256"),
            "pacing_identity_sha256": report.get("pacing_identity_sha256"),
            "segmentation_input_sha256": report.get("segmentation_input_sha256"),
            "section": {
                key: value
                for key, value in section.items()
                if key not in {"section_acceptance_identity_sha256", "remediation_identity"}
            },
        }
    )


def rehash_remediation_report(report: dict[str, Any]) -> dict[str, Any]:
    report.pop("remediation_report_sha256", None)
    sections = report.get("sections")
    if not isinstance(sections, list) or any(not isinstance(section, dict) for section in sections):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice remediation report sections are malformed.", 409, retryable=False)
    for section in sections:
        section.pop("remediation_identity", None)
    for section in sections:
        section["remediation_identity"] = section_remediation_identity_sha256(report=report, section=section)
    report["remediation_report_sha256"] = remediation_report_sha256(report)
    return report


def validate_remediation_report_integrity(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict):
        return False
    stored = report.get("remediation_report_sha256")
    if not isinstance(stored, str) or len(stored) != 64 or remediation_report_sha256(report) != stored:
        return False
    sections = report.get("sections")
    if not isinstance(sections, list):
        return False
    for section in sections:
        if not isinstance(section, dict):
            return False
        stored_section_identity = section.get("remediation_identity")
        if (
            not isinstance(stored_section_identity, str)
            or len(stored_section_identity) != 64
            or section_remediation_identity_sha256(report=report, section=section) != stored_section_identity
        ):
            return False
    return True


def _kokoro_pacing_flow_current(
    pacing_plan: dict[str, Any],
    report: dict[str, Any] | None,
) -> bool:
    sections = pacing_plan.get("sections")
    if not isinstance(sections, list) or not sections:
        return False
    if any(
        not isinstance(section, dict)
        or section.get("provider_pacing_policy_version") != KOKORO_FLOW_VERSION
        for section in sections
    ):
        return False
    if not isinstance(report, dict):
        return True
    report_sections = report.get("sections")
    if not isinstance(report_sections, list):
        return False
    return all(
        not isinstance(section, dict)
        or section.get("status") != "regeneration_required"
        or section.get("feedback_supported") is not True
        or section.get("recommendation_policy_version") == KOKORO_FLOW_VERSION
        for section in report_sections
    )


def _kokoro_duration_model_current(pacing_plan: dict[str, Any]) -> bool:
    sections = pacing_plan.get("sections")
    if not isinstance(sections, list) or not sections:
        return False
    for section in sections:
        if (
            not isinstance(section, dict)
            or section.get("provider_duration_model") != VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED
            or not _is_finite_number(section.get("provider_fixed_duration_seconds"))
            or float(section["provider_fixed_duration_seconds"]) < 0
        ):
            return False
    return True


def classify_voice_pacing_remediation_actionability(
    *,
    provider_key: str | None,
    source_pacing_plan: dict[str, Any] | None,
    expected_script_revision_id: str,
    segmentation: dict[str, Any] | None,
    report: dict[str, Any] | None,
    section: dict[str, Any] | None,
    manually_accepted: bool = False,
) -> VoicePacingRemediationActionability:
    """Classify whether a persisted remediation can still be acted on.

    Only the current pacing contract is actionable. Non-current or malformed
    persisted plans are invalid and require fresh generation; no compatibility
    reader or historical contract dispatch exists.
    """
    if not isinstance(source_pacing_plan, dict) or not isinstance(segmentation, dict):
        return VoicePacingRemediationActionability(
            regeneration_supported=False,
            manual_acceptance_supported=False,
            remediation_block_reason=VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID,
            source_pacing_identity_sha256=None,
            message="The source Voice frozen pacing plan is invalid.",
        )

    schema_version = source_pacing_plan.get("schema_version")
    policy_version = source_pacing_plan.get("policy_version")
    if schema_version != VOICE_PACING_SCHEMA_VERSION or policy_version != VOICE_PACING_POLICY_VERSION:
        return VoicePacingRemediationActionability(
            regeneration_supported=False,
            manual_acceptance_supported=False,
            remediation_block_reason=VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID,
            source_pacing_identity_sha256=None,
            message="The source Voice frozen pacing plan is invalid.",
        )

    try:
        source_pacing_identity = validate_voice_pacing_plan(
            source_pacing_plan,
            expected_script_revision_id=expected_script_revision_id,
            segmentation=segmentation,
        )
    except AppError:
        return VoicePacingRemediationActionability(
            regeneration_supported=False,
            manual_acceptance_supported=False,
            remediation_block_reason=VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID,
            source_pacing_identity_sha256=None,
            message="The source Voice frozen pacing plan is invalid.",
        )

    if provider_key == "kokoro_local" and not _kokoro_duration_model_current(source_pacing_plan):
        return VoicePacingRemediationActionability(
            regeneration_supported=False,
            manual_acceptance_supported=False,
            remediation_block_reason=VOICE_REMEDIATION_BLOCK_KOKORO_DURATION_MODEL_INVALID,
            source_pacing_identity_sha256=source_pacing_identity,
            message="This Voice uses an incompatible Kokoro duration model. Generate a fresh Voice before regenerating individual sections.",
        )

    if provider_key == "kokoro_local" and not _kokoro_pacing_flow_current(source_pacing_plan, report):
        return VoicePacingRemediationActionability(
            regeneration_supported=False,
            manual_acceptance_supported=False,
            remediation_block_reason=VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID,
            source_pacing_identity_sha256=source_pacing_identity,
            message="The source Voice does not use the current Kokoro pacing flow.",
        )

    if not validate_remediation_report_integrity(report):
        return VoicePacingRemediationActionability(
            regeneration_supported=False,
            manual_acceptance_supported=False,
            remediation_block_reason=VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID,
            source_pacing_identity_sha256=source_pacing_identity,
            message="The Voice remediation report integrity is invalid.",
        )

    lineage_present = _remediation_lineage_present(report, section)
    section_status = section.get("status") if isinstance(section, dict) else None
    correction = section.get("correction_relative") if isinstance(section, dict) else None
    regeneration_supported = bool(
        not manually_accepted
        and lineage_present
        and section_status == "regeneration_required"
        and section.get("feedback_supported") is True
        and _is_finite_number(section.get("recommended_provider_pacing_value"))
        and isinstance(section.get("recommendation_policy_version"), str)
        and bool(section.get("recommendation_policy_version"))
    ) if isinstance(section, dict) else False
    manual_acceptance_supported = bool(
        not manually_accepted
        and lineage_present
        and section_status == "regeneration_required"
        and section.get("issue_code") == "VOICE_PACING_WPM_OUT_OF_RANGE"
        and _is_finite_number(correction)
        and abs(float(correction)) <= VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE + 1e-12
    ) if isinstance(section, dict) else False
    return VoicePacingRemediationActionability(
        regeneration_supported=regeneration_supported,
        manual_acceptance_supported=manual_acceptance_supported,
        remediation_block_reason=None,
        source_pacing_identity_sha256=source_pacing_identity,
        message=None,
    )


def build_voice_pacing_plan(
    *,
    script: dict[str, Any],
    segmentation: dict[str, Any],
    base_native_speaking_rate: float,
    pacing_directive_resolver: Callable[[VoiceProviderPacingRequest], VoiceProviderPacingDirective],
    source_script_revision_id: str | None = None,
    provider_duration_models_by_section: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sections = script.get("script_sections")
    if not isinstance(sections, list) or not sections:
        raise AppError("VOICE_SCRIPT_PACING_INVALID", "Script section pacing is unavailable.", 409, retryable=False)
    segments = segmentation.get("segments")
    if not isinstance(segments, list) or not segments:
        raise AppError("VOICE_SCRIPT_PACING_INVALID", "Voice segmentation is unavailable for pacing.", 409, retryable=False)

    planned_average_wpm = validate_planned_average_wpm(script.get("planned_average_wpm"))
    segment_ids_by_section: dict[str, list[str]] = defaultdict(list)
    for segment in segments:
        if isinstance(segment, dict):
            segment_ids_by_section[str(segment.get("section_id"))].append(str(segment.get("segment_id")))

    plan_sections: list[dict[str, Any]] = []
    for index, section in enumerate(sections, 1):
        section_id = section.get("section_id")
        narration = section.get("narration")
        if not isinstance(section_id, str) or not section_id or not isinstance(narration, str):
            raise AppError("VOICE_SCRIPT_PACING_INVALID", "Script section pacing identity is invalid.", 409, retryable=False)
        planned_wpm = validate_planned_narration_wpm(section.get("planned_narration_wpm"))
        word_count = count_narration_words(narration)
        target_duration_seconds = word_count * 60.0 / planned_wpm
        provider_duration_model_payload = (
            provider_duration_models_by_section.get(section_id)
            if provider_duration_models_by_section is not None
            else None
        )
        provider_duration_model = None
        provider_fixed_duration_seconds = 0.0
        if provider_duration_model_payload is not None:
            if not isinstance(provider_duration_model_payload, dict):
                raise AppError("VOICE_SCRIPT_PACING_INVALID", "Provider duration model is malformed.", 409, retryable=False)
            provider_duration_model = provider_duration_model_payload.get("provider_duration_model")
            raw_provider_fixed_duration_seconds = provider_duration_model_payload.get("provider_fixed_duration_seconds", 0.0)
            if not isinstance(raw_provider_fixed_duration_seconds, (int, float)) or isinstance(raw_provider_fixed_duration_seconds, bool):
                raise AppError("VOICE_SCRIPT_PACING_INVALID", "Provider fixed duration is invalid.", 409, retryable=False)
            provider_fixed_duration_seconds = float(raw_provider_fixed_duration_seconds)
            if provider_duration_model != VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED:
                raise AppError("VOICE_SCRIPT_PACING_INVALID", "Provider duration model is unsupported.", 409, retryable=False)
            if not math.isfinite(provider_fixed_duration_seconds) or provider_fixed_duration_seconds < 0:
                raise AppError("VOICE_SCRIPT_PACING_INVALID", "Provider fixed duration is invalid.", 409, retryable=False)
        if word_count <= 0:
            raise AppError(
                "VOICE_SCRIPT_PACING_INVALID",
                "Script section pacing requires narration words.",
                409,
                retryable=False,
                details={"section_id": section_id},
            )

        directive = pacing_directive_resolver(
            VoiceProviderPacingRequest(
                section_id=section_id,
                target_narration_wpm=planned_wpm,
                planned_average_wpm=planned_average_wpm,
                base_native_speaking_rate=float(base_native_speaking_rate),
                actual_word_count=word_count,
                target_duration_seconds=target_duration_seconds,
                fixed_duration_seconds=provider_fixed_duration_seconds,
            )
        )

        plan_section = {
            "section_id": section_id,
            "section_order": index,
            "planned_narration_wpm": planned_wpm,
            "actual_word_count": word_count,
            "target_duration_seconds": target_duration_seconds,
            "provider_pacing_control": directive.control_model,
            "provider_pacing_value": round(directive.value, 6) if directive.value is not None else None,
            "provider_pacing_policy_version": directive.policy_version,
            "provider_pacing_value_range": list(directive.value_range) if directive.value_range is not None else None,
            "provider_pacing_clamped": directive.clamped,
            "speed_response_exponent": round(float(directive.speed_response_exponent), 6),
            "response_model_source": directive.response_model_source,
            "response_model_identity": directive.response_model_identity,
            "segment_ids": segment_ids_by_section.get(section_id, []),
        }
        if provider_duration_model is not None:
            plan_section["provider_duration_model"] = provider_duration_model
            plan_section["provider_fixed_duration_seconds"] = round(provider_fixed_duration_seconds, 6)
        plan_sections.append(plan_section)
    control_models = sorted({str(section["provider_pacing_control"]) for section in plan_sections})
    plan = {
        "schema_version": VOICE_PACING_SCHEMA_VERSION,
        "policy_version": VOICE_PACING_POLICY_VERSION,
        "source_script_revision_id": source_script_revision_id,
        "control_model": control_models[0] if len(control_models) == 1 else "mixed",
        "base_native_speaking_rate": float(base_native_speaking_rate),
        "planned_average_wpm": planned_average_wpm,
        "quality_fence_relative": VOICE_PACING_QUALITY_FENCE_RELATIVE,
        "atempo_safety_fence_relative": VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE,
        "final_wpm_tolerance_relative": VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE,
        "sample_rounding_epsilon": VOICE_PACING_SAMPLE_ROUNDING_EPSILON,
        "sections": plan_sections,
    }
    return {**plan, "pacing_identity_sha256": canonical_hash(plan)}


def validate_voice_pacing_plan(
    pacing_plan: dict[str, Any],
    *,
    expected_script_revision_id: str,
    segmentation: dict[str, Any],
) -> str:
    if not isinstance(pacing_plan, dict):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing plan is missing.", 409, retryable=False)
    if pacing_plan.get("schema_version") != VOICE_PACING_SCHEMA_VERSION or pacing_plan.get("policy_version") != VOICE_PACING_POLICY_VERSION:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing policy version is not current.", 409, retryable=False)
    if pacing_plan.get("source_script_revision_id") != expected_script_revision_id:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing plan Script lineage is stale.", 409, retryable=False)
    stored_hash = pacing_plan.get("pacing_identity_sha256")
    if not isinstance(stored_hash, str):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing identity is missing.", 409, retryable=False)
    identity_payload = {key: value for key, value in pacing_plan.items() if key != "pacing_identity_sha256"}
    if canonical_hash(identity_payload) != stored_hash:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing identity hash does not match the frozen plan.", 409, retryable=False)
    if (
        pacing_plan.get("quality_fence_relative") != VOICE_PACING_QUALITY_FENCE_RELATIVE
        or pacing_plan.get("atempo_safety_fence_relative") != VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE
        or pacing_plan.get("final_wpm_tolerance_relative") != VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE
        or pacing_plan.get("sample_rounding_epsilon") != VOICE_PACING_SAMPLE_ROUNDING_EPSILON
    ):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing quality policy is inconsistent.", 409, retryable=False)
    segments = segmentation.get("segments")
    sections = pacing_plan.get("sections")
    if not isinstance(segments, list) or not isinstance(sections, list) or not sections:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing coverage is malformed.", 409, retryable=False)
    all_segment_ids = [str(segment.get("segment_id")) for segment in segments if isinstance(segment, dict)]
    covered: list[str] = []
    for order, section in enumerate(sections, 1):
        if not isinstance(section, dict) or section.get("section_order") != order:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing section order is invalid.", 409, retryable=False)
        validate_planned_narration_wpm(section.get("planned_narration_wpm"))
        if not isinstance(section.get("actual_word_count"), int) or section["actual_word_count"] <= 0:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing section word count is invalid.", 409, retryable=False)
        if (
            not isinstance(section.get("target_duration_seconds"), (int, float))
            or not math.isfinite(float(section["target_duration_seconds"]))
            or float(section["target_duration_seconds"]) <= 0
        ):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing target duration is invalid.", 409, retryable=False)
        duration_model = section.get("provider_duration_model")
        fixed_duration = section.get("provider_fixed_duration_seconds")
        if duration_model is not None:
            if duration_model != VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED:
                raise AppError("VOICE_PACING_PLAN_INVALID", "Voice provider duration model is invalid.", 409, retryable=False)
            if (
                not isinstance(fixed_duration, (int, float))
                or not math.isfinite(float(fixed_duration))
                or float(fixed_duration) < 0
            ):
                raise AppError("VOICE_PACING_PLAN_INVALID", "Voice provider fixed duration is invalid.", 409, retryable=False)
        elif fixed_duration is not None:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice provider fixed duration requires a duration model.", 409, retryable=False)
        control = section.get("provider_pacing_control")
        value = section.get("provider_pacing_value")
        if control not in {"relative_multiplier", "native_wpm", "none"}:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice provider pacing control is invalid.", 409, retryable=False)
        if (control == "none") != (value is None):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice provider pacing value is inconsistent with its control model.", 409, retryable=False)
        if not isinstance(section.get("provider_pacing_policy_version"), str) or not section["provider_pacing_policy_version"].strip():
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice provider pacing policy version is missing.", 409, retryable=False)
        if (
            not isinstance(section.get("speed_response_exponent"), (int, float))
            or isinstance(section.get("speed_response_exponent"), bool)
            or not math.isfinite(float(section["speed_response_exponent"]))
            or float(section["speed_response_exponent"]) <= 0
        ):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice speed response exponent is invalid.", 409, retryable=False)
        value_range = section.get("provider_pacing_value_range")
        if value_range is not None and (
            not isinstance(value_range, list)
            or len(value_range) != 2
            or not all(isinstance(item, (int, float)) for item in value_range)
            or float(value_range[0]) > float(value_range[1])
        ):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice provider pacing value range is invalid.", 409, retryable=False)
        segment_ids = section.get("segment_ids")
        if not isinstance(segment_ids, list) or not segment_ids:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing section has no segments.", 409, retryable=False)
        covered.extend(str(value) for value in segment_ids)
    if covered != all_segment_ids:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing segments do not match frozen segmentation.", 409, retryable=False)
    return stored_hash


def pacing_section_for_segment(pacing_plan: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    section_id = str(segment.get("section_id"))
    sections = pacing_plan.get("sections")
    if not isinstance(sections, list):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing plan is malformed.", 409, retryable=False)
    matches = [section for section in sections if isinstance(section, dict) and section.get("section_id") == section_id]
    if len(matches) != 1:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice segment pacing target is missing.", 409, retryable=False)
    return matches[0]


def pacing_directive_for_segment(pacing_plan: dict[str, Any], segment: dict[str, Any]) -> VoiceProviderPacingDirective:
    section = pacing_section_for_segment(pacing_plan, segment)
    value = section.get("provider_pacing_value")
    return VoiceProviderPacingDirective(
        control_model=section["provider_pacing_control"],
        value=float(value) if isinstance(value, (int, float)) else None,
        target_narration_wpm=int(section["planned_narration_wpm"]),
        policy_version=str(section["provider_pacing_policy_version"]),
        clamped=bool(section.get("provider_pacing_clamped")),
        value_range=(
            (float(section["provider_pacing_value_range"][0]), float(section["provider_pacing_value_range"][1]))
            if isinstance(section.get("provider_pacing_value_range"), list)
            else None
        ),
    )


def provider_control_value_for_segment(pacing_plan: dict[str, Any], segment: dict[str, Any]) -> float:
    directive = pacing_directive_for_segment(pacing_plan, segment)
    return float(directive.value) if directive.value is not None else float(pacing_plan["base_native_speaking_rate"])


def raw_synthesis_pacing_directives(pacing_plan: dict[str, Any]) -> list[dict[str, Any]]:
    sections = pacing_plan.get("sections")
    if not isinstance(sections, list):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing plan sections are missing.", 409, retryable=False)
    return [
        {
            "section_id": section.get("section_id"),
            "provider_pacing_control": section.get("provider_pacing_control"),
            "provider_pacing_value": section.get("provider_pacing_value"),
            "provider_pacing_policy_version": section.get("provider_pacing_policy_version"),
            "provider_pacing_value_range": section.get("provider_pacing_value_range"),
            "provider_pacing_clamped": section.get("provider_pacing_clamped"),
            "provider_duration_model": section.get("provider_duration_model"),
            "provider_fixed_duration_seconds": section.get("provider_fixed_duration_seconds"),
            "segment_ids": section.get("segment_ids"),
        }
        for section in sections
        if isinstance(section, dict)
    ]


def raw_synthesis_identity_sha256(
    *,
    provider_identity: dict[str, Any],
    settings_snapshot: dict[str, Any],
    pacing_plan: dict[str, Any],
) -> str:
    material_settings = {
        key: value
        for key, value in settings_snapshot.items()
        if key not in _RAW_SYNTHESIS_IDENTITY_IGNORED_SETTINGS
    }
    return canonical_hash(
        {
            "provider_identity": provider_identity,
            "settings": material_settings,
            "provider_pacing_directives": raw_synthesis_pacing_directives(pacing_plan),
        }
    )


def _raw_segment_synthesis_identity_sha256(
    *,
    provider_identity: dict[str, Any],
    settings_snapshot: dict[str, Any],
    pacing_plan: dict[str, Any],
    segment: dict[str, Any],
    synthesis_context_sha256: str | None = None,
    ignored_settings: set[str],
    schema_version: int,
) -> str:
    section = pacing_section_for_segment(pacing_plan, segment)
    material_settings = {
        key: value
        for key, value in settings_snapshot.items()
        if key not in ignored_settings
    }
    return canonical_hash(
        {
            "schema_version": schema_version,
            "provider_identity": provider_identity,
            "settings": material_settings,
            "segment": {
                "segment_id": segment.get("segment_id"),
                "segment_order": segment.get("segment_order"),
                "section_id": segment.get("section_id"),
                "source_start": segment.get("source_start"),
                "source_end": segment.get("source_end"),
                "text_sha256": segment.get("text_sha256"),
                "exact_text": segment.get("exact_text"),
                "synthesis_context_sha256": synthesis_context_sha256,
                "voice_first": segment.get("voice_first"),
            },
            "section_pacing": {
                "section_id": section.get("section_id"),
                "provider_pacing_control": section.get("provider_pacing_control"),
                "provider_pacing_value": section.get("provider_pacing_value"),
                "provider_pacing_policy_version": section.get("provider_pacing_policy_version"),
                "provider_pacing_value_range": section.get("provider_pacing_value_range"),
                "provider_pacing_clamped": section.get("provider_pacing_clamped"),
            },
        }
    )


def raw_segment_synthesis_identity_sha256(
    *,
    provider_identity: dict[str, Any],
    settings_snapshot: dict[str, Any],
    pacing_plan: dict[str, Any],
    segment: dict[str, Any],
    synthesis_context_sha256: str | None = None,
) -> str:
    return _raw_segment_synthesis_identity_sha256(
        provider_identity=provider_identity,
        settings_snapshot=settings_snapshot,
        pacing_plan=pacing_plan,
        segment=segment,
        synthesis_context_sha256=synthesis_context_sha256,
        ignored_settings=_RAW_SEGMENT_SYNTHESIS_IDENTITY_IGNORED_SETTINGS,
        schema_version=RAW_SEGMENT_SYNTHESIS_IDENTITY_SCHEMA_VERSION,
    )


def raw_segment_synthesis_material_identity_sha256(
    *,
    provider_identity: dict[str, Any],
    settings_snapshot: dict[str, Any],
    pacing_plan: dict[str, Any],
    segment: dict[str, Any],
    synthesis_context_sha256: str | None = None,
) -> str:
    section = pacing_section_for_segment(pacing_plan, segment)
    material_settings = {
        key: value
        for key, value in settings_snapshot.items()
        if key not in _RAW_SEGMENT_SYNTHESIS_IDENTITY_IGNORED_SETTINGS
    }
    return canonical_hash(
        {
            "schema_version": 1,
            "provider_identity": provider_identity,
            "settings": material_settings,
            "segment": {
                "segment_id": segment.get("segment_id"),
                "segment_order": segment.get("segment_order"),
                "section_id": segment.get("section_id"),
                "source_start": segment.get("source_start"),
                "source_end": segment.get("source_end"),
                "text_sha256": segment.get("text_sha256"),
                "exact_text": segment.get("exact_text"),
                "synthesis_context_sha256": synthesis_context_sha256,
                "voice_first": segment.get("voice_first"),
            },
            "section_material": {
                "section_id": section.get("section_id"),
                "provider_pacing_control": section.get("provider_pacing_control"),
                "provider_duration_model": section.get("provider_duration_model"),
                "provider_fixed_duration_seconds": section.get("provider_fixed_duration_seconds"),
            },
        }
    )


def apply_section_pacing_recommendation(
    pacing_plan: dict[str, Any],
    *,
    section_id: str,
    recommended_provider_pacing_value: float,
    recommendation_policy_version: str,
    recommendation_clamped: bool = False,
    speed_response_exponent_used: float | None = None,
    response_model_source: str | None = None,
    response_model_identity: str | None = None,
) -> dict[str, Any]:
    if not isinstance(section_id, str) or not section_id:
        raise AppError("VOICE_PACING_REMEDIATION_INVALID", "Voice remediation section identity is missing.", 409, retryable=False)
    if not isinstance(recommendation_policy_version, str) or not recommendation_policy_version.strip():
        raise AppError("VOICE_PACING_REMEDIATION_INVALID", "Voice remediation policy version is missing.", 409, retryable=False)
    if not isinstance(recommended_provider_pacing_value, (int, float)) or not math.isfinite(float(recommended_provider_pacing_value)):
        raise AppError("VOICE_PACING_REMEDIATION_INVALID", "Voice remediation pacing value is invalid.", 409, retryable=False)
    if speed_response_exponent_used is not None and (
        not isinstance(speed_response_exponent_used, (int, float))
        or isinstance(speed_response_exponent_used, bool)
        or not math.isfinite(float(speed_response_exponent_used))
        or float(speed_response_exponent_used) <= 0
    ):
        raise AppError("VOICE_PACING_REMEDIATION_INVALID", "Voice remediation response model is invalid.", 409, retryable=False)
    matched = False
    sections: list[dict[str, Any]] = []
    for section in pacing_plan.get("sections", []):
        if not isinstance(section, dict):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing section is malformed.", 409, retryable=False)
        next_section = dict(section)
        if next_section.get("section_id") == section_id:
            if next_section.get("provider_pacing_control") == "none":
                raise AppError("VOICE_PACING_REMEDIATION_UNSUPPORTED", "The Voice provider cannot apply section pacing remediation.", 409, retryable=False)
            value_range = next_section.get("provider_pacing_value_range")
            if isinstance(value_range, list) and len(value_range) == 2:
                minimum, maximum = float(value_range[0]), float(value_range[1])
                if not minimum <= float(recommended_provider_pacing_value) <= maximum:
                    raise AppError("VOICE_PACING_REMEDIATION_INVALID", "Voice remediation pacing value is outside provider range.", 409, retryable=False)
            next_section["provider_pacing_value"] = round(float(recommended_provider_pacing_value), 6)
            next_section["provider_pacing_policy_version"] = recommendation_policy_version
            next_section["provider_pacing_clamped"] = bool(recommendation_clamped)
            if speed_response_exponent_used is not None:
                next_section["speed_response_exponent"] = round(float(speed_response_exponent_used), 6)
            if response_model_source is not None:
                next_section["response_model_source"] = response_model_source
            if response_model_identity is not None:
                next_section["response_model_identity"] = response_model_identity
            matched = True
        sections.append(next_section)
    if not matched:
        raise AppError("VOICE_PACING_REMEDIATION_INVALID", "Voice remediation target section is not in the frozen pacing plan.", 404, retryable=False)
    payload = {key: value for key, value in pacing_plan.items() if key != "pacing_identity_sha256"}
    payload["sections"] = sections
    return {**payload, "pacing_identity_sha256": canonical_hash(payload)}


def analyze_section_pacing(
    *,
    segment_bytes: list[bytes],
    segment_records: list[dict[str, Any]],
    segmentation: dict[str, Any],
    pacing_plan: dict[str, Any],
    source_operation_id: str | None = None,
    source_script_revision_id: str | None = None,
    provider_identity_sha256: str | None = None,
    generation_settings_snapshot_sha256: str | None = None,
    recommendation_resolver: Callable[[VoiceProviderPacingObservation], VoiceProviderPacingRecommendation] | None = None,
    progress_callback: Callable[[], None] | None = None,
) -> SectionPacingAnalysis:
    segments = segmentation.get("segments")
    if not isinstance(segments, list) or len(segments) != len(segment_bytes) or len(segments) != len(segment_records):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice segment pacing coverage is incomplete.", 409, retryable=False)
    sections = pacing_plan.get("sections")
    if not isinstance(sections, list) or not sections:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice section pacing plan is missing.", 409, retryable=False)

    raw_frames_by_index: list[bytes] = []
    raw_sample_counts: list[int] = []
    for data in segment_bytes:
        info = validate_canonical_wav_bytes(data)
        raw_sample_counts.append(info.sample_count)
        raw_frames_by_index.append(_wav_frames(data, info.sample_count))

    section_summaries: list[dict[str, Any]] = []
    index_by_segment_id = {str(segment.get("segment_id")): index for index, segment in enumerate(segments)}

    for section in sections:
        if not isinstance(section, dict):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing section is malformed.", 409, retryable=False)
        if progress_callback is not None:
            progress_callback()
        segment_ids = section.get("segment_ids")
        if not isinstance(segment_ids, list) or not segment_ids:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice section pacing has no segments.", 409, retryable=False)
        indexes = [index_by_segment_id[str(segment_id)] for segment_id in segment_ids if str(segment_id) in index_by_segment_id]
        if len(indexes) != len(segment_ids):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice section pacing segment identity is incomplete.", 409, retryable=False)
        raw_samples = sum(raw_sample_counts[index] for index in indexes)
        material_identities = [
            segment_records[index].get("raw_segment_material_identity_sha256")
            for index in indexes
        ]
        response_lineage_sha256 = (
            canonical_hash(
                {
                    "schema_version": 1,
                    "source_script_revision_id": source_script_revision_id,
                    "provider_identity_sha256": provider_identity_sha256,
                    "section_id": section.get("section_id"),
                    "provider_duration_model": section.get("provider_duration_model"),
                    "provider_fixed_duration_seconds": section.get("provider_fixed_duration_seconds", 0.0),
                    "segment_material_identities": material_identities,
                }
            )
            if all(isinstance(value, str) and len(value) == 64 for value in material_identities)
            else None
        )
        desired_target_samples = max(1, round(float(section["target_duration_seconds"]) * CANONICAL_SAMPLE_RATE))
        tempo_factor = raw_samples / desired_target_samples
        raw_duration_seconds = raw_samples / CANONICAL_SAMPLE_RATE
        raw_wpm = float(section["actual_word_count"]) * 60.0 / raw_duration_seconds
        correction_relative = tempo_factor - 1.0
        target_wpm = float(section["planned_narration_wpm"])
        wpm_deviation_relative = (raw_wpm / target_wpm) - 1.0
        wpm_out_of_range = abs(wpm_deviation_relative) > VOICE_PACING_QUALITY_FENCE_RELATIVE + 1e-12
        atempo_out_of_range = abs(correction_relative) > VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE + 1e-12
        severity = "regeneration_required" if wpm_out_of_range or atempo_out_of_range else "pass"
        issue_code = (
            "VOICE_PACING_CORRECTION_OUT_OF_RANGE"
            if atempo_out_of_range
            else "VOICE_PACING_WPM_OUT_OF_RANGE"
            if wpm_out_of_range
            else None
        )
        current_provider_pacing_value = section.get("provider_pacing_value")
        provider_duration_model = section.get("provider_duration_model")
        provider_fixed_duration_seconds = (
            float(section.get("provider_fixed_duration_seconds"))
            if isinstance(section.get("provider_fixed_duration_seconds"), (int, float))
            and math.isfinite(float(section.get("provider_fixed_duration_seconds")))
            else 0.0
        )
        speed_response_exponent = (
            float(section.get("speed_response_exponent"))
            if isinstance(section.get("speed_response_exponent"), (int, float))
            and not isinstance(section.get("speed_response_exponent"), bool)
            and math.isfinite(float(section.get("speed_response_exponent")))
            and float(section.get("speed_response_exponent")) > 0
            else 1.0
        )
        recommendation_payload: dict[str, Any] = {
            "feedback_supported": False,
            "recommended_provider_pacing_value": None,
            "recommendation_policy_version": None,
            "recommendation_clamped": False,
            "recommendation_reason": None,
            "speed_response_exponent_used": None,
            "response_model_source": None,
            "response_model_identity": None,
        }
        if severity == "regeneration_required" and recommendation_resolver is not None:
            value_range = section.get("provider_pacing_value_range")
            observation = VoiceProviderPacingObservation(
                section_id=str(section["section_id"]),
                target_narration_wpm=int(section["planned_narration_wpm"]),
                measured_raw_wpm=raw_wpm,
                current_control_model=section["provider_pacing_control"],
                current_provider_pacing_value=(
                    float(section["provider_pacing_value"])
                    if isinstance(section.get("provider_pacing_value"), (int, float))
                    else None
                ),
                provider_value_range=(
                    (float(value_range[0]), float(value_range[1]))
                    if isinstance(value_range, list) and len(value_range) == 2
                    else None
                ),
                actual_word_count=int(section["actual_word_count"]),
                measured_raw_duration_seconds=raw_duration_seconds,
                target_duration_seconds=desired_target_samples / CANONICAL_SAMPLE_RATE,
                fixed_duration_seconds=provider_fixed_duration_seconds,
                speed_response_exponent=speed_response_exponent,
                response_model_source=section.get("response_model_source") if isinstance(section.get("response_model_source"), str) else None,
                response_model_identity=section.get("response_model_identity") if isinstance(section.get("response_model_identity"), str) else None,
                response_lineage_sha256=response_lineage_sha256,
            )
            recommendation = recommendation_resolver(observation)
            recommendation_payload = {
                "feedback_supported": recommendation.status == "supported",
                "recommended_provider_pacing_value": recommendation.recommended_value,
                "recommendation_policy_version": recommendation.policy_version,
                "recommendation_clamped": recommendation.clamped,
                "recommendation_reason": recommendation.reason,
                "speed_response_exponent_used": recommendation.speed_response_exponent_used,
                "response_model_source": recommendation.response_model_source,
                "response_model_identity": recommendation.response_model_identity,
            }
        effective_response_exponent = recommendation_payload.get("speed_response_exponent_used")
        if not isinstance(effective_response_exponent, (int, float)) or not math.isfinite(float(effective_response_exponent)) or float(effective_response_exponent) <= 0:
            effective_response_exponent = speed_response_exponent
        inferred_native_wpm_at_rate_1 = None
        if (
            section.get("provider_pacing_control") == "relative_multiplier"
            and isinstance(current_provider_pacing_value, (int, float))
            and math.isfinite(float(current_provider_pacing_value))
            and float(current_provider_pacing_value) > 0
        ):
            if provider_duration_model == VOICE_PROVIDER_DURATION_MODEL_INVERSE_SPEED_PLUS_FIXED:
                speech_duration = raw_duration_seconds - provider_fixed_duration_seconds
                speech_duration_at_rate_1 = (float(current_provider_pacing_value) ** float(effective_response_exponent)) * speech_duration
                if math.isfinite(speech_duration_at_rate_1) and speech_duration_at_rate_1 > 1e-9:
                    inferred_native_wpm_at_rate_1 = float(section["actual_word_count"]) * 60.0 / speech_duration_at_rate_1
            else:
                inferred_native_wpm_at_rate_1 = raw_wpm / float(current_provider_pacing_value)
        section_payload = {
            "section_id": section["section_id"],
            "section_order": section["section_order"],
            "segment_ids": list(segment_ids),
            "planned_narration_wpm": section["planned_narration_wpm"],
            "target_wpm": section["planned_narration_wpm"],
            "actual_word_count": section["actual_word_count"],
            "raw_sample_count": raw_samples,
            "raw_duration_seconds": raw_duration_seconds,
            "measured_raw_wpm": raw_wpm,
            "raw_wpm": raw_wpm,
            "target_duration_seconds": desired_target_samples / CANONICAL_SAMPLE_RATE,
            "target_sample_count": desired_target_samples,
            "required_tempo_factor": tempo_factor,
            "tempo_factor": tempo_factor,
            "correction_relative": correction_relative,
            "relative_correction_magnitude": abs(correction_relative),
            "wpm_deviation_relative": wpm_deviation_relative,
            "wpm_deviation_percent": wpm_deviation_relative * 100.0,
            "quality_fence_relative": VOICE_PACING_QUALITY_FENCE_RELATIVE,
            "atempo_safety_fence_relative": VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE,
            "provider_duration_model": provider_duration_model,
            "provider_fixed_duration_seconds": provider_fixed_duration_seconds,
            "speed_response_exponent": speed_response_exponent,
            "response_lineage_sha256": response_lineage_sha256,
            "inferred_native_wpm_at_rate_1": inferred_native_wpm_at_rate_1,
            "status": severity,
            "severity": severity,
            "issue_code": issue_code,
            "provider_pacing_control": section["provider_pacing_control"],
            "current_provider_pacing_control": section["provider_pacing_control"],
            "provider_pacing_value": section["provider_pacing_value"],
            "current_provider_pacing_value": section["provider_pacing_value"],
            "provider_pacing_policy_version": section.get("provider_pacing_policy_version"),
            "provider_pacing_clamped": section["provider_pacing_clamped"],
            **recommendation_payload,
        }
        section_complete_segments = [
            {
                "segment_id": str(segment_id),
                "segment_audio_artifact_id": segment_records[index].get("segment_audio_artifact_id"),
                "raw_segment_synthesis_identity_sha256": segment_records[index].get("raw_segment_synthesis_identity_sha256"),
                "sample_count": segment_records[index].get("sample_count"),
            }
            for segment_id, index in zip(segment_ids, indexes, strict=True)
        ]
        section_payload["section_acceptance_identity_sha256"] = section_pacing_acceptance_identity_sha256(
            section=section_payload,
            complete_segments=section_complete_segments,
            source_script_revision_id=source_script_revision_id,
            provider_identity_sha256=provider_identity_sha256,
        )
        section_summaries.append(section_payload)

    native_wpm_observations = [
        float(item["inferred_native_wpm_at_rate_1"])
        for item in section_summaries
        if isinstance(item.get("inferred_native_wpm_at_rate_1"), (int, float))
        and math.isfinite(float(item["inferred_native_wpm_at_rate_1"]))
        and float(item["inferred_native_wpm_at_rate_1"]) > 0
    ]
    report = {
        "schema_version": "voice_section_pacing_analysis_v2",
        "status": "regeneration_required"
        if any(item["status"] == "regeneration_required" for item in section_summaries)
        else "pass",
        "source_operation_id": source_operation_id,
        "source_script_revision_id": source_script_revision_id,
        "provider_identity_sha256": provider_identity_sha256,
        "generation_settings_snapshot_sha256": generation_settings_snapshot_sha256,
        "pacing_identity_sha256": pacing_plan.get("pacing_identity_sha256"),
        "segmentation_input_sha256": segmentation.get("segmentation_input_sha256"),
        "policy_version": pacing_plan["policy_version"],
        "quality_fence_relative": VOICE_PACING_QUALITY_FENCE_RELATIVE,
        "atempo_safety_fence_relative": VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE,
        "inferred_native_wpm_at_rate_1_median": median(native_wpm_observations) if native_wpm_observations else None,
        "inferred_native_wpm_observation_count": len(native_wpm_observations),
        "sections": section_summaries,
    }
    report = rehash_remediation_report(report)
    return SectionPacingAnalysis(
        report=report,
        raw_frames_by_index=raw_frames_by_index,
        raw_sample_counts=raw_sample_counts,
    )


def apply_section_pacing(
    *,
    analysis: SectionPacingAnalysis,
    segment_records: list[dict[str, Any]],
    segmentation: dict[str, Any],
    pacing_plan: dict[str, Any],
    progress_callback: Callable[[], None] | None = None,
) -> PacedSegmentOutputs:
    if analysis.report.get("status") != "pass":
        raise AppError(
            "VOICE_PACING_REGENERATION_REQUIRED",
            "One or more Voice sections require provider resynthesis before final pacing can be applied.",
            422,
            retryable=False,
            details=analysis.report,
        )
    segments = segmentation.get("segments")
    if not isinstance(segments, list) or len(segments) != len(segment_records):
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice segment pacing coverage is incomplete.", 409, retryable=False)
    output_frames_by_index: dict[int, bytes] = {}
    output_sample_counts_by_index: dict[int, int] = {}
    output_scales_by_index: dict[int, float] = {}
    section_summaries: list[dict[str, Any]] = []
    index_by_segment_id = {str(segment.get("segment_id")): index for index, segment in enumerate(segments)}

    for section_summary in analysis.report["sections"]:
        if progress_callback is not None:
            progress_callback()
        segment_ids = section_summary.get("segment_ids")
        if not isinstance(segment_ids, list) or not segment_ids:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice section pacing has no segments.", 409, retryable=False)
        indexes = [index_by_segment_id[str(segment_id)] for segment_id in segment_ids if str(segment_id) in index_by_segment_id]
        if len(indexes) != len(segment_ids):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice section pacing segment identity is incomplete.", 409, retryable=False)
        raw_samples = int(section_summary["raw_sample_count"])
        desired_target_samples = int(section_summary["target_sample_count"])
        tempo_factor = float(section_summary["required_tempo_factor"])
        raw_frames = b"".join(analysis.raw_frames_by_index[index] for index in indexes)
        try:
            paced_frames = _retime_pcm(
                raw_frames,
                tempo_factor=tempo_factor,
                target_sample_count=desired_target_samples,
            )
        finally:
            if progress_callback is not None:
                progress_callback()
        frame_bytes = CANONICAL_SAMPLE_WIDTH * CANONICAL_CHANNELS
        actual_section_samples = len(paced_frames) // frame_bytes
        if actual_section_samples <= 0:
            raise AppError("VOICE_PACING_NORMALIZER_FAILED", "Voice tempo pacing produced empty audio.", 422, retryable=False)
        final_duration_seconds = actual_section_samples / CANONICAL_SAMPLE_RATE
        final_wpm = float(section_summary["actual_word_count"]) * 60.0 / final_duration_seconds
        relative_error = abs(final_wpm - float(section_summary["target_wpm"])) / float(section_summary["target_wpm"])
        if relative_error > VOICE_PACING_FINAL_WPM_TOLERANCE_RELATIVE:
            raise AppError(
                "VOICE_PACING_TARGET_MISSED",
                "Voice section pacing missed the Script WPM target.",
                422,
                retryable=False,
                details={
                    "section_id": section_summary["section_id"],
                    "actual_sample_count": actual_section_samples,
                    "target_sample_count": desired_target_samples,
                    "relative_error": relative_error,
                },
            )
        split_counts = _proportional_counts([analysis.raw_sample_counts[index] for index in indexes], actual_section_samples)
        section_scale = actual_section_samples / raw_samples
        cursor = 0
        for index, count in zip(indexes, split_counts, strict=True):
            byte_count = count * frame_bytes
            output_frames_by_index[index] = paced_frames[cursor : cursor + byte_count]
            output_sample_counts_by_index[index] = count
            output_scales_by_index[index] = section_scale
            cursor += byte_count
        if cursor != len(paced_frames):
            raise AppError("VOICE_PACING_NORMALIZER_FAILED", "Voice paced section split did not preserve all samples.", 422, retryable=False)
        section_summaries.append({
            **section_summary,
            "final_duration_seconds": final_duration_seconds,
            "final_sample_count": actual_section_samples,
            "effective_wpm": final_wpm,
            "final_wpm": final_wpm,
            "relative_wpm_error": relative_error,
            "deviation_percent": relative_error * 100.0,
            "status": "pass",
            "severity": "pass",
        })

    paced_bytes: list[bytes] = []
    paced_records: list[dict[str, Any]] = []
    for index, (segment, record) in enumerate(zip(segments, segment_records, strict=True)):
        frames = output_frames_by_index[index]
        sample_count = output_sample_counts_by_index[index]
        data = _wav_from_frames(frames)
        section = pacing_section_for_segment(pacing_plan, segment)
        paced_bytes.append(data)
        paced_records.append(
            {
                **record,
                "sample_count": sample_count,
                "raw_sample_count": analysis.raw_sample_counts[index],
                "raw_duration_seconds": analysis.raw_sample_counts[index] / CANONICAL_SAMPLE_RATE,
                "provider_timing": _scale_provider_timing(record.get("provider_timing"), scale=output_scales_by_index[index]),
                "pacing_policy_version": pacing_plan["policy_version"],
                "pacing_identity_sha256": pacing_plan["pacing_identity_sha256"],
                "target_narration_wpm": section["planned_narration_wpm"],
                "section_word_count": section["actual_word_count"],
                "section_target_duration_seconds": section["target_duration_seconds"],
                "provider_pacing_control": section["provider_pacing_control"],
                "provider_pacing_value": section["provider_pacing_value"],
                "provider_pacing_policy_version": section.get("provider_pacing_policy_version"),
                "provider_pacing_clamped": section["provider_pacing_clamped"],
                "provider_duration_model": section.get("provider_duration_model"),
                "provider_fixed_duration_seconds": section.get("provider_fixed_duration_seconds"),
            }
        )
    full_bytes = _wav_from_frames(b"".join(_wav_frames(data, validate_canonical_wav_bytes(data).sample_count) for data in paced_bytes))
    full_info = validate_canonical_wav_bytes(full_bytes)
    if full_info.sample_count != sum(output_sample_counts_by_index.values()):
        raise AppError("VOICE_PACING_NORMALIZER_FAILED", "Voice paced narration sample accounting is inconsistent.", 422, retryable=False)
    return PacedSegmentOutputs(
        segment_bytes=paced_bytes,
        segment_records=paced_records,
        pacing_summary={
            "schema_version": VOICE_PACING_SCHEMA_VERSION,
            "policy_version": pacing_plan["policy_version"],
            "pacing_identity_sha256": pacing_plan["pacing_identity_sha256"],
            "analysis_report_sha256": analysis.report["remediation_report_sha256"],
            "sections": section_summaries,
        },
        full_info=full_info,
    )


def pace_section_outputs(
    *,
    segment_bytes: list[bytes],
    segment_records: list[dict[str, Any]],
    segmentation: dict[str, Any],
    pacing_plan: dict[str, Any],
    recommendation_resolver: Callable[[VoiceProviderPacingObservation], VoiceProviderPacingRecommendation] | None = None,
    progress_callback: Callable[[], None] | None = None,
) -> PacedSegmentOutputs:
    analysis = analyze_section_pacing(
        segment_bytes=segment_bytes,
        segment_records=segment_records,
        segmentation=segmentation,
        pacing_plan=pacing_plan,
        recommendation_resolver=recommendation_resolver,
        progress_callback=progress_callback,
    )
    return apply_section_pacing(
        analysis=analysis,
        segment_records=segment_records,
        segmentation=segmentation,
        pacing_plan=pacing_plan,
        progress_callback=progress_callback,
    )


def _wav_frames(data: bytes, sample_count: int) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.readframes(sample_count)


def _wav_from_frames(frames: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CANONICAL_CHANNELS)
        wav.setsampwidth(CANONICAL_SAMPLE_WIDTH)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(frames)
    return output.getvalue()


def _fit_pcm_to_sample_count(frames: bytes, target_sample_count: int) -> bytes:
    frame_bytes = CANONICAL_SAMPLE_WIDTH * CANONICAL_CHANNELS
    source_sample_count = len(frames) // frame_bytes
    delta = target_sample_count - source_sample_count
    if abs(delta) > VOICE_PACING_SAMPLE_ROUNDING_EPSILON:
        raise AppError(
            "VOICE_PACING_SAMPLE_ADJUSTMENT_OUT_OF_RANGE",
            "Voice pacing sample adjustment exceeds rounding tolerance.",
            422,
            retryable=False,
            details={"source_sample_count": source_sample_count, "target_sample_count": target_sample_count},
        )
    if delta < 0:
        return frames[: target_sample_count * frame_bytes]
    if delta > 0:
        return frames + (b"\x00" * (delta * frame_bytes))
    return frames


def _retime_pcm(frames: bytes, *, tempo_factor: float, target_sample_count: int) -> bytes:
    if tempo_factor <= 0:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice tempo factor must be positive.", 409, retryable=False)
    frame_bytes = CANONICAL_SAMPLE_WIDTH * CANONICAL_CHANNELS
    source_samples = len(frames) // frame_bytes
    if abs(source_samples - target_sample_count) <= VOICE_PACING_SAMPLE_ROUNDING_EPSILON or abs(tempo_factor - 1.0) <= (1 / CANONICAL_SAMPLE_RATE):
        if abs(source_samples - target_sample_count) <= VOICE_PACING_SAMPLE_ROUNDING_EPSILON:
            return _fit_pcm_to_sample_count(frames, target_sample_count)
        return frames

    source_wav = _wav_from_frames(frames)
    with tempfile.TemporaryDirectory(prefix="voice-pacing-") as temp_dir:
        input_path = f"{temp_dir}/input.wav"
        output_path = f"{temp_dir}/output.wav"
        with open(input_path, "wb") as handle:
            handle.write(source_wav)
        command = [
            get_settings().ffmpeg_path,
            "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", input_path,
            "-filter:a", _atempo_filter(tempo_factor),
            "-ac", str(CANONICAL_CHANNELS),
            "-ar", str(CANONICAL_SAMPLE_RATE),
            "-sample_fmt", "s16",
            output_path,
        ]
        try:
            completed = subprocess.run(command, check=False, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        except FileNotFoundError as exc:
            raise AppError("VOICE_PACING_NORMALIZER_UNAVAILABLE", "FFmpeg is required for Voice tempo pacing correction.", 503, retryable=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise AppError("VOICE_PACING_NORMALIZER_TIMEOUT", "Voice tempo pacing correction timed out.", 504, retryable=True) from exc
        if completed.returncode != 0:
            raise AppError(
                "VOICE_PACING_NORMALIZER_FAILED",
                "Voice tempo pacing correction failed.",
                422,
                retryable=True,
                details={"stderr_bytes": len(completed.stderr or b"")},
            )
        with open(output_path, "rb") as handle:
            converted_wav = handle.read()
    converted_info = validate_canonical_wav_bytes(converted_wav)
    converted = _wav_frames(converted_wav, converted_info.sample_count)
    if abs(converted_info.sample_count - target_sample_count) <= VOICE_PACING_SAMPLE_ROUNDING_EPSILON:
        return _fit_pcm_to_sample_count(converted, target_sample_count)
    return converted


def _atempo_filter(tempo_factor: float) -> str:
    factors: list[float] = []
    remaining = tempo_factor
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    factors.append(remaining)
    return ",".join(f"atempo={factor:.8f}" for factor in factors)


def _proportional_counts(raw_counts: list[int], target_total: int) -> list[int]:
    if not raw_counts or any(count <= 0 for count in raw_counts) or target_total <= 0:
        raise AppError("VOICE_PACING_PLAN_INVALID", "Voice pacing sample counts are invalid.", 409, retryable=False)
    raw_total = sum(raw_counts)
    counts: list[int] = []
    assigned = 0
    for index, raw_count in enumerate(raw_counts):
        if index == len(raw_counts) - 1:
            count = target_total - assigned
        else:
            count = max(1, round(target_total * raw_count / raw_total))
            remaining_segments = len(raw_counts) - index - 1
            count = min(count, target_total - assigned - remaining_segments)
        counts.append(count)
        assigned += count
    return counts


def _scale_provider_timing(provider_timing: Any, *, scale: float) -> Any:
    if not isinstance(provider_timing, dict) or scale <= 0:
        return provider_timing
    scaled: dict[str, Any] = dict(provider_timing)
    for key in ("word_timings", "character_timings"):
        timings = provider_timing.get(key)
        if not isinstance(timings, list):
            scaled[key] = timings
            continue
        scaled_items: list[Any] = []
        for item in timings:
            if not isinstance(item, dict):
                scaled_items.append(item)
                continue
            next_item = dict(item)
            for field in ("start_seconds", "end_seconds", "start", "end"):
                if isinstance(next_item.get(field), (int, float)):
                    next_item[field] = float(next_item[field]) * scale
            scaled_items.append(next_item)
        scaled[key] = scaled_items
    return scaled
