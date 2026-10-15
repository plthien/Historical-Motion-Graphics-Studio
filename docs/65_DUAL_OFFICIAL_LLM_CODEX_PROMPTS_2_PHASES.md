# Codex Implementation Prompts — Dual Official LLM Providers (2 Phases)

## 1. Usage

Copy both files into the repository:

```text
docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md
docs/65_DUAL_OFFICIAL_LLM_CODEX_PROMPTS_2_PHASES.md
```

Run the prompts in this order:

```text
Phase 1 Prompt
Phase 2 Prompt
```

The implementation plan is the source of truth.

Every phase must read the plan before editing code.

If the plan is missing at the exact path below, stop:

```text
docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md
```

When a prompt and the plan appear inconsistent, follow the plan.

Baseline is the current repository extracted from:

```text
youtube_ai_studio_v3
```

Do not add tasks to prove the source is pre-KIE or to scan for KIE references.

---

# Prompt — Phase 1

```text
You are the senior backend engineer, frontend engineer, contract engineer, and QA engineer responsible for Phase 1 of the dual official LLM provider implementation.

BASELINE

Use the current repository extracted from:

youtube_ai_studio_v3

Do not perform source pre-KIE verification.
Do not perform KIE code/config/docs scanning.

MANDATORY PLAN

Before inspecting or modifying implementation code, read the complete file:

docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md

This file is the implementation source of truth.

If it does not exist at that exact path, stop and report the missing plan.
Do not reconstruct the plan from this prompt.
If this prompt conflicts with the plan, follow the plan.

MANDATORY PROJECT DOCUMENTS

Read:

- 00_START_HERE.md
- docs/32_CODING_RULES.md
- contracts/README.md
- docs/58_TASK10_LEGACY_REMOVAL.md
- docs/10_IMPLEMENTATION_PHASE_PLAN.md
- docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md

OBJECTIVE

Implement Phase 1 from the plan:

- official OpenAI provider support;
- official Anthropic provider support;
- one LLMGateway;
- provider/model selection per active LLM stage;
- provider-level credentials;
- OpenAI and Anthropic provider cards;
- redesigned stage settings with provider and model only;
- backend contracts;
- static OpenAPI;
- frontend API types;
- targeted tests and frontend build.

SCOPE CONTROL

Focus only on the main LLM runtime, settings, contracts, frontend Settings screen, and tests required by Phase 1.

Do not redesign:

- prompts;
- canonical business schemas;
- business stages;
- validators;
- artifacts;
- persistence;
- Scene Breakdown;
- Visual Planner;
- Prompt Composer or Prompt Bundle;
- asset, TTS, media, assembly, or render flow.

Do not implement:

- KIE;
- compatible providers;
- custom base URLs;
- automatic fallback;
- calling both providers for one request;
- provider benchmarking;
- dynamic plugin architecture;
- cost optimization;
- database migration or backfill;
- compatibility for old per-stage API-key settings;
- unrelated cleanup.

REQUIRED IMPLEMENTATION FLOW

1. Inspect the existing gateway, provider boundary, OpenAI implementation, structured-output runner, settings backend, settings routes, static OpenAPI, frontend Settings page, frontend API types, and active LLM stage callers.

2. Preserve the current official OpenAI implementation when it already works. Make only changes required to fit the final generic provider contract and new settings model.

3. Add one official Anthropic Messages provider:
   - POST https://api.anthropic.com/v1/messages
   - x-api-key authentication
   - anthropic-version: 2023-06-01
   - top-level system prompt
   - user prompt in messages
   - JSON structured output through output_config.format
   - normalize JSON text into the existing LLMResponse
   - model claude-sonnet-5
   - do not expose temperature, top_p, or top_k.

4. Keep exactly two active provider IDs:
   - openai
   - anthropic

5. Implement a small backend-owned model catalog.
   - Keep the supported OpenAI model or models from the baseline.
   - Add claude-sonnet-5.
   - Validate that model belongs to provider.
   - Do not fetch the full vendor model list for the UI.

6. Update LLMGateway:
   - resolve provider and model from stage settings;
   - pin provider and model before the first attempt;
   - reuse them for HTTP retry, structured-output retry, and semantic repair;
   - no fallback;
   - no provider inference from model name;
   - no provider branch in business stage code.

7. Redesign settings backend:
   - separate OpenAI credential;
   - separate Anthropic credential;
   - provider configured state;
   - provider connection test;
   - stage stores provider and model only;
   - reject API keys in stage settings;
   - reject unknown provider/model pairs;
   - execution preflight when the selected provider has no configured key;
   - never return raw API keys.

8. Connection tests must use the production provider adapters and a tiny structured-output schema.
   Do not create a separate vendor HTTP client path just for testing keys.

9. Redesign the frontend Settings screen:

   AI Providers
   - OpenAI API card
   - Anthropic Claude API card

   Each card:
   - key configured state;
   - save/update key;
   - Test Connection;
   - concise result;
   - never display the saved raw key.

   Stage Models
   - show every active LLM-backed stage;
   - provider dropdown;
   - model dropdown;
   - model filtered by provider;
   - reset invalid model when provider changes;
   - warning when provider key is not configured;
   - no API key, base URL, temperature, retry, or fallback fields.

10. Synchronize:
    - runtime backend schemas;
    - static OpenAPI;
    - frontend API types;
    - frontend fixtures;
    - Settings UI.

11. Add or update targeted tests required by Phase 1 in the plan.

REQUIRED VALIDATION

Run the applicable commands defined by the repository for:

- backend provider tests;
- gateway tests;
- settings API tests;
- contract validation;
- runtime/static OpenAPI parity;
- frontend Settings tests;
- frontend build.

Use fake transports for deterministic provider tests.
Run real connection tests only when credentials are available.
Do not claim a real provider passed without a real successful request.

Do not use node_modules cleanup, local database cleanup, logs, caches, or generated artifacts as acceptance gates.

PHASE REPORT

Create a Phase 1 report in the docs directory using the repository naming convention.

The report must include:

1. Files and functions changed.
2. Final provider architecture.
3. OpenAI request/response path.
4. Anthropic request/response path.
5. Settings contract before and after.
6. Provider credential storage and redaction.
7. Settings UI before and after.
8. Active LLM stage list.
9. Tests run with exact results.
10. Real OpenAI status.
11. Real Anthropic status.
12. Remaining blockers.
13. Every Phase 1 acceptance gate copied from the plan and marked:
    - PASS
    - FAIL
    - NOT VERIFIED

DEFINITION OF DONE

Phase 1 is complete only when the Phase 1 acceptance gates in:

docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md

have been evaluated honestly.

Stop after Phase 1.
Do not begin Phase 2 verification work beyond the targeted tests needed to complete Phase 1.
```

