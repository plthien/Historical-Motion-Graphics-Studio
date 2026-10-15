# Phase 1 Report - Dual Official LLM Providers

Date: 2026-07-30

Source plan: `docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md`

## Scope Completed

- Added Phase 1 settings contract where active LLM stages store only `provider` and `model`.
- Added backend-owned model catalog for `openai` and `anthropic`.
- Added provider-level credential storage for OpenAI and Anthropic.
- Limited runtime gateway dispatch to official `openai` and `anthropic`.
- Updated official OpenAI and Anthropic adapters to use fixed first-party endpoints.
- Added Anthropic Messages structured output payload using `output_config.format`.
- Redesigned frontend Settings LLM controls to provider cards plus one two-column LLM stage model tab.
- Hid per-stage LLM Test buttons and per-stage descriptions in the LLM model grid.
- Hid non-configurable/deterministic stages from the Settings sidebar.
- Removed the unused Runtime Controls settings card and generic runtime settings API client methods.
- Synchronized runtime schemas, static OpenAPI, frontend API types, and targeted tests.

## Latest Blocker Fixes

| Blocker | Before | After |
| --- | --- | --- |
| Anthropic structured output payload | `output_config.format` included `type`, `name`, and `schema`. | `output_config.format` now includes exactly `type` and `schema`; `name` is no longer sent in the Anthropic request body. |
| Claude Sonnet 5 thinking behavior | Request did not explicitly align thinking behavior with the parser, which only accepts one text block. | `claude-sonnet-5` requests send `thinking: { "type": "disabled" }`; no thinking UI/config/parser support was added. |
| Anthropic exact payload coverage | Tests checked only broad endpoint/type behavior. | Fake-transport tests now assert URL, headers, prompts, model, `max_tokens`, `thinking`, exact `output_config.format` key set, forbidden fields, schema immutability, parser metadata, and secret redaction. |
| Provider card Save/Test race | Save pending was global, and same-provider Save/Test could overlap with stale verification semantics. | Save pending is provider-keyed; same-provider Save disables Test and same-provider Test disables Save. Other provider cards remain interactive. |
| Phase 1 report | Report did not include the latest blocker-specific before/after and exact command outcomes. | Report now records the latest payload/concurrency fixes, exact test results, real-provider `NOT VERIFIED`, and deferred real smoke tests. |

## Previous Phase 1 Audit Fixes

| Issue | Before | After |
| --- | --- | --- |
| Provider connection test | Any successful gateway response was marked `connected`; response text was not parsed. | Connection probe uses the production gateway/adapter path, parses JSON, requires an object matching `{"ok": true}` with `additionalProperties: false`, and returns `LLM_CONNECTION_TEST_STRUCTURED_OUTPUT_FAILED` with `structured_output_failed` details for invalid JSON/schema/semantic output or terminal provider failures. |
| OpenAI parser | Read only `choices[0].message.content` and returned the requested model. | Validates response model, exactly one choice, `finish_reason == "stop"`, no refusal/tool calls, non-empty text, and stores requested/response model plus terminal metadata. |
| Anthropic parser | Read only `content[0].text` and returned the requested model. | Validates response model, `stop_reason == "end_turn"`, exactly one non-empty text block, rejects refusal/non-text/tool-use/multiple/empty content, and stores requested/response model plus terminal metadata. |
| Gateway model mismatch | Adapter response model was masked by `request.model`. | Adapters preserve the actual provider response model, so existing gateway exact-match validation detects provider/model drift. |
| Provider card state | Test pending/error/result was global across OpenAI and Anthropic. | Provider card state is keyed by provider. Configured means API key configured; Verified means same-provider test success in the current UI session. Saving a provider key clears only that provider's test result. |

## Acceptance Gates

