async function loadDashboard() {
    try {
        const cb = '?t=' + new Date().getTime();
        const results = await Promise.allSettled([
            fetch('data/tafor_intel.json' + cb).then(r => r.json()),
            fetch('data/latest_weights.json' + cb).then(r => r.json()),
            fetch('data/latest_performance.json' + cb).then(r => r.json()),
            fetch('data/taf_guidance.json' + cb).then(r => r.json()),
            fetch('data/climatology.json' + cb).then(r => r.json()),
            fetch('data/individual_models.json' + cb).then(r => r.json()),
            fetch('data/db_health.json' + cb).then(r => r.json()),
            fetch('data/persistency.json' + cb).then(r => r.json())
        ]);
        
        const intelData = results[0].status === 'fulfilled' ? results[0].value : {};
        let currentIssuance = "2300";
        let intel = intelData[currentIssuance] || intelData || {};
        const weights = results[1].status === 'fulfilled' ? results[1].value : {weights: {}};
        const perf = results[2].status === 'fulfilled' ? results[2].value : null;
        const guidanceJson = results[3].status === 'fulfilled' ? results[3].value : {data: []};
        const data = guidanceJson.data || [];
        const generatedAt = guidanceJson.metadata ? guidanceJson.metadata.generated_at : (intel.valid_start || "Unknown");

        // Filter data to only show current and future hours
        const now = new Date();
        const localNow = new Date(now.getTime() + (8 * 60 * 60 * 1000));
        const witaHourStr = localNow.toISOString().replace('T', ' ').substring(0, 13) + ':00:00';
        
        const futureData = data.filter(d => d.Datetime >= witaHourStr);
        const renderData = futureData.length > 0 ? futureData : data;

        const clim_data = results[4].status === 'fulfilled' ? results[4].value : null;
        const modelsData = results[5].status === 'fulfilled' ? results[5].value : null;
        
        const healthData = results[6].status === 'fulfilled' ? results[6].value : null;
        if (healthData) {
            const fcElem = document.getElementById('db-forecast-count');
            const obsElem = document.getElementById('db-obs-count');
            const sizeElem = document.getElementById('db-size');
            const syncElem = document.getElementById('db-last-sync');
            
            if (fcElem) fcElem.innerText = healthData.forecast_records.toLocaleString();
            if (obsElem) obsElem.innerText = healthData.observation_records.toLocaleString();
            if (sizeElem) sizeElem.innerText = healthData.size_mb + ' MB';
            if (syncElem) syncElem.innerText = healthData.last_sync_utc + ' UTC';
        }

        function startShiftCountdown() {
            const labelEl = document.getElementById('next-taf-issuance-label');
            const cdEl = document.getElementById('shift-countdown');
            if(!labelEl || !cdEl) return;
            
            function update() {
                const now = new Date();
                const h = now.getUTCHours();
                const m = now.getUTCMinutes();
                const s = now.getUTCSeconds();
                
                const shifts = [5, 11, 17, 23];
                let next = shifts.find(shift => shift > h || (shift === h && m === 0 && s === 0));
                
                let target = new Date(now);
                if (next === undefined) {
                    next = 5;
                    target.setUTCDate(target.getUTCDate() + 1);
                }
                target.setUTCHours(next, 0, 0, 0);
                
                const diff = target - now;
                const hrs = Math.floor(diff / 3600000);
                const mins = Math.floor((diff % 3600000) / 60000);
                const secs = Math.floor((diff % 60000) / 1000);
                
                labelEl.innerText = `${String(next).padStart(2, '0')}00Z`;
                cdEl.innerText = `-${String(hrs).padStart(2,'0')}:${String(mins).padStart(2,'0')}:${String(secs).padStart(2,'0')}`;
                
                if(diff < 3600000) { // < 1 hour
                    document.getElementById('shift-status-banner').style.background = 'rgba(245, 158, 11, 0.1)';
                    document.getElementById('shift-status-banner').style.borderLeft = '4px solid var(--amber)';
                    cdEl.style.color = 'var(--amber)';
                }
            }
            update();
            setInterval(update, 1000);
        }
        startShiftCountdown();
        
        // TS Probability logic
        const tsProbVal = document.getElementById('ts-prob-val');
        const tsProbTime = document.getElementById('ts-prob-time');
        if (tsProbVal && tsProbTime && renderData && renderData.length > 0) {
            let maxProb = 0;
            let peakTime = "";
            renderData.forEach(d => {
                let prob = d['Prob Precip 10.0mm'] || 0;
                if(prob > maxProb) {
                    maxProb = prob;
                    peakTime = d.Datetime.substring(11, 16);
                }
            });
            tsProbVal.innerText = maxProb.toFixed(0) + '%';
            if (maxProb > 0) {
                tsProbTime.innerText = `Peak window: ${peakTime} WITA`;
            } else {
                tsProbTime.innerText = `No significant risk`;
            }
            if (maxProb >= 30) {
                tsProbVal.style.color = 'var(--red)';
            }
        }

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
        // Render data is already filtered above

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
        renderData.forEach((d, idx) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>
                    ${d.Datetime.substring(11, 16)}<br>
                    <span style="font-size:0.8em; color:var(--text-secondary)">+${idx}h</span>
                </td>
                <td style="color: #ef4444">${Number(d.Temperature).toFixed(1)}</td>
                <td style="color: #3b82f6">${Number(d.Dewpoint).toFixed(1)}</td>
                <td style="color: #a855f7">${Number(d.Pressure).toFixed(1)}</td>
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
        
        // 7. Verification & Persistency
        const persData = results[7].status === 'fulfilled' ? results[7].value : null;
        if (perf) setupVerification(perf);
        if (persData) setupPersistency(persData);
        
    } catch (e) {
        console.error(e);
        document.getElementById('update-time').innerText = "Load failed: " + (e.message || e);
        const tafDisplay = document.getElementById('taf-text-display');
        if (tafDisplay) tafDisplay.innerText = e.stack || e;
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
    // Theme toggle logic
    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) {
        if (localStorage.getItem('theme') === 'light') {
            document.documentElement.classList.add('light-mode');
        }
        themeBtn.addEventListener('click', () => {
            document.documentElement.classList.toggle('light-mode');
            const isLight = document.documentElement.classList.contains('light-mode');
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
        });
    }

    document.querySelectorAll('.nav-links li').forEach(li => {
        li.addEventListener('click', () => {
            switchTab(li.getAttribute('data-tab'));
        });
    });
    
    loadDashboard();
});

