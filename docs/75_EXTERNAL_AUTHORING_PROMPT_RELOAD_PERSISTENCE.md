# External Authoring Prompt Reload Persistence

## Scope

This release closes two Studio authoring reliability defects without changing the external-authoring architecture:

1. Topic Ideas, Script & Analysis, and full-Script Scene Breakdown prompt packages must survive a browser reload while their reservation is still pending.
2. Scene Breakdown pacing telemetry must render a deterministic global maximum even when backend global timing metrics are absent and the UI falls back to beat durations.

## Prompt reservation authority

Building an external-authoring prompt creates one pending `ExternalAuthoringReservation`. The reservation remains the server-side authority for the Artifact ID handoff until it is consumed by a successful import or replaced by an explicit rebuild.

The reservation source snapshot now persists the exact `ExternalPromptPackage` returned to the browser. This includes:

- complete `prompt_text`;
- reserved `artifact_ids`;
- `output_schema_name`;
- `prompt_version`.

Topic Ideas and Script & Analysis also expose the request-input snapshot needed to restore their editable prompt-building controls after reload. Scene Breakdown has no user-editable prompt-build form, so its request snapshot is null.

No prompt is regenerated during reload. The exact stored package is returned. Therefore reload cannot silently issue new Artifact IDs or change the prompt text because templates or runtime code changed after the original build.

## Read endpoints

The existing prompt routes now support GET in addition to POST:

- `GET /api/v3/projects/{project_id}/topic-ideas/prompt`
- `GET /api/v3/projects/{project_id}/script-analysis/prompt`
- `GET /api/v4/projects/{project_id}/scene-breakdown/prompt`

A read returns the current pending package only when the reservation still matches the current project/upstream state. Otherwise it returns `generated=false` with no package.

## Rebuild semantics

Reload is not rebuild.

The Studio restores a pending prompt automatically. The user does not need to press Build Prompt again merely to import an external JSON file.

An explicit Rebuild Prompt action keeps the existing reservation semantics: it replaces the pending reservation and issues new reserved Artifact IDs. The UI warns that JSON generated from the prior prompt will no longer import after that replacement.

This preserves the strict one-active-pending-reservation rule and prevents accidental Artifact ID churn caused only by browser reload.

## Import semantics

Import remains server-authoritative and unchanged:

- external JSON must echo the exact active reserved Artifact ID(s);
- source/upstream CAS checks still apply;
- failed imports publish no partial artifacts;
- successful import consumes the reservation;
- consumed reservations are not restored after reload.

## Scene Breakdown telemetry fix

`SceneBreakdownPanel.durationStats()` now returns `average`, `median`, `p90`, and `max` for every input, including the empty case. The UI may therefore safely evaluate:

`timingMetrics?.max_beat_duration_seconds ?? pacingStats.max`

without calling `toFixed()` on `undefined`.

This is a frontend telemetry fallback fix only. It does not alter Scene Breakdown timing policy, beat durations, or backend timing authority.
