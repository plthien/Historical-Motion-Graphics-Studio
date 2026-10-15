# P1 — Atomic YTB removal and history-only clean foundation

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no LLM local, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Required preconditions
- P0 technical gate accepted; exact main/staging HEAD recorded.
- Complete source ledger and transitive import graph prepared. This is ONE atomic feature PR; do not split into a partially active hybrid Studio. No P2 until this passes.

## Scope and file ownership
- Backend: replace old `app/main.py` composition and history-only routes, fresh `app/history/{api,domain,storage,authoring}/` (or one equivalent consistent namespace). Remove active imports and source of obsolete `app/modules/v4` scene/beat/prompt bundle/image generation/render graph, old production routes, old media jobs and old transition adapters. Preserve tested generic utilities by extracting to clean packages BEFORE removing old imports.
- Database: replace `app/db/models.py` domain schema; fresh `alembic/versions/001_history_initial.py` ONLY, delete YTB 001..031 migrations, separate root `history_data/` and `history_app.db`, history schema marker and fail-closed foreign/old SQLite detection.
- Frontend: keep theme/layout/CockpitShell/selected Channel/Project navigation; replace `src/pages/StudioPage.tsx`, `src/features/studio/types.ts` and old DTOs with `src/features/history/` and exactly seven history stage IDs. Delete old SceneBreakdownPanel, SceneAssetsPanel, old Assembly/VisualReference/YTB modal implementations and their obsolete tests, not merely feature-flag them.
- Runtime: remove old scene/asset workers and old FFmpeg montage host from `start.bat` / `scripts/windows/`; adjust health checks, Python dependencies, package scripts, OpenAPI and CI to one history-only application. Do not start a placeholder Remotion daemon before it exists.
- Retire obsolete YTB contracts/prompts/repositories/models/fixtures/docs as product authority. Maintain only project-neutral helper code and appropriate tests. No root MANIFEST.json, no generator/check workflow.

## New minimal data model (finalize fields and constraints before implementation)
- `channels(channel_id,language,niche,history_dna_json,revision,created_at,...)`.
- `projects(project_id,channel_id,name,history_context_json,created_at,...)`.
- `stage_heads(project_id,stage_key,artifact_id,revision,updated_at)` UNIQUE(project_id, stage_key). Allowed stage_key = topic, script_analysis, voice, assets, visual_director, motion_timeline, final_video. No old scene/beat IDs.
- `artifacts(artifact_id,project_id,kind,content_hash,storage_uri,status,version,created_at,...)`; immutable versions and staged/published transitions.
- `authoring_reservations(reservation_id,project_id,stage_key,parent_fingerprint,status,created_at,...)`; no old YTB rows.
- `jobs` only if neutral persisted execution already needed, with no legacy provider enums. Future Voice/Render tables introduced in owner phase.
- SQLite WAL and FK on, transactionally guarded CAS for head swaps. No side effects during validate-only.

## Required minimal routes/UI
- GET /health, POST/GET /api/history/channels, POST/GET /api/history/projects, GET /api/history/projects/{id}/status with stage readiness + blocked_reason.
- GET/POST basic selected Channel/Project; project persistence and restart.
- Stage skeleton shows Topic, Script, Voice, Historical Assets, Visual Director, Motion Timeline, Render & Delivery; unfinished stages disabled with real reason. No active fake buttons; dark Cockpit style preserved.
- New API app must NEVER import legacy `app.modules.v4` or read legacy storage.

## Exact implementation sequence
1. Generate and freeze complete donor extract/deletion graph + before/after table/API route catalog.
2. Extract only generic JSON duplicate-key parser, hash/CAS, artifact staging, paths/process guard and UI shell; write new independent tests.
3. Implement fresh DB/schema marker/init and legacy-DB rejection with content-hash/mtime immutability tests.
4. Implement new routes/project/status and UI shell on history DTOs; ensure backend/frontend compile.
5. Delete old implementations/routes/fixtures/migrations/tests and old Windows hosts as one atomic source tree change.
6. Run `python -m pytest -q tests/history`, relevant neutral test suites, TypeScript build, API startup+OpenAPI check, clean fresh DB/restart, Windows launcher smoke and new CI closure scanner. Ship only when all pass.

## Negative acceptance and no-legacy closure
- A copy of the old YTB DB passed via config fails with `HISTORY_LEGACY_STORAGE_UNSUPPORTED` before ANY write; SHA/mtime preserved. Old files remain on user's machine but are NEVER read/imported/shipped/deleted.
- Inspect SQLite schema: zero scene/beat/scene_asset/prompt_bundle/YTB tables and zero older Alembic revisions.
- Regex/AST static scan + runtime dependency graph show ZERO active old V4, old route/panels/services/provider config or old data reader.
- OpenAPI contains only history routes and neutral health; frontend contains only history stage IDs. No hidden mode toggle, aliased compatibility endpoint or inactive source support.
- Test one empty Channel/Project survives backend restart; no migrations of old data; validate-only causes no mutations.
- Windows UI screenshots 1440×900 and 1920×1080; no LLM API; CI exact HEAD verified. If one missing, P1 BLOCKED.
