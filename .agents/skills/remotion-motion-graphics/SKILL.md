---
name: remotion-motion-graphics
description: Build and verify the deterministic layer-based historical Remotion composition, shared browser preview/export renderer, frame-cued motion and final MP4.
---

# Remotion Motion Graphics Skill — NEW HISTORY RULES ONLY

## Scope
P0 feasibility, P5 compiler contract, P6 production composition, P7 timeline editor, P8 visual QC. Read root `AGENTS.md` and `docs/history/CODING_RULES.md` first, then relevant NEW phase spec. Never follow old YTB per-beat images/FFmpeg montage rules, even in donated source comments.

## Visual system and allowed inputs
Reference look: archive-paper collage, credible historic photos and maps, layered text/diagrams and red/yellow stamps; dynamic elements appear independently over persistent compositions. Accept ONLY a server-validated `CompiledTimeline.v1` with exact fps/frames, approved final WAV hash, approved asset versions and enumerated layer/motion types. Reject arbitrary model-authored code, CSS, URLs and executable effects.

## Implementation procedure
1. Pin matching versions of `remotion`, `@remotion/player`, `@remotion/renderer` / `@remotion/cli` with lockfile. Share one `HistoryComposition` source between React preview and the actual Node export. No duplicate HTML preview logic.
2. Define reusable components: PaperCanvas, ArchivalPhoto/Polaroid, PortraitCutout, PeriodMap/SchematicMap, RouteDraw, KineticHeadline, TimeLabel, Stamp, Annotation, Diagram, FactComparison, CameraRig and optional Captions.
3. Render based solely on `useCurrentFrame()`, `useVideoConfig()`, `Sequence` and deterministic transforms. Each VisualSetup may span multiple sentences, while layer events independently enter/hold/move/exit. Explicit transition and continuity ownership avoids blank or duplicate frames.
4. Compile word/phrase IDs from actual final forced-aligned WAV to exact INTEGER global frame cues. Use documented rational-fps rounding and reject missing/cross-project word references or changed WAV identity.
5. Resolve locally approved assets, license labels, font packages and image dimensions; reject unsafe paths, unknown motion presets, off-canvas critical text, unsupported image types and dynamic component/source execution.
6. Verify scene density and visual legibility with real Player playback/scrub and server-rendered sample frame screenshots. Run full local/CI Remotion H.264 MP4 and independent FFprobe; inspect audio presence, frame count and actual visual keyframes.
7. Accept controlled layer edits as versioned candidate revisions; never silently change script, narrator voice, evidence metadata or unrelated visual setups.

## Test gates
- P0 demo: 1920x1080, 30fps, EXACT 60s and frame boundary samples; labeled placeholder assets allowed only in technical experiment.
- P6 pilot: 60–90s REAL narration with actual approved historical assets, synchronized motion/FFprobe and source manifest. Silent POC cannot count.
- P7: local layer edit rendered identically in Player/export with no old scene pipeline. Windows child-process cancellation/recovery verified.
- Every required test has actual exact-SHA evidence: npm ci/test/build, headless Player snapshot, MP4/FFprobe, keyframe contact sheet, relevant negative tests. Mark unrun gates BLOCKED, never PASS.

## Forbidden
No animation script emitted by LLM, no FFmpeg old-slide alternate compositor, no frame-by-frame static image per sentence, no blind use of modern borders, no root release MANIFEST.json.
