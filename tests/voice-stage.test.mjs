import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (path) => readFileSync(resolve(process.cwd(), path), 'utf8');
const panel = read('src/features/studio/VoicePanel.tsx');
const review = read('src/features/studio/voice/VoiceReviewComponents.tsx');
const voiceProfileSetup = read('src/features/studio/voice/ChannelVoiceProfileSetup.tsx');
const studio = read('src/pages/StudioPage.tsx');
const client = read('src/api/client.ts');
const progress = read('src/features/projects/projectProgress.ts');

test('Voice is a separate stage after Script and no longer depends on Scene Plan', () => {
  const sceneIndex = studio.indexOf("id: 'scene_assets'");
  const voiceIndex = studio.indexOf("id: 'voice'");
  const scriptIndex = studio.indexOf("id: 'script'");
  assert.ok(scriptIndex >= 0 && voiceIndex > scriptIndex);
  assert.match(studio, /<VoicePanel/);
  assert.match(studio, /enabled=\{Boolean\(project\.script_artifact_id\)\}/);
  assert.match(progress, /'Script',[\s\S]*'Voice'/);
  assert.match(progress, /narration_audio_artifact_id/);
  assert.match(progress, /narration_word_timing_map_artifact_id/);
});

test('Voice stage is section-oriented and does not expose segment review UI', () => {
  for (const name of [
    'VoiceSummary', 'NarrationMasterPlayer', 'VoiceGenerationProgress',
    'VoiceSectionRemediationList', 'VoiceReviewGate',
  ]) {
    assert.match(panel, new RegExp(`<${name}`));
    assert.match(review, new RegExp(`export function ${name}`));
  }
  for (const value of [
    'voice-generation-provider-select', 'voice-preset-select', 'kokoro-voice-select',
    'Generate Voice', 'Generate Missing', 'Regenerate Section',
    'Approve Voice',
    'voice-progress', 'voice-action-error', 'voice-section-remediation', 'voice-review-gate',
  ]) assert.match(`${panel}\n${review}`, new RegExp(value));
  assert.doesNotMatch(`${panel}\n${review}`, /voice-profile-select|voice-voice-select|voice-pacing-mode/);
  assert.doesNotMatch(`${panel}\n${review}`, /VoiceTimeline|VoiceSegmentNavigator|VoiceSegmentInspector/);
  assert.doesNotMatch(`${panel}\n${review}`, /voice-segment-navigator|voice-segment-inspector|voice-timeline|preview-voice-segment|Regenerate Segment/);
  assert.doesNotMatch(panel, /selectedSegmentId|previewSegmentId|useProgressiveLimit|getVoiceSegmentAudioUrl/);
  assert.doesNotMatch(`${panel}\n${review}`, /voice-speaking-rate/);
});

test('Narration review keeps one full player and adds section-local playback without segment UI', () => {
  const source = `${panel}\n${review}`;
  const audioElements = source.match(/<audio\b/g) ?? [];
  assert.equal(audioElements.length, 2);
  assert.match(review, /data-testid="voice-master-player"/);
  assert.match(review, /voice-section-audio-/);
  assert.match(panel, /getVoiceAudioUrl/);
  assert.match(panel, /getVoiceSectionAudioUrl/);
  assert.match(panel, /label="Full narration"/);
  assert.doesNotMatch(panel, /getVoiceSegmentAudioUrl/);
});

test('Voice polls only active progress and parks ambiguous submission polling', () => {
  const workspaceStart = panel.indexOf('const workspace = useQuery');
  const workspaceEnd = panel.indexOf('const discoveredJobId =', workspaceStart);
  const workspaceBlock = panel.slice(workspaceStart, workspaceEnd);
  assert.match(workspaceBlock, /getVoiceWorkspace\(projectId, signal\)/);
  assert.doesNotMatch(workspaceBlock, /refetchInterval/);

  const jobStart = panel.indexOf('const job = useQuery');
  const jobEnd = panel.indexOf('const refresh =', jobStart);
  const jobBlock = panel.slice(jobStart, jobEnd);
  assert.match(jobBlock, /\['voice-job', projectId, activeJobId\]/);
  assert.match(jobBlock, /apiClient\.getVoiceJob/);
  assert.match(jobBlock, /refetchInterval/);
  assert.match(jobBlock, /current\.action === PARKED_JOB_ACTION/);
  assert.match(jobBlock, /!current\.next_retry_at/);
  assert.match(jobBlock, /!current\.cancel_requested/);
  assert.match(jobBlock, /return false/);
  assert.match(jobBlock, /return 800/);
  assert.match(panel, /PARKED_JOB_ACTION = 'submission_outcome_unknown'/);
  assert.match(panel, /TERMINAL_JOB/);
  assert.match(panel, /invalidateQueries\(\{ queryKey \}\)/);
});

