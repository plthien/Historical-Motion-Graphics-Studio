# Source of Truth

## Runtime ownership

There is one active schema-driven external-authoring pipeline. The application does not execute LLM or web-research requests internally.

```text
Channel → Project → Channel DNA
→ Topic Ideas prompt/import
→ combined Script & Analysis prompt/import
→ Voice from Script only
  → approved Narration Audio + NarrationWordTimingMap
→ deterministic Style Bible
→ Visual References V2 durable working graph
  → immutable Visual Reference Set + Approval Set publication snapshots
→ backend deterministic Scene Breakdown Step-1 planning after approved Voice
→ fixed micro-Scenes + complete legal timing-atom Beat boundary grid
→ Scene Breakdown Step 1 semantic partition/Beat-state prompt/import
  → backend materializes exact Beat source/timing spans and locks the Scene Plan candidate; project Scene/Visual pointers unchanged
→ Scene Breakdown Step 2 Visual prompt/import over the exact locked Scene/Beat IDs
  → atomic draft Scene Plan 2.0 + Visual Plan 3.0 publication
→ Scene Breakdown approval
  → approved Scene Plan 2.0 + approved Visual Plan 3.0 + approved Prompt Bundle 3.0 + BeatTimingMap
→ Scene Assets V2 durable working graph
  → approved Scene Asset Manifest 4.0 publication snapshot
→ Assembly Plan 3.0 + SRT/VTT
→ Dedicated Durable Render Host
→ Final Video
```

Prompt Bundle remains the aggregate immutable backend prompt artifact, pointer, and Scene Assets authoring-lineage boundary. It is hidden from Studio navigation and is automatically compiled/approved during Scene Breakdown approval. Scene Assets V2 additionally maintains one current `scene_asset_prompt_revisions` row per beat. PromptRevision is the beat-local prompt lineage and optimistic-concurrency identity used by prompt edit/restore and generation scheduling. Clients send `expected_prompt_revision_id` together with the BeatState version; the backend derives the expected aggregate Prompt Bundle artifact from that revision instead of accepting the aggregate bundle ID as the beat-local token.

Scene Assets V2 live execution authority is `scene_asset_working_operations` + `scene_asset_operation_beats` + `scene_asset_operation_tasks`; mutable prompt/review authority is `scene_asset_prompt_revisions` + `scene_asset_beat_states` + `scene_asset_candidates`.

Visual References V2 live execution authority is `visual_reference_working_operations` + `visual_reference_operation_anchors` + `visual_reference_operation_tasks`; mutable prompt/review authority is `visual_reference_prompt_revisions` + `visual_reference_anchor_states` + `visual_reference_candidates`. Visual Reference Set and Visual Reference Approval Set are immutable publication snapshots and are never mutable working-state authority.

One shared Durable Media Workflow Host process registers the Scene Assets V2, Visual References V2, and Voice domain runners. The host owns only process-level fair scheduling and does not merge domain tables or state machines. Each domain claims due work from its own persisted `next_reconcile_at` under fenced reconcile leases. Provider/network I/O runs outside SQLite write transactions. API GET routes are read-only projections and never advance provider or workflow state.

Render is SQLite-backed but intentionally excluded from the shared media host because FFmpeg/FFprobe work is long-running and must not starve the shared domains. `render_operations` is the durable Render execution authority and the generic `jobs` row is its public status/history projection. Submit/retry persist queued operation/job state plus `next_reconcile_at` in one transaction and return without queue dispatch. The Dedicated Durable Render Host claims due operations by reconcile owner/token/expiry, renews the lease while executing, fences every execution commit to the current lease/attempt, and recovers expired interrupted work as a new attempt. Redis, RQ, the platform Backend Worker, legacy Render worker/recovery modules, and queue dispatch are not active runtime architecture.

On Windows, every Render-owned FFmpeg/FFprobe subprocess tree is assigned to an owned Windows Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Cancellation, timeout, lease loss, or host termination kills the owned process tree. Final Video publication continues to validate frozen/current Assembly/SRT/VTT/final-pointer lineage and source bytes before one guarded Project pointer compare-and-set; stale attempts cannot publish.

## External authoring authority

