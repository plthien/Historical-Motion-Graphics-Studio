# Historical Motion Graphics Studio — CODING RULES v1

**Effective 2026-10-15. This is the ONLY active coding rulebook in the repository.**
Authority chain: root `AGENTS.md` → THIS FILE → `HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md` → relevant `phases/P<n>_*.md` → applicable **NEW** skill(s) in `.agents/skills/`.
The deleted `docs/32_CODING_RULES.md` and ALL YouTube AI Studio/YTB V4 rules, prompts, old coding standards, source-of-truth guides, manifests and legacy instructions are **not permitted implementation authority**. If they disagree, ignore the legacy instruction and record the issue in the PR.

## R01. Scope and Git discipline
- Work ONLY in `plthien/Historical-Motion-Graphics-Studio`; inspect actual current `main` SHA explicitly, because GitHub default branch may differ.
- Never push directly to main, merge PRs, reset branches, publish release or close PRs unless user expressly requests it. Branch per phase, reviewable PR, exact SHA tests.
- Read mandatory first-read set in `AGENTS.md`. No coding before phase contract, source/import graph and acceptance tests are identified.
- Treat current YTB Studio source as donor only. It is not an alternate production mode or a source of coding rules.

## R02. Early clean cut and data isolation
- P1 MUST atomically remove the old YTB product runtime **before P2**; not merely hide old UI or disable features. Delete old Scene/Beat/VisualReference/SceneAsset/PromptBundle/Assembly/FFmpeg slideshow implementations, routes, domain schemas, migrations, old provider code and obsolete tests from the active tool.
- No compatibility readers, flags, old YTB API aliases, dual DB migrations, data backfills, artifact converts or old-project reopening.
- New History SQLite schema and migration initial head, storage root and domain artifact types. Never load or migrate old YTB database/media/projects. Fail closed on foreign schema without touching legacy bytes; never delete user-controlled legacy files.
- Neutral utilities (JSON strict parsing, hashes, CAS, supervised processes, Kokoro/ElevenLabs TTS) may be EXTRACTED and independently tested, not preserved as old coupled packages.

## R03. Absolute no-LLM-API rule
- No OpenAI/Anthropic/Gemini/chat-completions/agent API or local language model runtime in the app, even for repairs, fact-check, retry or thumbnail text. Never automate a ChatGPT browser session from the Studio.
- External ChatGPT workflow ONLY: deterministic prompt package with exact input/schema/source IDs → user runs ChatGPT manually → downloaded JSON file import → strict validate-only → candidate import → review → guarded approval. Repairs only create copyable/downloadable external prompts.
- Separately approved **non-LLM** APIs may retrieve archive metadata or perform TTS/real forced alignment; they cannot become hidden language-model providers.

## R04. Contract, lifecycle, CAS and source lineage
- Schemas in `contracts/history/` are strict/versioned: reject unknown keys, duplicate JSON keys, wrong/missing/foreign IDs, stale source fingerprint, invalid references, malformed payloads and arbitrary file or network paths.
- Validate-only has no DB or file mutation. Import revalidates. Approve publishes immutable bytes/artifact hash via CAS. Every read checks project/channel ownership, approval status, current head and parent lineage. No speculative "repair" of model JSON.
- One domain head per stage; dependency invalidation computed from exact hashes, cycle-safe and atomic. Changes to a layer do NOT regenerate script/voice; new exact script/audio invalidates timing and relevant descendants.
- API schemas, DB models, OpenAPI and UI DTOs must agree. No fake controls, mock success messages or unauthorized fallback routes.

## R05. Real voice timing
- Exact approved Script text alone feeds Kokoro or explicit ElevenLabs synthesis. Preserve provider/runtime/voice/calibration identity, final WAV SHA and sample counts, bounded retries/cancel.
- Only real forced alignment of the FINAL WAV produces approved WordTimingMap. NO WPM/section-estimate pseudo-aligner as final source.
- Validate monotonic real word/phrase offsets, coverage, source hash, overflow and identity; source edits invalidate dependent CompiledTimeline and RenderOutput.
- Real external TTS/forced-alignment service calls are optional and auditable; don't pretend integration succeeded if credentials/assets unavailable.

## R06. Historical evidence/media/rights
- A ChatGPT-proposed URL/source/license is untrusted. Approved assets require immutable SHA256, source record/creator/date/place/period, rights evidence/attribution, role and explicit review.
- Media download allowlist, HTTPS, SSRF/private-IP/redirect checks, path containment, file-size/MIME/decode bombs and reject executable SVG/user script.
- Differentiate archive image vs recreation/illustration vs SCHEMATIC map; verify era-specific borders and historical claim link or visibly label schematic.
- Asset library is **not** "one generated image per beat". No mandatory image-provider call per script sentence.

## R07. Remotion-only visual composition and deterministic timeline
- `VisualSetup` persists across multiple sentences/ideas; independent Layer and MotionEvent objects enter/move/hold/exit with explicit z-order/continuity.
- External model imports DATA only (approved component IDs, presets and bounded transforms), never arbitrary JSX/JS/CSS or URL. Compiler resolves actual word/phrase anchors to INTEGER frames and validated local approved asset references.
- One identical Remotion React composition is used by `@remotion/player` and final Node Remotion renderer. No alternate FFmpeg still-frame montage or fake DOM preview.
- Reproducible, version-matched pinned Remotion dependencies + lockfile. Test frame boundaries, animation holds, layout overflow, text glyphs, audio and output FFprobe; no style-parity claims without images.

## R08. Jobs, Windows host and resource ownership
- Durable job submit persists queue/intent first and returns; GET only reads state, does not dispatch. SQLite lease/owner/attempt fencing prevents stale status/progress/final-video publication.
- IO/network/render outside SQLite write transaction. Secure local Node argv/paths, bounded stdout/stderr, timeouts, disk and concurrency.
- Windows Job Object owns Chrome/Node/FFmpeg process children with kill-on-close; cancel, crash, timeout, restart, retry and ambiguous submissions require adversarial tests. No reintroduction of obsolete YTB workers.

## R09. UI, phase gates and quality
- Preserve neutral Cockpit layout/theme/navigation and Channel/Project context, NOT old Stage-specific Scene UI.
- Seven History stages with real BE readiness and precise blockers; components only enabled when API exists. Virtualize large asset/timeline collections.
- Per-phase PR must demonstrate positive, negative, security and regression checks appropriate to touched code; Windows/headless Player/render/FFprobe and visual comparisons when phase requires them.
- No manifest-based "success" check. Root `MANIFEST.json`, its generator, CI verification or auto-commit workflow are prohibited. Project-specific asset attribution and render metadata remain required.
- Phase status = PASS only with exact-SHA evidence for ALL required gates; otherwise PARTIAL/BLOCKED, never unverified PASS.

## R10. Mandatory NEW skill and agent routing
- Before touching a phase, read `.agents/skills/README.md`, then only relevant current `.agents/skills/<slug>/SKILL.md` as mapped in `docs/history/agents/README.md`.
- Use agent role documentation for review checklists only; these are project roles, not permission for autonomous unreviewed writes.
- **NEVER consult or follow skills/rules in legacy YouTube Studio docs, cached developer prompts or the old `docs/32_CODING_RULES.md`.** They may be inspected as historical evidence of what to remove, never as work instructions.
- New skills/rules changes require their own review and must update the skill routing/index, relevant tests and coding-governance checks.

## Final required handoff
Report `repository, phase, base SHA, head SHA, modified/deleted files, contract changes, validation, test command/outcome, visual/Windows proof, old-runtime/LLM closure and blockers, PR URL`. Never auto-merge.
