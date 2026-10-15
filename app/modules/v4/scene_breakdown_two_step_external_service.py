from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.external_authoring import parse_external_json, render_external_prompt, require_source_values, validation_success
from app.core.hashing import canonical_json_hash
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.modules.platform.video_pacing import VideoPacingError, validate_preferred_hold_seconds
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from app.modules.v4.scene_breakdown_input_builder import build_section_input
from app.modules.v4.scene_breakdown_micro_scene import build_micro_scenes
from app.modules.v4.scene_breakdown_partition_planner import (
    build_legal_boundary_plan,
    project_scene_authoring_input,
)
from app.modules.v4.scene_breakdown_scene_materializer import authoring_metrics, materialize_scene_section
from app.modules.v4.scene_breakdown_semantic_validator import (
    canonical_fragment_from_locked,
    combined_fragment_with_visuals,
    validate_and_normalize_materialized_semantic_section,
    validate_semantic_progression_across_fragments,
)
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.scene_breakdown_validator import SceneBreakdownValidationError, validate_reusable_section_fragment
from app.modules.v4.scene_plan_assembler import assemble_scene_plan
from app.modules.v4.visual_plan_assembler import assemble_visual_plan_from_scene_breakdown
from app.repositories import SceneVisualPlanCommitGuard
from app.services.external_authoring_reservations import (
    ExternalAuthoringReservationService,
    SCENE_PLAN_STAGE,
    VISUAL_PLAN_STAGE,
)


logger = logging.getLogger(__name__)
_PROSE_NORMALIZE_RE = re.compile(r"[^\w]+", re.UNICODE)
_SCENE_INPUT_SCHEMA_NAME = "scene_breakdown_scene_authoring_input_v2.schema.json"
_SCENE_OUTPUT_SCHEMA_NAME = "scene_breakdown_scene_llm_output_v2.schema.json"
_SCENE_PROMPT_VERSION = "scene_breakdown_scene_external_v9"
_VISUAL_OUTPUT_SCHEMA_NAME = "scene_breakdown_visual_llm_output_v2.schema.json"
_VISUAL_PROMPT_VERSION = "scene_breakdown_visual_external_v4"
_PROMPT_AUDIT_FIELDS = ("prompt_version", "prompt_sha256", "raw_schema_id", "raw_schema_sha256")
_SCENE_PROMPT_AUDIT_FIELDS = _PROMPT_AUDIT_FIELDS + (
    "scene_input_schema_id",
    "scene_input_schema_sha256",
)


