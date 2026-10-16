# P6 — Production Remotion engine and first complete historical video

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no local LLM, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Precondition
P0 Remotion technical proof accepted. P1 history-only product; P2 exact approved Script; P3 real final WAV/WordTimingMap; P4 actual approved rights-cleared image versions; P5 valid immutable CompiledTimeline. Never make fake assets/time anchors to claim first complete video.

## Runtime ownership and files
- New `runtime/remotion/` package with pinned same-version remotion, @remotion/player, @remotion/renderer/@remotion/cli and React dependencies plus checked package-lock. Source: `src/composition/HistoryComposition.tsx`, `src/components/{PaperCanvas,ArchivalPhoto,PortraitCutout,HistoricalMap,RouteDraw,HeadlineReveal,Timestamp,Stamp,Diagram,FactComparison,CameraRig,Annotation,Caption}.tsx`, `src/templates/`, `src/renderer/render.ts`, `src/contracts/`, `tests/`.
- Shared composition library consumed by `src/features/history/motion/MotionPreview.tsx` (@remotion/player) and Node render host with exactly the same compiled plan/media/fonts/version. No separate DOM simulation.
- Python `app/history/render/{request_validation,bridge,manifest,service}.py`, `app/history/api/preview.py`, typed frontend DTO for selected CompiledTimeline revision.
- Renderer MUST only read whitelisted local media assets by immutable source IDs/hashes; never arbitrary remote URLs or model-authored JSX/CSS/JS.

## Composition / video behavior
- VisualSetup covers multiple spoken thoughts; all layers have own frame-indexed enter/move/hold/exit, z, color, crop and masks. Smooth layer motion is driven by useCurrentFrame/spring/interpolate with deterministic easing, not mutation/timeouts.
- Components: paper/off-white background + subtle texture, historical photo/polaroid/tape, period-map (or conspicuously schematic), animated route path, chronological title/labels, kinetic headline, stamp, callout, timeline, camera pan/zoom, optional subtitles. Combine many within one persistent setup.
- Word-cued animation uses approved word_id/phrase_id mapping, exactly matching final audio hash; no invented narrator timestamps or after-the-fact WPM assumptions.
- Sound: exact final WAV, optional approved local BGM/SFX assets with provenance, time-aligned captions generated deterministically from approved spoken text/timing. No editorial historical claim added solely by rendering.
- Render resolution configurable, pilot 1920x1080 30fps H.264/AAC; rendering and browser preview honor same fonts/font metrics/resolution, deterministic paths and source versions.

## Server/UI protocol
- GET `/api/history/projects/{id}/preview-contract`: approved compiled timeline schema/version, asset safe local media endpoint tokens, exact final audio info, required fonts, frame metadata.
- POST `.../render/preview-jobs` optional headless sample generation; P6 command-line Node production render pilot may run under controlled launcher until P7 full durable scheduler exists. P6 must not resurrect old FFmpeg graph or obsolete RenderOperation fields.
- Studio Motion Preview play, pause, seek frame, inspect active layers, basic warnings and metadata. Full editing deferred to P7. Preview consumes same authoritative props passed to output renderer.

## Implementation order
1. Audit/port P0 composition: replace demo-only hard-coded plan with CompiledTimeline.v1 adapter (strict).
2. Implement template registry and layer renderers; each supports typed behavior with no dynamic code injection; unit test props and frame-local boundaries.
3. Add multimodal media loading, validated hashes/license state, font loading and local path containment. Map/source labels must remain attached to correct assets on each frame.
4. Implement cue-locked voice/caption integration and audio track validation, source checks to avoid silent exports.
5. Add shared Composition package, browser Player and Node render entrypoint; use viewport/resolution consistency, no duplicated animation logic.
6. Render pilot project 60–90 sec from actual External ChatGPT JSON + reviewed historical assets + actual final WAV; capture actual MP4, frame contact sheet, FFprobe and operator experience.
7. QA/edit motion for reference visual density/continuity, OCR-independent title-safe zones and cutout/route pacing. Record source attribution document.
8. Document commands, Chrome setup, exact Node version, error codes, no automatic model connections.

## Acceptance tests
- Unit: per-frame rendering of each component, sequence boundaries, long-lived setup, overlapping separate layers, ease curves, 30fps quantization, captions audio-cue match, untrusted JSX/script injection refused.
- Browser: @remotion/player playback and seek at first/last frame and setup transitions. Screenshots of 0/5/10/20/40/60s (adapt for 60–90s pilot), compare to rendered frames at corresponding frame numbers.
- CLI/server: actual H.264 video 1920x1080, 30/1fps, 60–90s, correct audio codec/presence, no black unintended frames, no clipped overlays, source hashes verified before and after render. FFprobe & audio peak QC.
- Model/video facts: archived images and period maps labeled correctly, rights manifest, no generated archival forgery.
- No legacy YTB renderer/assets/DB/support; no LLM API; no root release manifest/check. If missing real voice or archive media, mark P6 BLOCKED rather than call it a full film.

## P0-DEFER-P6-001 — Production Visual Reference Benchmark & Editorial Motion Quality Gate

**Transferred from accepted P0; deferred to P6, not a P1 blocker.** Obtain a real reference video when available and document its source, timestamped visual observations, VisualSetup length, independently animated layers, photo/map/type allocation, transitions, typography, camera movement and editorial pacing. Compare actual frame-matched `@remotion/player` and rendered MP4 sequences. Inspect static stretches, layer motion, hierarchy, route drift, safe-area text collisions, genuine archive media/license facts and narration cues. Define reviewer-verifiable quality gates before implementation. Required evidence: timestamped reference observations, approved historical asset/rights record, real narrated 60–90s pilot, sampled motion sequences, Player/MP4 parity, editorial review and limitations. When unavailable report exactly `REFERENCE_STYLE_COMPARISON_NOT_VERIFIED`. Owner: P6 Remotion/Frontend/QA; depends on approved P1 foundation and P2–P5 script, voice, assets, compiler. **Do not implement under P1.**
