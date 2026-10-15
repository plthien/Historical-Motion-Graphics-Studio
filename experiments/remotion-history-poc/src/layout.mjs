// Pure deterministic layout metadata at 1920×1080. Rectangle coordinates are stable
// during spring entrances: animation is constrained within zone padding.
export const SAFE_MARGIN = 70;
export const ZONES = Object.freeze({
  title: {
    headline: {x:115,y:275,w:1460,h:250},
    timestamp: {x:145,y:585,w:850,h:145},
    label: {x:145,y:850,w:720,h:90},
    stamp: {x:1300,y:702,w:440,h:135},
    annotation: {x:980,y:940,w:770,h:56},
  },
  map: {
    headline: {x:105,y:240,w:630,h:250},
    map: {x:750,y:190,w:1050,h:610},
    label: {x:845,y:824,w:390,h:92},
    stamp: {x:1410,y:826,w:330,h:135},
    annotation: {x:90,y:935,w:1140,h:55},
  },
  archive: {
    photo: {x:135,y:205,w:780,h:665},
    headline: {x:1000,y:275,w:750,h:235},
    label: {x:1000,y:560,w:750,h:100},
    stamp: {x:1300,y:748,w:415,h:135},
    annotation: {x:970,y:930,w:775,h:60},
  }
});
export const rectsOverlap = (a,b,pad=0) => a.x < b.x+b.w+pad && a.x+a.w+pad > b.x && a.y < b.y+b.h+pad && a.y+a.h+pad > b.y;
export function validateLayout(zones=ZONES){
 const errors=[];
 for(const [setup,items] of Object.entries(zones)){
  for(const [name,r] of Object.entries(items)) {
   if(![r.x,r.y,r.w,r.h].every(Number.isFinite) || r.w<=0||r.h<=0||r.x<SAFE_MARGIN||r.y<SAFE_MARGIN||r.x+r.w>1920-SAFE_MARGIN||r.y+r.h>1080-SAFE_MARGIN)
     errors.push(setup+':'+name+':out_of_safe_area');
  }
  const entries=Object.entries(items);
  for(let i=0;i<entries.length;i++)for(let j=i+1;j<entries.length;j++){
   if(rectsOverlap(entries[i][1],entries[j][1],5)) errors.push(setup+':'+entries[i][0]+':'+entries[j][0]+':collision');
  }
 }
 return errors;
}
