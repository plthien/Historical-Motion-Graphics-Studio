# Historical Motion Graphics Studio — Detailed Implementation Plan v1

Status: IMPLEMENTATION PLAN / proposed phase gates; not a claim of completed implementation.
Date: 2026-10-15. Repository: plthien/2D-Animation-Studio.
Starting point: PR #2, branch feat/r0-remotion-history-motion-poc (Draft); reference prototype in experiments/remotion-history-poc.
Related authority: docs/architecture/EXTERNAL_CHATGPT_AUTHORING_CONTRACT.md and docs/architecture/HISTORY_REMOTION_PIVOT.md.
Priority: working video from concept to final first; scalable editor and batch operations second.

## 0. Product definition, boundaries, and non-negotiable invariants

Deliver a local-first Windows Historical Motion Graphics Studio for PERSONAL HISTORY STUDY. An operator starts with a history topic, uses External ChatGPT for creative/research authoring, imports strict JSON files, reviews/approves evidence and narration, and lets deterministic media services build a layered Remotion timeline and final MP4.

The benchmark is the user's reference clip 1015.mp4 / YouTube H4nFEeW2Tjw: archival collage, textured paper, photograph/cutout layouts, historical maps, kinetic typography, stamps, diagram paths, overlays, controlled camera and vocal-synchronized visual reveals. The product is NOT a Spine/character-rig animation studio, not an image-per-beat slideshow, and not an LLM-API agent.

Absolute rules for ALL phases:
1. NO OpenAI/Anthropic/Gemini/other LLM API, no local language model, no web-based ChatGPT UI automation, no hidden model retries. Creative stages ONLY: local Build Prompt -> operator uses External ChatGPT -> downloadable JSON -> Upload -> Validate -> Review -> Approve. Even invalid-result repairs are copyable prompts, not model calls.
2. Non-LLM services may be configured separately: local Kokoro TTS, opt-in ElevenLabs voice/forced-alignment, archival metadata/search/download, FFmpeg/FFprobe and Remotion rendering. Optional paid calls require explicit operator action and scoped consent; no implicit purchases.
3. Preserve recognizable YTB cockpit UX: src/layout/CockpitShell.tsx, GlobalSidebar, PageHeader, PipelineStepper, cards, modals, dark tokens, Channel/Project selection. Do not replace with a generic dashboard.
4. CLEAN CUT product flow: reuse only neutral modules verified by tests. No old YTB DB migration, old Scene/Beat/VisualPlan/PromptBundle producer, old image-per-beat generator, old static slideshow renderer, dual modes, compatibility adapters or old routes. Do not destructively touch the existing YTB repository; clone's history DB/assets are disposable and must be kept separate.
5. The authoritative result of authoring is immutable validated JSON. AI-authored content cannot execute JS/JSX, CSS, shell, remote URLs or arbitrary React components. The renderer only understands an enumerated, versioned grammar.
6. One approved FINAL audio file determines word/phrase timing. Motion events anchor to approved word/phrase IDs, not estimated narration WPM, and are compiled deterministically to integer frames.
7. A Visual Setup persists across successive narrated thoughts; individual layers and motion events change within it. Never force one new setup for every sentence or one generated image for every beat.
8. Authenticity: distinguish verified historical statements, plausible reconstruction and purely schematic graphic. Every externally retrieved asset has source URL/author/license/allowed-use evidence, attribution, immutable file hash, review state. Do not treat ChatGPT candidate links as validated truth.
9. Preview and exported MP4 must render the SAME Remotion component and approved CompiledTimeline; no parallel simplified preview renderer.
10. Each PR must include negative tests, regression evidence and a reviewer report. CI green on old YTB jobs alone is NOT Remotion validation. Fail closed and report BLOCKED if a required runtime/browser/render check was not run.

## 1. Verified repository inventory (as of initial audit)

