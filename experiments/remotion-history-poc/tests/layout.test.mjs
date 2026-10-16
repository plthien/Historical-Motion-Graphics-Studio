import test from 'node:test';
import assert from 'node:assert/strict';
import {SAFE_MARGIN,ZONES,rectsOverlap,validateLayout} from '../src/layout.mjs';
test('all functional zones stay inside safe margins without overlapping',()=>{
 assert.deepEqual(validateLayout(),[]);
 for(const zones of Object.values(ZONES))for(const r of Object.values(zones)){
  assert.ok(r.x>=SAFE_MARGIN&&r.y>=SAFE_MARGIN);
  assert.ok(r.x+r.w<=1920-SAFE_MARGIN&&r.y+r.h<=1080-SAFE_MARGIN);
 }
});
test('regression: map stamp avoids Sarajevo label and archive headline avoids frame',()=>{
 assert.equal(rectsOverlap(ZONES.map.stamp,ZONES.map.label,20),false);
 assert.equal(rectsOverlap(ZONES.archive.headline,ZONES.archive.photo,20),false);
});
test('collision detector catches forced overlaps and offscreen regions',()=>{
 const broken=structuredClone(ZONES);
 broken.map.stamp={...broken.map.label};
 broken.archive.headline.x=-100;
 assert.ok(validateLayout(broken).some(s=>s.includes('collision')));
 assert.ok(validateLayout(broken).some(s=>s.includes('out_of_safe_area')));
});
