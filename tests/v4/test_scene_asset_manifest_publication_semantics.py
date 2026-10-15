from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service
from app.services.artifacts import ArtifactService

NOW = "2026-08-20T10:00:00+00:00"
PROJECT_ID = "project_000000000001"
BEAT_ID = "beat_001"
SCENE_ID = "scene_001"
PROMPT_BUNDLE_ID = "artifact_000000000006"
PROMPT_HASH = "sha256:" + "a" * 64


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'scene_assets_publish.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _json_artifact(artifact_id: str) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"project/{PROJECT_ID}/test/{artifact_id}",
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.JSON,
        artifact_role=None,
        path=f"json/{artifact_id}.json",
        content_hash=f"hash_{artifact_id}",
        sha256="0" * 64,
        mime_type="application/json",
        size_bytes=2,
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _image_artifact(artifact_root: Path, artifact_id: str, byte: bytes) -> Artifact:
    relative = Path("images") / f"{artifact_id}.png"
    path = artifact_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(byte)
    digest = hashlib.sha256(byte).hexdigest()
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"project/{PROJECT_ID}/scene_assets/{BEAT_ID}/{artifact_id}",
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.IMAGE,
        artifact_role=None,
        path=relative.as_posix(),
        content_hash=digest,
        sha256=digest,
        mime_type="image/png",
        width=1280,
        height=720,
        size_bytes=len(byte),
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _upstream() -> tuple[dict, dict, dict]:
    treatment = {
        "visual_concept": "A clear approved frame.",
        "subject_action": "The subject holds a stable pose.",
        "environment_emphasis": "A readable studio background.",
        "framing": "medium",
        "camera_angle": "eye_level",
        "camera_movement": "static",
        "composition": "Centered and readable.",
        "lighting_direction": "soft front light",
        "color_emphasis": "neutral production colors",
        "motion_guidance": "Hold still.",
        "continuity_with_previous": "new_setup",
        "continuity_notes": "No prior continuity required.",
        "production_notes": "Use the selected approved image.",
        "negative_requirements": [],
        "visible_text": [],
    }
    visual_beat = {
        "scene_id": SCENE_ID,
        "beat_id": BEAT_ID,
        "beat_order": 1,
        "local_beat_order": 1,
        "continuity_group_id": "continuity_001",
        "computed_duration_seconds": 3.0,
        **treatment,
    }
    prompt_item = {
        "prompt_item_id": "prompt_001",
        "scene_id": SCENE_ID,
        "beat_id": BEAT_ID,
        "beat_order": 1,
        "local_beat_order": 1,
        "image_prompt": "Approved image prompt.",
        "negative_prompt": "No text.",
        "prompt_hash": PROMPT_HASH,
        "prompt_origin": "compiled",
        "generation_options": {"aspect_ratio": "16:9"},
        "reference_bindings": [],
        "continuity_reference_requirement": None,
    }
    return (
        {"scene_plan_revision_id": "artifact_000000000004", "scenes": [{"scene_id": SCENE_ID, "scene_order": 1, "beats": [{"beat_id": BEAT_ID, "script_quote": "Narration."}]}]},
        {"visual_plan_revision_id": "artifact_000000000005", "scenes": [{"scene_id": SCENE_ID, "scene_order": 1, "section_id": "sec_001", "section_order": 1, "section_title": "Section", "beat_plans": [visual_beat]}]},
        {"prompt_bundle_revision_id": PROMPT_BUNDLE_ID, "source_visual_reference_approval_set_artifact_id": None, "prompt_items": [prompt_item]},
    )


