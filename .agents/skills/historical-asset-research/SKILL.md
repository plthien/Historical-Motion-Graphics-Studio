---
name: historical-asset-research
description: Curate historical photographs, portraits, maps and archival documents with source/date/rights evidence, safe retrieval, human review and immutable asset versions.
---

# Historical Asset Research Skill — NEW HISTORY RULES ONLY

## Task authority
Applies P4–P8 for historical media evidence. Read `AGENTS.md`, `docs/history/CODING_RULES.md`, current phase spec and external authoring skill when asset-plan prompt/import is involved. Old YTB Scene Assets beat-per-image logic is PROHIBITED.

## Input and classification
Approved ScriptHistoricalAnalysis, claim IDs and event/period/geography targets; manual External ChatGPT `AssetResearchPlan.v1` JSON with search leads but no automatic factual authority. Classify each source as `authentic archival photograph`, `historical document/map`, `modern illustrative photo`, `derived diagram / schematic`, or `fictional/generated reconstruction`. Do not imply non-authentic media is archival truth.

## Procedure
1. Build/import External ChatGPT asset-research prompt/JSON; validate URLs/claim links, leave candidates UNTRUSTED.
2. Use separately authorized **non-LLM** archive metadata/search endpoints (Wikimedia Commons, Library of Congress) or manual uploads; source-page item ID, creator, year/period, rights/licensing text, attribution, historical relevance and geography must be reviewed.
3. Download only approved HTTPS allowlist, reject redirect/DNS SSRF/internal/private/link-local IP, unsupported MIME/SVG scripting, massive inputs, decompression bombs, symlink/path escapes and malware-like containers.
4. Store original bytes SHA256 in fresh history-only content-addressed path, record immutable file version, MIME/dimensions, source URL, author, date, rights evidence, reviewer and claim/event bindings.
5. Local non-destructive edits (alpha cutout, crop, texture, contrast) create NEW derived versions with parent hash and transform metadata; never overwrite original.
6. Approve a snapshot of exact asset versions for rendering. Reject a map not supported for claimed period or attach conspicuous “SƠ ĐỒ MINH HỌA” label. Never auto-approve based solely on ChatGPT web citations.

## Exit tests
At least eight source-verified diverse sample assets for pilot, proper rights/attribution and actual hashes; wrong period map, missing license/author, 403/redirect anomalies, HTML masquerading as image, cross-project reference, stale hash, symlink/SSRF/media-limit attacks all handled with deterministic errors. No mandatory one image per Beat, no LLM API.

## Output
Versioned ApprovedAssetManifest as PROJECT DATA (permitted)—NOT prohibited root repository `MANIFEST.json` or release manifest generation workflow.