- Topic prompt construction derives `avoid_topics` from titles currently selected by projects in the same Channel and freezes that list for the issued prompt.
- Topic Ideas preserve the full review contract and editable Script handoff recommendations. Topic authoring receives the Project-snapshotted free-form `narration_identity` so candidate `tone` and `pov_style` remain compatible with Channel personality rather than redefining it. Every candidate also recommends one video pacing mode (`fast | medium | slow`) plus a short reason. Selecting a Topic initializes that recommendation in the Script form once; the user may override it before building the Script prompt.
- Script & Analysis is one prompt, one response, and one paired immutable publication. The active prompt is `script_and_analysis_external_v17`. Script Analysis proposes recurring canonical characters, locations, props, and recurring visual subjects: direct `occurrences` provide representative Script anchors while `presence_ranges` define the authoritative narrative spans where the same identity remains active. It exclusively authors the per-video `visual_context_catalog` and canonical entity `physical_constraints`; backend normalization assigns deterministic `ctx_###` IDs and requires exactly one context owner for every non-whitespace Script character. Before Style Bible generation, the user may remove unnecessary continuity locks without changing Script narration. The effective downstream continuity set is the current Analysis catalog minus those explicit removals.
- Project Channel DNA snapshots include free-form `narration_identity` (`voice`, `delivery`, `explanation_style`, `tone_boundaries`, `avoid`) and required free-form `visual_identity.editorial_language`. Script external authoring receives the frozen narration identity and must preserve it section-by-section; Scene Breakdown Step 2 receives the projected editorial language as visual-communication authority only. Backend/runtime code owns no narration-style taxonomy, creative profile mapping, humor ratio, representation-mode enum, meme taxonomy, or shot quota.
- Style Bible deterministically projects the complete Channel DNA visual identity, including `editorial_language`, plus only the effective downstream continuity set. It preserves canonical presence provenance for retained items and does not reinterpret narrative scope. Removed continuity items remain ordinary Script content but do not become Style Bible identity/style locks.
- Script & Analysis owns `spoken_overrides[]` and `delivery_cues[]` on every Script section. The external model supplies exact quotes and plain spoken text/cue strength only; backend resolution owns deterministic canonical `full_script` offsets and IDs. Every ASCII digit and `$`, `€`, `£`, `¥`, or `%` in narration must be covered by exactly one spoken override, and the backend must reject ambiguity rather than choosing the first match.
- Voice depends on Script, not Scene Plan. Voice and Style Bible / Visual References may proceed in parallel after Script & Analysis. Production Voice generation is selected explicitly per operation. `elevenlabs` uses the Channel-owned Professional Voice Clone presets (`economy -> eleven_flash_v2_5`, `quality_cloned_voice -> eleven_multilingual_v2`); `kokoro_local` uses an explicit voice ID from the local Kokoro runtime catalog and is not stored in the Channel Voice Profile. The final paced WAV is audio truth for every generation provider, and ElevenLabs Forced Alignment on that final paced WAV is the only timing truth. Draft Voice candidates change no Project current Voice pointer and invalidate nothing downstream. Voice approval is the Voice source-of-truth cutover and invalidation point.
- Scene Breakdown is one visible Studio stage with two external authoring steps after approved Voice exists: `scene_breakdown_scene_external_v9` and `scene_breakdown_visual_external_v4`. Step 1 uses the single active `scene_breakdown_scene_authoring_input_v2` contract. Before Step 1, backend deterministically creates context-safe fixed micro-Scenes from authored natural clause structure, approved Voice timing, locked Script Analysis `ctx_###` authority, and the resolved section-level `preferred_hold_seconds`, then computes the complete technically legal timing-atom Beat boundary grid. The same resolved hold band is exposed once in compact model-facing `sections[]` only as a post-keyframe semantic-rescan signal; it never selects a boundary, creates meaning, exposes hidden timing, or defines a target Beat count. A context transition is a mandatory planning boundary but does not itself force `new_setup`. Every legal internal boundary is supplied to Step 1 inline with the fixed source-once Scene representation; backend does not recommend, rank, score, preselect, or hide Beat partitions. Step 1 cannot create/change Scene boundaries, author timing/source spans, or select/change context; it chooses an ordered semantic subset of the supplied legal boundary IDs, including `[]` for one Beat across the whole fixed micro-Scene, and authors only `meaning_now`, `visible_state_now`, `delta_now`, and `continuity_with_previous`. The exact model-visible Step-1 decision surface is canonicalized and identity-frozen when the prompt is issued. Backend rebuilds the deterministic plan and reprojects the surface on import; internal-plan lineage and surface identity must both match, otherwise import fails closed as stale before model-output boundary validation. Backend then materializes exact frozen-Script spans/timing and locks `visual_context_ref` without publishing project Scene/Visual pointers. A Scene boundary is not a visual reset: only the first Beat of the whole plan is forced to `new_setup`, and later Scene starts may inherit continuity from the immediately previous Beat. Step 2 receives locked context and canonical physical authority, must echo `visual_context_ref` exactly, authors one final still per Beat without changing segmentation, and atomically publishes the draft Scene Plan/Visual Plan pair only after the complete Visual import validates. Canonical refs are supported by current Beat eligibility or by exact immediate carry within the relation-specific scope; immediate carry may cross a Scene boundary, never introduces a new identity, and is cleared by `new_setup`.
- Step 1's semantic choice is a Scene-level keyframe decision: each Beat is one keyframe in the fixed micro-Scene's ordered visual sequence, not a narration chunk. Word count, sentence/clause/punctuation count, timing convenience, and technically legal availability do not determine Beat count; selected keyframes must collectively make the Scene visually complete without redundant adjacent states or multiple sequential states forced into one still. A mandatory later-Scene continuity hold is allowed only when that Scene's opening source does not yet authorize any new source-owned keyframe state or delta and is never a general Beat-count reduction tool. A subtle source-owned delta remains valid regardless of magnitude; a fixed Scene boundary is not a reason to invent novelty.
- BeatTimingMap is Scene-owned and published on Scene approval. Assembly consumes BeatTimingMap and does not derive Beat timing itself.
- Section review/edit reconstructs the exact current paired ScenePlan + VisualPlan prefix before validating a non-first Section, revalidates preceding combined fragments in order, and derives carry from the actual immediately previous validated Beat. Scene/Beat source ownership, resolved spans, ordering, membership, and segmentation are immutable in ordinary review/edit; changing them requires rebuilding/repartitioning Step 1 through the same v7 legal-boundary authority. Section boundaries therefore do not reset `same_setup`, `match_subject`, `match_location`, `match_composition`, or `graphic_sequence`; `new_setup` alone clears transient immediate carry.
- The active v7 ScenePlan and VisualPlan contracts do not retain LLM-authored `scene_purpose`, `narrative_role`, or the dependent `scene_visual_strategy`; review/edit and Scene Assets identify Scenes by immutable IDs, source previews, Beat count, and duration. Step 2's semantic bridge remains `meaning_now`, `visible_state_now`, `delta_now`, and `continuity_with_previous`.
- Prompt Bundle continuity follows the same global Beat order. A non-`new_setup` Beat may require `@prev_beat` even when the immediately previous Beat belongs to another Scene or Section. Scene Assets resolves that requirement by exact source Beat identity, not by same-Scene membership.
- Prompt Composer loads the exact Script Analysis revision named by the Visual Plan and compiles separate `VISUAL CONTEXT` and ownership-scoped `PHYSICAL CONSTRAINTS` authorities. Constraints remain attached to each selected visible canonical entity. Apparent in-frame size and perspective may change, but intrinsic physical size, proportions, relative scale, graspability, anatomy, attachment, capacity, and operability may not. Current visual context outranks incompatible setting, era, evidence-status, or world cues carried by `@prev_beat`.

