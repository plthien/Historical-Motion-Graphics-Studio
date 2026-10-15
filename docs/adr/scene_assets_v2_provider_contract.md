# Scene Assets V2 provider capability audit

- Scope: current `GLabsWebhookClient` / `MediaTaskService` behavior at the redesign baseline
- Purpose: define what the durable coordinator may safely assume

## Current capabilities found in source

The current GLabs client exposes these provider operations:

- health check,
- authenticated task-list/auth check,
- image submission,
- task-status polling,
- completed-result lookup,
- bounded local result download.

Image submission is `POST api/image/generate` and returns a provider `task_id` plus `pending` or `running` status. Polling is `GET api/status/{task_id}`. Result lookup is `GET api/result/{task_id}`. Result downloads are restricted to local HTTP(S) URLs and validated for image type and bounded size.

The Scene Asset media payload currently contains prompt, model, aspect ratio, reference images, and upscale settings. No client idempotency key is sent by the current adapter.

## Capabilities not exposed by the current client

The current client exposes no:

- provider cancel method,
- submit idempotency key,
- lookup by client-generated submission token.

Therefore V2 must not rely on any of those capabilities.

## Required coordinator semantics

Every Scene Asset provider command must be derived from the exact frozen approved Prompt Bundle item snapshot stored on the owning operation beat. Provider reconciliation must not reload a newer Prompt Bundle revision and silently change in-flight prompt, generation-option, or reference-binding inputs. The owning operation also freezes a canonical non-secret provider execution-settings snapshot; coordinator restart may resolve current credentials, but it must not silently switch provider/model execution semantics.

### Submission

Before network submit, persist:

- operation task status `submitting`,
- `submit_attempt_token`,
- `submit_started_at`.

After provider acceptance, persist the provider-owned task identity through the existing MediaTask state before considering submission recoverable.

If the process restarts with an operation task still in `submitting` and no recoverable provider task identity, V2 must transition to `submit_outcome_unknown`. It must not automatically submit again, because the original request may already have been accepted remotely.

A later provider adapter may allow automatic recovery only if it provides an idempotency primitive or reliable lookup by the persisted submit token.

### Polling

Provider statuses accepted by the current client are:

- pending,
- running,
- completed,
- failed.

Transient control-plane failures remain retryable. Provider terminal failure is locally terminal for the corresponding operation task.

### Download

Completed provider results must be downloaded outside any Scene Asset working-state transaction. The existing MediaTask download ownership/Artifact write path may be reused initially, but the Scene Asset coordinator must only materialize the downloaded artifact into V2 working state after late-result fencing passes.

### Cancellation

Local cancellation is authoritative for Scene Assets V2. Because the current provider client has no cancel method, remote work may continue after local cancellation.

Late completion after cancellation must be ignored by V2 working state. The downloaded/provider result may be cleaned up separately, but it cannot create or select a live Scene Asset candidate for the cancelled operation.

## Future adapter extension

If a provider later supports these primitives, expose them explicitly rather than inferring support:

```python
supports_idempotent_submit: bool
supports_remote_cancel: bool

submit_image(..., idempotency_key: str | None = None)
cancel_task(external_task_id: str) -> None
lookup_by_idempotency_key(key: str) -> ProviderTask | None
```

Coordinator behavior must branch on declared capability, not provider name or version guesses.
