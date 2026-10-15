from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.prompt_composer_service import PromptComposerService
from app.modules.v4.scene_asset_continuity import (
    SceneAssetContinuityInvalidationService,
    transitive_dependents,
)
from app.modules.v4.scene_asset_prompt_v2 import SceneAssetPromptV2Service
from app.modules.v4.scene_asset_workspace_v2 import SceneAssetWorkspaceV2Service


NOW = "2026-08-17T01:00:00+00:00"
PROJECT_ID = "project_continuity"
PROMPT_ARTIFACT_ID = "artifact_prompt"
SOURCE_ARTIFACT_ID = "artifact_source"


def _prompt_revision_id(beat_id: str) -> str:
    return f"scene_asset_prompt_revision_{beat_id * 12}"


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'continuity.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(artifact_id: str, *, image: bool = False) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=(f"project/{PROJECT_ID}/scene_assets/test/{artifact_id}" if image else f"logical/{artifact_id}"),
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.IMAGE if image else ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.{'png' if image else 'json'}",
        content_hash=f"hash_{artifact_id}",
        sha256=(artifact_id.replace("artifact_", "") + "0" * 64)[:64] if image else f"sha_{artifact_id}",
        mime_type="image/png" if image else "application/json",
        size_bytes=1,
        width=1280 if image else None,
        height=720 if image else None,
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _seed_foundation(session: Session) -> None:
    session.add(
        Channel(
            channel_id="channel_continuity",
            name="Continuity",
            language="en",
            niche="test",
            audience="test",
            channel_dna_json={},
            channel_context_json={},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.add_all([
        _artifact(SOURCE_ARTIFACT_ID),
        _artifact(PROMPT_ARTIFACT_ID),
        *[_artifact(f"artifact_{beat_id}", image=True) for beat_id in ("a", "b", "c", "d")],
    ])
    session.flush()
    session.add(
        Project(
            project_id=PROJECT_ID,
            channel_id="channel_continuity",
            name="Continuity",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            script_artifact_id=SOURCE_ARTIFACT_ID,
            script_analysis_artifact_id=SOURCE_ARTIFACT_ID,
            style_bible_artifact_id=SOURCE_ARTIFACT_ID,
            scene_plan_artifact_id=SOURCE_ARTIFACT_ID,
            visual_plan_artifact_id=SOURCE_ARTIFACT_ID,
            prompt_bundle_artifact_id=PROMPT_ARTIFACT_ID,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    for beat_id in ("a", "b", "c", "d"):
        session.add(
            SceneAssetPromptRevision(
                prompt_revision_id=_prompt_revision_id(beat_id),
                project_id=PROJECT_ID,
                scene_id=f"scene_{beat_id}",
                beat_id=beat_id,
                revision_no=1,
                revision_kind="compiled",
                is_current=True,
                source_prompt_bundle_artifact_id=PROMPT_ARTIFACT_ID,
                source_prompt_item_id=f"prompt_{beat_id}",
                source_prompt_hash=f"hash_{beat_id}",
                image_prompt=f"prompt {beat_id}",
                negative_prompt="",
                created_at=NOW,
            )
        )
    session.flush()


def _candidate(beat_id: str, source_beat_id: str | None) -> SceneAssetCandidate:
    snapshot = None
    if source_beat_id is not None:
        snapshot = {
            "relation": "previous_beat",
            "source_beat_id": source_beat_id,
            "source_asset_artifact_id": f"artifact_{source_beat_id}",
            "source_asset_sha256": "a" * 64,
            "mime_type": "image/png",
            "source_kind": "approved",
        }
    return SceneAssetCandidate(
        candidate_id=f"candidate_{beat_id}",
        project_id=PROJECT_ID,
        scene_id=f"scene_{beat_id}",
        beat_id=beat_id,
        artifact_id=f"artifact_{beat_id}",
        source_kind="uploaded",
        operation_beat_id=None,
        operation_task_id=None,
        source_prompt_revision_id=_prompt_revision_id(beat_id),
        source_prompt_bundle_artifact_id=PROMPT_ARTIFACT_ID,
        source_prompt_item_id=f"prompt_{beat_id}",
        source_prompt_hash=f"hash_{beat_id}",
        continuity_snapshot_json=snapshot,
        created_at=NOW,
    )


def _seed_busy_operation(session: Session, beat_id: str) -> str:
    operation_id = "operation_busy"
    operation_beat_id = f"operation_beat_{beat_id}"
    session.add(
        SceneAssetWorkingOperation(
            operation_id=operation_id,
            project_id=PROJECT_ID,
            operation_type="generate_beat",
            status="running",
            source_script_artifact_id=SOURCE_ARTIFACT_ID,
            source_script_analysis_artifact_id=SOURCE_ARTIFACT_ID,
            source_style_bible_artifact_id=SOURCE_ARTIFACT_ID,
            source_scene_plan_artifact_id=SOURCE_ARTIFACT_ID,
            source_visual_plan_artifact_id=SOURCE_ARTIFACT_ID,
            source_prompt_bundle_artifact_id=PROMPT_ARTIFACT_ID,
            source_reference_approval_set_artifact_id=None,
            provider_settings_snapshot_json={"provider": "test"},
            provider_settings_snapshot_hash="provider_hash",
            total_beat_count=1,
            completed_count=0,
            failed_count=0,
            cancelled_count=0,
            next_reconcile_at=NOW,
            last_progress_at=NOW,
            reconcile_owner=None,
            reconcile_token=None,
            reconcile_lease_expires_at=None,
            cancel_requested_at=None,
            cancelled_at=None,
            request_json={},
            result_json={},
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        )
    )
    session.flush()
    session.add(
        SceneAssetOperationBeat(
            operation_beat_id=operation_beat_id,
            operation_id=operation_id,
            project_id=PROJECT_ID,
            scene_id=f"scene_{beat_id}",
            beat_id=beat_id,
            version=1,
            status="running",
            candidate_count=1,
            prompt_revision_id=_prompt_revision_id(beat_id),
            prompt_bundle_artifact_id=PROMPT_ARTIFACT_ID,
            prompt_item_id=f"prompt_{beat_id}",
            prompt_hash=f"hash_{beat_id}",
            image_prompt="test",
            negative_prompt="",
            generation_options_json={"aspect_ratio": "16:9"},
            generation_options_hash="options_hash",
            reference_bindings_json=[],
            reference_bindings_hash="bindings_hash",
            continuity_snapshot_json=None,
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        )
    )
    session.flush()
    return operation_beat_id


def _seed_graph(session: Session, *, busy_beat_id: str | None = None) -> None:
    _seed_foundation(session)
    session.add_all([
        _candidate("a", None),
        _candidate("b", "a"),
        _candidate("c", "b"),
        _candidate("d", "a"),
    ])
    session.flush()
    busy_operation_beat_id = _seed_busy_operation(session, busy_beat_id) if busy_beat_id else None
    for beat_id in ("a", "b", "c", "d"):
        session.add(
            SceneAssetBeatState(
                project_id=PROJECT_ID,
                beat_id=beat_id,
                scene_id=f"scene_{beat_id}",
                version=1,
                resolution_status="approved",
                review_status="approved",
                current_draft_candidate_id=f"candidate_{beat_id}",
                selected_candidate_id=f"candidate_{beat_id}",
                active_operation_beat_id=(busy_operation_beat_id if beat_id == busy_beat_id else None),
                continuity_source_scene_id=None,
                continuity_source_beat_id=None,
                continuity_source_candidate_id=None,
                last_error_json=None,
                last_progress_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    session.commit()


def _fake_prompt_edit(session: Session):
    def edit_beat(
        _self,
        project_id: str,
        beat_id: str,
        request: dict,
        *,
        before_commit=None,
    ) -> dict:
        assert project_id == PROJECT_ID
        assert beat_id == "a"
        assert request["expected_prompt_bundle_artifact_id"] == PROMPT_ARTIFACT_ID
        assert before_commit is not None
        before_commit()
        session.commit()
        return {"project_id": project_id, "beat_id": beat_id}

    return edit_beat


def _fake_prompt_restore(session: Session):
    def recompile_beat(
        _self,
        project_id: str,
        beat_id: str,
        request: dict,
        *,
        before_commit=None,
    ) -> dict:
        assert project_id == PROJECT_ID
        assert beat_id == "a"
        assert request["expected_prompt_bundle_artifact_id"] == PROMPT_ARTIFACT_ID
        assert before_commit is not None
        before_commit()
        session.commit()
        return {"project_id": project_id, "beat_id": beat_id}

    return recompile_beat


def _assert_source_and_dependents_invalidated(session: Session) -> None:
    source = session.get(SceneAssetBeatState, (PROJECT_ID, "a"))
    assert source is not None
    assert source.selected_candidate_id is None
    assert source.current_draft_candidate_id is None
    assert source.resolution_status == "not_generated"
    assert source.review_status == "unreviewed"
    assert source.version == 2
    for beat_id in ("b", "c", "d"):
        state = session.get(SceneAssetBeatState, (PROJECT_ID, beat_id))
        assert state is not None
        assert state.current_draft_candidate_id is None
        assert state.selected_candidate_id is None
        assert state.resolution_status == "not_generated"
        assert state.review_status == "unreviewed"
        assert state.version == 2
        assert state.last_error_json is not None
        assert state.last_error_json["error_code"] == "SCENE_ASSET_CONTINUITY_SOURCE_CHANGED"


def _assert_graph_unchanged(session: Session) -> None:
    for beat_id in ("a", "b", "c", "d"):
        state = session.get(SceneAssetBeatState, (PROJECT_ID, beat_id))
        assert state is not None
        assert state.current_draft_candidate_id == f"candidate_{beat_id}"
        assert state.selected_candidate_id == f"candidate_{beat_id}"
        assert state.resolution_status == "approved"
        assert state.review_status == "approved"
        assert state.version == 1


def test_transitive_dependents_is_deterministic_branching_and_cycle_safe() -> None:
    graph = {
        "a": {"d", "b"},
        "b": {"c"},
        "d": {"e"},
        "e": {"a"},
    }
    assert transitive_dependents(graph, "a") == ["b", "d", "c", "e"]
    assert transitive_dependents(graph, "a", skip_direct={"b"}) == ["d", "e"]


def test_prompt_edit_invalidates_full_transitive_closure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed_graph(session)
        monkeypatch.setattr(PromptComposerService, "edit_beat", _fake_prompt_edit(session))
        response = SceneAssetPromptV2Service(session).edit(
            PROJECT_ID,
            "a",
            expected_beat_version=1,
            expected_prompt_revision_id=_prompt_revision_id("a"),
            image_prompt="edited prompt",
            negative_prompt="",
        )
        assert response["beat_id"] == "a"
        session.expire_all()
        _assert_source_and_dependents_invalidated(session)


def test_prompt_restore_invalidates_full_transitive_closure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed_graph(session)
        monkeypatch.setattr(PromptComposerService, "recompile_beat", _fake_prompt_restore(session))
        response = SceneAssetPromptV2Service(session).restore(
            PROJECT_ID,
            "a",
            expected_beat_version=1,
            expected_prompt_revision_id=_prompt_revision_id("a"),
        )
        assert response["beat_id"] == "a"
        session.expire_all()
        _assert_source_and_dependents_invalidated(session)


def test_prompt_edit_deep_busy_descendant_aborts_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed_graph(session, busy_beat_id="c")
        monkeypatch.setattr(PromptComposerService, "edit_beat", _fake_prompt_edit(session))
        with pytest.raises(AppError) as exc:
            SceneAssetPromptV2Service(session).edit(
                PROJECT_ID,
                "a",
                expected_beat_version=1,
                expected_prompt_revision_id=_prompt_revision_id("a"),
                image_prompt="edited prompt",
                negative_prompt="",
            )
        assert exc.value.error_code == "SCENE_ASSET_V2_DEPENDENCY_BUSY"
        session.expire_all()
        _assert_graph_unchanged(session)


def test_prompt_restore_deep_busy_descendant_aborts_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed_graph(session, busy_beat_id="c")
        monkeypatch.setattr(PromptComposerService, "recompile_beat", _fake_prompt_restore(session))
        with pytest.raises(AppError) as exc:
            SceneAssetPromptV2Service(session).restore(
                PROJECT_ID,
                "a",
                expected_beat_version=1,
                expected_prompt_revision_id=_prompt_revision_id("a"),
            )
        assert exc.value.error_code == "SCENE_ASSET_V2_DEPENDENCY_BUSY"
        session.expire_all()
        _assert_graph_unchanged(session)


def test_shared_engine_preserves_direct_branches_already_on_new_source_artifact(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed_graph(session)
        candidate_a = session.get(SceneAssetCandidate, "candidate_a")
        assert candidate_a is not None
        service = SceneAssetContinuityInvalidationService(session)
        plan = service.plan(PROJECT_ID, "a", new_source_candidate_id="candidate_a")
        assert plan.affected_beat_ids == ()


def test_continuity_readiness_uses_exact_source_beat_across_scene_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed_graph(session)
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        workspace = SceneAssetWorkspaceV2Service(session)
        monkeypatch.setattr(
            workspace,
            "_assert_image_artifact",
            lambda _project, _beat_id, _artifact: None,
        )
        no_requirement = workspace._continuity_readiness(
            project,
            {
                "beat_id": "b",
                "scene_id": "scene_b",
                "continuity_reference_requirement": None,
            },
        )
        assert no_requirement == {
            "required": False,
            "source_beat_id": None,
            "source_ready": True,
            "source_candidate_id": None,
            "block_reason": None,
        }
        prompt_item = {
            "beat_id": "b",
            "scene_id": "scene_b",
            "continuity_reference_requirement": {
                "reference_tag": "prev_beat",
                "source_beat_id": "a",
                "relation": "same_setup",
            },
        }

        source = session.get(SceneAssetBeatState, (PROJECT_ID, "a"))
        assert source is not None
        source.resolution_status = "not_generated"
        source.review_status = "unreviewed"
        session.flush()

        blocked = workspace._continuity_readiness(project, prompt_item)
        assert blocked == {
            "required": True,
            "source_beat_id": "a",
            "source_ready": False,
            "source_candidate_id": None,
            "block_reason": "source_not_approved",
        }

        source.resolution_status = "approved"
        source.review_status = "approved"
        source.selected_candidate_id = "candidate_a"
        session.flush()

        ready = workspace._continuity_readiness(project, prompt_item)
        assert ready["required"] is True
        assert ready["source_beat_id"] == "a"
        assert ready["source_ready"] is True
        assert ready["source_candidate_id"] == "candidate_a"
        assert ready["block_reason"] is None


def test_invalidation_graph_contains_only_actual_snapshot_dependencies(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        _seed_graph(session)
        candidate_c = session.get(SceneAssetCandidate, "candidate_c")
        candidate_d = session.get(SceneAssetCandidate, "candidate_d")
        assert candidate_c is not None and candidate_d is not None
        candidate_c.continuity_snapshot_json = None
        candidate_d.continuity_snapshot_json = {
            "relation": "previous_beat",
            "source_beat_id": "c",
            "source_asset_artifact_id": "artifact_c",
            "source_asset_sha256": "a" * 64,
            "mime_type": "image/png",
            "source_kind": "approved",
        }
        session.flush()

        service = SceneAssetContinuityInvalidationService(session)
        assert service.plan(PROJECT_ID, "a").affected_beat_ids == ("b",)
        assert service.plan(PROJECT_ID, "c").affected_beat_ids == ("d",)


def test_transitive_invalidation_chain_is_scene_and_section_agnostic() -> None:
    # Scene Assets intentionally stores continuity ownership by exact Beat ID,
    # not Scene/Section locality. This graph therefore models an immediate
    # previous-Beat chain that may cross any planning-container boundary.
    graph = {
        "beat_a": {"beat_b"},
        "beat_b": {"beat_c"},
        "beat_c": {"beat_d"},
        "beat_d": {"beat_e"},
    }
    assert transitive_dependents(graph, "beat_a") == [
        "beat_b",
        "beat_c",
        "beat_d",
        "beat_e",
    ]
    # new_setup is represented by absence of a dependency edge and therefore
    # cuts the invalidation chain deterministically.
    cut_graph = {
        "beat_a": {"beat_b"},
        "beat_b": {"beat_c"},
        "beat_c": set(),
        "beat_d": {"beat_e"},
    }
    assert transitive_dependents(cut_graph, "beat_a") == [
        "beat_b",
        "beat_c",
    ]
