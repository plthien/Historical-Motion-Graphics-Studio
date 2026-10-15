# P0 PR #2 — independent implementation and QA review

## Exact scope and provenance
- Repo: `plthien/Historical-Motion-Graphics-Studio`
- Existing Draft PR: https://github.com/plthien/Historical-Motion-Graphics-Studio/pull/2
- Frozen donor main SHA: `0119572d651cb2b1902aea21b6267cca9809a940`
- Starting audited P0 HEAD: `1e5dd650a83baab935a7381383d0c755c35f5278`
- Final candidate SHA and proof are always recorded in the PR comment/body after the last changed commit and its CI runs. Never reuse evidence from a prior candidate after source changes.

## Seven confirmed findings — remediation and regression ownership

1. **Legacy manifest pytest: FIXED.** Deleted only `tests/v4/test_kokoro_setup_static.py::test_release_manifest_refresh_targets_active_branch`. The missing manifest workflow is intentionally forbidden; no global pytest bypass, no manifest restoration. All Voice, neutral infrastructure, History governance and P0 tests remain mandatory.
2. **Player/MP4 wrong inherited foreground: FIXED.** One `PaperCanvas` and composition-level ink/font/background own all visual elements, independent of Vite page CSS. Real Player screenshot and exact decoded H.264 frame parity with an automated RGB differential report.
3. **Collision/overflow: FIXED in POC layout.** `src/layout.mjs` owns rect zones and safe boundaries, with collision and off-canvas regression tests. Duplicate photo warning layer removed. Map stamp does not collide with Sarajevo label; archival headline does not intersect photo. Frame contact sheet reviewed at required times. Archive opening and tail improved after initial inspection.
4. **Map/Route drift: FIXED.** One `MapViewport` SVG includes common camera transform, background road, shared-world-route `d`, annotation and waypoint. Player test compares identical SVG path and screen CTMs across animated route frames.
5. **Asset preflight bypass: FIXED.** `npm run render` invokes `scripts/render.mjs`: schema validation → filesystem realpath containment (root/file/extension) → reject failures → spawn Remotion. Security tests include missing/valid media, directory-as-file, invalid root, traversal, remote input, symlink escape, and ensure invalid plans never invoke renderer.
6. **Donor ledger mixed-SHA/coverage: FIXED statically.** Immutable Git donor object reads; 778 tracked production/config files, including root launch/build files; deterministic sorting and bytewise `--verify`; regression tests cover root coverage, working tree mutations, missing donor SHA and missing entries. Dynamic dependency and runtime P1 migration safety are NOT proven by this static report.
7. **Exact-head CI/contact-sheet: FIXED.** Both Linux/Windows checkout explicit PR HEAD and verify `git rev-parse HEAD`. Linux Player, pixel parity, H.264, full decoded FFprobe and 12 chronological zero-padded extracted frames; Windows H.264/FFprobe with bounded dependency-install retries. CI fails closed when a required prerequisite is missing.

## Technical acceptance evidence (commits must match the tested HEAD)
- `cd experiments/remotion-history-poc && npm ci && npm test && npm run build && npm run test:player && npm run render && npm run verify:video`.
- `python tools/history_p0_source_audit.py --verify`
- `python tools/check_history_governance.py`
- `python -m pytest -q tests/history/test_history_p0_source_audit.py`
- Full donor backend regression and focused Voice tests remain mandatory in the existing Main Candidate Audit.
- Required export: H.264 / 1920×1080 / 30/1 FPS / 1800 frames / 60 seconds, fully decodable; sampled Player frame IDs exactly match decoded export frame IDs.

## Honest acceptance limitations and out-of-scope boundaries
- `REFERENCE_STYLE_COMPARISON_NOT_VERIFIED`: no original benchmark clip was provided in this P0 task. No numeric similarity score or claim that a documentary-quality final historical video was produced.
- POC contains **only a labeled archival-image PLACEHOLDER and schematic geography**. Media date/license/geography cannot be claimed verified. No finalized narrator voice, external historical source package or production-aligned WAV.
- Static ledger does **not** authorize P1 deletion; dynamic imports/routes/runtime/DB cutover must be independently reviewed during P1, which has NOT begun.
- Donor YTB backend, UI, DB and user-data are not modified by P0; the one obsolete manifest-only test was deleted because it enforced prohibited root manifest behavior.
- P0 verdict: **PARTIAL** until exact-head technical gates and independent editorial/reference comparison satisfy all approved acceptance criteria. Keep PR Draft; no merge/main writes, no P1.
