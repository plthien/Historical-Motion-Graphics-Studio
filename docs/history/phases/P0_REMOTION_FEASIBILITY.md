# P0 — Baseline audit and actual Remotion renderer feasibility

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no LLM local, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Precondition / input
- New main codebase is legacy YTB Studio. Snapshot main HEAD, Git tree and currently mounted branch before working.
- The previous `feat/r0-remotion-history-motion-poc` branch contains isolated unverified composition code; use as READ-ONLY implementation reference, not merge base.
- Collect a **machine-readable KEEP / EXTRACT / REWRITE / DELETE ledger** of every source under app/, src/, contracts/, tests/, alembic/, scripts/, tools/ and .github workflows. State the new history owner, direct/transitive importers and tests to carry across.

## Ownership and implementation files
- Add `experiments/remotion-history-poc/` under the NEW main-based feature branch, POC-local `package.json`, `package-lock.json`, `src/plan.mjs`, `src/DocumentaryComposition.jsx`, `src/Root.jsx`, `src/preview.jsx`, `tests/` and `README.md`; selectively copy from R0 branch after audit.
- Add dedicated `.github/workflows/history-remotion-poc.yml` pinned Node 22, remotion package exact matching version, `npm ci`, `npm test`, Vite build, Chrome/Headless Shell render and FFprobe; produce screenshot/frame artifacts. Existing YTB workflow remains unchanged except the explicitly removed root manifest checks.
- Add source ledger `docs/history/source-ownership-ledger.json`, P0 gate report and example source asset/license register (not root MANIFEST.json).

## Data contract
- Frozen technical-only fixture `history_motion_poc.v1`: {schemaVersion, fps, width, height, durationFrames, audioSrc|null, setups[]}; setup {id,kind,startFrame,endFrame,events[]}; event {id,kind,atFrame,durationFrames,layer,text?,assetPath?}.
- POC validation must reject null/arrays/wrong types/unknown keys, duplicate setup or event IDs, gaps, overlapping setup intervals, missing asset paths, absolute/URL/path-traversal/symlink escapes and inconsistent fps/duration. For POC only, local fixture may use explicitly labeled placeholders. Do not infer real word timing.
- Actual video demo: 60s/1800 frames/1920x1080/30fps; archival collage, paper/tape texture, editorial type, line/route, stamp, progressive layers. The schematic map must be labeled; fictitious archival images cannot be presented as genuine.

## Execution order
1. Record exact main SHA, commit tree, branch and current CI workflow; do not mutate main.
2. Produce ledger incl. dependency graph of YTB schema/Voice/Render and retain independently useful tests.
3. Port inspected R0 files one at a time; pin dependency versions and create lockfile.
4. Harden planner and event timing tests: setup boundaries at 0/569/570/1199/1200/1799; verified shared Player/CLI composition and frame-local spring/interpolate.
5. Run dependency install, Node tests, build, browser preview seek/play, H.264 render; FFprobe metadata and representative stills at 0/5/10/19/20/25/35/39/40/45/55/59 sec.
6. Perform frame-by-frame editorial comparison with provided video reference where accessible. Record rights of real demo images and transparent limitations where not.
7. Submit Draft P0 PR, file ledger, CI runs, screenshot gallery, FFprobe output and issue list. No auto-merge.

## Mandatory tests and gates
- `npm ci`, `npm test`, `npm run build`, Remotion render and browser Player smoke must actually complete on candidate SHA.
- Independent FFprobe: duration 60.000s, 30/1 fps, 1920x1080, H.264, frame count 1800 (allow container metadata nuance with decoded frame check). No unintended blank frames or overlaps; text not clipped.
- Security tests: null events, wrong schema, duplicate IDs, malicious media path, bounds overflow. No LLM SDK/endpoint imports.
- CI uses NO root release manifest; no auto-push workflow.
- If Node/Chrome/media assets unavailable, explicitly BLOCKED. A silent technical POC is not a finished narrated historical film.

## Out of scope
No history production DB, no new routes in app/main.py, no Voice extraction, no full Story/Director editor. These begin only AFTER P0 proof, and P1 MUST hard-cut old YTB runtime before P2.
