# Historical Motion Graphics Studio — Main-Codebase Migration Plan v2

**Status:** Implementation proposal based on source audit; not implemented or runtime-tested.
**Audit date:** 2026-10-15. **Repo:** plthien/Historical-Motion-Graphics-Studio.
**Exact baseline:** main@ea403973e6c6b23e5c3f073e83ca07ef9a8ca768.
**Companion audit:** docs/history/MAIN_CODEBASE_AUDIT_2026-10-15.md.
**Supersedes:** The old generic v1 plan on feat/r0-remotion-history-motion-poc assumed an empty repository. The user has now pushed an actual YouTube AI Studio V3 codebase to main. Do not follow old branch instructions literally.

## A. Final product architecture and hard rules

Outcome: History topic → External ChatGPT Topic/Script JSON → approved factual Script → Kokoro/ElevenLabs voice + real word alignment → External ChatGPT historical asset research plan + validated archive library → External ChatGPT multi-layer Visual Director JSON → deterministic timeline compile → Remotion shared Player/render composition → local MP4 + QA/attribution.

1. **NO LLM API.** No text/model endpoints, local LLM or automated ChatGPT browser interactions. Creative and analytical stages ONLY use Build Prompt, Copy/Download Prompt, manual External ChatGPT, JSON file upload, Validate-only, Import, Review, Approve and Build Repair Prompt. No hidden fallback or auto-repair model calls.
2. **Reuse** YTB Cockpit UX, JSON validators, artifact storage/hashing/CAS, selected Channel/Project context, safe process management, SQLite durable jobs and real Kokoro/ElevenLabs voice/alignment after extraction and tests.
3. **Replace** current mandatory one-image-per-Beat Scene Assets, Scene/Beat/Visual Plan, image Prompt Bundle and FFmpeg still-image montage with persistent VisualSetup + independently moving Layer/MotionEvent and Remotion rendering.
4. One product/one authority at final cutover: no legacy YTB compatibility mode, no dual persisted stage heads, no old YTB DB migration or wholesale import of old ScenePlan artifacts.
5. Do not change **main**. Always branch exact approved main/staging SHA; user merges only after review. Existing old feature branch is not a safe merge source; port tested files individually.
6. Historical accuracy and licensing: explicitly classify genuine archive, derived visualization, hypothetical reconstruction, labeled schematic. A ChatGPT-proposed source URL/rights claim is NOT evidence until validated.
7. Approved final audio + real word/phrase alignment determines frame timings. Don't invent timestamps, silently average WPM or use fallback approximate alignment as authoritative.
8. Preview and final render use the SAME Remotion composition, timeline and local media bytes, never a simplified DOM preview or generated JSX from ChatGPT.

## B. Seven target Studio stages and UI/API alignment

**Stage 1 Topic Ideas:** External ChatGPT import or manually entered approved topic, period/geography, hook, factual risk, selection.
**Stage 2 Script & Historical Analysis:** external combined JSON import (exact spoken text, claim IDs, chronology, provenance/citation candidates, verified/uncertain/reconstructed tags); atomic publication.
**Stage 3 Voice & Timing:** generate/review/approve FINAL WAV, actual ElevenLabs forced alignment or separately validated non-LLM offline alternative, stable timing IDs.
**Stage 4 Historical Assets:** external research-plan import; archive metadata/search, safe download/manual upload, rights/time/geography approval, immutable asset/version manifests.
**Stage 5 Visual Director:** external JSON import of persistent setups, layers, timeline events, actual word anchors; strict schema/semantic/lineage validation and targeted repair prompt.
**Stage 6 Motion Timeline:** deterministic compile to frames, @remotion/player preview, per-layer inspector, controlled revisions, selective invalidation.
**Stage 7 Render & Delivery:** bounded dedicated Node Remotion host, job progress/cancel/retry, FFprobe, visual/audio QC, MP4 and rights/attribution report.

Channel DNA and HistoryMotionStyle are Channel/Project context and an approved deterministic style artifact rather than a forced character-image-generation stage. Keep current Cockpit shell, modal/card grid, stepper and status grammar.

Current main UI stages (observed StudioPage.tsx): topic, script, style_bible, voice, scene, scene_assets, assembly, render. Update readiness conditions AND current Project schema together, not only text labels.

## C. Precise code ownership — KEEP / EXTRACT / ADAPT / RETIRE

**Keep + adapt React shell:** src/App.tsx, src/layout/{CockpitShell,GlobalSidebar,PageHeader,PipelineStepper}.tsx, src/components/ui/, src/theme/, Channel/Project cards. Preserve UX, selected workspace persistence and look/feel.

