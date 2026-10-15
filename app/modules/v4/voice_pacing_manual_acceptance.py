from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.enums import JobStatus
from app.db.models import Job, Project, VoiceGenerationOperation
from app.modules.v4.voice_pacing import (
    SectionPacingAnalysis,
    VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE,
    VOICE_REMEDIATION_BLOCK_KOKORO_DURATION_MODEL_INVALID,
    VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID,
    classify_voice_pacing_remediation_actionability,
    rehash_remediation_report,
    section_pacing_acceptance_identity_sha256,
    voice_pacing_policy_identity,
)


VOICE_PACING_MANUAL_ACCEPTANCE_POLICY_VERSION = "manual_section_wpm_acceptance_v1"
VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY = "manual_pacing_acceptances"


def exact_manual_pacing_acceptance_matches(
    remediation: dict[str, Any] | None,
    remediation_report_sha256: str | None,
    accepted_records: list[dict[str, Any]] | None,
) -> bool:
    """Return whether the exact current remediation has a durable human acceptance."""
    if not isinstance(remediation, dict) or not isinstance(remediation_report_sha256, str) or not accepted_records:
        return False
    section_id = remediation.get("section_id")
    remediation_identity = remediation.get("remediation_identity")
    if not isinstance(section_id, str) or not isinstance(remediation_identity, str):
        return False
    section_acceptance_identity = remediation.get("section_acceptance_identity_sha256")
    for item in accepted_records:
        if not isinstance(item, dict) or item.get("section_id") != section_id:
            continue
        accepted_section_identity = item.get("section_acceptance_identity_sha256")
        if isinstance(section_acceptance_identity, str) and isinstance(accepted_section_identity, str):
            return accepted_section_identity == section_acceptance_identity
        # Compatibility for decisions created before section-local acceptance
        # identity existed. Once the current report has the new section-local
        # identity, the legacy remediation identity is sufficient: it is already
        # fenced to the same source operation, Script, provider/settings, whole
        # pacing plan, segmentation, and exact section payload. This permits a
        # code-version upgrade to add the new field without invalidating an
        # already-recorded human decision. Old reports without the new field
        # retain the stricter legacy report-hash check.
        if item.get("remediation_identity") == remediation_identity:
            if isinstance(section_acceptance_identity, str):
                return True
            if item.get("remediation_report_sha256") == remediation_report_sha256:
                return True
    return False


def carry_forward_manual_pacing_acceptances(
    *,
    source_report: dict[str, Any] | None,
    accepted_records: list[dict[str, Any]] | None,
    complete_segments: list[dict[str, Any]] | None,
    source_script_revision_id: str | None,
    provider_identity_sha256: str | None,
    exclude_section_id: str | None = None,
) -> list[dict[str, Any]]:
    """Carry only still-valid sibling decisions into a remediation operation.

    Legacy records are upgraded to the section-local identity using the exact
    source operation segment artifacts. The target section is always excluded,
    because explicitly regenerating that section must require a new review.
    """
    if not isinstance(source_report, dict) or not isinstance(accepted_records, list):
        return []
    report_sha = source_report.get("remediation_report_sha256")
    sections_by_id = {
        str(item.get("section_id")): item
        for item in source_report.get("sections", [])
        if isinstance(item, dict) and isinstance(item.get("section_id"), str)
    }
    segments = [item for item in complete_segments or [] if isinstance(item, dict)]
    carried: list[dict[str, Any]] = []
    for record in accepted_records:
        if not isinstance(record, dict):
            continue
        section_id = record.get("section_id")
        if not isinstance(section_id, str) or section_id == exclude_section_id:
            continue
        section = sections_by_id.get(section_id)
        if not isinstance(section, dict):
            continue
        if not exact_manual_pacing_acceptance_matches(
            section,
            report_sha if isinstance(report_sha, str) else None,
            [record],
        ):
            continue
        section_identity = section.get("section_acceptance_identity_sha256")
        if not isinstance(section_identity, str):
            section_identity = section_pacing_acceptance_identity_sha256(
                section=section,
                complete_segments=segments,
                source_script_revision_id=source_script_revision_id,
                provider_identity_sha256=provider_identity_sha256,
            )
        next_record = deepcopy(record)
        next_record["section_acceptance_identity_sha256"] = section_identity
        carried.append(next_record)
    carried.sort(key=lambda item: str(item.get("section_id")))
    return carried


