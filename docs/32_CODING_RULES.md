# Coding Rules

## Mandatory implementation gate

Before changing product behavior, read `00_START_HERE.md`, this file, `docs/99_SOURCE_OF_TRUTH.md`, and the stage owner contract. Record a rule-compliance snapshot before coding.

## Active-development data policy

- Do not add compatibility readers, schema-version dispatch, payload adapters, old-flow fallback, data-copy migrations, backfills, rollback transforms, or artifact conversion unless explicitly approved.
- Old databases, fixtures, and generated artifacts may be deleted and recreated.
- Structural schema changes use Alembic and must pass clean-database upgrade, model/schema alignment, and startup tests.

## Single-flow ownership

- `app.modules.platform` owns Channel, Project, Topic external authoring, combined Script & Analysis external authoring, and media foundations.
- `app.modules.media.durable_workflow` owns only neutral durable operation/unit/task coordination, provider-execution sequencing, timing helpers, and process hosting. It must not know BeatState, AnchorState, scene/section semantics, continuity, Style Bible inventory semantics, or publication contracts, and it must not introduce generic physical workflow tables.
- `app.modules.v4` owns deterministic Style Bible, Visual References, full-Script Scene Breakdown import/projection, internal Prompt Bundle, Scene Assets, and production stages.
- `app.core.external_authoring` owns only neutral prompt packaging, reserved Artifact ID extraction, strict JSON parsing, true upstream-source checks, and validation response helpers. `app.services.external_authoring_reservations` owns pending/consumed authoring reservations.
- The application must not call an LLM or web-research provider internally.
- Do not restore LLM settings, provider gateways, research jobs, creative generation jobs, old routes, or diagnostics surfaces.

## External authoring rules

- Building a prompt preallocates the canonical Artifact ID or paired Artifact IDs, stores one pending reservation for the project and stage, and requires ChatGPT to echo those exact IDs in the JSON output.
- Validate-only endpoints never persist artifacts.
- Import endpoints rerun parsing and all validation; they never trust a prior validation response.
- Duplicate JSON keys, missing/unknown/mismatched/consumed Artifact IDs, true upstream-source changes, unknown fields, unsupported references, and semantic mismatches fail explicitly.
- The backend never auto-corrects imported creative JSON.
- Topic `avoid_topics` is backend-derived from currently selected topics in the same Channel and is not a separate UI field.
- Script is normalized before Analysis is resolved; Script and Analysis publish atomically.
- Full Scene Breakdown validates every Script section before the draft Scene Plan and Visual Plan publish atomically.
- Scene Breakdown approval deterministically compiles Prompt Bundle and BeatTimingMap and publishes approved Scene Plan, approved Visual Plan, approved Prompt Bundle, and approved BeatTimingMap in one guarded transaction.

## Contract and artifact rules

- JSON objects are strict unless a contract explicitly states otherwise.
- Imported output is untrusted until schema and semantic validation complete.
- Persist immutable artifact bytes, content hash, lineage, and status.
- Consumers validate schema, byte integrity, current project pointer, approval state, and inherited lineage.
- Publication uses compare-and-set ownership; staged artifacts are removed when publication loses or any later stage of the same transaction fails.
- Never silently repair, coerce, reinterpret, or compatibility-convert persisted data.
- Runtime response schemas, static OpenAPI, DB models/migrations, and contract registries must describe the same active architecture. A syntactically valid but stale static OpenAPI file is a contract failure.

## Unified image rules

- Every Scene Breakdown beat describes one generated image through the common visual fields. Do not add an asset type, strategy, discriminator, or equivalent classification.
- Text-led, infographic-like, symbolic, editorial, and cinematic visuals use the same image contract and the same media-provider path.
- Prompt Bundle contains exactly one prompt item for every Visual Plan beat, in canonical order, with no exclusion list.
- Scene Assets must consume the exact current approved Prompt Bundle; it must not reconstruct prompt semantics.
- Do not restore local text-card/diagram rendering, existing-asset resolution, or transition-only asset handling.
- Transitions are timeline behavior between image clips, not an asset category.

## Durable media execution rules