def _seed(session: Session, artifact_root: Path) -> None:
    source_ids = [
        "artifact_000000000001",
        "artifact_000000000002",
        "artifact_000000000003",
        "artifact_000000000004",
        "artifact_000000000005",
        PROMPT_BUNDLE_ID,
        "artifact_000000000101",
        "artifact_000000000102",
        "artifact_000000000103",
        "artifact_000000000104",
        "artifact_000000000105",
    ]
    session.add(Channel(channel_id="channel_publish", name="Publish", language="en", niche="test", audience="test", channel_dna_json={}, channel_context_json={}, created_at=NOW, updated_at=NOW))
    session.add_all([_json_artifact(artifact_id) for artifact_id in source_ids])
    session.add(_image_artifact(artifact_root, "artifact_000000000201", b"first image bytes"))
    session.add(_image_artifact(artifact_root, "artifact_000000000202", b"second image bytes"))
    session.flush()
    session.add(Project(
        project_id=PROJECT_ID,
        channel_id="channel_publish",
        name="Publish semantics",
        description=None,
        channel_context_json={},
        channel_dna_json={},
        script_artifact_id="artifact_000000000001",
        script_analysis_artifact_id="artifact_000000000002",
        style_bible_artifact_id="artifact_000000000003",
        scene_plan_artifact_id="artifact_000000000004",
        visual_plan_artifact_id="artifact_000000000005",
        prompt_bundle_artifact_id=PROMPT_BUNDLE_ID,
        created_at=NOW,
        updated_at=NOW,
    ))
    session.add(SceneAssetPromptRevision(
        prompt_revision_id="scene_asset_prompt_revision_000000000001",
        project_id=PROJECT_ID,
        scene_id=SCENE_ID,
        beat_id=BEAT_ID,
        revision_no=1,
        revision_kind="compiled",
        is_current=True,
        source_prompt_bundle_artifact_id=PROMPT_BUNDLE_ID,
        source_prompt_item_id="prompt_001",
        source_prompt_hash=PROMPT_HASH,
        image_prompt="Approved image prompt.",
        negative_prompt="No text.",
        created_at=NOW,
    ))
    session.flush()
    for index, artifact_id in enumerate(("artifact_000000000201", "artifact_000000000202"), start=1):
        session.add(SceneAssetCandidate(
            candidate_id=f"scene_asset_candidate_00000000000{index}",
            project_id=PROJECT_ID,
            scene_id=SCENE_ID,
            beat_id=BEAT_ID,
            artifact_id=artifact_id,
            source_kind="uploaded",
            operation_beat_id=None,
            operation_task_id=None,
            source_prompt_revision_id="scene_asset_prompt_revision_000000000001",
            source_prompt_bundle_artifact_id=PROMPT_BUNDLE_ID,
            source_prompt_item_id="prompt_001",
            source_prompt_hash=PROMPT_HASH,
            continuity_snapshot_json=None,
            created_at=NOW,
        ))
    session.flush()
    session.add(SceneAssetBeatState(
        project_id=PROJECT_ID,
        beat_id=BEAT_ID,
        scene_id=SCENE_ID,
        version=1,
        resolution_status="approved",
        review_status="approved",
        current_draft_candidate_id="scene_asset_candidate_000000000001",
        selected_candidate_id="scene_asset_candidate_000000000001",
        active_operation_beat_id=None,
        continuity_source_scene_id=None,
        continuity_source_beat_id=None,
        continuity_source_candidate_id=None,
        last_error_json=None,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    session.commit()


def _service(session: Session, monkeypatch, artifact_root: Path) -> SceneAssetWorkspaceV2Service:
    monkeypatch.setattr(ArtifactService, "_artifact_root", lambda self: artifact_root)
    service = SceneAssetWorkspaceV2Service(session)
    monkeypatch.setattr(service, "_load_upstream", lambda _project: _upstream())
    return service


def _manifest_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Artifact).where(Artifact.logical_id == f"project/{PROJECT_ID}/scene_asset_manifest")) or 0)


def test_repeated_identical_publish_returns_existing_manifest_without_downstream_invalidation(tmp_path: Path, monkeypatch) -> None:
    artifact_root = tmp_path / "artifacts"
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session, artifact_root)
        service = _service(session, monkeypatch, artifact_root)

        first = service.publish_approved_manifest(PROJECT_ID)
        assert first["already_current"] is False
        first_artifact_id = first["artifact_id"]
        assert _manifest_count(session) == 1

        project = session.get(Project, PROJECT_ID)
        assert project is not None
        project.assembly_plan_artifact_id = "artifact_000000000101"
        project.subtitle_srt_artifact_id = "artifact_000000000102"
        project.subtitle_vtt_artifact_id = "artifact_000000000103"
        project.final_video_artifact_id = "artifact_000000000104"
        session.commit()

        second = service.publish_approved_manifest(PROJECT_ID)
        assert second == {"project_id": PROJECT_ID, "artifact_id": first_artifact_id, "status": "approved", "beat_count": 1, "already_current": True}
        assert _manifest_count(session) == 1

        first_artifact = session.get(Artifact, first_artifact_id)
        assert first_artifact is not None
        assert first_artifact.is_current is True
        assert first_artifact.superseded_by_artifact_id is None
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        assert project.scene_asset_manifest_artifact_id == first_artifact_id
        assert project.assembly_plan_artifact_id == "artifact_000000000101"
        assert project.subtitle_srt_artifact_id == "artifact_000000000102"
        assert project.subtitle_vtt_artifact_id == "artifact_000000000103"
        assert project.final_video_artifact_id == "artifact_000000000104"

        workspace = service.workspace(PROJECT_ID)
        assert workspace["manifest_state"] == "current"
        assert workspace["actions"]["can_publish_manifest"] is False
        assert workspace["actions"]["can_approve_manifest"] is False


