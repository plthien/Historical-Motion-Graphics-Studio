# ADR — Shared Durable Media Workflow Core

Status: accepted for staged implementation on `durable-media-workflow`.

Baseline: `scene-assets-v2-redesign` commit `68d0613616adc428a7b8f48ebfae1b3648afe46d`.

## Decision

Extract only the durable media execution mechanics already proven by Scene Assets V2 into a shared core. Scene Assets remains the first consumer and must preserve its existing database, REST, OpenAPI, state, continuity, publication, and frontend contracts while the extraction is performed. Visual References may become a second consumer only after the Scene Assets coordinator and provider-execution regression gates are green.

The shared core uses neutral workflow terms:

- operation
- unit
- task
- reconcile lease
- submit fence

The shared core must not know Scene Asset or Visual Reference domain identity such as `scene_id`, `beat_id`, `anchor_type`, `anchor_id`, continuity, Style Bible inventory, or Manifest publication.

## Shared responsibilities

The reusable durable workflow layer may own:

- due-operation scanning contracts;
- reconcile lease acquisition, renewal, release, and fencing;
- operation/unit/task execution-state definitions;
- reconcile timing helpers;
- deterministic next-action scheduling;
- submit-attempt fencing and stale-submit recovery;
- provider submit/poll/download lifecycle orchestration;
- provider-capacity mechanics;
- generic execution aggregation.

Provider transport remains `MediaTask`/`MediaBatch`; it is not the workflow authority.

## Domain responsibilities

Scene Assets keeps ownership of:

- `SceneAssetBeatState` CAS and beat ownership;
- Scene Asset prompt revisions and candidates;
- continuity dependency semantics and invalidation;
- scene/section semantics;
- Scene Asset Manifest publication;
- Scene Asset prompt composition and reference binding rules.

Visual References V2 will own, in domain-specific tables:

- `VisualReferenceAnchorState` CAS;
- Visual Reference prompt revisions and candidates;
- anchor definition/source hash rules;
- required-anchor approval completeness;
- immutable Visual Reference Set / Approval Set publication.

## Database decision

Do not create generic physical workflow/domain tables during this migration.

Scene Assets keeps its existing tables unchanged. Visual References V2 will receive its own working-state tables. Repository adapters translate domain models to neutral workflow snapshots/interfaces.

This explicitly rejects a shared `GenericAssetState`, shared candidate table, shared prompt-revision table, or a migration from `SceneAssetOperationBeat` to a generic persisted unit table.

## Scene Assets compatibility contract

Until Visual References cutover is complete, every extraction phase must preserve all of the following for Scene Assets:

1. **Database:** no Scene Asset schema migration, table rename, column rename, or data backfill caused by the shared-core extraction.
2. **REST/OpenAPI:** no breaking route, request, response, enum, error-code, or DTO changes.
3. **Execution states:** operation, operation-beat, and task transition semantics remain byte-for-byte equivalent in meaning.
4. **CAS/ownership:** BeatState version CAS and active operation-beat ownership remain authoritative.
5. **Leases/fencing:** reconcile lease and submit-attempt fencing retain current behavior and failure semantics.
6. **Continuity:** continuity graph/readiness/invalidation remains Scene Asset domain code and is not genericized.
7. **Publication:** Scene Asset Manifest preconditions, immutable publication, and project-pointer behavior remain unchanged.
8. **Provider ownership:** Scene Assets remains a DB-backed durable workflow and must not return to RQ ownership.
9. **Frontend:** workspace/execution DTOs and targeted-refresh behavior remain unchanged during backend extraction.
10. **Import compatibility:** public/current Scene Asset service class import paths remain available while internals move to shared code.

## Current execution-state baseline

Operation states:

- active: `queued`, `running`, `finalizing`
- terminal: `completed`, `partial`, `failed`, `cancelled`, `superseded`

Unit / Scene Asset operation-beat states:

- active: `queued`, `running`
- terminal: `candidate_ready`, `failed`, `cancelled`

Task states:

- active: `queued`, `submitting`, `waiting_provider`, `downloading`, `materializing`
- terminal: `submit_outcome_unknown`, `completed`, `failed`, `cancelled`

Transition definitions are frozen by parity tests before Scene Assets imports them from the shared module.

## Method inventory

### Generic execution mechanics

Current Scene Asset methods that are candidates for shared ownership include:

- due-operation scan;
- reconcile lease acquire/renew/release/fence;
- reconcile timing helpers;
- deterministic task/action selection;
- prepare-submission fencing;
- stale `submitting` recovery;
- provider submit/poll/download advancement;
- provider capacity release/deferral;
- generic operation/unit/task terminal aggregation.

### Scene Asset-specific mechanics

The following remain behind a Scene Asset driver/adapter:

- BeatState CAS and busy checks;
- mapping a workflow unit to `scene_id` / `beat_id`;
- prompt/reference/continuity ownership validation;
- Scene Asset candidate materialization;
- continuity source validation/invalidation;
- Scene Asset publication.

## Staged implementation gates

### Phase A — freeze

- freeze this ADR and compatibility contract;
- baseline current tests and source ownership;
- inventory generic vs domain mechanics.

### Phase B — shared primitives

- add neutral contracts, state definitions, timing helpers, repository protocol, and Scene Asset repository adapter;
- do not switch Scene Assets coordinator/provider execution yet;
- add parity tests against the existing Scene Asset definitions;
- full regression must pass.

### Phase C — shared coordinator

- Scene Assets becomes the first live consumer of the generic coordinator;
- preserve compatibility wrappers/import paths;
- full coordinator/concurrency/cancel/recovery regression must pass.

### Phase D — shared provider executor

- extract common image-provider lifecycle behind domain hooks;
- Scene Assets becomes the first live consumer;
- crash-after-submit, lease-loss, cancellation, retry, provider-capacity, and submit-outcome-unknown tests must pass.

**Visual References V2 work must not begin before Phase D is green.**

## Non-goals

- no redesign of Scene Assets continuity;
- no Scene Asset schema cleanup or renaming while extracting the core;
- no dual-write between old and new Scene Asset workflows;
- no generic all-purpose job framework;
- no Visual Reference RQ cutover in Phases A–D;
- no cleanup of legacy Visual Reference runtime before its V2 hard cutover.

## Verification baseline

Required gates are the repository's existing full regression plus focused shared-core parity tests. In particular, the existing Scene Assets suites for working-state schema/runtime, coordinator submission, provider execution, scheduler repair, provider capacity, execution architecture, OpenAPI parity, frontend cutover/behavioral tests, and refresh strategy remain authoritative regression evidence.
