# External Authoring Implementation Plan

1. Keep reservation-backed external prompt/validate/import for Topic Ideas, combined Script & Analysis, and full-Script Scene Breakdown.
2. Keep deterministic Style Bible and current Visual Reference webhook/approval architecture.
3. Replace the Scene Breakdown visual union with one strict image-design object per beat; remove every asset classification field and compatibility path.
4. Upgrade Visual Plan to 3.0 and Prompt Bundle to 3.0. Compile exactly one prompt for every beat with no exclusions.
5. Refactor Scene Breakdown approval to stage and publish approved Scene Plan, approved Visual Plan, and approved Prompt Bundle in one CAS transaction.
6. Keep Prompt Bundle as the immediate approved input to Scene Assets, but remove it from Studio navigation and manual Studio workflow.
7. Move aspect-ratio selection to Scene Breakdown approval and hydrate it from the current Prompt Bundle when available.
8. Replace Scene Assets branches with one media-provider generation/review path for every beat; remove local graphic rendering, existing-asset binding, and transition-only resolution.
9. Remove classification from Assembly, frame quantization, and render graph contracts.
10. Run contract, regression, clean-database, OpenAPI, closure, manifest, and clean-candidate release gates.

No compatibility reader, fallback provider execution, backfill, data migration, or legacy artifact conversion is supported.
