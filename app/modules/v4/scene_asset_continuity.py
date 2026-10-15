from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Set
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate


def transitive_dependents(
    dependents_by_source: Mapping[str, set[str]],
    source_beat_id: str,
    *,
    skip_direct: Set[str] | None = None,
) -> list[str]:
    """Return all descendants of source in stable breadth-first order.

    ``skip_direct`` applies only to direct children of the root. It is used when
    a dependent candidate already references the exact newly-approved source
    artifact, so that unchanged branch remains valid. Descendants of any child
    that is actually invalidated are always traversed and invalidated.
    """
    skipped = set(skip_direct or ())
    visited = {source_beat_id}
    queue: deque[str] = deque(
        sorted(child for child in dependents_by_source.get(source_beat_id, set()) if child not in skipped)
    )
    result: list[str] = []
    while queue:
        beat_id = queue.popleft()
        if beat_id in visited:
            continue
        visited.add(beat_id)
        result.append(beat_id)
        for child in sorted(dependents_by_source.get(beat_id, set())):
            if child not in visited:
                queue.append(child)
    return result


@dataclass(frozen=True, slots=True)
class ContinuityInvalidationPlan:
    project_id: str
    source_beat_id: str
    affected_beat_ids: tuple[str, ...]
    direct_source_by_beat: tuple[tuple[str, str], ...]


