from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routes.artifacts import router as artifacts_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.settings import router as settings_router
from app.api.routes.voice_pacing_calibration import router as voice_pacing_calibration_router
from app.api.routes.voice_pacing_acceptance import router as voice_pacing_acceptance_router
from app.api.routes.system import router as system_router
from app.api.routes.platform import router as platform_router
from app.api.routes.visual_references_canonical_v2 import router as visual_references_canonical_v2_router
from app.api.routes.scene_breakdown_two_step import router as scene_breakdown_two_step_router
from app.api.routes.v4 import router as v4_router
from app.api.routes.scene_assets_v2 import router as scene_assets_v2_router
from app.api.routes.scene_assets_workspace_v2 import router as scene_assets_workspace_v2_router
from app.api.routes.scene_assets_section_approval_v2 import router as scene_assets_section_approval_v2_router
from app.api.routes.visual_references_v2 import router as visual_references_v2_router
from app.api.routes.visual_reference_publication_v2 import router as visual_reference_publication_v2_router
from app.api.routes.production_v4 import router as production_v4_router
from app.core.config import get_settings
from app.core.version import APP_VERSION
from app.db import scene_asset_working_models as _scene_asset_working_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_working_models  # noqa: F401
from app.db.migrations import upgrade_database
from app.db.session import build_session_factory
from app.modules.media.media_ownership_recovery import recover_media_ownership
from app.modules.media.visual_reference_v2_recovery import recover_visual_reference_v2_media_ownership


_LEGACY_COMBINED_SCENE_AUTHORING_ROUTES = frozenset(
    {
        "get_pending_scene_breakdown_prompt_v4",
        "build_scene_breakdown_prompt_v4",
        "validate_scene_breakdown_import_v4",
        "import_scene_breakdown_v4",
    }
)


@asynccontextmanager
async def app_lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.auto_apply_migrations:
        upgrade_database(settings)
    session_factory = build_session_factory(settings)
    # Visual Reference V2 owns its durable workflow graph. Release provider
    # transport slots whose exact durable owner is gone before generic media
    # recovery examines the remaining transport records.
    recover_visual_reference_v2_media_ownership(session_factory)
    recover_media_ownership(session_factory, settings)
    yield


def _disable_legacy_combined_scene_authoring() -> None:
    """Hard-cut over Scene Breakdown authoring to the two-step flow.

    Canonical Scene Breakdown read/review/approval routes remain mounted. Only
    the superseded combined external prompt/import surface is removed from the
    runtime and generated OpenAPI document.
    """

    v4_router.routes[:] = [
        route
        for route in v4_router.routes
        if getattr(route, "name", None) not in _LEGACY_COMBINED_SCENE_AUTHORING_ROUTES
    ]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="YouTube AI Studio V3 Platform API", version=APP_VERSION, lifespan=app_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_origin_regex=settings.cors_allow_origin_regex,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(system_router)
    app.include_router(settings_router)
    app.include_router(voice_pacing_calibration_router)
    app.include_router(voice_pacing_acceptance_router)
    app.include_router(jobs_router)
    app.include_router(artifacts_router)
    app.include_router(platform_router)
    # Hard cutover: canonical Visual Reference paths are owned by the durable
    # V2 runtime. Register this router before the historical V4 router because
    # FastAPI resolves duplicate path/method combinations in registration order.
    app.include_router(visual_references_canonical_v2_router)
    # Scene Breakdown stays one Studio stage, but external authoring is now
    # split into semantic Scene/Beat lock and Visual Plan authoring routes.
    app.include_router(scene_breakdown_two_step_router)
    _disable_legacy_combined_scene_authoring()
    app.include_router(v4_router)
    # Explicit V2 diagnostic/targeted-read endpoints stay available alongside
    # canonical paths and share the same durable working-state authority.
    app.include_router(visual_references_v2_router)
    app.include_router(visual_reference_publication_v2_router)
    # Scene Assets V2 runtime and workspace routers are registered before the
    # legacy Production V4 router. FastAPI resolves overlapping path/method
    # combinations in registration order, so all live Scene Asset execution,
    # workspace, upload, review, cancel, and publication traffic is owned by V2.
    app.include_router(scene_assets_workspace_v2_router)
    app.include_router(scene_assets_section_approval_v2_router)
    app.include_router(scene_assets_v2_router)
    app.include_router(production_v4_router)
    return app


app = create_app()
