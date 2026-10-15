from __future__ import annotations

from types import SimpleNamespace

from app.modules.v4.durable_voice_service import DurableVoiceService


def _project():
    return SimpleNamespace(
        project_id="project_voice_projection",
        script_artifact_id="artifact_script_current",
        narration_audio_artifact_id="artifact_audio_approved",
        narration_word_timing_map_artifact_id="artifact_timing_approved",
    )


def _report(source_operation_id: str, section_ids: tuple[str, ...]) -> dict:
    return {
        "status": "regeneration_required",
        "source_operation_id": source_operation_id,
        "remediation_report_sha256": f"report-{source_operation_id}",
        "sections": [
            {"section_id": section_id, "status": "regeneration_required"}
            for section_id in section_ids
        ],
    }


def _operation(
    operation_id: str,
    *,
    status: str,
    error_code: str | None = None,
    report: dict | None = None,
    operation_type: str = "generate_all",
    source_remediation_operation_id: str | None = None,
):
    return SimpleNamespace(
        operation_id=operation_id,
        project_id="project_voice_projection",
        operation_type=operation_type,
        status=status,
        error_code=error_code,
        script_artifact_id="artifact_script_current",
        expected_narration_audio_artifact_id="artifact_audio_approved",
        expected_narration_word_timing_map_artifact_id="artifact_timing_approved",
        request_json=(
            {"source_remediation_operation_id": source_remediation_operation_id}
            if source_remediation_operation_id is not None
            else {}
        ),
        result_json={"pacing_remediation_report": report} if report is not None else {},
        error_details_json=report if status == "failed" else None,
    )


def _service(operations):
    service = object.__new__(DurableVoiceService)
    by_id = {item.operation_id: item for item in operations}
    service.operations = SimpleNamespace(
        list_for_project=lambda _project_id: operations,
        get=lambda operation_id: by_id.get(operation_id),
    )
    return service


def test_manual_acceptance_resume_keeps_current_report_instead_of_resurfacing_older_sections() -> None:
    old_report = _report("op_old", ("sec_005", "sec_006"))
    current_report = _report("op_current", ("sec_006",))
    service = _service([
        _operation("op_current", status="finalizing", report=current_report),
        _operation(
            "op_old",
            status="failed",
            error_code="VOICE_PACING_REGENERATION_REQUIRED",
            report=old_report,
        ),
    ])

    assert service._latest_pacing_remediation_report(_project()) is current_report


def test_completed_newer_voice_operation_suppresses_older_failed_remediation_report() -> None:
    old_report = _report("op_old", ("sec_005", "sec_006"))
    service = _service([
        _operation("op_completed", status="completed"),
        _operation(
            "op_old",
            status="failed",
            error_code="VOICE_PACING_REGENERATION_REQUIRED",
            report=old_report,
        ),
    ])

    assert service._latest_pacing_remediation_report(_project()) is None


def test_newer_non_pacing_failure_suppresses_older_pacing_remediation_report() -> None:
    old_report = _report("op_old", ("sec_005", "sec_006"))
    service = _service([
        _operation("op_provider_failed", status="failed", error_code="VOICE_PROVIDER_FAILED"),
        _operation(
            "op_old",
            status="failed",
            error_code="VOICE_PACING_REGENERATION_REQUIRED",
            report=old_report,
        ),
    ])

    assert service._latest_pacing_remediation_report(_project()) is None


def test_cancelled_attempt_does_not_hide_previous_actionable_remediation() -> None:
    old_report = _report("op_old", ("sec_006",))
    service = _service([
        _operation("op_cancelled", status="cancelled"),
        _operation(
            "op_old",
            status="failed",
            error_code="VOICE_PACING_REGENERATION_REQUIRED",
            report=old_report,
        ),
    ])

    assert service._latest_pacing_remediation_report(_project()) is old_report



def test_active_section_regeneration_keeps_source_report_projected() -> None:
    source_report = _report("op_source", ("sec_001", "sec_002"))
    source = _operation(
        "op_source",
        status="failed",
        error_code="VOICE_PACING_REGENERATION_REQUIRED",
        report=source_report,
    )
    active = _operation(
        "op_regen",
        status="running",
        operation_type="regenerate_section",
        source_remediation_operation_id="op_source",
    )
    service = _service([active, source])

    assert service._latest_pacing_remediation_report(_project()) is source_report
