from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service


class _ProjectionObserved(RuntimeError):
    pass


class _Session:
    def __init__(self) -> None:
        self.calls = 0
        self.state = SimpleNamespace(beat_id="beat_001")
        self.candidate = SimpleNamespace(
            beat_id="beat_001",
            candidate_id="candidate_001",
            artifact_id="artifact_001",
            created_at="2026-08-17T08:30:00+00:00",
        )
        self.artifact = SimpleNamespace(artifact_id="artifact_001")

    def scalars(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return [self.state]
        if self.calls == 2:
            return [self.candidate]
        if self.calls == 3:
            return [self.artifact]
        raise AssertionError("workspace preload issued an unexpected extra query")


class _PromptRevisions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def prime_current(self, project_id: str, beat_ids):
        self.calls.append((project_id, tuple(beat_ids)))
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
        return (
            {"scenes": []},
            {"scenes": [{"beat_plans": [{"beat_id": "beat_001"}]}]},
            {"prompt_items": []},
        )

    def _workspace_payload(self, project, _scene_plan, _visual_plan, _prompt_bundle):
        assert project.project_id == "project_001"
        assert list(self._projection_candidates_by_beat) == ["beat_001"]
        assert self._projection_candidates_by_beat["beat_001"][0].candidate_id == "candidate_001"
        assert self._projection_artifacts_by_id["artifact_001"].artifact_id == "artifact_001"
        raise _ProjectionObserved


def test_workspace_preloads_projection_rows_once_and_clears_request_cache() -> None:
    session = _Session()
    service = _Service(session)

    with pytest.raises(_ProjectionObserved):
        service.workspace("project_001")

    assert session.calls == 3
    assert service.prompt_revisions.calls == [("project_001", ("beat_001",))]
    assert service._projection_candidates_by_beat is None
    assert service._projection_artifacts_by_id is None
