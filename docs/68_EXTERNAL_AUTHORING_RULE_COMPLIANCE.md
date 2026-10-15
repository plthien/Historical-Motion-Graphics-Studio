# External Authoring Rule-Compliance Record

## Implemented scope

Internal LLM and research execution is replaced by external prompt build plus strict JSON import for Topic Ideas, combined Script & Analysis, and Scene Breakdown.

Scene Breakdown remains one top-level Studio stage but uses two internal external-authoring steps: Step 1 locks the semantic Scene & Beat Plan, then Step 2 authors one generated-still visual per locked Beat. Deterministic Style Bible, Visual References, internal Prompt Bundle, Scene Assets, Voice, Assembly, subtitles, and Render ownership is preserved. Render execution ownership is now SQLite-backed under the Dedicated Durable Render Host rather than Redis/RQ.

## Compatibility and data

- One active external-authoring flow per stage. Scene Breakdown's active flow is the internal Step 1 → Step 2 authoring sequence; there is no independent Visual Planner stage.
- No compatibility readers, old-flow provider fallback, backfill, data migration, or artifact conversion are required for downstream canonical artifacts.
- Development databases and generated artifacts may be recreated.
- Current Alembic head is `030_render_durable_sqlite`; clean-database upgrade and model/schema alignment are required release gates.

## Contract ownership

- `app.modules.platform` owns Topic and combined Script & Analysis prompt/import services.
- `app.modules.v4` owns Scene Breakdown Step-1 semantic Scene/Beat locking, Step-2 Visual authoring, paired draft publication, automatic Prompt Bundle and BeatTimingMap compilation during approval, and production stages.
- `app.core.external_authoring` owns neutral prompt packaging, reserved Artifact ID extraction, strict raw JSON parsing, true upstream-source checks, and validation helpers. `app.services.external_authoring_reservations` owns pending/consumed reservations, including invalidation of a pending Step-2 Visual reservation when Step 1 is rebuilt.
- Scene Assets V2, Visual References V2, and Voice retain separate durable SQLite graphs scheduled by the shared Durable Media Workflow Host.
- Render submit/retry persist durable SQLite scheduling state; the Dedicated Durable Render Host owns execution fencing, FFmpeg/FFprobe process ownership, recovery, and guarded Final Video publication.
- Raw external response schemas are distinct from canonical immutable artifact schemas.

## Removed runtime

- internal LLM gateways, providers, settings, diagnostics, and provider registry;
- Research & Generate and web-research sidecar/configuration;
- standalone Script and Script Analysis generation;
- internal Scene Breakdown generation/regeneration/resume jobs;
- independent top-level Visual Planner generation;
- asset classification and discriminated visual detail unions;
- Prompt Bundle Studio stage and manual Studio prompt workflow;
- local text-card/diagram rendering, existing-asset binding, and transition-only asset handling;
- Redis/RQ dependencies, queue dispatch, the platform Backend Worker, legacy Render worker/recovery entrypoints, and the Windows worker launcher.

## Scene Breakdown authoring and publication invariants

- Backend owns deterministic micro-Scene boundaries, every technically legal Beat boundary, exact Script spans, approved-Voice timing/sample arithmetic, deterministic materialization, validation, and final BeatTimingMap. Step 1 owns the ordered semantic subset of those legal boundary IDs plus Beat semantic meaning/state/delta and continuity/reset authority. It outputs no source offsets/timestamps/durations and no final visual-directing prose.
- Step 1 validate/import materializes a locked candidate only in durable authoring reservation state and does **not** change current project ScenePlan/VisualPlan pointers.
- Step 2 consumes the exact locked Step-1 scene/Beat IDs and states. It may author visual direction only and cannot re-segment, reorder, move semantic/reveal authority, or change continuity/reset.
- Rebuilding Step 1 invalidates any pending Step-2 Visual prompt tied to the superseded semantic candidate.
- Step 2 must cover every locked scene and Beat exactly once and in exact order before canonical publication.
- After Step 2 passes, ScenePlan and VisualPlan publish together with one existing Scene/Visual CAS. Project readers never observe a half-pair.
- Validate-only endpoints never persist canonical artifacts or move project pointers.
- Script and Script Analysis stage and publish atomically with one CAS.
- Scene Breakdown approval compiles one prompt per beat plus BeatTimingMap from the approved Voice timing authority, validates full coverage and lineage, then publishes approved Scene Plan, approved Visual Plan, approved Prompt Bundle, and approved BeatTimingMap with one guarded four-pointer publication and one commit.
- Any compile, validation, staging, or CAS failure publishes none of the four approval artifacts and removes staged files.
- Prompt Bundle remains the current approved immediate input to Scene Assets; BeatTimingMap remains the Scene-owned timing authority consumed downstream by Assembly.
- Losing publication cleans all staged artifacts.
- Render publication revalidates exact current Assembly/SRT/VTT/final-pointer lineage and immutable source bytes, then uses one Project final-video pointer CAS; stale attempts cannot publish.