**Keep + extend external authoring:** app/core/external_authoring.py (duplicate JSON key rejection, bundle_prompt_schema, prompt text, source-value check), app/services/external_authoring_reservations.py, app/modules/platform/services.py, app/api/routes/platform.py, src/features/studio/PromptImportWorkspace.tsx / TopicIdeasPanel.tsx / ScriptPanel.tsx. Replace YTB-specific output schemas; add history-fact review, explicit validate/approve and repair where appropriate.

**Keep neutral artifact/transactions:** app/services/artifacts.py, app/core/{hashing,ids,time,diagnostic_redaction}.py, app/repositories/core.py guarded CAS/publication patterns. Reimplement Project head guards/dependency graph; do not reuse scene-beat CAS structs.

**Extract Voice:** app/modules/v4/voice_*.py, durable_voice_service.py, elevenlabs_*.py, kokoro/, voice UI and current voice regression suites. Preserve provider choice, section reuse, pacing identities and audio provenance. Current voice service has V4 Project and downstream Beat associations; remove coupling after tests. Current voice_alignment.py has fallback estimator: never publish estimation as actual forced alignment.

**Extract durable execution:** app/modules/media/durable_workflow/*, app/modules/v4/{windows_job_object,windows_subprocess_guard,host_owned_process}.py, app/render_coordinator_runner.py, RenderOperation attempt fencing, FFprobe helpers. Redesign render owner for Node+Chromium. Preserve leases, cancellation, crash recovery and bounded stdout.

**Adapt Channel DNA and Style Bible:** app/modules/v4/style_bible_*.py and Channel schema. Preserve editorial identity but add print/paper/archival/map/text/dynamic layer grammar; remove universal character/background assumptions.

**Replace legacy planning:** app/modules/v4/scene_breakdown_*.py, scene_plan_assembler.py, visual_plan_assembler.py, beat_timing_map.py, prompt_bundle_*.py and scene_asset_*.py, mandatory visual_reference_*.py. They encode exact one-generated-still-per-Beat assumption. Optional image-generation providers may be retained independently as explicitly selected illustration assets, NEVER a required beat-per-image production stage.

**Replace renderer:** app/modules/v4/{assembly_motion,assembly_service,render_graph,render_frame_quantization}.py and current still-image FFmpeg visual filter composition. Keep only verified audio, FFprobe, process and output-validation primitives from render_ffmpeg.py/render_service.py.

**Rewire production owners:** app/main.py, app/api/routes/{v4,production_v4,scene_breakdown_two_step,scene_assets_v2,visual_references_v2}.py, src/pages/StudioPage.tsx, src/api/client.ts, src/features/studio/types.ts. Remove legacy paths at ONE product cutover, no shadow routes/dual provider authority.

**Fresh database:** app/db/models.py Project currently owns scene_plan_artifact_id, visual_plan_artifact_id, prompt_bundle_artifact_id, scene_asset_manifest_artifact_id, beat_timing_map_artifact_id, assembly_plan_artifact_id and other scene-centric heads. Current Alembic 001..031 are YTB-owned. Use a new History SQLite path + new baseline migration; reject old DB at startup, do not silently migrate/drop or destroy user data. Old migrations can be retired from final product AFTER tests and extraction.

**New motion implementation:** contracts/history/, app/modules/history/{authoring,assets,voice,motion,render,quality}/, history-domain DB/repositories, src/features/history/, runtime/remotion/ (shared composition, Player and renderer, package-lock), dedicated Windows Node render host, .github/workflows/history-studio-candidate.yml.

## D. Contracts to freeze (schema and semantic validator)

**HistoryChannelDNA.v1:** channel description, language/narration identity, print/archival style, licensed fonts/colors, layout and motion presets, allowed historical reconstruction policy.
**TopicIdeas.v1 / SelectedTopic.v1:** stable idea/period/location IDs, hook, factual-risk notes, research questions.
**ScriptAnalysis.v1:** exact full_script, exact section text, stable segment_id, claim_id, spoken overrides/cues, claim source candidates, time-period/geography, verified/uncertain/hypothetical.
**VoiceManifest.v1 / WordTimingMap.v1:** exact script hash, final WAV hash, provider/runtime/calibration identity, true duration/sample count, aligned character/word IDs+time intervals, QA.
**AssetResearchPlan.v1:** ChatGPT candidate URLs and archive queries, linked claims/events, desired graphic role, date/rights checklist; candidate data only.
**ApprovedAssetManifest.v1:** local asset/version/SHA, original provenance/creator/license/evidence, historical relevance/date/place, approved state, derivatives, attribution and project binding.
**HistoryMotionStyle.v1:** deterministic Channel DNA projection plus explicit override authority; visual templates, fonts, transitions.
**VisualMotionPlan.v1:** setup_id with semantic word anchors and continuity, layer_id with kind/approved asset ref/z-index/layout/crop, motion_event_id with allowed preset/easing/cue/duration/hold/exit, transition rules. NO arbitrary executable JSX, scripts, CSS strings or remote asset fetch.
**CompiledTimeline.v1:** immutable global integer frames at specified fps/resolution, exact audio hash, approved asset hashes, setup intervals, layer graph, motion event resolved bounds, composition version and source fingerprint.
**RenderOperation/RenderManifest/QCReport/FinalVideo.v1:** leased attempts, approved input hashes, MP4 settings, decoded output metadata, rights attribution and QA review.

All external authoring uses server-reserved artifact IDs, frozen parent hashes, max-byte constraints and strict no-extra-field schemas. Validate-only read-only, import revalidates then writes candidate, approval CAS. Stale/replay/cross-project/duplicate keys -> explicit typed error. No regenerating accepted script/voice on a layer-only repair.

Motion constraints: persistent setups across multiple spoken thoughts, several independently changing layers per setup, exact word cue/frame resolution, no floating-frame drift, approved media only, missing historical-map date requires schematic label, bounded safe areas/typography, explicit crossfade or overlap, per-layer position/scale/easing limits. Render output uses approved timeline, not raw imported JSON.

Proposed DB groups: channels, projects, project_stage_heads, artifacts, authoring_reservations, assets, asset_versions, project_asset_bindings, voice_operations, voice_alignment_tasks, render_operations/attempts, jobs and optional media operations. Freeze exact columns/indexes/FKs in P1. WAL, FK on, CAS, source hashes, file containment and no network IO inside transactions.

## E. Detailed implementation PR sequence — P0 to P8

### P0 — Baseline + verified Remotion POC (source branch: NEW MAIN)
**Files:** docs/history/* audit, experiments/remotion-history-poc/ reviewed copy from old feature branch, POC-local lockfile, .github/workflows/history-remotion-poc.yml. NO API/DB rewrites in this phase.
**Work:** verify exact main source + inventory ledger; run baseline YTB Python/Node/Windows suites; selectively port static 60s POC from old branch (never merge that branch). Pin compatible Remotion packages, npm ci, npm test, npm build, browser Player, real CLI H.264 render, FFprobe 60 seconds/1080p/30fps, frame samples at 0/5/10/19/20/25/35/39/40/45/55/59 sec. Repair viewport/timing, missing images, fake historical claims/period maps, style density. Add dedicated CI and sample source/license sheet.
**Gate:** real rendered MP4 and Player parity, screenshots and FFprobe on exact candidate SHA, actual tests recorded. If no browser/render -> BLOCKED, not PASS.

### P1 — History product contracts + standalone storage foundation
**Files:** contracts/history/*, app/modules/history/core/*, app/modules/history/authoring/*, app/db/history_*, app/repositories/history_*, tests/history/*.
**Work:** version/schema fixtures and semantic validators for ALL history DTOs; fresh history DB and stage-head DAG; reuse hashing, reserved IDs, prompt renderer and CAS via neutral extraction. No new history routes in active YTB app yet. Create isolated history test entrypoint; preserve old test suite intact. Fresh DB does not open/migrate existing YTB DB.
**Gate:** clean DB, offline history input contract suite, stale/duplicate/replay/concurrent approval tests, true artifact immutability, no LLM API.

### P2 — External ChatGPT Topic/Script & Channel migration
**Files:** history channel/project services and prompt schemas, topic/script services, selected UI panels, exact-text/claim validators.
**Work:** Channel DNA/history style scope; Topic prompt/download/import/select with direct topic bypass; combined Script+Historical Analysis external prompt and JSON import; exact quote/spoken text integrity, timeline/claims, reliable source review, approve and restart restore. Reuse PromptImportWorkspace interaction and adjust names. No search/LLM provider in backend.
**Gate:** approved history script from one topic entirely via manual External ChatGPT JSON; atomic script/analysis publication; invalid/stale JSON rejected and useful repair prompt generated.

### P3 — Kokoro / ElevenLabs final voice + real alignment
**Files:** extracted voice module & provider adapters, Kokoro runtime, voice UI and operation tables.
**Work:** inventory all old Voice dependencies; copy focused voice/pacing/lease/regen tests FIRST; decouple script and timing from old ScenePlan/BeatTimingMap pointers; synthesize exact accepted text; select approved FINAL WAV; real forced alignment of that WAV via existing ElevenLabs integration or separately tested non-LLM offline aligner. Section regenerations allowed but final WAV hash invalidates old timings. Optional paid calls explicit.
**Gate:** actual voice/word-time pair with exact lineage and timing QC, restart/cancel/retry and provider-identity tests; no fake timings.

### P4 — Historical archive research/asset library
**Files:** asset research prompt/schema/service, archive adapters (Wikimedia/LoC as feasible), download/media validators, library UI, asset/version DB.
**Work:** External ChatGPT ResearchPlan JSON import; do not treat suggested source URLs as truth. Manual upload + metadata lookup/download with SSRF/redirect/private-network defense, MIME checking, image size limits, content hashes; human review source license/author/time/geography and role. Handle crop/alpha/paper derivatives as immutable variants. Optional illustration generation can be an opt-in media service, not mandatory one-per-beat.
**Gate:** approved diverse asset set including photo/portrait/map/source docs; all rights/relevance metadata visible; rejected/unverified source cannot enter final delivery by default.

### P5 — External ChatGPT Visual Director and Timeline compiler
**Files:** director prompt builder/schema, validator, import/repair/approval service, deterministic motion compiler and targeted tests.
**Work:** use exact script/claims, final WordTimingMap, approved assets, style grammar. ChatGPT returns layered setup/asset/graphic motion JSON, NOT code. Validate every setup/layer/cue/asset/source hash/continuity/typographic boundary; preserve locked good portions in targeted repair prompts. Compile semantic word anchors to integer frames deterministically, resolve asset versions, freeze one hash.
**Gate:** repeated compile produces identical bytes/hash, event frames are valid, known supported components only, no image-per-beat assumption, no LLM API, malformed/stale imports fail closed.

### P6 — Production Remotion & first complete history video
**Files:** runtime/remotion/{compositions,components,templates,renderer,shared-types}, Player integration, history preview/backend render bridge, composition tests.
**Work:** share one composition between React Player and CLI/renderer; implement PaperCanvas, Polaroid/ArchivePhoto, PortraitCutout, PeriodMap/SchematicMap, RouteDraw, HeadlineReveal, Timestamp, Labels, Stamp, Diagram, FactComparison, CameraPanZoom, captions; multiple independent layers per long-lived setup. Use final WAV, real word-timed cues and local approved media. Prove a full 60–90 second project from manually imported ChatGPT files to final H.264 with non-silent voice.
**Gate:** first actual history final-video pilot with source manifest, screenshots, FFprobe, narration and Player/export parity. P6 may run in isolated history-only test app, but no user-facing two-mode YTB/history runtime.

### P7 — Motion Timeline editor + durable Node host
**Files:** src/features/history/motion/*, Node render host, Python render scheduler/operation model, Windows launch test scaffolding.
**Work:** scrub/seek/zoom and tracks for persistent setup, separate photos/maps/text/stamp/routes, voice, captions; inspect/edit per-layer asset, timing, scale/position and preset, save versioned candidate, approve. Rebuild only affected descendants. Replace old assembly/SRT/VTT RenderOperation source lineage with exact CompiledTimeline+WAV+approved asset hash set; fenced SQLite attempts, bounded child process, Windows Job Object, cancellation, timeout and lease ownership; read-only statuses and retry recovery. 
**Gate:** regenerate/edit one label without redoing Script or Voice, asset swap confined to its layers, preview/render same output; recovery/cancel/Windows process-tree tests pass.

### P8 — Atomic product clean-cut, QC, docs and Windows release
**Files:** app/main.py route composition, history startup entrypoint, src/pages/StudioPage.tsx and src/api/client.ts, final history SQLite migration, legacy V4 code/route/schema/test deletion, start.bat/scripts/windows/*, release manifest, README/docs, full CI.
**Work:** remove old Scene/Beat/PromptBundle/SceneAsset/FFmpeg-montage/Spine references and dormant YTB prod endpoints AFTER extracted tests pass; one history-only product, one DB schema and stage authority. New Launcher starts API + relevant media/voice host + Node Remotion render host + frontend. Preserve old user data by backup/reject path, not silent DROP. FFprobe final verification, black/blank/frozen frames, audio clipping/silence, rendered typography bounds, historical period-map check and rights manifest. New MANIFEST.json generated only after clean tree. UI fidelity/screenshots at 1440x900 and 1920x1080.
**Gate:** clean Windows install, 60–90s and 5+ minute history videos from manual External ChatGPT JSON handoffs to final MP4, resume/restart/cancel recovery, exact-candidate green Python/UI/Remotion/Windows/QC checks. Only then ACCEPTED; no partial PASS.

## F. Dependency graph / milestones

M0 after P0: renderer technical feasibility.
M1 after P1–P3: project, exact historical script, real voice/timing.
M2 after P4–P5: source-verified assets + deterministic Director MotionPlan.
M3 after P6: first true non-silent source-backed final video 60–90s (operator approves ChatGPT handoffs).
M4 after P7–P8: maintainable local Studio with editor, durable rendering, QC and no old YTB pipeline.

P5 needs BOTH approved final WordTimingMap and approved asset library snapshot. Voice and archival research may proceed in parallel after script acceptance. No universal shot-per-sentence rule. P8 must not delete previous sources before all neutral extractions have passing tests.

## G. BE and UI feature parity / acceptance table

- Topic workspace ↔ prompt/reservation/import/approve and manual-topic API ↔ ApprovedTopic head.
- Script workspace ↔ one combined historical script+analysis authoring contract ↔ approved Script+Claims pair.
- Voice ↔ explicit synth/approve/forced-align/retry/audio API ↔ approved WAV + WordTimingMap.
- Asset research ↔ prompt/import, archive metadata/download, upload/source-license evidence review ↔ approved asset versions.
- Visual Director ↔ external prompt/import/validate/repair/approve ↔ immutable MotionPlan.
- Motion Player/inspector ↔ get compiled timeline, versioned layer patch, recompile/approve, preview asset reads ↔ CompiledTimeline hash.
- Render ↔ submit/status/cancel/retry, durable Node host, stream MP4/QC/provenance ↔ immutable FinalVideo.

UI controls must map to working APIs before marked active. Feature availability checks are explicit and disabled-with-reason, not decorative or claimed complete. Keep Channel/Project selection/visual language unchanged.

## H. Negative testing / security / provenance gates

Authoring: exact prompt restoration after reload, no model connection/retry, duplicate JSON keys, invalid nested null/unknown fields, source reserved IDs, stale/foreign-channel projects, malicious text, replayed reservation and concurrent CAS races. Validate-only must not mutate DB.
Voice: exact final audio vs script hash, provider identity/calibration consistency, real forced alignment validity, no inferred timestamps, section regen and cancel/restart.
Assets: source/rights attribution, historical time/location match, archive metadata confidence, missing/unapproved assets, SSRF (private IP/redirect), MIME/type/decompression, symlink/path traversal, local original bytes and derived version IDs.
Motion: no executable JSX/CSS/URL, known templates only, event overlap, out-of-range frame/word IDs, offscreen titles, continuity, asset hash, explicit transitions, deterministic build.
Render: browser install/lockfile, real Player seek, H.264 metadata, audio present, sample keyframes, visual density and scene holds, bounded node child tree, crashed/retried/lease-stale operation, output CAS, rights manifest.
Cutover: old routes/imports/jobs/DB authority absent, old data paths fail closed, channel/project persistence, Windows clean install+restart, no LLM provider settings.

CI gates: dedicated history backend+frontend+Node Remotion/browser+Windows process/render+FFprobe+contract/source-closure suites on exact candidate commit SHA. Old YTB CI green alone is INSUFFICIENT. Report PASS/PARTIAL/BLOCKED with actual logs; re-run after any patch.

## I. Developer implementation protocol

1. Verify repository is plthien/Historical-Motion-Graphics-Studio, explicit base main and current exact SHA; no direct main push or default branch assumptions.
2. Read companion audit, this plan and actual touched code. Treat YouTube README/00_START_HERE rules as a historical description of old product, not authority for new history stages.
3. For every phase, enumerate files KEEP/EXTRACT/ADAPT/DELETE, expected interface/input/output, DB migrations, negative tests, CI job and rollback.
4. Code only the authorized phase. Do NOT merge feat/r0-remotion-history-motion-poc wholesale. Do NOT bring old 2D-Animation-Studio source or PR #1 Spine assumptions into this repository.
5. Run affected unit/integration suites, browser screenshots and actual Remotion/Windows render gates. Mark unexecuted checks NOT RUN, never PASS.
6. Self-review for YTB Scene/Beat residues, style mismatch, historical factual errors, rights, LLM endpoints and silent invalidation. Fix and retest on exact head.
7. Submit draft PR with implementation report: base/head SHA, changed files, test matrix, artifact/frame links, risk and DONE/PARTIAL/BLOCKED verdict. No auto merge.

**Next coding task:** P0, and ONLY P0, after acknowledging that target main is now a full YTB Studio. A separate Codex P0 prompt is provided in the same docs/history directory.
