// R0 frozen demonstrator: all timing is in absolute integer frames at 30 FPS.
// Later phases replace this fixture with an approved, source-checked director contract.
export const motionPlan = {
  schemaVersion: 'history_motion_poc.v1',
  fps: 30,
  width: 1920,
  height: 1080,
  durationFrames: 1800,
  audioSrc: null,
  setups: [
    {
      id: 'opening',
      kind: 'title',
      startFrame: 0,
      endFrame: 570,
      events: [
        {id: 'intro', kind: 'headline', atFrame: 12, durationFrames: 450, text: 'MỘT BUỔI SÁNG\nTHAY ĐỔI LỊCH SỬ', layer: 2},
        {id: 'date', kind: 'timestamp', atFrame: 94, durationFrames: 375, text: '28.06.1914', layer: 3},
        {id: 'location', kind: 'label', atFrame: 180, durationFrames: 305, text: 'SARAJEVO · BOSNIA', layer: 4},
        {id: 'stamp-a', kind: 'stamp', atFrame: 382, durationFrames: 155, text: 'BƯỚC NGOẶT', layer: 5}
      ]
    },
    {
      id: 'map',
      kind: 'map',
      startFrame: 570,
      endFrame: 1200,
      events: [
        {id: 'map-title', kind: 'headline', atFrame: 24, durationFrames: 570, text: 'MỘT TUYẾN ĐƯỜNG\nQUA SARAJEVO', layer: 2},
        {id: 'map-place', kind: 'label', atFrame: 125, durationFrames: 450, text: 'SARAJEVO', layer: 3},
        {id: 'route', kind: 'route', atFrame: 250, durationFrames: 300, text: 'LỘ TRÌNH', layer: 4},
        {id: 'map-warning', kind: 'stamp', atFrame: 435, durationFrames: 160, text: 'RẼ SAI', layer: 5}
      ]
    },
    {
      id: 'archive',
      kind: 'archive',
      startFrame: 1200,
      endFrame: 1800,
      events: [
        {id: 'photo', kind: 'photo', atFrame: 35, durationFrames: 520, text: 'TƯ LIỆU LỊCH SỬ', layer: 2},
        {id: 'archive-heading', kind: 'headline', atFrame: 128, durationFrames: 430, text: 'MỘT SỰ KIỆN,\nNHIỀU HỆ QUẢ', layer: 3},
        {id: 'archive-note', kind: 'label', atFrame: 310, durationFrames: 250, text: 'ẢNH CẦN XÁC MINH NGUỒN', layer: 4},
        {id: 'archive-stamp', kind: 'stamp', atFrame: 430, durationFrames: 135, text: 'HỆ QUẢ', layer: 5}
      ]
    }
  ]
};

const allowedKinds = new Set(['title', 'map', 'archive']);
const allowedEvents = new Set(['headline', 'timestamp', 'label', 'stamp', 'route', 'photo']);
const isPositiveInteger = (value) => Number.isInteger(value) && value > 0;
const isNonnegativeInteger = (value) => Number.isInteger(value) && value >= 0;

export function validatePlan(plan) {
  const errors = [];
  if (!plan || typeof plan !== 'object' || Array.isArray(plan)) return ['Plan must be an object'];
  if (plan.schemaVersion !== 'history_motion_poc.v1') errors.push('Unsupported schemaVersion');
  if (!isPositiveInteger(plan.fps) || !isPositiveInteger(plan.width) || !isPositiveInteger(plan.height) || !isPositiveInteger(plan.durationFrames)) errors.push('Invalid composition metadata');
  if (plan.audioSrc !== null && plan.audioSrc !== undefined && (typeof plan.audioSrc !== 'string' || !/^[A-Za-z0-9_/-]+\.(wav|mp3)$/.test(plan.audioSrc) || plan.audioSrc.includes('..') || plan.audioSrc.startsWith('/'))) errors.push('Invalid local audioSrc');
  if (!Array.isArray(plan.setups) || !plan.setups.length) return [...errors, 'At least one setup is required'];
  let cursor = 0;
  const setupIds = new Set(), eventIds = new Set();
  for (const setup of plan.setups) {
    if (typeof setup.id !== 'string' || !setup.id || setupIds.has(setup.id)) errors.push('Invalid or duplicate setup id');
    setupIds.add(setup.id);
    if (!allowedKinds.has(setup.kind)) errors.push('Invalid setup kind for ' + setup.id);
    if (!isNonnegativeInteger(setup.startFrame) || !isPositiveInteger(setup.endFrame) || setup.endFrame <= setup.startFrame || setup.startFrame !== cursor) errors.push('Setups must be contiguous, ordered and nonempty: ' + setup.id);
    const setupLength = setup.endFrame - setup.startFrame;
    cursor = setup.endFrame;
    if (!Array.isArray(setup.events)) {errors.push('Events missing for ' + setup.id); continue;}
    for (const event of setup.events) {
      if (typeof event.id !== 'string' || !event.id || eventIds.has(event.id)) errors.push('Invalid or duplicate event id');
      eventIds.add(event.id);
      if (!allowedEvents.has(event.kind)) errors.push('Invalid event kind for ' + event.id);
      if (!isNonnegativeInteger(event.atFrame) || !isPositiveInteger(event.durationFrames) || event.atFrame + event.durationFrames > setupLength) errors.push('Event outside setup: ' + event.id);
      if (!Number.isInteger(event.layer) || event.layer < 0 || event.layer > 99) errors.push('Invalid layer for ' + event.id);
      if (typeof event.text !== 'string' || event.text.length > 220) errors.push('Invalid event text: ' + event.id);
      if (event.assetPath !== undefined && (typeof event.assetPath !== 'string' || !/^[A-Za-z0-9_/-]+\.(png|jpg|jpeg|webp)$/.test(event.assetPath) || event.assetPath.includes('..') || event.assetPath.startsWith('/'))) errors.push('Invalid local asset path: ' + event.id);
    }
  }
  if (cursor !== plan.durationFrames) errors.push('Setups must cover the complete duration');
  return errors;
}

export function setupAtFrame(plan, frame) {
  if (!Number.isInteger(frame) || frame < 0 || frame >= plan.durationFrames) return null;
  return plan.setups.find((s) => frame >= s.startFrame && frame < s.endFrame) ?? null;
}

export function visibleEventsAtFrame(plan, frame) {
  const setup = setupAtFrame(plan, frame);
  if (!setup) return [];
  const local = frame - setup.startFrame;
  return setup.events.filter((e) => local >= e.atFrame && local < e.atFrame + e.durationFrames).map((e) => e.id);
}