test('Voice terminal settlement blocks stale workspace job resurrection', () => {
  assert.match(panel, /useRef<Set<string>>\(new Set\(\)\)/);
  assert.match(panel, /const discoveredJobId = workspace\.data\?\.active_operation\?\.job_id \?\? null;/);
  assert.match(panel, /const discoverableJobId = discoveredJobId && !settledTerminalJobIdsRef\.current\.has\(discoveredJobId\) \? discoveredJobId : null;/);
  assert.match(panel, /if \(discoverableJobId && !activeJobId\) setActiveJobId\(discoverableJobId\);/);
  assert.doesNotMatch(panel, /if \(discoveredJobId && !activeJobId\) setActiveJobId\(discoveredJobId\);/);
});

test('Voice terminal settlement refreshes at most once per terminal job id', () => {
  assert.match(panel, /const currentJob = activeJobId && job\.data\?\.job_id === activeJobId \? job\.data : undefined;/);
  const terminalStart = panel.indexOf('const terminalJobId =');
  const terminalEnd = panel.indexOf('const expectedAudio =', terminalStart);
  const terminalBlock = panel.slice(terminalStart, terminalEnd);
  assert.match(terminalBlock, /const terminalJobId = currentJob\?\.job_id \?\? null;/);
  assert.match(terminalBlock, /const terminalJobStatus = currentJob\?\.status \?\? null;/);
  assert.match(terminalBlock, /TERMINAL_JOB\.has\(terminalJobStatus\)/);
  assert.match(terminalBlock, /settledTerminalJobIdsRef\.current\.has\(terminalJobId\)/);
  assert.match(terminalBlock, /settledTerminalJobIdsRef\.current\.add\(terminalJobId\)/);
  assert.match(terminalBlock, /setActiveJobId\(\(current\) => current === terminalJobId \? null : current\)/);
  assert.equal((terminalBlock.match(/void refreshWorkspace\(\)/g) ?? []).length, 1);
  assert.ok(
    terminalBlock.indexOf('settledTerminalJobIdsRef.current.has(terminalJobId)') < terminalBlock.indexOf('void refreshWorkspace()'),
    'settled job guard must run before terminal refresh side effects',
  );
});

test('Voice terminal settlement does not flash fallback progress back to zero', () => {
  assert.match(review, /if \(!job && !fallbackStatus\) return null;/);
  assert.match(panel, /const activeFallback = useMemo\(\(\) => discoverableJobId \? workspace\.data\?\.active_operation\?\.status \?\? null : null/);
  assert.match(panel, /const currentJob = activeJobId && job\.data\?\.job_id === activeJobId \? job\.data : undefined;/);
  assert.match(panel, /launchState && !currentJob/);
  assert.match(panel, /const progressJob = currentJob \?\? \(!hasLiveGeneration \? lastTerminalJob \?\? undefined : undefined\);/);
  assert.match(panel, /<VoiceGenerationProgress job=\{progressJob\} fallbackStatus=\{activeFallback\}/);
  assert.doesNotMatch(panel, /<VoiceGenerationProgress job=\{job\.data\}/);
  assert.match(panel, /const operationActive = Boolean\(activeJobId \|\| discoverableJobId\);/);
});

test('Voice terminal settlement permits later jobs and page-reload recovery', () => {
  const captureStart = panel.indexOf('const captureJob =');
  const captureEnd = panel.indexOf('const generate =', captureStart);
  const captureBlock = panel.slice(captureStart, captureEnd);
  assert.match(captureBlock, /settledTerminalJobIdsRef\.current\.delete\(result\.job_id\);/);
  assert.match(captureBlock, /setActiveJobId\(result\.job_id\);/);
  assert.ok(
    captureBlock.indexOf('settledTerminalJobIdsRef.current.delete(result.job_id)') < captureBlock.indexOf('setActiveJobId(result.job_id)'),
    'newly submitted jobs must clear any previous settled marker before becoming active',
  );
  assert.match(panel, /if \(discoverableJobId && !activeJobId\) setActiveJobId\(discoverableJobId\);/);
  assert.match(panel, /apiClient\.cancelVoiceJob\(projectId, activeJobId \?\? discoverableJobId!\)/);
});

test('Voice polling status sets match the durable lifecycle', () => {
  const activeMatch = panel.match(/const ACTIVE_JOB = new Set\(\[([^\]]+)\]\)/);
  const terminalMatch = panel.match(/const TERMINAL_JOB = new Set\(\[([^\]]+)\]\)/);
  assert.ok(activeMatch, 'ACTIVE_JOB set is declared');
  assert.ok(terminalMatch, 'TERMINAL_JOB set is declared');
  for (const status of ['queued', 'running', 'waiting_for_provider', 'assembling', 'finalizing']) {
    assert.ok(activeMatch[1].includes(`'${status}'`), status);
  }
  for (const status of ['completed', 'failed', 'cancelled', 'superseded']) {
    assert.ok(terminalMatch[1].includes(`'${status}'`), status);
  }
  assert.match(panel, /current\.action === PARKED_JOB_ACTION/);
});

