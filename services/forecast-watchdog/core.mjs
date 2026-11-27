/* Scheduled, fail-closed watchdog. No data ingestion, archive access or forecast calculation. */
export const REPO='anzthebest0-cpu/wawp-forecast-engine';
export const WORKFLOW='wawp_model_only_refresh.yml';
const PUBLICATION='wawp_publish_model_preview.yml';
const API='https://api.github.com/repos/'+REPO+'/actions/workflows/';
const MANIFEST='https://anzthebest0-cpu.github.io/wawp-forecast-engine/data/model_preview_manifest.json';
const MIN=60000, HOUR=60*MIN;
export const LEAD=120*MIN, COOLDOWN=90*MIN, MAX_ATTEMPTS=8;
const ACTIVE=['queued','in_progress','requested','waiting','pending'];
const stamp=s=>typeof s==='string'&&/(?:Z|[+-]\d\d:\d\d)$/.test(s)?Date.parse(s):NaN;
const obj=o=>o!==null&&typeof o==='object'&&!Array.isArray(o);
export function inspectManifest(m,now){
 if(!obj(m)||m.contract!=='raw-model-preview-v1'||m.taf_status!=='withheld_pending_independent_validation'||!obj(m.models)||!Number.isSafeInteger(m.row_count)||m.row_count<0)throw Error('manifest_schema');
 const asOf=stamp(m.as_of_utc);if(!Number.isFinite(asOf)||asOf>now+2*MIN)throw Error('manifest_clock');
 const count=Object.keys(m.models).length;
 if(m.status==='unavailable'&&count===0&&m.row_count===0&&m.expires_at_utc===null)return {due:true,reason:'unavailable',models:0};
 if(m.status!=='model_only'||!count||!m.row_count)throw Error('manifest_schema');
 const expiry=stamp(m.expires_at_utc);
 const expiries=Object.values(m.models).map(v=>stamp(v?.expires_at_utc));
 if(!Number.isFinite(expiry)||expiry<=asOf||expiry>asOf+6*HOUR+MIN||expiries.some(v=>!Number.isFinite(v))||expiry!==Math.min(...expiries))throw Error('manifest_expiry');
 return {due:expiry-now<=LEAD,reason:expiry<=now?'expired':expiry-now<=LEAD?'approaching_expiry':'fresh',models:count,minutes_remaining:Math.floor((expiry-now)/MIN)};
}
export function reserveDecision(previous,now){
 if(previous!==undefined&&(!Array.isArray(previous)||previous.some(t=>!Number.isFinite(t)||t>now)))throw Error('state_invalid');
 const attempts=(previous||[]).filter(t=>t>now-24*HOUR).sort((a,b)=>a-b);
 if(attempts.length>=MAX_ATTEMPTS)return {allowed:false,reason:'daily_cap'};
 if(attempts.length&&now-attempts[attempts.length-1]<COOLDOWN)return {allowed:false,reason:'cooldown'};
 return {allowed:true,attempts:[...attempts,now]};
}
async function json(fetcher,url,init,code){
 let response;try{response=await fetcher(url,{...init,redirect:'error',signal:AbortSignal.timeout(12000)});}catch(_){throw Error(code+'_transport');}
 if(!response.ok)throw Error(code+'_http_'+response.status);
 try{return await response.json();}catch(_){throw Error(code+'_json');}
}
function validateRuns(r){
 if(!obj(r)||!Number.isSafeInteger(r.total_count)||r.total_count<0||!Array.isArray(r.workflow_runs)||(r.total_count>0&&!r.workflow_runs.length))throw Error('github_runs_schema');
 return r;
}
export async function tick({mode,token,fetcher=fetch,clock=Date.now,reserve}){
 const now=clock();
 if(!['observe','active'].includes(mode))return {status:'disabled'};
 try{
  const m=await json(fetcher,MANIFEST+'?watchdog='+Math.floor(now/MIN),{headers:{'Cache-Control':'no-cache'},cache:'no-store'},'manifest');
  const health=inspectManifest(m,now);
  if(!health.due)return {status:'fresh',...health};
  if(typeof token!=='string'||!token.trim())return {status:'no_token',...health};
  const headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','User-Agent':'TITAN-forecast-watchdog'};
  const get=async(url)=>json(fetcher,url,{headers},'github');
  const info=await get(API+WORKFLOW);
  if(info.state!=='active'||info.path!=='.github/workflows/'+WORKFLOW)return {status:'workflow_inactive',...health};
  // Explicit filters avoid overlooking queued or older active runs. The publication
  // workflow shares the refresh concurrency group; do not displace its pending run.
  for(const workflow of [WORKFLOW,PUBLICATION]){
   for(const state of ACTIVE){
    const r=validateRuns(await get(API+workflow+'/runs?branch=main&exclude_pull_requests=true&per_page=1&status='+state));
    if(r.total_count>0)return {status:'busy',workflow,state,...health};
   }
  }
  const recent=validateRuns(await get(API+WORKFLOW+'/runs?branch=main&exclude_pull_requests=true&per_page=1'));
  if(recent.workflow_runs.length){
   const created=stamp(recent.workflow_runs[0].created_at);
   if(!Number.isFinite(created)||created>now+2*MIN)throw Error('github_run_clock');
   if(now-created<45*MIN)return {status:'recent_refresh',...health};
  }
  if(mode==='observe')return {status:'would_dispatch',...health};
  // Reserve BEFORE sending POST: timeouts and ambiguous outcomes consume a
  // cooldown slot. Durable storage serializes competing scheduled invocations.
  const slot=await reserve(clock());
  if(!slot.allowed)return {status:slot.reason,...health};
  let response;
  try{response=await fetcher(API+WORKFLOW+'/dispatches',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({ref:'main'}),redirect:'error',signal:AbortSignal.timeout(12000)});}catch(_){return {status:'dispatch_uncertain',...health};}
  if(response.status===204)return {status:'dispatched',...health};
  if(response.status===200){
   let id=null;try{const r=await response.json();if(Number.isSafeInteger(r.workflow_run_id))id=r.workflow_run_id;}catch(_){}
   return {status:'dispatched',run_id:id,...health};
  }
  return {status:'dispatch_rejected',http_status:response.status,...health};
 }catch(e){
  // Never emit upstream bodies, tokens, exception stacks, or arbitrary strings.
  const code=typeof e?.message==='string'&&/^(?:manifest_(?:schema|clock|expiry|transport|json|http_\d{3})|github_(?:transport|json|http_\d{3}|runs_schema|run_clock)|state_invalid)$/.test(e.message)?e.message:'internal_error';
  return {status:'check_failed',reason:code};
 }
}
