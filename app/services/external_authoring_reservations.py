from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.models import ExternalAuthoringReservation

TOPIC_STAGE = "topic_ideas"
SCRIPT_STAGE = "script_and_analysis"
SCENE_STAGE = "scene_breakdown"  # legacy combined authoring reservation
SCENE_PLAN_STAGE = "scene_breakdown_scene"
VISUAL_PLAN_STAGE = "scene_breakdown_visual"


def _identity_paths(stage: str) -> tuple[str, str | None]:
    if stage == TOPIC_STAGE:
        return "$.artifact_id", None
    if stage == SCRIPT_STAGE:
        return "$.artifact_ids.script", "$.artifact_ids.analysis"
    if stage == SCENE_STAGE:
        return "$.artifact_ids.scene_plan", "$.artifact_ids.visual_plan"
    if stage in {SCENE_PLAN_STAGE, VISUAL_PLAN_STAGE}:
        return "$.artifact_id", None
    return "$", None


class ExternalAuthoringReservationService:
    """Own the durable database handoff for external ChatGPT authoring.

    A reservation is created when a prompt is built. The exact prompt package
    may be persisted inside the reservation source snapshot so the browser can
    restore the same reserved Artifact IDs after reload. ChatGPT output must
    echo those IDs exactly. Pending reservations have no expiration policy.

    Scene Breakdown now uses two independent reservation stages for authoring:
    Step 1 locks the semantic Scene/Beat candidate; Step 2 locks a Visual Plan
    prompt to that candidate. Project Scene/Visual pointers are still published
    atomically only after Step 2 passes.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_pending(
        self,
        *,
        project_id: str,
        stage: str,
        primary_artifact_id: str,
        paired_artifact_id: str | None,
        source: dict[str, Any],
    ) -> ExternalAuthoringReservation:
        stages_to_clear = [stage]
        # Rebuilding semantic Scene/Beat authoring invalidates both any Visual
        # prompt bound to the previous candidate and any stale pre-cutover
        # combined Scene Breakdown reservation for this project.
        if stage == SCENE_PLAN_STAGE:
            stages_to_clear.extend([VISUAL_PLAN_STAGE, SCENE_STAGE])
        self.session.execute(
            delete(ExternalAuthoringReservation).where(
                ExternalAuthoringReservation.project_id == project_id,
                ExternalAuthoringReservation.stage.in_(stages_to_clear),
                ExternalAuthoringReservation.status == "pending",
            )
        )
        reservation = ExternalAuthoringReservation(
            primary_artifact_id=primary_artifact_id,
            paired_artifact_id=paired_artifact_id,
            project_id=project_id,
            stage=stage,
            source_json=deepcopy(source),
            status="pending",
            created_at=utc_now_iso(),
            consumed_at=None,
        )
        self.session.add(reservation)
        self.session.commit()
        return reservation

    def require(
        self,
        *,
        project_id: str,
        stage: str,
        primary_artifact_id: str,
        paired_artifact_id: str | None,
    ) -> ExternalAuthoringReservation:
        primary_path, paired_path = _identity_paths(stage)
        reservation = self.session.get(ExternalAuthoringReservation, primary_artifact_id)
        if reservation is None:
            raise AppError(
                "AUTHORING_ARTIFACT_ID_UNKNOWN",
                "The imported Artifact ID was not issued by an active prompt.",
                409,
                retryable=False,
                details={
                    "errors": [
                        {
                            "path": primary_path,
                            "code": "authoring_artifact_id_unknown",
                            "message": "Build a prompt for this stage and use the exact Artifact ID returned in that prompt.",
                        }
                    ]
                },
            )
        if reservation.project_id != project_id:
            raise AppError(
                "AUTHORING_ARTIFACT_PROJECT_MISMATCH",
                "The imported Artifact ID belongs to another project.",
                409,
                retryable=False,
                details={
                    "errors": [
                        {
                            "path": primary_path,
                            "code": "authoring_artifact_project_mismatch",
                            "message": "Use the JSON file generated for this project.",
                        }
                    ]
                },
            )
        if reservation.stage != stage:
            raise AppError(
                "AUTHORING_ARTIFACT_STAGE_MISMATCH",
                "The imported Artifact ID belongs to another authoring stage.",
                409,
                retryable=False,
                details={
                    "errors": [
                        {
                            "path": primary_path,
                            "code": "authoring_artifact_stage_mismatch",
                            "message": "Use the JSON file generated for this authoring stage.",
                        }
                    ]
                },
            )
        if reservation.paired_artifact_id != paired_artifact_id:
            raise AppError(
                "AUTHORING_ARTIFACT_ID_MISMATCH",
                "The paired Artifact ID does not match the prompt reservation.",
                409,
                retryable=False,
                details={
                    "errors": [
                        {
                            "path": paired_path or primary_path,
                            "code": "authoring_artifact_id_mismatch",
                            "message": "Use the exact reserved Artifact ID(s) from the same prompt.",
                        }
                    ]
                },
            )
        if reservation.status != "pending":
            raise AppError(
                "AUTHORING_ARTIFACT_ALREADY_CONSUMED",
                "This authoring Artifact ID has already been imported.",
                409,
                retryable=False,
                details={
                    "errors": [
                        {
                            "path": primary_path,
                            "code": "authoring_artifact_already_consumed",
                            "message": "Build a new prompt before importing another result for this stage.",
                        }
                    ]
                },
            )
        return reservation

    @staticmethod
    def source(reservation: ExternalAuthoringReservation) -> dict[str, Any]:
        value = reservation.source_json
        if not isinstance(value, dict):
            raise AppError(
                "AUTHORING_RESERVATION_INVALID",
                "The authoring reservation source is invalid.",
                500,
                retryable=False,
            )
        return deepcopy(value)

    @staticmethod
    def mark_consumed(reservation: ExternalAuthoringReservation) -> None:
        reservation.status = "consumed"
        reservation.consumed_at = utc_now_iso()

    def pending_for(self, *, project_id: str, stage: str) -> ExternalAuthoringReservation | None:
        return self.session.scalar(
            select(ExternalAuthoringReservation).where(
                ExternalAuthoringReservation.project_id == project_id,
                ExternalAuthoringReservation.stage == stage,
                ExternalAuthoringReservation.status == "pending",
            )
        )
