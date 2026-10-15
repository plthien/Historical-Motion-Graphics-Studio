# P0 execution/self-review evidence

This document is a provenance register of implementation and verification scope, **not a green-gate assertion**.

- **Repository:** plthien/Historical-Motion-Graphics-Studio, explicit `main` at `0119572d651cb2b1902aea21b6267cca9809a940`.
- **Branch:** `feat/p0-history-remotion-feasibility`; PR #2 remains Draft.
- **POC:** experiments/remotion-history-poc only; browser uses `DocumentaryComposition.jsx` and `motionPlan`, CLI Root registers the same component/plan.
- **Fixed:** root PostCSS config inheritance; Node-only filesystem code incorrectly bundled into Vite; frame boundary validator error structure and null inputs; missing lockfile; old Player smoke subprocess not terminating; missing independent Windows FFprobe prerequisite.
- **Motion:** setups [0,570), [570,1200), [1200,1800), independent layer event durations, progressive labels, SVG route drawing and light camera drift.
- **Asset provenance:** synthetic paper/SVG, explicit photo PLACEHOLDER and SCHEMATIC map; no actual archival photograph claims.
- **No LLM API:** no model SDK/endpoint added to POC. Legacy YTB app remains donor-only at P0, not a supported future mode.
- **No release manifest:** no root `MANIFEST.json`, generator or auto-update introduced.
- **Evidence needed on exact final SHA:** `npm ci`, `npm test`, `npm run build`, Player playback/pause/seek screenshot gallery, headless Chrome real 1800-frame H.264 render, decoded FFprobe metadata, representative 12 still frames and Windows proof.
- **Known limits:** static donor importer/test graph needs runtime P1 closure; placeholder archival assets are not evidence-backed historical media; no production voice or source assets in P0.

**Verdict stays P0 PARTIAL until ALL exact-final-SHA gates pass.** No approval to merge or start P1.
