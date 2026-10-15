# ADR — Durable Voice Workflow and Provider Adapter Architecture

**Status:** Historical durable-workflow ADR; active Voice provider, authoring, timing, and Scene-lineage contract is superseded by `docs/VOICE_FIRST_ELEVENLABS_IMPLEMENTATION_SPEC.md`
**Baseline:** `f53f718e59924e2abb0c4865abcc74ad2a141a44`  
**Original implementation branch:** `durable-media-workflow`
**Migration baseline:** `027_visual_reference_candidate_task_fence`  
**Next migration:** `028_durable_voice_workflow`

This ADR remains the historical record for the DB-backed durable Voice workflow cutover from RQ. The active Voice-first architecture now depends on Script rather than Scene Plan, uses Channel-owned ElevenLabs Professional Voice Clone presets, treats final paced WAV plus Forced Alignment as Voice timing truth, and publishes BeatTimingMap from Scene approval as specified in `docs/VOICE_FIRST_ELEVENLABS_IMPLEMENTATION_SPEC.md`.

## Context

At the time this ADR was accepted, Voice was the remaining production media stage still owned by Redis/RQ. Scene Assets V2 and Visual References V2 already proved the repository's DB-backed durable execution model and shared the Durable Media Workflow Host. The implemented decision moves Voice into that host without importing image-domain semantics or creating a parallel Voice V2 runtime.

The current Voice implementation already owns useful domain state (`VoiceGenerationOperation`, `VoiceSegmentTask`), exact narration segmentation, immutable segment/audio/timing artifacts, provider identity/settings hashes, approval, and project publication pointers. These remain authoritative domain concepts.

## Rule-compliance snapshot

- Compatibility/backfill: none. No legacy reader, schema dispatch, data-copy migration, rollback transform, or fallback execution path will be added.
- Database: extend the existing Voice tables with migration `028_durable_voice_workflow`; do not create generic physical workflow tables or a Voice operation-unit table.
- Contract ownership: `app.modules.media.durable_workflow` remains neutral; Voice-specific provider lifecycle, normalization, publication, and review remain under `app.modules.v4`.
- Active execution routes: current public Voice routes remain; successful submission means durable DB work is committed and discoverable, never that an RQ message exists.
- Worker ownership after cutover: RQ owns Render only. Voice is removed from RQ registration, enqueue, and Redis startup recovery.
- Test scope: migration, repository leases/fences, provider contracts, immediate/blocking/async lifecycle, ambiguous submission, retry, restart, cancel, late-result fencing, finalization, host fairness, Job projection, RQ closure, Voice API/UI, Assembly handoff, OpenAPI/contracts, full backend/frontend regression.

## Decision 1 — execution authority

`VoiceGenerationOperation` plus `VoiceSegmentTask` are the sole execution authority. `Job` remains a public API/status/progress projection because current routes and contracts expose job identity, but durable scheduling never depends on Redis, RQ, or Job queue state.

Directionality is one-way:

```text
Voice durable state -> Job projection
```

Never:

```text
Job/RQ state -> durable scheduling decision
```

## Decision 2 — process topology

Exactly four top-level processes remain:

```text
Backend API
RQ Worker                         # Render only
Durable Media Workflow Host       # Scene Assets + Visual References + Voice
Frontend
```

`VoiceProviderExecutionPool` is an in-process bounded pool owned by the durable host and is not a fifth process.

## Decision 3 — shared durable boundary

Voice reuses:

- `DurableMediaCoordinator`;
- `DurableWorkflowRepository` snapshots;
- persisted `next_reconcile_at` due scheduling;
- owner/token/expiry reconcile leases;
- fenced writes;
- stale-submitting semantics;
- shared operation/unit/task states where applicable;
- shared host fairness.

Voice is not forced through `MediaBatch`, `MediaTask`, image provider execution, or candidate semantics.

## Decision 4 — Operation / Unit / Task mapping

No Voice operation-unit table is created.

```text
Operation = VoiceGenerationOperation
Unit      = logical projection of VoiceSegmentTask
Task      = VoiceSegmentTask
```

The same row and ID are used for Unit and Task:

```text
operation_unit_id = voice_segment_task_id
operation_task_id = voice_segment_task_id
```

`list_task_snapshots_for_unit()` returns a singleton list.

Neutral Unit projection:

```text
queued                                      -> queued
submitting / waiting_provider /
materializing / submit_outcome_unknown      -> running
completed                                   -> candidate_ready
failed                                      -> failed
cancelled                                   -> cancelled
```

## Decision 5 — operation states

Durable Voice execution uses operation states equivalent to:

```text
queued -> running -> finalizing -> completed
                    \-> failed
                    \-> cancelled
                    \-> superseded
```

