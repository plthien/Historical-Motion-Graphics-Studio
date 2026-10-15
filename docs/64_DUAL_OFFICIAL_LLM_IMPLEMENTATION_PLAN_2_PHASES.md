# Dual Official LLM Providers — Implementation Plan (2 Phases)

## 1. Status

This document is the single source of truth for implementing dual official LLM providers in YouTube AI Studio.

Repository destination:

```text
docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md
```

Baseline:

```text
youtube_ai_studio_v3

Assume the current repository was extracted from this baseline.

Do not add work to:

- prove whether the source is pre-KIE;
- scan for KIE code, configuration, or documentation;
- clean unrelated files or generated data.

---

## 2. Goal

Support two official LLM providers in one existing business pipeline:

- OpenAI API;
- Anthropic Claude API.

Provider credentials are configured once on provider cards.

Each active LLM stage stores only:

- provider;
- model.

The implementation must preserve the current business flow, prompts, canonical schemas, validators, artifacts, persistence, and downstream behavior.

---

## 3. Target Architecture

```text
Business stage
→ Stage LLM settings resolver
→ LLMGateway
→ selected official provider adapter
   ├── OpenAI
   └── Anthropic
→ normalized LLMResponse
→ existing structured-output runner
→ canonical schema validation
→ semantic validation
→ persistence and downstream
```

Rules:

1. One business pipeline.
2. One provider selected per stage operation.
3. The selected provider remains fixed for all retries and repair attempts of that operation.
4. No automatic provider fallback.
5. Do not call both providers for one business request.
6. Do not infer provider from the model name.
7. No provider-specific logic inside business stage workers or services.
8. No custom base URL or compatible-provider mode.

---

## 4. Business Invariants

Do not redesign or rewrite:

- system prompts;
- user prompts;
- canonical response schemas;
- stage ownership;
- stage workers and services;
- structured-output runner behavior;
- semantic validators;
- artifact contracts;
- persistence;
- Scene Breakdown business rules;
- Visual Planner business rules;
- Prompt Composer or Prompt Bundle;
- media, TTS, assembly, or render flow.

Provider adapters may project a canonical schema into the provider-supported structured-output shape, but the original canonical schema remains the final validation source.

---

## 5. Active Provider Set

The active provider IDs are exactly:

```text
openai
anthropic
```

Do not add:

- KIE;
- OpenAI-compatible;
- Anthropic-compatible;
- DeepSeek;
- automatic provider fallback;
- dynamic provider plugins;
- custom provider URLs.

### OpenAI

Preserve the current official OpenAI implementation if it already satisfies the generic LLM contract.

Do not migrate OpenAI endpoints or rewrite working OpenAI code without a concrete requirement.

The OpenAI adapter must:

- receive the generic `LLMRequest`;
- preserve the original system and user prompt content;
- use official OpenAI structured JSON output supported by the selected model;
- normalize the result into the existing `LLMResponse`;
- keep provider metadata outside business artifacts.

### Anthropic

Add one official Anthropic Messages adapter.

Endpoint:

```text
POST https://api.anthropic.com/v1/messages
```

Headers:

```text
x-api-key: <ANTHROPIC_API_KEY>
anthropic-version: 2023-06-01
content-type: application/json
```

Main mapping:

```text
LLMRequest.system_prompt  → top-level system
LLMRequest.user_prompt    → messages[].content
LLMRequest.model          → model
max output tokens         → max_tokens
projected response schema → output_config.format
```

Use JSON structured output through `output_config.format`.

Do not implement a fake `submit_structured_output` tool unless a verified project constraint proves JSON structured output cannot satisfy the existing contract.

Normalize the returned JSON text into the current `LLMResponse`, then run the existing canonical validation flow.

For Claude Sonnet 5:

```text
model ID: claude-sonnet-5
```

Do not expose `temperature`, `top_p`, or `top_k` for this model.

---

## 6. Model Catalog

The backend owns a small curated model catalog.

The frontend must not fetch the complete vendor model list and must not hard-code a separate model list.

Minimum shape:

```json
{
  "providers": [
    {
      "id": "openai",
      "display_name": "OpenAI API",
      "default_model": "<existing supported OpenAI model>",
      "models": [
        {
          "id": "<existing supported OpenAI model>",
          "display_name": "<display name>"
        }
      ]
    },
    {
      "id": "anthropic",
      "display_name": "Anthropic Claude API",
      "default_model": "claude-sonnet-5",
      "models": [
        {
          "id": "claude-sonnet-5",
          "display_name": "Claude Sonnet 5"
        }
      ]
    }
  ]
}
```

Rules:

- keep the currently supported OpenAI model or model set from the baseline;
- add only Anthropic models implemented and verified by this project;
- validate that every saved model belongs to the selected provider;
- reject unknown providers and unknown models.

---

## 7. Settings Design

The Settings screen contains two sections.

### 7.1 AI Providers

Display two provider cards:

```text
OpenAI API
- API key configured state
- Save or update API key
- Test Connection
- Connection result