class ExternalSceneBreakdownTwoStepService:
    """External authoring flow with semantic Scene/Beat lock before Visual authoring.

    Step 1 never changes project Scene/Visual pointers. It validates a semantic
    Scene/Beat candidate and stores the locked candidate in the pending Step-2
    reservation. Step 2 authors only visuals, re-validates the locked candidate,
    then publishes ScenePlan + VisualPlan atomically through the existing guard.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.service = SceneBreakdownServiceV4(session)
        self.legacy = ExternalSceneBreakdownService(session)
        self.reservations = ExternalAuthoringReservationService(session)
        self.settings = self.service.artifacts.settings

    @staticmethod
    def _lineage(project: Any) -> dict[str, Any]:
        return {
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

    def _context(self, project_id: str) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[Any]]:
        # Reauthoring replaces the current Scene/Visual pair. Its frozen CAS
        # pointers must remain stable, but an older pair need not satisfy the
        # latest output schema before Step 1 can build its replacement.
        project, script, analysis, style, word_timing = self.legacy._current_context(
            project_id,
            validate_current_pair=False,
        )
        full_input, sections = self.legacy._full_input(project, script, analysis, style, word_timing)
        return project, script, analysis, style, word_timing, full_input, sections

    @staticmethod
    def _timing_section(full_input: dict[str, Any], section_id: str) -> dict[str, Any]:
        sections = full_input.get("timing_context", {}).get("sections", [])
        for item in sections if isinstance(sections, list) else []:
            if isinstance(item, dict) and item.get("section_id") == section_id:
                return item
        raise AppError(
            "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID",
            "Approved Voice timing is missing a Script section.",
            409,
            retryable=False,
            details={"section_id": section_id},
        )

    @classmethod
    def _build_scene_authoring_plan(
        cls,
        full_input: dict[str, Any],
        sections: list[Any],
        word_timing_map: dict[str, Any],
    ) -> dict[str, Any]:
        script_rows = full_input.get("script_context", {}).get("sections", [])
        rows_by_id = {
            str(item.get("section_context", {}).get("section_id") or ""): item
            for item in script_rows
            if isinstance(item, dict)
        }
        planned_sections: list[dict[str, Any]] = []
        for section in sections:
            row = rows_by_id.get(section.section_id)
            if not isinstance(row, dict):
                raise AppError(
                    "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID",
                    "Scene authoring input is missing a Script section.",
                    409,
                    retryable=False,
                    details={"section_id": section.section_id},
                )
            cadence = deepcopy(row["visual_cadence_guidance"]["preferred_hold_seconds"])
            timing_section = cls._timing_section(full_input, section.section_id)
            micro_scenes = build_micro_scenes(
                section=section,
                timing_section=timing_section,
                word_timing_map=word_timing_map,
                preferred_hold_seconds=cadence,
                visual_context_catalog=full_input["visual_context"]["visual_context_catalog"],
            )
            planned_sections.append(
                {
                    "section_id": section.section_id,
                    "preferred_hold_seconds": cadence,
                    "scenes": [
                        build_legal_boundary_plan(
                            scene=scene,
                            timing_section=timing_section,
                            word_timing_map=word_timing_map,
                        )
                        for scene in micro_scenes
                    ],
                }
            )
        return {"sections": planned_sections}

    @staticmethod
    def _project_authoring_plan(authoring_plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "sections": [
                {
                    "section_id": str(section["section_id"]),
                    "scenes": [
                        project_scene_authoring_input(scene)
                        for scene in section["scenes"]
                    ],
                }
                for section in authoring_plan["sections"]
            ]
        }

    @classmethod
    def _scene_prompt_input(
        cls,
        full_input: dict[str, Any],
        sections: list[Any],
        authoring_plan: dict[str, Any],
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        planned_sections = authoring_plan.get("sections")
        if not isinstance(planned_sections, list):
            raise AppError(
                "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID",
                "Scene authoring plan is missing resolved section cadence.",
                409,
                retryable=False,
                details={"path": "$.sections"},
            )
        cadence_by_section: dict[str, dict[str, Any]] = {}
        for index, planned_section in enumerate(planned_sections):
            path = f"$.sections[{index}].preferred_hold_seconds"
            if not isinstance(planned_section, dict):
                raise AppError(
                    "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID",
                    "Scene authoring plan section is invalid.",
                    409,
                    retryable=False,
                    details={"path": f"$.sections[{index}]"},
                )
            section_id = str(planned_section.get("section_id") or "")
            cadence = planned_section.get("preferred_hold_seconds")
            try:
                validate_preferred_hold_seconds(cadence)
            except VideoPacingError:
                cadence_is_valid = False
            else:
                cadence_is_valid = True
            if not section_id or section_id in cadence_by_section or not cadence_is_valid:
                raise AppError(
                    "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID",
                    "Scene authoring plan section has invalid resolved cadence.",
                    409,
                    retryable=False,
                    details={"path": path, "section_id": section_id or None},
                )
            cadence_by_section[section_id] = deepcopy(cadence)

        script_rows = full_input["script_context"]["sections"]
        compact_sections = []
        for index, row in enumerate(script_rows):
            context = row["section_context"]
            section_id = str(context["section_id"])
            resolved_cadence = cadence_by_section.get(section_id)
            if resolved_cadence is None:
                raise AppError(
                    "SCENE_BREAKDOWN_AUTHORING_PLAN_INVALID",
                    "Scene authoring plan is missing resolved cadence for a Script section.",
                    409,
                    retryable=False,
                    details={
                        "path": f"$.sections[{index}].preferred_hold_seconds",
                        "section_id": section_id,
                    },
                )
            compact_sections.append(
                {
                    "section_id": section_id,
                    "section_order": context["section_order"],
                    "section_title": context["section_title"],
                    "section_role": context["section_role"],
                    "preferred_hold_seconds": deepcopy(resolved_cadence),
                }
            )
        payload = {
            "artifact_id": artifact_id or "artifact_000000000000",
            "title": full_input["script_context"]["title"],
            "sections": compact_sections,
            "scene_authoring_plan": cls._project_authoring_plan(authoring_plan),
        }
        return payload

    @staticmethod
    def _prompt_audit(package: dict[str, Any], schema: dict[str, Any]) -> dict[str, str]:
        return {
            "prompt_version": str(package["prompt_version"]),
            "prompt_sha256": "sha256:" + sha256(package["prompt_text"].encode("utf-8")).hexdigest(),
            "raw_schema_id": str(schema.get("$id")),
            "raw_schema_sha256": "sha256:" + canonical_json_hash(schema),
        }

    @staticmethod
    def _scene_authoring_input_sha256(prompt_input: dict[str, Any]) -> str:
        return "sha256:" + canonical_json_hash(prompt_input)

    @classmethod
    def _scene_contract_audit(
        cls,
        package: dict[str, Any],
        output_schema: dict[str, Any],
        input_schema: dict[str, Any],
    ) -> dict[str, str]:
        return {
            **cls._prompt_audit(package, output_schema),
            "scene_input_schema_id": str(input_schema.get("$id")),
            "scene_input_schema_sha256": "sha256:" + canonical_json_hash(input_schema),
        }

    def _scene_prompt_texts(self) -> tuple[str, str]:
        prompt_dir = self.settings.root_dir / "contracts" / "prompts" / "v4" / "scene_breakdown_scene"
        return (
            (prompt_dir / "system.md").read_text(encoding="utf-8"),
            (prompt_dir / "user.md").read_text(encoding="utf-8"),
        )

    def _current_scene_contract(
        self,
        *,
        full_input: dict[str, Any],
        sections: list[Any],
        authoring_plan: dict[str, Any],
        artifact_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Rebuild the current Step-1 contract without mutating reservation state."""

        prompt_input = self._scene_prompt_input(
            full_input,
            sections,
            authoring_plan,
            artifact_id,
        )
        input_schema = deepcopy(self.service.v4.load_schema(_SCENE_INPUT_SCHEMA_NAME))
        prompt_input = self.service.v4.validate_schema(
            _SCENE_INPUT_SCHEMA_NAME,
            input_schema,
            prompt_input,
            error_code="SCENE_BREAKDOWN_SCENE_INPUT_INVALID",
        )
        output_schema = deepcopy(self.service.v4.load_schema(_SCENE_OUTPUT_SCHEMA_NAME))
        output_schema["properties"]["artifact_id"]["const"] = artifact_id
        system_prompt, user_prompt = self._scene_prompt_texts()
        package = render_external_prompt(
            stage="scene_breakdown_scene",
            prompt_version=_SCENE_PROMPT_VERSION,
            system_prompt=system_prompt,
            user_prompt=user_prompt.replace(
                "{{input_json}}",
                json.dumps(prompt_input, ensure_ascii=False, separators=(",", ":")),
            ),
            output_schema_name=_SCENE_OUTPUT_SCHEMA_NAME,
            output_schema=output_schema,
            artifact_ids={"scene_plan": artifact_id},
            output_filename="scene_plan.json",
        )
        return package, output_schema, input_schema, prompt_input

    @classmethod
    def _assert_scene_prompt_current(
        cls,
        *,
        source: dict[str, Any],
        package: dict[str, Any],
        output_schema: dict[str, Any],
        input_schema: dict[str, Any],
    ) -> None:
        current_audit = cls._scene_contract_audit(package, output_schema, input_schema)
        stored_audit = {key: source.get(key) for key in _SCENE_PROMPT_AUDIT_FIELDS}
        if stored_audit != current_audit:
            raise AppError(
                "SCENE_BREAKDOWN_SCENE_PROMPT_STALE",
                "The stored Step-1 prompt or schema contract is stale. Rebuild the Step-1 prompt before importing.",
                409,
                retryable=False,
                details={
                    "issued": stored_audit,
                    "current": current_audit,
                    "action": "rebuild_step_1_prompt",
                },
            )

    def _ensure_scene_prompt_current(
        self,
        *,
        source: dict[str, Any],
        full_input: dict[str, Any],
        sections: list[Any],
        authoring_plan: dict[str, Any],
        artifact_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        input_schema = deepcopy(self.service.v4.load_schema(_SCENE_INPUT_SCHEMA_NAME))
        current_input_audit = {
            "scene_input_schema_id": str(input_schema.get("$id")),
            "scene_input_schema_sha256": "sha256:" + canonical_json_hash(input_schema),
        }
        stored_input_audit = {key: source.get(key) for key in current_input_audit}
        if stored_input_audit != current_input_audit:
            raise AppError(
                "SCENE_BREAKDOWN_SCENE_PROMPT_STALE",
                "The stored Step-1 prompt or schema contract is stale. Rebuild the Step-1 prompt before importing.",
                409,
                retryable=False,
                details={
                    "issued": stored_input_audit,
                    "current": current_input_audit,
                    "action": "rebuild_step_1_prompt",
                },
            )
        output_schema = deepcopy(self.service.v4.load_schema(_SCENE_OUTPUT_SCHEMA_NAME))
        output_schema["properties"]["artifact_id"]["const"] = artifact_id
        current_output_audit = {
            "raw_schema_id": str(output_schema.get("$id")),
            "raw_schema_sha256": "sha256:" + canonical_json_hash(output_schema),
        }
        stored_output_audit = {key: source.get(key) for key in current_output_audit}
        if stored_output_audit != current_output_audit:
            raise AppError(
                "SCENE_BREAKDOWN_SCENE_PROMPT_STALE",
                "The stored Step-1 prompt or schema contract is stale. Rebuild the Step-1 prompt before importing.",
                409,
                retryable=False,
                details={
                    "issued": stored_output_audit,
                    "current": current_output_audit,
                    "action": "rebuild_step_1_prompt",
                },
            )
        contract = self._current_scene_contract(
            full_input=full_input,
            sections=sections,
            authoring_plan=authoring_plan,
            artifact_id=artifact_id,
        )
        package, output_schema, input_schema, _prompt_input = contract
        self._assert_scene_prompt_current(
            source=source,
            package=package,
            output_schema=output_schema,
            input_schema=input_schema,
        )
        return contract

    @staticmethod
    def _validation_error(
        exc: SceneBreakdownValidationError,
        *,
        error_code: str,
        message: str,
        section_id: str,
    ) -> AppError:
        errors = exc.report.get("errors") if isinstance(exc.report, dict) else None
        return AppError(
            error_code,
            message,
            422,
            retryable=False,
            details={"section_id": section_id, "errors": errors or []},
        )

    def build_scene_prompt(self, project_id: str) -> dict[str, Any]:
        project, _script, _analysis, _style, word_timing, full_input, sections = self._context(project_id)
        authoring_plan = self._build_scene_authoring_plan(
            full_input,
            sections,
            word_timing,
        )
        artifact_id = self.service.artifacts.preallocate_artifact_id()
        package, schema, input_schema, prompt_input = self._current_scene_contract(
            full_input=full_input,
            sections=sections,
            authoring_plan=authoring_plan,
            artifact_id=artifact_id,
        )
        scene_authoring_input_sha256 = self._scene_authoring_input_sha256(prompt_input)
        self.reservations.replace_pending(
            project_id=project_id,
            stage=SCENE_PLAN_STAGE,
            primary_artifact_id=artifact_id,
            paired_artifact_id=None,
            source={
                **self._lineage(project),
                **self._scene_contract_audit(package, schema, input_schema),
                "scene_authoring_plan_sha256": "sha256:" + canonical_json_hash(authoring_plan),
                "scene_authoring_input_sha256": scene_authoring_input_sha256,
                "prompt_package": deepcopy(package),
            },
        )
        return package

    @staticmethod
    def _visual_request_input(source: dict[str, Any]) -> dict[str, Any] | None:
        scene_plan = source.get("scene_plan")
        if not isinstance(scene_plan, dict):
            return None
        return {
            "scene_plan_artifact_id": source.get("scene_plan_artifact_id"),
            "scene_plan": deepcopy(scene_plan),
        }

    def _current_visual_contract(
        self,
        *,
        source: dict[str, Any],
        full_input: dict[str, Any],
        visual_plan_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        scene_plan = source.get("scene_plan")
        locked_fragments = source.get("locked_fragments")
        if not isinstance(scene_plan, dict) or not isinstance(locked_fragments, list):
            raise AppError(
                "SCENE_BREAKDOWN_VISUAL_RESERVATION_INVALID",
                "Locked Step-1 candidate is missing; rebuild Step 1 before rebuilding Step 2.",
                409,
                retryable=False,
            )
        return self._build_visual_package(
            scene_plan=scene_plan,
            locked_fragments=locked_fragments,
            visual_context=full_input["visual_context"],
            visual_plan_id=visual_plan_id,
        )

    def _ensure_visual_prompt_current(
        self,
        *,
        source: dict[str, Any],
        full_input: dict[str, Any],
        visual_plan_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        package, schema = self._current_visual_contract(
            source=source,
            full_input=full_input,
            visual_plan_id=visual_plan_id,
        )
        current_audit = self._prompt_audit(package, schema)
        stored_audit = {key: source.get(key) for key in _PROMPT_AUDIT_FIELDS}
        if stored_audit != current_audit:
            raise AppError(
                "SCENE_BREAKDOWN_VISUAL_PROMPT_STALE",
                "The stored Step-2 prompt/schema contract is stale. Rebuild the Step-2 Visual prompt before importing.",
                409,
                retryable=False,
                details={"issued": stored_audit, "current": current_audit, "action": "rebuild_step_2_prompt"},
            )
        return package, schema

    def _pending_prompt(self, project_id: str, stage: str) -> dict[str, Any]:
        reservation = self.reservations.pending_for(project_id=project_id, stage=stage)
        if reservation is None:
            return {"generated": False, "rebuild_required": False, "prompt_package": None, "request_input": None}
        source = self.reservations.source(reservation)
        try:
            project, _script, _analysis, _style, word_timing, full_input, sections = self._context(project_id)
        except AppError:
            return {"generated": False, "rebuild_required": True, "prompt_package": None, "request_input": self._visual_request_input(source) if stage == VISUAL_PLAN_STAGE else None}
        if any(source.get(key) != value for key, value in self._lineage(project).items()):
            return {"generated": False, "rebuild_required": True, "prompt_package": None, "request_input": self._visual_request_input(source) if stage == VISUAL_PLAN_STAGE else None}
        package = source.get("prompt_package")
        if not isinstance(package, dict):
            return {"generated": False, "rebuild_required": True, "prompt_package": None, "request_input": self._visual_request_input(source) if stage == VISUAL_PLAN_STAGE else None}
        request_input = None
        if stage == SCENE_PLAN_STAGE:
            try:
                authoring_plan = self._build_scene_authoring_plan(
                    full_input,
                    sections,
                    word_timing,
                )
                expected_plan_hash = "sha256:" + canonical_json_hash(authoring_plan)
                current_prompt_input = self._scene_prompt_input(
                    full_input,
                    sections,
                    authoring_plan,
                    reservation.primary_artifact_id,
                )
                expected_surface_hash = self._scene_authoring_input_sha256(current_prompt_input)
                if (
                    source.get("scene_authoring_plan_sha256") != expected_plan_hash
                    or source.get("scene_authoring_input_sha256") != expected_surface_hash
                ):
                    return {"generated": False, "rebuild_required": True, "prompt_package": None, "request_input": None}
                self._ensure_scene_prompt_current(
                    source=source,
                    full_input=full_input,
                    sections=sections,
                    authoring_plan=authoring_plan,
                    artifact_id=reservation.primary_artifact_id,
                )
            except AppError as exc:
                if exc.error_code == "SCENE_BREAKDOWN_SCENE_PROMPT_STALE":
                    return {"generated": False, "rebuild_required": True, "prompt_package": None, "request_input": None}
                raise
        elif stage == VISUAL_PLAN_STAGE:
            request_input = self._visual_request_input(source)
            try:
                self._ensure_visual_prompt_current(
                    source=source,
                    full_input=full_input,
                    visual_plan_id=reservation.primary_artifact_id,
                )
            except AppError as exc:
                if exc.error_code == "SCENE_BREAKDOWN_VISUAL_PROMPT_STALE":
                    return {"generated": False, "rebuild_required": True, "prompt_package": None, "request_input": request_input}
                raise
        return {"generated": True, "rebuild_required": False, "prompt_package": deepcopy(package), "request_input": request_input}

    def get_pending_scene_prompt(self, project_id: str) -> dict[str, Any]:
        return self._pending_prompt(project_id, SCENE_PLAN_STAGE)

    def get_pending_visual_prompt(self, project_id: str) -> dict[str, Any]:
        return self._pending_prompt(project_id, VISUAL_PLAN_STAGE)

    def build_visual_prompt(self, project_id: str) -> dict[str, Any]:
        reservation = self.reservations.pending_for(project_id=project_id, stage=VISUAL_PLAN_STAGE)
        if reservation is None:
            raise AppError(
                "SCENE_BREAKDOWN_VISUAL_PROMPT_UNAVAILABLE",
                "Import Step 1 first so a locked Scene/Beat candidate exists before rebuilding the Step-2 Visual prompt.",
                409,
                retryable=False,
            )
        source = self.reservations.source(reservation)
        project, _script, _analysis, _style, _word_timing, full_input, _sections = self._context(project_id)
        self._require_current(source, project, "Scene Breakdown upstream artifacts changed before the Step-2 prompt was rebuilt.")
        visual_plan_id = self.service.artifacts.preallocate_artifact_id()
        package, schema = self._current_visual_contract(
            source=source,
            full_input=full_input,
            visual_plan_id=visual_plan_id,
        )
        replacement_source = deepcopy(source)
        replacement_source.update(
            {
                **self._lineage(project),
                **self._prompt_audit(package, schema),
                "prompt_package": deepcopy(package),
            }
        )
        self.reservations.replace_pending(
            project_id=project_id,
            stage=VISUAL_PLAN_STAGE,
            primary_artifact_id=visual_plan_id,
            paired_artifact_id=None,
            source=replacement_source,
        )
        return package

    def _require_current(self, source: dict[str, Any], project: Any, message: str) -> None:
        current = self._lineage(project)
        require_source_values(
            expected=source,
            current=current,
            fields=list(current),
            message=message,
        )

    def _validate_scene_import(self, project_id: str, request_payload: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], Any, dict[str, Any]]:
        raw = parse_external_json(str(request_payload.get("result_json") or ""))
        artifact_id = str(raw.get("artifact_id") or "")
        reservation = self.reservations.require(
            project_id=project_id,
            stage=SCENE_PLAN_STAGE,
            primary_artifact_id=artifact_id,
            paired_artifact_id=None,
        )
        source = self.reservations.source(reservation)
        project, script, analysis, style, word_timing, full_input, sections = self._context(project_id)
        self._require_current(source, project, "Scene & Beat Plan upstream artifacts changed after this prompt was built.")
        authoring_plan = self._build_scene_authoring_plan(
            full_input,
            sections,
            word_timing,
        )
        expected_plan_hash = "sha256:" + canonical_json_hash(authoring_plan)
        if source.get("scene_authoring_plan_sha256") != expected_plan_hash:
            raise AppError(
                "SCENE_BREAKDOWN_AUTHORING_PLAN_STALE",
                "The deterministic Scene authoring plan changed after this prompt was built.",
                409,
                retryable=False,
            )
        current_prompt_input = self._scene_prompt_input(
            full_input,
            sections,
            authoring_plan,
            artifact_id,
        )
        expected_surface_hash = self._scene_authoring_input_sha256(current_prompt_input)
        if source.get("scene_authoring_input_sha256") != expected_surface_hash:
            raise AppError(
                "SCENE_BREAKDOWN_AUTHORING_SURFACE_STALE",
                "The issued Step-1 decision surface no longer matches the current projection. Rebuild the Step-1 prompt before importing.",
                409,
                retryable=False,
                details={
                    "issued_sha256": source.get("scene_authoring_input_sha256"),
                    "current_sha256": expected_surface_hash,
                },
            )
        self._ensure_scene_prompt_current(
            source=source,
            full_input=full_input,
            sections=sections,
            authoring_plan=authoring_plan,
            artifact_id=artifact_id,
        )
        raw = self.service.v4.validate(
            _SCENE_OUTPUT_SCHEMA_NAME,
            raw,
            error_code="SCENE_BREAKDOWN_SCENE_OUTPUT_INVALID",
        )
        expected_ids = [section.section_id for section in sections]
        actual_ids = [item["section_id"] for item in raw["sections"]]
        if actual_ids != expected_ids:
            raise AppError(
                "SCENE_BREAKDOWN_SECTION_COVERAGE_INVALID",
                "Step 1 must contain every Script section exactly once and in order.",
                422,
                retryable=False,
                details={"errors": [{"path": "$.sections", "code": "section_order_mismatch", "message": f"Expected {expected_ids}; received {actual_ids}."}]},
            )
        locked_fragments: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for section_index, (section, output, planned_section) in enumerate(
            zip(sections, raw["sections"], authoring_plan["sections"], strict=True)
        ):
            materialized, selection_metrics = materialize_scene_section(
                section=section,
                authoring_section=planned_section,
                output_section=output,
                require_first_new_setup=section_index == 0,
            )
            section_input = build_section_input(
                section=section,
                script_analysis=analysis,
                style_bible=style,
                carry_over=self.legacy._carry_from_fragment(previous),
                video_pacing_profile=script["video_pacing_profile"],
            )
            try:
                locked = validate_and_normalize_materialized_semantic_section(
                    materialized_output=materialized,
                    section=section,
                    section_input=section_input,
                    script_analysis=analysis,
                    style_bible=style,
                    word_timing_map=word_timing,
                )
            except SceneBreakdownValidationError as exc:
                raise self._validation_error(
                    exc,
                    error_code="SCENE_BREAKDOWN_SCENE_VALIDATION_FAILED",
                    message=f"Scene & Beat Plan section '{section.section_id}' failed validation.",
                    section_id=section.section_id,
                ) from exc
            metrics = authoring_metrics(
                authoring_section=planned_section,
                materialized=materialized,
                selection_metrics=selection_metrics,
                word_timing_map=word_timing,
            )
            timing_metrics = locked.get("backend_qc", {}).get("timing_metrics")
            if isinstance(timing_metrics, dict):
                for key in ("within_preferred_ratio", "below_preferred_ratio", "above_preferred_ratio"):
                    if key in timing_metrics:
                        metrics[key] = timing_metrics[key]
            if section_index > 0 and materialized["scenes"]:
                relation = materialized["scenes"][0]["beats"][0]["continuity_with_previous"]
                metric_key = (
                    "cross_section_new_setup_count"
                    if relation == "new_setup"
                    else "cross_section_continuity_count"
                )
                metrics[metric_key] = int(metrics.get(metric_key, 0)) + 1
            locked["backend_qc"]["authoring_plan_metrics"] = metrics
            locked_fragments.append(locked)
            previous = locked
        validate_semantic_progression_across_fragments(locked_fragments)
        return project, script, analysis, style, word_timing, locked_fragments, reservation, source

    def _scene_candidate(self, project: Any, locked_fragments: list[dict[str, Any]], scene_plan_id: str, run_artifact_id: str) -> dict[str, Any]:
        candidate = assemble_scene_plan(
            project_id=project.project_id,
            revision_id=scene_plan_id,
            parent_revision_id=project.scene_plan_artifact_id,
            revision_operation="full_generation",
            source_script_revision_id=str(project.script_artifact_id),
            source_script_analysis_revision_id=str(project.script_analysis_artifact_id),
            source_style_bible_revision_id=str(project.style_bible_artifact_id),
            source_run_artifact_id=run_artifact_id,
            ordered_sections=[canonical_fragment_from_locked(item) for item in locked_fragments],
            status="draft_ready",
        )
        return self.service.v4.validate("scene_plan_v1.schema.json", candidate, error_code="SCENE_PLAN_INVALID")

    @staticmethod
    def _locked_visual_input(scene_plan: dict[str, Any], locked_fragments: list[dict[str, Any]], visual_context: dict[str, Any]) -> dict[str, Any]:
        states: list[dict[str, Any]] = []
        scene_cursor = 0
        for fragment in locked_fragments:
            for locked_scene in fragment["scenes"]:
                canonical_scene = scene_plan["scenes"][scene_cursor]
                scene_cursor += 1
                beat_states: list[dict[str, Any]] = []
                for canonical_beat, locked_beat in zip(canonical_scene["beats"], locked_scene["beats"], strict=True):
                    beat_states.append(
                        {
                            "beat_id": canonical_beat["beat_id"],
                            "script_quote": canonical_beat["script_quote"],
                            "meaning_now": locked_beat["meaning_now"],
                            "visible_state_now": locked_beat["visible_state_now"],
                            "delta_now": locked_beat["delta_now"],
                            "continuity_with_previous": locked_beat["continuity_with_previous"],
                            "visual_context_ref": canonical_beat["visual_context_ref"],
                            "eligible_character_refs": canonical_beat["eligible_character_refs"],
                            "eligible_location_refs": canonical_beat["eligible_location_refs"],
                            "eligible_prop_refs": canonical_beat["eligible_prop_refs"],
                            "eligible_recurring_visual_subject_refs": canonical_beat["eligible_recurring_visual_subject_refs"],
                            "eligible_motif_refs": canonical_beat["eligible_motif_refs"],
                        }
                    )
                states.append(
                    {
                        "scene_id": canonical_scene["scene_id"],
                        "section_id": canonical_scene["section_id"],
                        "beats": beat_states,
                    }
                )
        return {
            "scene_plan_revision_id": scene_plan["scene_plan_revision_id"],
            "scene_count": scene_plan["scene_count"],
            "beat_count": scene_plan["beat_count"],
            "locked_scenes": states,
            "visual_context": deepcopy(visual_context),
        }

    def _build_visual_package(self, *, scene_plan: dict[str, Any], locked_fragments: list[dict[str, Any]], visual_context: dict[str, Any], visual_plan_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        schema = deepcopy(self.service.v4.load_schema(_VISUAL_OUTPUT_SCHEMA_NAME))
        schema["properties"]["artifact_id"]["const"] = visual_plan_id
        prompt_input = self._locked_visual_input(scene_plan, locked_fragments, visual_context)
        prompt_dir = self.settings.root_dir / "contracts" / "prompts" / "v4" / "scene_breakdown_visual"
        package = render_external_prompt(
            stage="scene_breakdown_visual",
            prompt_version=_VISUAL_PROMPT_VERSION,
            system_prompt=(prompt_dir / "system.md").read_text(encoding="utf-8"),
            user_prompt=(prompt_dir / "user.md").read_text(encoding="utf-8").replace(
                "{{input_json}}", json.dumps(prompt_input, ensure_ascii=False, indent=2)
            ),
            output_schema_name=_VISUAL_OUTPUT_SCHEMA_NAME,
            output_schema=schema,
            artifact_ids={"visual_plan": visual_plan_id},
            output_filename="visual_plan.json",
        )
        return package, schema

    @staticmethod
    def _normalized_prose(value: Any) -> str:
        return _PROSE_NORMALIZE_RE.sub(" ", str(value).casefold()).strip()

    @classmethod
    def _validate_visual_semantics(
        cls,
        raw: dict[str, Any],
        scene_plan: dict[str, Any],
        locked_fragments: list[dict[str, Any]],
    ) -> None:
        """Catch deterministic Step-2 contract failures before canonical validation.

        This deliberately avoids subjective template scoring. It rejects only
        definite field collapse and selected references that are unsupported by
        the current Beat or the exact immediately inherited continuity state.
        """

        locked_scenes = [scene for fragment in locked_fragments for scene in fragment["scenes"]]
        issues: list[dict[str, str]] = []
        previous_selected: dict[str, set[str]] | None = None
        for scene_index, (expected_scene, visual_scene, locked_scene) in enumerate(
            zip(scene_plan["scenes"], raw["scenes"], locked_scenes, strict=True)
        ):
            for beat_index, (expected_beat, item, locked_beat) in enumerate(
                zip(expected_scene["beats"], visual_scene["beat_plans"], locked_scene["beats"], strict=True)
            ):
                base_path = f"$.scenes[{scene_index}].beat_plans[{beat_index}]"
                if item.get("visual_context_ref") != expected_beat.get("visual_context_ref"):
                    issues.append(
                        {
                            "path": f"{base_path}.visual_context_ref",
                            "code": "visual_context_ref_mismatch",
                            "message": "Step 2 must echo the locked Scene Plan visual_context_ref exactly.",
                        }
                    )
                prose = {
                    field: cls._normalized_prose(item.get(field))
                    for field in ("visual_concept", "subject_action", "environment_emphasis", "composition")
                }
                fields = list(prose)
                for left_index, left in enumerate(fields):
                    for right in fields[left_index + 1 :]:
                        if prose[left] and prose[left] == prose[right]:
                            issues.append(
                                {
                                    "path": base_path,
                                    "code": "visual_field_collapse",
                                    "message": f"{left} and {right} collapse to the same normalized prose; final fields must perform distinct jobs.",
                                }
                            )

                selected = {
                    "selected_character_refs": set(map(str, item.get("selected_character_refs", []))),
                    "selected_location_refs": set(map(str, item.get("selected_location_refs", []))),
                    "selected_prop_refs": set(map(str, item.get("selected_prop_refs", []))),
                    "selected_recurring_visual_subject_refs": set(map(str, item.get("selected_recurring_visual_subject_refs", []))),
                    "selected_motif_refs": set(map(str, item.get("selected_motif_refs", []))),
                }
                eligible = {
                    "selected_character_refs": set(map(str, expected_beat["eligible_character_refs"])),
                    "selected_location_refs": set(map(str, expected_beat["eligible_location_refs"])),
                    "selected_prop_refs": set(map(str, expected_beat["eligible_prop_refs"])),
                    "selected_recurring_visual_subject_refs": set(map(str, expected_beat["eligible_recurring_visual_subject_refs"])),
                    "selected_motif_refs": set(map(str, expected_beat["eligible_motif_refs"])),
                }
                relation = str(locked_beat.get("continuity_with_previous") or "new_setup")
                previous_image_required = bool(item["previous_beat_image_reference_required"])
                invalid_previous_image_decision = (
                    (relation == "new_setup" and previous_image_required)
                    or (relation == "match_composition" and not previous_image_required)
                )
                if invalid_previous_image_decision:
                    issues.append(
                        {
                            "path": f"{base_path}.previous_beat_image_reference_required",
                            "code": "previous_beat_image_reference_relation_mismatch",
                            "message": (
                                "Previous-Beat image dependency does not satisfy the locked Step-1 continuity relation matrix."
                            ),
                        }
                    )
                carry_by_relation = {
                    "same_setup": {"selected_character_refs", "selected_location_refs", "selected_prop_refs", "selected_recurring_visual_subject_refs", "selected_motif_refs"},
                    "match_subject": {"selected_character_refs", "selected_prop_refs", "selected_recurring_visual_subject_refs"},
                    "match_location": {"selected_location_refs"},
                    "match_composition": set(),
                    "graphic_sequence": set(),
                }
                carry_fields = carry_by_relation.get(relation, set()) if previous_selected is not None else set()
                for field, values in selected.items():
                    supported = set(eligible[field])
                    if field in carry_fields and previous_selected is not None:
                        supported.update(previous_selected[field])
                    unsupported = sorted(values - supported)
                    if unsupported:
                        issues.append(
                            {
                                "path": f"{base_path}.{field}",
                                "code": "visual_reference_outside_locked_eligibility",
                                "message": (
                                    "Selected reference(s) are unsupported by this locked Beat's eligibility "
                                    f"or immediate continuity state: {unsupported}."
                                ),
                            }
                        )
                previous_selected = selected
        if issues:
            raise AppError(
                "SCENE_BREAKDOWN_VISUAL_SEMANTIC_INVALID",
                "Visual Plan violates locked Beat field/reference authority.",
                422,
                retryable=False,
                details={"errors": issues},
            )

    @staticmethod
    def _scene_authoring_metrics_summary(locked: list[dict[str, Any]]) -> dict[str, Any]:
        totals = {
            "micro_scene_count": 0,
            "beat_count": 0,
            "single_beat_scene_count": 0,
            "multi_beat_scene_count": 0,
            "all_in_band_scene_count": 0,
            "scene_with_timing_exception_count": 0,
            "selected_out_of_band_beat_count": 0,
            "cross_scene_continuity_count": 0,
            "cross_scene_new_setup_count": 0,
            "cross_section_continuity_count": 0,
            "cross_section_new_setup_count": 0,
        }
        weighted = {
            "within_preferred_ratio": 0.0,
            "below_preferred_ratio": 0.0,
            "above_preferred_ratio": 0.0,
        }
        total_beats = 0
        sections: list[dict[str, Any]] = []
        for fragment in locked:
            metrics = fragment.get("backend_qc", {}).get("authoring_plan_metrics")
            if not isinstance(metrics, dict):
                continue
            section_metrics = deepcopy(metrics)
            sections.append(
                {
                    "section_id": str(fragment["section_id"]),
                    **section_metrics,
                }
            )
            for key in totals:
                totals[key] += int(metrics.get(key, 0))
            beat_count = int(metrics.get("beat_count", 0))
            total_beats += beat_count
            for key in weighted:
                weighted[key] += float(metrics.get(key, 0.0)) * beat_count
        for key in weighted:
            totals[key] = round(weighted[key] / total_beats, 4) if total_beats else 0.0
        return {"totals": totals, "sections": sections}

    def validate_scene_import(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        project, _script, _analysis, _style, _word_timing, locked, reservation, _source = self._validate_scene_import(project_id, request_payload)
        candidate = self._scene_candidate(project, locked, reservation.primary_artifact_id, self.service.artifacts.preallocate_artifact_id())
        return validation_success(
            summary={
                "scene_count": candidate["scene_count"],
                "beat_count": candidate["beat_count"],
                "semantic_state_locked": True,
                "authoring_plan_metrics": self._scene_authoring_metrics_summary(locked),
            }
        )

    def import_scene_result(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        project, _script, _analysis, _style, _word_timing, locked, reservation, source = self._validate_scene_import(project_id, request_payload)
        run_artifact_id = self.service.artifacts.preallocate_artifact_id()
        scene_plan = self._scene_candidate(project, locked, reservation.primary_artifact_id, run_artifact_id)
        _project, _s, _a, _st, _wt, full_input, _sections = self._context(project_id)
        visual_plan_id = self.service.artifacts.preallocate_artifact_id()
        visual_package, visual_schema = self._build_visual_package(
            scene_plan=scene_plan,
            locked_fragments=locked,
            visual_context=full_input["visual_context"],
            visual_plan_id=visual_plan_id,
        )
        self.reservations.mark_consumed(reservation)
        self.reservations.replace_pending(
            project_id=project_id,
            stage=VISUAL_PLAN_STAGE,
            primary_artifact_id=visual_plan_id,
            paired_artifact_id=None,
            source={
                **self._lineage(project),
                "scene_plan_artifact_id": scene_plan["scene_plan_revision_id"],
                "scene_plan": deepcopy(scene_plan),
                "locked_fragments": deepcopy(locked),
                "run_artifact_id": run_artifact_id,
                "scene_prompt_audit": {key: source.get(key) for key in _SCENE_PROMPT_AUDIT_FIELDS},
                **self._prompt_audit(visual_package, visual_schema),
                "prompt_package": deepcopy(visual_package),
            },
        )
        return {
            "scene_plan_artifact_id": scene_plan["scene_plan_revision_id"],
            "scene_plan": scene_plan,
            "visual_prompt_package": visual_package,
        }

    def _validate_visual_import(self, project_id: str, request_payload: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any], list[dict[str, Any]], Any, dict[str, Any]]:
        raw = parse_external_json(str(request_payload.get("result_json") or ""))
        artifact_id = str(raw.get("artifact_id") or "")
        reservation = self.reservations.require(
            project_id=project_id,
            stage=VISUAL_PLAN_STAGE,
            primary_artifact_id=artifact_id,
            paired_artifact_id=None,
        )
        source = self.reservations.source(reservation)
        project, script, analysis, style, word_timing, full_input, sections = self._context(project_id)
        self._require_current(source, project, "Scene Breakdown upstream artifacts changed after the Visual prompt was built.")
        self._ensure_visual_prompt_current(
            source=source,
            full_input=full_input,
            visual_plan_id=reservation.primary_artifact_id,
        )
        raw = self.service.v4.validate(
            _VISUAL_OUTPUT_SCHEMA_NAME,
            raw,
            error_code="SCENE_BREAKDOWN_VISUAL_OUTPUT_INVALID",
        )
        scene_plan = deepcopy(source.get("scene_plan"))
        locked_fragments = deepcopy(source.get("locked_fragments"))
        if not isinstance(scene_plan, dict) or not isinstance(locked_fragments, list):
            raise AppError("SCENE_BREAKDOWN_VISUAL_RESERVATION_INVALID", "Locked Step-1 candidate is missing.", 409, retryable=False)
        expected_scene_ids = [scene["scene_id"] for scene in scene_plan["scenes"]]
        actual_scene_ids = [scene["scene_id"] for scene in raw["scenes"]]
        if actual_scene_ids != expected_scene_ids:
            raise AppError("SCENE_BREAKDOWN_VISUAL_COVERAGE_INVALID", "Visual output scene IDs/order must exactly match Step 1.", 422, retryable=False)
        visuals_by_beat_id: dict[str, dict[str, Any]] = {}
        for expected_scene, visual_scene in zip(scene_plan["scenes"], raw["scenes"], strict=True):
            expected_beats = [beat["beat_id"] for beat in expected_scene["beats"]]
            actual_beats = [beat["beat_id"] for beat in visual_scene["beat_plans"]]
            if actual_beats != expected_beats:
                raise AppError(
                    "SCENE_BREAKDOWN_VISUAL_BEAT_COVERAGE_INVALID",
                    f"Visual Beat IDs/order for {expected_scene['scene_id']} must exactly match Step 1.",
                    422,
                    retryable=False,
                )
            for item in visual_scene["beat_plans"]:
                visual = deepcopy(item)
                beat_id = visual.pop("beat_id")
                visual.pop("visual_context_ref")
                visuals_by_beat_id[beat_id] = visual

        self._validate_visual_semantics(raw, scene_plan, locked_fragments)

        combined_fragments: list[dict[str, Any]] = []
        scene_cursor = 0
        previous: dict[str, Any] | None = None
        for section, locked in zip(sections, locked_fragments, strict=True):
            section_input = build_section_input(
                section=section,
                script_analysis=analysis,
                style_bible=style,
                carry_over=self.legacy._carry_from_fragment(previous),
                video_pacing_profile=script["video_pacing_profile"],
            )
            local_scene_count = len(locked["scenes"])
            canonical_scenes = scene_plan["scenes"][scene_cursor : scene_cursor + local_scene_count]
            scene_cursor += local_scene_count
            beat_ids = [beat["beat_id"] for scene in canonical_scenes for beat in scene["beats"]]
            combined = combined_fragment_with_visuals(
                locked_fragment=locked,
                visuals_by_beat_id=visuals_by_beat_id,
                beat_ids_in_order=beat_ids,
            )
            try:
                validated = validate_reusable_section_fragment(
                    fragment=combined,
                    section=section,
                    section_input=section_input,
                    script_analysis=analysis,
                    style_bible=style,
                    word_timing_map=word_timing,
                )
            except SceneBreakdownValidationError as exc:
                raise self._validation_error(
                    exc,
                    error_code="SCENE_BREAKDOWN_VISUAL_VALIDATION_FAILED",
                    message=f"Visual Plan section '{section.section_id}' failed validation.",
                    section_id=section.section_id,
                ) from exc
            combined_fragments.append(validated)
            previous = validated
        return project, style, scene_plan, combined_fragments, reservation, source

    def validate_visual_import(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        _project, _style, scene_plan, fragments, _reservation, _source = self._validate_visual_import(project_id, request_payload)
        return validation_success(summary={"scene_count": scene_plan["scene_count"], "beat_count": scene_plan["beat_count"], "visual_mapping_locked": True, "section_count": len(fragments)})

    def import_visual_result(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        project, style, scene_plan, fragments, reservation, source = self._validate_visual_import(project_id, request_payload)
        run_artifact_id = str(source["run_artifact_id"])
        scene_plan_id = str(source["scene_plan_artifact_id"])
        visual_plan_id = reservation.primary_artifact_id
        # Rebuild the canonical ScenePlan from the same validated Step-1 boundaries;
        # no project pointer has changed since Step 1.
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
            raise AppError("SCENE_BREAKDOWN_VISUAL_REFERENCE_APPROVAL_STALE", "The visual-reference approval set changed during authoring.", 409, retryable=False)
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
        run = {
            "artifact_type": "scene_breakdown_run",
            "schema_version": "2.0",
            "run_id": generate_id("import"),
            "operation": "full_import",
            "project_id": project_id,
            "source_script_revision_id": str(project.script_artifact_id),
            "source_script_analysis_revision_id": str(project.script_analysis_artifact_id),
            "source_style_bible_revision_id": str(project.style_bible_artifact_id),
            "expected_previous_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "source_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "expected_previous_visual_plan_artifact_id": project.visual_plan_artifact_id,
            "source_visual_plan_artifact_id": project.visual_plan_artifact_id,
            "source_visual_reference_approval_set_artifact_id": approval_id,
            "frozen_execution": {
                "mode": "external_import",
                "authoring_artifact_ids": {"scene_plan": scene_plan_id, "visual_plan": visual_plan_id},
            },
            "status": "completed",
            "section_results": [
                {
                    "section_id": fragment["section_id"],
                    "status": "passed",
                    "normalized_fragment": deepcopy(fragment),
                    "failure_artifact_id": None,
                    "validation_summary": {
                        "critical_status": "passed",
                        "warning_count": len(fragment.get("backend_qc", {}).get("warnings", [])) + len(fragment.get("backend_qc", {}).get("timing_warnings", [])),
                    },
                }
                for fragment in fragments
            ],
            "created_at": utc_now_iso(),
        }
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
            logger.exception("Unexpected two-step Scene Breakdown publication failure for %s", project_id)
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
            "scene_plan_artifact_id": scene_plan_id,
            "scene_plan": scene_plan,
            "visual_plan_artifact_id": visual_plan_id,
            "visual_plan": visual_plan,
        }
