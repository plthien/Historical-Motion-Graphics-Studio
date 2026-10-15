# YouTube AI Studio V3 — Start Here

This repository has one active external-authoring pipeline and no backward-compatibility requirement. Test databases and generated artifacts are disposable.

## Mandatory read order

1. `README.md`
2. `docs/00_INDEX.md`
3. `docs/32_CODING_RULES.md`
4. `docs/99_SOURCE_OF_TRUTH.md`
5. The owner document for the stage being changed
6. `docs/66_IMPLEMENTER_REVIEWER_RELEASE_PROTOCOL.md`
7. `docs/68_EXTERNAL_AUTHORING_RULE_COMPLIANCE.md`
8. `docs/69_UNIFIED_IMAGE_HIDDEN_PROMPT_BUNDLE_COMPLIANCE.md`

Before coding, record a rule-compliance snapshot covering compatibility, database changes, contract ownership, active routes/workers, and test scope.

Before any `PASS`, the required candidate CI runner must complete on the exact reviewed HEAD with **Backend review gates, Frontend and cutover review gates, and Windows Render review gates all at `success`**. A skipped, pending, cancelled, failed, timed-out, not-run, or otherwise unverified required job forbids `PASS`. If the runner cannot be completed or verified because of an external dependency, the verdict is `BLOCKED_BY_UNVERIFIED_EXTERNAL_DEPENDENCY`.

## Active architecture

```text
Channel → Project → Channel DNA
→ Topic Ideas prompt/import
→ combined Script & Analysis prompt/import
→ Voice/Timing from Script only
  → draft Voice candidates do not change current Project Voice pointers
  → Voice approval publishes approved Narration Audio + NarrationWordTimingMap
→ deterministic Style Bible
→ Visual References V2 durable working state
  → immutable Visual Reference Set + Approval Set publication snapshots
→ backend deterministic micro-Scene + legal Beat-boundary planning after approved Voice
→ Scene Breakdown Step 1 partition/semantic-state prompt/import
  → exact backend materialization + locked Scene Plan candidate
→ Scene Breakdown Step 2 Visual prompt/import
  → draft Scene Plan 2.0 + Visual Plan 3.0
→ Scene Breakdown approval atomically publishes:
  approved Scene Plan 2.0 + approved Visual Plan 3.0 + approved Prompt Bundle 3.0 + BeatTimingMap
→ Scene Assets V2 durable working state
  → approved Scene Asset Manifest 4.0 publication snapshot
→ Assembly 3.0/Subtitles
→ Render through the Dedicated Durable Render Host
→ Final Video
```

Prompt Bundle remains an immutable backend artifact and the immediate input to Scene Assets. It is not a visible Studio stage. Scene Assets V2, Visual References V2, and Voice execution are DB-backed and process-scheduled by the shared Durable Media Workflow Host. Their runtime GET endpoints are read-only projections; the Studio polls execution endpoints only while durable operations are active.

Render is also SQLite-backed but is intentionally not registered in the shared media host. Long-running FFmpeg/FFprobe execution is owned by the Dedicated Durable Render Host, which claims due `RenderOperation` rows under owner/token/expiry fencing and renewable leases. On Windows, spawned media processes are assigned to an owned Job Object with kill-on-close semantics so host loss cannot leave an unfenced FFmpeg tree running. Final Video publication remains guarded by exact Assembly/SRT/VTT lineage and project-pointer compare-and-set. Redis, RQ, and the platform Backend Worker are not active runtime dependencies.

Voice depends on Script, not Scene Plan. Voice and Style Bible / Visual References may proceed in parallel after Script & Analysis. Scene Breakdown requires an approved Voice pair. Production Voice generation is selected explicitly per operation: `elevenlabs` uses the Channel-owned Professional Voice Clone presets (`economy -> eleven_flash_v2_5`, `quality_cloned_voice -> eleven_multilingual_v2`), while `kokoro_local` uses an explicit voice ID from the local Kokoro runtime catalog and is not stored in the Channel Voice Profile. Initial provider pacing is frozen into the pacing policy/identity and raw synthesis identity so retries/reuse cannot cross provider, runtime, voice, or calibration revisions. Final paced WAV is audio truth for every generation provider, and ElevenLabs Forced Alignment on that final paced WAV is the only timing truth. Draft Voice candidates change no Project current Voice pointer and invalidate nothing downstream. Voice approval is the Voice source-of-truth cutover and invalidation point. BeatTimingMap is Scene-owned, published on Scene approval, and Assembly consumes BeatTimingMap without deriving Beat timing itself.

## Windows launch

Extract the release under a short path such as `D:\yas3`, then run `start.bat`. The launcher creates `.venv`, installs Python and frontend dependencies when missing or changed, and starts four separate processes:

- Backend API;
- Durable Media Workflow Host for Scene Assets V2 + Visual References V2 + Voice;
- Dedicated Durable Render Host for SQLite-scheduled FFmpeg/FFprobe Render execution;
- Frontend.

Python 3.12 x64 and Node.js LTS must already be installed. FFmpeg and FFprobe must be available to the Render launcher. Redis and RQ are not required or started.

## Non-negotiable rules

- Do not add compatibility readers, fallback provider execution, old-flow routes, schema dispatch, data backfills, or artifact conversion unless explicitly approved.
- The application never calls an LLM or web-search provider internally.
- Validate-only endpoints never persist artifacts; import endpoints rerun the complete validation path.
- Script and Analysis publish as one immutable pair.
- Scene Breakdown approval publishes Scene Plan, Visual Plan, Prompt Bundle, and BeatTimingMap as one guarded transaction; any failure leaves the draft pair current and removes staged files.
- Every visual beat is one generated image. There is no asset classification, local diagram/text-card renderer, existing-asset branch, or transition-only asset branch.
- Scene boundaries are backend planning-container boundaries, not visual resets. Only the first Beat of the whole plan must be `new_setup`; later Scene starts may continue the immediately previous Beat's setup/reference state.
- Scene Assets consumes the exact current approved Prompt Bundle and sends every beat to the media provider.
- Scene Assets V2 operation/beat/task state and Visual References V2 operation/anchor/task state survive API/UI/host restarts in SQLite. The shared host gives each registered domain bounded reconcile turns; each domain claims due work by `next_reconcile_at` under fenced leases, and provider I/O stays outside SQLite write transactions.
- Scene Assets and Visual References GET endpoints never advance provider/workflow state. Mutations happen only through explicit write/action paths or durable coordinator execution.
- Render submission/retry only persist and schedule SQLite state. The Dedicated Durable Render Host is the sole Render executor; every execution/progress/publication write remains fenced to the current durable lease/attempt, and Windows child-process ownership must fail closed.
- Redis/RQ queue dispatch, the platform Backend Worker, and legacy Render worker/recovery entrypoints must not be restored.
- Continuity source changes invalidate the full transitive dependent-beat closure, not only the immediate child.
- Visual Reference Set and Approval Set are immutable publication snapshots, never mutable working-state authority.
- `PASS` is forbidden unless the required runner is green for Backend review gates, Frontend and cutover review gates, and Windows Render review gates on the exact candidate HEAD; any corrective patch invalidates previous runner evidence and requires a new run.
- Regenerate `MANIFEST.json` only after the final clean source tree passes review.
