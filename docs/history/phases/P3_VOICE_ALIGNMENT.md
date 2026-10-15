# P3 — History voice production and accurate word alignment

**Authority:** docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md. Target repository ONLY plthien/Historical-Motion-Graphics-Studio; always branch from an explicitly verified current main or accepted staging SHA. Never push or auto-merge main. No root MANIFEST.json or release-manifest generator/check. NO LLM API, no local LLM, no ChatGPT browser automation: External ChatGPT prompts + downloaded/imported JSON only. No old YTB data, DB, routes, runtime, stages or compatibility. Use test evidence from exact candidate SHA; otherwise mark BLOCKED.

## Prerequisite
P2 approved ScriptHistoricalAnalysis exact text; no old BeatTimingMap/Scene ownership remains. Voice provider APIs are NON-LLM media interfaces, not model authoring: opt-in ElevenLabs TTS or forced alignment may use explicit credentials; Kokoro local TTS needs validated install/identity.

## Contracts and authority
- `VoiceRequest.v1`: project/script artifact/version SHA, language, selected provider+voice+runtime+calibration, target pacing by section, supported local/output settings, stable attempt ID.
- `VoiceCandidate.v1`: draft status, exact text hash, section audio hashes, sample count and effective pacing, provider identity/response metadata; never advances approved voice head.
- `VoiceManifest.v1`: final approved concatenated WAV file SHA256, audio sample_rate/channels/sample_count, actual duration, provider+voice+calibration identities, exact script SHA, content hash; immutability enforced on publish.
- `WordTimingMap.v1`: final WAV SHA + exact script SHA, real forced-align provider identity, intervals with stable word_id/section_id and source char offsets, start_sample/end_sample, confidence/coverage QC, monotonic bounds and no unsupported holes. Char/word alignment must use exactly the final WAV, NOT per-section pre-reassembly raw audio.
- Explicit blocked states: `VOICE_RUNTIME_NOT_READY`, `VOICE_ALIGNMENT_UNAVAILABLE`, `VOICE_AUDIO_IDENTITY_MISMATCH`, `VOICE_TIMING_COVERAGE_INVALID`, `VOICE_CALIBRATION_IDENTITY_INVALID`.

## Codebase extraction mapping
- Donor (read-only): `app/modules/v4/voice_service.py`, `durable_voice_service.py`, `voice_provider*.py`, `voice_pacing*.py`, `elevenlabs_*.py`, `voice_audio.py`, `voice_first_policy.py`, `voice_current_realignment.py`, `kokoro/` and `tests/v4/test_voice_*.py`.
- New owners: `app/history/voice/{domain,providers,alignment,pacing,artifacts,service,operations}.py`, `app/history/api/voice.py`, `src/features/history/voice/`, `contracts/history/schemas/voice_*.json`, `tests/history/voice/`.
- Extract neutral durable task scheduler, retries, Windows process guards to history/core, no importing deleted old V4 as a "temporary compatibility" package. Translate only tests that remain valid for NEW history contract.

## API/UI
- GET `/api/history/projects/{id}/voice/readiness`; POST `voice/jobs`; GET `voice/jobs/{job_id}`; POST `voice/jobs/{job_id}/cancel`.
- POST `voice/sections/{section_id}/regenerate` (if supported), POST `voice/candidates/{candidate_id}/approve`; POST `voice/realign`; GET `voice/audio`; GET `voice/timings`.
- UI shows provider/voice, identity/calibration, section timings, raw and final candidate WPM, audio preview and QC warning, explicit final approval; disabled alignment buttons explain missing real provider. UI auto-polls only active durable jobs.
- If user can run local TTS but lacks alignment service, Voice stage remains blocked and motion-word-anchored production cannot proceed by silent approximate timestamps.

## Exact implementation order
1. Extract/port Voice production capabilities and focused donor tests after P1 dependency closure; map every imported symbol to new owner, no old ScenePlan/Beat updates.
2. Implement validation for script exact text, safe audio paths, provider config/readiness, canonical runtime identity and approved candidate hash.
3. Reimplement bounded local Kokoro process execution and opt-in ElevenLabs synthesis without LLM SDK; final WAV assembly and waveform/sample QC.
4. Implement actual word/character forced alignment on FINAL WAV (initial provider ElevenLabs). Preserve deterministic stable word ID and char offset mapping.
5. Apply pacing QC and section-targeted retry/reuse with source hashes; publish voice only via guarded CAS. A new script/audio invalidates prior time map, timeline and render, never history claims unrelated to text.
6. Durable state: SQLite attempt fencing, timeout/cancel, crash/restart, audio staging outside DB transaction, orphan cleanup.
7. Run realistic synthesis and real alignment smoke (only when configured), plus fake provider tests; no fake timestamp fallback.
8. Add contract parity, UI tests, errors and final approved pair publishing.

## Negative tests and PASS criteria
- Wrong script hash, old audio candidate, forged word interval, reassembled audio differs, missing tokens/out-of-bounds, nonmonotonic intervals, identity/calibration mismatch, partial section replacement and stale job publication rejected.
- Host killed mid-TTS, retry after lease loss, cancel while staging, duplicate approval, concurrent provider callbacks, no credentials and wrong voice ID are safely handled.
- Actual non-silent final WAV sample count and timing coverage verified; no pseudo-alignment. If paid provider unavailable the real-alignment gate is BLOCKED, never assumed PASS.
- No legacy DB or app.modules.v4 usage; no root release manifest; no LLM authoring API.
