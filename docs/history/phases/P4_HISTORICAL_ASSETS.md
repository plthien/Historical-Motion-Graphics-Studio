# P4 — Historical asset research, provenance library and versioned media

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no local LLM, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Prerequisites
P2 exact approved script and history claim IDs; P1 history-only product. P3 voice can run in parallel, but P5 waits for both final timings and asset approvals.

## Contracts
- `AssetResearchPlan.v1`: External ChatGPT output with reserved artifact ID and script fingerprint, `search_tasks[]` (claim/event/entity ID, period, location, search query, desired visual role, archive type, candidate URLs, rights/relevance review questions). No candidate URL is trusted/approved.
- `EvidenceAsset.v1`: asset_id, immutable version_id, sha256 bytes, original local relative path, media MIME/dimensions/duration, normalized creator/title/archival_item_id/source URL/date/period/geography, license type/evidence URL/attribution, credibility classification `archive|modern_document|schematic|reconstruction|generated_illustration`, approval status and reviewer notes.
- `ApprovedAssetManifest.v1`: approved snapshot ID, source approved script/claim IDs, unique asset-version bindings, exact content hashes, rights evidence/attribution. This domain manifest is a **project artifact**, NOT the prohibited root release MANIFEST.json.
- `AssetDerivative.v1`: original version hash, crop/alpha/background removal/degradation treatment, output hash and transformation policy. Derivative doesn't erase original provenance.

## New code files and storage
- `app/history/assets/{research_authoring,library,archive_adapters,download_guard,metadata,derivatives,review,service}.py`, `app/history/api/assets.py`; `src/features/history/assets/{AssetLibrary,SourceInspector,ResearchPromptWorkspace,AssetBindingEditor}.tsx`; contracts and tests/history/assets.
- Fresh DB `assets`, `asset_versions`, `project_asset_bindings`, optionally `asset_reviews`; immutable local asset bytes in history_data/assets/<hash>/; no old scene asset tables or cached visuals.
- Initial approved archives: Wikimedia Commons and Library of Congress (non-LLM metadata/search only). Always expose image's item page/source verification; adapt endpoint pagination and rights statements without assuming all public images are free.
- Manual user upload of owned/licensed source, optional local crop/contrast/transparent cutout processing; configurable strict size/dimension limits and safe MIME decoding.

## API and UI mapping
- POST/GET `.../assets/research/prompt`, POST `.../assets/research/import/validate`, POST `.../assets/research/import`, POST `.../assets/research/approve`.
- POST `.../assets/search` (only approved archive metadata), POST `.../assets/upload`, POST `.../assets/download` (whitelisted URL) and GET `.../assets/{assetId}/versions`.
- POST `.../assets/{assetId}/review`, POST `.../assets/{assetId}/derivatives`, POST `.../assets/approve-manifest`, GET `.../assets/current-manifest`.
- UI columns: preview thumbnail, historical subject, source, creator/date, license+evidence, archive item, period/geography relevance, linkage to claim, approval state, selected version/derivatives. Filters for ready/unverified/rejected and asset roles; bulk workflow must not implicitly approve missing rights.

## Implementation order
1. Freeze schemas/strict external ChatGPT research authoring and cite/source-fingerprint validation.
2. Implement candidate intake and source-provenance review DB. Research plan imports don't fetch blindly.
3. Add manual upload with file bytes SHA256/MIME/image decode/size bounds and local content-addressed storage.
4. Add allowlisted archive adapters with HTTPS, resolved IP/redirect validation, private DNS/loopback/link-local rejection, bounded responses and archive item verification.
5. Implement asset derivatives (cropped photo, portrait cutout, paper texture) as immutable versioned child media, source hash preserved.
6. Implement period-map review and schematic-label requirement; asset review/approval with CAS and known claim linkage.
7. Wire library UI + select/bind/approve snapshot and tests, then verify actual sample rights-cleared assets.

## Negative tests and delivery gate
- SSRF through redirect or DNS rebinding, data: URL/file:, symlink escapes, decompression bomb, malicious SVG scripts, unsupported MIME, giant image, wrong rights/creator, wrong geographic/period map, stale asset version, original file tamper, cross-project binding.
- Content hash and source references frozen; unapproved image cannot enter final render. Generated images labeled illustrations, NEVER genuine archive.
- Demonstrate a curated library of >=8 actual verified licensed/public-domain assets for pilot, including real period-safe map or clearly labeled schematic; all hashes/attributions checked, UI source inspector accurate.
- No arbitrary LLM API, no old SceneAssets provider mandatory beat-per-image, no root release manifest generation/check.