function setupSpreadCharts(modelsData, timeLabels) {
    const getPercentile = (dataArr, p) => {
        if(dataArr.length === 0) return null;
        dataArr.sort((a,b) => a-b);
        const index = (dataArr.length - 1) * p;
        const lower = Math.floor(index);
        const upper = Math.ceil(index);
        const weight = index - lower;
        if(upper >= dataArr.length) return dataArr[lower];
        return dataArr[lower] * (1 - weight) + dataArr[upper] * weight;
    };

    const createPlumeSeries = (param) => {
        const rangeMaxMin = [];
        const rangeIQR = [];
        const medLine = [];
        
        for (const t of timeLabels) {
            const ts = new Date(t.replace(' ', 'T') + 'Z').getTime();
            const vals = [];
            for (const [model, modelVals] of Object.entries(modelsData[param] || {})) {
                if (modelVals[t] !== undefined && modelVals[t] !== null && model !== 'Multi-Model') {
                    vals.push(modelVals[t]);
                }
            }
            if (vals.length > 0) {
                rangeMaxMin.push({ x: ts, y: [getPercentile(vals, 0.0), getPercentile(vals, 1.0)] });
                rangeIQR.push({ x: ts, y: [getPercentile(vals, 0.25), getPercentile(vals, 0.75)] });
                medLine.push({ x: ts, y: getPercentile(vals, 0.50) });
            }
        }
        return [
            { name: 'Min-Max Range', type: 'rangeArea', data: rangeMaxMin },
            { name: 'Interquartile Range', type: 'rangeArea', data: rangeIQR },
            { name: 'Median', type: 'line', data: medLine }
        ];
    };

    const createStackedSeries = (param) => {
        const series = [];
        for (const [model, modelVals] of Object.entries(modelsData[param] || {})) {
            if (model === 'Multi-Model') continue;
            const dataPts = [];
            for (const t of timeLabels) {
                const ts = new Date(t.replace(' ', 'T') + 'Z').getTime();
                if (modelVals[t] !== undefined && modelVals[t] !== null) {
                    dataPts.push([ts, modelVals[t]]);
                }
            }
            series.push({ name: model, data: dataPts, type: 'area' });
        }
        return series;
    };

    const spreadOptions = (title, yAxisLabel, isStacked=false) => ({
        ...TITAN_BASE,
        chart: { ...TITAN_BASE.chart, type: 'area', height: 280, stacked: isStacked },
        title: { text: title, style: { fontSize: '13px', fontWeight: 'bold', color: 'var(--text-primary)' } },
        stroke: { curve: 'smooth', width: isStacked ? 1 : [0, 0, 2] },
        dataLabels: { enabled: false },
        markers: { size: 0 },
        xaxis: { type: 'datetime', labels: { style: { colors: 'var(--text-secondary)' } } },
        yaxis: { title: { text: yAxisLabel }, labels: { style: { colors: 'var(--text-secondary)' }, formatter: (val) => val.toFixed(1) } },
        legend: { position: 'top' },
        fill: isStacked ? { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.6, opacityTo: 0.2 } } : { opacity: [0.15, 0.4, 1] }
    });

    const tempOptions = spreadOptions('Temperature Plume (°C)', '°C');
    tempOptions.colors = ['#ef4444', '#ef4444', '#ef4444'];
    new ApexCharts(document.querySelector('#spread-temp'), { ...tempOptions, series: createPlumeSeries('Temperature') }).render();
    
    // T - Td Spread (Fog Risk) Plume
    const createSpreadTTdPlume = () => {
        const rangeMaxMin = [];
        const rangeIQR = [];
        const medLine = [];
        for (const t of timeLabels) {
            const ts = new Date(t.replace(' ', 'T') + 'Z').getTime();
            const vals = [];
            for (const [model, modelVals] of Object.entries(modelsData['Temperature'] || {})) {
                if (model === 'Multi-Model') continue;
                const temp = modelsData['Temperature'][model]?.[t];
                const dew = modelsData['Dewpoint']?.[model]?.[t];
                if (temp !== undefined && dew !== undefined) {
                    vals.push(Math.max(0, temp - dew));
                }
            }
            if (vals.length > 0) {
                rangeMaxMin.push({ x: ts, y: [getPercentile(vals, 0.0), getPercentile(vals, 1.0)] });
                rangeIQR.push({ x: ts, y: [getPercentile(vals, 0.25), getPercentile(vals, 0.75)] });
                medLine.push({ x: ts, y: getPercentile(vals, 0.50) });
            }
        }
        return [
            { name: 'Min-Max Range', type: 'rangeArea', data: rangeMaxMin },
            { name: 'Interquartile Range', type: 'rangeArea', data: rangeIQR },
            { name: 'Median', type: 'line', data: medLine }
        ];
    };
    
    const ttdContainer = document.querySelector('#spread-t-td');
    if (ttdContainer) {
        const ttdOptions = spreadOptions('T - Td Plume (Fog Risk)', '°C');
        ttdOptions.colors = ['#a855f7', '#a855f7', '#a855f7'];
        new ApexCharts(ttdContainer, { ...ttdOptions, series: createSpreadTTdPlume() }).render();
    }
    
    const windOptions = spreadOptions('Wind Speed Plume (kt)', 'kt');
    windOptions.colors = ['#10b981', '#10b981', '#10b981'];
    new ApexCharts(document.querySelector('#spread-wind'), { ...windOptions, series: createPlumeSeries('Wind Speed') }).render();
    
    const rainOptions = spreadOptions('Rainfall Spread (Stacked)', 'mm', true);
    new ApexCharts(document.querySelector('#spread-rain'), { ...rainOptions, series: createStackedSeries('Rainfall') }).render();
}

