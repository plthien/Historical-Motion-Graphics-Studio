
# Historical Motion Graphics Studio — STRICT CLEAN-CUT Implementation Plan v3

**STATUS: AUTHORITATIVE PROPOSED MIGRATION PLAN.** Supersedes v2 and all earlier roadmap assumptions wherever they propose retaining the old YouTube Studio runtime through P8, keeping old database tables/migrations, preserving old production artifacts, or supporting old Scene/Beat APIs.

**Repository:** plthien/Historical-Motion-Graphics-Studio.
**Baseline source audited:** main commit ea403973e6c6b23e5c3f073e83ca07ef9a8ca768 (2026-10-15).
**Change policy:** Work exclusively on named branches based on an explicitly checked main SHA. Never push to main, merge, rewrite default branch, or delete existing user-machine files without authorization. The legacy source is a DONOR for technical ideas and rigorously extracted generic utilities, not a supported product.

## 0. Unambiguous final-product rule

The delivered application must be **one new Historical Motion Graphics Studio**, with:

- Exactly one history-specific pipeline, domain model, route surface, database schema, migration head, storage namespace, launcher and release manifest.
- ZERO old YouTube AI Studio production mode, legacy toggles, old stage routes, adapters/compatibility readers, data migration/conversion, retained scene/beat image-generation pipeline, retired feature settings, worker processes or UI panels.
- ZERO import/open/read/write of old YTB SQLite databases or historical YTB project/artifact directories. Never point the new app at old DB/data paths. Never carry old project records, generated stills, old media operation history or old cache into the new application's storage.
- ZERO reuse of old business-specific schemas/tables/artifact enum values when merely renamed; model history-specific ownership afresh.
- NO LLM API, local LLM or automated external ChatGPT login/browser. Creative work remains **manual External ChatGPT prompt package → JSON file import → strict validation → review → approval**.
- Remotion is the only visual compositor and shared preview/export composition; FFmpeg/FFprobe may remain as neutral audio/metadata/encode helpers, not an alternate YTB image-slideshow engine.
- Real source-checked historical media with dates/usage rights/attribution; fictional illustration and schematic maps visibly differentiated.

**What KEEP means:** preserve behavior of independently useful technical primitives through an explicit extraction into NEW clean modules with fresh tests. It does NOT mean preserving the old package, workflow, route or database for compatibility. For example, port the strict JSON parser but DELETE the obsolete YTB authoring orchestration; port the Windows child-process guard but RETIRE the old FFmpeg still-image render host.

### Data-safety boundary: no legacy data in the new tool ≠ permission to destroy user data

During development, leave the pre-existing legacy files/DB on the user's workstation untouched. Do not import, migrate, ship, attach, seed or open them in the history app. Create a **new, empty, dedicated history storage root and SQLite file**. If old paths are explicitly configured, fail closed with HISTORY_LEGACY_STORAGE_UNSUPPORTED. Exclude .db, .sqlite, historical exports, cached images, WAVs, videos, secrets and binaries from repository/release artifacts. Deleting existing user files outside new tool scope requires a separate explicit instruction. Old source code on the feature branch is removed by the code cutover; no data deletion operation is required.

## 1. What the current main actually contains (donor inventory)

