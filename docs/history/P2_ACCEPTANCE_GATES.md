# Frozen P2 Acceptance Gates — 2026-10-16

**Base:** explicit `main` at `64a5cc7b6476f474a5b0296e45b413e5980f722c`. **Branch:** `feat/p2-history-topic-script-authoring`. **Authority:** AGENTS.md → History Coding Rules → Strict Clean-Cut v3 → P2 phase → only NEW skills. Frozen before P2 implementation. Do not append unrelated gates.

| Gate | Required P2 result | Evidence |
|---|---|---|
| G1 | Exact main-derived branch, Draft PR, new governance only | SHA/PR + governance |
| G2 | No LLM SDK, provider, local model or ChatGPT automation | source/runtime scan |
| G3 | P1 History Channel/Project storage/UI and no-legacy safety preserved | restart/foreign DB regression |
| G4 | Persisted versioned validated HistoryChannelDNA and source fingerprint | migration + API tests |
| G5 | Real Topic prompt reservation, copy/download, GET reload | API/UI |
| G6 | Strict TopicIdeas JSON, validate-only read-only, imported candidate + review | negative tests |
| G7 | Imported/manual explicit Topic approval, immutable version/CAS and history | concurrent/stale tests |
| G8 | Script prompt from currently approved Topic and Channel DNA | source lineage |
| G9 | Exact full_script/section text, ordered sections, claims, verification nuance and IDs | schema/negative tests |
| G10 | Script candidate reviewed then CAS atomically publishes immutable Script+analysis | DB/file failure + race |
| G11 | Reload/restart, source invalidation, no deleted old bytes | end-to-end |
| G12 | Real Topic and Script workspaces, UI/API parity, file picker/errors at 1440×900 and 1920×1080 | Playwright |
| G13 | Strict security negatives + P1/P0 regression, cross-platform exact-head CI | pytest/Node/Windows |
| G14 | No P3–P8 implementation, documented deferrals in owner phase plans | diff/scan |

## Mandatory negative cases
Duplicate nested JSON keys; unknown/missing fields; wrong type/enum/ID/claim reference/UTF-8; >1MiB import; reservation replay/expired/rebuilt/cross-project; stale topic and ChannelDNA; cross-project approval, CAS race, storage failure, invalid section/quote/claim; approved bytes unchanged; old/foreign database untouched; missing future-stage service disabled.

## Phase-scoped audit decisions
`P2_BLOCKER` and `P2_REGRESSION` fixed before PASS. `DEFER_P3`–`DEFER_P8` updated in those plans with test/dependency. `OPTIONAL_IMPROVEMENT` recorded, `NOT_A_DEFECT` explained. Only user-requested change, concrete security/data-integrity defect, P2 contract contradiction, or accepted behavior regression may change these frozen gates.

**Do not begin P3, merge PR, or reopen accepted P0/P1 without proven regression.**
