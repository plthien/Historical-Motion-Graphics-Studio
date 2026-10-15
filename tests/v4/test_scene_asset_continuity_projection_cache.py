from __future__ import annotations

from types import SimpleNamespace

from app.db.scene_asset_candidate import SceneAssetCandidate
from app.modules.v4.scene_asset_continuity import SceneAssetContinuityInvalidationService


class _FakeSession:
    def __init__(self) -> None:
        self.scalar_calls = 0
        self.get_calls = 0
        self.source = SimpleNamespace(
            beat_id="beat_source",
            selected_candidate_id=None,
            current_draft_candidate_id=None,
            active_operation_beat_id=None,
            version=1,
            updated_at=None,
        )
        self.dependent = SimpleNamespace(
            beat_id="beat_dependent",
            selected_candidate_id=None,
            current_draft_candidate_id="candidate_dependent",
            active_operation_beat_id=None,
            resolution_status="candidate_ready",
            review_status="current_draft",
            last_error_json=None,
            last_progress_at=None,
            version=1,
            updated_at=None,
        )
        self.candidate = SimpleNamespace(
            continuity_snapshot_json={"source_beat_id": "beat_source"},
        )

    def scalars(self, _statement):
        self.scalar_calls += 1
        return [self.source, self.dependent]

    def get(self, model, key):
        self.get_calls += 1
        if model is SceneAssetCandidate and key == "candidate_dependent":
            return self.candidate
        return None


def test_impact_count_reuses_graph_within_service_projection() -> None:
    session = _FakeSession()
    service = SceneAssetContinuityInvalidationService(session)

    assert service.impact_count("project_001", "beat_source") == 1
    assert service.impact_count("project_001", "beat_source") == 1

    assert session.scalar_calls == 1
    assert session.get_calls == 1


def test_apply_invalidates_cached_graph_before_next_projection() -> None:
    session = _FakeSession()
    service = SceneAssetContinuityInvalidationService(session)

    plan = service.plan("project_001", "beat_source")
    assert plan.affected_beat_ids == ("beat_dependent",)
    assert session.scalar_calls == 1

    service.apply(plan, now_iso="2026-08-17T08:30:00+00:00")
    assert session.scalar_calls == 2

    assert service.impact_count("project_001", "beat_source") == 0
    assert session.scalar_calls == 3
