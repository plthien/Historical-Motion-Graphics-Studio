import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { resolveSelectedTopicIdea } from '../src/features/studio/topicIdeaResolution.js';

const CURRENT_ARTIFACT_ID = 'artifact_topics_current';
const SELECTED_IDEA_ID = 'idea_001';
const studio = readFileSync(new URL('../src/pages/StudioPage.tsx', import.meta.url), 'utf8');
const scriptPanel = readFileSync(new URL('../src/features/studio/ScriptPanel.tsx', import.meta.url), 'utf8');
const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8');

function topicIdea(ideaId = SELECTED_IDEA_ID) {
  return {
    idea_id: ideaId,
    title: `Exact topic ${ideaId}`,
    core_question: 'What is the exact question?',
    viewer_hook: 'An exact viewer hook.',
    topic_angle: 'An exact topic angle.',
    tone: 'documentary',
    pov_style: 'second-person experiential narration',
    avoid: ['unsupported certainty'],
    ctr_score: 8,
    difficulty: 'medium',
    content_tags: ['exact'],
    risk_notes: [],
    recommended_pacing_mode: 'fast',
    pacing_mode_reason: 'Dense evidence progression supports rapid visual-state refresh.',
  };
}

function topicIdeasResult(artifactId = CURRENT_ARTIFACT_ID, ideas = [topicIdea()]) {
  return {
    generated: true,
    artifact_id: artifactId,
    artifact: {
      artifact_type: 'topic_ideas',
      schema_version: '1.0',
      channel_context: { name: 'Test Channel', language: 'English' },
      source_user_input: {
        seed_topic: 'seed',
        topic_count: ideas.length,
        focus_keywords: [],
        focus: 'balanced',
        avoid_topics: [],
      },
      ideas,
    },
  };
}

function project(overrides = {}) {
  return {
    project_id: 'project_topic_handoff',
    channel_id: 'channel_topic_handoff',
    topic_ideas_artifact_id: CURRENT_ARTIFACT_ID,
    selected_topic_ideas_artifact_id: CURRENT_ARTIFACT_ID,
    selected_topic_idea_id: SELECTED_IDEA_ID,
    ...overrides,
  };
}

test('resolver returns the exact selected Topic Idea from the current artifact', () => {
  const resolution = resolveSelectedTopicIdea({ project: project(), topicIdeasResult: topicIdeasResult() });
  assert.equal(resolution.status, 'valid');
  assert.equal(resolution.idea.idea_id, SELECTED_IDEA_ID);
  assert.equal(resolution.idea.pov_style, 'second-person experiential narration');
  assert.deepEqual(resolution.idea.avoid, ['unsupported certainty']);
  assert.equal(resolution.idea.recommended_pacing_mode, 'fast');
});

test('resolver fails closed for absent, partial, stale, unloaded, mismatched, missing, or duplicate selection', () => {
  assert.equal(resolveSelectedTopicIdea({
    project: project({ selected_topic_ideas_artifact_id: null, selected_topic_idea_id: null }),
    topicIdeasResult: topicIdeasResult(),
  }).status, 'none');
  assert.equal(resolveSelectedTopicIdea({
    project: project({ selected_topic_idea_id: null }),
    topicIdeasResult: topicIdeasResult(),
  }).status, 'partial_state');
  assert.equal(resolveSelectedTopicIdea({
    project: project({ selected_topic_ideas_artifact_id: 'artifact_old' }),
    topicIdeasResult: topicIdeasResult(),
  }).status, 'stale_selection');
  assert.equal(resolveSelectedTopicIdea({ project: project(), topicIdeasResult: undefined }).status, 'artifact_not_loaded');
  assert.equal(resolveSelectedTopicIdea({
    project: project(),
    topicIdeasResult: topicIdeasResult('artifact_other'),
  }).status, 'artifact_mismatch');
  assert.equal(resolveSelectedTopicIdea({
    project: project(),
    topicIdeasResult: topicIdeasResult(CURRENT_ARTIFACT_ID, [topicIdea('idea_002')]),
  }).status, 'stale_selection');
  assert.equal(resolveSelectedTopicIdea({
    project: project(),
    topicIdeasResult: topicIdeasResult(CURRENT_ARTIFACT_ID, [topicIdea(), topicIdea()]),
  }).status, 'stale_selection');
});

test('selected Topic recommendations are copied into editable Script form only once per idea', () => {
  assert.match(studio, /appliedTopicDefaultsRef/);
  assert.match(studio, /const selectedTopicKey = `\$\{projectId\}:\$\{project\.selected_topic_ideas_artifact_id\}:\$\{selectedIdea\.idea_id\}`/);
  assert.match(studio, /appliedTopicDefaultsRef\.current === selectedTopicKey/);
  assert.match(studio, /pov_style: selectedIdea\.pov_style/);
  assert.match(studio, /avoid: selectedIdea\.avoid\.join\(', '\)/);
  assert.match(studio, /pacing_mode: selectedIdea\.recommended_pacing_mode/);
  assert.match(scriptPanel, /Recommended/);
  assert.match(scriptPanel, /value=\{scriptForm\.pacing_mode\}/);
  assert.match(scriptPanel, /value=\{scriptForm\.pov_style\}/);
  assert.match(scriptPanel, /value=\{scriptForm\.avoid\}/);
});

test('Script request transports editable recommendations, pacing mode, and section planning mode', () => {
  assert.match(studio, /pov_style: scriptForm\.pov_style/);
  assert.match(studio, /avoid: csvToList\(scriptForm\.avoid\)/);
  assert.match(studio, /section_count_mode: scriptForm\.section_count_mode/);
  assert.match(studio, /pacing_mode: scriptForm\.pacing_mode/);
  assert.match(studio, /narration_wpm: scriptForm\.narration_wpm/);
  assert.match(client, /pov_style: string; avoid: string\[\]/);
  assert.match(client, /section_count_mode: 'llm_decides' \| 'backend_plan'/);
  assert.match(client, /pacing_mode: VideoPacingMode/);
  assert.match(client, /narration_wpm: NarrationWpmRange/);
});