test('Generation progress exposes provider, action, retry, problems and durable cancel', () => {
  for (const marker of [
    'voiceProviderLabel(job.provider, job.action)', 'job?.action', 'job.retry_count', 'job.max_attempts', 'job.next_retry_at',
    'job?.problem', 'cancel-voice', 'Cancellation requested',
  ]) assert.ok(review.includes(marker), marker);
  assert.match(review, /provider === 'elevenlabs'/);
  assert.match(review, /provider === 'kokoro_local'/);
  assert.match(panel, /apiClient\.cancelVoiceJob/);
  assert.match(client, /cancelVoiceJob/);
});

test('Voice phase UI stops calling a completed target section regenerating and preserves failures', () => {
  assert.match(panel, /SYNTHESIS_JOB_ACTION/);
  assert.match(panel, /const activeSynthesisSectionId = useMemo/);
  assert.match(panel, /activeSectionId=\{activeSynthesisSectionId\}/);
  assert.match(panel, /activeTargetAction=\{currentJob\?\.action \?\? null\}/);
  assert.match(panel, /lastTerminalJob/);
  assert.doesNotMatch(review, /const regenerating = activeSectionId === section\.section_id \|\| activeTargetSectionId === section\.section_id/);
  for (const label of ['Aligning…', 'Finalizing…', 'Needs attention', 'Local processing', 'Forced Alignment']) {
    assert.ok(review.includes(label), label);
  }
  assert.match(review, /job\?\.error_code/);
  assert.match(review, /Voice operation needs attention/);
});

test('Active section regeneration labels source remediation metrics as the previous attempt', () => {
  assert.match(panel, /activeOperationId=\{workspace\.data\?\.active_operation\?\.operation_id \?\? null\}/);
  assert.match(panel, /const resolvingPacingProblem = workspace\.data\?\.active_operation\?\.operation_type === 'regenerate_section'/);
  assert.match(review, /const showingPriorAttempt = targetOperation/);
  assert.match(review, /Regeneration in progress/);
  assert.match(review, /Previous attempt is shown below while the new regeneration is still being evaluated/);
  assert.match(review, /The previous attempt failed the ±5% raw-WPM QC fence/);
  assert.doesNotMatch(review, /You can accept this exact ±5% QC deviation/);
  assert.match(review, /outside the ±5% WPM QC fence/);
  assert.match(review, /within the ±15% final-pacing safety fence/);
  assert.match(review, /manualAcceptanceSafe && !targetOperation/);
});