---

# Prompt — Phase 2

```text
You are the QA lead, release auditor, backend engineer, and frontend engineer responsible for Phase 2 of the dual official LLM provider implementation.

MANDATORY PLAN

Before inspecting or modifying implementation code, read the complete file:

docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md

This file is the implementation source of truth.

If it does not exist at that exact path, stop and report the missing plan.
Do not reconstruct the plan from this prompt.
If this prompt conflicts with the plan, follow the plan.

MANDATORY INPUTS

Read:

- 00_START_HERE.md
- docs/32_CODING_RULES.md
- contracts/README.md
- docs/58_TASK10_LEGACY_REMOVAL.md
- docs/10_IMPLEMENTATION_PHASE_PLAN.md
- docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md
- the Phase 1 implementation report
- the source and tests produced by Phase 1

PRECONDITION

Do not start Phase 2 if Phase 1 has unresolved FAIL gates that block the main runtime, settings contracts, provider adapters, or frontend build.

If Phase 1 has NOT VERIFIED real-provider gates because credentials were unavailable, continue fake production-path verification but do not issue final production approval for that provider without real verification.

OBJECTIVE

Execute Phase 2 from the plan:

- verify every active LLM stage with OpenAI;
- verify every active LLM stage with Anthropic;
- verify mixed-provider stage routing;
- run full vertical slices;
- verify Settings end to end;
- run regressions;
- issue APPROVE or REJECT.

SCOPE CONTROL

Phase 2 is verification and defect correction.

Do not redesign:

- provider architecture;
- settings architecture;
- prompts;
- canonical business schemas;
- business stages;
- validators;
- artifacts;
- persistence;
- Scene Breakdown;
- Visual Planner;
- Prompt Composer or Prompt Bundle.

Do not add:

- fallback;
- dual execution;
- automatic best-model selection;
- provider benchmarking;
- dynamic providers;
- custom base URLs;
- KIE;
- unrelated cleanup.

REQUIRED WORK

1. Identify every active LLM-backed operation from the actual source.

   At minimum, verify when active:
   - Topic Ideas;
   - Script Generation;
   - Research Draft;
   - Fact-check and Rewrite;
   - Script Analysis;
   - Scene Breakdown;
   - Scene Breakdown retry or repair;
   - Visual Planner;
   - Visual Planner retry or failure handling;
   - Production Plan when it has an active caller.

2. Build or complete fake production-path tests for every active stage and both providers.

   Required path:

   stage worker or service
   → stage settings resolver
   → LLMGateway
   → selected provider adapter
   → normalized LLMResponse
   → structured-output runner
   → canonical schema validation
   → semantic validation
   → persistence and downstream

   Do not mock past the gateway or provider adapter.

3. For every active stage and each provider, cover:
   - valid structured output;
   - malformed provider response;
   - canonical-invalid output;
   - semantic-invalid output;
   - refusal;
   - truncation;
   - authentication failure;
   - rate limit;
   - provider unavailable;
   - retry remains on the originally selected provider;
   - no duplicate provider execution;
   - no partial-success artifact.

4. Verify provider pinning:
   - initial request;
   - HTTP retry;
   - structured-output retry;
   - semantic repair;
   must all use the same provider and model selected at operation start.

5. Verify Settings end to end:
   - save OpenAI key;
   - test OpenAI;
   - select OpenAI for a stage;
   - stage uses OpenAI;
   - save Anthropic key;
   - test Anthropic;
   - select Anthropic for a stage;
   - stage uses Anthropic;
   - missing provider key fails execution preflight;
   - invalid provider/model pair is rejected;
   - provider change resets invalid model;
   - raw keys are never returned or rendered.

6. When real credentials are available:
   - run OpenAI connection test;
   - run Anthropic connection test;
   - run representative structured-output requests;
   - run a small OpenAI-only vertical slice;
   - run a small Anthropic-only vertical slice;
   - run a small mixed-provider vertical slice.

7. Run the main business flow:

   Topic
   → Script
   → Research or Fact-check when enabled
   → Script Analysis
   → Scene Breakdown
   → Style Bible
   → Visual Planner
   → Prompt Composer
   → Prompt Bundle

   Required configurations:
   - OpenAI-only;
   - Anthropic-only;
   - mixed-provider.

   Mixed-provider routing is not fallback.

8. Run applicable regressions:
   - provider tests;
   - gateway tests;
   - settings API tests;
   - contract validation;
   - runtime/static OpenAPI parity;
   - frontend Settings tests;
   - frontend build;
   - all active LLM stage tests;
   - Scene Breakdown regressions;
   - Visual Planner regressions;
   - full main-flow tests.

9. Fix only defects directly related to this implementation.
   Do not expand scope to unrelated refactoring.

PHASE REPORT

Create a Phase 2 final audit report in the docs directory using the repository naming convention.

The report must include:

1. Rule-compliance snapshot.
2. Final architecture.
3. Active LLM stage matrix.
4. OpenAI fake production-path results.
5. Anthropic fake production-path results.
6. Provider-pinning and retry results.
7. Settings end-to-end results.
8. OpenAI-only vertical-slice result.
9. Anthropic-only vertical-slice result.
10. Mixed-provider vertical-slice result.
11. Real OpenAI verification status.
12. Real Anthropic verification status.
13. Scene Breakdown regression result.
14. Visual Planner regression result.
15. Backend tests.
16. Contract and OpenAPI parity.
17. Frontend tests and build.
18. Remaining operational risks.
19. Every Phase 2 acceptance gate copied from the plan and marked:
    - PASS
    - FAIL
    - NOT VERIFIED
20. Final recommendation:
    - APPROVE
    - REJECT

APPROVAL RULE

Do not approve when:

- any required main-flow gate fails;
- provider/model changes during retry;
- fallback or duplicate execution exists;
- prompts or canonical business schemas were changed;
- Settings still place API keys inside stages;
- raw API keys can be returned or rendered;
- a required real provider remains unverified.

Stop after the final recommendation.
Do not start unrelated phases or features.
```