- Requested `target_duration_minutes` is the Script runtime authority. Before prose, external Script authoring selects and locks one `planned_narration_wpm` for every section within broad defensive bounds. The backend counts canonical narration, estimates each section from its own locked WPM, sums those section durations, and derives the Script-level `planned_average_wpm` only as an aggregate diagnostic. There is no fixed application-wide Script WPM authority and no hard creative word-count acceptance range. Voice freezes those section WPM values into provider-neutral pacing identity; provider native speed/rate controls are only bounded hints, and final TTS audio/timing is section-paced during Voice finalization before downstream Assembly.
- Channel DNA owns the exact numeric `pacing_profiles` calibration for all three per-video modes (`fast | medium | slow`), and every Project snapshots that complete calibration at creation. Topic Ideas receives the Project snapshot and recommends one of those three Channel-defined modes; the user may override the recommendation before Script authoring. The backend resolves the effective selected mode against the Project's snapshotted profiles and owns no global numeric pacing table. The resolved profile is a binding authoring/conformance target, never a beat-count quota or permission to invent semantic states.
- Script authoring plans section visual density before prose and must run the realized-density gate before freeze. Script Analysis then independently classifies the actual frozen `pacing_density` (`high | medium | low`) from genuine drawable visual-refresh opportunities: story/state changes plus distinct source-owned explanatory/editorial facets. Density is not narrative/emotional intensity and does not choose or alter section WPM. Before Scene authoring, backend resolves the selected video profile plus that frozen section density into one section-local `visual_cadence_guidance.preferred_hold_seconds` range. Raw pacing mode/profile/density and planned narration WPM are not exposed to the Scene LLM.
- Scene Step 1 timing authority is backend-owned and derived from the exact approved Narration Audio + NarrationWordTimingMap pair. Backend normalizes ordinary project-owned Voice words to timing atoms and preserves every `spoken_override` as one indivisible authored timing atom; when one project-owned Voice word intersects multiple ordered non-overlapping overrides, the backend deterministically partitions that word into ordinary and override-owned atoms using the canonical authored↔spoken mapping and character timing rather than rejecting the approved Voice state. One project-global visual-interval projection policy is shared by micro-Scene planning, post-selection timing diagnostics, and final BeatTimingMap compilation: the first Beat of the whole narration starts at sample 0; later Beats start at their own speech start; each Beat ends at the next selected Beat's visual start; and the final Beat ends at the narration master total sample count. A Section boundary never resets that policy, so non-final Section tail silence and the gap to the next Section's first spoken word belong to the final Beat before that boundary. Backend creates micro-Scenes only where an authored natural endpoint is also a safe canonical timing-atom endpoint, including semicolon and bare-newline clause boundaries, never ordinary word endpoints, and every emitted micro-Scene must meet the section's preferred minimum hold or fail closed. It computes every technically legal internal Beat boundary ID. The external Step-1 model receives each Scene source exactly once as ordered text segments with every legal `boundary_id_after`; it does not receive raw timing atoms, samples, source offsets, or timing-repair algorithms. Semantic truth controls the final boundary subset, including the valid empty subset. Backend materializes exact source spans after selection, and selected-plan QC is calculated from those realized canonical visual intervals.
- Each returned beat directly describes one image through common visual fields. No asset type, strategy, discriminator, local-render instruction, existing-asset instruction, or transition-only instruction is accepted. Editorial graphic/text compression is allowed only as a representation choice inside that same generated-image contract when it is semantically entailed by the locked Beat and adds no factual claim.
- Every external prompt requests a named UTF-8 JSON file, with pure JSON response fallback.
- Building a prompt preallocates canonical Artifact IDs and stores a pending reservation. Upload/import resolves that reservation from the JSON itself. GET prompt projections are read-only. Step-2 import requires both current upstream lineage and a matching stored prompt/schema audit (`prompt_version`, prompt hash, raw schema ID, raw schema hash); a stale audit fails closed with `SCENE_BREAKDOWN_VISUAL_PROMPT_STALE` until an explicit Step-2 Build/Rebuild allocates a fresh Visual Plan Artifact ID. Rebuilding Scene Breakdown Step 1 invalidates any pending Step-2 reservation, while rebuilding Step 2 preserves the exact locked Step-1 candidate.
- Visual Plan backend QC reports the complete-plan Beat total plus count and percentage for each representation family (`character_environment_lived`, `object_evidence_closeup`, `text_diagram_editorial`) as non-authoritative observability; it does not select, re-segment, or reject Beats.
- Validate-only endpoints never write; import endpoints rerun the full validation path.

