from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.errors import AppError
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service


class FakeSession:
    def __init__(self, candidates):
        self.candidates = candidates
        self.executed = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def execute(self, statement):
        self.executed.append(str(statement))

    def get(self, _model, identity):
        return self.candidates.get(identity)

    def flush(self):
        self.flush_count += 1

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


class FakeContinuity:
    def __init__(self, states):
        self.states = states
        self.plan_calls = []
        self.apply_calls = []

    def plan(self, project_id, beat_id, *, new_source_candidate_id=None):
        plan = SimpleNamespace(
            project_id=project_id,
            source_beat_id=beat_id,
            new_source_candidate_id=new_source_candidate_id,
        )
        self.plan_calls.append(plan)
        return plan

    def apply(self, plan, *, now_iso=None):
        self.apply_calls.append((plan, now_iso))
        if plan.source_beat_id != "beat_001":
            return []
        dependent = self.states["beat_002"]
        dependent.current_draft_candidate_id = None
        dependent.selected_candidate_id = None
        dependent.resolution_status = "not_generated"
        dependent.review_status = "unreviewed"
        dependent.version += 1
        return ["beat_002"]


class SectionApprovalHarness(SceneAssetWorkspaceV2Service):
    def __init__(self, session, states):
        self.session = session
        self.states = states
        self.continuity = FakeContinuity(states)
        self.artifact_rows = SimpleNamespace(get=lambda _artifact_id: object())

    def _project_required(self, project_id):
        return SimpleNamespace(project_id=project_id)

    def _load_upstream(self, _project):
        return (
            {},
            {
                "scenes": [
                    {
                        "section_id": "sec_001",
                        "beat_plans": [
                            {"beat_id": "beat_001"},
                            {"beat_id": "beat_002"},
                        ],
                    },
                    {
                        "section_id": "sec_002",
                        "beat_plans": [{"beat_id": "beat_003"}],
                    },
                ],
            },
            {},
        )

    def _prompt_item(self, _prompt_bundle, beat_id):
        return {"beat_id": beat_id}

    def _current_prompt_revision(self, _project, _prompt_item):
        return SimpleNamespace(prompt_revision_id="revision")

    def _ensure_state(self, _project, prompt_item):
        return self.states[prompt_item["beat_id"]]

    def _assert_expected_version(self, state, expected):
        if state.version != expected:
            raise AssertionError((state.version, expected))

    def _assert_mutable_beat(self, _state, *, action):
        assert action == "review"

    def _is_locked(self, state):
        return state.resolution_status == "approved"

    def _assert_candidate_current_prompt(self, _project, _prompt_item, _candidate):
        return None

    def _assert_candidate_current_continuity(self, _project, _prompt_item, _candidate):
        return None

    def _assert_image_artifact(self, _project, _beat_id, _artifact):
        return None

    def _workspace_actions_payload(self, _project, _scene_plan, _visual_plan=None, _prompt_bundle=None):
        approved = sum(1 for state in self.states.values() if state.resolution_status == "approved")
        return {
            "can_generate_all": False,
            "can_generate_missing": False,
            "can_retry_failed": False,
            "can_approve_manifest": False,
            "can_publish_manifest": False,
            "eligible_generate_count": 0,
            "failed_count": 0,
            "approved_count": approved,
            "total_count": 3,
        }


def _state(beat_id, candidate_id, version):
    return SimpleNamespace(
        beat_id=beat_id,
        version=version,
        resolution_status="candidate_ready",
        review_status="current_draft",
        current_draft_candidate_id=candidate_id,
        selected_candidate_id=None,
        continuity_source_scene_id=None,
        continuity_source_beat_id=None,
        continuity_source_candidate_id=None,
        last_error_json=None,
        last_progress_at=None,
        updated_at=None,
    )


def _candidate(project_id, beat_id, candidate_id):
    return SimpleNamespace(
        candidate_id=candidate_id,
        project_id=project_id,
        beat_id=beat_id,
        artifact_id=f"artifact_{beat_id}",
    )


def _service():
    project_id = "project_a"
    states = {
        "beat_001": _state("beat_001", "candidate_001", 4),
        "beat_002": _state("beat_002", "candidate_002", 7),
        "beat_003": _state("beat_003", "candidate_003", 2),
    }
    candidates = {
        "candidate_001": _candidate(project_id, "beat_001", "candidate_001"),
        "candidate_002": _candidate(project_id, "beat_002", "candidate_002"),
        "candidate_003": _candidate(project_id, "beat_003", "candidate_003"),
    }
    session = FakeSession(candidates)
    return SectionApprovalHarness(session, states), session, states


def test_section_approval_is_one_transaction_and_skips_later_requested_beat_invalidated_by_earlier_approval():
    service, session, states = _service()

    response = service.approve_section_ready(
        "project_a",
        "sec_001",
        beats=[
            {"beat_id": "beat_001", "candidate_id": "candidate_001", "expected_beat_version": 4},
            {"beat_id": "beat_002", "candidate_id": "candidate_002", "expected_beat_version": 7},
        ],
    )

    assert session.executed == ["BEGIN IMMEDIATE"]
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert response["approved_beat_ids"] == ["beat_001"]
    assert response["skipped_beat_ids"] == ["beat_002"]
    assert response["invalidated_beat_ids"] == ["beat_002"]
    assert response["published_manifest_artifact_id"] is None
    assert response["published_manifest_status"] is None
    assert response["manifest_state"] == "unpublished"

    source = states["beat_001"]
    assert source.resolution_status == "approved"
    assert source.review_status == "approved"
    assert source.selected_candidate_id == "candidate_001"
    assert source.version == 5

    dependent = states["beat_002"]
    assert dependent.resolution_status == "not_generated"
    assert dependent.current_draft_candidate_id is None
    assert dependent.version == 8


def test_section_approval_rejects_cross_section_scope_and_rolls_back():
    service, session, _states = _service()

    with pytest.raises(AppError) as exc_info:
        service.approve_section_ready(
            "project_a",
            "sec_001",
            beats=[
                {"beat_id": "beat_003", "candidate_id": "candidate_003", "expected_beat_version": 2},
            ],
        )

    assert exc_info.value.error_code == "SCENE_ASSET_SECTION_APPROVAL_SCOPE_INVALID"
    assert session.commit_count == 0
    assert session.rollback_count == 1
