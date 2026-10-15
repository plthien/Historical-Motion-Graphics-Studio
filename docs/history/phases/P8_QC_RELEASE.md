# P8 — Historical Studio final QC, Windows release and production readiness

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no local LLM, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Precondition
P1 history-only clean cut already accepted, P6 full pilot film real, P7 editor and durable Node host tests pass. **P8 is NOT allowed to postpone any old YTB deletion**: old stages, routes, migrations, modules, data and adapters must have been removed in P1.

## Final deliverable
- Local Windows Historical Motion Graphics Studio install/start flow; one Python/FastAPI backend, required media/voice host only, dedicated Node Remotion render host, React UI and fresh history-only SQLite. No YTB Worker/Redis/RQ old jobs.
- `FinalVideo.v1` approved artifact with MP4 bytes/SHA256, source CompiledTimeline SHA, final WAV/asset hashes, Remotion version/build, render job attempts and settings.
- `QCReport.v1` includes independent metadata, audio and visual checks, script/claim/asset rights review status and attribution. No root release MANIFEST.json: this project explicitly removed its generator/check, so do not reintroduce it in CI, Windows packaging or docs.
- Release docs `README.md`, `00_START_HERE.md`, `docs/architecture/*`, install troubleshooting/backup and External ChatGPT export/import operator tutorial. Packaging generates safe ZIP directly from allowed source/dependency output paths and independently excludes DB/media/secrets.

## Quality system modules
- `app/history/quality/{ffprobe,video_frames,audio,rights,historical_review,report}.py`, `app/history/api/quality.py`, `src/features/history/delivery/{RenderDeliveryPanel,QCReportViewer,AttributionViewer}.tsx`.
- Windows launch scripts and setup preflight Python 3.12, compatible Node, Remotion Chromium/headless shell, FFmpeg/FFprobe, Kokoro runtime assets if selected, available disk and audio provider readiness.
- Dedicated new CI `.github/workflows/history-studio-candidate.yml`: Python/DB contracts and legacy closure, React build/UI screenshots, Node Remotion render/browser/FFprobe/audio checks, Windows host process lifecycle, P0–P7 regression, clean packaging; stage artifacts include logs/screenshots and MP4 references. Never depend on root release manifest.

## QC definitions and thresholds (project-configured and audited)
- Structural: video codecs (H.264), pixel format supported, resolution/aspect, nominal fps, duration/frame count to expected integer frames, audio codecs/sample rate/non-silent narration, output size/hash.
- Frame sample: sample first/last/setup boundary/critical cue frames, reject blank/black frames not intentionally authored, image missing/decode errors, text overflow safe areas, brand/watermark artifacts.
- Audio: independent WAV and final AAC duration difference within documented frame tolerance, loudness peak/clipping/silence, word cue visual samples vs known aligned intervals. Avoid claiming automatic semantic fact-check accuracy.
- Source/rights: each photo/map/illustration has provenance/version/author/license/attribution, historical year/location correctness signoff or labeled reconstruction, cited claim review; missing license/rights blocks export by default.
- Reproducibility: frozen exact script/audio/asset/renderer SHA fingerprints; no cached stale renders after source change.

## Implementation order
1. Freeze QC schema and typed evidence errors, independent validation of rendered files with FFprobe/ffmpeg/known samples.
2. Add QC job after render attempt completes but BEFORE CAS FinalVideo publication. Failed QC cannot publish and must keep prior valid final revision.
3. Add history factual/source reviewer signoff, attribution text/sidecar production and operator-visible exception state; never silently approve missing rights.
4. Windows launcher: handle fresh empty history DB init, install deps from pinned locks, start only history services and supervised render host; clean uninstall stops owned tasks without deleting user source media unless explicit user action.
5. Build multi-OS and Windows CI test matrix, upload frame contact sheets and stdout artifacts, batch render long project to test resource caps and retry.
6. Replace old YTB README/docs/settings and old packaging scaffolding with history-specific docs, no manifest generation/verification of root file. Verify source closure scans against P1 contract still green.
7. Final review: reference sample visual density/typography, project save/reload, Manual External ChatGPT handoffs, two video lengths, crashes and restart.

## Release readiness gates (NO PARTIAL PASS)
- From approved topic → external ChatGPT JSON Topic/Script/Research/Director imports → voice+real word timing → actual approved images/maps → edit/preview → render video + QC/rights → final downloadable MP4. Demonstrate 60–90 second and 5+ minute projects with distinct media and no missing audio.
- Clean Windows setup and update/restart, backend+Node/browser builds, rendered frame/audio FFprobe, project reopening, precise UI screenshots at 1440x900 and 1920x1080.
- Fail-to-pass cases: missing historical license, wrong period map, unapproved asset, stale WAV timing, black frame, clipped title, silent final video, failed Windows Job Object process ownership, crash/resume attempt race, stale project head on publish, missing file/long path in ZIP.
- Negative closure: no old YouTube Studio workflow/data/DB/routes/migrations/test directories used in runtime; no root MANIFEST.json/file check/generator/auto workflow; no LLM API/model; local storage default separate from all previous YTB paths.
- Tests/CI on exact final candidate HEAD; no skipped/unverified required gate can PASS. Release report documents version, schema, Remotion license check, provenance and operator limitations.
