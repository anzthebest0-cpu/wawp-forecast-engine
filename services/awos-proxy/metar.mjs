// Independent, allowlisted NOAA METAR adapter. No AWOS dependency or storage.
export const METAR_URL='https://aviationweather.gov/api/data/metar?ids=WAWP&format=json&hours=3';
const stamp=t=>new Date(t).toISOString().slice(0,19)+'Z';
export function normalizeMetar(rows,now){
 if(!Array.isArray(rows)||rows.length>100)throw Error('schema');
 const found=[];
 for(const x of rows){
  if(x?.icaoId!=='WAWP'||!Number.isInteger(x.obsTime)||typeof x.rawOb!=='string'||x.rawOb.length>400)throw Error('identity');
  const at=x.obsTime*1000;
  if(!Number.isFinite(at)||at>now)throw Error('clock');
  let raw=x.rawOb.trim().replace(/=$/,'').trim();
  if(/[\x00-\x1f\x7f]/.test(raw))throw Error('report');
  raw=raw.toUpperCase().replace(/ +/g,' ');
  if(/^WAWP /.test(raw)&&['METAR','SPECI'].includes(x.metarType))raw=x.metarType+' '+raw;
  const m=/^(METAR|SPECI)(?: COR)? WAWP (\d{2})(\d{2})(\d{2})Z(?: |$)/.exec(raw);
  if(!m||stamp(at).slice(8,10)!==m[2]||stamp(at).slice(11,13)!==m[3]||stamp(at).slice(14,16)!==m[4]||at%60000!==0)throw Error('report timestamp');
  found.push({at,raw,corrected:/\bCOR\b/.test(raw)});
 }
 if(!found.length)return null;
 const latest=Math.max(...found.map(x=>x.at));
 const tied=found.filter(x=>x.at===latest);
 const candidates=tied.some(x=>x.corrected)?tied.filter(x=>x.corrected):tied;
 if(new Set(candidates.map(x=>x.raw)).size!==1)throw Error('conflicting reports');
 const raw=candidates[0].raw;
 if(/\bNIL\b/.test(raw)||now-latest>3600000)return null;
 return {schema:'titan-live-metar-v1',station:'WAWP',source:'NOAA AWC',report_text:raw,observed_at_utc:stamp(latest),retrieved_at_utc:stamp(now)};
}
