import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';

const server=spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5174', '--strictPort'], {stdio:'ignore'});
let browser;
const sleep=t=>new Promise(r=>setTimeout(r,t));
try {
 let ready=false;
 for(let i=0;i<90;i++) {
   if(server.exitCode!==null)throw Error('Vite exited unexpectedly');
   try{const r=await fetch('http://127.0.0.1:5174/');if(r.ok){ready=true;break;}}catch{}
   await sleep(1000);
 }
 assert.ok(ready,'Vite did not start within timeout');
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5174/',{waitUntil:'networkidle'});
 await page.getByText('Historical Motion Lab').waitFor();
 await page.waitForFunction(()=>Boolean(window.__p0Player?.current?.getCurrentFrame));
 assert.equal(await page.locator('button.jump').count(),3);
 const frame=()=>page.evaluate(()=>window.__p0Player.current.getCurrentFrame());
 await page.evaluate(()=>{const p=window.__p0Player.current;p.seekTo(0);p.play();});
 await sleep(900);
 assert.ok(await frame()>1,'Actual Remotion Player failed to advance');
 await page.evaluate(()=>window.__p0Player.current.pause());
 const paused=await frame();await sleep(250);assert.equal(await frame(),paused,'Player did not pause');
 await mkdir('out/player',{recursive:true});
 for(const n of [0,150,300,569,570,750,1050,1199,1200,1350,1650,1799]) {
   await page.evaluate(n=>window.__p0Player.current.seekTo(n),n);
   await page.waitForTimeout(250);
   assert.equal(await frame(),n,'Player seek inaccurate at '+n);
   await page.screenshot({path:'out/player/frame-'+String(n).padStart(4,'0')+'.png'});
 }
 for(const [i,n] of [[0,0],[1,570],[2,1200]]) {
   await page.locator('button.jump').nth(i).click();
   assert.equal(await frame(),n,'Jump-to-setup failed '+n);
 }

 // Independent native 1920x1080 frame capture, without browser controls/UI chrome.
 const native=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
 native.on('pageerror',e=>errors.push(e.message));
 await native.goto('http://127.0.0.1:5174/?parity=1',{waitUntil:'networkidle'});
 await native.waitForFunction(()=>Boolean(window.__p0Player?.current?.getCurrentFrame));
 await mkdir('out/player-native',{recursive:true});
 for(const n of [150,570,750,850,900,1000,1050,1100,1200,1350,1799]){
  await native.evaluate(n=>window.__p0Player.current.seekTo(n),n);
  await native.waitForTimeout(300);
  assert.equal(await native.evaluate(()=>window.__p0Player.current.getCurrentFrame()),n);
  const rects=await native.evaluate(()=>[...document.querySelectorAll('#root *')].slice(0,80).map(el=>{
    const r=el.getBoundingClientRect();return {tag:el.tagName,w:Math.round(r.width),h:Math.round(r.height)};
  }));
  assert.ok(rects.some(r=>r.w===1920&&r.h===1080),'Native Player composition missing 1920x1080 surface: '+JSON.stringify(rects.slice(0,10)));
  await native.screenshot({path:'out/player-native/frame-'+String(n).padStart(4,'0')+'.png', animations:'disabled'});
  if([850,900,1000,1050,1100].includes(n)){
   const geometry=await native.evaluate(()=>{
    const base=document.querySelector('[data-road-path]');
    const route=document.querySelector('[data-animated-route]');
    const arr=m=>m?[m.a,m.b,m.c,m.d,m.e,m.f]:[];
    return {sameD:base?.getAttribute('d')===route?.getAttribute('d'),
      base:arr(base?.getScreenCTM()),route:arr(route?.getScreenCTM())};
   });
   assert.equal(geometry.sameD,true,'Map/route must share identical world path at frame '+n);
   assert.ok(geometry.base.length===6&&geometry.route.length===6);
   for(let k=0;k<6;k++)assert.ok(Math.abs(geometry.base[k]-geometry.route[k])<0.01,'Map/route camera desync at '+n);
  }
 }
 await native.close();
 assert.deepEqual(errors,[],'Browser console runtime errors');
 console.log('PASS: actual @remotion/player playback, pause, 12 seeks, jumps and screenshots');
}finally{
 if(browser)await browser.close();
 if(server.exitCode===null)server.kill('SIGTERM');
}