Anthropic Claude API
- API key configured state
- Save or update API key
- Test Connection
- Connection result
```

Each provider card manages only provider-level credentials and connection state.

Do not place stage selection on provider cards.

The backend must never return the raw API key. It may return only safe state such as:

```json
{
  "provider": "openai",
  "api_key_configured": true,
  "connection_status": "verified"
}
```

### 7.2 Stage Models

Each active LLM stage displays exactly:

```text
Provider
Model
```

Example:

```text
Scene Breakdown

Provider: Anthropic
Model: Claude Sonnet 5
```

Do not display an API key, base URL, temperature, retry policy, or fallback setting on a stage card.

When the user changes the provider:

```text
if the current model does not belong to the new provider
→ select that provider's default model
```

The model dropdown is populated from the backend model catalog.

Show only active LLM-backed stages found in the source.

Do not show deterministic or non-LLM stages such as:

- Style Bible;
- Prompt Composer;
- Prompt Bundle;
- TTS;
- media or render stages.

---

## 8. Settings Contracts

Use existing project route and naming conventions. The exact URLs below are examples, not mandatory route names.

### Provider credential operations

```text
PUT  /settings/llm/providers/{provider}/credential
POST /settings/llm/providers/{provider}/test
GET  /settings/llm/providers
```

Credential write input contains the API key.

Credential read output must not contain the API key.

Supported secret keys:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

Use the project's existing secret-storage mechanism.

### Stage selection

Minimum stage setting:

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-5"
}
```

Validation:

1. The stage is an active LLM-backed stage.
2. The provider is `openai` or `anthropic`.
3. The model belongs to the selected provider.
4. No API key is accepted inside stage settings.
5. No custom base URL is accepted.
6. No fallback provider is accepted.

Old per-stage credential payloads do not need migration or compatibility support.

---

# Phase 1 — Implement Providers, Settings, Contracts, and UI

## 9. Phase 1 Objective

Complete the production implementation for:

- OpenAI and Anthropic official adapters;
- gateway dispatch;
- provider credential storage and connection testing;
- stage provider/model settings;
- backend contracts;
- static OpenAPI;
- frontend API types;
- redesigned Settings screen;
- targeted tests and build.

Phase 1 changes production code.

---

## 10. Phase 1 Work

### 10.1 Read Project Rules and Relevant Source

Read before editing:

```text
00_START_HERE.md
docs/32_CODING_RULES.md
contracts/README.md
docs/58_TASK10_LEGACY_REMOVAL.md
docs/10_IMPLEMENTATION_PHASE_PLAN.md
docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md
```

Inspect only relevant areas:

- `LLMGateway`;
- provider interfaces and implementations;
- `LLMRequest` and `LLMResponse`;
- structured-output runner;
- active LLM stage callers;
- settings storage and validation;
- settings API routes;
- runtime and static OpenAPI;
- frontend API types;
- Settings page and components;
- provider and settings tests.

### 10.2 Provider Adapters

Keep or minimally adjust the working official OpenAI provider.

Add the official Anthropic provider.

Both adapters must:

- implement the same generic provider interface;
- preserve original prompt content;
- use provider-native structured output;
- normalize response text and metadata;
- reject malformed, incomplete, refused, or truncated responses;
- redact secrets from logs and errors;
- classify authentication, billing, rate-limit, timeout, invalid-model, provider-unavailable, and invalid-response failures.

Do not duplicate business validation in provider adapters.

### 10.3 Gateway and Retry Pinning

`LLMGateway` resolves the provider from stage settings.

The selected provider and model must be pinned to the operation context before the first provider call.

All later attempts for that operation must reuse the same provider and model, including:

- HTTP retries;
- structured-output retries;
- semantic repair retries.

Do not add fallback logic.

### 10.4 Backend Settings

Implement:

- provider model catalog;
- separate OpenAI credential state;
- separate Anthropic credential state;
- credential save/update;
- provider connection test;
- stage provider/model selection;
- validation that a model belongs to its provider;
- execution preflight when the selected provider has no configured credential.

A stage may be saved with an unconfigured provider, but the UI must warn and execution must fail clearly before calling the provider.

### 10.5 Connection Tests

Connection tests must use the production adapter implementation.

Flow:

```text
Settings API
→ selected provider adapter
→ tiny structured-output request
→ parse JSON
→ validate a tiny canonical schema
→ return safe connection result
```

Do not create a separate vendor HTTP path that bypasses the production adapter.

Minimum status values:

```text
not_configured
verified
authentication_failed
billing_error
rate_limited
model_not_supported
provider_unavailable
structured_output_failed
```

### 10.6 Settings UI

Implement:

```text
AI Providers
├── OpenAI API card
└── Anthropic Claude API card

Stage Models
├── one card or row per active LLM stage
└── provider + model only
```

Provider cards:

- save/update key;
- show configured state without revealing the key;
- run Test Connection;
- show a concise result.

Stage cards:

- provider dropdown;
- model dropdown filtered by provider;
- reset an invalid model when provider changes;
- warn when the selected provider has no configured key;
- contain no key input.

Do not add advanced settings.

### 10.7 Contract Synchronization

Update together:

- runtime backend schemas;
- static OpenAPI;
- frontend API types;
- frontend fixtures;
- Settings UI tests.

Remove per-stage API-key fields from active contracts.

### 10.8 Phase 1 Tests

Backend tests must cover:

- OpenAI adapter request and response normalization;
- Anthropic request and response normalization;
- prompt preservation;
- structured-output success;
- malformed output;
- refusal or incomplete output;
- authentication failure;
- billing failure;
- rate limit;
- timeout and provider unavailable;
- provider dispatch;
- provider/model pinning across retries;
- no fallback;
- provider/model validation;
- missing credential preflight;
- secret redaction;
- connection tests;
- stage payload rejects API keys;
- runtime/static OpenAPI parity.

Frontend tests must cover:

- two provider cards render;
- save/update credential behavior;
- configured state does not reveal the key;
- Test Connection states;
- stage UI contains only provider and model;
- model filtering;
- invalid model reset;
- missing-key warning;
- API key is absent from stage payloads;
- frontend build.

---

## 11. Phase 1 Acceptance Gates

Phase 1 is complete only when all applicable gates pass:

```text
OpenAI official adapter works                         PASS
Anthropic official adapter works                      PASS
Gateway dispatches only openai or anthropic           PASS
Provider and model are pinned across retries          PASS
No automatic fallback                                PASS
Prompts and canonical business schemas are unchanged  PASS
Provider credentials are separate from stage settings PASS
Two provider cards work                               PASS
Every active LLM stage stores provider and model only PASS
Model is validated against provider                   PASS
Raw API keys are never returned                       PASS
Connection tests use production adapters              PASS
Runtime/static/frontend contracts are synchronized    PASS
Targeted backend tests pass                           PASS
Frontend tests and build pass                         PASS
```

If real credentials are unavailable, report the relevant real-provider gates as `NOT VERIFIED`; do not report them as passed.

---

# Phase 2 — Main-Flow Verification and Final Approval

## 12. Phase 2 Objective

Verify the complete business flow with:

- OpenAI-only routing;
- Anthropic-only routing;
- mixed stage routing.