def test_changed_working_state_is_stale_and_republish_creates_new_revision(tmp_path: Path, monkeypatch) -> None:
    artifact_root = tmp_path / "artifacts"
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session, artifact_root)
        service = _service(session, monkeypatch, artifact_root)

        first = service.publish_approved_manifest(PROJECT_ID)
        first_artifact_id = first["artifact_id"]
        project = session.get(Project, PROJECT_ID)
        state = session.get(SceneAssetBeatState, (PROJECT_ID, BEAT_ID))
        assert project is not None and state is not None
        project.assembly_plan_artifact_id = "artifact_000000000101"
        project.subtitle_srt_artifact_id = "artifact_000000000102"
        project.subtitle_vtt_artifact_id = "artifact_000000000103"
        project.final_video_artifact_id = "artifact_000000000104"
        state.current_draft_candidate_id = "scene_asset_candidate_000000000002"
        state.selected_candidate_id = "scene_asset_candidate_000000000002"
        state.version += 1
        session.commit()

        stale_workspace = service.workspace(PROJECT_ID)
        assert stale_workspace["manifest_state"] == "stale"
        assert stale_workspace["actions"]["can_publish_manifest"] is True

        republished = service.publish_approved_manifest(PROJECT_ID)
        assert republished["already_current"] is False
        assert republished["artifact_id"] != first_artifact_id
        assert _manifest_count(session) == 2

        old_manifest = session.get(Artifact, first_artifact_id)
        project = session.get(Project, PROJECT_ID)
        assert old_manifest is not None and project is not None
        assert old_manifest.is_current is False
        assert old_manifest.superseded_by_artifact_id == republished["artifact_id"]
        assert project.scene_asset_manifest_artifact_id == republished["artifact_id"]
        assert project.assembly_plan_artifact_id is None
        assert project.subtitle_srt_artifact_id is None
        assert project.subtitle_vtt_artifact_id is None
        assert project.final_video_artifact_id is None
        for invalidated_id in (
            "artifact_000000000101",
            "artifact_000000000102",
            "artifact_000000000103",
            "artifact_000000000104",
        ):
            invalidated = session.get(Artifact, invalidated_id)
            assert invalidated is not None
            assert invalidated.is_current is False

        current_workspace = service.workspace(PROJECT_ID)
        assert current_workspace["manifest_state"] == "current"
        assert current_workspace["actions"]["can_publish_manifest"] is False


def test_manifest_candidate_inventory_has_deterministic_order(tmp_path: Path, monkeypatch) -> None:
    artifact_root = tmp_path / "artifacts"
    factory = _factory(tmp_path)
    with factory() as session:
        _seed(session, artifact_root)
        session.add(_image_artifact(artifact_root, "artifact_000000000203", b"third image bytes"))
        session.flush()
        session.add(
            SceneAssetCandidate(
                candidate_id="scene_asset_candidate_000000000000",
                project_id=PROJECT_ID,
                scene_id=SCENE_ID,
                beat_id=BEAT_ID,
                artifact_id="artifact_000000000203",
                source_kind="uploaded",
                operation_beat_id=None,
                operation_task_id=None,
                source_prompt_revision_id="scene_asset_prompt_revision_000000000001",
                source_prompt_bundle_artifact_id=PROMPT_BUNDLE_ID,
                source_prompt_item_id="prompt_001",
                source_prompt_hash=PROMPT_HASH,
                continuity_snapshot_json=None,
                created_at=NOW,
            )
        )
        session.commit()

        service = _service(session, monkeypatch, artifact_root)
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        scene_plan, visual_plan, prompt_bundle = _upstream()
        manifest = service._build_published_manifest(
            project,
            scene_plan,
            visual_plan,
            prompt_bundle,
            manifest_id="artifact_000000000999",
            parent_manifest_id=None,
        )

        entry = manifest["scenes"][0]["entries"][0]
        assert entry["candidate_asset_artifact_ids"] == [
            "artifact_000000000203",
            "artifact_000000000201",
            "artifact_000000000202",
        ]
