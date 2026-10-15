---
name: voice-alignment
description: Extract and validate Kokoro/ElevenLabs history narration and real forced-aligned word timing from the exact approved final WAV with durable process and source identity.
---

# Voice and Alignment Skill — NEW HISTORY RULES ONLY

## Applicability
P3, P5 cue compilation, P6 final audio, P7 invalidation and P8 QC. First read AGENTS.md, docs/history/CODING_RULES.md and relevant phase spec. The donor voice system is technical reference only; old Beat/Scene entities are not allowed product dependencies.

## Inputs/contract
Only the current approved exact `ScriptHistoricalAnalysis.v1` text and source hash; explicit user-selected TTS provider (local Kokoro or separately authorized ElevenLabs voice synthesis), exact voice runtime identity and pacing calibration; stable section IDs. No LLM provider.

## Workflow
1. Extract clean voice provider and Windows bounded process primitives without `app.modules.v4`; preserve identity hash of voices/model/interpreter/command/calibration. Identity mismatch fails closed; no fallback voice.
2. Generate draft WAV candidate(s) from EXACT text with real sample counts/duration, section provenance, pacing QC and user approval. Regenerate/reuse only matching section and runtime lineage; never silently change a locked approved Script.
3. Build ONE final approved WAV hash. Any change in section audio, text, rate or provider results in a NEW final WAV; old word timings and motion/render descendants become stale.
4. Invoke actually configured **non-LLM forced alignment** (initially existing ElevenLabs integration or independently tested real offline engine) on the finalized WAV bytes and exact corresponding Script. Publish per-word/phrase stable ID+char offsets, start/end sample/time and confidence/coverage.
5. Validate monotonic offsets, actual audio duration, word coverage, source identity; if unavailable or incomplete, return `VOICE_ALIGNMENT_UNAVAILABLE` / `VOICE_TIMING_COVERAGE_INVALID` and BLOCK visual cue acceptance. Never fabricate estimated timing from WPM or an approximate section timer.
6. Durable operations must have owner/lease fencing, controlled submit/cancel/retry, progress read-only and actual cleanup/kill-on-close of Windows descendants; no old render/job queues.

## Validation and outputs
VoiceManifest(final WAV SHA, provider identity, sample count/duration, script SHA); WordTimingMap(real aligner identity, exact final WAV SHA, stable words); unit/negative tests for bad runtime identity, wrong transcript, section cache mismatch, out-of-order word, silent audio, provider failure/cancel and concurrent stale publication; actual WAV + alignment evidence for PASS.

No root release manifest; project VoiceManifest is an approved production artifact.