Phase 2 should primarily add or adjust tests and fix defects found by those tests. It must not redesign the provider or settings architecture.

---

## 13. Phase 2 Work

### 13.1 Active Stage Matrix

Identify every active LLM-backed stage from the source.

At minimum, verify these when active:

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

### 13.2 Fake Production-Path Tests

For every active LLM stage, test both providers through the production path:

```text
stage worker or service
→ stage settings resolver
→ LLMGateway
→ selected provider adapter
→ normalized LLMResponse
→ structured-output runner
→ canonical schema validation
→ semantic validation
→ persistence and downstream
```

Do not mock past the gateway or provider adapter.

Cover:

- valid structured output;
- malformed provider response;
- canonical-invalid output;
- semantic-invalid output;
- refusal;
- truncation;
- authentication failure;
- rate limit;
- provider unavailable;
- retry remains on the selected provider;
- no duplicate provider execution;
- no partial-success artifact.

### 13.3 Real Provider Verification

When credentials are available, run:

1. OpenAI provider connection test.
2. Anthropic provider connection test.
3. Representative structured-output calls for active schema groups.
4. Small OpenAI-only vertical slice.
5. Small Anthropic-only vertical slice.
6. Small mixed-provider vertical slice.

If credentials are missing, report `NOT VERIFIED` and do not claim production approval for that provider.

### 13.4 Vertical Slices

Run the main flow:

```text
Topic
→ Script
→ Research or Fact-check when enabled
→ Script Analysis
→ Scene Breakdown
→ Style Bible
→ Visual Planner
→ Prompt Composer
→ Prompt Bundle
```

Configurations:

```text
OpenAI-only
Anthropic-only
Mixed-provider
```

The mixed configuration verifies routing only. It is not fallback.

### 13.5 Settings End-to-End

Verify:

```text
Save OpenAI API key
→ test OpenAI
→ select OpenAI for a stage
→ stage uses OpenAI

Save Anthropic API key
→ test Anthropic
→ select Anthropic for a stage
→ stage uses Anthropic
```

Also verify:

- provider without a configured key fails execution preflight;
- invalid provider/model pair is rejected;
- changing provider resets an invalid model;
- raw keys are never returned or rendered.

### 13.6 Regression

Run applicable project suites:

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

Do not use local databases, logs, caches, generated artifacts, or `node_modules` cleanup as acceptance gates.

---

## 14. Phase 2 Acceptance Gates

```text
Every active LLM stage works with OpenAI              PASS
Every active LLM stage works with Anthropic           PASS
Mixed-provider routing is correct                     PASS
Provider remains fixed across retries                 PASS
No fallback or duplicate provider execution           PASS
OpenAI-only vertical slice passes                     PASS
Anthropic-only vertical slice passes                  PASS
Mixed-provider vertical slice passes                  PASS
Provider credential UI works end to end               PASS
Stage provider/model UI works end to end              PASS
Prompts and canonical business schemas are unchanged  PASS
Scene Breakdown does not regress                      PASS
Visual Planner does not regress                       PASS
Contracts and frontend remain synchronized            PASS
Full applicable regression passes                     PASS
Real provider status is reported honestly             PASS
```

Final recommendation must be one of:

```text
APPROVE
REJECT
```

Do not approve when a required real provider remains unverified.

---

## 15. Out of Scope

Do not implement:

- source pre-KIE verification;
- KIE static scanning;
- KIE provider support;
- automatic provider fallback;
- calling both providers for one request;
- provider benchmarking;
- automatic best-model selection;
- cost optimization;
- dynamic provider plugins;
- custom base URLs;
- prompt redesign;
- canonical business schema redesign;
- database migration or backfill;
- compatibility for old per-stage credential settings;
- asset, TTS, assembly, or render changes;
- unrelated cleanup.

---

## 16. Official API References

OpenAI:

- https://platform.openai.com/docs/quickstart/make-your-first-api-request
- https://platform.openai.com/docs/api-reference/responses-streaming/response/output_item

Anthropic:

- https://platform.claude.com/docs/en/api/messages/create
- https://platform.claude.com/docs/en/manage-claude/authentication
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5