## Review evidence

Release review must cover named JSON-file delivery, reserved Artifact ID issuance and reload-safe lookup for both Scene Breakdown internal steps, missing/unknown/mismatched/consumed ID rejection, Step-1 rebuild invalidation of stale Visual reservations, true upstream-source changes, duplicate-key rejection, selected-topic avoidance, quote resolution, exact full-Script coverage, semantic Beat-state lock, Step-2 exact scene/Beat mapping, unified image projection, exact all-beat prompt coverage, four-artifact Scene approval publication including BeatTimingMap, Scene Assets provider-only generation, downstream timing/Assembly regression, OpenAPI parity, Redis/RQ closure, clean-candidate tests, manifest verification, archive audit, and the required Windows Render candidate gate.

For the Render ownership cutover, focused evidence covers SQLite submit/retry scheduling, lease/attempt fencing, host-crash recovery to a new attempt, Windows Job Object process-tree termination, launcher preflight, Final Video CAS/lineage preservation, and absence of Redis/RQ dependencies or queue dispatch.

- Scene Breakdown prompt context is stage-isolated further: raw Script WPM, raw video pacing mode/profile, raw Analysis density, raw timing atoms, sample offsets, source offsets, and model-side timing-repair algorithms are not exposed to Step 1. Backend creates deterministic micro-Scenes only at authored natural endpoints that are also safe timing-atom endpoints, including semicolon and bare-newline clause boundaries, while abbreviation protection remains active; every emitted micro-Scene must meet the section's preferred minimum hold or fail closed. It computes every technically legal timing-atom Beat boundary ID and exposes the fixed Scene source once with the complete legal boundary lattice. Step 1 chooses an ordered semantic subset of those legal IDs and authors Beat state/continuity only. Backend rebuilds the authoring plan on import, verifies its frozen hash and exact decision-surface identity, materializes exact Script/timing spans, and never silently substitutes a partition. QC describes only the selected/materialized result. Scene and Section boundaries do not imply `new_setup`; review/edit, Prompt Bundle, and Scene Assets preserve immediate global continuity across them when permitted.

## Phase-1 entertainment-contract clarification

- Topic Ideas now receives the Project-snapshotted free-form `narration_identity` so candidate `tone` and `pov_style` remain video-specific recommendations inside the Channel personality instead of replacing it.
- Script visual density means honest visual-refresh opportunity from story/state changes plus distinct source-owned explanatory/editorial facets; it is not a proxy for narrative or emotional intensity.
- Scene Step 1 may preserve such a facet as a Beat only when the exact narration span owns a meaningful new contribution; timing, wording, emphasis, and camera variety cannot manufacture Beats.
- Scene Step 2 may use editorial graphic/text compression as one ordinary generated image when it remains semantically entailed by the locked Beat. No local renderer, asset type, or factual expansion is introduced.
- Channel DNA V3 now requires free-form `visual_identity.editorial_language`; Style Bible preserves it deterministically and Scene Step 2 requires the projected value. Runtime code does not interpret it as an enum, quota, or asset taxonomy.


## Phase-2 Channel DNA and SKILL boundary

- Channel DNA owns Zenn's durable narration identity and visual editorial language; task prompts consume those identities but do not redefine them.
- `editorial_language` remains free-form creative identity. Runtime transport is strict and deterministic, while representation-mode choice remains external-authoring judgment.
- Numeric pacing profiles remain unchanged in this phase. Competitor-observed keyframe refresh informs creative direction but is not treated as a universal shot-duration measurement.
- The external Zenn authoring SKILL is refactored separately as HOW-only guidance; it is not a backend runtime artifact and does not redefine schemas, IDs, timing authority, or publication contracts.
