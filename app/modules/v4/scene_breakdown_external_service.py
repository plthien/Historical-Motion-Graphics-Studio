from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.external_authoring import (
    extract_reserved_artifact_ids,
    parse_external_json,
    render_external_prompt,
    require_source_values,
    validation_success,
)
from app.core.hashing import canonical_json_hash
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.modules.v4.scene_breakdown_input_builder import build_section_input, extract_ordered_sections
from app.modules.v4.beat_timing_map import build_scene_timing_context
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.scene_breakdown_validator import SceneBreakdownValidationError, validate_and_normalize_section
from app.modules.v4.scene_plan_assembler import assemble_scene_plan
from app.modules.v4.visual_plan_assembler import (
    VisualPlanProjectionError,
    assemble_visual_plan_from_scene_breakdown,
)
from app.repositories import SceneVisualPlanCommitGuard
from app.services.external_authoring_reservations import ExternalAuthoringReservationService, SCENE_STAGE


logger = logging.getLogger(__name__)


class ExternalSceneBreakdownService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.service = SceneBreakdownServiceV4(session)
        self.settings = self.service.artifacts.settings
        self.reservations = ExternalAuthoringReservationService(session)

    def _current_context(
        self,
        project_id: str,
        *,
        validate_current_pair: bool = True,
    ) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        project = self.service._project(project_id)
        if not project.script_artifact_id or not project.script_analysis_artifact_id or not project.style_bible_artifact_id:
            raise AppError(
                "SCENE_BREAKDOWN_UPSTREAM_REQUIRED",
                "Current Script, Script Analysis and text-approved Style Bible are required.",
                409,
                retryable=False,
            )
        request = {
            "script_artifact_id": project.script_artifact_id,
            "script_analysis_artifact_id": project.script_analysis_artifact_id,
            "style_bible_artifact_id": project.style_bible_artifact_id,
            "expected_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "expected_visual_plan_artifact_id": project.visual_plan_artifact_id,
        }
        script, analysis, style = self.service.validate_current_lineage(
            project,
            request,
            validate_current_pair=validate_current_pair,
        )
        _audio, word_timing = self.service.approved_voice_timing(project)
        return project, script, analysis, style, word_timing

    @staticmethod
    def _merge_reference_catalogs(catalogs: list[dict[str, Any]]) -> dict[str, Any]:
        merged: dict[str, dict[str, Any]] = {
            "characters": {},
            "locations": {},
            "props": {},
            "subjects": {},
            "motifs": {},
        }
        for catalog in catalogs:
            for category in merged:
                items = catalog.get(category)
                if not isinstance(items, dict):
                    continue
                for ref_id, descriptor in items.items():
                    candidate = deepcopy(descriptor)
                    existing = merged[category].get(ref_id)
                    if existing is None:
                        merged[category][ref_id] = candidate
                        continue
                    for key in ("label", "description", "visual_traits", "continuity_notes", "physical_constraints"):
                        if existing.get(key) != candidate.get(key):
                            raise AppError(
                                "SCENE_BREAKDOWN_REFERENCE_CATALOG_CONFLICT",
                                "The same semantic reference has inconsistent definitions across Script sections.",
                                422,
                                retryable=False,
                                details={"category": category, "reference_id": ref_id, "field": key},
                            )
                    evidence: list[str] = []
                    seen: set[str] = set()
                    for text in list(existing.get("evidence_script_texts", [])) + list(candidate.get("evidence_script_texts", [])):
                        value = str(text)
                        if value not in seen:
                            seen.add(value)
                            evidence.append(value)
                    existing["evidence_script_texts"] = evidence[:20]
                    if category != "motifs":
                        presence_ranges: list[dict[str, Any]] = []
                        seen_ranges: set[tuple[str, str, str]] = set()
                        for presence_range in list(existing["presence_ranges"]) + list(candidate["presence_ranges"]):
                            if not isinstance(presence_range, dict):
                                continue
                            key = (
                                str(presence_range.get("section_id") or ""),
                                str(presence_range.get("start_quote") or ""),
                                str(presence_range.get("end_quote") or ""),
                            )
                            if all(key) and key not in seen_ranges:
                                seen_ranges.add(key)
                                presence_ranges.append(deepcopy(presence_range))
                        existing["presence_ranges"] = presence_ranges[:40]
        return merged

    def _full_input(
        self, project: Any, script: dict[str, Any], analysis: dict[str, Any], style: dict[str, Any], word_timing: dict[str, Any]
    ) -> tuple[dict[str, Any], list[Any]]:
        sections = extract_ordered_sections(script, analysis)
        section_payloads: list[dict[str, Any]] = []
        catalogs: list[dict[str, Any]] = []
        visual_style: dict[str, Any] | None = None
        for section in sections:
            item = build_section_input(
                section=section,
                script_analysis=analysis,
                style_bible=style,
                carry_over=None,
                video_pacing_profile=script["video_pacing_profile"],
            )
            section_payloads.append(
                {
                    "section_context": item["section_context"],
                    "analysis_guidance": item["analysis_guidance"],
                    "visual_cadence_guidance": item["visual_cadence_guidance"],
                }
            )
            catalogs.append(item["visual_context"]["reference_catalog"])
            if visual_style is None:
                visual_style = deepcopy(item["visual_context"]["style"])
            elif visual_style != item["visual_context"]["style"]:
                raise AppError(
                    "SCENE_BREAKDOWN_STYLE_CONTEXT_CONFLICT",
                    "Global visual style changed while building the full Script prompt.",
                    409,
                    retryable=False,
                )
        channel_dna = project.channel_dna_json
        narration_identity = channel_dna.get("narration_identity") if isinstance(channel_dna, dict) else None
        if not isinstance(narration_identity, dict):
            raise AppError(
                "SCENE_BREAKDOWN_NARRATION_IDENTITY_REQUIRED",
                "The frozen Project Channel DNA does not contain a valid narration identity.",
                409,
                retryable=False,
            )
        payload = {
            "script_context": {
                "title": script["title"],
                "sections": section_payloads,
            },
            "narration_context": {
                "narration_identity": deepcopy(narration_identity),
            },
            "timing_context": build_scene_timing_context(
                project_id=project.project_id,
                script_artifact_id=str(project.script_artifact_id),
                narration_audio_artifact_id=str(project.narration_audio_artifact_id),
                narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id),
                script=script,
                word_timing_map=word_timing,
            ),
            "visual_context": {
                "style": visual_style or {},
                "reference_catalog": self._merge_reference_catalogs(catalogs),
                "visual_context_catalog": deepcopy(analysis["visual_context_catalog"]),
            },
        }
        payload = self.service.v4.validate(
            "scene_breakdown_full_llm_input_v2.schema.json",
            payload,
            error_code="SCENE_BREAKDOWN_FULL_INPUT_INVALID",
        )
        return payload, sections

    def _prompt_package(self, project_id: str) -> dict[str, Any]:
        project, script, analysis, style, word_timing = self._current_context(project_id)
        payload, _sections = self._full_input(project, script, analysis, style, word_timing)
        prompt_dir = self.settings.root_dir / "contracts" / "prompts" / "v4" / "scene_breakdown_full"
        system_prompt = (prompt_dir / "system.md").read_text(encoding="utf-8")
        user_prompt = (prompt_dir / "user.md").read_text(encoding="utf-8").replace(
            "{{input_json}}", json.dumps(payload, ensure_ascii=False, indent=2)
        )
        scene_plan_id = self.service.artifacts.preallocate_artifact_id()
        visual_plan_id = self.service.artifacts.preallocate_artifact_id()
        output_schema = deepcopy(
            self.service.v4.load_schema("scene_breakdown_full_llm_output_v1.schema.json")
        )
        output_schema["properties"]["artifact_ids"]["properties"]["scene_plan"]["const"] = scene_plan_id
        output_schema["properties"]["artifact_ids"]["properties"]["visual_plan"]["const"] = visual_plan_id
        package = render_external_prompt(
            stage="scene_breakdown_full_script",
            prompt_version="scene_breakdown_full_external_v11",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema_name="scene_breakdown_full_llm_output_v1.schema.json",
            output_schema=output_schema,
            artifact_ids={"scene_plan": scene_plan_id, "visual_plan": visual_plan_id},
            output_filename="scene_breakdown.json",
        )
        self.reservations.replace_pending(
            project_id=project_id,
            stage=SCENE_STAGE,
            primary_artifact_id=scene_plan_id,
            paired_artifact_id=visual_plan_id,
            source={
                "script_artifact_id": project.script_artifact_id,
                "script_analysis_artifact_id": project.script_analysis_artifact_id,
                "style_bible_artifact_id": project.style_bible_artifact_id,
                "visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
                "narration_audio_artifact_id": project.narration_audio_artifact_id,
                "narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                "previous_scene_plan_artifact_id": project.scene_plan_artifact_id,
                "previous_visual_plan_artifact_id": project.visual_plan_artifact_id,
                "prompt_version": package["prompt_version"],
                "prompt_sha256": "sha256:" + sha256(package["prompt_text"].encode("utf-8")).hexdigest(),
                "raw_schema_id": str(output_schema.get("$id")),
                "raw_schema_sha256": "sha256:" + canonical_json_hash(output_schema),
                "channel_dna_sha256": "sha256:" + canonical_json_hash(project.channel_dna_json),
                "prompt_package": deepcopy(package),
            },
        )
        return package

    def build_prompt(self, project_id: str) -> dict[str, Any]:
        return self._prompt_package(project_id)

    def get_pending_prompt(self, project_id: str) -> dict[str, Any]:
        reservation = self.reservations.pending_for(project_id=project_id, stage=SCENE_STAGE)
        if reservation is None:
            return {"generated": False, "prompt_package": None, "request_input": None}
        source = self.reservations.source(reservation)
        try:
            project, _script, _analysis, _style, _word_timing = self._current_context(project_id)
        except AppError:
            return {"generated": False, "prompt_package": None, "request_input": None}
        current = {
            "script_artifact_id": project.script_artifact_id,
            "script_analysis_artifact_id": project.script_analysis_artifact_id,
            "style_bible_artifact_id": project.style_bible_artifact_id,
            "visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
            "narration_audio_artifact_id": project.narration_audio_artifact_id,
            "narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            "previous_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "previous_visual_plan_artifact_id": project.visual_plan_artifact_id,
            "channel_dna_sha256": "sha256:" + canonical_json_hash(project.channel_dna_json),
        }
        if any(source.get(key) != value for key, value in current.items()):
            return {"generated": False, "prompt_package": None, "request_input": None}
        package = source.get("prompt_package")
        if not isinstance(package, dict):
            return {"generated": False, "prompt_package": None, "request_input": None}
        return {"generated": True, "prompt_package": deepcopy(package), "request_input": None}

    @staticmethod
    def _carry_from_fragment(fragment: dict[str, Any] | None) -> dict[str, Any] | None:
        return SceneBreakdownServiceV4._carry_from_fragment(fragment)

    def _validate_import(
        self, project_id: str, request_payload: dict[str, Any]
    ) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], Any, dict[str, Any]]:
        raw = parse_external_json(str(request_payload.get("result_json") or ""))
        scene_plan_id, visual_plan_id = extract_reserved_artifact_ids(raw, stage=SCENE_STAGE)
        reservation = self.reservations.require(
            project_id=project_id,
            stage=SCENE_STAGE,
            primary_artifact_id=scene_plan_id,
            paired_artifact_id=visual_plan_id,
        )
        source = self.reservations.source(reservation)
        project, script, analysis, style, word_timing = self._current_context(project_id)
        require_source_values(
            expected=source,
            current={
                "script_artifact_id": project.script_artifact_id,
                "script_analysis_artifact_id": project.script_analysis_artifact_id,
                "style_bible_artifact_id": project.style_bible_artifact_id,
                "visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
                "narration_audio_artifact_id": project.narration_audio_artifact_id,
                "narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
                "previous_scene_plan_artifact_id": project.scene_plan_artifact_id,
                "previous_visual_plan_artifact_id": project.visual_plan_artifact_id,
                "channel_dna_sha256": "sha256:" + canonical_json_hash(project.channel_dna_json),
            },
            fields=[
                "script_artifact_id",
                "script_analysis_artifact_id",
                "style_bible_artifact_id",
                "visual_reference_approval_set_artifact_id",
                "narration_audio_artifact_id",
                "narration_word_timing_map_artifact_id",
                "previous_scene_plan_artifact_id",
                "previous_visual_plan_artifact_id",
                "channel_dna_sha256",
            ],
            message="Scene Breakdown upstream artifacts changed after this prompt was built.",
        )
        _full_input, sections = self._full_input(project, script, analysis, style, word_timing)
        raw = self.service.v4.validate(
            "scene_breakdown_full_llm_output_v1.schema.json",
            raw,
            error_code="SCENE_BREAKDOWN_FULL_OUTPUT_INVALID",
        )
        output_sections = raw["sections"]
        expected_ids = [item.section_id for item in sections]
        actual_ids = [item["section_id"] for item in output_sections]
        if actual_ids != expected_ids:
            raise AppError(
                "SCENE_BREAKDOWN_SECTION_COVERAGE_INVALID",
                "Full Scene Breakdown output must contain every Script section exactly once and in order.",
                422,
                retryable=False,
                details={
                    "errors": [
                        {
                            "path": "$.sections",
                            "code": "section_order_mismatch",
                            "message": f"Expected {expected_ids}; received {actual_ids}.",
                        }
                    ]
                },
            )
        fragments: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for section, output in zip(sections, output_sections, strict=True):
            section_input = build_section_input(
                section=section,
                script_analysis=analysis,
                style_bible=style,
                carry_over=self._carry_from_fragment(previous),
                video_pacing_profile=script["video_pacing_profile"],
            )
            try:
                fragment = validate_and_normalize_section(
                    raw_output={"scenes": deepcopy(output["scenes"])},
                    section=section,
                    section_input=section_input,
                    script_analysis=analysis,
                    style_bible=style,
                    word_timing_map=word_timing,
                )
            except SceneBreakdownValidationError as exc:
                errors = exc.report.get("errors") if isinstance(exc.report, dict) else None
                raise AppError(
                    "SCENE_BREAKDOWN_FULL_VALIDATION_FAILED",
                    f"Scene Breakdown section '{section.section_id}' failed validation.",
                    422,
                    retryable=False,
                    details={"section_id": section.section_id, "errors": errors or []},
                ) from exc
            fragments.append(fragment)
            previous = fragment
        return project, script, analysis, style, fragments, reservation, source

    @staticmethod
    def _external_execution_audit(
        source: dict[str, Any],
        *,
        scene_plan_id: str,
        visual_plan_id: str,
    ) -> dict[str, Any]:
        """Materialize the exact current external-authoring prompt/schema audit."""

        required = ("prompt_version", "prompt_sha256", "raw_schema_id", "raw_schema_sha256")
        missing = [field for field in required if not isinstance(source.get(field), str) or not source[field]]
        if missing:
            raise AppError(
                "SCENE_BREAKDOWN_RESERVATION_AUDIT_INVALID",
                "Current Scene Breakdown reservation is missing required prompt/schema audit metadata.",
                409,
                retryable=False,
                details={"missing_fields": missing},
            )
        return {
            "mode": "external_import",
            "authoring_artifact_ids": {
                "scene_plan": scene_plan_id,
                "visual_plan": visual_plan_id,
            },
            **{field: source[field] for field in required},
        }

    def _materialize_import(
        self,
        *,
        project_id: str,
        project: Any,
        style: dict[str, Any],
        fragments: list[dict[str, Any]],
        reservation: Any,
        source: dict[str, Any],
        run_artifact_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], SceneVisualPlanCommitGuard]:
        scene_plan_id = reservation.primary_artifact_id
        visual_plan_id = str(reservation.paired_artifact_id)
        run_id = generate_id("import")
        section_results: list[dict[str, Any]] = []
        for fragment in fragments:
            report = fragment.get("backend_qc", {})
            section_results.append(
                {
                    "section_id": fragment["section_id"],
                    "status": "passed",
                    "normalized_fragment": deepcopy(fragment),
                    "failure_artifact_id": None,
                    "validation_summary": {
                        "critical_status": "passed",
                        "warning_count": len(report.get("warnings", []))
                        + len(report.get("timing_warnings", [])),
                    },
                }
            )
        run = {
            "artifact_type": "scene_breakdown_run",
            "schema_version": "2.0",
            "run_id": run_id,
            "operation": "full_import",
            "project_id": project_id,
            "source_script_revision_id": str(project.script_artifact_id),
            "source_script_analysis_revision_id": str(project.script_analysis_artifact_id),
            "source_style_bible_revision_id": str(project.style_bible_artifact_id),
            "expected_previous_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "source_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "expected_previous_visual_plan_artifact_id": project.visual_plan_artifact_id,
            "source_visual_plan_artifact_id": project.visual_plan_artifact_id,
            "source_visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
            "frozen_execution": self._external_execution_audit(
                source,
                scene_plan_id=scene_plan_id,
                visual_plan_id=visual_plan_id,
            ),
            "status": "completed",
            "section_results": section_results,
            "created_at": utc_now_iso(),
        }
        scene_plan = assemble_scene_plan(
            project_id=project_id,
            revision_id=scene_plan_id,
            parent_revision_id=project.scene_plan_artifact_id,
            revision_operation="full_generation",
            source_script_revision_id=str(project.script_artifact_id),
            source_script_analysis_revision_id=str(project.script_analysis_artifact_id),
            source_style_bible_revision_id=str(project.style_bible_artifact_id),
            source_run_artifact_id=run_artifact_id,
            ordered_sections=fragments,
            status="draft_ready",
        )
        approval_id, bindings = self.service.visual_reference_loader.approved_bindings(project)
        if approval_id != project.style_bible_visual_reference_approval_set_artifact_id:
            raise AppError(
                "SCENE_BREAKDOWN_VISUAL_REFERENCE_APPROVAL_STALE",
                "The visual-reference approval set changed during import.",
                409,
                retryable=False,
            )
        visual_plan = assemble_visual_plan_from_scene_breakdown(
            scene_plan=scene_plan,
            text_approved_style_bible=style,
            ordered_sections=fragments,
            visual_plan_revision_id=visual_plan_id,
            parent_visual_plan_revision_id=project.visual_plan_artifact_id,
            source_run_artifact_id=run_artifact_id,
            revision_operation="full_generation",
            status="draft_ready",
            canonical_reference_bindings=bindings,
            source_visual_reference_approval_set_artifact_id=approval_id,
        )
        guard = SceneVisualPlanCommitGuard(
            script_artifact_id=str(project.script_artifact_id),
            script_analysis_artifact_id=str(project.script_analysis_artifact_id),
            style_bible_artifact_id=str(project.style_bible_artifact_id),
            visual_reference_approval_set_artifact_id=approval_id,
            narration_audio_artifact_id=str(project.narration_audio_artifact_id),
            narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id),
            previous_scene_plan_artifact_id=project.scene_plan_artifact_id,
            previous_visual_plan_artifact_id=project.visual_plan_artifact_id,
        )
        self.service.v4.validate(
            "scene_breakdown_run_v1.schema.json",
            run,
            error_code="SCENE_BREAKDOWN_RUN_INVALID",
        )
        self.service.v4.validate(
            "scene_plan_v1.schema.json",
            scene_plan,
            error_code="SCENE_PLAN_INVALID",
        )
        self.service.v4.validate(
            "visual_plan_v1.schema.json",
            visual_plan,
            error_code="VISUAL_PLAN_INVALID",
        )
        return run, scene_plan, visual_plan, guard

    def _materialize_import_safely(
        self,
        *,
        project_id: str,
        project: Any,
        style: dict[str, Any],
        fragments: list[dict[str, Any]],
        reservation: Any,
        source: dict[str, Any],
        run_artifact_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], SceneVisualPlanCommitGuard]:
        try:
            return self._materialize_import(
                project_id=project_id,
                project=project,
                style=style,
                fragments=fragments,
                reservation=reservation,
                source=source,
                run_artifact_id=run_artifact_id,
            )
        except AppError:
            raise
        except VisualPlanProjectionError as exc:
            raise AppError(
                exc.code,
                exc.message,
                422,
                retryable=False,
                details={
                    "errors": [
                        {
                            "path": "$.sections",
                            "code": exc.code.lower(),
                            "message": exc.message,
                        }
                    ]
                },
            ) from exc
        except ValidationError as exc:
            errors = []
            for issue in exc.errors(include_url=False, include_context=False, include_input=False):
                path = "$" + "".join(
                    f"[{part}]" if isinstance(part, int) else f".{part}"
                    for part in issue.get("loc", ())
                )
                errors.append(
                    {
                        "path": path,
                        "code": str(issue.get("type") or "canonical_validation_error"),
                        "message": str(issue.get("msg") or "Canonical plan validation failed."),
                    }
                )
            raise AppError(
                "SCENE_BREAKDOWN_CANONICAL_VALIDATION_FAILED",
                "Scene Breakdown passed raw validation but could not form canonical plans.",
                422,
                retryable=False,
                details={"errors": errors},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected Scene Breakdown materialization failure for %s", project_id)
            raise AppError(
                "SCENE_BREAKDOWN_IMPORT_MATERIALIZATION_FAILED",
                "Scene Breakdown passed raw validation but canonical plans could not be materialized.",
                500,
                retryable=False,
                details={
                    "phase": "canonical_plan_materialization",
                    "exception_type": exc.__class__.__name__,
                },
            ) from exc

    def validate_import(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        project, _script, _analysis, style, fragments, reservation, source = self._validate_import(
            project_id, request_payload
        )
        self._materialize_import_safely(
            project_id=project_id,
            project=project,
            style=style,
            fragments=fragments,
            reservation=reservation,
            source=source,
            run_artifact_id=self.service.artifacts.preallocate_artifact_id(),
        )
        return validation_success(
            summary={
                "section_count": len(fragments),
                "scene_count": sum(len(item["scenes"]) for item in fragments),
                "beat_count": sum(
                    len(scene["beats"])
                    for item in fragments
                    for scene in item["scenes"]
                ),
            }
        )

    def import_result(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        project, _script, _analysis, style, fragments, reservation, source = self._validate_import(
            project_id, request_payload
        )
        run_artifact_id = self.service.artifacts.preallocate_artifact_id()
        run, scene_plan, visual_plan, guard = self._materialize_import_safely(
            project_id=project_id,
            project=project,
            style=style,
            fragments=fragments,
            reservation=reservation,
            source=source,
            run_artifact_id=run_artifact_id,
        )
        try:
            self.service.publish_scene_visual_plan_with_completed_run(
                project_id=project_id,
                run_artifact_id=run_artifact_id,
                run_payload=run,
                scene_plan=scene_plan,
                visual_plan=visual_plan,
                guard=guard,
                source_job_id=None,
                before_commit=lambda: self.reservations.mark_consumed(reservation),
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected Scene Breakdown import publication failure for %s", project_id)
            raise AppError(
                "SCENE_BREAKDOWN_IMPORT_PERSISTENCE_FAILED",
                "Scene Breakdown was valid but could not be persisted.",
                500,
                retryable=True,
                details={
                    "phase": "paired_artifact_publication",
                    "exception_type": exc.__class__.__name__,
                },
            ) from exc
        return {
            "scene_plan_artifact_id": reservation.primary_artifact_id,
            "scene_plan": scene_plan,
            "visual_plan_artifact_id": str(reservation.paired_artifact_id),
            "visual_plan": visual_plan,
        }
