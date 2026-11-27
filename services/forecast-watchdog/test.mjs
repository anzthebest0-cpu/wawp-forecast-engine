import test from 'node:test';
import assert from 'node:assert/strict';
import {inspectManifest,reserveDecision,tick,LEAD,COOLDOWN,MAX_ATTEMPTS,WORKFLOW} from './core.mjs';
import worker,{DispatchGuard} from './worker.mjs';
const now=Date.parse('2026-11-28T00:00:00Z'),hour=3600000;
const iso=n=>new Date(n).toISOString();
const manifest=(remaining=hour)=>({contract:'raw-model-preview-v1',status:'model_only',as_of_utc:iso(now-5*hour),expires_at_utc:iso(now+remaining),row_count:10,models:{IFS:{expires_at_utc:iso(now+remaining)}},taf_status:'withheld_pending_independent_validation'});
const unavailable={...manifest(),status:'unavailable',models:{},row_count:0,expires_at_utc:null};
function fixture({m=manifest(),mode='active',busy=null,recent=null,post=200,manifestStatus=200,githubStatus=200,throwPost=false,token='test-token',previous,workflowState='active'}={}){
 const calls=[];let attempts=previous;
 const fetcher=async(url,opts={})=>{
  const u=new URL(url);calls.push({url,opts});
  if(u.hostname==='anzthebest0-cpu.github.io'){
   assert.equal(opts.headers.Authorization,undefined);
   return Response.json(m,{status:manifestStatus});
  }
  assert.equal(u.hostname,'api.github.com');
  assert.equal(opts.headers.Authorization,'Bearer '+token);
  assert.equal(opts.redirect,'error');
  if(u.pathname.endsWith('/dispatches')){
   assert.equal(u.pathname.endsWith('/'+WORKFLOW+'/dispatches'),true);
   assert.deepEqual(JSON.parse(opts.body),{ref:'main'});
   if(throwPost)throw Error('private fake transport diagnostic '+token);
   return post===204?new Response(null,{status:204}):Response.json({workflow_run_id:123},{status:post});
  }
  if(githubStatus!==200)return Response.json({}, {status:githubStatus});
  if(!u.pathname.endsWith('/runs'))return Response.json({state:workflowState,path:'.github/workflows/'+WORKFLOW});
  const state=u.searchParams.get('status');
  const rr=state&&state===busy?[{id:1,status:state}]:!state&&recent!==null?[{id:2,created_at:iso(recent)}]:[];
  return Response.json({total_count:rr.length,workflow_runs:rr});
 };
 const reserve=async time=>{const d=reserveDecision(attempts,time);if(d.allowed)attempts=d.attempts;return d;};
 const run=()=>tick({mode,token,fetcher,clock:()=>now,reserve});
 return {calls,run,attempts:()=>attempts,posts:()=>calls.filter(c=>c.opts.method==='POST').length};
}
test('manifest verifies exact minimum expiry and never extends freshness',()=>{
 assert.equal(inspectManifest(manifest(hour),now).due,true);
 const fresh={...manifest(3*hour),as_of_utc:iso(now-2*hour)};
 assert.equal(inspectManifest(fresh,now).due,false);
 assert.equal(inspectManifest(manifest(-hour),now).reason,'expired');
 assert.equal(inspectManifest(unavailable,now).reason,'unavailable');
 assert.throws(()=>inspectManifest({...manifest(),expires_at_utc:iso(now+2*hour)},now));
 assert.throws(()=>inspectManifest({...manifest(),taf_status:'issued'},now));
 assert.throws(()=>inspectManifest({...manifest(),as_of_utc:iso(now+hour)},now));
 assert.throws(()=>inspectManifest({...manifest(),models:{}},now));
});
test('disabled and fresh modes do not call GitHub',async()=>{
 let f=fixture({mode:'off'});assert.equal((await f.run()).status,'disabled');assert.equal(f.calls.length,0);
 f=fixture({m:{...manifest(3*hour),as_of_utc:iso(now-2*hour)}});assert.equal((await f.run()).status,'fresh');assert.equal(f.calls.length,1);
});
test('observe mode diagnoses without dispatch or persistent reservation',async()=>{
 const f=fixture({mode:'observe'});assert.equal((await f.run()).status,'would_dispatch');assert.equal(f.posts(),0);assert.equal(f.attempts(),undefined);
});
test('all active statuses block refresh',async()=>{
 for(const busy of ['queued','in_progress','requested','waiting','pending']){
  const f=fixture({busy});assert.equal((await f.run()).status,'busy');assert.equal(f.posts(),0);
 }
});
test('recent refresh and inactive workflow prevent dispatch',async()=>{
 let f=fixture({recent:now-30*60000});assert.equal((await f.run()).status,'recent_refresh');assert.equal(f.posts(),0);
 f=fixture({workflowState:'disabled_manually'});assert.equal((await f.run()).status,'workflow_inactive');assert.equal(f.posts(),0);
});
test('dispatch accepts run ID or legacy 204 and reserves before POST',async()=>{
 for(const post of [200,204]){
  const f=fixture({post});const result=await f.run();assert.equal(result.status,'dispatched');
  if(post===200)assert.equal(result.run_id,123);
  assert.deepEqual(f.attempts(),[now]);assert.equal(f.posts(),1);
  assert.equal((await f.run()).status,'cooldown');assert.equal(f.posts(),1);
 }
});
test('concurrent ticks share one atomic reservation',async()=>{
 const f=fixture();const outcomes=await Promise.all([f.run(),f.run()]);
 assert.deepEqual(outcomes.map(x=>x.status).sort(),['cooldown','dispatched']);assert.equal(f.posts(),1);
});
test('unknown manifest, network errors and GitHub denial fail closed',async()=>{
 for(const opts of [{m:{}},{manifestStatus:503},{githubStatus:403},{githubStatus:429}]){
  const f=fixture(opts);assert.equal((await f.run()).status,'check_failed');assert.equal(f.posts(),0);
 }
 const f=fixture({token:''});assert.equal((await f.run()).status,'no_token');assert.equal(f.posts(),0);
});
test('ambiguous dispatch failure consumes cooldown without secret exposure',async()=>{
 const f=fixture({throwPost:true});const r=await f.run();assert.equal(r.status,'dispatch_uncertain');
 assert(!JSON.stringify(r).includes('test-token'));assert.equal((await f.run()).status,'cooldown');
 assert.equal(f.posts(),1);
 const denied=fixture({post:403});assert.equal((await denied.run()).status,'dispatch_rejected');assert.equal((await denied.run()).status,'cooldown');
});
test('bounded rolling attempt history and fail-closed malformed state',()=>{
 assert.equal(LEAD,120*60000);
 assert.equal(reserveDecision([now-COOLDOWN+1],now).reason,'cooldown');
 assert.equal(reserveDecision([now-COOLDOWN],now).allowed,true);
 const prior=Array.from({length:MAX_ATTEMPTS},(_,i)=>now-(i+1)*COOLDOWN);
 assert.equal(reserveDecision(prior,now).reason,'daily_cap');
 assert.deepEqual(reserveDecision([now-25*hour],now).attempts,[now]);
 assert.throws(()=>reserveDecision([now+1],now));
});
test('no public HTTP endpoint; guard accepts internal POST only',async()=>{
 assert.equal(worker.fetch().status,404);
 const guard=new DispatchGuard({},{});
 assert.equal((await guard.fetch(new Request('https://watchdog.internal/tick'))).status,404);
 assert.equal((await guard.fetch(new Request('https://watchdog.internal/other',{method:'POST'}))).status,404);
});
