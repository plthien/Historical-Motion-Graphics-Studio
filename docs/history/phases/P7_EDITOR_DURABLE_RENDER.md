# P7 — Usable motion timeline editor and fenced durable Node rendering

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no local LLM, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Precondition
P6 actual first history MP4 passed visual/audio/source verification. The new history product has only CompiledTimeline and approved immutable source artifacts; zero old assembly/beat render authority.

## New owner files and source
- `src/features/history/motion/{MotionTimeline,LayerTrack,SetupTrack,AudioTrack,LayerInspector,AssetPicker,CueEditor,PreviewControls}.tsx` plus shared @remotion/player Composition.
- `app/history/timeline/{revision,change_validator,dependencies,invalidation}.py`, `app/history/render/{operations,repository,coordinator,executor,process_guard,download,quality}.py`, `app/history/api/{timeline_editor,render}.py`.
- `runtime/remotion/src/renderer/worker.ts` or bounded Node CLI receiving one frozen JSON descriptor, no arbitrary command input.
- Fresh database `render_operations`, `render_attempts`, revisions and editable candidate records; one migration added to HISTORY chain, not YTB Alembic 001..031.
- Port (not import legacy runtime) audited Windows Job Object/lease fencing and media diagnostics patterns. No old `render_graph.py` or YTB assembly plan support.

## UI API/behavior details
- GET `.../timeline/current`, POST `.../timeline/revisions`, PATCH `.../timeline/revisions/{id}/layers/{layer_id}`, POST `.../timeline/revisions/{id}/validate`, POST `.../timeline/revisions/{id}/approve`; GET `.../timeline/dependencies`.
- GET `.../render/readiness`, POST `.../render/jobs`, GET `.../render/jobs/{job_id}`, POST `.../render/jobs/{job_id}/cancel`, POST `.../render/jobs/{job_id}/retry`, GET `.../render/video`, GET `.../render/qc`.
- Timeline UI: separate setup track, image/map/text/route/stamp/diagram layer tracks, audio/caption tracks, zoom/scrub, active-layer selection, keyframe/cue offsets and crop inspector; undo/redo only within unapproved editor draft.
- Editing asset/version, position/scale, opacity, text, entrance/exit preset, cue offsets must create new candidate and server validation. Approval produces new immutable compiled revision; same source audio/script when only layer changes; no hidden ChatGPT call.
- For creative motion redesign UI builds External ChatGPT targeted repair prompt, user manually imports new JSON; no internal AI.

## Durability and process ownership
- Submit persists queued job + bounded immutable descriptor, returns immediately; history render host claims due jobs using owner/lease/attempt token and prevents duplicates. IO/render outside SQLite write transactions.
- Node child process uses strict argv array and whitelisted executable path, refuses arbitrary shell args. On Windows, Job Object kill-on-close or equivalent owned process tree ensures cancel/crash/timeout kills Chrome/ffmpeg descendants.
- Retry can only publish when job still owns current lease/attempt and CompiledTimeline/final WAV/asset hashes match current approved heads. Crash recovery fences old attempt; file staged and SHA verified before publication.
- Bounded concurrency/memory/disk/cache, limited stdout/stderr and progress event frequency. API GET status must be read-only and not schedule new work.

## Dependency invalidation matrix
- Script changed → new voice/word map, Director and compiled motion, renders stale.
- Approved final WAV changed → align again, Director word cues/timeline/render stale, asset provenance untouched.
- Asset crop/version changed → only affected layer/timeline render downstream invalidates, script/voice untouched.
- Director setup changed → relevant setups/timeline/render revised, facts/voice untouched.
- Layer event timestamp/easing changed → motion revision/render only; history-source citations unchanged.
- Cache keys include local immutable bytes SHA + renderer build ID + frame settings; no stale preview copies.

## Tests & PASS
- UI controls all connect to API; play/seek + layer editing changes same composition in preview/output.
- Concurrent revision save/approval, stale optimistic lock, wrong asset/source, invalid offscreen bbox, re-render not touching Script/Voice when editing stamp; undo/redo staged only.
- Windows: kill Node/Chrome tree on cancellation, lease expiry, render timeout, host crash; stale attempt never publishes, restart resumes valid queued jobs. Duplicate submit idempotency.
- FFprobe and media hash before/after; huge timeline asset list UI reasonably responsive via lazy/virtualized tracks.
- Actual first video edit (replace one map asset and modify a stamp cue)→save→review→render updated MP4, with unaffected upstream SHA identical. No old database, root MANIFEST.json, LLM API, legacy subprocesses.
