import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const studio = readFileSync(new URL('../src/pages/StudioPage.tsx', import.meta.url), 'utf8');
const topic = readFileSync(new URL('../src/features/studio/TopicIdeasPanel.tsx', import.meta.url), 'utf8');
const script = readFileSync(new URL('../src/features/studio/ScriptPanel.tsx', import.meta.url), 'utf8');
const scene = readFileSync(new URL('../src/features/studio/SceneBreakdownPanel.tsx', import.meta.url), 'utf8');
const workspace = readFileSync(new URL('../src/features/studio/PromptImportWorkspace.tsx', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../src/pages/SettingsPage.tsx', import.meta.url), 'utf8');
const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8');
const twoStepClient = readFileSync(new URL('../src/api/sceneBreakdownTwoStep.ts', import.meta.url), 'utf8');
const workerPath = new URL('../app/worker.py', import.meta.url);
const durableRenderRunner = readFileSync(new URL('../app/durable_render_runner.py', import.meta.url), 'utf8');

test('authoring pipeline has three top-level external authoring stages and no separate analysis or visual stage', () => {
  for (const panel of [topic, script, scene]) assert.match(panel, /PromptImportWorkspace/);
  assert.match(studio, /id: 'topic'/);
  assert.match(studio, /id: 'script'/);
  assert.match(studio, /id: 'scene'/);
  assert.doesNotMatch(studio, /id: 'analysis'/);
  assert.doesNotMatch(studio, /id: 'visual'/);
  assert.doesNotMatch(studio, /ScriptAnalysisPanel|VisualPlannerPanel/);
});

test('common workspace supports build, copy, paste, upload, and one-click import', () => {
  for (const token of ['Build Prompt', 'Rebuild Prompt', 'Copy Prompt', 'Upload JSON', 'ChatGPT JSON Result', 'Import JSON']) {
    assert.match(workspace, new RegExp(token));
  }
  assert.doesNotMatch(workspace, /Import Valid Result|onValidate|isValidating/);
  assert.match(workspace, /accept="application\/json,\.json"/);
  assert.match(workspace, /navigator\.clipboard\.writeText/);
  assert.match(workspace, /disabled={!props.resultJson\.trim\(\) \|\| props\.importDisabled \|\| props\.isImporting}/);
  assert.match(studio, /externalValidationFromError/);
  assert.match(studio, /function externalPayload\(resultJson: string\)/);
  assert.doesNotMatch(studio, /source_fingerprint|source_snapshot/);
});

test('topic UI exposes no avoid-topics input or selected-topic counter', () => {
  assert.doesNotMatch(topic, /Avoiding \d+|View topics|avoid_topics|selected topics/i);
  for (const label of ['Seed Topic', 'Count', 'Focus', 'Focus Keywords']) assert.match(topic, new RegExp(label));
});

test('topic review restores the complete planning card and Script recommendations', () => {
  for (const token of ['Core Question', 'Topic Angle', 'Viewer Hook', 'Suggested POV Style', 'Script Avoid', 'CTR', 'Tone:', 'risk_notes', 'content_tags']) {
    if (token === 'risk_notes' || token === 'content_tags') continue;
    assert.match(topic, new RegExp(token));
  }
  assert.match(topic, /idea\.risk_notes/);
  assert.match(topic, /idea\.content_tags/);
  assert.match(topic, /idea\.difficulty/);
  assert.match(topic, /idea\.pov_style/);
  assert.match(topic, /idea\.avoid/);
  assert.match(topic, /Suggested Pacing/);
  assert.match(topic, /idea\.recommended_pacing_mode/);
  assert.match(topic, /idea\.pacing_mode_reason/);
});

test('Script UI applies editable topic recommendations and exposes pacing plus section planning modes', () => {
  assert.match(studio, /pov_style: selectedIdea\.pov_style/);
  assert.match(studio, /avoid: selectedIdea\.avoid\.join/);
  assert.match(studio, /pacing_mode: selectedIdea\.recommended_pacing_mode/);
  assert.match(studio, /appliedTopicDefaultsRef/);
  assert.match(script, /ChatGPT decides section count and roles/);
  assert.match(script, /Use fixed backend section plan/);
  assert.match(script, /Video pacing/);
  assert.match(script, /FAST/);
  assert.match(script, /MEDIUM/);
  assert.match(script, /SLOW/);
  assert.match(studio, /section_count_mode: scriptForm\.section_count_mode/);
  assert.match(studio, /pacing_mode: scriptForm\.pacing_mode/);
  assert.match(studio, /narration_wpm: scriptForm\.narration_wpm/);
  assert.match(studio, /project\.channel_dna_narration_wpm/);
  assert.match(script, /Narration WPM range/);
  assert.match(script, /Project&apos;s Channel DNA snapshot/);
  assert.match(script, /does not save back to Channel DNA/);
  assert.doesNotMatch(script, /narration_wpm_override|target_narration_wpm|target_narration_wpm_source/);
});

test('script and analysis are one prompt and one imported response', () => {
  assert.match(script, /Script & Analysis/);
  assert.match(script, /uses the selected narration WPM range/);
  assert.match(script, /Visual pacing does not choose narration speed/);
  assert.match(script, /Each section chooses its own WPM inside the selected range/);
  assert.match(script, /realized-density gate/);
  assert.match(script, /keeps the hook at high visual density/);
  assert.match(script, /Imported Script/);
  assert.match(script, /Imported Analysis/);
  assert.doesNotMatch(script, /Research & Generate|Generate Script Analysis/);
  assert.match(client, /buildScriptAnalysisPrompt/);
  assert.match(client, /validateScriptAnalysisImport/);
  assert.match(client, /importScriptAnalysis/);
  assert.doesNotMatch(client, /researchGenerateScript|generateScriptAnalysis/);
});

test('Script UI reports runtime diagnostics without a hard creative word-count gate', () => {
  for (const token of ['Target runtime', 'Pacing mode', 'Derived avg pace', 'Estimated runtime', 'Runtime delta', 'Diagnostic only', 'planned_average_wpm']) {
    assert.match(script, new RegExp(token));
  }
  assert.match(script, /runtime_diagnostics/);
  assert.doesNotMatch(script, /word_count_validation|minimum_word_count|maximum_word_count|Accepted .*words/s);
  assert.doesNotMatch(studio, /scriptWithinRange|SCRIPT_WORD_COUNT_OUT_OF_RANGE|within-range Script|accepted range/i);
  assert.match(studio, /canGenerate=\{Boolean\(project\.script_analysis_artifact_id\)\}/);
  assert.match(studio, /generationBlockedReason=\{null\}/);
});

test('Script review exposes Story Analysis, Continuity remove-restore, and QC', () => {
  for (const token of ['Story Analysis', 'Continuity', 'QC', 'Narrative Markers', 'Recurring Props', 'Recurring Visual Subjects', 'Remove', 'Restore']) {
    assert.match(script, new RegExp(token));
  }
  assert.match(script, /do not lock this identity\/style downstream/);
  assert.match(client, /getScriptAnalysisContinuityReview/);
  assert.match(client, /mutateScriptAnalysisContinuityReview/);
  assert.match(script, /invalidate the current Style Bible and all derived downstream artifacts/);
});

test('scene breakdown uses two internal prompt/import steps and keeps paired review in one screen', () => {
  assert.match(scene, /Step 1 · Scene & Beat Plan/);
  assert.match(scene, /Step 2 · Visual Plan/);
  assert.match(scene, /scene-breakdown-authoring-steps/);
  assert.match(scene, /scene-breakdown-card-grid/);
  assert.match(scene, /scene-breakdown-drawer/);
  assert.doesNotMatch(scene, /Regenerate Section|Retry Failed Section|Resume/);
  assert.match(twoStepClient, /buildScenePrompt/);
  assert.match(twoStepClient, /validateSceneImport/);
  assert.match(twoStepClient, /importScene/);
  assert.match(twoStepClient, /getVisualPrompt/);
  assert.match(twoStepClient, /validateVisualImport/);
  assert.match(twoStepClient, /importVisual/);
});

test('settings expose production providers while production execution has no platform RQ worker', () => {
  assert.match(settings, /tool no longer stores LLM provider credentials or stage model settings/);
  assert.doesNotMatch(settings, /LLMStage|OpenAI|Anthropic/);
  assert.doesNotMatch(client, /getLLMSettings|updateLLM|testLLMProvider/);
  assert.equal(existsSync(workerPath), false);
  assert.match(durableRenderRunner, /RenderCoordinatorRunner/);
  assert.match(durableRenderRunner, /install_windows_subprocess_job_ownership/);
});

test('pending external prompt packages are restored from backend after reload', () => {
  for (const token of ['getPendingTopicIdeasPrompt', 'getPendingScriptAnalysisPrompt']) {
    assert.match(client, new RegExp(token));
    assert.match(studio, new RegExp(token));
  }
  assert.match(studio, /topicPromptDraftQuery\.data/);
  assert.match(studio, /scriptPromptDraftQuery\.data/);
  assert.match(studio, /restoredTopicInputRef/);
  assert.match(studio, /restoredScriptInputRef/);
  assert.match(twoStepClient, /getScenePrompt/);
  assert.match(twoStepClient, /getVisualPrompt/);
  assert.match(scene, /getVisualPrompt\(projectId\)/);
  assert.match(scene, /getScenePrompt\(projectId\)/);
  assert.match(workspace, /replaces the reserved Artifact ID\(s\)/);
});