def apply_manual_pacing_acceptances(
    analysis: SectionPacingAnalysis,
    accepted_records: list[dict[str, Any]] | None,
) -> SectionPacingAnalysis:
    """Turn exact human-accepted WPM-only failures into final-pacing inputs.

    This does not relax the deterministic atempo safety fence. Acceptance is
    matched to the immutable remediation identity produced from the exact raw
    section audio/pacing lineage, so another generation or remediation cannot
    inherit a stale human decision.
    """
    if not accepted_records:
        return analysis
    report = deepcopy(analysis.report)
    accepted_by_section = {
        str(item.get("section_id")): item
        for item in accepted_records
        if isinstance(item, dict) and isinstance(item.get("section_id"), str)
    }
    applied: list[str] = []
    for section in report.get("sections", []):
        if not isinstance(section, dict) or section.get("status") != "regeneration_required":
            continue
        section_id = str(section.get("section_id") or "")
        accepted = accepted_by_section.get(section_id)
        if not isinstance(accepted, dict):
            continue
        correction = section.get("correction_relative")
        if (
            not exact_manual_pacing_acceptance_matches(
                section,
                report.get("remediation_report_sha256"),
                [accepted],
            )
            or section.get("issue_code") != "VOICE_PACING_WPM_OUT_OF_RANGE"
            or not isinstance(correction, (int, float))
            or abs(float(correction)) > VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE + 1e-12
        ):
            continue
        section["status"] = "pass"
        section["severity"] = "pass"
        section["manual_pacing_acceptance"] = {
            "policy_version": VOICE_PACING_MANUAL_ACCEPTANCE_POLICY_VERSION,
            "accepted_at": accepted.get("accepted_at"),
            "remediation_identity": accepted.get("remediation_identity"),
            "section_acceptance_identity_sha256": accepted.get("section_acceptance_identity_sha256"),
            "source_remediation_report_sha256": accepted.get("remediation_report_sha256"),
            "raw_wpm": accepted.get("raw_wpm"),
            "target_wpm": accepted.get("target_wpm"),
            "wpm_deviation_percent": accepted.get("wpm_deviation_percent"),
        }
        applied.append(section_id)

    report["status"] = (
        "regeneration_required"
        if any(
            isinstance(item, dict) and item.get("status") == "regeneration_required"
            for item in report.get("sections", [])
        )
        else "pass"
    )
    report["manual_pacing_acceptance_policy_version"] = VOICE_PACING_MANUAL_ACCEPTANCE_POLICY_VERSION
    report["accepted_pacing_deviation_section_ids"] = applied
    report = rehash_remediation_report(report)
    return SectionPacingAnalysis(
        report=report,
        raw_frames_by_index=analysis.raw_frames_by_index,
        raw_sample_counts=analysis.raw_sample_counts,
    )


class VoicePacingManualAcceptanceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def accept_section(
        self,
        *,
        project_id: str,
        section_id: str,
        source_remediation_operation_id: str,
        expected_remediation_report_sha256: str,
        expected_remediation_identity: str,
    ) -> dict[str, Any]:
        project = self.session.get(Project, project_id)
        operation = self.session.get(VoiceGenerationOperation, source_remediation_operation_id)
        if (
            project is None
            or operation is None
            or operation.project_id != project_id
            or operation.status != "failed"
            or operation.error_code != "VOICE_PACING_REGENERATION_REQUIRED"
        ):
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_STALE",
                "The source Voice pacing remediation is no longer actionable.",
                409,
                retryable=False,
            )
        job = self.session.get(Job, operation.job_id)
        if job is None:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice operation Job is missing.", 409, retryable=False)
        report = operation.result_json.get("pacing_remediation_report") if isinstance(operation.result_json, dict) else None
        if (
            not isinstance(report, dict)
            or report.get("status") != "regeneration_required"
            or report.get("remediation_report_sha256") != expected_remediation_report_sha256
        ):
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_STALE",
                "The Voice pacing remediation report identity changed.",
                409,
                retryable=False,
            )
        if (
            operation.script_artifact_id != project.script_artifact_id
            or operation.expected_narration_audio_artifact_id != project.narration_audio_artifact_id
            or operation.expected_narration_word_timing_map_artifact_id != project.narration_word_timing_map_artifact_id
        ):
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_STALE",
                "The Voice pacing remediation Script or approved Voice lineage is stale.",
                409,
                retryable=False,
            )
        settings_snapshot = operation.generation_settings_snapshot_json if isinstance(operation.generation_settings_snapshot_json, dict) else {}
        sections = report.get("sections")
        matches = [
            item
            for item in sections
            if isinstance(item, dict) and str(item.get("section_id")) == section_id
        ] if isinstance(sections, list) else []
        if len(matches) != 1:
            raise AppError("VOICE_PACING_ACCEPTANCE_INVALID", "The Voice section is not present in the remediation report.", 404, retryable=False)
        section = matches[0]
        provider_identity = operation.provider_identity_json if isinstance(operation.provider_identity_json, dict) else {}
        provider_key = (
            settings_snapshot.get("generation_provider")
            or provider_identity.get("provider_key")
            or provider_identity.get("provider")
            or "elevenlabs"
        )
        source_pacing_plan = operation.request_json.get("script_section_pacing") if isinstance(operation.request_json, dict) else None
        segmentation = operation.request_json.get("segmentation") if isinstance(operation.request_json, dict) else None
        actionability = classify_voice_pacing_remediation_actionability(
            provider_key=str(provider_key),
            source_pacing_plan=source_pacing_plan,
            expected_script_revision_id=str(project.script_artifact_id),
            segmentation=segmentation,
            report=report,
            section=section,
        )
        if actionability.remediation_block_reason == VOICE_REMEDIATION_BLOCK_KOKORO_DURATION_MODEL_INVALID:
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_STALE",
                actionability.message or "The source Voice remediation uses an incompatible Kokoro duration model. Generate a fresh Voice before accepting individual section pacing deviations.",
                409,
                retryable=False,
            )
        if actionability.remediation_block_reason == VOICE_REMEDIATION_BLOCK_SOURCE_PACING_PLAN_INVALID:
            raise AppError("VOICE_PACING_ACCEPTANCE_STALE", "The source Voice frozen pacing plan is invalid.", 409, retryable=False)
        if (
            not isinstance(source_pacing_plan, dict)
            or settings_snapshot.get("voice_pacing_policy_identity_sha256")
            != voice_pacing_policy_identity(str(provider_key), source_pacing_plan)
        ):
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_STALE",
                "The Voice pacing policy identity changed after this remediation report was created.",
                409,
                retryable=False,
            )

        if provider_key not in {"elevenlabs", "kokoro_local"}:
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_STALE",
                "The Voice generation provider does not support manual pacing acceptance.",
                409,
                retryable=False,
            )

        correction = section.get("correction_relative")
        if section.get("remediation_identity") != expected_remediation_identity:
            raise AppError("VOICE_PACING_ACCEPTANCE_STALE", "The Voice section remediation identity changed.", 409, retryable=False)
        if section.get("status") != "regeneration_required" or section.get("issue_code") != "VOICE_PACING_WPM_OUT_OF_RANGE":
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_UNSAFE",
                "Only WPM quality-fence deviations can be manually accepted.",
                409,
                retryable=False,
            )
        if (
            not isinstance(correction, (int, float))
            or abs(float(correction)) > VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE + 1e-12
        ):
            raise AppError(
                "VOICE_PACING_ACCEPTANCE_UNSAFE",
                "This section exceeds the deterministic final-pacing safety fence and cannot be manually accepted.",
                409,
                retryable=False,
                details={"atempo_safety_fence_relative": VOICE_PACING_ATEMPO_SAFETY_FENCE_RELATIVE},
            )

        now = utc_now_iso()
        result = deepcopy(operation.result_json) if isinstance(operation.result_json, dict) else {}
        existing = result.get(VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY)
        records = [item for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
        records = [item for item in records if str(item.get("section_id")) != section_id]
        section_acceptance_identity = section.get("section_acceptance_identity_sha256")
        if not isinstance(section_acceptance_identity, str):
            complete_segments = (
                operation.result_json.get("complete_segments")
                if isinstance(operation.result_json, dict)
                else None
            )
            section_acceptance_identity = section_pacing_acceptance_identity_sha256(
                section=section,
                complete_segments=[item for item in complete_segments or [] if isinstance(item, dict)],
                source_script_revision_id=str(operation.script_artifact_id),
                provider_identity_sha256=operation.provider_identity_sha256,
            )
        records.append({
            "policy_version": VOICE_PACING_MANUAL_ACCEPTANCE_POLICY_VERSION,
            "section_id": section_id,
            "remediation_identity": expected_remediation_identity,
            "section_acceptance_identity_sha256": section_acceptance_identity,
            "remediation_report_sha256": expected_remediation_report_sha256,
            "raw_wpm": section.get("raw_wpm"),
            "target_wpm": section.get("target_wpm"),
            "wpm_deviation_percent": section.get("wpm_deviation_percent"),
            "correction_relative": correction,
            "accepted_at": now,
        })
        records.sort(key=lambda item: str(item.get("section_id")))
        result[VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY] = records
        operation.result_json = result

        required = {
            str(item.get("section_id")): str(item.get("remediation_identity"))
            for item in report.get("sections", [])
            if isinstance(item, dict) and item.get("status") == "regeneration_required"
        }
        accepted = {
            str(item.get("section_id")): str(item.get("remediation_identity"))
            for item in records
            if isinstance(item, dict)
        }
        remaining = sorted(
            section_key
            for section_key, remediation_identity in required.items()
            if accepted.get(section_key) != remediation_identity
        )
        accepted_ids = sorted(
            section_key
            for section_key, remediation_identity in required.items()
            if accepted.get(section_key) == remediation_identity
        )
        status = "accepted"
        if not remaining:
            operation.status = "finalizing"
            operation.error_code = None
            operation.error_message = None
            operation.error_details_json = None
            operation.completed_at = None
            operation.next_reconcile_at = now
            operation.reconcile_owner = None
            operation.reconcile_token = None
            operation.reconcile_lease_expires_at = None
            operation.state_revision += 1
            job.status = JobStatus.RUNNING
            job.retryable = False
            job.progress = 90
            job.error_code = None
            job.error_message = None
            job.error_details_json = None
            job.completed_at = None
            job.finished_at = None
            job.updated_at = now
            job.heartbeat_at = now
            status = "resuming"
        operation.updated_at = now
        self.session.commit()
        return {
            "project_id": project_id,
            "operation_id": operation.operation_id,
            "job_id": operation.job_id,
            "section_id": section_id,
            "status": status,
            "accepted_section_ids": accepted_ids,
            "remaining_regeneration_required_section_ids": remaining,
        }
