async function loadDashboard() {
    try {
        const cb = '?t=' + new Date().getTime();
        const [intelRes, weightsRes, perfRes, dataRes, climRes, modelsRes] = await Promise.all([
            fetch('data/tafor_intel.json' + cb),
            fetch('data/latest_weights.json' + cb),
            fetch('data/latest_performance.json' + cb),
            fetch('data/taf_guidance.json' + cb),
            fetch('data/climatology.json' + cb),
            fetch('data/individual_models.json' + cb)
        ]);
        
        const intelData = await intelRes.json();
        let currentIssuance = "2300";
        let intel = intelData[currentIssuance] || intelData;
        const weights = await weightsRes.json();
        const perf = await perfRes.json();
        const guidanceJson = await dataRes.json();
        const data = guidanceJson.data;
        const generatedAt = guidanceJson.metadata ? guidanceJson.metadata.generated_at : intel.valid_start;

        const clim_data = (climRes && climRes.ok) ? await climRes.json().catch(()=>null) : null;
        const modelsData = (modelsRes && modelsRes.ok && typeof modelsRes.json === 'function') ? await modelsRes.json().catch(()=>null) : null;

        // 1. Update Header
        document.getElementById('update-time').innerText = generatedAt + " UTC";
        
        function renderIntel(intelObj) {
            document.getElementById('taf-text-display').innerText = intelObj.taf_text || "TAF Unavailable";
            
            const bg = intelObj.base_group;
            if(!bg) return;
            const bgHtml = `
                <div class="param-card">
                    <div class="param-label">Wind Direction</div>
                    <div class="param-value">${bg.dir}°</div>
                </div>
                <div class="param-card">
                    <div class="param-label">Wind Speed</div>
                    <div class="param-value">${bg.spd} kt</div>
                </div>
                <div class="param-card">
                    <div class="param-label">Visibility</div>
                    <div class="param-value">${bg.vis} m</div>
                </div>
                <div class="param-card">
                    <div class="param-label">Weather</div>
                    <div class="param-value">${bg.wx || '-'}</div>
                </div>
                <div class="param-card">
                    <div class="param-label">Cloud</div>
                    <div class="param-value">${bg.cloud || '-'}</div>
                </div>
            `;
            document.getElementById('bg-params').innerHTML = bgHtml;
            const badge = document.getElementById('consensus-badge');
            if(badge) badge.innerText = bg.badge || "MME Consensus";
            
            const warnings = intelObj.warnings;
            if(warnings && warnings.length > 0) {
                document.getElementById('taf-warnings').innerHTML = warnings.map(w => `<div style="color:var(--amber); margin-top:10px;">⚠️ ${w}</div>`).join('');
            } else {
                document.getElementById('taf-warnings').innerHTML = '';
            }
            
            // Populate Narration
            const narrationBox = document.getElementById('taf-narration');
            if (narrationBox) {
                narrationBox.innerText = intelObj.narration || "Narasi tidak tersedia untuk saat ini.";
            }

            // Create manual edit boxes
            const tafLines = (intelObj.taf_text || "").split('\n');
            const manualContainer = document.getElementById('taf-manual-boxes');
            if(manualContainer) {
                manualContainer.innerHTML = '';
                tafLines.forEach(line => {
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.value = line;
                    input.className = 'manual-taf-input';
                    input.style.width = '100%';
                    input.style.padding = '8px';
                    input.style.background = 'var(--bg-tertiary)';
                    input.style.color = 'var(--text-primary)';
                    input.style.border = '1px solid var(--border-glass)';
                    input.style.borderRadius = '4px';
                    manualContainer.appendChild(input);
                });
            }
        }
        
        renderIntel(intel);
        
        const tafSelect = document.getElementById('taf-issuance-select');
        if(tafSelect) {
            tafSelect.addEventListener('change', (e) => {
                currentIssuance = e.target.value;
                if(intelData[currentIssuance]) {
                    renderIntel(intelData[currentIssuance]);
                }
            });
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
        // Filter data to only show current and future hours
        const now = new Date();
        const localNow = new Date(now.getTime() + (8 * 60 * 60 * 1000));
        const witaHourStr = localNow.toISOString().replace('T', ' ').substring(0, 13) + ':00:00';
        
        const futureData = data.filter(d => d.Datetime >= witaHourStr);
        const renderData = futureData.length > 0 ? futureData : data;

        // 4. Meteogram Tab (using apexcharts from guidance)
        // Convert to [timestamp_ms, value] format expected by apexcharts-theme.js
        const chartData = {
            tempData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Temperature]),
            dewData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Dewpoint]),
            windData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Wind]),
            gustData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Gust || 0]),
            rainData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Rain]),
            windDirData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d['Wind Dir.'] || 0]),
            highCloudData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d['High Clouds'] || 0]),
            midCloudData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d['Mid Clouds'] || 0]),
            lowCloudData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d['Low Clouds'] || 0]),
            condData: renderData.map(d => [new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime(), d.Condition || 'Normal'])
        };
        
        // Initialize charts using the new TITAN_BASE design system
        initTitanCharts(chartData);
        
        // 5. Matrix Tab
        const tbody = document.querySelector("#data-table tbody");
        renderData.forEach(d => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${d.Datetime.substring(11, 16)}</td>
                <td style="color: #ef4444">${Number(d.Temperature).toFixed(1)}</td>
                <td style="color: #3b82f6">${Number(d.Dewpoint).toFixed(1)}</td>
                <td>${Number(d['Wind Dir.'] || 0).toFixed(0)}°</td>
                <td style="color: #10b981">${Number(d.Wind).toFixed(1)}</td>
                <td style="color: #0ea5e9">${Number(d.Rain).toFixed(1)}</td>
                <td>${Number(d['Prob Precip 1.0mm'] || 0) > 0 ? Number(d['Prob Precip 1.0mm']).toFixed(1) + '%' : '-'}</td>
                <td>${Number(d['Prob Precip 10.0mm'] || 0) > 0 ? Number(d['Prob Precip 10.0mm']).toFixed(1) + '%' : '-'}</td>
                <td>${d.Condition || '-'}</td>
            `;
            tbody.appendChild(tr);
        });
        
        // 5.5 Individual Models
        if (modelsData && renderData) {
            setupIndividualModels(modelsData, renderData.map(d=>d.Datetime));
            setupSpreadCharts(modelsData, renderData.map(d=>d.Datetime));
        }
        
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

function setupSpreadCharts(modelsData, timeLabels) {
    // Reusable function to create spread series
    const createSeries = (param) => {
        const series = [];
        for (const [model, modelVals] of Object.entries(modelsData[param] || {})) {
            // align with timeLabels
            const dataPts = [];
            for (const t of timeLabels) {
                const ts = new Date(t.replace(' ', 'T') + 'Z').getTime();
                if (modelVals[t] !== undefined && modelVals[t] !== null) {
                    dataPts.push([ts, modelVals[t]]);
                }
            }
            series.push({ name: model, data: dataPts });
        }
        return series;
    };

    const spreadOptions = (title, yAxisLabel, isBar=false) => ({
        ...TITAN_BASE,
        chart: { ...TITAN_BASE.chart, type: isBar ? 'bar' : 'line', height: 250 },
        title: { text: title, style: { fontSize: '12px', fontWeight: 'bold', fontFamily: TITAN_COLORS.font } },
        stroke: { curve: 'smooth', width: 1.5 },
        dataLabels: { enabled: false },
        tooltip: { enabled: false },
        markers: { size: 0 },
        xaxis: { type: 'datetime', labels: { style: { colors: TITAN_COLORS.text } } },
        yaxis: { title: { text: yAxisLabel }, labels: { style: { colors: TITAN_COLORS.text } } },
        legend: { position: 'right' }
    });

    new ApexCharts(document.querySelector('#spread-temp'), { ...spreadOptions('Temperature Spread', '°C'), series: createSeries('Temperature') }).render();
    new ApexCharts(document.querySelector('#spread-wind'), { ...spreadOptions('Wind Speed Spread', 'kt'), series: createSeries('Wind Speed') }).render();
    new ApexCharts(document.querySelector('#spread-rain'), { ...spreadOptions('Rainfall Spread', 'mm', true), series: createSeries('Rainfall') }).render();
}

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
    
    // Upper Air Charts
    const uaDate = document.getElementById('ua-date');
    const uaSelect = document.getElementById('ua-chart-select');
    const uaImg = document.getElementById('ua-img');
    const uaUrl = document.getElementById('ua-url');
    
    if(uaDate && uaSelect && uaImg) {
        let uaDt = new Date(now);
        let uaYr = uaDt.getUTCFullYear();
        let uaMo = String(uaDt.getUTCMonth()+1).padStart(2, '0');
        let uaDa = String(uaDt.getUTCDate()).padStart(2, '0');
        uaDate.value = `${uaYr}-${uaMo}-${uaDa}`;
        
        function updateUaImage() {
            const dateStr = uaDate.value; // YYYY-MM-DD
            if(!dateStr) return;
            const [y, m, d] = dateStr.split('-');
            const ddmmyyyy = `${d}${m}${y}`;
            const file = uaSelect.value;
            const finalUrl = `https://web-aviation.bmkg.go.id/rason/${ddmmyyyy}/${file}`;
            uaImg.src = finalUrl;
            if(uaUrl) uaUrl.innerText = finalUrl;
        }
        
        uaDate.addEventListener('change', updateUaImage);
        uaSelect.addEventListener('change', updateUaImage);
        updateUaImage();
    }
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

