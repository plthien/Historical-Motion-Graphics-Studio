# Historical Motion Lab — R0 (standalone feasibility proof)

A deliberately isolated, **non-production** Remotion prototype for a personal, noncommercial history-learning video tool. It proves persistent visual setups, independently timed motion layers, and React preview/CLI render. This is not a fork of Remotion and does not copy source code from remotion-dev/remotion.

## Windows / local prerequisites

- Node.js 22; npm; adequate disk space for browser/render binaries.
- Internet access for the **initial** dependency and Remotion browser installation.
- From this directory: `npm install` (a prototype-local package-lock will be generated).
- Run `npm test` to check plan/coverage invariants.
- Run `npm run dev` then open http://127.0.0.1:5174.
- Run `npm run render` to render `out/history-poc.mp4`. The Remotion CLI may install a Chrome Headless Shell binary.

No packages are added to the repository-root package.json. The existing backend, production pipeline and C0 Spine draft PR remain untouched. The prototype deliberately has **no** external image, video or TTS dependencies; it is silent unless a local approved voice file is supplied. Frame timings are illustrative rather than voice aligned.

## Plan authority

`src/plan.mjs` holds one 60-second demo plan, with `setups` covering the whole film and independently timed `events` in local setup frames. `validatePlan()` rejects gaps, overlaps, duplicate event IDs, out-of-bounds timing, unsupported kinds and unsafe local path strings. The reusable composition is `src/DocumentaryComposition.jsx`, shared by `@remotion/player` and the Remotion CLI registration in `src/index.jsx`. No separate fake player or alternative HTML renderer exists.

Set `audioSrc: 'narration.wav'` only **after** placing a licensed voice file at `public/narration.wav`; then recalculate plan timing from the final aligned audio in a later phase. Any `assetPath` must likewise point to a validated file under `public/`.

The demo's map is explicitly a **schematic**, not a historically accurate border map. Its archival photograph is an obvious placeholder. They must never be described as genuine evidence.

## R0 review gates

1. `npm test` passes.
2. `npm run build` succeeds.
3. Preview (Player) displays all three contiguous setups at 0, 19, 20, 39, 40 and 59 seconds; layer reveals are timed, not a new picture per sentence.
4. CLI render produces exactly 60 seconds, 1920×1080/30fps without blank frames, with an independent FFprobe check.
5. Side-by-side editorial comparison with user reference footage. R0 is **not accepted as style-parity** until photographs/maps/fonts/kinetic timing are iterated and signed off.

## Follow-up integration decision

The repo's draft PR #1 models Spine actor rigging and is not compatible with an archival motion-graphics-first workflow as its primary engine. Review and replace its stage contracts before any C1/C2 clean-cut. Future production needs authoritative script, approved audio, word timing, evidence-backed asset manifest, AI visual director, immutable motion plan, Remotion preview/render, and per-layer QC. See `docs/architecture/HISTORY_REMOTION_PIVOT.md` for the proposed boundary.

Pinned Remotion family version: `4.0.535` from the upstream main branch on 2026-10-15; all Remotion packages must match. Before broader rollout, resolve versions/lockfiles and check license against https://www.remotion.dev/docs/license.