function setupRegionalCharts() {
    const now = new Date();
    // SIGWX Slots
    const slots = [0, 6, 12, 18];
    const currentHour = now.getUTCHours();
    let anchorHour = Math.max(...slots.filter(h => h <= currentHour));
    if(anchorHour === -Infinity) anchorHour = 18; // fallback to previous day if needed
    const months = ['01','02','03','04','05','06','07','08','09','10','11','12'];
    
    function renderRegionalChartType(chartType) {
        let sigwxHtml = '';
        let firstUrl = '';
        
        if (chartType === "olr") {
            document.getElementById('sigwx-slots').innerHTML = "";
            document.getElementById('sigwx-img').src = "https://cews.bmkg.go.id/operational-early-warning-pdi/0_Latest/olr.cfs.all.indonesia.2_latest.png";
            return;
        }

        if (chartType.startsWith("rason_")) {
            const fn = chartType.replace("rason_", "");
            
            // Allow user to select date, defaulting to current UTC date if not set
            if (!window.rasonDate) window.rasonDate = new Date();
            
            const d = String(window.rasonDate.getUTCDate()).padStart(2,'0');
            const m = String(window.rasonDate.getUTCMonth() + 1).padStart(2,'0');
            const y = window.rasonDate.getUTCFullYear();
            const dateStr = `${d}${m}${y}`;
            const url = `https://web-aviation.bmkg.go.id/rason/${dateStr}/${fn}`;
            
            const html = `
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                    <span style="color:var(--text-secondary); font-size:0.85em;">Select Date:</span>
                    <input type="date" id="rason-date-picker" value="${y}-${m}-${d}" 
                           style="background:var(--bg-tertiary); color:var(--text-primary); border:1px solid var(--border-glass); padding:5px; border-radius:4px;">
                </div>
            `;
            
            document.getElementById('sigwx-slots').innerHTML = html;
            document.getElementById('sigwx-img').src = url;
            
            document.getElementById('rason-date-picker').addEventListener('change', (e) => {
                if (e.target.value) {
                    window.rasonDate = new Date(e.target.value + 'T00:00:00Z');
                    renderRegionalChartType(chartType);
                }
            });
            return;
        }
        
        // Parse type (e.g. mediumsigwx, highsigwx, windtemp_100)
        let basePath = chartType;
        let prefix = "sigwx_";
        if (chartType.startsWith("windtemp_")) {
            basePath = "windtemp";
            prefix = "wt" + chartType.split("_")[1] + "_";
        }
        
        for(let i=0; i<4; i++) {
            let dt = new Date(now);
            dt.setUTCHours(anchorHour - (6*i), 0, 0, 0);
            let yr = dt.getUTCFullYear();
            let mo = months[dt.getUTCMonth()];
            let da = String(dt.getUTCDate()).padStart(2, '0');
            let hr = String(dt.getUTCHours()).padStart(2, '0');
            let fn = `${prefix}${yr}${mo}${da}${hr}00.jpeg`;
            let url = `https://web-aviation.bmkg.go.id/model/${basePath}/${yr}/${mo}/${fn}`;
            if(i===0) firstUrl = url;
            sigwxHtml += `<button onclick="document.getElementById('sigwx-img').src='${url}'" style="background:var(--bg-tertiary); color:var(--text-primary); border:1px solid var(--border-glass); margin-right:8px; padding:5px 10px; border-radius:4px; cursor:pointer;">${da}/${mo} ${hr}Z</button>`;
        }
        document.getElementById('sigwx-slots').innerHTML = sigwxHtml;
        document.getElementById('sigwx-img').src = firstUrl;
    }

    const regionalSelect = document.getElementById('regional-chart-type');
    if (regionalSelect) {
        regionalSelect.addEventListener('change', (e) => {
            renderRegionalChartType(e.target.value);
        });
        renderRegionalChartType(regionalSelect.value);
    }
    
    // CB Animation
    let yesterday = new Date(now);
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    let yda = String(yesterday.getUTCDate()).padStart(2, '0');
    let ymo = months[yesterday.getUTCMonth()];
    let yyr = yesterday.getUTCFullYear();
    let cbStr = `${yda}${ymo}${yyr}`;
    document.getElementById('cb-img').src = `https://web-aviation.bmkg.go.id/prakcb/${cbStr}/CB_FORECAST_ANIMATION_${cbStr}.gif`;
}

