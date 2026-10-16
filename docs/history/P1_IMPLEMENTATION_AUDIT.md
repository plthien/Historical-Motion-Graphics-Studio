# P1 strict independent self-review — source, tests and phase gates

**Base:** main df09b5718377a79774c7169e55b09b159c9aa2d3. **Branch:** feat/p1-atomic-history-clean-cut. **Review:** Draft PR #3. The final tested SHA and CI run URLs must come from this PR's latest successful jobs; an older SHA is never proof of a later one.

## Frozen G1–G15 closure

- G1 Git safety: branch created from verified main; Draft PR only, no push/merge to main/default-branch change.
- G2 Coding authority: retained new AGENTS, History CODING_RULES, v3 plan, all new skills/agents.
- G3 Runtime: YTB V4, Scene/Beat, Visual References, PromptBundle, image-per-beat pipeline, old workers and obsolete UI removed as tracked source. Only app.main and app.history remain operational.
- G4 API: GET /health and real History Channel/Project/status routes, typed errors and no YTB aliases. Runtime OpenAPI checked in backend tests.
- G5 Database: one History-owned 001_history_initial migration; history_meta marker, channels, projects, stage_heads, immutable artifacts and authoring reservations; FK and uniqueness checked.
- G6 Safety: old/foreign/empty SQLite fixtures reject as HISTORY_LEGACY_STORAGE_UNSUPPORTED; no SHA/mtime changes or -wal/-shm creation. No old data migration.
- G7 Persistence: real create/list Channel+Project, status and reopen after process restart via TestClient, browser smoke and Windows launcher.
- G8 Neutral utilities: strict JSON parser rejecting duplicate keys, safe asset path/symlink containment, hash-checked immutable artifact bytes, foreign-ownership check, revision CAS race and stale-head protection. Validate-only remains zero-write.
- G9 Dark Cockpit: exactly seven named History stages, real API state and disabled future operations; Playwright captures 1440×900 and 1920×1080. No overflow or old branding.
- G10 Windows: powershell launcher checks Python3.12/npm, installs pinned dependencies, initializes isolated DB, starts FastAPI/Vite only and owns start/restart/shutdown; no old worker.
- G11 CI: exact SHA Linux/Windows History tests, frontend build/smoke and P0 Remotion test/build regressions. No blanket legacy pytest bypass: donor product tests physically removed.
- G12 No model SDK/API or automatic ChatGPT workflow: static source and dependency scan.
- G13 No root MANIFEST.json/generator/verification/autopush.
- G14 P0-TRANSFER-001: README REWRITE, 00_START_HERE and docs/00_INDEX PRESERVE; immutable 839-file donor ledger corrected and regenerated; retired YTB docs non-authoritative.
- G15 P0-DEFER-P6-001 recorded in P6 plan; no P6 code, voice or source photo implemented.

## Scoped findings and actions

- P1_BLOCKER: accidental old Voice-only test left under tests/history; deleted with retired tooling. P1_BLOCKER: closure scanner NameError; repaired. P1_BLOCKER: duplicate JSON diagnostic omitted stable code; repaired.
- P1_REGRESSION/Security: artifact directory symlink out of History storage failed to reject; added targeted containment guard and negative regression. No other material regression identified in the frozen acceptance scope.
- DEFER_P6: reference-video editorial benchmark, real narrated 60–90s Remotion, rights-cleared historical photos, production source verification.
- DEFER_P3/P7: dedicated Job Object supervision for future long-running voice/render children; P1 launcher stops owned API/UI descendants, does not start voice/render hosts.
- NOT_A_DEFECT: P2–P8 production operations intentionally disabled with exact NOT_IMPLEMENTED_P* readiness.
- OPTIONAL_IMPROVEMENT: future virtualized mass project list and wider backend operational telemetry, not P1 acceptance criteria.

## Source-level review

Retired old donor production files and migration chain, isolated accepted P0 experiment, retained new History governance. Backend imports new app.history modules only. Frontend imports History DTO/client only. Frozen P0 donor report remains a historical audit; dynamic route closure is independently checked with runtime OpenAPI and Python AST source tests. No user data was deleted or altered by Git operations. Domain changes establish P1 typed Channel/Project schema; P2 authoring APIs are intentionally absent.

## Evidence needed at exact final commit

- python tools/check_history_governance.py
- python tools/check_history_p1_closure.py
- python tools/history_p0_source_audit.py --verify
- python -m pytest -q tests/history
- npm ci && npm test && npm run build
- npm run test:ui (1920×1080 and 1440×900, live API restart)
- Windows start-history.ps1 -CheckOnly and -Smoke
- P0 experiment npm ci && npm test && npm run build

**Verdict is determined by exact final SHA results, not by this static document.** User merge approval required; never start P2 automatically.
