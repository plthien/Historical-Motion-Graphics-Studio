from __future__ import annotations

from copy import deepcopy
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.external_authoring import (
    extract_reserved_artifact_ids,
    parse_external_json,
    render_external_prompt,
    require_source_values,
    validation_success,
)
from app.core.config import get_settings
from app.core.hashing import canonical_json_hash
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactType
from app.modules.platform.services import AuthoringService, build_script_section_ranges
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_analysis_input_builder import build_script_analysis_input
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.repositories import ArtifactRepository, ProjectRepository
from app.repositories.core import ScriptAnalysisPairCommitGuard
from app.services.external_authoring_reservations import ExternalAuthoringReservationService, SCRIPT_STAGE
from app.services.artifacts import (
    ArtifactService,
    StagedArtifact,
    cleanup_staged_artifact_safely,
    rollback_safely,
)

logger = logging.getLogger(__name__)


def _runtime_input_for_count_agnostic_import(
    runtime_input: dict[str, Any],
    raw_script: dict[str, Any],
) -> dict[str, Any]:
    """Build an import-only normalization view that does not enforce section cardinality.

    Prompt-generation/runtime policy remains unchanged. This copy exists only so
    external Script & Analysis JSON can be normalized when the authored section
    count differs from the prompt-time recommendation/plan. Backend-plan role
    validation is preserved for every position that existed in the original plan.
    """

    adjusted = deepcopy(runtime_input)
    constraints = adjusted.get("authoring_constraints")
    sections = raw_script.get("script_sections")
    if not isinstance(constraints, dict) or not isinstance(sections, list):
        return adjusted

    mode = constraints.get("section_count_mode")
    if mode == "llm_decides":
        section_count = len(sections)
        constraints["minimum_section_count"] = section_count
        constraints["maximum_section_count"] = section_count
        return adjusted

    if mode == "backend_plan":
        existing_plan = constraints.get("section_plan")
        if not isinstance(existing_plan, list):
            return adjusted
        adjusted_plan: list[dict[str, Any]] = []
        for index, section in enumerate(sections):
            if index < len(existing_plan) and isinstance(existing_plan[index], dict):
                plan_item = deepcopy(existing_plan[index])
            else:
                plan_item = {
                    "section_index": index + 1,
                    "section_role": (
                        str(section.get("section_role") or "")
                        if isinstance(section, dict)
                        else ""
                    ),
                }
            plan_item["section_index"] = index + 1
            adjusted_plan.append(plan_item)
        constraints["section_plan"] = adjusted_plan

    return adjusted


