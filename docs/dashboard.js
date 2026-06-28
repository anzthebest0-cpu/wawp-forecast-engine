async function loadDashboard() {
    try {
        const [intelRes, weightsRes, perfRes, guidanceRes, climRes] = await Promise.all([
            fetch('data/tafor_intel.json'),
            fetch('data/latest_weights.json'),
            fetch('data/latest_performance.json'),
            fetch('data/taf_guidance.json'),
            fetch('data/climatology.json')
        ]);
        
        const intel = await intelRes.json();
        const weights = await weightsRes.json();
        const perf = await perfRes.json();
        const guidance = await guidanceRes.json();
        const clim_data = (climRes && climRes.ok && typeof climRes.json === 'function') ? await climRes.json().catch(()=>null) : null;

        // 1. Update Header
        document.getElementById('update-time').innerText = intel.valid_start + " UTC";
        
        // 2. Intelligence Tab (TAF & Base Group)
        document.getElementById('taf-text-display').innerText = intel.taf_text;
        
        const bg = intel.base_group;
        const bgHtml = `
            <div class="param-card">
                <div class="param-label">Wind Direction</div>
                <div class="param-value">A</div>
            </div>
            <div class="param-card">
                <div class="param-label">Wind Speed</div>
                <div class="param-value"> kt</div>
            </div>
            <div class="param-card">
                <div class="param-label">Visibility</div>
                <div class="param-value"> m</div>
            </div>
            <div class="param-card">
                <div class="param-label">Weather</div>
                <div class="param-value"></div>
            </div>
            <div class="param-card">
                <div class="param-label">Cloud</div>
                <div class="param-value"></div>
            </div>
        `;
        document.getElementById('bg-params').innerHTML = bgHtml;
        
        const warnings = intel.warnings;
        if(warnings && warnings.length > 0) {
            document.getElementById('taf-warnings').innerHTML = warnings.map(w => `<div style="color:var(--amber); margin-top:10px;">A </div>`).join('');
        }

        
        // 3. Weighter Tab
        const wContainer = document.getElementById('weighter-container');
        let wHtml = '';
        for (const [param, modelWeights] of Object.entries(weights.weights)) {
            wHtml += `<div class="weight-card"><h3 style="color:var(--accent); margin-top:0;">${param} Weights</h3>`;
            const sortedModels = Object.entries(modelWeights).sort((a,b) => b[1] - a[1]);
            for (const [model, weight] of sortedModels) {
                const wPct = (weight * 100).toFixed(1);
                wHtml += `
                    <div class="weight-row">
                        <span>${model}</span>
                        <span>${wPct}%</span>
                    </div>
                    <div class="weight-bar-bg">
                        <div class="weight-bar-fill" style="width: ${wPct}%"></div>
                    </div>
                `;
            }
            wHtml += `</div>`;
        }
        wContainer.innerHTML = wHtml;
        
        // 4. Meteogram Tab (using apexcharts from guidance)
        const data = guidance.data;
        
        // Convert to [timestamp_ms, value] format expected by apexcharts-theme.js
        const chartData = {
            tempData: data.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Temperature]),
            dewData: data.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Dewpoint]),
            windData: data.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Wind]),
            gustData: data.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Gust || 0]),
            rainData: data.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Rain])
        };
        
        // Initialize charts using the new TITAN_BASE design system
        initTitanCharts(chartData);
        
        // 5. Matrix Tab
        const tbody = document.querySelector("#data-table tbody");
        data.forEach(d => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${d.Datetime.substring(11, 16)}</td>
                <td style="color: #ef4444">${Number(d.Temperature).toFixed(1)}</td>
                <td style="color: #3b82f6">${Number(d.Dewpoint).toFixed(1)}</td>
                <td>${Number(d['Wind Dir.'] || 0).toFixed(0)}°</td>
                <td style="color: #10b981">${Number(d.Wind).toFixed(1)}</td>
                <td style="color: #0ea5e9">${Number(d.Rain).toFixed(1)}</td>
                <td>${d.Condition || '-'}</td>
            `;
            tbody.appendChild(tr);
        });
        
        // 6. Regional & Climatology
        setupRegionalCharts();
        if (clim_data && intel) {
            setupClimatology(clim_data, intel.valid_start);
        }
        
    } catch (e) {
        console.error(e);
        document.getElementById('update-time').innerText = "Load failed";
    }
}

function switchTab(tabId) {
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-links li').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tabId).classList.add('active');
    document.querySelector(`.nav-links li[data-tab="${tabId}"]`).classList.add('active');
}

// Global setup
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.nav-links li').forEach(li => {
        li.addEventListener('click', () => {
            switchTab(li.getAttribute('data-tab'));
        });
    });
    
    loadDashboard();
});


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
    if(!clim || !clim.seasonal) return;
    
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
