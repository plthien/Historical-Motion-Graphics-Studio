from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service


class _BeatProjected(RuntimeError):
    pass


class _Session:
    def __init__(self) -> None:
        self.scalars_calls = 0
        self.state = SimpleNamespace(active_operation_beat_id=None)
        self.candidate = SimpleNamespace(
            beat_id="beat_001",
            candidate_id="candidate_001",
            artifact_id="artifact_001",
            created_at="2026-08-17T08:30:00+00:00",
        )
        self.artifact = SimpleNamespace(artifact_id="artifact_001")

    def get(self, model, key):
        assert model is SceneAssetBeatState
        assert key == ("project_001", "beat_001")
        return self.state

    def scalars(self, _statement):
        self.scalars_calls += 1
        if self.scalars_calls == 1:
            return [self.candidate]
        if self.scalars_calls == 2:
            return [self.artifact]
        raise AssertionError("beat-local projection issued an unexpected extra preload query")


class _PromptRevisions:
    def __init__(self) -> None:
        self.calls = 0

    def prime_current(self, project_id: str, beat_ids):
        assert project_id == "project_001"
        assert list(beat_ids) == ["beat_001"]
        self.calls += 1
        return []


class _Service(SceneAssetWorkspaceV2Service):
    def __init__(self, session) -> None:
        self.session = session
        self.prompt_revisions = _PromptRevisions()
        self._projection_candidates_by_beat = None
        self._projection_artifacts_by_id = None

    def _project_required(self, project_id: str):
        return SimpleNamespace(project_id=project_id)

    def _load_upstream(self, _project):
        prompt_item = {
            "beat_id": "beat_001",
            "scene_id": "scene_001",
        }
        return (
            {"scenes": [{"beats": [{"beat_id": "beat_001"}]}]},
            {"scenes": [{"beat_plans": [{"beat_id": "beat_001"}]}]},
            {"prompt_items": [prompt_item]},
        )

    def _active_operation_beat_ids(self, _project_id: str):
        return set()

    def _workspace_payload(self, *_args, **_kwargs):
        raise AssertionError("beat endpoint must not build the full workspace")

    def _project_beat(self, _project, _prompt_item, _visual_beat, _scene_plan_beat, _state, _active):
        assert self._projection_candidates_by_beat["beat_001"][0].candidate_id == "candidate_001"
        assert self._projection_artifacts_by_id["artifact_001"].artifact_id == "artifact_001"
        raise _BeatProjected


def test_beat_endpoint_projects_target_without_building_full_workspace() -> None:
    session = _Session()
    service = _Service(session)

    with pytest.raises(_BeatProjected):
        service.beat("project_001", "beat_001")

    assert session.scalars_calls == 2
    assert service.prompt_revisions.calls == 1
    assert service._projection_candidates_by_beat is None
    assert service._projection_artifacts_by_id is None
