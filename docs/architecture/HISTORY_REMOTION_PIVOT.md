# Architectural decision proposal — Historical Motion Graphics (Remotion)

Status: **proposed, not approved/implemented in production**, 2026-10-15. Target: personal history education, local Windows tool. Reference implementation: `experiments/remotion-history-poc/`.

## Research and repository conflict

Remotion upstream: https://github.com/remotion-dev/remotion, packages `core` (remotion compositions and frame hooks), `player` (embed in React), `cli` (local render) and `renderer` (server render). Use published, pinned dependencies; do not copy the monorepo or republish a derivative Remotion product. Individual/free eligibility is described in https://www.remotion.dev/docs/license.

The **existing draft PR #1** on `feat/c0-clean-cutover-contracts` assumes Spine/Pixi, actor rigging, seven stages and 14 SQLite tables. The user-approved historical reference instead needs mixed archival photographs, maps, kinetic typography and composited layer effects. Consequently, the PR #1 animation plan, rig tables/stages and fixed motion actions are **not normative for this product**. Do not merge or implement C1/C2 under those assumptions. This R0 experiment is a separate branch off main; it does not overwrite PR #1.

## Proposed product workflow

`Local Prompt Builder → user runs External ChatGPT → user imports validated Topic Ideas JSON → user imports validated Script + Historical Analysis JSON → Voice synthesis/final audio approval → Word alignment → user imports External ChatGPT Asset Research Plan JSON → evidence/asset lookup and review → user imports External ChatGPT Visual Director/Motion Plan JSON → deterministic timeline compilation → multi-layer preview/editor → Remotion render → QC → final MP4`.

**Mandatory external-authoring rule:** [EXTERNAL_CHATGPT_AUTHORING_CONTRACT.md](EXTERNAL_CHATGPT_AUTHORING_CONTRACT.md) is authoritative for every authoring stage. No LLM SDK/API/model execution inside the application, including research and visual planning. The app assembles copyable prompts and strictly imports user-downloaded JSON files; approval gates feed the deterministic media pipeline. This overrides any suggestion that the backend itself generates topics, scripts, or motion plans. Non-LLM TTS/alignment, approved-archive metadata APIs and Remotion remain distinct, optional services.\n\nVoice first for **timing** (not as a reused legacy control plane). No mandatory AI image generation per beat. A visual setup persists across multiple spoken thoughts and a motion event controls one layer within it. AI emits *data*, not arbitrary executable JSX/JavaScript or untrusted CSS.

## Contracts to freeze after R0

- `ScriptDocument`: stable text segments and historical-claim references.
- `VoiceTimingMap`: immutable final audio ID/hash, word/phrase intervals (seconds), and alignment confidence.
- `EvidenceAsset`: immutable version/hash, normalized local path, origin URL, attribution, license/public-domain evidence, factual scope, approval state.
- `VisualSetup`: global frame interval, background family, layout regions, continuity/hold behavior.
- `MotionLayer`: stable id, kind, asset reference, bounding box, z-order and look.
- `MotionEvent`: frame interval, transform/opacity/path/reveal preset, reference word/phrase IDs, easing data.
- `CompiledTimeline`: approved frame quantization, resolution, fps, source hashes and change invalidation fingerprint.
- `RenderOperation/RenderManifest/QCReport`: durable job, idempotency, render output/hash/FFprobe metadata and visual/semantic QC checks.

Immutable source-of-truth is the approved `CompiledTimeline`; per-frame renderer is a **pure projection** of its data. Validation requires full setup coverage, stable IDs, correct asset/voice lineage, no off-canvas text, missing assets, unsupported layer actions or absolute/path-traversal media. A historical map requires time-correct geographical evidence. Source assets are not fetched from arbitrary user URLs during rendering.

## Remotion adoption boundary

React UI: preserve `CockpitShell`, navigation, project selection and dark design tokens. Embed `@remotion/player` with **exactly the same composition** used by the background render executor. Renderer: run `@remotion/renderer` or CLI in a bounded, supervised Node child process with isolated work/output directories. Backend can remain Python/FastAPI for project metadata, evidence workflow and operations. Never run arbitrary AI-written JavaScript. No production legacy/compatibility path. No destructive migration of prior YTB data: clean disposable DB only after explicitly approved cutover.

## Phase gates

- **R0 Feasibility:** 60-second isolated preview and local H.264 render; frame bounds tests; editorial quality comparison; no LLM, historical facts or backend claim.
- **R1 Authority:** finalize schemas for topic/script/audio/asset/setup/layer/events and mandatory external ChatGPT prompt/import/validation gates; enforce no-LLM-SDK/static dependency checks; compatibility decision for PR #1; clean database design and shared Cockpit UI parity.
- **R2 Voice + evidence:** final audio alignment and research/asset review with license/source attribution; no blind web-scrape-as-evidence.
- **R3 External ChatGPT Visual Director:** build/copy/download prompt package, user-imported JSON, schema/semantic/source-lineage validation, targeted repair prompt (no LLM API), deterministic timeline compilation and zero arbitrary executable code.
- **R4 Editor + operations:** asset chooser, scene/layer timeline, single composition preview/render parity, durable background host, cancellation, crash recovery.
- **R5 Quality gate:** comparative sample review against input video, style consistency, map factual validation, render/black-frame/audio/metadata checks.

## Explicit exclusions at R0

No autogenerated AI plan, historical source retrieval, voice production, asset rights verification, production backend routes, schema migration, video export API, old pipeline deletion or claims of automatic quality parity. These are gated later. Neither the Spine PR nor its database design is automatically carried forward.
