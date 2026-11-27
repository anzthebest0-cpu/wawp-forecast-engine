import {test} from 'node:test';
import assert from 'node:assert/strict';
import {normalize,makeHandler} from './worker.mjs';
const now=Date.parse('2026-01-02T04:00:00Z'), epoch=String(now/1000+28800);
const xml='<awosnet><airportIdentifier value="WAWP"/><airportID value="WAWP"/><date value="02/01/2026" time="'+epoch+'"/><time value="03:59:00" time="'+epoch+'"/><twoMinuteWindSpeed value="0" units="knots" time="'+epoch+'"/><twoMinutewindSpeed value="0" units="knots" time="'+epoch+'"/><windGust value="" units="knots"/><rainfallCurrentHour value="0" units="mm" time="'+epoch+'"/><airTemperature value="30" units="deg C" time="'+epoch+'"/><METAR value="METAR WAWP 020350Z 00000KT CAVOK 30/24 Q1012 NOSIG="/></awosnet>';
const origin='https://anzthebest0-cpu.github.io',env={ALLOWED_ORIGIN:origin,PUBLIC_FEED_AUTHORIZED:'true',DISPLAY_CLOCK_UTC_CONFIRMED:'true',WIND_TRUE_CONFIRMED:'true'};
test('strict field parsing and separate epoch',()=>{const s=normalize(xml,now);assert.equal(s.wind_speed_kt,0);assert.equal(s.gust_kt,null);assert.equal(s.rain_current_hour_mm,0);assert.equal(s.metar_date_utc,'2026-01-02');assert.equal(s.source_epoch_offset_seconds,28860);assert.equal(s.retrieved_at_utc,'2026-01-02T04:00:00Z');assert.equal(s.qnh_hpa,null);});
test('reject conflicting aliases, future/stale, wrong station, invalid XML',()=>{for(const x of [xml.replace('twoMinutewindSpeed value="0"','twoMinutewindSpeed value="1"'),xml.replace('03:59:00','04:01:00'),xml.replace('03:59:00','03:00:00'),xml.replaceAll('WAWP','WAAA'),xml.replaceAll('knots','m/s'),'<awosnet>', '<!DOCTYPE awosnet><awosnet/>'])assert.throws(()=>normalize(x,now));});
test('no request before authorization; exact path and origin',async()=>{let calls=0;const h=makeHandler(async()=>{calls++;return new Response(xml);},()=>now);const req=(o=origin,path='/awos')=>new Request('https://proxy.example'+path,{headers:{Origin:o}});assert.equal((await h(req(),{})).status,503);assert.equal((await h(req('https://other.example'),env)).status,403);assert.equal((await h(req(origin,'/other'),env)).status,404);assert.equal(calls,0);const res=await h(req(),env);assert.equal(res.status,200);assert.equal(res.headers.get('cache-control'),'no-store, max-age=0');assert.equal(res.headers.get('access-control-allow-origin'),origin);assert.equal((await res.json()).wind_speed_kt,0);assert.equal(calls,1);});
test('bad upstream and oversized response fail closed',async()=>{const req=new Request('https://proxy.example/awos',{headers:{Origin:origin}});for(const response of [new Response('x',{status:503}),new Response('x'.repeat(131073))]){const h=makeHandler(async()=>response,()=>now);assert.equal((await h(req,env)).status,502);}});

import {normalizeMetar,METAR_URL} from './metar.mjs';
const obs=Date.parse('2026-01-02T03:50:00Z')/1000;
const row={icaoId:'WAWP',obsTime:obs,metarType:'METAR',rawOb:'WAWP 020350Z 00000KT CAVOK 30/24 Q1012'};
test('independent METAR validation, ordering, corrections and no report',()=>{
 const r=normalizeMetar([row],now);assert.equal(r.report_text,'METAR '+row.rawOb);assert.equal(r.observed_at_utc,'2026-01-02T03:50:00Z');
 assert.equal(normalizeMetar([],now),null);assert.equal(normalizeMetar([row],now+3600000),null);
 assert.equal(normalizeMetar([{...row,rawOb:'METAR WAWP 020350Z NIL'}],now),null);
 const correction={...row,rawOb:'METAR COR WAWP 020350Z 01001KT CAVOK 30/24 Q1012'};
 assert.equal(normalizeMetar([row,correction],now).report_text,correction.rawOb);
 for(const r of [{...row,icaoId:'WAAA'},{...row,rawOb:'WAWP 020351Z 00000KT'},{...row,obsTime:now/1000+60}])assert.throws(()=>normalizeMetar([r],now));
 assert.throws(()=>normalizeMetar([row,{...row,rawOb:'WAWP 020350Z 01001KT'}],now));
});
test('METAR independent of AWOS flags, no upstream without authorization',async()=>{
 let calls=0;const request=new Request('https://proxy.example/metar',{headers:{Origin:origin}});
 const h=makeHandler(async url=>{calls++;assert.equal(url,METAR_URL);return new Response(JSON.stringify([row]));},()=>now);
 assert.equal((await h(request,env)).status,503);assert.equal(calls,0);
 const metarEnv={ALLOWED_ORIGIN:origin,PUBLIC_METAR_AUTHORIZED:'true'};
 const res=await h(request,metarEnv);assert.equal(res.status,200);assert.equal((await res.json()).source,'NOAA AWC');assert.equal(calls,1);
 const none=makeHandler(async()=>new Response(null,{status:204}),()=>now);
 assert.equal((await (await none(request,metarEnv)).json()).status,'no_recent_report');
 assert.equal((await h(new Request('https://proxy.example/metar',{headers:{Origin:'https://elsewhere.example'}}),metarEnv)).status,403);
});

