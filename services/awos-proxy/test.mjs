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
