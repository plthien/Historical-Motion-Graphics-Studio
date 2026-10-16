# P1 Frozen Acceptance Gates — 2026-10-16

**Authority:** AGENTS.md → CODING_RULES.md → Clean-Cut v3 → P1_ATOMIC_CLEAN_CUT.md. Scope is exactly **P1**, plus P0-TRANSFER-001. Later-phase work cannot become a P1 blocker unless it breaks current P1 correctness/safety.

| Gate | Frozen requirement | Evidence required |
|---|---|---|
| G1 | Branch `feat/p1-atomic-history-clean-cut` from verified `main` `df09b5718377a79774c7169e55b09b159c9aa2d3`, Draft PR only | Git SHA/PR |
| G2 | Only new History governance, agents and skills active | Governance source scan |
| G3 | All old YTB production source/routes/process/services/tests removed, no dual mode | tree/import/closure |
| G4 | Only `/health`, real `/api/history/*` and neutral API docs | Runtime OpenAPI |
| G5 | One empty History SQLite schema and only initial History Alembic revision | SQLite/migration |
| G6 | Foreign/legacy database fails closed `HISTORY_LEGACY_STORAGE_UNSUPPORTED`; source hash and mtime unchanged | negative startup test |
| G7 | Create/list/select Channel & Project, fetch status, persist/reopen across restart | API/UI persistence |
| G8 | Strict duplicate-key JSON, immutable artifact SHA/bytes, project ownership, validate-only purity, CAS/revision conflict/race, reservation foundation, safe paths | independent tests |
| G9 | Dark History-only Cockpit and seven exact stages, truthful future-stage blockers and real API states | TypeScript build, screenshots 1440x900/1920x1080 |
| G10 | Windows local launcher: fresh setup/start/restart/owned shutdown, no obsolete workers | Windows process smoke |
| G11 | Required History backend, frontend, governance, P0 regressions run on exact HEAD; retired YTB suite not required | CI |
| G12 | No internal LLM SDK/API/ChatGPT automation | static/dynamic scan |
| G13 | No root MANIFEST or generator/check workflow | tree/CI |
| G14 | P0-TRANSFER-001: rewrite README, preserve 00_START_HERE and docs/00_INDEX new authority; classification regression | docs and tests |
| G15 | P0-DEFER-P6-001 recorded in P6 with future tests; no P6 implementation | P6 plan |

Negative cases: old/foreign DB unchanged bytes+mtime; YTB routes gone; wrong stage IDs; stale head and foreign artifact rejected; duplicate keys; unsafe paths; no LLM endpoint; obsolete process absent; UI buttons disabled when no backend; no root manifest; new skills/AGENTS preserved.

### Phase-scoped finding classes
`P1_BLOCKER`, `P1_REGRESSION` → fix; `DEFER_P2`–`DEFER_P8` → phase-plan handoff with tests; `OPTIONAL_IMPROVEMENT` → backlog; `NOT_A_DEFECT` → explain. Do not reopen P0 or implement future phases without permission.

**Frozen before runtime P1 editing.** Changes require demonstrated safety/functional defect, regression, contradiction, or explicit user scope change.