- PR #2: Draft, isolated experiments/remotion-history-poc; last inspected source had one 60-second plan at 1920x1080/30fps, three setups: title, schematic map and archive. Timed title/date/label/stamp/route/photo motion events; Player and CLI use DocumentaryComposition.jsx. The demo map is not period-accurate and its archive image is a placeholder.
- POC package.json pins Remotion family version 4.0.535; local package-lock.json did not exist in inspected POC folder. npm install/test/build/render, FFprobe and reference parity have NOT been demonstrated by existing YTB workflow jobs.
- Main branch still owns legacy V4 modules (scene breakdown, image-per-beat assets, old Assembly/FFmpeg), original React Cockpit, external JSON authoring utilities, Kokoro/ElevenLabs Voice and durable process infrastructure.
- PR #1 (separate Draft) freezes a Spine/Pixi rigging product design and database. It is NOT a dependency nor source of authority for this historical product. Do not merge it blindly. Document/close/supersede it by an explicit repository decision before clean-cut.
- Audit/find exact current commit SHAs and file owners again at the start of every implementation PR. Never rely on stale source-tree assumptions from this plan.

## 2. Canonical operator journey (the TRUE end-to-end flow)

1. Create Channel (history niche, language, audience, palette/visual identity, voice preferences) and Project. Input topic seed.
2. Topic Ideas: Build Prompt -> External ChatGPT -> import topic_ideas.v1 JSON -> validate/review -> select/approve one idea. User may bypass topic discovery by manually entering one topic; this MUST still produce a source-identifiable approved Topic record.
3. Script + Historical Analysis: Build Prompt using approved Topic -> External ChatGPT -> import script_analysis.v1 JSON containing EXACT spoken text, segments, claims, source leads, uncertainty classification and citation candidates -> validate/review/approve.
4. Voice: deterministic TTS from approved exact spoken text, draft/compare/approve final WAV, then forced-align EXACT final WAV to script. If ElevenLabs alignment is unavailable, stop in an explicit blocked state or use a separately tested, explicit non-LLM offline alignment implementation; NEVER fabricate timings.
5. Historical Asset Research Planning: Build Prompt based on script/claim IDs -> External ChatGPT -> import asset_research_plan.v1 JSON with candidate archival URLs, query strings, asset types and relevance hypotheses -> strict validation. The application may use approved, non-LLM archive endpoints to retrieve metadata and candidate images; human checks factual relevance, date, usage rights, attribution and approves immutable assets.
6. Visual Director: Build Prompt using EXACT approved script, approved final WAV word timing, approved asset inventory, layout/template grammar, editorial reference profile and verified claims -> External ChatGPT -> import visual_motion_plan.v1 JSON. Validate events, IDs, source-fingerprints, layout references and anchors; targeted external repair if needed.
7. Compile: translate semantic word/phrase anchors to exact frames, place layers with measured bounds and safe areas, freeze CompiledTimeline hash. The compiler MUST NOT improvise narrative, fetch random images, rewrite script or invoke AI.
8. Preview: @remotion/player opens the same composition used for final export; review visual continuity, individual assets, motion cues and sound. Allowed local edits produce versioned plan revisions and invalidate only affected descendants; AI repairs go back to External ChatGPT.
9. Render: supervised Node Remotion renderer/CLI using frozen timeline, approved local assets/audio; durable operations + crash/cancel recovery. FFprobe and frame/audio QA; publish FinalVideo + QCReport, attribution manifest and project file bundle.

The visual research stage can work in parallel with voice production after script approval, but the final Visual Director prompt requires the approved voice timing and approved asset versions.

## 3. Target module and file ownership (proposed; freeze actual names in P1)

