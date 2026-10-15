# Current-main codebase audit — Historical Motion Graphics Studio

Date: 2026-10-15. Scope: read-only inspection of \`plthien/Historical-Motion-Graphics-Studio\`, branch **main**, commit \`ea403973e6c6b23e5c3f073e83ca07ef9a8ca768\`. Audit is source-review only, NOT a successful build or runtime demonstration.

## Critical branch and product state

- User pushed the YTB AI Studio V3 *static-image production codebase* to **main**. README.md and 00_START_HERE.md still describe the legacy product and single-image-per-beat production. This is the NEW baseline for the migration.
- The separate \`feat/r0-remotion-history-motion-poc\` branch holds the historical Remotion experimental code and previous generic plan. It does NOT contain the newly pushed YTB main source tree. Do NOT merge the two branches wholesale or derive application changes from the old feature branch. Port individual reviewed POC files to a fresh branch based on current main.
- GitHub metadata lists the old feature branch as default at audit time, even though \`main\` exists. Always explicitly select \`main\` as source/base. Do not change repository settings or default branch without the user's approval.
- The source main already contains production-like reusable infrastructure, but its 31 migrations / YTB artifacts are intertwined with scene/beat image generation. No independent Historical Motion pipeline exists in current main.
- The main commit has the tree \`36850a11f26418e868ab383b9069ab8fa38c424f\`. It was inspected by exact ref; later commits need a fresh diff audit.

## Verified source-backed module classification

| Area | Current main owner (actual files) | Decision / why |
|---|---|---|
| Cockpit UI/navigation | \`src/App.tsx\`, \`src/pages/StudioPage.tsx\`, \`src/layout/CockpitShell.tsx\`, \`GlobalSidebar.tsx\`, \`PipelineStepper.tsx\`, \`src/components/ui/*\`, \`src/theme/*\` | **KEEP + ADAPT**: preserve UX, replace YTB stage identities and project DTOs |
| Prompt/import UI | \`src/features/studio/PromptImportWorkspace.tsx\`, \`TopicIdeasPanel.tsx\`, \`ScriptPanel.tsx\` | **KEEP + ADAPT**: add independent Validate, Review, Approve, repair/download workflows and historical metadata |
| External authoring security | \`app/core/external_authoring.py\`, \`app/services/external_authoring_reservations.py\`, \`app/modules/platform/services.py\` | **EXTRACT + ADAPT**: duplicate-key JSON rejection, reserved IDs, frozen source, import checks; remove YTB-shaped semantics |
| Topic/Script+Analysis | \`app/modules/platform/services.py\`, \`app/modules/platform/external_script_analysis.py\`, \`app/api/routes/platform.py\`, \`contracts/prompts/platform/*\` | **ADAPT**: retain one external ChatGPT step for Script+Analysis, add historical claims / period / citations / uncertainty and a direct-topic bypass |
| Artifact lineage | \`app/services/artifacts.py\`, \`app/repositories/core.py\`, \`app/core/{hashing,ids,time}.py\` | **KEEP EXTRACTED**: content hashing, guarded publication, stage revisions; re-key domain CAS dependencies |
| Channel/Project DB | \`app/db/models.py\`, \`app/db/session.py\`, \`alembic/versions/001..031\` | **REPLACE DOMAIN SCHEMA**: old Project has 16+ YTB scene/beat/prompt/asset/assembly pointers. Fresh historical schema + dedicated DB; do not run legacy upgrades on new data |
| Voice | \`app/modules/v4/voice_service.py\`, \`durable_voice_service.py\`, \`voice_provider*...\`, \`voice_pacing*...\`, \`voice_audio.py\`, \`voice_current_realignment.py\`, \`kokoro/*\`, \`VoicePanel.tsx\` | **EXTRACT + ADAPT**: preserve Kokoro/ElevenLabs, exact final WAV and forced alignment. Voice classes contain V4/YTB project & invalidation dependencies. Do not directly transplant \`voice_alignment.py\`'s fallback timing as final timing authority |
| Windows process safety | \`app/modules/v4/host_owned_process.py\`, \`windows_job_object.py\`, \`windows_subprocess_guard.py\`, \`app/render_coordinator_runner.py\` | **EXTRACT + ADAPT**: supervised Node+Chromium/Remotion subprocess execution, Windows Job Object, stdout limits, cancellation |
| Durable work | \`app/modules/media/durable_workflow/*\`, \`app/repositories/render_durable_working.py\`, \`app/db/models.py:RenderOperation\` | **KEEP SEMANTICS / REBUILD DOMAIN**: lease/attempt fencing, persisted recovery and idempotent result; old operation requires Assembly/SRT/VTT identifiers |
| Current assembly/render | \`app/modules/v4/{assembly_service,assembly_motion,render_graph,render_ffmpeg,render_service,durable_render_service}.py\`, \`app/api/routes/production_v4.py\` | **REPLACE ENGINE**: old FFmpeg still-image motion/beat montage cannot act as multilayer renderer. Retain only FFprobe/audio/file/process primitives |
| Scene/Beat planning | \`app/modules/v4/scene_breakdown_*.py\`, \`scene_plan_assembler.py\`, \`beat_timing_map.py\`, \`visual_plan_assembler.py\` | **REPLACE** with persistent VisualSetup, Layer and MotionEvent plan, governed by final word timing. Old micro-scenes may be studied for boundary validity only, never persisted as old Scene/Beat owners |
| Per-beat image generation | \`app/modules/v4/scene_asset_*.py\`, \`visual_reference_*.py\`, scene asset DB working tables and \`src/features/studio/SceneAssetsPanel.tsx\` | **REMOVE AS MANDATORY PATH**: preserve optional image generation/media transport as separate, opt-in service only if needed; build archival Asset Library, source/rights validation and per-layer assets |
| Existing visual style | \`app/modules/v4/style_bible_*.py\` and Channel DNA V3 | **ADAPT**: derive HistoryMotionStyle profile from user DNA with typography/maps/photo layouts/texture; remove character-only assumptions |
| Production routes | \`app/main.py\` + \`app/api/routes/{platform,v4,production_v4,scene_assets_v2,...}.py\` | **REPLACE ROUTE COMPOSITION**: history-only API; no shadow/duplicate route precedence or legacy dual-mode, no old V4 activation |
| Frontend/API types | \`src/api/client.ts\`, \`src/features/studio/types.ts\` | **ADAPT** to history DTOs and seven-stage readiness graph; remove \`scene_assets/beat\` dependencies |
| Startup/release | \`start.bat\`, \`scripts/windows/*\`, \`.github/workflows/scene-assets-v2-candidate-audit.yml\`, \`tools/package_manifest.py\` | **ADAPT** to API, voice/media host, Node Remotion render host, frontend; new manifest and tests when cutover done |
| Remotion proof | Available ONLY on former feature branch at \`experiments/remotion-history-poc/\` | **PORT SELECTIVELY**: known static 60s demo, three setups, unverified runtime, no actual voice/history assets, no production API |

## Concrete code observations

1. \`src/pages/StudioPage.tsx\` currently lists eight visible stages: Topic Ideas, Script & Analysis, Style Bible, Voice, Scene Breakdown, Scene Assets, Assembly, Render. \`StudioStageId\` also contains \`media\`. Runtime readiness is coupled to current Project scene/beat pointers.
2. \`app/api/routes/platform.py\` exposes working-style prompt/read/validate/import endpoints for topic and script; \`app/core/external_authoring.py\` rejects duplicate JSON keys and supports bundled schema.
3. \`src/features/studio/PromptImportWorkspace.tsx\` already provides Build/Rebuild Prompt, Copy Prompt, Upload JSON, Import JSON and validation display. Reuse the interaction; improve isolated Validate-only and explicit approval where workflow needs it.
4. \`app/db/models.py:Project\` stores \`scene_plan_artifact_id\`, \`visual_plan_artifact_id\`, \`prompt_bundle_artifact_id\`, \`scene_asset_manifest_artifact_id\`, \`beat_timing_map_artifact_id\`, \`assembly_plan_artifact_id\`, plus voice/final pointers. Changing only the renderer leaves YTB invalidation semantics intact.
5. \`app/main.py\` includes many old routers, including two-step Scene Breakdown, multiple Visual References/Scene Assets owners, Production V4 and V4 router shadow precedence. Hard-cut once, not parallel compatibility.
6. \`app/modules/v4/render_service.py\` validates Assembly-driven inputs, \`durable_render_service.py\` compiles old FFmpeg render graph and \`app/db/models.py:RenderOperation\` has \`source_assembly_plan_artifact_id\` / subtitle pointers: MUST be redesigned for exact \`CompiledTimeline\` + audio + asset version hashes.
7. \`README.md\` and \`00_START_HERE.md\` explicitly force one generated image per beat and prohibit diagrams/text-card local rendering: THESE LEGACY RULES MUST BE REPLACED in the new product's source of truth.
8. \`README.md\` asserts final paced WAV + ElevenLabs Forced Alignment authoritative. Preserve the actual final audio/word timing rule. Current \`voice_alignment.py\` also contains fallback segment estimators; prevent any fallback estimate from masquerading as forced-aligned final timing.
9. The old GitHub candidate workflow runs YTB Python/Node suites and FFmpeg Windows gates. Dedicated \`@remotion/player\`/Remotion CLI/Chromium render/FFprobe/preview parity gates must be added.
10. \`package.json\` presently has React 18, Vite, MUI, TanStack, Tailwind and Node tests, **no remotion packages**. Node renderer should be isolated with version-matched remotion dependencies and lockfile; otherwise Vite/root build and binary installs can conflict.

## Principal migration hazards / required gates

- **REPO HEAD:** Re-check main SHA before branching/merging. Docs and current changes must be on a feature branch based on the exact main ref; no direct pushes to main.
- **LEGACY DATA:** Default development DB may be disposable, but never delete a real database/media library without explicit backup and opt-in. Introduce history-specific DB/data path, reject old migration revisions, and test clean initialization separately.
- **LINEAGE:** Extract publication utilities but rewrite CAS guards, stale logic, ArtifactType/roles and project head names; no old Scene/Beat pointers survive production.
- **VOICE:** High complexity; test Kokoro identity/pacing, section regen/reuse, chosen provider, final audio alignment, cancel/recovery before and after extraction.
- **RENDER:** Completely new child-process protocol for Node/Chromium, with Windows process group ownership, cancellation, output hash and preview equivalence.
- **ASSET LICENSE/FACTS:** Archive search and downloaded image rights are not the same thing. Keep source/time/geography attribution and explicit approvals. ChatGPT-proposed URLs are UNTRUSTED.
- **REVIEW:** Draft PR checks may be green while no actual Remotion test ran. Final acceptance demands browser frames and verified H.264 MP4/audio/FFprobe on exact tested head.

## What this audit does NOT claim

No dependencies installed, pytest/node tests run, local Windows process executed, real MP4 rendered or UI screenshots captured during this source-only review. No completeness audit of every one of the ~800+ legacy paths. The file-level decisions above are owner-level recommendations and MUST be refined into an exhaustive inventory + static dependency scanner before destructive clean-cut.

See \`HISTORY_STUDIO_MAIN_BASED_MIGRATION_PLAN_V2.md\` for executable PR plan.