`partial` may remain only as historical/reporting state. It must never represent undiscoverable unfinished work. Retryable unfinished work remains scanner-discoverable.

## Decision 6 — segment task states

Voice uses the shared durable task lifecycle:

```text
queued
  -> submitting
      -> waiting_provider
          -> materializing
              -> completed
      -> submit_outcome_unknown
```

Terminal/error edges are `failed` and `cancelled`.

A retryable failure with attempts remaining returns the task to `queued` and persists `next_reconcile_at` backoff. No Redis re-enqueue is involved.

## Decision 7 — ambiguous submission

`submit_outcome_unknown` is mandatory. A stale `submitting` task is never blindly replayed when provider acceptance may have crossed an external side-effect boundary.

Recovery is capability-driven:

- deterministic idempotency may permit safe replay;
- provider lookup may reconcile an accepted submission;
- a durably stored provider handle may resume polling;
- otherwise transition to `submit_outcome_unknown` and expose an explicit problem state.

Internal publication exactly-once safety is distinct from external provider billing/execution exactly-once behavior. Providers without idempotency/lookup may still have an unavoidable ambiguous external-execution window, but stale/duplicate results cannot publish current output.

## Decision 8 — provider-neutral adapter architecture

The durable core only knows `provider_key` plus frozen provider/settings identity. Provider-name branching is forbidden in durable orchestration.

Voice introduces normalized contracts for:

- provider capabilities;
- readiness;
- synthesis request;
- provider handle;
- submission result;
- poll result;
- cancel result;
- execution result;
- execution control;
- provider registry.

Provider-specific readiness/options live in adapters.

## Decision 9 — blocking providers

Blocking/synchronous provider calls run through `VoiceProviderExecutionPool` so `DurableMediaWorkflowHost.run_once()` stays bounded and Scene Assets/Visual References cannot be starved by synthesis duration.

The pool is non-authoritative. Durable DB state records at minimum:

- attempt number;
- execution attempt token;
- execution start time;
- provider status;
- `next_reconcile_at`.

A Future, thread, subprocess handle, or in-memory provider handle is never required to decide the next durable action after restart.

## Decision 10 — execution control and Kokoro cancellation

Blocking adapters receive provider-neutral `VoiceExecutionControl` exposing at minimum:

```text
execution_attempt_token
cancellation_requested()
deadline_reached()
```

Kokoro keeps cooperative cancellation by polling this control while its subprocess runs. Correctness is DB-first: cancellation durably fences the attempt; provider cancellation/termination is best effort.

## Decision 11 — late-result fencing

Every blocking completion envelope carries:

```text
operation_id
segment_task_id
execution_attempt_token
provider_key
completion status
```

Before materialization:

```text
result.execution_attempt_token == current_task.execution_attempt_token
```

must hold. Otherwise the result is stale, temporary resources are cleaned, and current state/publication is untouched.

## Decision 12 — async provider handles

Async adapters may return a durable provider handle. Generic provider identity uses existing fields (`provider_task_id`, `provider_status`, `provider_submitted_at`, result metadata) unless a generic `provider_handle_json` is required. Provider-specific columns are forbidden.

## Decision 13 — idempotency

Each attempt derives a stable idempotency identity from operation, segment, attempt, provider identity, and frozen generation settings. Adapters use provider-supported idempotency only when capabilities declare support. The system guarantees fenced publication, not universal exactly-once provider billing.

## Decision 14 — retry/backoff

Provider errors normalize to:

```text
retryable
non_retryable
cancelled
ambiguous_submission
```

Retryable work persists attempt failure and schedules a future `next_reconcile_at`. Maximum attempts are explicit. No request polling, RQ enqueue, or worker restart drives retry.

## Decision 15 — cancellation

Cancellation persists `cancel_requested_at` first. After that:

- no new attempts start;
- queued tasks become cancelled;
- the current attempt is fenced;
- adapter cancellation is best effort when supported;
- late completion cannot materialize;
- reconciliation drives the operation to terminal `cancelled`.

## Decision 16 — normalization

Existing Voice audio/timing behavior is extracted, not reimplemented.

`VoiceAudioNormalizer` owns provider audio -> canonical segment audio.

`VoiceTimingNormalizer` owns provider word/character timing and deterministic fallback into provider-neutral timing records consumed by the existing NarrationTimingMap builder/validators.

Script V3 `script_sections[].planned_narration_wpm` is the Voice pacing authority. Provider-native speed/rate fields are implementation hints only and are never treated as whole-video WPM. Durable operation identity freezes a provider-neutral Script section pacing plan containing target WPM, section word count, target duration, native hint, clamp state, policy version, and pacing identity hash.

## Decision 17 — finalization/publication

Finalization remains Voice-domain authority:

```text
ordered validated segment artifacts
-> section-level pacing correction
-> full narration assembly
-> timing map
-> immutable narration audio + timing artifacts
-> guarded/CAS project publication
```

Restart after all segments are complete resumes finalization without resynthesis. A stale finalizer cannot replace a newer current pair.

Finalization groups synthesized segment outputs by Script section, computes one tempo factor per section, applies that factor consistently to all segment audio in that section, and transforms provider timing by the same factor before canonical timing normalization. The final narration audio/timing map, not provider raw duration, is authoritative for downstream Assembly.

Before applying any section FFmpeg pacing, finalization measures all sections and persists the complete remediation report when one or more sections exceed provider-neutral pacing bounds. `VOICE_PACING_REGENERATION_REQUIRED` is a terminal durable Voice failure that preserves completed raw segment artifacts, exposes section remediation rows to the workspace, and requires an explicit `regenerate_section` request. The client supplies only lineage/report IDs; provider-owned feedback computes the section pacing directive, and every segment in the target Script section is resynthesized under that same directive.

## Decision 18 — provider readiness/settings

Core readiness owns current Script/approved Scene Plan lineage, active-operation conflicts, and structural profile/voice selection. Adapter readiness owns executable/credentials/model/path/provider capability compatibility and provider options.

General settings use a normalized `provider_key` and optional `provider_options`; provider-specific option names do not become durable-core fields.

## Decision 19 — active status polling

Workspace GET remains a read-only boundary load. While Voice generation is active, the frontend polls the targeted Voice job/status projection. The status projection exposes progress/counts/current segment/provider action/retry/cancel/problem state. When terminal, the frontend refreshes the full workspace.

## Decision 20 — explicit shutdown

The durable host gains an explicit optional runner shutdown lifecycle. Host shutdown stops new Voice provider dispatch, signals stop/cancellation, shuts down `VoiceProviderExecutionPool` with bounded grace, and cleans resources. Scene Assets and Visual References remain compatible.

## Decision 21 — atomic RQ cutover

Voice RQ enqueue/handler/startup recovery are removed only after the durable path passes focused failure-first tests. There is no long-lived dual runtime. Render remains registered on RQ.

## Decision 22 - ElevenLabs Multilingual v2 Phase 1

ElevenLabs is integrated as one provider adapter behind `VoiceProviderRegistry`.
Model-specific behavior belongs to provider-owned model policies; Phase 1
supports `eleven_multilingual_v2` only, while Eleven v3 remains deferred to a
future policy addition. The adapter uses authenticated `GET /v2/voices` account
catalog discovery, live `GET /v1/models` readiness, and
`POST /v1/text-to-speech/{voice_id}/with-timestamps` with `pcm_24000` wire
output. The provider returns a temporary 24 kHz mono PCM S16LE WAV; the existing
normalizer remains the authority for canonical 48 kHz Voice artifacts.

Continuity hints are generic `previous_text` and `next_text` fields on the
internal synthesis request and are derived only from the operation's frozen
segmentation in `operation.request_json`. The adapter consumes those fields for
Multilingual v2 and does not send `language_code`, request-id stitching, audio
tags, cloning, streaming, or Shared Voice Library calls.

ElevenLabs credentials use a dedicated server-only `voice_provider_api_keys`
settings row rather than `tts_settings_v3`, provider options, project rows, or
durable operation/result payloads. Effective credential precedence is the
Settings-stored ElevenLabs key, then `ELEVENLABS_API_KEY` as an environment
fallback, then not configured. The adapter resolves that effective credential
at the action boundary, so a Settings save is visible to both API readiness and
the long-lived Durable Media Workflow Host without a Python process restart.
Clearing the stored override explicitly falls back to the environment value
when present. Credential GET responses expose only configured/source status and
never return the raw key. Settings Test Connection performs only the non-billable
`GET /v1/models` and `GET /v2/voices` calls.

Durable execution identity stores only credential/base URL fingerprints plus
model policy identity; raw API keys, account catalogs, request bodies, and raw
response headers are not stored in TTS settings or durable result metadata.
Credential replacement changes the execution fingerprint. The generic action
controller identity fence validates the frozen provider execution identity before
each provider action. ElevenLabs then resolves one effective credential at the
frozen execution action boundary, validates its fingerprint against that frozen
identity, and uses that same action-bound credential for account preflight and
billable synthesis I/O. Effective credentials remain dynamic between actions:
replacing the Settings credential affects new readiness/catalog/Test Connection
operations and new Voice generation operations immediately, but cannot silently
change the account authority of an already-frozen provider action. Successful
synthesis may persist sanitized `request-id`, `x-trace-id`, and `character-cost`.