function setupVerifyCharts(lbMetrics, ltData) {
}

// ==============================================================================
// VERIFICATION AND PERSISTENCY
// ==============================================================================
window.verifyData = null;
window.currentLookback = "Month";
window.currentVerifySub = "verify-skill";

function setupVerification(perfData) {
    if(!perfData || !perfData.metrics) return;
    window.verifyData = perfData;
    
    // Attach listeners to Lookback radios
    document.querySelectorAll('.lookback-radio input').forEach(el => {
        el.addEventListener('change', (e) => {
            window.currentLookback = e.target.value;
            renderVerification();
        });
    });
    
    // Attach listeners to Subtabs
    document.querySelectorAll('.verify-subtab').forEach(el => {
        el.addEventListener('click', (e) => {
            document.querySelectorAll('.verify-subtab').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.verify-content').forEach(c => c.style.display = 'none');
            
            e.target.classList.add('active');
            const targetId = e.target.getAttribute('data-sub');
            document.getElementById(targetId).style.display = 'block';
            window.currentVerifySub = targetId;
            renderVerification();
        });
    });
    
    // Attach listeners to param selectors
    document.getElementById('leadtime-param-select').addEventListener('change', renderVerification);
    document.getElementById('diurnal-param-select').addEventListener('change', renderVerification);
    
    renderVerification();
}

