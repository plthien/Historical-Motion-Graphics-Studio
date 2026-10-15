from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_models  # noqa: F401
from app.db.base import Base
from app.db.models import Setting
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.scene_asset_provider_execution import SceneAssetProviderExecutionService
from app.modules.v4.scene_asset_runtime_v2 import SceneAssetRuntimeV2
from app.modules.v4.visual_reference_provider_execution import VisualReferenceProviderExecutionService
from app.modules.v4.visual_reference_runtime_v2 import VisualReferenceRuntimeV2
from app.repositories import SettingsRepository
from app.services.media_settings import SECRET_KEY, SETTINGS_KEY, MediaProviderSettingsService

NOW = "2026-09-02T09:45:00+00:00"
_RETIRED = {"supported_image_aspect_ratios", "max_poll_attempts"}


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(
        f"sqlite:///{(tmp_path / 'media_provider_snapshot_bridge.sqlite3').resolve()}"
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _seed_settings(factory: sessionmaker[Session], *, include_retired: bool = False) -> None:
    public: dict[str, object] = {
        "provider": "glabs_webhook",
        "enabled": True,
        "base_url": "http://127.0.0.1:8765",
        "api_key_configured": True,
        "default_image_model": "nano_banana_2",
        "default_image_aspect_ratio": "1:1",
        "default_upscale": [],
        "poll_interval_seconds": 4,
        "max_parallel_tasks": 3,
        "enabled_media_kinds": ["image"],
    }
    if include_retired:
        public["supported_image_aspect_ratios"] = ["1:1", "16:9"]
        public["max_poll_attempts"] = 100
    with factory() as session:
        session.add(Setting(key=SETTINGS_KEY, value_json=public, updated_at=NOW))
        session.add(Setting(key=SECRET_KEY, value_json={"glabs_webhook": "test-key"}, updated_at=NOW))
        session.commit()


def _operation(operation_id: str, snapshot: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        operation_id=operation_id,
        provider_settings_snapshot_json=snapshot,
        provider_settings_snapshot_hash=canonical_hash(snapshot),
    )


def test_persisted_pre_v3_media_settings_drop_only_known_retired_keys(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed_settings(factory, include_retired=True)
    with factory() as session:
        service = MediaProviderSettingsService(session, SettingsRepository(session))
        backend = service.get_settings_for_backend()
        assert _RETIRED.isdisjoint(backend)
        service._validate_runtime_ready(backend)


def test_visual_reference_runtime_snapshot_round_trips_into_provider_contract(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed_settings(factory)
    with factory() as session:
        frozen = VisualReferenceRuntimeV2(session)._provider_snapshot()
    assert _RETIRED.isdisjoint(frozen)
    operation = _operation("vr_bridge", frozen)
    with factory() as session:
        merged = VisualReferenceProviderExecutionService(factory)._frozen_provider_settings(session, operation)
    assert merged["provider"] == "glabs_webhook"
    assert merged["api_key"] == "test-key"
    assert _RETIRED.isdisjoint(merged)


def test_visual_reference_queued_legacy_snapshot_recovers_without_rewriting_hash(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed_settings(factory, include_retired=True)
    with factory() as session:
        frozen = VisualReferenceRuntimeV2(session)._provider_snapshot()
    frozen["supported_image_aspect_ratios"] = None
    frozen["max_poll_attempts"] = None
    operation = _operation("vr_legacy", frozen)
    original_hash = operation.provider_settings_snapshot_hash
    with factory() as session:
        merged = VisualReferenceProviderExecutionService(factory)._frozen_provider_settings(session, operation)
    assert operation.provider_settings_snapshot_hash == original_hash
    assert canonical_hash(operation.provider_settings_snapshot_json) == original_hash
    assert _RETIRED.isdisjoint(merged)


def test_visual_reference_unknown_frozen_field_remains_fail_closed(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed_settings(factory)
    with factory() as session:
        frozen = VisualReferenceRuntimeV2(session)._provider_snapshot()
    frozen["unexpected_provider_knob"] = "unsafe"
    operation = _operation("vr_unknown", frozen)
    with factory() as session, pytest.raises(AppError) as caught:
        VisualReferenceProviderExecutionService(factory)._frozen_provider_settings(session, operation)
    assert caught.value.error_code == "VISUAL_REFERENCE_V2_PROVIDER_SNAPSHOT_INVALID"
    assert caught.value.details["unknown_fields"] == ["unexpected_provider_knob"]


def test_scene_asset_runtime_snapshot_round_trips_into_provider_contract(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed_settings(factory)
    with factory() as session:
        frozen = SceneAssetRuntimeV2(session)._provider_snapshot()
    assert _RETIRED.isdisjoint(frozen)
    operation = _operation("scene_bridge", frozen)
    with factory() as session:
        merged = SceneAssetProviderExecutionService(factory)._frozen_provider_settings(session, operation)
    assert merged["provider"] == "glabs_webhook"
    assert merged["api_key"] == "test-key"
    assert _RETIRED.isdisjoint(merged)


def test_scene_asset_queued_legacy_snapshot_recovers_and_unknown_stays_closed(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed_settings(factory, include_retired=True)
    with factory() as session:
        frozen = SceneAssetRuntimeV2(session)._provider_snapshot()
    frozen["supported_image_aspect_ratios"] = ["1:1", "16:9"]
    frozen["max_poll_attempts"] = 100
    operation = _operation("scene_legacy", frozen)
    original_hash = operation.provider_settings_snapshot_hash
    with factory() as session:
        merged = SceneAssetProviderExecutionService(factory)._frozen_provider_settings(session, operation)
    assert canonical_hash(operation.provider_settings_snapshot_json) == original_hash
    assert _RETIRED.isdisjoint(merged)

    bad = dict(frozen)
    bad["unexpected_provider_knob"] = "unsafe"
    bad_operation = _operation("scene_unknown", bad)
    with factory() as session, pytest.raises(AppError) as caught:
        SceneAssetProviderExecutionService(factory)._frozen_provider_settings(session, bad_operation)
    assert caught.value.error_code == "SCENE_ASSET_V2_PROVIDER_SNAPSHOT_INVALID"
    assert caught.value.details["unknown_fields"] == ["unexpected_provider_knob"]
