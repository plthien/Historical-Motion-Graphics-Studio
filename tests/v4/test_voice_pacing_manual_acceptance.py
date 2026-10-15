from __future__ import annotations

from kokoro.runtime_response_calibration import KOKORO_FLOW_VERSION

from app.modules.v4.voice_pacing import (
    SectionPacingAnalysis,
    rehash_remediation_report,
    validate_remediation_report_integrity,
)
from app.modules.v4.voice_pacing_manual_acceptance import (
    VOICE_PACING_MANUAL_ACCEPTANCE_POLICY_VERSION,
    apply_manual_pacing_acceptances,
    carry_forward_manual_pacing_acceptances,
    exact_manual_pacing_acceptance_matches,
)


def _analysis(*, correction: float = 0.08, issue_code: str = "VOICE_PACING_WPM_OUT_OF_RANGE", identity: str = "a" * 64) -> SectionPacingAnalysis:
    return SectionPacingAnalysis(
        report={
            "status": "regeneration_required",
            "remediation_report_sha256": "b" * 64,
            "sections": [
                {
                    "section_id": "sec_001",
                    "status": "regeneration_required",
                    "severity": "regeneration_required",
                    "issue_code": issue_code,
                    "correction_relative": correction,
                    "remediation_identity": identity,
                    "raw_wpm": 185.0,
                    "target_wpm": 174,
                }
            ],
        },
        raw_frames_by_index=[b"raw"],
        raw_sample_counts=[48_000],
    )


def _accepted(identity: str = "a" * 64) -> list[dict]:
    return [{
        "section_id": "sec_001",
        "remediation_identity": identity,
        "remediation_report_sha256": "b" * 64,
        "raw_wpm": 185.0,
        "target_wpm": 174,
        "wpm_deviation_percent": 6.2,
        "accepted_at": "2026-09-03T00:00:00Z",
    }]


def test_exact_wpm_only_deviation_can_be_manually_accepted_inside_atempo_fence() -> None:
    result = apply_manual_pacing_acceptances(_analysis(), _accepted())
    section = result.report["sections"][0]
    assert result.report["status"] == "pass"
    assert result.report["accepted_pacing_deviation_section_ids"] == ["sec_001"]
    assert result.report["manual_pacing_acceptance_policy_version"] == VOICE_PACING_MANUAL_ACCEPTANCE_POLICY_VERSION
    assert section["status"] == "pass"
    assert section["manual_pacing_acceptance"]["remediation_identity"] == "a" * 64
    assert result.report["remediation_report_sha256"] != "b" * 64
    assert validate_remediation_report_integrity(result.report) is True


def test_manual_acceptance_is_fenced_to_exact_remediation_identity() -> None:
    result = apply_manual_pacing_acceptances(_analysis(), _accepted("c" * 64))
    assert result.report["status"] == "regeneration_required"
    assert result.report["accepted_pacing_deviation_section_ids"] == []


def test_manual_acceptance_never_bypasses_atempo_safety_fence() -> None:
    result = apply_manual_pacing_acceptances(
        _analysis(correction=0.16, issue_code="VOICE_PACING_CORRECTION_OUT_OF_RANGE"),
        _accepted(),
    )
    assert result.report["status"] == "regeneration_required"
    assert result.report["sections"][0]["status"] == "regeneration_required"


def test_workspace_projection_matches_only_exact_manual_acceptance_lineage() -> None:
    remediation = _analysis().report["sections"][0]
    assert exact_manual_pacing_acceptance_matches(remediation, "b" * 64, _accepted()) is True
    assert exact_manual_pacing_acceptance_matches(remediation, "d" * 64, _accepted()) is False
    assert exact_manual_pacing_acceptance_matches(remediation, "b" * 64, _accepted("c" * 64)) is False



def test_section_local_acceptance_survives_sibling_report_revision() -> None:
    analysis = _analysis(identity="new-remediation")
    analysis.report["remediation_report_sha256"] = "new-report"
    analysis.report["sections"][0]["section_acceptance_identity_sha256"] = "section-lineage"
    accepted = _accepted("old-remediation")
    accepted[0]["remediation_report_sha256"] = "old-report"
    accepted[0]["section_acceptance_identity_sha256"] = "section-lineage"

    result = apply_manual_pacing_acceptances(analysis, accepted)

    assert result.report["status"] == "pass"
    assert result.report["accepted_pacing_deviation_section_ids"] == ["sec_001"]