- Scene Assets V2 live generation state is DB-backed (`WorkingOperation` → `OperationBeat` → `OperationTask`) and Visual References V2 live generation state is DB-backed (`WorkingOperation` → `OperationAnchor` → `OperationTask`). Neither domain may depend on request polling, Redis, RQ job ownership, or an in-memory queue for correctness.
- Scene Assets and Visual References retain separate physical tables, repositories, domain state, mutation rules, and publication authority. Do not replace them with generic operation/unit/task tables.
- Automatic process scheduling for Scene Assets V2, Visual References V2, and Voice is owned by the shared Durable Media Workflow Host. The host gives each registered domain bounded reconcile turns and must not become a second domain state machine.
- Render is SQLite-backed through `RenderOperation` durable scheduling/lease fields and a generic Job status projection, but Render is **not** registered in the shared Durable Media Workflow Host. Long-running FFmpeg/FFprobe execution belongs only to the Dedicated Durable Render Host.
- Render submit/retry must persist queued state plus `next_reconcile_at` and return; they must not dispatch a queue message. The Dedicated Durable Render Host claims due operations by owner/token/expiry fencing, renews the lease during execution, and rejects stale execution tokens/attempts before progress or publication writes.
- Redis/RQ packages, imports, queue dispatch, the platform Backend Worker, legacy Render worker/recovery entrypoints, and the Windows worker launcher are removed active runtime. Do not restore them without an explicit architecture change approved before implementation.
- Coordinator work is selected from persisted `next_reconcile_at` and protected by owner/token/expiry fencing. Crash/restart recovery must derive only from persisted state.
- Provider submission, poll, download, and materialization I/O must occur outside SQLite write transactions. Persist pre-I/O intent and revalidate ownership immediately before external I/O and before materialization.
- Ambiguous provider submission is explicit; never silently resubmit when provider acceptance cannot be proven absent.
- GET endpoints are read-only projections. They must not call provider reconciliation, acquire execution ownership, alter task state, or be required for an operation to make progress.
- Studio execution polling is active-operation-driven and targeted. Do not poll/reload a whole workspace to drive progress.
- Operation/unit health including `stalled` is derived observability from persisted execution state, never a second state machine.
- Scene continuity-source changes invalidate the full transitive descendant closure. Invalidation must be deterministic, cycle-safe, preflight active descendants, and commit atomically.
- Published Scene Asset Manifest 4.0 is immutable downstream authority; it is not live execution storage or a beat-local CAS token.
- Visual Reference Set and Visual Reference Approval Set are immutable publication snapshots. Mutable prompt, candidate, review, and execution authority belongs only to Visual References V2 working state.

## Product boundaries

- Channel DNA defines identity and constraints; it does not generate content.
- Channel DNA `narration_identity` is free-form creative intent. Backend/runtime code must not define narration-style enums, named creative profiles, humor/seriousness ratios, genre-to-style mappings, or default narration personalities. The backend may validate structure, snapshot, transport, hash, and expose the supplied values only. SKILL/prompt guidance may teach adaptable creative interpretation, but arbitrary and previously unseen channel voices must remain valid without a source-code change.
- Channel DNA `visual_identity.editorial_language` is free-form channel identity for visual communication grammar. Backend/runtime code must not convert it into representation-mode enums, shot quotas, asset categories, meme taxonomies, or creative defaults. It may validate, snapshot, transport, hash, deterministically project, and expose the supplied values only.
- Narration identity, visual style/editorial language, and video pacing mode are independent. Do not infer one from another in runtime code.
- Topic Ideas owns imported topic candidates only.
- Combined Script & Analysis owns ordered Script sections, narrative guidance, markers, and continuity catalog as one authoring transaction.
- Style Bible deterministically projects Channel DNA and the effective reviewed continuity set derived from the current Script Analysis; continuity review never rewrites the imported Analysis artifact.
- Visual References deterministically compile anchor prompts; Visual References V2 working state owns prompt revisions, candidates, exact-anchor review, durable operations, and CAS ownership. Publication materializes immutable Visual Reference Set and Approval Set snapshots only after the current inventory is fully approved and idle.
- Full-Script Scene Breakdown owns scenes, beats, and beat-level image decisions, then projects paired Scene Plan and Visual Plan artifacts.
- Prompt Composer is an internal deterministic compiler invoked by Scene Breakdown approval; Prompt Bundle remains a backend lineage boundary but is not a Studio stage.
- Scene Assets V2 owns beat-local prompt revisions, candidates, review state, durable operations, continuity invalidation, and Scene Asset Manifest publication.
- Production stages consume only their approved immediate inputs and do not reconstruct upstream semantics.

## Timing and rendering rules

- Requested `target_duration_minutes` is the runtime authority. Every externally authored Script section must declare and lock its own `planned_narration_wpm` within broad defensive bounds before prose is frozen. The backend estimates runtime by summing section-local planned durations and derives whole-Script `planned_average_wpm` only as an aggregate diagnostic. Estimated runtime must not hard-reject otherwise-valid creative Script prose. No fixed WPM constant or word-count acceptance range may own Script length.
- Video pacing mode is a per-video user choice (`fast | medium | slow`) recommended by Topic Ideas and user-overridable before Script authoring. Channel DNA owns the numeric calibration for all three modes; each Project snapshots those profiles. Runtime code validates and resolves the selected mode from that Project snapshot and must not define or fall back to a global numeric pacing table. Section `pacing_density` remains `high | medium | low`. The selected profile is a binding authoring/conformance target but never a beat quota, word envelope, seconds-per-sentence rule, or permission to invent/remove semantic states.
- Final Scene Breakdown Beat boundaries are semantic choices over backend-owned legal timing-atom boundaries: Step 1 selects a boundary only for a meaningful independently drawable source-owned contribution, including story/visual state changes, information/causal increments, evidence components, or distinct explanatory/editorial facets already present in narration. Timing, wording, emphasis, and camera variety alone never justify selecting a Beat boundary. Backend timing may rank compact legal partition recommendations but may not invent meaning or silently replace the model's legal semantic selection; absent an honest semantic/facet boundary, the truthful Beat may remain outside the preferred band.
- Actual TTS timing supersedes planned timing for downstream assembly. Voice must preserve Script section `planned_narration_wpm` as provider-neutral pacing authority, treat provider speed/rate as native hints, and apply final section-level pacing correction outside provider-specific adapters.
- Beat narration duration must never be shorter than authoritative audio.
- Timeline boundaries use integer time units and remain gap-free and monotonic.
- Every assembled beat requires one approved image.
- Render frame count is authoritative and validated with the current render graph contract.
- Render execution must preserve the frozen profile/FFmpeg identity/command graph/frame quantization and exact Assembly/SRT/VTT/final-pointer lineage from submission through Final Video publication. A stale lease or stale attempt may not update progress, terminal state, or publication authority.
- On Windows, every Render-owned FFmpeg/FFprobe subprocess tree must be attached to the host-owned Windows Job Object with kill-on-close behavior. Cancellation, timeout, lease loss, host failure, and recovery must terminate or abandon the old owned process tree before a new durable attempt can execute.