test('AWOS failure reasons are bounded and do not expose payloads',async()=>{
 const req=new Request('https://proxy.example/awos',{headers:{Origin:origin}});
 const cases=[
  [async()=>{throw Error('private transport details');},'upstream_transport'],
  [async()=>new Response('private upstream message',{status:503}),'upstream_http'],
  [async()=>new Response('x'.repeat(131073)),'upstream_body'],
  [async()=>new Response(xml.replace('03:59:00','03:00:00')),'validation_stale_clock'],
  [async()=>new Response(xml.replace('03:59:00','04:01:00')),'validation_future_clock'],
  [async()=>new Response(xml.replaceAll('knots','m/s')),'validation_units']
 ];
 for(const [upstream,reason] of cases){
  const res=await makeHandler(upstream,()=>now)(req,env);
  assert.equal(res.status,502);
  const body=await res.json();assert.equal(body.reason,reason);assert.equal(body.status,'unavailable');
  assert.deepEqual(Object.keys(body).sort(),(reason==='upstream_http'?['status','reason','upstream_status']:['status','reason']).sort());
 }
});

import {collectScheduled} from './worker.mjs';
import {historyEnabled,readHistory,recordHistory,latestHistory,pruneHistory} from './history.mjs';
// D1 API contract test with a deterministic in-memory statement adapter.
// Real schema/binding deployment is checked separately against the user's database.
function fakeHistory(){
 const samples=new Map(),reports=new Map();
 function prepare(sql){return {bind(...args){return {async run(){
  if(sql.startsWith('DELETE FROM awos_samples')){for(const [t] of samples)if(t<args[0]||t>args[1])samples.delete(t);}
  else if(sql.startsWith('DELETE FROM awos_reports')){for(const [k,r] of reports)if(r.observed_ms<args[0]||r.observed_ms>args[1])reports.delete(k);}
  else if(sql.startsWith('INSERT INTO awos_samples')){const [observed_ms,retrieved_ms,payload]=args;if(!samples.has(observed_ms)||samples.get(observed_ms).retrieved_ms<=retrieved_ms)samples.set(observed_ms,{observed_ms,retrieved_ms,payload});}
  else if(sql.startsWith('INSERT OR IGNORE INTO awos_reports')){const [observed_ms,report_text,report_date_utc]=args;reports.set(observed_ms+':'+report_text,{observed_ms,report_text,report_date_utc});}
  else throw Error('unexpected write '+sql);
 },async all(){const m=sql.includes('FROM awos_samples')?samples:reports;const results=[...m.values()].filter(r=>r.observed_ms>=args[0]&&r.observed_ms<=args[1]).sort((a,b)=>b.observed_ms-a.observed_ms);return {results:results.slice(0,sql.endsWith('LIMIT 2')?2:1500)};},async first(){return (await this.all()).results[0]||null;}};}};}
 return {samples,reports,prepare,async batch(stmts){for(const s of stmts)await s.run();}};
}
test('scheduled collection retains unique source timestamps, reports, raw bearing and expires rows',async()=>{
 const db=fakeHistory(),e={...env,AWOS_HISTORY:db,AWOS_HISTORY_ENABLED:'true'};
 let calls=0;const source=async()=>{calls++;return new Response(xml);};
 assert.equal(historyEnabled({...e,AWOS_HISTORY_ENABLED:'false'}),false);
 await collectScheduled({...e,AWOS_HISTORY_ENABLED:'false'},source,()=>now);assert.equal(calls,0);
 await collectScheduled(e,source,()=>now);await collectScheduled(e,source,()=>now+60000);
 assert.equal(calls,2);assert.equal(db.samples.size,1);assert.equal(db.reports.size,1);
 const sample=await latestHistory(e,now+60000);assert.equal(sample.retrieved_at_utc,'2026-01-02T04:01:00Z');assert.equal(sample.metar_date_utc,'2026-01-02');
 const history=await readHistory(e,6,now+60000);assert.equal(history.rows.length,1);assert.equal(history.reports.length,1);assert.equal(history.wind_reference,'unverified_source_bearing');
 assert.equal('metar_text' in history.rows[0],false);
 const h=makeHandler(async()=>{throw Error('must not poll upstream');},()=>now+60000);
 const req=p=>new Request('https://relay.example'+p,{headers:{Origin:origin}});
 assert.equal((await h(req('/awos'),e)).status,200);
 assert.equal((await h(req('/awos-history?hours=6'),e)).status,200);
 assert.equal((await h(req('/awos-history?hours=48'),e)).status,400);
 assert.equal((await h(req('/awos-history?hours=6'),{...e,AWOS_HISTORY_ENABLED:'false'})).status,503);
 assert.equal((await h(new Request('https://relay.example/awos-history?hours=6'),e)).status,403);
 assert.equal(await latestHistory(e,now+600000),null);
 await pruneHistory(e,now+86400000+60000);assert.equal(db.samples.size,0);assert.equal(db.reports.size,0);
});