def test_elevenlabs_section_acceptance_survives_mixed_policy_transition() -> None:
    analysis = _analysis(identity="new-remediation")
    analysis.report["remediation_report_sha256"] = "post-regeneration-report"
    analysis.report["sections"][0].update(
        {
            "provider_pacing_policy_version": "elevenlabs_voice_first_section_feedback_v1",
            "section_acceptance_identity_sha256": "section-lineage",
        }
    )
    analysis.report["sections"].append(
        {
            "section_id": "sec_002",
            "status": "pass",
            "provider_pacing_policy_version": "elevenlabs_relative_script_average_v1",
            "section_acceptance_identity_sha256": "sibling-lineage",
        }
    )
    accepted = _accepted("old-remediation")
    accepted[0]["remediation_report_sha256"] = "initial-report"
    accepted[0]["section_acceptance_identity_sha256"] = "section-lineage"

    result = apply_manual_pacing_acceptances(analysis, accepted)

    assert result.report["status"] == "pass"
    assert result.report["accepted_pacing_deviation_section_ids"] == ["sec_001"]
    accepted_section = result.report["sections"][0]["manual_pacing_acceptance"]
    assert accepted_section["section_acceptance_identity_sha256"] == "section-lineage"


def test_section_local_acceptance_is_invalidated_when_exact_section_lineage_changes() -> None:
    analysis = _analysis(identity="same-remediation")
    analysis.report["remediation_report_sha256"] = "new-report"
    analysis.report["sections"][0]["section_acceptance_identity_sha256"] = "new-section-lineage"
    accepted = _accepted("same-remediation")
    accepted[0]["remediation_report_sha256"] = "old-report"
    accepted[0]["section_acceptance_identity_sha256"] = "old-section-lineage"

    result = apply_manual_pacing_acceptances(analysis, accepted)

    assert result.report["status"] == "regeneration_required"
    assert result.report["accepted_pacing_deviation_section_ids"] == []


def test_legacy_acceptance_is_upgraded_before_sibling_regeneration() -> None:
    source_report = {
        "status": "regeneration_required",
        "remediation_report_sha256": "b" * 64,
        "sections": [{
            "section_id": "sec_001",
            "segment_ids": ["seg_001"],
            "planned_narration_wpm": 174,
            "target_wpm": 174,
            "provider_pacing_control": "relative_multiplier",
            "provider_pacing_value": 1.0,
            "provider_pacing_policy_version": "policy-v1",
            "provider_pacing_clamped": False,
            "status": "regeneration_required",
            "remediation_identity": "a" * 64,
        }],
    }
    complete_segments = [{
        "segment_id": "seg_001",
        "segment_audio_artifact_id": "artifact_audio_seg_001",
        "raw_segment_synthesis_identity_sha256": "c" * 64,
        "sample_count": 48_000,
    }]

    carried = carry_forward_manual_pacing_acceptances(
        source_report=source_report,
        accepted_records=_accepted(),
        complete_segments=complete_segments,
        source_script_revision_id="artifact_script_current",
        provider_identity_sha256="d" * 64,
        exclude_section_id="sec_999",
    )

    assert [item["section_id"] for item in carried] == ["sec_001"]
    assert isinstance(carried[0].get("section_acceptance_identity_sha256"), str)
    assert len(carried[0]["section_acceptance_identity_sha256"]) == 64


def test_manual_acceptance_rehashes_all_section_identities_and_preserves_remaining_failure() -> None:
    analysis = _analysis(identity="a" * 64)
    analysis.report["sections"][0]["section_acceptance_identity_sha256"] = "1" * 64
    analysis.report["sections"].append({
        "section_id": "sec_002",
        "status": "regeneration_required",
        "severity": "regeneration_required",
        "issue_code": "VOICE_PACING_WPM_OUT_OF_RANGE",
        "correction_relative": 0.08,
        "remediation_identity": "b" * 64,
        "section_acceptance_identity_sha256": "2" * 64,
        "feedback_supported": True,
        "recommended_provider_pacing_value": 1.1,
        "recommendation_policy_version": KOKORO_FLOW_VERSION,
        "raw_wpm": 185.0,
        "target_wpm": 174,
    })
    rehash_remediation_report(analysis.report)
    before = {section["section_id"]: section["remediation_identity"] for section in analysis.report["sections"]}
    accepted = _accepted()
    accepted[0]["section_acceptance_identity_sha256"] = "1" * 64

    result = apply_manual_pacing_acceptances(analysis, accepted)

    assert validate_remediation_report_integrity(result.report) is True
    after = {section["section_id"]: section for section in result.report["sections"]}
    assert after["sec_001"]["status"] == "pass"
    assert after["sec_001"]["remediation_identity"] != before["sec_001"]
    assert after["sec_002"]["status"] == "regeneration_required"
    assert after["sec_002"]["remediation_identity"] == before["sec_002"]
