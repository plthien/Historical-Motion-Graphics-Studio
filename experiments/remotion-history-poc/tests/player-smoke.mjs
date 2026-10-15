import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';

const server=spawn(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['run','dev'], {stdio:'pipe', shell:process.platform==='win32'});
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
 assert.deepEqual(errors,[],'Browser console runtime errors');
 console.log('PASS: actual @remotion/player playback, pause, 12 seeks, jumps and screenshots');
}finally{
 if(browser)await browser.close();
 server.kill('SIGTERM');
}