## Visual References V2 authority

- The current text-approved Style Bible deterministically compiles an anchor inventory for characters, locations, props, and recurring visual subjects. `source_definition_hash` is canonical `sha256:<64-hex>` identity derived from the approved anchor definition.
- Each anchor has one current PromptRevision and one AnchorState CAS authority. Prompt edits create immutable revisions; upload and generation create immutable Candidate rows. Selected and approved candidates are referenced from AnchorState rather than mutating candidate history.
- Generation/regeneration/retry creates durable working operation/anchor/task rows and exact active-operation ownership before returning. Provider settings and source-definition identity are frozen for the operation.
- Stale/late provider results cannot publish unless durable operation/task/anchor ownership still matches.
- The workspace is a boundary/initial read. Exact anchor endpoints are used for targeted refresh. Execution polling is active-only and does not drive progress.
- Publication requires no active operation and requires every current Style Bible anchor to be approved against the current Style Bible/source-definition hash. Publication validates media integrity, materializes immutable per-reference artifacts plus Visual Reference Set and Approval Set, and atomically compare-and-sets the project publication pointers.
- Publication does not become working-state authority and does not recreate RQ jobs.

## Approval and Prompt Bundle authority

Scene Breakdown approval owns output aspect ratio and executes one atomic production handoff:

```text
validate current draft pair and upstream lineage
→ compile one Prompt Bundle item for every Visual Plan beat
→ compile BeatTimingMap from the approved Voice timing authority and current Scene Plan
→ validate order, coverage, prompt content, hash, options, reference bindings, and timing lineage
→ stage approved Scene Plan + approved Visual Plan + approved Prompt Bundle + approved BeatTimingMap
→ compare-and-set all four current pointers and invalidate downstream
→ commit once
```

Failure at any step leaves the draft pair current, preserves previous pointers, and removes staged artifacts.

## Scene Assets V2 durable execution authority

- Submission resolves every scheduled beat's exact current PromptRevision and writes durable operation, operation-beat, operation-task, and exact beat ownership before returning `202`. Each operation beat freezes its PromptRevision identity plus the corresponding Prompt Bundle/item/hash, prompts, generation options, reference bindings, and continuity snapshot.
- Prompt edit and restore are beat-local optimistic writes. The request carries `expected_prompt_revision_id` and `expected_beat_version`; the backend revalidates both before commit, derives the expected Prompt Bundle from the revision, publishes the next PromptRevision atomically with the immutable bundle update, and invalidates approved continuity descendants through the shared continuity engine.
- `next_reconcile_at`, `last_progress_at`, lease owner/token/expiry, task submit intent, provider task identity, and cancellation state are persisted in SQLite and survive API/UI/host restarts.
- The domain runner claims globally due Scene Asset operations; stale/expired leases are reclaimable. Provider submission, polling, download, and materialization are reconciled from persisted task state.
- Before remote submit, a task persists `submitting`, `submit_attempt_token`, and `submit_started_at`. If the process dies in the ambiguous post-provider/pre-commit window, the state is surfaced explicitly and is not silently duplicated.
- Operation/beat execution projection exposes `healthy | stalled | terminal` plus a stalled reason where applicable. This is observability derived from durable state, not a second execution authority.
- Cancellation is a local durable transition. Late provider results cannot publish unless the current operation/task/beat ownership fences still match.
- The Studio polls only the Scene Assets execution-status projection while active execution exists; it does not load or mutate the whole workspace to drive progress.
- GET execution/status routes are read-only. Provider/workflow mutation occurs only through explicit write/action endpoints or durable coordinator execution.
- One shared continuity invalidation engine owns every approved-source-changing Scene Assets mutation: candidate approval replacement, clear-approval, prompt edit, and restore-compiled. It computes the complete transitive dependent-beat closure, so `A → B → C` invalidates both B and C when A changes.
- The continuity engine plans and preflights all affected descendants before the source state is changed. Any actively generating descendant aborts the enclosing mutation atomically; review and prompt paths cannot diverge into separate direct-child invalidation behavior.

## Shared durable media workflow core

- `app.modules.media.durable_workflow` is domain-neutral. It may know operation/unit/task state, reconcile leases, submission fencing, provider-task identity, scheduling and provider-execution sequencing.
- It must not know BeatState, AnchorState, Scene Asset Manifest, Scene continuity semantics, Style Bible inventory semantics, Visual Reference publication completeness, or category/aspect-ratio rules.
- Scene Assets and Visual References keep separate physical tables and domain repositories. There is no universal durable media workflow table set.
- The shared Durable Media Workflow Host registers Scene Assets V2, Visual References V2, and Voice domain runners and provides fair process scheduling. It does not own a second persistent state machine and cannot bypass domain CAS/fencing/materialization rules.
- Render does not run in this host. Its separate coordinator/host exists solely to isolate long-lived FFmpeg/FFprobe work while retaining the same persisted-state/fenced-lease durability principles.

## Unified image and production authority