function renderVerification() {
    if(!window.verifyData) return;
    const lbMetrics = window.verifyData.metrics[window.currentLookback];
    if(!lbMetrics) return;
    
    if (window.currentVerifySub === 'verify-skill') {
        // Temperature MAE
        const tempMetrics = lbMetrics['Temperature']?.overall;
        if (tempMetrics && Object.keys(tempMetrics).length > 0) {
            const models = Object.keys(tempMetrics).filter(m => tempMetrics[m] && tempMetrics[m].MAE !== null);
            models.sort((a,b) => tempMetrics[a].MAE - tempMetrics[b].MAE);
            const options = {
                series: [{ name: 'MAE (°C)', data: models.map(m => tempMetrics[m].MAE) }],
                chart: { type: 'bar', height: 300, background: 'transparent', toolbar: { show: false } },
                plotOptions: { bar: { horizontal: true, borderRadius: 4, colors: { ranges: [{ from: 0, to: 100, color: '#ef4444' }] } } },
                dataLabels: { enabled: true, style: { colors: ['#fff'] } },
                xaxis: { categories: models, labels: { style: { colors: '#94a3b8' } } },
                yaxis: { labels: { style: { colors: '#94a3b8', fontSize: '12px', fontWeight: 600 } } },
                theme: { mode: 'dark' }
            };
            document.getElementById('verify-temp-chart').innerHTML = '';
            new ApexCharts(document.getElementById('verify-temp-chart'), options).render();
        } else {
            document.getElementById('verify-temp-chart').innerText = 'No sufficient verification data yet.';
        }
    
        // Rainfall HSS
        const rainMetrics = lbMetrics['Rainfall']?.overall;
        if (rainMetrics && Object.keys(rainMetrics).length > 0) {
            const models = Object.keys(rainMetrics).filter(m => rainMetrics[m] && rainMetrics[m].HSS !== null);
            models.sort((a,b) => rainMetrics[b].HSS - rainMetrics[a].HSS);
            const options = {
                series: [{ name: 'HSS', data: models.map(m => rainMetrics[m].HSS) }],
                chart: { type: 'bar', height: 300, background: 'transparent', toolbar: { show: false } },
                plotOptions: { bar: { horizontal: true, borderRadius: 4, colors: { ranges: [{ from: -1, to: 1, color: '#0ea5e9' }] } } },
                dataLabels: { enabled: true, style: { colors: ['#fff'] } },
                xaxis: { categories: models, labels: { style: { colors: '#94a3b8' } } },
                yaxis: { labels: { style: { colors: '#94a3b8', fontSize: '12px', fontWeight: 600 } } },
                theme: { mode: 'dark' }
            };
            document.getElementById('verify-rain-chart').innerHTML = '';
            new ApexCharts(document.getElementById('verify-rain-chart'), options).render();
        } else {
            document.getElementById('verify-rain-chart').innerText = 'No sufficient verification data yet.';
        }
        
        // Dewpoint MAE
        const dewMetrics = lbMetrics['Dewpoint']?.overall;
        if (dewMetrics && Object.keys(dewMetrics).length > 0) {
            const models = Object.keys(dewMetrics).filter(m => dewMetrics[m] && dewMetrics[m].MAE !== null);
            models.sort((a,b) => dewMetrics[a].MAE - dewMetrics[b].MAE);
            const options = {
                series: [{ name: 'MAE (°C)', data: models.map(m => dewMetrics[m].MAE) }],
                chart: { type: 'bar', height: 300, background: 'transparent', toolbar: { show: false } },
                plotOptions: { bar: { horizontal: true, borderRadius: 4, colors: { ranges: [{ from: 0, to: 100, color: '#3b82f6' }] } } },
                dataLabels: { enabled: true, style: { colors: ['#fff'] } },
                xaxis: { categories: models, labels: { style: { colors: '#94a3b8' } } },
                yaxis: { labels: { style: { colors: '#94a3b8', fontSize: '12px', fontWeight: 600 } } },
                theme: { mode: 'dark' }
            };
            document.getElementById('verify-dew-chart').innerHTML = '';
            new ApexCharts(document.getElementById('verify-dew-chart'), options).render();
        } else {
            document.getElementById('verify-dew-chart').innerText = 'No sufficient verification data yet.';
        }
        
        // Pressure MAE
        const presMetrics = lbMetrics['Pressure']?.overall;
        if (presMetrics && Object.keys(presMetrics).length > 0) {
            const models = Object.keys(presMetrics).filter(m => presMetrics[m] && presMetrics[m].MAE !== null);
            models.sort((a,b) => presMetrics[a].MAE - presMetrics[b].MAE);
            const options = {
                series: [{ name: 'MAE (hPa)', data: models.map(m => presMetrics[m].MAE) }],
                chart: { type: 'bar', height: 300, background: 'transparent', toolbar: { show: false } },
                plotOptions: { bar: { horizontal: true, borderRadius: 4, colors: { ranges: [{ from: 0, to: 100, color: '#a855f7' }] } } },
                dataLabels: { enabled: true, style: { colors: ['#fff'] } },
                xaxis: { categories: models, labels: { style: { colors: '#94a3b8' } } },
                yaxis: { labels: { style: { colors: '#94a3b8', fontSize: '12px', fontWeight: 600 } } },
                theme: { mode: 'dark' }
            };
            document.getElementById('verify-pressure-chart').innerHTML = '';
            new ApexCharts(document.getElementById('verify-pressure-chart'), options).render();
        } else {
            document.getElementById('verify-pressure-chart').innerText = 'No sufficient verification data yet.';
        }
        
        // Wind Speed MAE
        const windMetrics = lbMetrics['Wind Speed']?.overall;
        if (windMetrics && Object.keys(windMetrics).length > 0) {
            const models = Object.keys(windMetrics).filter(m => windMetrics[m] && windMetrics[m].MAE !== null);
            models.sort((a,b) => windMetrics[a].MAE - windMetrics[b].MAE);
            const options = {
                series: [{ name: 'MAE (kt)', data: models.map(m => windMetrics[m].MAE) }],
                chart: { type: 'bar', height: 300, background: 'transparent', toolbar: { show: false } },
                plotOptions: { bar: { horizontal: true, borderRadius: 4, colors: { ranges: [{ from: 0, to: 100, color: '#10b981' }] } } },
                dataLabels: { enabled: true, style: { colors: ['#fff'] } },
                xaxis: { categories: models, labels: { style: { colors: '#94a3b8' } } },
                yaxis: { labels: { style: { colors: '#94a3b8', fontSize: '12px', fontWeight: 600 } } },
                theme: { mode: 'dark' }
            };
            document.getElementById('verify-wind-chart').innerHTML = '';
            new ApexCharts(document.getElementById('verify-wind-chart'), options).render();
        } else {
            document.getElementById('verify-wind-chart').innerText = 'No sufficient verification data yet.';
        }

    }
    else if (window.currentVerifySub === 'verify-leadtime') {
        const param = document.getElementById('leadtime-param-select').value;
        const ltData = lbMetrics[param]?.lead_time;
        if (ltData && Object.keys(ltData).length > 0) {
            const days = ["Day 1", "Day 2", "Day 3", "Day 4+"];
            const models = Object.keys(lbMetrics[param].overall || {}).filter(m => m !== 'Multi-Model');
            
            const series = models.map(m => {
                return {
                    name: m,
                    data: days.map(d => (ltData[d] && ltData[d][m]) ? ltData[d][m].RMSE : null)
                }
            });
            
            const options = {
                series: series,
                chart: { type: 'line', height: 350, background: 'transparent', toolbar: { show: false } },
                stroke: { width: 3, curve: 'smooth' },
                xaxis: { categories: days, labels: { style: { colors: '#94a3b8' } } },
                yaxis: { title: { text: 'RMSE' }, labels: { style: { colors: '#94a3b8' } } },
                theme: { mode: 'dark' },
                legend: { position: 'top' }
            };
            document.getElementById('verify-leadtime-chart').innerHTML = '';
            new ApexCharts(document.getElementById('verify-leadtime-chart'), options).render();
        } else {
            document.getElementById('verify-leadtime-chart').innerText = 'Insufficient data for Lead-Time Analysis.';
        }
    }
    else if (window.currentVerifySub === 'verify-diurnal') {
        const param = document.getElementById('diurnal-param-select').value;
        const dData = lbMetrics[param]?.diurnal_bias;
        if (dData && Object.keys(dData).length > 0) {
            const hours = Array.from({length: 24}, (_, i) => i.toString());
            const models = Object.keys(lbMetrics[param].overall || {}).filter(m => m !== 'Multi-Model');
            
            const series = models.map(m => {
                return {
                    name: m,
                    data: hours.map(h => (dData[m] && dData[m][h] !== undefined) ? dData[m][h] : null)
                }
            });
            
            const options = {
                series: series,
                chart: { type: 'line', height: 350, background: 'transparent', toolbar: { show: false } },
                stroke: { width: 2, curve: 'smooth' },
                xaxis: { categories: hours.map(h => h.padStart(2, '0') + ':00'), labels: { style: { colors: '#94a3b8' } } },
                yaxis: { title: { text: 'Bias' }, labels: { style: { colors: '#94a3b8' } } },
                theme: { mode: 'dark' },
                legend: { position: 'top' },
                annotations: {
                    yaxis: [{ y: 0, borderColor: '#ef4444', label: { text: 'Zero Bias', style: { color: '#fff', background: '#ef4444' } } }]
                }
            };
            document.getElementById('verify-diurnal-chart').innerHTML = '';
            new ApexCharts(document.getElementById('verify-diurnal-chart'), options).render();
        } else {
            document.getElementById('verify-diurnal-chart').innerText = 'Insufficient data for Diurnal Analysis.';
        }
    }
    else if (window.currentVerifySub === 'verify-significancy') {
        const sigData = lbMetrics['Rainfall']?.significance;
        if (sigData && sigData.fused) {
            const html = `
                <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); padding: 15px; border-radius: 8px;">
                    <h4 style="color: var(--green); margin-bottom: 10px;">Test Results</h4>
                    <p><strong>Diebold-Mariano Stat (DM):</strong> ${Number(sigData.fused.dm_stat).toFixed(3)}</p>
                    <p><strong>P-Value:</strong> ${Number(sigData.fused.p_value).toFixed(4)}</p>
                    <p><strong>Newey-West Bandwidth:</strong> ${sigData.fused.nw_bandwidth}</p>
                    <p style="margin-top: 10px; font-weight: bold; color: ${sigData.fused.p_value < 0.05 ? 'var(--green)' : 'var(--amber)'};">
                        ${sigData.fused.p_value < 0.05 ? '[OK] Weighted ensemble significantly better than equal weighting (p < 0.05)' : '[WARN] Improvement is not statistically significant'}
                    </p>
                </div>
            `;
            document.getElementById('verify-significancy-content').innerHTML = html;
        } else {
            document.getElementById('verify-significancy-content').innerText = 'No significancy test data available.';
        }
    }
}