Frontend: preserve src/layout and src/components/ui. Add src/features/history/{topic,script,voice,assets,director,motion,delivery} and a new history pipeline coordinator wired into StudioPage; reuse shell/context, not old V4 panels.
Backend: create app/modules/history/{authoring,voice,assets,motion,render,quality} and app/api/routes/history_*.py with explicit ownership. Extract minimal safe utilities from app/core and V4 where independently tested.
Contracts: create contracts/history/schemas/*.v1.schema.json, valid/invalid fixtures, prompt contracts and API definitions. Do not reuse Spine contracts/animation as historical authority.
Render: prototype stays in experiments/remotion-history-poc until verified. After P1 promotion, new production runtime at runtime/remotion/ with a shared composition library and separately invoked supervised Node render host; Node is the sole owner of Remotion rendering.
Storage: fresh SQLite DB for this cloned product, and content-addressed local artifact/media store, kept separate from original YTB DB. Data directory is configurable and path-hardened.
Testing: tests/history backend + contracts, src history UI tests, runtime/remotion tests, Windows process/render tests, screenshot/ffprobe acceptance artifacts.

A PR MUST NOT quietly promote experiments into production without making the split explicit and demonstrating import/preview/render parity.

## 4. Contract specification to freeze in P1 (examples are logical shapes, not final schemas)

All creative JSON artifacts: strict schema_version, artifact_id, channel_id, project_id, parent_artifact_ids, source_fingerprint, created_at context (server authority), data; unknown fields rejected, duplicate JSON keys rejected, size limited; reservation id preallocated by server. An imported JSON cannot supply arbitrary server-side filesystem paths, HTML/JS, or metadata that overrides the server's hash/approval state.

- HistoryChannelDNA.v1: channel/language/audience/history scope; narration/voice identity and pacing; documentary-collage visual identity; colors/fonts/layout grammar; authenticity and reconstruction policy. Sample profiles for 16:9 reference must be visually audited, not presumed stylistically equivalent.
- TopicIdeas.v1: each idea has stable ID, hook, story angle, factual risk, opportunity/rationale and research questions. Selection is a versioned approved Topic head.
- ScriptAnalysis.v1: exact spoken script with stable segment IDs and immutable exact text, narrative events, historical claims with claim IDs, time period/geography, confidence/verified_vs_reconstruction category, source leads and uncertainty. Voice uses SCRIPT TEXT ONLY.
- VoiceManifest.v1 / WordTimingMap.v1: script hash, final WAV content hash, provider/runtime/voice/pacing identity, audio duration, sorted word/phrase IDs and bounded nondecreasing timestamps, forced alignment provenance and QC. Timing must be based on finalized audio, not draft synthesis.
- AssetResearchPlan.v1: candidate queries/links by claim/entity/event, desired composition role, source-candidate metadata, verification requirements. This is not an ApprovedAssetManifest.
- ApprovedAssetManifest.v1: local content-addressed file/version/sha256, MIME/dimensions, source URL, author, rights statement and evidence, archive item ID, source date and historical relevance, attribution string, approval state, optional entity ID/reuse key. Unsafe/unverified assets cannot enter final publish without explicit override/reason.
- VisualMotionPlan.v1: approved parent IDs/hashes, visual setups with stable setup_id and semantic start/end phrase anchors, ordered stable layer_id references (image, map, portrait, headline, text card, symbol, diagram, stamp, texture), asset/version ID where required, normalized transform/layout region/z-index and explicitly enumerated motion events (at phrase/word ID + offset, preset, duration/easing). There is no arbitrary JSX, style string, custom script or remote asset fetch.
- CompiledTimeline.v1: fps, exact width/height, duration_frames, immutable voice hash, referenced approved asset versions, setup intervals in global integer frames, layer graph, motion event global/local frame bounds, resolved typography and layout data, fingerprint/revision.
- RenderManifest.v1 / QCReport.v1 / FinalVideo.v1: exact timeline/audio/assets lineage, render settings, fps/duration/codecs, output hash, frames/audio QA metrics and errors/warnings, operator acceptance.

Timeline invariants: no gaps/overlapping setup coverage unless transition regions are explicitly represented; every event uses a valid setup/layer and occupies bounded frames; no fractional/negative frames; stable IDs across targeted revisions; no invisible or off-canvas critical caption; no text outside safe bounds; every audio anchor resolves to an actual aligned word/phrase; all referenced assets exist locally at approved hashes; scene continuation/hold state is explicit.

Database design decisions: channels, projects, stage_heads, artifacts, authoring_reservations, immutable assets/versions and project bindings, approved voice/timings, render operations/attempts and QC. Stage invalidation is a computed dependency graph, not manual stale flags. One publication owner, compare-and-set revisions and atomic approval; no write transaction across network/media IO. The exact number of tables/columns is NOT inherited from PR #1; freeze it with tests in P1.

## 5. Ordered implementation phases / PR gates

### P0 — Make R0 a VERIFIED renderer reference (first PR: amend existing PR #2)

Objective: actually test the isolated POC before touching production. Do NOT interpret existing YTB CI green as R0 green.

Files: experiments/remotion-history-poc/package.json; generated package-lock.json; src/{plan.mjs,DocumentaryComposition.jsx,Root.jsx,preview.jsx}; src/style.css; tests/*; .github/workflows/history-remotion-poc.yml; README.md. Avoid old app/backend/runtime changes.

Tasks:
1. On Node 22, resolve/pin valid matching Remotion dependency versions; generate/commit local lockfile; npm ci with reproducible install; no root lockfile mutation.
2. Create dedicated CI job that runs npm ci, npm test, npm run build, and renders a sample on a real Chrome/Headless Shell environment. Windows render and FFprobe acceptance are separately required before calling the demo READY. Save logs and short screenshot/frame artifacts.
3. Fix plan validation edge cases: malformed setup/event null/non-object, unexpected keys, duplicate setup IDs, bad event fields, invalid audio/asset paths, path aliases, event overflow, invalid temporal ordering. Add negative tests and normalized asset references, not arbitrary URL strings.
4. Confirm semantics of nested Remotion Sequence and useCurrentFrame at setup and event boundaries (frame 0, 569, 570, 1199, 1200, 1799); unit or browser frames must show deterministic reveals. Confirm preview seekTo uses global frames.
5. Replace archive placeholders and schematic assets in the ACCEPTANCE DEMO with only owned/public-domain or manually approved media; maintain prominent SCHEMATIC labels where evidence is not period-accurate. Do not misrepresent fake imagery.
6. Visual polish: archive collage, portrait/photo cutouts, paper/tape texture, typography hierarchy, labels, yellow highlights, red stamps, map/route motion, transitions, easing and timing. Increase event density to match reference rhythm where appropriate. Avoid a slide every ~20 seconds if the benchmark demonstrates much faster information reveals.
7. Produce headless screenshot/keyframe sheets at minimum 0, 5, 10, 19, 20, 25, 35, 39, 40, 45, 55, 59 seconds and compare with reference visually. Fix image crop, overlap, contrast and text overflow.
8. Render exact 1920x1080/30fps/60.0s H.264 MP4, FFprobe metadata and black-frame/audio inspection. Silence in this R0 technical test is allowed, but MUST be explicitly declared; do not treat R0 as a completed narrated video.

P0 gate: dependency install, unit tests, build, Player preview, render, FFprobe, screenshot gallery and comparative reviewer signoff all recorded for exact candidate SHA. Any missing evidence => BLOCKED, not PASS. Keep PR #2 draft until all pass.

### P1 — Product authority, clean-cut and UI parity (separate PR; start ONLY after P0)

Tasks:
1. Inventory/annotate old repo files KEEP/EXTRACT/ADAPT/DELETE with source-to-destination and one owner. Resolve PR #1 Spine conflict explicitly (supersede/rebase/discontinue); do not accidentally merge Spine schemas.
2. Freeze contracts described in section 4 with strict JSON Schema, valid/invalid fixtures and Python/TS semantic validators; version/evolution rules; stage dependency graph.
3. Create fresh migration and isolated history SQLite/store. Explicitly reject old YTB DB revisions; preserve operator backup. No compatibility readers and no silent DB wipe.
4. Extract neutral external prompt builder/duplicate-key JSON parsing/reservations/hash/CAS, process guards, Kokoro/voice units and FFmpeg utilities with regression tests. Delete retired V4 services/routes/prompts/tests/migrations only AFTER extracted tests pass in the same clean-cut change.
5. Preserve CockpitShell/sidebar/cards/modals/selected project state and the dark design system. Rewire Studio to the canonical history pipeline and stage readiness using new DTOs; no old Scene Assets/Spine features in navigation.
6. Introduce startup/version checks, dedicated local paths, audit banlist for forbidden old imports/LLM SDKs and typed errors.
P1 gate: one clean product, fresh empty DB launches and creates Channel/Project; navigation screenshots at 1440x900 and 1920x1080 reflect YTB Cockpit; restart restores project; old routes/tables/Spine references absent in production. Windows startup test and full regression pass.

### P2 — External ChatGPT Topic + Script authoring

Tasks:
1. Create local prompt package builder for TopicIdeas using channel DNA, idea count, audience, constraints, specific history research instructions and embedded output schema. Reserve artifact ID, freeze parent fingerprints and persist pending package metadata.
2. UI: Build Prompt / Copy / Download; Import JSON file; Validate-only; inline error report; Review/Approve; Build Repair Prompt. Show inputs clearly; no "Generate with API" actions.
3. Implement user-select/approve Topic or manual topic pathway with equivalent lineage.
4. Implement ScriptAnalysis authoring similarly, emphasizing EXACT spoken text, claims, classification (historical fact/uncertain/reconstructed), evidence leads and factual risk. Script + historical analysis approval atomic.
5. Test duplicate keys, malformed Unicode/JSON, oversized uploads, unknown fields, stale reserved IDs, concurrent approvals, foreign project artifacts, locked parent and exact quote integrity.
P2 gate: from a history seed to approved script using only external ChatGPT handoff; no external model connections attempted, operator can resume after restart.

### P3 — Narration + word alignment

Tasks:
1. Extract Kokoro local TTS with exact runtime/voice identity, calibrated pacing safeguards and WAV duration QC from old repo; keep external ElevenLabs voice and final-WAV forced-alignment optional/configured as NON-LLM media operations.
2. Source only approved script exact text; generate draft candidates, let user choose/approve FINAL WAV; no mutation of approved source audio by later operations.
3. Align the exact final WAV; publish WordTimingMap from final audio hash; validate positive duration, monotonic intervals, coverage/unknown word warnings and segment text identity. If alignment cannot run, block final synchronized production; never approximate silent timings.
4. Add media jobs with cancellation/retry/attempt fencing, restart persistence and identity mismatch rejection. Reuse neutral durable-workflow primitives only after isolation tests.
P3 gate: playback of voice, approval, real alignment, validated word timings and restart/partial failure tests. Negative: changing text or audio invalidates prior alignment.

### P4 — Historical Asset Research / Resolver / library

Tasks:
1. Build External ChatGPT AssetResearchPlan prompt and strict JSON import/review; candidate URLs are UNTRUSTED and not assets yet.
2. Add allowlisted archive/metadata integrations, initial manual-upload workflow and safe downloads with URL scheme/host/IP/redirect checks, limits, MIME sniffing, decode dimensions, hashing and no internal LLM. Prioritize Wikimedia Commons and Library of Congress, but require source-item confirmation.
3. Asset table UI: thumbnail, source, author, date/period, historical relevance, copyright/license evidence, attribution, rights uncertainty, approval/reject. Support upload/crop/transparency/contrast derivations as versioned non-destructive operations.
4. Explicit historical map rules: source year/period, geography/legend, claims linked; no modern borders silently used for historical depiction. Allow verified primary map or clearly labeled schematic diagram only.
5. Resolve asset references into frozen local approved versions, with media cache and provenance. Missing/unverified/copyright-uncertain asset blocks default publication; require explicit documented exception for internal study preview only.
P4 gate: at least 8 distinct real/approved archival assets in a reference project; all have verified metadata and hashes; blocked candidates never reach release render. Recover on restart.

### P5 — External ChatGPT Visual Director

Tasks:
1. Build prompt using frozen approved script, exact word timing, approved asset inventory, visual grammar, factual/visual continuity cues and reference profile. Enumerate allowed layer types/layout regions, motion presets and pacing constraints; require a downloadable JSON file.
2. Import VisualMotionPlan; strict schema and semantic validator: exact parent hashes; known IDs; all events anchored to real word/phrase refs; bounded setup/hold logic; layer/template vocabulary; title length/caption safe-zone bounds; no arbitrary code or remote fetch. Validate shot density, information reveal rhythm and asset license state.
3. Targeted repair prompts list ONLY failed fields/segments and frozen valid parts. On reimport, preserve approved unaffected story and asset identities.
4. Deterministic compiler maps semantic anchors to integer frames, resolves approved asset versions and layout templates, freezes CompiledTimeline for renderer; explicit errors for overlap, unsupported effects or overfull text.
P5 gate: External ChatGPT file alone creates a validated, immutable film plan for an approved project; no LLM API; two identical compiles have byte-identical normalized output/hash.

### P6 — Production Remotion engine + first complete narrated video

Tasks:
1. Promote verified POC to production runtime/remotion; reuse single Composition between @remotion/player and renderer; pin dependencies/lockfile with clean Node render-host interface.
2. Build reusable motion components: PaperCanvas, ArchivePhoto, PortraitCutout, HistoricalMap/SchematicMap, HeadlineReveal, Timestamp, Label, Stamp, RouteDiagram, Arrows, Annotation, FactComparison, CameraPanZoom. Combine multiple layers in same persistent VisualSetup, with enter/hold/exit and cross-setup continuity.
3. Render ONLY compiled timeline and local approved sources. Embed final narration WAV, optional music/SFX from approved local asset versions, captions where enabled; implement exact-frame scheduling and transition rules. Match speaker cues to words/phrases.
4. Build a renderer-owned preview surface in Cockpit and a bounded Node command/process protocol for deterministic MP4; do not run untrusted JSX. Cache reusable source transformations and a hashed timeline render where safe.
5. End-to-end pilot: one approved history project, >=60–90 seconds, factual assets, final narration, multiple setup types, at least one correctly labeled route/map, word-cued text and stamp reveals, final H.264 1080p/30fps MP4.
P6 gate: TRUE FIRST COMPLETE VIDEO is playable and independently verified with screenshot storyboard and FFprobe; must contain non-silent narration and reviewed real assets. Do not call finished if only placeholder images or synthetic timings.

### P7 — Timeline editor and selective revisions

Tasks:
1. Responsive Cockpit timeline view: persistent setup track, separate voice/asset/text/route/stamp layer tracks, playhead, zoom/scrub, asset inspector and controlled editing. Preview must use the approved/rematerialized composition, not DOM mimicry.
2. Support editing layer position/scale, visibility, text/label, cue offsets, preset/easing, asset swap and visual setup hold. Save candidate revisions; revalidate and approve; Ctrl-Z/undo only as editor state, not silent mutation of approved artifacts.
3. Hash-based dependencies and selective rebuild: modifying one layer need not regenerate script/voice; audio or exact script changes invalidate timings and derived motion; changing asset version invalidates referencing compositions and renders, not unrelated projects.
4. Provide precise structured errors and external repair prompt generation for creative failures. No automatically-invoked model calls.
P7 gate: operator changes one label's timing and a single asset, confirms unaffected earlier approved portions retain identity, previews exact change, renders updated video and safely restores project after restart.

### P8 — Render durability, QC, packaging and release acceptance

Tasks:
1. Durable fenced RenderOperations/attempts with idempotency key, owner/lease, cancel/timeouts, stdout/stderr bounds, Windows Job Object process tree kill-on-close, correct error state after crash, no duplicate output publication.
2. Audit FFprobe duration/fps/size/codecs/audio presence, audio peaks/clipping/silence, frame samples/black frames/freeze anomalies, missing/off-canvas critical text, asset attribution coverage and timeline/audio source hashes. Editorial historical factual assessment remains explicit human approval, not a claimed deterministic AI fact-check.
3. Render/preview parity screenshots for selected frames; smoke test projects at 1 minute and 5+ minutes, restart/crash/retry and file path security tests. Benchmark render time without inventing performance guarantees.
4. Export MP4, QCReport, attribution/rights manifest, project JSON manifest and reproducible source fingerprints. Add Windows local launcher and clean install/upgrade instructions. Keep secrets out of files/logs.
P8 gate: exact candidate SHA passes frontend/backend/Windows render/Remotion/browser QC jobs and manual reference-style review; output survives restart and can be reproduced from approved sources. No final PASS on partial checks.

## 6. Explicit release milestones

M-A (after P6 and minimal P8 checks): ONE 60–90 second complete, narrated video from idea -> external ChatGPT JSON imports -> verified assets -> synchronized Remotion -> MP4. User handoffs/approvals are expected.
M-B (after P7–P8): resilient multi-minute production with timeline editor, selectable repairs, repeatable rendering and comprehensive delivery QC.
Unattended API-driven creative generation is NOT a goal; do not claim "one-click AI generates everything" while manual External ChatGPT steps remain part of the contract.

## 7. Cross-cutting acceptance/security matrix

- LLM closure: source/dependency/config scan for prohibited LLM providers or chat-completion endpoints; network mocks prove no internal LLM calls, including error/retry paths.
- External JSON: duplicate keys, wrong schema, malformed/null nested objects, unknown fields, reserved ID mismatch, stale project/channel heads, malicious text/path/URL, 10 MB limits (configurable), race in approval, double-submit, invalid source lineage.
- Asset security: local path containment, symlink/reparse escape, illegal file types, size/decompression bombs, SSRF/private network redirect, malicious SVG/script, unapproved remote URLs and rights/attribution omissions.
- Timing: exact audio hash, monotonic alignment, no negative/fractional frame in compiled form, event stays inside allowed frame ranges, setup overlap/crossfade explicit, anchor within true audio duration.
- Determinism: canonical serialization, SHA256 input keys, compile repeatability, preview/render same composition/timeline and local asset bytes.
- Render: Node/browser version pins, subprocess guard, FFprobe, frame samples and Windows crash/cancel tests, audio present for production, status correct after restart, output hash lineage.
- UI parity: YTB navigation/grid/modal/header proportions, dark tokens, visual feedback and saved selection at 1440x900 and 1920x1080; no fake/inert buttons.
- Historical integrity: distinguish authentic archive asset vs illustration, year/period map verification, human fact/source review; educational/noncommercial purpose never bypasses rights checks.
- CI policy: each changed phase has dedicated exact-SHA jobs. A green legacy CI matrix is not proof of new workflow. Any changed commit invalidates prior acceptance evidence until rerun.

## 8. Implementation protocol for Codex / coding model

Before coding a phase:
1. Read this entire plan and both architecture authority documents; fetch current HEAD/PR state.
2. Confirm phase prerequisites and record a KEEP/CHANGE/DELETE inventory for touched directories; identify tests to move before deleting sources.
3. Create a phase-specific PR from exact accepted previous-phase commit. P0 work can amend existing PR #2; do NOT use PR #1 Spine branch as base.
4. State precise input/output contract owner, one true stage head, invariants, expected API/UI changes, file paths and negative tests.
5. Implement code and tests only for current phase; no dummy endpoints, fake completion, compatibility fallback or broad refactors; if a dependency is inaccessible, label BLOCKED and provide reproducible steps.
6. Run focused tests, then affected full suites and Windows/browser/render gates where required on the exact candidate SHA. Capture evidence (logs, screenshots, FFprobe, source hashes) in PR or named artifacts.
7. Self-review source diff for architectural violations, source leaks, license misuse, unsafe path handling, visual quality regressions, missing negative tests and UI parity. Fix and re-run evidence after any patch.
8. Report DONE/PARTIAL/BLOCKED per task, actual checks and open risks. Never mark a phase PASS based on README claims or old unaffected CI green.

For P0, the immediately executable model prompt is in docs/implementation/CODEX_P0_REMAINING_WORK_PROMPT.md. It must be handed to the coding model with this plan.

## 9. Anti-scope and decisions still to freeze

Do NOT: add GPT/Claude/Gemini/local model provider configuration; scrape ChatGPT session; accept arbitrary JSX emitted by ChatGPT; map script sentences directly to one image each; force every setup to reset on a sentence/scene boundary; silently approximate unaligned words; use modern map as historical proof; auto-approve ChatGPT suggested rights; merge PR #1 unreviewed; mutate the old YTB repository; build a parallel replacement UI.

Decisions to freeze in P1: exact history ChannelDNA schema; stage key enumerations; SQLite tables and migrations; strict legal archive connector allowlist; alignment provider options and availability; default aspect ratio/profile; first pilot story and authorized asset selection; selective invalidation graph. Default plan assumptions: Windows, 16:9, 1920x1080, 30fps, H.264, Vietnamese demo acceptable; switch language/ratio only through explicit project config and tests.

## 10. Required PR report format

- Current phase / exact base + head SHA / scope and changed files
- Architecture and licensing compliance (including NO LLM API)
- Implemented functionality, screenshot/MP4 evidence
- Tests and commands actually run, exact outcome, CI links
- Negative/security cases, affected previous stages/regressions
- Acceptance criteria checklist with proof for each
- Open blockers / targeted follow-up
- Final verdict: PASS only when ALL mandatory phase gates are verified; otherwise PARTIAL or BLOCKED.
