const DAY=86400000,HOUR=3600000;
export const HOURS=[3,6,12,24];
export const historyEnabled=env=>env.AWOS_HISTORY_ENABLED==='true'&&env.PUBLIC_FEED_AUTHORIZED==='true'&&env.DISPLAY_CLOCK_UTC_CONFIRMED==='true'&&env.WIND_TRUE_CONFIRMED==='true'&&!!env.AWOS_HISTORY;
const db=env=>env.AWOS_HISTORY;
export async function pruneHistory(env,now){
 if(!db(env))return;
 await db(env).batch([
  db(env).prepare('DELETE FROM awos_samples WHERE observed_ms < ? OR observed_ms > ?').bind(now-DAY,now),
  db(env).prepare('DELETE FROM awos_reports WHERE observed_ms < ? OR observed_ms > ?').bind(now-DAY,now)
 ]);
}
export async function recordHistory(env,sample,now){
 if(!historyEnabled(env))throw Error('history_disabled');
 const t=Date.parse(sample.observed_at_utc),r=Date.parse(sample.retrieved_at_utc);
 if(!Number.isFinite(t)||!Number.isFinite(r)||t>r||r>now||now-t>300000)throw Error('history_clock');
 const {metar_text,metar_date_utc,...payload}=sample;
 // Preserve the original raw bearing; do not infer true/magnetic conversion.
 const statements=[db(env).prepare('INSERT OR IGNORE INTO awos_samples (observed_ms,retrieved_ms,payload) VALUES (?,?,?)').bind(t,r,JSON.stringify(payload))];
 if(typeof metar_text==='string'&&metar_text.length<=400&&typeof metar_date_utc==='string'){
  const m=/^(?:METAR|SPECI)(?: COR)? WAWP (\d{2})(\d{2})(\d{2})Z(?: |$)/.exec(metar_text);
  if(m&&metar_date_utc.slice(8)===m[1]){
   const at=Date.parse(metar_date_utc+'T'+m[2]+':'+m[3]+':00Z');
   if(Number.isFinite(at)&&at<=t&&at>=now-DAY)statements.push(db(env).prepare('INSERT OR IGNORE INTO awos_reports (observed_ms,report_text,report_date_utc) VALUES (?,?,?)').bind(at,metar_text,metar_date_utc));
  }
 }
 await db(env).batch(statements);
}
export async function latestHistory(env,now){
 const row=await db(env).prepare('SELECT observed_ms,retrieved_ms,payload FROM awos_samples WHERE observed_ms BETWEEN ? AND ? ORDER BY observed_ms DESC LIMIT 1').bind(now-300000,now).first();
 if(!row||row.retrieved_ms>now||now-row.retrieved_ms>120000)return null;
 const payload=JSON.parse(row.payload);
 const reports=await db(env).prepare('SELECT report_text,report_date_utc FROM awos_reports WHERE observed_ms BETWEEN ? AND ? ORDER BY observed_ms DESC,report_text LIMIT 2').bind(now-3600000,row.observed_ms).all();
 // Avoid guessing which same-time correction is authoritative.
 const r=reports.results||[];
 if(r.length===1||r.length===2&&r[0].report_text.match(/ WAWP \d{6}Z/)?.[0]!==r[1].report_text.match(/ WAWP \d{6}Z/)?.[0]){payload.metar_text=r[0].report_text;payload.metar_date_utc=r[0].report_date_utc;}
 return payload;
}
export async function readHistory(env,hours,now){
 if(!HOURS.includes(hours))throw Error('history_window');
 const start=now-hours*HOUR;
 const [samples,reports]=await Promise.all([
  db(env).prepare('SELECT payload FROM awos_samples WHERE observed_ms BETWEEN ? AND ? ORDER BY observed_ms DESC LIMIT 1500').bind(start,now).all(),
  db(env).prepare('SELECT observed_ms,report_text,report_date_utc FROM awos_reports WHERE observed_ms BETWEEN ? AND ? ORDER BY observed_ms DESC LIMIT 1500').bind(start,now).all()
 ]);
 return {schema:'titan-awos-history-v1',station:'WAWP',source:'AWOSnet',retention_hours:24,window_hours:hours,generated_at_utc:new Date(now).toISOString().slice(0,19)+'Z',wind_reference:'unverified_source_bearing',rows:(samples.results||[]).reverse().map(r=>JSON.parse(r.payload)),reports:(reports.results||[]).reverse().map(r=>({observed_at_utc:new Date(r.observed_ms).toISOString().slice(0,19)+'Z',report_text:r.report_text,report_date_utc:r.report_date_utc}))};
}