function setupPersistency(persData) {
    if(!persData || persData.length === 0) {
        document.getElementById('persistency-temp-chart').innerText = 'No historical observation data found.';
        document.getElementById('persistency-rain-chart').innerText = 'No historical observation data found.';
        return;
    }
    
    const times = persData.map(d => new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime());
    const temp = persData.map(d => d.temperature);
    const dew = persData.map(d => d.dewpoint);
    const rain = persData.map(d => d.rain_1h);
    const wind = persData.map(d => d.wind_speed);

    // Temp/Dew Chart
    const tempOptions = {
        series: [
            { name: 'Temperature (°C)', data: temp },
            { name: 'Dewpoint (°C)', data: dew }
        ],
        chart: { type: 'area', height: 300, background: 'transparent', toolbar: { show: false } },
        stroke: { width: 2, curve: 'smooth' },
        fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.7, opacityTo: 0.1 } },
        colors: ['#ef4444', '#3b82f6'],
        dataLabels: { enabled: false },
        xaxis: { type: 'datetime', categories: times, labels: { style: { colors: '#94a3b8' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        theme: { mode: 'dark' }
    };
    document.getElementById('persistency-temp-chart').innerHTML = '';
    new ApexCharts(document.getElementById('persistency-temp-chart'), tempOptions).render();

    // Rain/Wind Chart
    const rainOptions = {
        series: [
            { name: 'Rainfall (mm)', type: 'bar', data: rain },
            { name: 'Wind Speed (kt)', type: 'line', data: wind }
        ],
        chart: { height: 300, background: 'transparent', toolbar: { show: false } },
        colors: ['#0ea5e9', '#10b981'],
        dataLabels: { enabled: false },
        stroke: { curve: 'smooth', width: [0, 2] },
        xaxis: { type: 'datetime', categories: times, labels: { style: { colors: '#94a3b8' } } },
        yaxis: [
            { title: { text: 'Rain (mm)' }, labels: { style: { colors: '#0ea5e9' } } },
            { opposite: true, title: { text: 'Wind (kt)' }, labels: { style: { colors: '#10b981' } } }
        ],
        theme: { mode: 'dark' }
    };
    document.getElementById('persistency-rain-chart').innerHTML = '';
    new ApexCharts(document.getElementById('persistency-rain-chart'), rainOptions).render();
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
            const pres = modelsData['Pressure']?.[modelName]?.[dt];
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
                    <td style="color: #a855f7">${pres !== undefined ? Number(pres).toFixed(1) : '-'}</td>
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

    // AWOS Upload Logic (GitHub API Direct - INSECURE)
    const patInput = document.getElementById('github-pat-input');
    const awosInput = document.getElementById('awos-file-input');
    const uploadBtn = document.getElementById('upload-awos-btn');
    const statusDiv = document.getElementById('upload-status');

    if (patInput) {
        patInput.value = localStorage.getItem('gh_wawp_pat') || '';
        patInput.addEventListener('input', (e) => {
            localStorage.setItem('gh_wawp_pat', e.target.value);
        });
    }

    if (uploadBtn && patInput && awosInput) {
        uploadBtn.addEventListener('click', async () => {
            const token = patInput.value.trim();
            if (!token) {
                showStatus('Error: GitHub Token is required.', 'var(--amber)');
                return;
            }
            if (!awosInput.files || awosInput.files.length === 0) {
                showStatus('Error: Please select a .dat file.', 'var(--amber)');
                return;
            }
            
            const file = awosInput.files[0];
            uploadBtn.innerText = 'Uploading...';
            uploadBtn.disabled = true;
            showStatus('Reading file...', 'var(--text-secondary)');
            
            try {
                // Read as base64
                const reader = new FileReader();
                reader.readAsDataURL(file);
                reader.onload = async () => {
                    const base64Content = reader.result.split(',')[1];
                    
                    // GitHub API push
                    const repoOwner = "anzthebest0-cpu";
                    const repoName = "wawp-forecast-engine";
                    const path = "data/raw_obs/latest.dat";
                    const apiUrl = `https://api.github.com/repos/${repoOwner}/${repoName}/contents/${path}`;
                    
                    // Get current file SHA if exists to overwrite
                    let sha = null;
                    try {
                        const getRes = await fetch(apiUrl, {
                            headers: { 'Authorization': `Bearer ${token}` }
                        });
                        if (getRes.ok) {
                            const getJson = await getRes.json();
                            sha = getJson.sha;
                        }
                    } catch(e) {}
                    
                    const payload = {
                        message: `Upload manual AWOS observation: ${file.name}`,
                        content: base64Content,
                        branch: "main"
                    };
                    if (sha) payload.sha = sha;
                    
                    const putRes = await fetch(apiUrl, {
                        method: 'PUT',
                        headers: {
                            'Authorization': `Bearer ${token}`,
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(payload)
                    });
                    
                    if (putRes.ok) {
                        showStatus('Upload Successful! GitHub Actions triggered.', 'var(--green)');
                        setTimeout(() => {
                            uploadBtn.innerText = 'Upload to GitHub & Trigger Verification';
                            uploadBtn.disabled = false;
                            awosInput.value = '';
                        }, 3000);
                    } else {
                        const errText = await putRes.text();
                        showStatus(`Upload Failed: ${putRes.status}`, 'var(--crimson)');
                        console.error(errText);
                        uploadBtn.innerText = 'Upload to GitHub & Trigger Verification';
                        uploadBtn.disabled = false;
                    }
                };
            } catch(e) {
                showStatus(`Error: ${e.message}`, 'var(--crimson)');
                uploadBtn.innerText = 'Upload to GitHub & Trigger Verification';
                uploadBtn.disabled = false;
            }
        });
    }
    
    function showStatus(msg, color) {
        if (!statusDiv) return;
        statusDiv.style.display = 'block';
        statusDiv.innerText = msg;
        statusDiv.style.color = color;
        statusDiv.style.border = `1px solid ${color}`;
        statusDiv.style.background = 'rgba(255, 255, 255, 0.05)';
    }
});
