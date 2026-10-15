import test from 'node:test';
import assert from 'node:assert/strict';
import {motionPlan, validatePlan, setupAtFrame, visibleEventsAtFrame} from '../src/plan.mjs';

test('sample plan has exact coverage and valid bounds', () => {
  assert.deepEqual(validatePlan(motionPlan), []);
  assert.equal(motionPlan.durationFrames / motionPlan.fps, 60);
  assert.equal(motionPlan.setups.at(-1).endFrame, motionPlan.durationFrames);
});
test('boundary frames select one setup only', () => {
  assert.equal(setupAtFrame(motionPlan, 0)?.id, 'opening');
  assert.equal(setupAtFrame(motionPlan, 569)?.id, 'opening');
  assert.equal(setupAtFrame(motionPlan, 570)?.id, 'map');
  assert.equal(setupAtFrame(motionPlan, 1200)?.id, 'archive');
  assert.equal(setupAtFrame(motionPlan, 1800), null);
});
test('timed reveal and expiry are deterministic', () => {
  assert.deepEqual(visibleEventsAtFrame(motionPlan, 0), []);
  assert.deepEqual(visibleEventsAtFrame(motionPlan, 12), ['intro']);
  assert.ok(visibleEventsAtFrame(motionPlan, 95).includes('date'));
  assert.ok(!visibleEventsAtFrame(motionPlan, 540).includes('stamp-a'));
  assert.ok(visibleEventsAtFrame(motionPlan, 820).includes('route'));
});
test('rejects gaps, duplicate identifiers and out-of-range events', () => {
  const copy = structuredClone(motionPlan);
  copy.setups[1].startFrame++;
  assert.ok(validatePlan(copy).some(e => e.includes('contiguous')));
  const other = structuredClone(motionPlan);
  other.setups[0].events[1].id = other.setups[0].events[0].id;
  assert.ok(validatePlan(other).some(e => e.includes('duplicate event')));
  const outside = structuredClone(motionPlan);
  outside.setups[0].events[0].durationFrames = 1000;
  assert.ok(validatePlan(outside).some(e => e.includes('outside setup')));
});
test('prevents path traversal for media assets', () => {
  const p = structuredClone(motionPlan);
  p.setups[2].events[0].assetPath = '../secret.png';
  assert.ok(validatePlan(p).some(e => e.includes('Invalid local asset path')));
  p.setups[2].events[0].assetPath = 'assets/archive.png';
  assert.deepEqual(validatePlan(p), []);
});