class SceneAssetContinuityInvalidationService:
    """Single mutation engine for Scene Assets V2 continuity invalidation.

    Planning is read-only and preflights the complete descendant closure before
    any source or descendant state is changed. Applying a plan revalidates the
    busy fence and then invalidates every planned descendant in one transaction.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._graph_cache: dict[
            str,
            tuple[
                dict[str, SceneAssetBeatState],
                dict[str, set[str]],
                dict[str, dict],
            ],
        ] = {}

    def impact_count(self, project_id: str, source_beat_id: str) -> int:
        _states, dependents_by_source, _snapshots = self._graph(project_id)
        return len(transitive_dependents(dependents_by_source, source_beat_id))

    def plan(
        self,
        project_id: str,
        source_beat_id: str,
        *,
        new_source_candidate_id: str | None = None,
    ) -> ContinuityInvalidationPlan:
        states_by_id, dependents_by_source, snapshots_by_beat = self._graph(project_id)
        new_artifact_id: str | None = None
        if new_source_candidate_id is not None:
            source_candidate = self.session.get(SceneAssetCandidate, new_source_candidate_id)
            if (
                source_candidate is None
                or source_candidate.project_id != project_id
                or source_candidate.beat_id != source_beat_id
            ):
                raise AppError(
                    "SCENE_ASSET_CANDIDATE_OWNERSHIP_INVALID",
                    "Continuity source candidate does not belong to the source beat.",
                    409,
                    retryable=False,
                    details={
                        "project_id": project_id,
                        "source_beat_id": source_beat_id,
                        "candidate_id": new_source_candidate_id,
                    },
                )
            new_artifact_id = str(source_candidate.artifact_id)

        skip_direct: set[str] = set()
        if new_artifact_id is not None:
            for dependent_id in dependents_by_source.get(source_beat_id, set()):
                snapshot = snapshots_by_beat.get(dependent_id)
                if snapshot and snapshot.get("source_asset_artifact_id") == new_artifact_id:
                    skip_direct.add(dependent_id)

        affected = tuple(
            transitive_dependents(
                dependents_by_source,
                source_beat_id,
                skip_direct=skip_direct,
            )
        )
        self._assert_not_busy(states_by_id, source_beat_id, affected)
        direct_source_by_beat = tuple(
            (
                beat_id,
                str((snapshots_by_beat.get(beat_id) or {}).get("source_beat_id") or source_beat_id),
            )
            for beat_id in affected
        )
        return ContinuityInvalidationPlan(
            project_id=project_id,
            source_beat_id=source_beat_id,
            affected_beat_ids=affected,
            direct_source_by_beat=direct_source_by_beat,
        )

    def apply(
        self,
        plan: ContinuityInvalidationPlan,
        *,
        now_iso: str | None = None,
        error_message: str = "Approved continuity source changed; regenerate this beat.",
    ) -> list[str]:
        now = now_iso or utc_now_iso()
        states = list(
            self.session.scalars(
                select(SceneAssetBeatState).where(
                    SceneAssetBeatState.project_id == plan.project_id,
                    SceneAssetBeatState.beat_id.in_(plan.affected_beat_ids),
                )
            )
        ) if plan.affected_beat_ids else []
        states_by_id = {str(state.beat_id): state for state in states}
        self._assert_not_busy(states_by_id, plan.source_beat_id, plan.affected_beat_ids)
        direct_source_by_beat = dict(plan.direct_source_by_beat)
        for beat_id in plan.affected_beat_ids:
            state = states_by_id.get(beat_id)
            if state is None:
                raise AppError(
                    "SCENE_ASSET_V2_STATE_NOT_FOUND",
                    "A planned continuity-dependent beat disappeared before invalidation.",
                    409,
                    retryable=True,
                    details={
                        "project_id": plan.project_id,
                        "source_beat_id": plan.source_beat_id,
                        "dependent_beat_id": beat_id,
                    },
                )
            state.current_draft_candidate_id = None
            state.selected_candidate_id = None
            state.resolution_status = "not_generated"
            state.review_status = "unreviewed"
            state.last_error_json = {
                "error_code": "SCENE_ASSET_CONTINUITY_SOURCE_CHANGED",
                "error_message": error_message,
                "error_details": {
                    "source_beat_id": plan.source_beat_id,
                    "direct_source_beat_id": direct_source_by_beat.get(beat_id, plan.source_beat_id),
                },
            }
            state.last_progress_at = now
            state.version += 1
            state.updated_at = now
        self._graph_cache.pop(plan.project_id, None)
        return list(plan.affected_beat_ids)

    def invalidate(
        self,
        project_id: str,
        source_beat_id: str,
        *,
        new_source_candidate_id: str | None = None,
        now_iso: str | None = None,
        error_message: str = "Approved continuity source changed; regenerate this beat.",
    ) -> list[str]:
        plan = self.plan(
            project_id,
            source_beat_id,
            new_source_candidate_id=new_source_candidate_id,
        )
        return self.apply(plan, now_iso=now_iso, error_message=error_message)

    def _graph(
        self,
        project_id: str,
    ) -> tuple[dict[str, SceneAssetBeatState], dict[str, set[str]], dict[str, dict]]:
        cached = self._graph_cache.get(project_id)
        if cached is not None:
            return cached
        states = list(
            self.session.scalars(
                select(SceneAssetBeatState).where(SceneAssetBeatState.project_id == project_id)
            )
        )
        states_by_id = {str(state.beat_id): state for state in states}
        dependents_by_source: dict[str, set[str]] = {}
        snapshots_by_beat: dict[str, dict] = {}
        for state in states:
            candidate_id = state.selected_candidate_id or state.current_draft_candidate_id
            if not candidate_id:
                continue
            candidate = self.session.get(SceneAssetCandidate, candidate_id)
            snapshot = candidate.continuity_snapshot_json if candidate is not None else None
            if not isinstance(snapshot, dict):
                continue
            source = str(snapshot.get("source_beat_id") or "")
            if not source:
                continue
            beat_id = str(state.beat_id)
            snapshots_by_beat[beat_id] = snapshot
            dependents_by_source.setdefault(source, set()).add(beat_id)
        graph = (states_by_id, dependents_by_source, snapshots_by_beat)
        self._graph_cache[project_id] = graph
        return graph

    @staticmethod
    def _assert_not_busy(
        states_by_id: Mapping[str, SceneAssetBeatState],
        source_beat_id: str,
        affected_beat_ids: tuple[str, ...] | list[str],
    ) -> None:
        for dependent_id in affected_beat_ids:
            state = states_by_id.get(dependent_id)
            if state is not None and state.active_operation_beat_id is not None:
                raise AppError(
                    "SCENE_ASSET_V2_DEPENDENCY_BUSY",
                    "A dependent Scene Asset beat is actively generating; retry after it finishes.",
                    409,
                    retryable=True,
                    details={
                        "source_beat_id": source_beat_id,
                        "dependent_beat_id": dependent_id,
                        "operation_beat_id": state.active_operation_beat_id,
                    },
                )