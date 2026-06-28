import codecs
import json

with codecs.open(r'D:\UJI_PERFORMA_MODEL\meteologix-wawp\docs\dashboard.js', 'r', encoding='utf-8') as f:
    text = f.read()

fetch_block_old = '''const [intelRes, weightsRes, perfRes, guidanceRes] = await Promise.all([
            fetch('data/tafor_intel.json'),
            fetch('data/latest_weights.json'),
            fetch('data/latest_performance.json'),
            fetch('data/taf_guidance.json')
        ]);'''
fetch_block_new = '''const [intelRes, weightsRes, perfRes, guidanceRes, climRes] = await Promise.all([
            fetch('data/tafor_intel.json'),
            fetch('data/latest_weights.json'),
            fetch('data/latest_performance.json'),
            fetch('data/taf_guidance.json'),
            fetch('data/climatology.json').catch(e => ({ json: () => null }))
        ]);'''
text = text.replace(fetch_block_old, fetch_block_new)

parse_block_old = '''const guidance = await guidanceRes.json();'''
parse_block_new = '''const guidance = await guidanceRes.json();
        const clim_data = climRes && typeof climRes.json === 'function' ? await climRes.json() : null;'''
text = text.replace(parse_block_old, parse_block_new)

regional_logic = '''
        // 5. Regional Charts
        setupRegionalCharts();
        
        // 6. Climatology Engine
        if(clim_data) {
            setupClimatology(clim_data, intel.valid_start);
        }
'''
text = text.replace('''        // 4. Matrix Tab''', regional_logic + '''\n        // 4. Matrix Tab''')

functions = '''
function setupRegionalCharts() {
    const now = new Date();
    // SIGWX Slots
    const slots = [0, 6, 12, 18];
    const currentHour = now.getUTCHours();
    let anchorHour = Math.max(...slots.filter(h => h <= currentHour));
    if(anchorHour === -Infinity) anchorHour = 18; // fallback to previous day if needed
    
    let sigwxHtml = '';
    let firstUrl = '';
    const months = ['01','02','03','04','05','06','07','08','09','10','11','12'];
    
    for(let i=0; i<4; i++) {
        let dt = new Date(now);
        dt.setUTCHours(anchorHour - (6*i), 0, 0, 0);
        let yr = dt.getUTCFullYear();
        let mo = months[dt.getUTCMonth()];
        let da = String(dt.getUTCDate()).padStart(2, '0');
        let hr = String(dt.getUTCHours()).padStart(2, '0');
        let fn = `sigwx_${yr}${mo}${da}${hr}00.jpeg`;
        let url = `https://web-aviation.bmkg.go.id/model/mediumsigwx/${yr}/${mo}/${fn}`;
        if(i===0) firstUrl = url;
        sigwxHtml += `<button onclick="document.getElementById('sigwx-img').src='${url}'" style="background:var(--bg-tertiary); color:var(--text-primary); border:1px solid var(--border-glass); margin-right:8px; padding:5px 10px; border-radius:4px; cursor:pointer;">${da}/${mo} ${hr}Z</button>`;
    }
    document.getElementById('sigwx-slots').innerHTML = sigwxHtml;
    document.getElementById('sigwx-img').src = firstUrl;
    
    // CB Animation
    let yesterday = new Date(now);
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    let yda = String(yesterday.getUTCDate()).padStart(2, '0');
    let ymo = months[yesterday.getUTCMonth()];
    let yyr = yesterday.getUTCFullYear();
    let cbStr = `${yda}${ymo}${yyr}`;
    document.getElementById('cb-img').src = `https://web-aviation.bmkg.go.id/prakcb/${cbStr}/CB_FORECAST_ANIMATION_${cbStr}.gif`;
}

function setupClimatology(clim, valid_start_str) {
    if(!clim.seasonal) return;
    
    const date = new Date(valid_start_str.replace(' ', 'T') + 'Z');
    const m = date.getUTCMonth() + 1;
    let season = 'SON';
    if([12,1,2].includes(m)) season = 'DJF';
    else if([3,4,5].includes(m)) season = 'MAM';
    else if([6,7,8].includes(m)) season = 'JJA';
    
    const cs = clim.seasonal[season];
    if(!cs) return;
    
    let prose = `<p>Based on historical AWOS data, the current season (<strong>${season}</strong>) at Sangia Ni Bandera has the following characteristics:</p>`;
    
    if(cs.rain_frequency_pct >= 60) {
        prose += `<p><strong>Precipitation:</strong> High frequency (~${cs.rain_frequency_pct}%). This is a wet season, convective activity is highly likely and forecasts must be evaluated carefully.</p>`;
    } else if(cs.rain_frequency_pct >= 30) {
        prose += `<p><strong>Precipitation:</strong> Moderate frequency (~${cs.rain_frequency_pct}%). Afternoon convective showers should be monitored.</p>`;
    } else {
        prose += `<p><strong>Precipitation:</strong> Low frequency (~${cs.rain_frequency_pct}%). Generally dry conditions expected.</p>`;
    }
    
    prose += `<p><strong>Wind Profile:</strong> Historical median speed is ${cs.wind_speed_median} kt, with P90 extreme threshold at ${cs.wind_speed_p90} kt.</p>`;
    document.getElementById('climatology-text').innerHTML = prose;
    
    // Wind Rose
    if(cs.wind_dir_freq && cs.wind_speed_by_sector) {
        const angles = Object.keys(cs.wind_dir_freq).map(Number).sort((a,b)=>a-b);
        const freqs = angles.map(a => parseFloat(cs.wind_dir_freq[a.toString()]));
        
        const options = {
            series: freqs,
            chart: { type: 'polarArea', height: 400, background: 'transparent' },
            labels: angles.map(a => a + '°'),
            stroke: { colors: ['var(--border-glass)'] },
            fill: { opacity: 0.8 },
            theme: { mode: 'dark' },
            legend: { show: false }
        };
        const chart = new ApexCharts(document.querySelector("#chart-wind-rose"), options);
        chart.render();
    }
}
'''
text += '\n' + functions

with codecs.open(r'D:\UJI_PERFORMA_MODEL\meteologix-wawp\docs\dashboard.js', 'w', encoding='utf-8') as f:
    f.write(text)

print('Updated dashboard.js completely!')
