'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const a=require('../docs/analysis-workspace.js');
assert.deepEqual(a.scales.map(s=>s.id),['global','synoptic','meso','local']);
assert.equal(new Set(a.sources.map(x=>x.id)).size,a.sources.length);
assert.equal(a.sources.length,20);
for(const s of a.scales)assert.ok(a.forScale(s.id).length>=4);
for(const x of a.sources){
  assert.ok(x.source&&x.prompt&&x.type);
  if(x.url)assert.ok(new URL(x.url).protocol==='https:'||x.id==='awos');
  if(x.image){assert.equal(new URL(x.image).protocol,'https:');assert.ok(x.url);}
}
assert.equal(a.sources.find(x=>x.id==='awos').image,null);
assert.equal(a.sources.find(x=>x.id==='radar').image,null);
const html=fs.readFileSync('docs/index.html','utf8');
for(const id of ['analysis-workspace','analysis-scales','analysis-cards','analysis-image','analysis-image-status','aviation-product-sources','regional-olr-img','regional-olr-status','regional-sat-status'])assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1);
assert.ok(html.includes('analysis-workspace.js'));
assert.ok(html.includes('Automated TAF withheld'));
assert.ok(html.includes('https://web-aviation.bmkg.go.id/web/sigwx_medium_level.php'));
assert.ok(html.includes('https://web-aviation.bmkg.go.id/web/sigwx_high_level.php'));
assert.ok(!html.includes('rason_wind_streamlines'));
for(const id of ['enso','sst','mjo'])assert.equal(a.sources.find(x=>x.id===id).image,null);
console.log('TITAN analysis catalog and markup checks passed');

assert.ok(!html.includes('id="sigwx-image-panel"'));
