# ADR: Scene Assets V2 working state and durable execution boundary

- Status: Accepted and implemented on `scene-assets-v2-redesign`
- Scope: Scene Assets only
- Runtime schema head: `025_scene_asset_prompt_revisions`

## Decision summary

Scene Assets V2 has four distinct authorities:

1. **Approved aggregate prompt authority** — the exact current approved Prompt Bundle, plus one current PromptRevision per beat for beat-local prompt lineage/concurrency.
2. **Mutable prompt/review authority** — `scene_asset_prompt_revisions`, `scene_asset_beat_states`, and `scene_asset_candidates`.
3. **Durable execution authority** — `scene_asset_working_operations`, `scene_asset_operation_beats`, and `scene_asset_operation_tasks`.
4. **Published downstream authority** — immutable Scene Asset Manifest 4.0 snapshots.

Scene Assets V2 does not use RQ/Redis as workflow ownership. A standalone coordinator advances due durable operations from SQLite. API GET routes never perform reconciliation or provider I/O.

No compatibility reader, old-flow fallback, data-copy migration, backfill, or dual-write is part of this design.

## Working state

A beat working-state row is keyed by `(project_id, beat_id)`. `version` is the beat-local working-state optimistic concurrency token. `active_operation_beat_id` is the sole async owner for generation work on that beat.

Stable `resolution_status` stores output state only:

`not_generated | candidate_ready | approved | failed`

Transient execution state such as queued, submitting, waiting-provider, downloading, and materializing is derived from the operation graph. A live whole-project Manifest is never used as a mutation/CAS token.

## Prompt authority and frozen execution input

Prompt Bundle remains the aggregate immutable authoring artifact and project-level prompt lineage boundary. Migration 025 adds `scene_asset_prompt_revisions` so each beat also has one current beat-local prompt revision. PromptRevision is the identity used to prevent stale prompt edits/restores and to freeze exact per-beat prompt lineage into generation work.

A PromptRevision records:

- beat-local monotonic `revision_no`,
- `revision_kind = compiled | manual_edit | restore_compiled`,
- the exact source Prompt Bundle artifact ID,
- source prompt item ID and prompt hash,
- image and negative prompt text.

Only one revision may be current for a `(project_id, beat_id)`. A prompt mutation carries `expected_prompt_revision_id` plus the BeatState version; the backend derives the expected aggregate Prompt Bundle from that revision, revalidates both identities before commit, publishes the next PromptRevision atomically with the immutable bundle update, and routes approved-source changes through the shared continuity invalidation engine.

Each scheduled operation beat freezes:

- PromptRevision ID,
- Prompt Bundle artifact ID,
- prompt item ID and prompt hash,
- image and negative prompts,
- generation options + canonical hash,
- reference bindings + canonical hash,
- continuity source snapshot.

The operation freezes structural lineage and a non-secret provider execution-settings snapshot/hash. Credentials remain current secure settings; an in-flight operation cannot silently change provider execution semantics after restart.

Candidates record the exact source PromptRevision plus Prompt Bundle/item/hash lineage. Generated candidates additionally point to their operation beat/task. Uploaded candidates have no generation owner but still record current approved prompt lineage.

## Durable operation state machine

### Working operation

Active:

`queued → running → finalizing`

Terminal:

`completed | partial | failed | cancelled | superseded`

### Operation beat

`queued → running → candidate_ready`

Terminal alternatives:

`failed | cancelled`

### Operation task

Normal path:

`queued → submitting → waiting_provider → downloading → materializing → completed`

Terminal/error alternatives:

`submit_outcome_unknown | failed | cancelled`

Provider-specific states belong to operation tasks; they are not copied into aggregate operation state.

## Coordinator ownership and recovery

The standalone Scene Assets coordinator is the only automatic execution owner. It:

1. selects globally due active operations using persisted `next_reconcile_at`,
2. acquires a short DB reconciliation lease identified by owner + unique `reconcile_token` + expiry,
3. plans exactly one bounded transition,
4. performs provider/network I/O outside SQLite write transactions,
5. persists guarded results or schedules the next reconciliation,
6. releases the lease or allows expiry/reclaim on crash.

A stale coordinator cannot commit through a lease that has been replaced. Restart requires no in-memory queue reconstruction: due operation/task state remains in SQLite and a new coordinator resumes it.

The platform RQ registry does not register Scene Assets and platform stale-job recovery does not own Scene Assets. Legacy Scene Assets RQ worker/recovery modules are removed from active source.

## Provider reconciliation and ambiguous submit

Submission, provider poll, result/download, and candidate materialization are reconciled from durable operation-task state.

Before remote submit the task persists:

- `status = submitting`,
- `submit_attempt_token`,
- `submit_started_at`.

If a process dies after the provider accepted a request but before provider identity is committed, V2 represents the ambiguity explicitly. Under the current provider contract it must not silently resubmit and risk duplicate remote work. A provider-specific idempotency/lookup primitive may later narrow this window, but no compatibility fallback is allowed.

Late provider results are fenced by current operation/task/beat ownership and cannot become live candidates after cancellation/supersession or ownership change.

## Health and stalled execution projection

Execution status is a read-only projection, not a second workflow authority. Operations and operation beats expose:

`healthy | stalled | terminal`