## UI rules

- Always show selected Channel and Project context.
- Persist selected Channel and Project across pages.
- Display validation, Artifact ID/reservation, upstream-source, prompt-compilation, and CAS conflict errors visibly.
- Prompt Bundle must not appear as a Studio navigation stage.
- Scene Breakdown approval owns aspect ratio; current Prompt Bundle options may hydrate that selector without exposing a Prompt Bundle workspace.
- Large scene/beat collections use progressive mounting.
- Scene Assets and Visual References share the Active Operations drawer shell while retaining domain-specific operation cards and actions.
- Workspace reads are for initial/boundary loading; active execution uses targeted execution polling and exact beat/anchor refresh instead of full-workspace polling.
- Do not expose removed asset categories, local-render controls, or internal LLM/research/provider concepts.

## Final verification order

1. Rule compliance and ownership
2. Contract/schema validation, including runtime/static OpenAPI parity
3. External authoring validation and publication behavior
4. Four-artifact Scene publication lineage, CAS, source-change, tamper, and cleanup tests
5. Visual References V2 publication/durable-execution regression and Scene Assets single-provider/durable-execution regression
6. Assembly/render regression
7. Clean database and concurrency tests
8. Frontend source/build tests
9. Static/runtime OpenAPI parity
10. Closure scans, including zero active Redis/RQ imports, dependencies, queue dispatch, platform worker ownership, and legacy Render worker/recovery surfaces
11. Shared durable-host and Dedicated Durable Render Host startup/launcher coverage
12. Windows Render process ownership, crash/recovery, and exact-head candidate gate
13. Clean tree, deterministic manifest, and archive audit

### Voice-first Scene cadence input boundary

- Scene LLM input must not expose Script `planned_narration_wpm`, raw `pacing_mode`, raw `video_pacing_profile`, or raw Script Analysis `pacing_density`.
- Backend may consume upstream profile+density internally only to resolve one `visual_cadence_guidance.preferred_hold_seconds` range for each section. Step 1 receives that same resolved range once in compact `sections[]` as a post-keyframe semantic-rescan signal; no raw pacing profile, density, WPM, timing container, timestamp, sample position, duration, or timing atom crosses the model boundary.
- Backend owns Step-1 planning containers and technical timing choices: deterministic micro-Scenes come only from safe authored clause structure, and every legal Beat boundary comes only from canonical timing atoms.
- The external Step-1 model owns semantic still-image partition selection: it chooses an ordered subset of the supplied legal boundary IDs, including the empty subset when the fixed micro-Scene is one Beat. Backend must reject invalid selection rather than silently repair or substitute a partition.
- Scene boundaries are planning-container boundaries, not continuity resets. Only the first Beat of the whole plan is forced to `new_setup`; later Scene starts follow their locked relation to the immediately previous Beat.
- Approved Narration Audio + NarrationWordTimingMap remains the only timing authority. Use the single project-global canonical visual-interval projection for Scene planning, selected-plan QC, reusable-fragment timing validation, and final BeatTimingMap compilation; never duplicate Section-local visual-duration arithmetic. Section-tail silence/gaps and final narration tail must therefore resolve identically everywhere. Numeric duration never manufactures semantic content. Final BeatTimingMap remains backend-owned and deterministic.
- Review/edit validation for Section N > 1 must rebuild the exact current preceding paired fragments and pass canonical carry from the immediately previous validated Beat. Do not reset carry at a Section boundary. Ordinary review/edit may change approved semantic/visual metadata only; Beat/Scene source ownership, resolved spans, ordering, and membership are immutable and any resegmentation requires rebuilding/repartitioning Step 1 through v7 legal boundaries.
- Prompt Bundle and Scene Assets continuity dependencies follow immediate global Beat order and may cross Scene/Section boundaries when the locked continuity relation authorizes that scoped carry.
