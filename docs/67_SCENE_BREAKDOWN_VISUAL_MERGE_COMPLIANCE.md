# Scene Breakdown Two-Step Authoring — Rule Compliance Snapshot

Status: implementation candidate.

This document supersedes the earlier combined-call rule. Scene Breakdown remains one top-level Studio stage and one paired downstream milestone, but external authoring is intentionally split into two creative steps inside that screen.

## Approved scope

- Keep one top-level `Scene Breakdown` Studio stage. Do not restore an independent top-level Visual Planner stage.
- Backend owns deterministic micro-Scene containers, exact source ownership, legal timing-atom Beat boundaries, and approved-Voice timing projection. Step 1 (`Scene & Beat Plan`) selects an ordered semantic subset of the supplied legal boundary grid and owns Beat state/delta plus continuity/reset authority; it cannot move Scene/source boundaries.
- Step 2 (`Visual Plan`) consumes the exact locked Step-1 candidate and authors one generated-still visual for every locked Beat ID.
- Step 2 may not re-segment narration, merge/split Beats, reorder scenes/Beats, alter timing, or move semantic/reveal/reset authority.
- Preserve the canonical paired ScenePlan/VisualPlan architecture and downstream ownership boundaries. Any current VisualPlan contract changes must be propagated consistently through Prompt Composer, Scene Assets, OpenAPI/frontend types, validation, tests, and publication.
- Keep both authoring steps inside `SceneBreakdownPanel` with a visible internal Step 1 → Step 2 progression.
- Preserve the existing combined review/editor after a draft ScenePlan + VisualPlan pair has been published.

## Publication model

- Step 1 validates and materializes a locked semantic candidate in the durable external-authoring reservation only.
- Step 1 does **not** change `project.scene_plan_artifact_id` or `project.visual_plan_artifact_id`.
- The pending Step-2 reservation owns the locked Step-1 candidate, reserved Visual Plan Artifact ID, source lineage, and exact Visual prompt package.
- Step-2 GET is a read-only projection. Explicit Step-2 Build/Rebuild renders the current prompt/schema contract against the unchanged locked candidate, allocates a fresh Visual Plan Artifact ID, and replaces only the pending Step-2 reservation.
- The stored Step-2 prompt version, prompt hash, schema ID, and schema hash must match the current renderable contract. A mismatch returns `rebuild_required` and fails import closed with `SCENE_BREAKDOWN_VISUAL_PROMPT_STALE` until Step 2 is rebuilt.
- Rebuilding Step 1 invalidates Step 2; rebuilding Step 2 preserves the exact Step-1 Scene/Beat IDs, order, states, deltas, boundaries, timing, narration, and continuity metadata.
- Step 2 output must cover every locked `scene_id` and `beat_id` exactly once and in exact order.
- Only after Step 2 validation passes are canonical Scene Plan and Visual Plan artifacts staged and published through the existing atomic Scene/Visual compare-and-set pair.
- Approval remains atomic for Scene Plan + Visual Plan and continues to compile the approved Prompt Bundle / Beat Timing Map through the existing downstream path.
- Project pointers must never expose a half-pair state.

## Semantic lock invariants

Step 1 returns no final image-directing fields. Every Beat must provide:

- exact backend-materialized Beat/Scene source ownership;
- `meaning_now` — meaning authorized by the exact current Beat span;
- `visible_state_now` — concrete current state showable without later source;
- `delta_now` — new source-owned information/state or independently drawable explanatory/editorial facet versus the previous Beat;
- `continuity_with_previous` — locked continuity/reset relation.

Full-sentence context may be used to understand meaning, but source after the current Beat is unavailable authority for `meaning_now`, `visible_state_now`, and `delta_now`. A source-valid Beat may own a genuine story/state change or a distinct explanatory/editorial facet already present in its exact narration span. Timing-, wording-, emphasis-, or camera-only containers remain invalid.

## Visual authoring invariants

- Visual authoring is creative, but only inside the Step-1 semantic/timing boundary.
- `visual_concept`, `subject_action`, `environment_emphasis`, and `composition` are sibling decisions derived from the locked Beat state, not templates derived from one another.
- Stable geography may remain stable; changed environmental state must update when the locked Beat says it changed.
- Selected canonical references must be both eligible for the locked Beat and visibly present in the proposed image.
- Step 2 does not author `continuity_with_previous`; the published Visual Plan copies that relation from Step 1.
- Generic reusable prose/layout factories are not a substitute for Beat-specific visual direction.
- Step 2 may switch honestly among lived scenes, reactions, object/evidence inserts, simple diagrams, editorial graphic/text compression, comparisons, and callbacks without creating new asset types or adding facts.
- The complete Visual Plan reports non-authoritative representation-family counts and percentages, total Beat count, and complete-plan status in backend QC; this reporting never changes locked Beat selection or hard-fails a valid plan.
- Prompt Composer scopes `@prev_beat` by relation: `same_setup` carries ongoing subject/environment setup, `match_subject` carries relevant subject/object/character state, `match_location` carries location/environment state, `match_composition` carries geometry only, `graphic_sequence` carries editorial/layout grammar only, and `new_setup` carries nothing.
- Required Style Bible `editorial_language`, inherited from Channel DNA, is visual-communication authority only and cannot change Step-1 meaning.

## Mandatory platform invariants

- Provider output remains untrusted until strict schema and semantic validation complete.
- Creative style comes only from the current text-approved Style Bible and approved visual-reference authority.
- Scene Plan and Visual Plan remain a compare-and-set pair for all project/downstream readers.
- Prompt Composer and later production stages keep their immediate approved-input and public artifact contracts.
- Backend may derive IDs, order, spans, timing, marker facts, reference eligibility/bindings, lineage and continuity groups. It must not invent final visual direction.
- Pending Step-2 authoring becomes stale if Script, Analysis, Style Bible, visual-reference approval, approved Voice timing, previous paired plan pointers, or Channel DNA change.
- No real-provider readiness claim without a real-provider test.

## Failure paths in scope

- malformed Step-1 semantic output;
- exact-source coverage/order or approved-Voice timing failure;
- timing-/wording-/camera-only Beat containers without distinct source-owned state, information, or explanatory/editorial delta;
- stale Step-1 reservation;
- stale Step-2 prompt/schema reservation;
- malformed Step-2 Visual output;
- missing, duplicated, reordered, or foreign scene/Beat IDs in Step 2;
- unsupported/mis-categorized canonical reference selection;
- Step-2 attempt to change continuity/reset or semantic authority;
- stale upstream/CAS conflict between Step 1 and Step 2;
- paired review/edit/approval after publication;
- staged-artifact cleanup on losing atomic publication.

## Test scope

- Step-1 and Step-2 strict contracts and prompt ownership;
- Step 1 leaves current Scene/Visual project pointers unchanged;
- reload restores the exact pending prompt/reserved Artifact ID for the active internal step;
- explicit Step-2 Build/Rebuild allocates a fresh Visual Artifact ID without changing the locked Step-1 candidate;
- GET detects stale Step-2 prompt/schema audit without mutating the reservation, and import fails closed before output-schema validation;
- complete Visual Plan QC reports family counts/percentages and total Beat count;
- Step 2 is unavailable before a Step-1 candidate has been locked;
- Step-2 scene/Beat identity and order must match Step 1 exactly;
- final Step-2 import atomically publishes one canonical ScenePlan + VisualPlan pair;
- no independent `visual` Studio stage / VisualPlannerPanel returns;
- combined review/editor and paired approval remain functional;
- downstream Prompt Composer / Scene Assets compatibility;
- static/runtime OpenAPI parity;
- deterministic manifest/archive audit.
