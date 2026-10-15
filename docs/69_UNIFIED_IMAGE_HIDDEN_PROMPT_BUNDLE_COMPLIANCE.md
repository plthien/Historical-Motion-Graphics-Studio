# Unified Image + Hidden Prompt Bundle — Compliance Record

Status: active implementation authority.

## Approved behavior

- Remove `asset_strategy`, `asset_details`, asset discriminators, and all equivalent classification from the active flow.
- Every Scene Breakdown beat is one generated image design.
- Visual Plan 3.0 stores the canonical beat-level image treatment.
- Prompt Bundle 3.0 remains a separate immutable backend artifact and current project pointer.
- Prompt Bundle is hidden from Studio navigation and has no manual Studio build/edit/approve workflow.
- Scene Breakdown approval compiles and auto-approves Prompt Bundle and deterministically compiles BeatTimingMap from the approved Voice timing authority.
- Approved Scene Plan, approved Visual Plan, approved Prompt Bundle, and approved BeatTimingMap publish atomically.
- Scene Assets 3.0 sends every beat through the media provider.
- Scene Assets is section-first: the workspace derives full-section counters first, preserves one selected section across polling/refetch, and mounts only that section before applying status filters.
- Style Bible Visual References and Scene Assets expose the durable media lifecycle, including provider work, downloading, locally stored completion, failures, and the canonical polling cadence.
- MediaTask lifecycle has no wall-clock expiration: queued work waits for global provider capacity, provider-owned work keeps polling at the canonical interval, and only provider/domain-authoritative terminal events complete or fail the task. Per-request HTTP/network deadlines remain transport-level and retryable during provider status polling.
- Failed Scene Asset images have explicit per-beat retry and bulk Retry Failed semantics distinct from Generate Missing.
- Approved reference images retain the Prompt Bundle anchor ID as their semantic tag. Provider payloads use named references such as `char_001.png`, while prompts refer to `@char_001`; physical Artifact filenames are storage details and are never tag authority.
- Local graphic rendering, existing-asset binding, transition-only assets, and downstream classification are removed.
- No data migration, compatibility reader, artifact conversion, or fallback is supported.

## Failure invariants

- Prompt compilation failure leaves the draft pair current.
- Prompt or BeatTimingMap validation failure publishes no approval artifact.
- Artifact staging failure cleans all staged files.
- CAS conflict publishes no approval artifact and cleans all staged files.
- Scene Assets refuses stale, non-current, non-approved, incomplete, reordered, or hash-invalid Prompt Bundles.
- Candidate decisions remain coherent after set-current, approve, reject, clear-approval, and regenerate.
- An approved Scene Asset is generation-locked. Exact Generate, Retry, and Regenerate reject while approval is current; bulk generation/regeneration skips approved beats. Explicit Clear Approval is required before replacement generation and retains the existing downstream continuity invalidation semantics.

## UI invariants

- Visible stages: Topic Ideas, Script & Analysis, Voice, Style Bible, Scene Breakdown, Scene Assets, Assembly, Render.
- Scene Breakdown approval owns aspect ratio and visibly reports approval/compilation errors.
- The selector hydrates from the current Prompt Bundle for the same project when available.
- The JSON editor emits only fields accepted by the raw editable Scene Breakdown contract.
- Scene Assets previews do not crop non-16:9 candidates and respect backend action eligibility.
- Scene Assets exposes one-section-at-a-time navigation with per-section scene/beat/review/failure counters.
- Media progress labels provider generation, downloading, downloaded/stored-local completion, and failure states without treating provider completion as local download completion.

## Release evidence

- Contract registry and Python compile pass.
- Focused four-artifact Scene publication, Prompt Bundle, BeatTimingMap, Scene Assets, Assembly, and Render tests pass.
- Frontend static tests and TS/TSX syntax checks pass.
- Static/runtime OpenAPI parity passes.
- Clean database upgrade reaches the current Alembic head.
- Removed-runtime closure scans pass.
- `MANIFEST.json` is regenerated only after final review and verifies deterministically.