Classic synchronous ElevenLabs TTS is a cloud side effect without a Phase 1
submission lookup handle. Connect-stage failures may retry. If no HTTP response
is proven, write/read/protocol failures after a side-effecting POST remain an
ambiguous submission outcome. Once an HTTP error status is received, that status
is authoritative even if its diagnostic body cannot be read; status mapping
determines retryability and the failure is not reclassified as ambiguous. A 2xx
synthesis response whose body/audio/timing is malformed remains non-retryable
because provider execution may already have completed. Durable Voice parks truly
ambiguous attempts in `submit_outcome_unknown` and never silently replays them.
Kokoro remains replay-safe because it has no remote submission boundary.

Fresh supported Voice bootstrap registers both Local Kokoro and ElevenLabs
Multilingual v2 profiles. `TTS_PROVIDER_MODE` chooses the initial active profile
only. Existing persisted disposable `tts_settings_v3` rows are deliberately not
mutated to add new profiles; development settings/database state may be recreated
instead of adding compatibility or backfill behavior.

## Invariants

1. No in-memory thread, Future, subprocess handle, Redis job, or RQ message is required to decide what unfinished Voice work does next.
2. Provider I/O never requires a long SQLite write transaction.
3. A stale, cancelled, superseded, or ambiguous provider result cannot publish the current narration audio/timing pair.
4. Changing provider does not redesign durable Voice state, Narration Review Workspace, canonical Voice artifacts, or Assembly.
5. Ambiguous external submission is never silently replayed unless capability + durable identity prove replay safe.
6. Changing Script section pacing changes durable Voice identity and prevents reuse of segment outputs as valid for materially different pacing intent.
7. Provider-native speed/rate controls are bounded hints; final Voice WPM correctness is enforced by provider-neutral section pacing during finalization.
8. Non-retryable deterministic finalization failures terminalize the Voice operation through the durable Voice domain boundary. They are not deferred indefinitely by the coordinator runner.
9. Retryability classification is authoritative: retryable finalization failures may be deferred by policy; non-retryable Voice-domain failures become terminal failed operations/jobs.
10. Completed raw provider segment tasks and immutable raw segment artifacts survive downstream pacing/finalizer failure.
11. Provider-relative speed is a provider-owned mapping from Script WPM product intent into native provider controls. Generic durable orchestration and finalization do not branch on Kokoro, ElevenLabs, or future provider names for pacing.
12. Provider-native pacing policy version, control model, bounds, clamp state, and per-section directive value participate in raw synthesis identity, so incompatible raw synthesis is not reused.
13. Final provider-neutral section pacing remains the correctness and quality boundary. A pacing quality-fence failure may fail the operation, but it must never trigger an automatic paid cloud TTS tuning/resynthesis loop.
14. Long-running deterministic finalization renews the current reconcile lease at section/FFmpeg progress boundaries. Renewal preserves the same owner/token and never weakens expiry fencing.
15. Final Voice publication rechecks the current reconcile token and unexpired lease inside the same SQLite transaction immediately before the terminal commit. A stale finalizer must roll back staged publication rather than publish after ownership loss.
16. `VOICE_OPERATION_CANCELLED` is a terminal cancellation outcome, not a non-retryable failure to be reclassified as `failed`; runner handling preserves the already-committed cancelled operation/job state and only releases residual lease ownership.

## Implementation GO

All pre-implementation Go/No-Go questions are answered by this ADR and the frozen implementation plan. Implementation may proceed.

## Provider execution watchdog and cancellation ownership

The production Voice host treats provider execution ownership as bounded runtime state, not as durable authority:

17. A persisted `execution_started_at` plus `TTS_PROVIDER_TIMEOUT_SECONDS` is a host-level hard watchdog boundary. A provider Future may not be trusted as `running` forever merely because its thread has not returned.
18. Providers that can own an interruptible local execution resource declare `supports_execution_interrupt` and must interrupt only the exact `(segment_task_id, execution_attempt_token)` attempt. Durable core code remains provider-neutral.
19. Kokoro owns its synthesis subprocess by exact execution-attempt token. Cancellation and hard timeout may terminate that exact subprocess out-of-band; a stale cancellation cannot kill a newer attempt.
20. Voice cancellation persists durable cancellation first, then interrupts/retires the owned local submit action. It must not require a spare slot in the same saturated provider pool.
21. Best-effort remote cancellation with a durable provider handle is tracked and reaped. Completed cancel Futures must not permanently consume provider capacity.
22. Provider stdout is never required for Voice orchestration. Kokoro discards stdout and captures bounded stderr in a file-backed stream so Windows pipe reads cannot block the execution-control loop.
23. Runtime logs must expose operation/task/segment/provider lifecycle transitions and timeout/cancellation outcomes without narration text, credentials, or raw provider payloads.
