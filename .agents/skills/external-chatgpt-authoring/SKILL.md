---
name: external-chatgpt-authoring
description: Build manual External ChatGPT prompt packages and safely import, validate, review and approve typed JSON artifacts with reservations and source lineage, never invoking an LLM API.
---

# External ChatGPT Authoring Skill — NEW HISTORY RULES ONLY

## When to use
P2 Topic/Script & Historical Analysis, P4 asset research plan, P5 Visual Director and targeted repair prompts. First read `AGENTS.md`, `docs/history/CODING_RULES.md`, current phase plan. **Only the new skills apply**, never legacy YTB prompt/authoring rule files.

## Inputs
Approved immutable channel/project/stage parent artifacts, actual source hashes, history ChannelDNA, target schema version, exact allowed IDs, applicable historical fact/reconstruction rules, current content fingerprints and file download instructions.

## Six-step deterministic workflow
1. **Reserve**: server allocates explicit artifact ID(s), reservation ID and frozen source fingerprint for one project+stage; store pending prompt package with expiry/rebuild revision and bound parent versions.
2. **Build Prompt**: deterministically bundle meaningful human-readable inputs (not opaque labels), task, EXACT approved text (when required), historical uncertainty, explicit strict output JSON Schema, stable IDs, lineage and “download a UTF-8 JSON file”. Avoid memory-only context or invisible data.
3. **External handoff**: show Copy/Download Prompt; user manually runs External ChatGPT and retrieves JSON. NEVER call ChatGPT or any model API/service from the Studio, even on failure.
4. **Validate-only**: parse with duplicate JSON key rejection, UTF-8 and size limits, strict unknown key rejection, type/semantic check, source/foreign-project ID/hash checks and historical claim/asset/cue reference validation. MUST perform zero DB/artifact side effects.
5. **Import + Review**: independently re-run all checks and save one candidate with immutable bytes/hash, reference/rights/reconstruction uncertainty warnings. User chooses approve or reject.
6. **Approve**: compare-and-set current parent heads, reservation and candidate version; atomically publish approval with immutable lineage. On failure, keep prior approved head untouched; produce targeted repair prompt listing only invalid paths without mutating locked successes.

## API/UI
Every enabled button Build, Copy, Download, Import JSON, Validate, Review, Approve, Build Repair Prompt has an actual typed endpoint/effect or host copy/download. Source/fingerprint mismatch displayed plainly; imports survive restart. No “Generate via AI” background button. Store no secret ChatGPT session cookies.

## Required tests
Duplicate keys, deeply null/array payload, bad enum/unknown fields, malicious file path, source stale between prompt and import, reused/expired reservation, cross-project IDs, concurrent approvals/second imports, huge JSON, wrong exact spoken quote, incomplete claims, zero-mutation validate-only; restart and UI/API parity. Simulate all with no LLM network and prove model SDK/endpoint absence.

## Output/gate
Immutable approved stage head, prompt package/hash, validation report and targeted repair instructions; if external JSON absent or inconsistent → BLOCKED, not fabricated.