test('frontend sends explicit generation provider and preserves source identity for section remediation routes', () => {
  for (const method of [
    'getVoiceWorkspace', 'generateVoice',
    'getVoiceJob', 'cancelVoiceJob', 'regenerateVoiceSection', 'approveVoice',
    'getVoiceAudioUrl',
  ]) assert.match(client, new RegExp(method));
  assert.doesNotMatch(panel, /getTTSSettings|getTTSReadiness|selectedProfile|selectedVoiceId|speakingRate/);
  assert.match(client, /expected_narration_audio_artifact_id/);
  assert.match(client, /expected_narration_word_timing_map_artifact_id/);
  assert.match(client, /generation_provider/);
  assert.match(client, /generation_voice_id/);
  assert.match(client, /voice_preset/);
  assert.match(client, /source_remediation_operation_id/);
  assert.match(client, /expected_remediation_report_sha256/);
  assert.match(client, /\/voice\/sections\/\$\{sectionId\}\/regenerate\/jobs/);
  assert.doesNotMatch(client, /regenerateVoiceSegment/);
  const regenerateStart = panel.indexOf('const regenerate = useMutation');
  const regenerateEnd = panel.indexOf('const acceptPacingDeviation = useMutation', regenerateStart);
  const regenerateBlock = panel.slice(regenerateStart, regenerateEnd);
  assert.doesNotMatch(regenerateBlock, /voicePreset|generationProvider|kokoroVoiceId|voice_preset|generation_provider|generation_voice_id/);
  assert.doesNotMatch(`${panel}\n${review}`, /fetch\(|XMLHttpRequest|subprocess|KOKORO_COMMAND/i);
});

test('Voice generation provider selector separates ElevenLabs preset and Kokoro voice identity', () => {
  assert.match(panel, /useState<VoiceGenerationProvider>\('elevenlabs'\)/);
  assert.match(panel, /useState<VoicePreset>\('quality_cloned_voice'\)/);
  assert.match(panel, /useState<string>\(''\)/);
  assert.match(panel, /data-testid="voice-generation-provider-select"/);
  assert.match(panel, /data-testid="kokoro-voice-select"/);
  assert.match(panel, /if \(provider && !providerTouchedRef\.current\) setGenerationProvider\(provider\)/);
  assert.match(panel, /value=\{voicePreset\}/);
  assert.match(panel, /generation_provider: generationProvider/);
  assert.match(panel, /voice_preset: generationProvider === 'elevenlabs' \? voicePreset : null/);
  assert.match(panel, /generation_voice_id: generationProvider === 'kokoro_local' \? kokoroVoiceId : null/);
  assert.match(panel, /ElevenLabs Forced Alignment/);
  assert.doesNotMatch(`${panel}\n${review}`, /TTS profile|voice-profile-select|voice-voice-select|selectedProfile|selectedVoice/);
  assert.doesNotMatch(panel, /provider_id|model_id|tts_profile_id/);
});

test('Voice explains readiness blockers and keeps Channel PVC setup scoped to ElevenLabs', () => {
  assert.match(panel, /data-testid="voice-readiness-problem"/);
  assert.match(panel, /data-testid="voice-provider-readiness-problem"/);
  assert.match(panel, /data-testid="voice-provider-workflow-readiness"/);
  assert.match(panel, /Generation: \{generationReadinessLabel\}/);
  assert.match(panel, /Alignment: \{alignmentReadinessLabel\}/);
  assert.match(panel, /selectedProviderStatus\?\.generation_presets\?\.\[voicePreset\]/);
  assert.match(panel, /generationProvider === 'elevenlabs' && selectedPresetStatus\s*\?\s*selectedPresetStatus\.ready\s*:\s*selectedProviderStatus\?\.generation_ready/);
  assert.match(panel, /generationProvider === 'elevenlabs' && selectedPresetStatus\s*\?\s*selectedPresetStatus\.issue\s*:\s*selectedProviderStatus\?\.issue \?\? null/);
  assert.doesNotMatch(panel, /selectedPresetStatus\?\.issue\s*\?\?\s*selectedProviderStatus\?\.issue/);
  assert.match(panel, /selectedPresetStatus\?\.ready === true/);
  assert.match(client, /generation_presets\?: Record<VoicePreset, VoiceGenerationPresetStatus> \| null/);
  assert.match(panel, /<ChannelVoiceProfileSetup/);
  assert.match(panel, /generationProvider === 'elevenlabs' \? <ChannelVoiceProfileSetup/);
  for (const marker of [
    'getProject(projectId)',
    'getChannelVoiceProfile',
    'updateChannelVoiceProfile',
    'listProfessionalVoices',
    'channel-voice-profile-select',
    'save-channel-voice-profile',
    "voice_kind: 'professional'",
    'stability: 0.5',
    'similarity_boost: 0.75',
    'use_speaker_boost: true',
  ]) assert.ok(voiceProfileSetup.includes(marker), marker);
  assert.doesNotMatch(voiceProfileSetup, /getTTSSettings|getTTSReadiness|model[_-]select|provider[_-]select/);
});

