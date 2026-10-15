# Architecture

The backend is FastAPI + SQLAlchemy + SQLite. Redis/RQ is not part of the active runtime. Immutable artifacts carry strict hashes and lineage. The React frontend consumes the same OpenAPI boundary.

## Authoring boundary

Three stages use external ChatGPT:

1. Topic Ideas — one prompt and one raw JSON import.
2. Script & Analysis — one prompt, one response, one atomic paired import.
3. Scene Breakdown — one full-Script prompt, one response, one atomic draft Scene Plan/Visual Plan import.

`app.core.external_authoring` owns neutral prompt packaging, reserved Artifact ID extraction, strict duplicate-key JSON parsing, true upstream-source checks, and validation response helpers. `app.services.external_authoring_reservations` owns the minimal pending/consumed database handoff. Neither contains creative rules.

## Scene Breakdown approval boundary

Each Scene Breakdown beat directly describes one image through common visual fields. No asset type or strategy is modeled.

Topic Ideas recommends a per-video pacing mode (`fast | medium | slow`) and the user may override that recommendation before Script prompt construction. The backend deterministically resolves the selected mode to the approved visual-pacing profile. Script authoring plans and locks one `planned_narration_wpm` per section and must realize the intended visual-state density before freeze; Script Analysis independently classifies the actual frozen `pacing_density`.

Scene Breakdown remains semantic-first. The external author chooses independently drawable beat boundaries from frozen narration before consulting numeric timing and then performs strong rhetorical/same-dominant-still merge-back. Backend `static_image_v5` runs only after semantic stabilization, estimates each beat from its owning section WPM, and reports conformance against the selected density band. Material mismatch requires semantic rescan but does not generate required beat counts, word envelopes, or fake boundaries.

Approval is one all-or-nothing operation:

```text
draft Scene Plan 2.0 + draft Visual Plan 3.0
→ deterministic all-beat prompt compilation
→ deterministic BeatTimingMap compilation from the approved Voice timing authority
→ approved Scene Plan 2.0 + approved Visual Plan 3.0 + approved Prompt Bundle 3.0 + BeatTimingMap
```

The four artifacts are staged before one compare-and-set publication. Prompt compilation, timing compilation/validation, artifact staging, or CAS failure publishes none of them and cleans every staged file.

## Voice-first production boundary

Voice depends on Script, not Scene Plan. Voice and Style Bible / Visual References may proceed in parallel after Script & Analysis. Scene Breakdown requires an approved Voice pair.

Production Voice generation is selected explicitly per operation. `elevenlabs` uses the Channel-owned Professional Voice Clone presets (`economy -> eleven_flash_v2_5`, `quality_cloned_voice -> eleven_multilingual_v2`). `kokoro_local` uses an explicit voice ID from the local Kokoro runtime catalog and does not use or update the Channel Voice Profile. Final paced WAV is audio truth for every generation provider, and ElevenLabs Forced Alignment on that final paced WAV is the only timing truth. Draft Voice candidates change no Project current Voice pointer and invalidate nothing downstream. Voice approval is the Voice source-of-truth cutover and invalidation point.

BeatTimingMap is Scene-owned and published on Scene approval. Assembly consumes BeatTimingMap and does not derive Beat timing itself.

## Durable execution boundary

Scene Assets V2, Visual References V2, and Voice keep separate SQLite working-state graphs and are process-scheduled by the shared Durable Media Workflow Host. The host provides fair bounded turns only; domain repositories and CAS/fencing remain the execution authority.

Render is deliberately separate from that shared host. Submission and retry persist a queued `RenderOperation` and `next_reconcile_at` in SQLite, then return. The Dedicated Durable Render Host claims due work under owner/token/expiry fencing, renews its lease during long execution, and is the only process allowed to run FFmpeg/FFprobe for Render. A stale lease or stale attempt cannot update progress or publish Final Video.

On Windows, Render-owned FFmpeg/FFprobe subprocesses are attached to a Windows Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Cancellation, timeout, execution-fence loss, or host termination therefore kills the owned process tree. Expired interrupted operations are recovered from durable SQLite state as a new attempt; stale attempt state is never reused.

Final Video publication remains a separate immutable authority boundary: exact current Assembly/SRT/VTT/final-pointer lineage is revalidated, output bytes are validated and staged, and the Project final-video pointer is compare-and-set before the operation/Job complete.

## Deterministic and production boundary

- Style Bible is built from Channel DNA and current Script Analysis.
- Visual Reference prompts are compiled deterministically and images are generated by the media provider through durable V2 execution.
- Prompt Bundle is compiled deterministically for every approved Visual Plan beat. It remains a backend artifact but is hidden from Studio navigation.
- Scene Assets consumes the current approved Prompt Bundle and uses one media-provider path for every beat.
- Voice, Scene-owned BeatTimingMap, Assembly, and subtitles retain their production ownership.
- Render is SQLite-scheduled by the Dedicated Durable Render Host and owns deterministic FFmpeg compilation/validation plus guarded Final Video publication.

There is no internal LLM gateway, research service, creative generation worker, platform RQ worker, Redis/RQ queue runtime, local graphic renderer, or LLM settings surface.
