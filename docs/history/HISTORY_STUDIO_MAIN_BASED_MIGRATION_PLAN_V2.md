# Historical Motion Graphics Studio — Superseded plan v2

**STATUS: SUPERSEDED / DO NOT IMPLEMENT FROM THIS DOCUMENT.**

The user has imposed a stricter requirement: the new product must contain **zero old YouTube Studio support, zero old production data and zero old databases**. The earlier v2 implementation sequence was incorrect because it delayed the legacy cutover to P8.

**Use [HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md](HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md) as the sole authoritative current implementation plan.** Under v3, P1 performs the complete history-only source/UI/API/database clean-cut BEFORE P2 and all later features. No legacy compatibility routes, old schemas, old data paths or migration readers survive P1. P8 is QC/release only.

For factual code locations and the migration ledger starting point, see [MAIN_CODEBASE_AUDIT_2026-10-15.md](MAIN_CODEBASE_AUDIT_2026-10-15.md). This is a **source-audit** document, not permission to preserve the legacy runtime.
