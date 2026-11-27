import {tick,reserveDecision} from './core.mjs';

// Private Durable Object, one instance, storage only for bounded dispatch timestamps.
export class DispatchGuard {
 constructor(ctx,env){this.ctx=ctx;this.env=env;}
 async fetch(request){
  if(request.method!=='POST'||new URL(request.url).pathname!=='/tick')return new Response(null,{status:404});
  const reserve=now=>this.ctx.storage.transaction(async tx=>{
   const decision=reserveDecision(await tx.get('attempts'),now);
   if(decision.allowed)await tx.put('attempts',decision.attempts);
   return decision;
  });
  const result=await tick({mode:this.env.WATCHDOG_MODE,token:this.env.GITHUB_ACTIONS_TOKEN,reserve});
  return Response.json(result);
 }
}
export default {
 fetch(){return new Response(null,{status:404});},
 async scheduled(_event,env){
  if(!['observe','active'].includes(env.WATCHDOG_MODE)){console.log(JSON.stringify({status:'disabled'}));return;}
  const stub=env.DISPATCH_GUARD.get(env.DISPATCH_GUARD.idFromName('wawp-point-refresh-main'));
  const r=await stub.fetch('https://watchdog.internal/tick',{method:'POST'});
  if(!r.ok)throw Error('watchdog_guard_unavailable');
  const outcome=await r.json();
  console.log(JSON.stringify({event:'titan_forecast_watchdog',...outcome}));
 }
};