A stalled reason may identify conditions such as stale submission, expired reconciliation lease, overdue reconciliation, or missing reconciliation schedule. The projection uses persisted timestamps/lease state and never changes execution state while being read.

The Studio polls one endpoint for execution state:

`GET /api/v4/projects/{project_id}/scene-assets/execution-status`

That GET directly projects operation/operation-beat/task/BeatState data. It does not call reconciliation and does not reload the whole Scene Assets workspace merely to advance progress.

## Read/write HTTP boundary

GET routes are side-effect free. Provider/workflow mutation occurs only through:

- explicit POST/PUT/action endpoints for user commands, or
- the standalone Scene Assets coordinator.

Reading status must never be required to make an operation progress.

## Cancellation

Cancellation is a local durable Scene Assets transition and does not depend on Redis/RQ/provider availability. Local ownership is released/fenced even if remote cancellation is unavailable. Remote cancellation may only be added if a provider capability explicitly supports it.

## Continuity invalidation

Continuity source identity is exact: source beat + approved source candidate/artifact lineage.

When an approved continuity source changes, invalidation traverses the **full transitive descendant closure**, not only direct children. For a chain `A → B → C`, changing A invalidates both B and C. Branches are traversed deterministically and traversal is cycle-safe.

There is one production continuity invalidation engine. Candidate approval replacement/clear-approval and prompt edit/restore all route through the same graph planner and applier; no mutation path may maintain a private direct-child invalidation implementation.

The shared engine plans and preflights the entire affected closure **before the source state is mutated**. If any descendant is actively generating, the enclosing source mutation fails atomically rather than leaving a partially changed source or descendant graph. A direct dependent whose stored source is already the exact newly-approved source artifact may remain valid; descendants of every actually invalidated branch are invalidated.

## Publication boundary

Scene Asset Manifest 4.0 is an immutable publication snapshot built only from the approved current V2 working graph. It is not live provider progress storage and is not a beat-local concurrency token.

Publication validates current prompt lineage, candidate lineage, image integrity, continuity authority, coverage/order, and approval eligibility, stages the immutable artifact, then publishes it under existing project ownership/CAS semantics.

## Core invariants

1. One BeatState exists per initialized Scene Asset beat.
2. Every committed beat working-state mutation monotonically increments `version`.
3. `active_operation_beat_id` is the sole async beat owner and must identify the exact same project/scene/beat.
4. Same-beat writes serialize; unrelated beats do not conflict because they share a project.
5. Stable BeatState does not duplicate transient provider progress.
6. Prompt Bundle is the aggregate immutable prompt authority; exactly one current PromptRevision is the beat-local prompt lineage/concurrency identity.
7. Every operation beat freezes the exact current PromptRevision plus Prompt Bundle/item/hash, prompt text, generation/reference, and continuity input.
8. Provider/network I/O never runs inside a SQLite write transaction.
9. Reconciliation ownership is short-lived and fenced by `reconcile_token`.
10. Operation/beat/task state survives process restart.
11. Cancelled/superseded ownership fences late provider results.
12. Every generated candidate has exactly one operation task/beat lineage.
13. Uploaded candidates have no generation-task owner.
14. Every candidate records exact source PromptRevision plus Prompt Bundle/item/hash lineage.
15. Continuity source identity must match the exact referenced beat/candidate/artifact.
16. Continuity invalidation is transitive, deterministic, cycle-safe, atomic over the affected closure, and owned by one shared engine for review and prompt mutations.
17. GET status/read routes never mutate workflow state.
18. Scene Assets is absent from the RQ registry and requires no RQ delayed self-requeue.
19. Published Manifest 4.0 is immutable downstream authority, not live runtime state.
20. V2 does not dual-write legacy Scene Assets runtime state.
21. Prompt edit/restore must reject stale `expected_prompt_revision_id` or BeatState version and must never use a project-level Prompt Bundle ID as the beat-local concurrency token.

## Schema and migration policy

Migration `024_scene_asset_working_state_v2` introduced the V2 working/execution tables without backfilling old runtime data. Migration `025_scene_asset_prompt_revisions` added beat-local PromptRevision state plus exact PromptRevision lineage on operation beats and candidates. The 025 upgrade intentionally has no data conversion/backfill path and requires empty V2 working tables under the disposable-development-data policy.

Historical migration/table definitions may remain so a clean database can replay repository history. Historical schema presence does not grant runtime ownership. Active Scene Assets behavior is determined only by the V2 models/routes/services/coordinator described here.

## Operational launch

`start.bat` starts four independent processes:

- Backend API,
- platform RQ Worker for still-worker-owned subsystems,
- Scene Assets V2 Coordinator,
- Frontend.

If the Scene Assets coordinator stops, durable operations remain in SQLite. Restarting the coordinator resumes eligible due work; UI polling is not a recovery mechanism.

## Acceptance / release gate

A Scene Assets V2 candidate is not `PASS` until:

- contract and Alembic validation pass,
- backend platform/V4 regression suite passes,
- frontend tests/type-check/build pass,
- independent review of the exact final diff finds no blocking issue,
- the required candidate runner completes on the exact final HEAD with both required jobs at `success`.

Any corrective patch invalidates prior review/runner evidence and requires a new exact-HEAD run.