- Product shell: src/App.tsx; src/pages/StudioPage.tsx; src/layout/CockpitShell.tsx, GlobalSidebar, PipelineStepper; components/ui and theme. The current Studio has Topic Ideas, Script & Analysis, Style Bible, Voice, Scene Breakdown, Scene Assets, Assembly and Render.
- External ChatGPT tools: app/core/external_authoring.py, app/services/external_authoring_reservations.py, app/modules/platform/services.py, app/api/routes/platform.py, src/features/studio/PromptImportWorkspace.tsx and topic/script workspaces.
- Artifact/locking infrastructure: app/services/artifacts.py, app/core/hashing.py, app/repositories/core.py guarded publication and durable job mechanics.
- Voice: app/modules/v4/voice_*.py, elevenlabs_*.py, kokoro/ (Kokoro + ElevenLabs synthesis, final paced WAV, forced alignment, pacing and durable execution), linked to old Project/Scene/Beat invalidation.
- Process safety: app/modules/v4/windows_job_object.py, windows_subprocess_guard.py, host_owned_process.py, app/modules/media/durable_workflow/*.
- Legacy-specific source to remove as runtime owner: app/modules/v4/scene_breakdown_*.py, scene_asset_*.py, visual_reference_*.py, prompt_bundle_*.py, visual_plan_assembler.py, assembly_service.py, assembly_motion.py, render_graph.py, legacy render/assembly routes and old FFmpeg montage pipeline.
- Old data model: app/db/models.py Project has scene_plan_artifact_id, visual_plan_artifact_id, prompt_bundle_artifact_id, scene_asset_manifest_artifact_id, beat_timing_map_artifact_id, assembly_plan_artifact_id, etc. Alembic versions 001..031 are the YTB migration chain. NONE belong in the final history schema.
- Old startup/CI/docs: app/main.py mounts V4 and duplicate asset/reference routers; start.bat runs old media/render hosts; CI audits YTB still-image candidate; README/00_START_HERE and MANIFEST describe old product.
- The branch feat/r0-remotion-history-motion-poc has an isolated Remotion demo from BEFORE main was uploaded. Never merge it wholesale. Selectively copy reviewed composition ideas and install as clean new history modules.

See MAIN_CODEBASE_AUDIT_2026-10-15.md for verified file locations and source limits. Before deleting anything, Codex must generate a complete file-path ownership inventory from the actual repository tree and verify imports/transitive runtime references. The source code audit is not a claim that builds/tests have been run.

## 2. Target product and seven user-facing stages

1. **Topic Ideas:** local prompt builder → manual External ChatGPT JSON import → validate/review/approve selected historical topic or direct manual topic approval.
2. **Script & Historical Analysis:** manual External ChatGPT one combined exact spoken script + historical claims/period/geography/uncertainty JSON, atomic approval. ChatGPT source leads are unverified until checked.
3. **Voice & Alignment:** Kokoro local or optionally ElevenLabs TTS; approved final WAV and **real forced alignment on those exact bytes**, no estimated pseudo-word timings.
4. **Historical Assets:** External ChatGPT research-plan JSON; non-LLM archive search/metadata, manual upload/approved safe download, license/date/geography review, local immutable asset version set. No forced generated image for each beat.
5. **Visual Director:** External ChatGPT JSON specifying persistent visual setups, independently timed layer events, reusable archive/map/text/diagram components, supported motion presets, real word/phrase cues; approval.
6. **Motion Timeline:** deterministic compiler binds approved word timing and asset hashes to integer frames; Remotion Player is the same composition used for export, with responsive layer track editor and controlled versioned revisions.
7. **Render & Delivery:** one durable Node/Chromium Remotion renderer with supervised Windows process ownership; H.264 MP4, QA/report, source and license attributions.

Channel DNA / HistoryMotionStyle is context for all stages, not an old character-image-generation stage. Every visible UI control maps to an implemented history route and state. Incomplete stages must be honestly gated, not backed by old YTB fallbacks.

## 3. Absolute clean architecture and clean database contract

### New runtime/module namespace

- Python backend: app/history/{api,domain,authoring,topic,script,voice,assets,director,timeline,render,quality,storage}, with a history-only FastAPI entrypoint and no import dependency on app/modules/v4 or old app/api/routes/v4.
- Frontend: retain generic src/layout, theme, reusable components; implement src/features/history/*, history-specific API client types and new StageStepper/Studio orchestration. Do not render or import old SceneAssetsPanel/SceneBreakdownPanel/AssemblyPanel, not even behind a flag.
- Node: runtime/remotion/{src/components,src/composition,src/renderer,schemas,tests} with pinned same-version Remotion packages, local lockfile; shared Player and final render data contract. No arbitrary JSX or script from ChatGPT.
- New data and migration: history_app.db, NEW History Alembic head containing only history tables; no old chain or empty shim versions. New storage root history_data/ with approved assets, immutable artifacts, voice and renders created fresh. Never mount/import old YTB DB.
- App startup: check schema_marker=historical_motion_studio, schema_version; old YTB DB marker/schema → fail closed, no migration/backfill/drop. Config default must never inherit former app data-dir or DSN.
- No obsolete integrations in app settings: YTB image-per-beat provider, old scene jobs, RQ/Redis fallback, unused media queues, old render host flags, previous prompt bundle config.
- No old branch pseudo-history: past docs can remain as *development audit documents on a PR*, but final packaged tool has only one current README, startup guide, architecture guide, schema specs and active tests. Archive non-product docs outside packaged app or remove at final clean cut.

### Schema/authority

NEW schemas: HistoryChannelDNA, TopicIdeas/SelectedTopic, ScriptHistoricalAnalysis, VoiceManifest/WordTimingMap, AssetResearchPlan, ApprovedAssetManifest, HistoryMotionStyle, VisualMotionPlan, CompiledTimeline, RenderOperation, QCReport and FinalVideo. Each has history-owned immutable identifiers, content hashes, source fingerprint and revision.

Domain keys: channel/project/stage heads; candidate imports and reservations; immutable versioned asset+rights metadata; final WAV and actual alignment; approved asset hashes; visual setups, layers and motion events; compile fingerprint and render output.

No ScenePlan, BeatTimingMap, PromptBundle, SceneAssetManifest, old VisualReferenceSet, YTB AssemblyPlan or old RenderOperation's assembly/SRT/VTT source fields as canonical authority. Support transitions/crossfades with explicit event overlaps; persistent visual setup lives across many spoken sentences.

Imports are strict file-backed JSON with reserved IDs, duplicate key checks, unknown-field rejection, max size, source hash/lineage/ownership/approval and stale input checks; Validate-only never writes; Import revalidates; Approve uses CAS and staged immutable artifact publication. Invalid file → downloadable targeted repair prompt for External ChatGPT, never internal model calls.

History asset sources must have creator/date/period/geography/source URL/license/attribution/relevance evidence and local SHA256. Separate authentic vs reconstructed vs schematic. Renderer refuses unapproved asset refs/JS/CSS/remote arbitrary sources.

## 4. Required clean-cut implementation order: P0–P8

### P0 — Evidence and isolated Remotion proof (NO OLD APP MODIFICATION)

**Input:** current exact main SHA; donor YTB repo files as read-only sources.
**Work:** create full KEEP-EXTRACT/REWRITE/DELETE file ledger, identify transitive dependencies, current test baseline, dry-run extract boundaries. Port ONLY reviewed POC experiment code from old feature branch to a new branch based on main. Pin Remotion, lockfile, npm ci/test/build, actual headless 60s 1080p/30fps H.264 render with FFprobe frames and UI Player screenshot. One dedicated CI and visual reference comparison. Do not touch main or remove existing code in P0.
**Gate:** source ledger + verified actual render and tests; otherwise BLOCKED. P0 proof may be silent and is NOT complete video.
**No legacy support is implemented here:** legacy files are audited only as donor source; no new history runtime has been published yet.

### P1 — HARD CUTOVER TO EMPTY HISTORY PRODUCT (MANDATORY EARLY)

**Critical departure from v2: P1 is the one and ONLY product clean-cut. NO waiting until P8.**

Create history-only source, project, DB and UI skeleton and remove **all** active YTB runtime from the new product in this single atomic feature PR, before implementing Topic/Script/Voice/Assets stages. Extract reusable neutral code directly to history/shared modules and port the passing unit tests first.

Actions:
1. Prepare and review exhaustive file import graph/ledger: each old Python/TS/DB/schema/prompt/test/launcher path marked EXTRACT or DELETE. Anything reused must have new owner and independent tests; old package remains only until this change is committed.
2. Replace app/main.py router composition with exclusively historical API, including health and history Channel/Project. Remove all old V4/scene/beat/image-generation routes. No parallel running YTB app, feature flag, compatibility class or legacy alias endpoint.
3. Replace src/pages/StudioPage.tsx and old Stage IDs with new seven-stage skeleton and history DTOs. Old YTB-specific workspaces/panels/assets/route imports deleted, not hidden. Inactive history stages display honest prerequisite/Not Implemented state.
4. Create **a new history SQLite database and first migration**, not the legacy 001..031 chain. Remove old Alembic versions and project pointer columns from production source; no legacy DB migration, fallback or tables. Create fresh empty history storage root and schema marker. Do not ship old samples/media/cache/db.
5. Extract general artifact persistence, hashing/CAS/reservation/JSON validation, process guards and other demonstrably neutral primitives into clean ownership. Keep only focused tests for actual extracted behavior; delete legacy tests tied to removed behavior.
6. Wire historical launcher/CI/release manifest with new names/paths, no old render coordinator or scene asset worker startup, no old YTB settings. New DB may have empty project and no media, never legacy seeded project.
7. Add static and runtime **legacy closure** checks: no modules imported from app.modules.v4, no old route registration, no old schema/migration/DB table, no scene/beat stage in OpenAPI/Studio, no old image provider/env/config or dispatch, no legacy data path opened, no historical paths containing old user data.
8. Run clean Python/Node/history app startup + fresh migration + TypeScript build/UI screenshots; run against an old DB fixture and assert fail closed without mutations; dependency graph tests. Acceptance requires a working skeleton that can create an empty history Project and persist after restart.
**P1 gate:** New history-only app builds/runs from a clean installation, zero legacy runtime/stages/DB/migrations/API/code-support, old DB remains untouched outside product, and no LLM API. No later phase is allowed to reintroduce legacy support. If it fails, P1 remains BLOCKED and is not merged; do NOT ship a partial combined app.

### P2 — External ChatGPT historical topic and script

Adapt neutral prompt builder/JSON parser with NEW history contracts; Channel DNA, approved topic (including manual seed), Script+HistoricalAnalysis exact-text combined import and fact/citation review, reservations and atomic CAS. Build/Copy/Download/Import/Validate/Review/Approve/Repair. UI wired to real endpoints, reload resumes without stale prompts. Test malformed/duplicate/foreign/stale imports. **Never restore old YTB authoring service or old schema.**

### P3 — Clean history voice + real alignment

Port Kokoro/ElevenLabs core from extracted primitives, **not from app.modules.v4**; final audio hash and real forced-aligned word map; section retries, pacing identity, durable cancellation; no silent legacy BeatTimingMap or fallback estimated timings. If ElevenLabs alignment unavailable, block until verified non-LLM alignment solution explicitly enabled. Test actual WAV, real timestamps, retries and restart.

### P4 — History asset/evidence library

Research-plan via External ChatGPT JSON; safe non-LLM Wikimedia/LoC adapters and manual upload; per asset source/author/license/time/geography and relevance validation, approve/bind immutable versions. Schematic map labels distinct from fact. Optional illustration generation only as explicit user-invoked media service, never compulsory image for each beat. Test SSRF/path traversal/rights/asset versioning.

### P5 — Visual Director and deterministic compiler

External ChatGPT prompt+import VisualMotionPlan, strict setup/layer/event constraints, word anchors, composition grammar and editable review; targeted repair for bad JSON. Compile exact integer frames with audio/asset fingerprints; deterministic JSON/hash and stage invalidation. No rendered React code accepted from imported JSON. Test persistent setups across many sentences, no image-per-beat obligation.

### P6 — First fully narrated history MP4

Promote POC into production runtime/remotion with ONE shared composition, components Paper/Archive/Portrait/Map/Route/Headline/Stamp/Diagram/Camera/Text, with local approved assets; actual spoken cue synchronization; render 60–90s real non-silent MP4 + preview, source/rights manifest and FFprobe frame checks. Product must be history-only from P1 onward.

### P7 — Full Motion Timeline editor + durable Node renderer

Tracks for visual setups/layers/audio/camera/text; Play/seek, position/scale/crop/word cue/preset and per-layer undo candidate, preview and approve; exact selective recompute. Durable SQLite render operation with Node child process and Windows Job Object, bounded concurrent jobs, cancel/retry/crash recovery and no stale output publication. No FFmpeg YTB-montage fallback.

### P8 — QC, performance, release polish (NOT legacy removal)

P8 is ONLY QA/reliability/performance/documentation/packaging of the clean product: exact candidate SHA CI on Python/TypeScript/Node/Remotion/Windows, archive rights manifests, audio silence/clipping, black/frozen frames, typography safe zones, historical claim and period map review, UI screenshot parity and Windows install. Verify 60–90s and 5+ minute history projects. Clean source/bundle scans confirm P1 legacy closure remains intact. P8 must not contain deferred old YTB-support deletion.

**Milestone A:** P6 validated first narrated complete educational video with manual ChatGPT handoffs. **Milestone B:** P7/P8 responsive timeline, restart-safe rendering and quality release.

## 5. Test gates that must fail if ANY legacy support remains

Minimum closure checks added **in P1**, kept through P8:

- App package dependency DAG: no active imports of old app/modules/v4, old app/api/routes/v4, scene_* or scene_asset_* runtime; no compatibility alias/old stage switch.
- OpenAPI schema contains zero old Scene Breakdown/Scene Assets/Prompt Bundle/Visual References/YTB Assembly routes; history-only namespace and providers.
- Frontend compiled route/stage IDs contain zero old scene_assets, beat-specific generation, assembly-old settings and no images-per-beat UI action. All history buttons backed by API.
- SQLite new DB inspected: zero old YTB table names/columns, one first history migration and schema marker; attempts to use old YTB DB rejected and disk hash/mtime unchanged.
- New clean storage root: no old YTB database/media/voice/stills/cache/project artifact samples. Packaging never includes .db/.sqlite/.wav/.mp4/.png sourced from old data roots, secret files or user datasets. Explicitly permitted small licensed POC demo fixtures are documented separately.
- New launcher starts ONLY actual history services; no old scene media worker, old dedicated FFmpeg still-render host, old provider env vars or Redis/RQ fallback.
- Source/config scan rejects internal LLM API dependencies including retries and generated headers; allow explicit TTS/forced-alignment only.
- Historical assets manifest refuses unreviewed/unknown-license media as factual archive sources; visual templates cannot execute untrusted imports.
- Python/frontend/Remotion tests and Windows process/FFprobe gate on exact commit, with actual logs and no skipped required job. Any unrun required gate = BLOCKED, never PASS.

## 6. Git safety and implementation protocol

- Authoritative base for each feature is **explicit main SHA**, not the GitHub repository default branch (which previously pointed at an unrelated R0 prototype). No force-push, auto-merge, direct main writes or changes to GitHub settings.
- Model must NOT call the old YTB clone as repository destination. Historical Motion repo only.
- In P1, remove files at SOURCE LEVEL and tests tied to deprecated legacy features (after neutral extraction), not merely disable UI switches. Static closure audit must assert zero active old imports/routes/DB tables.
- A phase PR includes: affected exact files, before/after source graph, migration/stage schema, negative tests, screenshot/render/FFprobe proof (if applicable), user-data preservation audit, CI URLs and explicit PASS/PARTIAL/BLOCKED.
- No destructive cleanup of user-controlled directories outside the new tool root. All old data remains external and unsupported, rather than imported or deleted.
- If P1 closure cannot be completed, **do not ship a hybrid tool**; keep working on same feature branch/PR until one clean-cut passes review.

## 7. First assignment for coding model

First do P0 verification and complete file inventory. Then **P1 must hard-cut all legacy runtime/DB/UI before P2**. Do not defer old YTB removal to P8, do not create compatibility services or database migration, do not quietly use old source artifacts. Source branch references in old v1/v2 documents are historical, not executable instructions. Use docs/history/CODEX_P0_ON_NEW_MAIN_PROMPT.md (updated by this PR) and this v3 authority.