function setupIndividualModels(modelsData, timeLabels) {
    const modelSelect = document.getElementById('model-select');
    const tbody = document.querySelector('#model-table tbody');
    if(!modelSelect || !tbody) return;
    
    function renderModel(modelName) {
        tbody.innerHTML = '';
        if(!timeLabels) return;
        
        const initBadge = document.getElementById('model-init-badge');
        if (initBadge && modelsData['Run_Init'] && modelsData['Run_Init'][modelName]) {
            let initStr = modelsData['Run_Init'][modelName];
            if (initStr === "Unknown") {
                initBadge.innerText = "Init: Unknown";
                initBadge.style.color = "var(--text-secondary)";
                initBadge.style.borderColor = "var(--border-glass)";
            } else {
                // Ensure it has Z suffix
                if (!initStr.endsWith('Z')) {
                    initStr = initStr.replace(' ', 'T') + 'Z';
                }
                const d = new Date(initStr);
                const hrs = String(d.getUTCHours()).padStart(2, '0');
                const mns = String(d.getUTCMinutes()).padStart(2, '0');
                initBadge.innerText = `Init: ${hrs}${mns}Z`;
                initBadge.style.color = "var(--cyan)";
                initBadge.style.borderColor = "rgba(0, 212, 255, 0.25)";
            }
        }
        
        timeLabels.forEach(dt => {
            const temp = modelsData['Temperature']?.[modelName]?.[dt];
            const dew = modelsData['Dewpoint']?.[modelName]?.[dt];
            const windDir = modelsData['Wind Dir.']?.[modelName]?.[dt];
            const windSpd = modelsData['Wind Speed']?.[modelName]?.[dt];
            const gust = modelsData['Wind Gust']?.[modelName]?.[dt];
            const rain = modelsData['Rainfall']?.[modelName]?.[dt];
            const probRain = modelsData['Prob Precip 1.0mm']?.[modelName]?.[dt];
            const probRain10 = modelsData['Prob Precip 10.0mm']?.[modelName]?.[dt];
            
            if(temp !== undefined) {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${dt.substring(11, 16)}</td>
                    <td style="color: #ef4444">${Number(temp).toFixed(1)}</td>
                    <td style="color: #3b82f6">${Number(dew).toFixed(1)}</td>
                    <td>${windDir !== undefined ? Number(windDir).toFixed(0) + '°' : '-'}</td>
                    <td style="color: #10b981">${windSpd !== undefined ? Number(windSpd).toFixed(1) : '-'}</td>
                    <td style="color: #f59e0b">${gust !== undefined ? Number(gust).toFixed(1) : '-'}</td>
                    <td style="color: #6366f1">${rain !== undefined ? Number(rain).toFixed(1) : '-'}</td>
                    <td>${probRain !== undefined && probRain > 0 ? Number(probRain).toFixed(1) + '%' : '-'}</td>
                    <td>${probRain10 !== undefined && probRain10 > 0 ? Number(probRain10).toFixed(1) + '%' : '-'}</td>
                `;
                tbody.appendChild(tr);
            }
        });
    }
    
    modelSelect.addEventListener('change', (e) => {
        renderModel(e.target.value);
    });
    
    // Render first model initially
    renderModel(modelSelect.value);
}

// Copy TAF button logic
document.addEventListener('DOMContentLoaded', () => {
    let isManualOverride = false;
    
    const toggleBtn = document.getElementById('toggle-manual-btn');
    const tafTextDisplay = document.getElementById('taf-text-display');
    const tafManualBoxes = document.getElementById('taf-manual-boxes');
    
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            isManualOverride = !isManualOverride;
            if (isManualOverride) {
                toggleBtn.innerText = 'Cancel Manual';
                toggleBtn.style.color = 'var(--amber)';
                toggleBtn.style.borderColor = 'var(--amber)';
                tafTextDisplay.style.display = 'none';
                tafManualBoxes.style.display = 'flex';
            } else {
                toggleBtn.innerText = 'Manual Override';
                toggleBtn.style.color = 'var(--text-secondary)';
                toggleBtn.style.borderColor = 'var(--border-glass)';
                tafTextDisplay.style.display = 'block';
                tafManualBoxes.style.display = 'none';
            }
        });
    }

    const copyBtn = document.getElementById('copy-taf-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', async () => {
            let tafText = "";
            if (isManualOverride) {
                const inputs = tafManualBoxes.querySelectorAll('.manual-taf-input');
                const lines = [];
                inputs.forEach(input => {
                    if (input.value.trim() !== "") {
                        lines.push(input.value.trim());
                    }
                });
                tafText = lines.join('\n');
            } else {
                tafText = document.getElementById('taf-text-display').innerText;
            }
            
            try {
                await navigator.clipboard.writeText(tafText);
                const originalText = copyBtn.innerText;
                copyBtn.innerText = 'Copied!';
                copyBtn.style.background = 'var(--green-glow)';
                copyBtn.style.color = 'var(--green)';
                copyBtn.style.borderColor = 'rgba(0, 230, 118, 0.25)';
                setTimeout(() => {
                    copyBtn.innerText = originalText;
                    copyBtn.style.background = 'var(--cyan-glow)';
                    copyBtn.style.color = 'var(--cyan)';
                    copyBtn.style.borderColor = 'rgba(0, 212, 255, 0.25)';
                }, 2000);
            } catch (err) {
                console.error('Failed to copy text: ', err);
            }
        });
    }
});