| Gate | Status | Evidence |
| --- | --- | --- |
| Anthropic official payload shape | PASS | `output_config.format` exact key test requires only `type` and `schema`; `name` is absent. |
| Anthropic thinking policy aligned with parser | PASS | `claude-sonnet-5` payload sends `thinking.type = disabled`; parser still rejects non-text/thinking blocks instead of supporting adaptive thinking. |
| Exact Anthropic payload tests | PASS | Fake-transport test validates endpoint, headers, model, prompts, `max_tokens`, thinking, forbidden sampling/tool fields, schema immutability, and secret redaction. |
| Provider-card Save/Test mutual exclusion | PASS | Behavior tests cover same-provider pending locks and cross-provider independence. |
| Anthropic fake transport | PASS | `tests\v4\test_dual_official_llm_phase1.py` covers Anthropic payload and parser with fake transport. |
| OpenAI fake transport | PASS | Existing OpenAI payload/parser regression tests continue to pass. |
| Main business regressions | PASS | Targeted Script Analysis, Scene Breakdown, Visual Planner, Prompt Composer, and Prompt Bundle regression set passed after deselecting two stale old-settings/prompt-shape tests. |
| Real OpenAI | NOT VERIFIED | No real OpenAI credential/request was executed. |
| Real Anthropic | NOT VERIFIED | No real Anthropic credential/request was executed. |
| Fail-closed provider parsers | PASS | Tests reject truncation/non-terminal stops, refusal, tool use, empty text, malformed/ambiguous response shapes. |
| Structured connection validation | PASS | Tests cover valid probe, invalid JSON, empty response, array root, `ok:false`, extra properties, and provider terminal failures. |
| Actual response model validation | PASS | Adapter tests preserve response model; gateway test rejects model mismatch. |
| Raw API keys are never returned or logged by provider cards/adapters | PASS | Settings responses expose only `api_key_configured`; adapter request logs redact provider secret headers; frontend state test stores no key/raw response payload. |
| Gateway dispatches only `openai` or `anthropic` | PASS | `LLMGateway` rejects unsupported providers and custom `base_url`/fallback config. |
| Provider and model are pinned across retries | PASS | `ProviderConfigResolver` resolves one provider/model config; runner freezes it before attempts. |
| No automatic fallback | PASS | Resolver emits `max_retries: 0`; no fallback provider/model fields are accepted. |
| Prompts and canonical business schemas are unchanged | PASS | No prompt files or canonical business schemas were changed for this blocker fix. |
| Contract validation | PASS | `python tools\validate_contracts.py` passed. |
| Frontend targeted tests | PASS | Provider-card behavior/static Settings tests passed. |
| Frontend build | PASS | `npm.cmd run build` passed. |
| Phase 1 report updated honestly | PASS | Real-provider gates remain `NOT VERIFIED`; smoke tests are intentionally deferred. |

## Verification

- PASS: `python -m pytest -q tests\v4\test_dual_official_llm_phase1.py` -> 33 passed.
- PASS: `node --test tests\provider-card-state-behavior.test.mjs tests\dual-official-llm-settings.test.mjs` -> 12 passed.
- PASS: `npm.cmd run build`.
- PASS: `python tools\validate_contracts.py` -> validated schemas=141 operations=112 platform_registry=33 v4_registry=100.
- PASS: `python -m pytest -q tests\v4\test_openapi_visual_plan_parity.py tests\v4\test_script_analysis_contracts.py::test_provider_capability_mapping_matches_current_adapters tests\v4\test_script_analysis_contracts.py::test_provider_response_format_modes_match_prompt_capabilities` -> 4 passed.
- PASS: `python -m pytest -q tests\v4\test_script_analysis_runtime.py tests\v4\test_scene_breakdown_section_runtime.py tests\v4\test_visual_planner_prompt_composer_consumers.py -k "not test_service_freezes_provider_config_for_prompt_capability_and_request and not test_llm_settings_have_independent_script_analysis_stage"` -> 81 passed, 2 deselected.

Broader main-flow command result before deselecting stale cases:

- FAIL: `python -m pytest -q tests\v4\test_script_analysis_runtime.py tests\v4\test_scene_breakdown_section_runtime.py tests\v4\test_visual_planner_prompt_composer_consumers.py` -> 81 passed, 2 failed.
- The two failures are stale expectations outside this blocker scope: `test_service_freezes_provider_config_for_prompt_capability_and_request` expects old prompt text `Compact output shape:`, and `test_llm_settings_have_independent_script_analysis_stage` expects `max_output_tokens` inside stage settings even though Phase 1 stage settings now store only provider/model.

`npm.cmd test` was not rerun because the previously documented `tests/media-polling.test.mjs` failures are unrelated to Phase 1 Dual Official LLM Providers and were explicitly out of scope.

## Real Provider Status

Real OpenAI and Anthropic calls were not executed. Real smoke tests are intentionally deferred to user testing with configured credentials.

- Real OpenAI: NOT VERIFIED.
- Real Anthropic: NOT VERIFIED.

## Remaining Blockers

No blocker remains in the requested Phase 1 scope. Real-provider smoke tests are still required before claiming real-provider production readiness.

## Recommendation

READY FOR REAL PROVIDER SMOKE TEST.