class ExternalScriptAnalysisService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()
        self.projects = ProjectRepository(session)
        self.artifacts_repo = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifacts_repo, self.settings)
        self.authoring = AuthoringService(session)
        self.v4 = V4ContractValidator()
        self.semantic_validator = ScriptAnalysisSemanticValidator()
        self.reservations = ExternalAuthoringReservationService(session)

    def _project_and_runtime_input(
        self, project_id: str, request_payload: dict[str, Any]
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        project = self.authoring.get_project_required(project_id)
        self.authoring._validate_project_selected_topic_state(project)
        self.authoring.validator.validate(
            "generate_script_http_request.schema.json",
            request_payload,
            error_code="SCRIPT_REQUEST_INVALID",
        )
        if not project.selected_topic_ideas_artifact_id or not project.selected_topic_idea_id:
            raise AppError(
                "TOPIC_NOT_SELECTED",
                "A topic idea must be explicitly selected before building Script & Analysis.",
                409,
                retryable=False,
            )
        if project.selected_topic_ideas_artifact_id != project.topic_ideas_artifact_id:
            raise AppError(
                "SELECTED_TOPIC_STALE",
                "The selected topic idea belongs to an outdated Topic Ideas artifact.",
                409,
                retryable=False,
            )
        requested_idea = request_payload.get("selected_topic_idea_id")
        if requested_idea and requested_idea != project.selected_topic_idea_id:
            raise AppError(
                "TOPIC_IDEA_MISMATCH",
                "The requested selected topic does not match the project's current selection.",
                409,
                retryable=False,
            )
        topic_artifact = self.authoring.loader.load_topic_ideas(
            project, project.selected_topic_ideas_artifact_id, require_current=True
        )
        topic_idea = self.authoring._resolve_topic_idea_exactly_once(
            topic_artifact, project.selected_topic_idea_id
        )
        runtime_input = self.authoring._build_script_runtime_input(
            project, topic_idea, request_payload["user_input"]
        )
        self.authoring.validator.validate(
            "generate_script_llm_input.schema.json",
            runtime_input,
            error_code="SCRIPT_LLM_INPUT_INVALID",
        )
        return project, topic_idea, runtime_input

    def _prompt_package(
        self, project_id: str, request_payload: dict[str, Any]
    ) -> dict[str, Any]:
        project, topic_idea, runtime_input = self._project_and_runtime_input(
            project_id, request_payload
        )
        prompt_dir = (
            self.settings.root_dir
            / "contracts"
            / "prompts"
            / "platform"
            / "generate_script_and_analysis"
        )
        system_prompt = (prompt_dir / "system.md").read_text(encoding="utf-8")
        user_prompt = (prompt_dir / "user.md").read_text(encoding="utf-8").replace(
            "{{input_json}}", json.dumps(runtime_input, ensure_ascii=False, indent=2)
        )
        script_id = self.artifacts.preallocate_artifact_id()
        analysis_id = self.artifacts.preallocate_artifact_id()
        output_schema = deepcopy(
            self.authoring.validator.load_schema(
                "script_and_analysis_external_output_v2.schema.json"
            )
        )
        output_schema["properties"]["artifact_ids"]["properties"]["script"]["const"] = script_id
        output_schema["properties"]["artifact_ids"]["properties"]["analysis"]["const"] = analysis_id
        package = render_external_prompt(
            stage="script_and_analysis",
            prompt_version="script_and_analysis_external_v17",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema_name="script_and_analysis_external_output_v2.schema.json",
            output_schema=output_schema,
            artifact_ids={"script": script_id, "analysis": analysis_id},
            output_filename="script_and_analysis.json",
        )
        self.reservations.replace_pending(
            project_id=project_id,
            stage=SCRIPT_STAGE,
            primary_artifact_id=script_id,
            paired_artifact_id=analysis_id,
            source={
                "selected_topic_ideas_artifact_id": project.selected_topic_ideas_artifact_id,
                "selected_topic_idea_id": project.selected_topic_idea_id,
                "previous_script_artifact_id": project.script_artifact_id,
                "previous_script_analysis_artifact_id": project.script_analysis_artifact_id,
                "topic_idea": deepcopy(topic_idea),
                "runtime_input": deepcopy(runtime_input),
                "channel_dna_sha256": "sha256:" + canonical_json_hash(project.channel_dna_json),
                "prompt_package": deepcopy(package),
            },
        )
        return package

    def build_prompt(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        return self._prompt_package(project_id, request_payload)

    def get_pending_prompt(self, project_id: str) -> dict[str, Any]:
        reservation = self.reservations.pending_for(project_id=project_id, stage=SCRIPT_STAGE)
        if reservation is None:
            return {"generated": False, "prompt_package": None, "request_input": None}
        source = self.reservations.source(reservation)
        project = self.authoring.get_project_required(project_id)
        try:
            self.authoring._validate_project_selected_topic_state(project)
        except AppError:
            return {"generated": False, "prompt_package": None, "request_input": None}
        current = {
            "selected_topic_ideas_artifact_id": project.selected_topic_ideas_artifact_id,
            "selected_topic_idea_id": project.selected_topic_idea_id,
            "previous_script_artifact_id": project.script_artifact_id,
            "previous_script_analysis_artifact_id": project.script_analysis_artifact_id,
            "channel_dna_sha256": "sha256:" + canonical_json_hash(project.channel_dna_json),
        }
        if any(source.get(key) != value for key, value in current.items()):
            return {"generated": False, "prompt_package": None, "request_input": None}
        package = source.get("prompt_package")
        if not isinstance(package, dict):
            return {"generated": False, "prompt_package": None, "request_input": None}
        runtime_input = source.get("runtime_input")
        request_input = runtime_input.get("user_input") if isinstance(runtime_input, dict) else None
        return {
            "generated": True,
            "prompt_package": deepcopy(package),
            "request_input": deepcopy(request_input) if isinstance(request_input, dict) else None,
        }

    def _validate_import(
        self, project_id: str, request_payload: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
        combined = parse_external_json(str(request_payload.get("result_json") or ""))
        script_artifact_id, analysis_artifact_id = extract_reserved_artifact_ids(
            combined, stage=SCRIPT_STAGE
        )
        reservation = self.reservations.require(
            project_id=project_id,
            stage=SCRIPT_STAGE,
            primary_artifact_id=script_artifact_id,
            paired_artifact_id=analysis_artifact_id,
        )
        source = self.reservations.source(reservation)
        project = self.authoring.get_project_required(project_id)
        self.authoring._validate_project_selected_topic_state(project)
        require_source_values(
            expected=source,
            current={
                "selected_topic_ideas_artifact_id": project.selected_topic_ideas_artifact_id,
                "selected_topic_idea_id": project.selected_topic_idea_id,
                "previous_script_artifact_id": project.script_artifact_id,
                "previous_script_analysis_artifact_id": project.script_analysis_artifact_id,
                "channel_dna_sha256": "sha256:" + canonical_json_hash(project.channel_dna_json),
            },
            fields=[
                "selected_topic_ideas_artifact_id",
                "selected_topic_idea_id",
                "previous_script_artifact_id",
                "previous_script_analysis_artifact_id",
                "channel_dna_sha256",
            ],
            message="The selected Topic or current Script & Analysis pair changed after this prompt was built.",
        )
        runtime_input = deepcopy(source["runtime_input"])
        topic_idea = deepcopy(source["topic_idea"])
        self.authoring.validator.validate(
            "generate_script_llm_input.schema.json",
            runtime_input,
            error_code="SCRIPT_LLM_INPUT_INVALID",
        )
        combined = self.authoring.validator.validate(
            "script_and_analysis_external_output_v2.schema.json",
            combined,
            error_code="SCRIPT_ANALYSIS_EXTERNAL_OUTPUT_INVALID",
        )
        raw_script = self.authoring.validator.validate(
            "generate_script_llm_raw_output.schema.json",
            deepcopy(combined["script"]),
            error_code="SCRIPT_RAW_OUTPUT_INVALID",
        )
        normalization_runtime_input = _runtime_input_for_count_agnostic_import(
            runtime_input,
            raw_script,
        )
        script = self.authoring._normalize_script_output(
            raw_script,
            runtime_input=normalization_runtime_input,
            topic_idea=topic_idea,
            source_topic_ideas_artifact_id=str(source["selected_topic_ideas_artifact_id"]),
        )
        llm_input = build_script_analysis_input(
            project_id=project_id,
            script_revision_id=script_artifact_id,
            script_artifact=script,
        )
        self.v4.validate(
            "script_analysis_llm_input_v1.schema.json",
            llm_input,
            error_code="SCRIPT_ANALYSIS_LLM_INPUT_INVALID",
        )
        section_guidance = combined["analysis"].get("section_guidance")
        first_guidance = next(
            (item for item in section_guidance if isinstance(item, dict) and item.get("section_id") == "sec_001"),
            None,
        ) if isinstance(section_guidance, list) else None
        if not isinstance(first_guidance, dict) or first_guidance.get("pacing_density") != "high":
            raise AppError(
                "SCRIPT_HOOK_PACING_DENSITY_INVALID",
                "The first Script section is the hook and must have high pacing_density.",
                422,
                retryable=False,
            )
        raw_analysis = deepcopy(combined["analysis"])
        raw_analysis.update(
            {
                "artifact_type": "script_analysis_llm_output",
                "schema_version": "1.0",
                "project_id": project_id,
                "source_script_revision_id": script_artifact_id,
            }
        )
        raw_analysis = self.v4.validate(
            "script_analysis_llm_output_v1.schema.json",
            raw_analysis,
            error_code="SCRIPT_ANALYSIS_RAW_OUTPUT_INVALID",
        )
        canonical_analysis = self.semantic_validator.build_canonical(
            llm_input=llm_input,
            raw_output=raw_analysis,
            section_ranges=build_script_section_ranges(script["script_sections"]),
        )
        self.v4.validate(
            "script_analysis_v1.schema.json",
            canonical_analysis,
            error_code="SCRIPT_ANALYSIS_OUTPUT_INVALID",
        )
        self.semantic_validator.validate_canonical(
            llm_input=llm_input,
            canonical=canonical_analysis,
            section_ranges=build_script_section_ranges(script["script_sections"]),
        )
        return project, script, canonical_analysis, reservation, {
            "script_artifact_id": script_artifact_id,
            "analysis_artifact_id": analysis_artifact_id,
        }

    def validate_import(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        _project, script, analysis, _reservation, ids = self._validate_import(
            project_id, request_payload
        )
        return validation_success(
            summary={
                "script_title": script["title"],
                "section_count": len(script["script_sections"]),
                "word_count": script["word_count"],
                "narrative_marker_count": len(analysis["narrative_markers"]),
                "continuity_counts": {
                    key: len(value)
                    for key, value in analysis["continuity_catalog"].items()
                },
                "staged_script_identity": ids["script_artifact_id"],
            }
        )

    def import_result(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        project, script, analysis, reservation, identities = self._validate_import(
            project_id, request_payload
        )
        script_id = identities["script_artifact_id"]
        analysis_id = identities["analysis_artifact_id"]
        # The semantic validator already built the analysis against script_id.
        guard = ScriptAnalysisPairCommitGuard(
            topic_ideas_artifact_id=str(project.topic_ideas_artifact_id),
            selected_topic_ideas_artifact_id=str(project.selected_topic_ideas_artifact_id),
            selected_topic_idea_id=str(project.selected_topic_idea_id),
            previous_script_artifact_id=project.script_artifact_id,
            previous_script_analysis_artifact_id=project.script_analysis_artifact_id,
        )
        staged_script: StagedArtifact | None = None
        staged_analysis: StagedArtifact | None = None
        try:
            staged_script = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/script",
                artifact_type=ArtifactType.JSON,
                artifact_role=ArtifactRole.SCRIPT,
                filename="script.json",
                content=json.dumps(script, ensure_ascii=False, indent=2).encode("utf-8"),
                artifact_id=script_id,
                provider_source="external_chatgpt",
            )
            staged_analysis = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project_id}/script_analysis",
                artifact_type=ArtifactType.JSON,
                artifact_role=None,
                filename="script_analysis.json",
                content=json.dumps(analysis, ensure_ascii=False, indent=2).encode("utf-8"),
                artifact_id=analysis_id,
                provider_source="external_chatgpt",
                content_hash=script_id,
            )
            success = self.projects.compare_and_set_script_analysis_pair(
                project_id=project_id,
                new_script_artifact_id=script_id,
                new_script_analysis_artifact_id=analysis_id,
                guard=guard,
                updated_at=utc_now_iso(),
            )
            if not success:
                raise AppError(
                    "SCRIPT_ANALYSIS_PUBLICATION_CONFLICT",
                    "The selected topic or current Script & Analysis pair changed while the import was being published.",
                    409,
                    retryable=False,
                )
            # The CAS update deliberately bypasses ORM synchronization. Expire the
            # loaded project so later reads in the same request/session observe the
            # newly published pair and the atomically cleared downstream pointers.
            self.session.expire(project)
            for staged in (staged_script, staged_analysis):
                previous = self.artifacts_repo.get_current_by_logical_id(staged.logical_id)
                if previous is not None and previous.artifact_id != staged.artifact_id:
                    previous.is_current = False
                    previous.superseded_by_artifact_id = staged.artifact_id
                staged.artifact.is_current = True
                staged.artifact.superseded_by_artifact_id = None
            self.reservations.mark_consumed(reservation)
            self.session.flush()
            self.session.commit()
            return {
                "script_artifact_id": script_id,
                "script": script,
                "script_analysis_artifact_id": analysis_id,
                "script_analysis": analysis,
            }
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged_script, logger=logger)
            cleanup_staged_artifact_safely(staged_analysis, logger=logger)
            raise