- Style Bible is a deterministic projection of the complete Channel DNA visual identity (including `editorial_language`) plus the current Script Analysis after applying the current user-reviewed continuity removals.
- Visual Reference prompts are deterministic and generated images use the configured media provider through durable V2 execution.
- Prompt Bundle has no excluded beats. Prompt item count equals Visual Plan beat count.
- Scene Assets sends every beat to the media provider and manages the same candidate review/approval lifecycle for every beat.
- Scene Asset Manifest 4.0 is an immutable publication snapshot built from the approved V2 working graph; it is not live execution storage or a beat-local optimistic-lock token.
- Local text-card/diagram renderers, existing-asset resolution, transition-only asset handling, and downstream asset classification do not exist.
- Every assembled beat requires one approved image. Transitions are timeline behavior between image clips.
- Voice segmentation, durable provider execution, audio/timing materialization, and publication are DB-backed under the shared Durable Media Workflow Host. Assembly and subtitles remain deterministic API-owned production stages. Render execution is DB-backed under the Dedicated Durable Render Host and publishes Final Video through the existing guarded immutable publication boundary.

## Artifact graph and publication

```text
Topic Ideas
-> selected Topic
-> Script + Script Analysis pair
-> Voice/Timing
-> Style Bible + Visual References V2 working state
-> Visual Reference Set / Approval Set publication snapshots
-> Scene Breakdown Step 1 locked semantic Scene/Beat candidate
-> Scene Breakdown Step 2 atomic draft Scene Plan + Visual Plan pair
-> approved Scene Plan + Visual Plan + Prompt Bundle + BeatTimingMap four-artifact set
-> Scene Assets V2 working graph
-> approved Scene Asset Manifest 4.0
-> Assembly/Subtitles
-> Final Video
```

Upstream replacement clears every dependent project pointer atomically. Consumers validate schema, bytes, current pointer, approval state, and inherited lineage.

## Active UI, routes, host, and workers

The visible Studio stages are Topic Ideas, Script & Analysis, Voice, Style Bible / Visual References, Scene Breakdown, Scene Assets, Assembly, and Render. Prompt Bundle routes and artifact reads remain active backend capabilities, but there is no Prompt Bundle Studio stage.

Creative authoring exposes only prompt/import families plus Scene Breakdown paired review/edit/read/approve. Scene Breakdown exposes its semantic `scene/*` authoring routes and locked-state `visual/*` authoring routes under the same visible Studio stage; the legacy combined Scene Breakdown prompt/import routes are not active runtime surface. Scene Assets V2, Visual References V2, and Voice execute through the shared Durable Media Workflow Host. Render executes only through the Dedicated Durable Render Host. There is no active platform RQ worker, Redis/RQ queue runtime, LLM settings, research route, creative generation job, LLM diagnostics route, or local graphic worker.

The Windows launcher starts four processes: Backend API, Durable Media Workflow Host, Dedicated Durable Render Host, and Frontend. The shared host registers Scene Assets V2, Visual References V2, and Voice. The Render host claims SQLite-scheduled Render work independently and owns Windows FFmpeg/FFprobe process trees. Durable operations resume after API/UI/host restart without request polling or Redis/RQ ownership.

## Contracts and database

- Platform schemas: `contracts/schemas/platform/`
- V4 schemas: `contracts/schemas/v4/`
- Shared schemas: `contracts/schemas/core/`, `jobs/`, `settings/`
- Static API: `contracts/openapi/youtube_ai_studio_api.openapi.yaml`
- Alembic head: `030_render_durable_sqlite`
- Scene Assets V2 runtime tables: `scene_asset_working_operations`, `scene_asset_operation_beats`, `scene_asset_operation_tasks`, `scene_asset_prompt_revisions`, `scene_asset_beat_states`, `scene_asset_candidates`
- Visual References V2 runtime tables: `visual_reference_working_operations`, `visual_reference_operation_anchors`, `visual_reference_operation_tasks`, `visual_reference_prompt_revisions`, `visual_reference_anchor_states`, `visual_reference_candidates`
- Voice durable runtime tables: `voice_generation_operations`, `voice_segment_tasks`, `voice_alignment_tasks`
- Render durable runtime authority: `render_operations` with `next_reconcile_at`, reconcile owner/token/lease expiry, and `state_revision`; `jobs` remains the public Job projection/history row.

Development data is disposable. No compatibility reader, data conversion, fallback, or backfill is active. Historical migrations and legacy table definitions may remain solely as migration history; they do not confer runtime ownership.

## Change policy

Any change conflicting with `docs/32_CODING_RULES.md` must stop before implementation. Documentation cannot be changed merely to legitimize a rule violation.
