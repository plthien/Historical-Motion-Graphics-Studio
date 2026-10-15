# Copy/paste prompt for Codex — Finish P0 Remotion History POC

## Assignment

You are implementing P0 only in plthien/2D-Animation-Studio.

Target branch: feat/r0-remotion-history-motion-poc (PR #2, currently Draft). Never use the separate Spine/Pixi PR #1 as a base.

Read in this order:
1. docs/implementation/HISTORY_MOTION_STUDIO_DETAILED_IMPLEMENTATION_PLAN_v1.md
2. docs/architecture/EXTERNAL_CHATGPT_AUTHORING_CONTRACT.md
3. docs/architecture/HISTORY_REMOTION_PIVOT.md
4. experiments/remotion-history-poc/README.md
5. experiments/remotion-history-poc/src/{plan.mjs,DocumentaryComposition.jsx,Root.jsx,index.jsx,preview.jsx,style.css} and tests/plan.test.mjs
6. The repository CI workflow files and README / 00_START_HERE.md, interpreting legacy YTB product rules only as source inventory, NOT as product authority.

## Absolute constraints

- NO LLM APIs (OpenAI/Anthropic/Gemini/etc.), no local LLM, no model providers, no ChatGPT UI automation, no hidden creative generation. Our future workflow is External ChatGPT prompt -> user-downloaded JSON import -> strict validation -> explicit approval. P0 implements NONE of those future authoring features, but must not violate the rule.
- Use published, version-matched Remotion dependencies, NOT a fork/copy of remotion-dev/remotion.
- P0 is deliberately isolated under experiments/remotion-history-poc; do not change old V4 runtime, API, DB, studio UI or migrations. Do not merge Spine contracts from PR #1.
- Preview and CLI render MUST use the exact same DocumentaryComposition. No fake preview, HTML video replacement or separate fake renderer.
- The video style reference is collage-based historical motion graphics (photos/maps/text/cutouts/stamps). DO NOT reinterpret it as rigged 2D characters or an image-per-beat slideshow.
- Preserve actual historical distinctions: no invented archival photographs, no unlabeled schematic historical borders. Approved/rights-clear input media only.
- Do not claim PASS when tests/render/screenshots cannot be executed; report exact missing evidence, not optimistic completion.

## Work required in this PR

A. Dependency integrity and project scripts
1. Inspect current package.json, verify that pinned remotion, @remotion/player and @remotion/cli version 4.0.535 (or a single other verifiably compatible pinned release if justified) are available and match exactly.
2. Generate and commit experiments/remotion-history-poc/package-lock.json with Node 22/npm, and make npm ci reproducible. Do not alter root package-lock unless justified as part of isolated CI wiring.
3. Ensure npm test, npm run build, npm run dev and npm run render are functional and documented. Create output directories safely; prevent outputs from polluting Git tracked sources. Verify .gitignore isolation.
4. If an environment lacks npm/Chrome/FFmpeg, do not pretend a script ran. Add reproducible instructions and fail with a clear reason.

B. Robust deterministic timing validation
1. Keep a strict, versioned plan in src/plan.mjs for POC. Validate ALL nested object shapes safely against null/arrays; reject unknown event kinds and wrong runtime types without uncaught TypeError; reject duplicate/empty IDs.
2. Validate ordered contiguous setup intervals exactly covering duration, correct bounded relative event starts/ends, valid z-index, text length/safe unsupported characters and local media path restrictions.
3. Prevent path traversal and alias escaping; no HTTP URLs for renderer media. Verify referenced media files exist BEFORE attempting production-quality render, while permitting explicitly declared placeholder fixtures in technical tests only.
4. Review Remotion Sequence + useCurrentFrame timing at setup boundary and event boundary; ensure event opacity/route motion use event-local frames and visually hold/exit at intended moments. Review z-index and unmount semantics.
5. Add tests covering malformed nested objects, duplicates, timing overlaps/gaps, frame 0/569/570/1199/1200/1799, event reveal/expiry, long text and path attacks. Avoid snapshots that simply restate the implementation.

C. Real preview/render and visual acceptance
1. Run the Player on Windows or a documented equivalent browser and verify playback and seek behavior. Capture browser screenshots showing the actual composition.
2. Replace the ARCHIVAL IMAGE PLACEHOLDER and visibly fake diagram in an ACCEPTANCE sample with an operator-approved or public-domain asset (or transparently label an illustrative asset). Preserve source/rights/attribution metadata in a small sample asset manifest; if real rights-clear resources are unavailable, keep the demo technically testable and report VISUAL ACCEPTANCE BLOCKED.
3. Improve frame-to-frame motion feel toward the benchmark: archival photo paper frame/tape/shadow, real cutout/portrait as allowed, 1914 period-safe source or clearly schematic map, strong black/yellow/red editorial type, texture, easing, cut/pop/slide/hold, progressive reveal, layer density/continuity.
4. Test 0, 5, 10, 19, 20, 25, 35, 39, 40, 45, 55 and 59-second frames. Ensure no blank setup boundary, clipping, unsupported glyphs, covering labels, jitter, excessive 20-second static holds or all-content simultaneous appearance.
5. Produce exactly 60 seconds / 1800 frames / 1920x1080 / 30fps, H.264 MP4 with Remotion CLI. Verify with independent FFprobe duration/fps/dimensions/codecs and generated frame samples. P0 may remain SILENT; explicitly document missing narration as a P3/P6 feature, not call it a full final history video.
6. Compare benchmark 1015.mp4 side-by-side if accessible. If it isn't attached/accessible to the coding environment, report that inability; do not fabricate a similarity score or parity gate.

D. Dedicated CI/test evidence
1. Add a narrowly scoped workflow such as .github/workflows/history-remotion-poc.yml. It must run from exact candidate SHA and execute POC npm ci/test/build plus a headless render smoke gate. Record FFprobe metadata and representative stills/logs as artifacts when feasible.
2. Add real Windows render/FFprobe evidence (CI Windows job or documented operator acceptance). The current green legacy backend/frontend/Windows YTB workflow is NOT an R0 Remotion gate.
3. Do not degrade legacy CI while keeping the experiment isolated. If CI cannot install browser binaries, classify explicitly as environmental blocker and supply the failing logs.
4. Update POC README only with verified steps, exact commands, actual output artifacts and clear P0 scope exclusions.

## Required implementation order

1. Inspect HEAD and report initial findings; read mandatory docs.
2. Produce concise file-level task/checklist; identify all tests and environmental prerequisites.
3. Dependency resolution/lockfile/CI first, so later visual refinements are tested by an actual renderer.
4. Harden validator and timing semantics with unit tests.
5. Make real CLI render and Player preview work; instrument keyframe checks and FFprobe.
6. Replace/approve demo assets, improve composition style and verify sampled frames.
7. Self-review and rerun all affected exact-SHA checks; describe blockers honestly.

## Acceptance / mandatory evidence

- Current exact commit SHA, changed paths, all checks run (pass/fail/not-run), logs and CI URLs.
- npm ci (local POC) OK; npm test OK; npm run build OK; npm run render OK.
- Actual MP4 path/content hash and independent FFprobe summary confirming frame dimensions, fps, duration and H.264; representative screenshot gallery.
- No hidden LLM/API dependencies and no production module modifications.
- Distinct signoffs: TECHNICAL GATE and VISUAL STYLE GATE; both must pass to move to P1.
- If a required gate cannot be verified: PR stays Draft, verdict BLOCKED or PARTIAL; never PASS.

## Required final response from Codex

1. Implemented changes and exact files
2. Test matrix with real executed commands
3. Visual/style comparison findings with screenshot evidence
4. QA/security/factual/rights findings
5. Remotion POC readiness: PASS/PARTIAL/BLOCKED, with precise reasons
6. Missing work that must remain in P1–P8
7. GitHub PR link and exact candidate SHA

Do NOT proceed into P1 or call any LLM provider API as part of this assignment.
