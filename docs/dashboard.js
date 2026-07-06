const SPREAD_MAX_HOURS = 96;
let pendingSpreadModelsData = null;
let pendingSpreadTimeLabels = [];
let spreadChartsRendered = false;

function updatePullStatus(timestamp) {
    const statusEl = document.getElementById('pull-status');
    if (!statusEl) return;

    if (!timestamp || timestamp === "Unknown") {
        statusEl.innerText = "No pull data";
        statusEl.className = "pull-status stale";
        return;
    }

    const normalized = timestamp.endsWith('Z')
        ? timestamp
        : timestamp.replace(' ', 'T') + 'Z';
    const pulledAt = new Date(normalized);
    if (Number.isNaN(pulledAt.getTime())) {
        statusEl.innerText = "Pull time unknown";
        statusEl.className = "pull-status stale";
        return;
    }

    const ageHours = Math.max(0, (Date.now() - pulledAt.getTime()) / 36e5);
    if (ageHours <= 3) {
        statusEl.innerText = "Fresh";
        statusEl.className = "pull-status fresh";
    } else {
        statusEl.innerText = `Stale archive ${ageHours.toFixed(1)}h`;
        statusEl.className = "pull-status stale";
    }
}

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
            fetch('data/persistency.json' + cb).then(r => r.json()),
            fetch('data/diurnal_climatology.json' + cb).then(r => r.json()),
            fetch('data/system_workflow.json' + cb).then(r => r.json())
        ]);
        
        const intelData = results[0].status === 'fulfilled' ? results[0].value : {};
        let currentIssuance = "2300";
        let intel = intelData[currentIssuance] || intelData || {};
        const weights = results[1].status === 'fulfilled' ? results[1].value : {weights: {}};
        const perf = results[2].status === 'fulfilled' ? results[2].value : null;
        const guidanceJson = results[3].status === 'fulfilled' ? results[3].value : {data: []};
        const data = guidanceJson.data || [];
        const generatedAt = guidanceJson.metadata
            ? (guidanceJson.metadata.latest_data_pull_utc || guidanceJson.metadata.generated_at || guidanceJson.metadata.latest_model_run_init_utc)
            : (intel.valid_start || "Unknown");

        // Filter data to only show current and future hours
        const now = new Date();
        const localNow = new Date(now.getTime() + (8 * 60 * 60 * 1000));
        const witaHourStr = localNow.toISOString().replace('T', ' ').substring(0, 13) + ':00:00';
        
        const futureData = data.filter(d => d.Datetime >= witaHourStr);
        const renderData = futureData.length > 0 ? futureData : data;

        const clim_data = results[4].status === 'fulfilled' ? results[4].value : null;
        const modelsData = results[5].status === 'fulfilled' ? results[5].value : null;
        const diurnalData = results[8].status === 'fulfilled' ? results[8].value : null;
        const workflowData = results[9].status === 'fulfilled' ? results[9].value : null;
        
        const healthData = results[6].status === 'fulfilled' ? results[6].value : null;
        if (healthData) {
            const fcElem = document.getElementById('db-forecast-count');
            const histFcElem = document.getElementById('db-historical-forecast-count');
            const obsElem = document.getElementById('db-obs-count');
            const obs1mElem = document.getElementById('db-obs-1min-count');
            const qmRuntimeElem = document.getElementById('db-runtime-qm-count');
            const sizeElem = document.getElementById('db-size');
            const syncElem = document.getElementById('db-last-sync');
            
            if (fcElem) fcElem.innerText = Number(healthData.current_forecast_records ?? healthData.forecast_records ?? 0).toLocaleString();
            if (histFcElem) histFcElem.innerText = Number(healthData.historical_forecast_records ?? healthData.openmeteo_records ?? 0).toLocaleString();
            if (obsElem) obsElem.innerText = Number(healthData.observation_records || 0).toLocaleString();
            if (obs1mElem) obs1mElem.innerText = Number(healthData.observation_1min_records || 0).toLocaleString();
            if (qmRuntimeElem) qmRuntimeElem.innerText = Number(healthData.runtime_qm_cdfs_enabled ?? healthData.qm_cdfs_enabled ?? 0).toLocaleString();
            if (sizeElem) sizeElem.innerText = healthData.size_mb + ' MB';
            if (syncElem) syncElem.innerText = (healthData.latest_data_pull_utc || healthData.last_sync_utc || healthData.latest_model_run_init_utc) + ' UTC';
            setupHealthFreshness(healthData.model_freshness || []);
            setupQMProvenance(healthData.qm_provenance || null);
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
        
        // Thunderstorm proxy risk display. TAF weather groups are generated separately in the backend.
        const tsProbVal = document.getElementById('ts-prob-val');
        const tsProbTime = document.getElementById('ts-prob-time');
        if (tsProbVal && tsProbTime && renderData && renderData.length > 0) {
            let maxProb = 0;
            let peakTime = "";
            renderData.forEach(d => {
                let prob = d['Thunderstorm Risk'] ?? d['Precip Probability'] ?? d['Prob Precip 1.0mm'] ?? 0;
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
        updatePullStatus(generatedAt);
        
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
        const weightGroups = Object.values(weights.weights || {});
        const allWeightsEqual = weightGroups.length > 0 && weightGroups.every(group => {
            const vals = Object.values(group || {}).map(Number).filter(v => !Number.isNaN(v));
            return vals.length > 0 && Math.max(...vals) - Math.min(...vals) < 0.0001;
        });
        if (allWeightsEqual) {
            wHtml += `
                <div class="weight-status-card">
                    Dynamic weights are using equal fallback because verified forecast-observation skill pairs are not available in this export.
                </div>
            `;
        }
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
                <td>${Number(d['Precip Probability'] || d['Prob Precip 1.0mm'] || 0) > 0 ? Number(d['Precip Probability'] || d['Prob Precip 1.0mm']).toFixed(1) + '%' : '-'}</td>
                <td>${d.Condition || '-'}</td>
            `;
            tbody.appendChild(tr);
        });
        
        // 5.5 Individual Models
        if (modelsData && renderData) {
            setupIndividualModels(modelsData, renderData.map(d=>d.Datetime));
            pendingSpreadModelsData = modelsData;
            pendingSpreadTimeLabels = renderData.map(d=>d.Datetime).slice(0, SPREAD_MAX_HOURS);
            spreadChartsRendered = false;
            if (document.getElementById('tab-spread')?.classList.contains('active')) {
                setupSpreadCharts(pendingSpreadModelsData, pendingSpreadTimeLabels);
            }
        }
        
        // 6. Regional & Climatology
        try {
            setupRegionalCharts();
        } catch (sectionError) {
            console.error("Regional charts failed", sectionError);
        }
        try {
            if (diurnalData) setupDiurnalClimatology(diurnalData);
        } catch (sectionError) {
            console.error("Diurnal climatology failed", sectionError);
            const el = document.getElementById('climatology-text');
            if (el) el.innerText = 'Climatology data is temporarily unavailable.';
        }
        try {
            if (workflowData && (!healthData || !(healthData.model_freshness || []).length)) {
                setupHealthFreshness(workflowData.model_freshness || []);
            }
        } catch (sectionError) {
            console.error("System workflow fallback failed", sectionError);
        }

        // 7. Verification & Persistency
        const persData = results[7].status === 'fulfilled' ? results[7].value : null;
        try {
            if (perf) setupVerification(perf);
        } catch (sectionError) {
            console.error("Live verification failed", sectionError);
        }
        try {
            if (persData) setupPersistency(persData);
        } catch (sectionError) {
            console.error("Persistency failed", sectionError);
        }
        
    } catch (e) {
        console.error(e);
        document.getElementById('update-time').innerText = "Load failed: " + (e.message || e);
        updatePullStatus("Unknown");
        const tafDisplay = document.getElementById('taf-text-display');
        if (tafDisplay) tafDisplay.innerText = e.stack || e;
    }
}

function switchTab(tabId) {
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-links li').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tabId).classList.add('active');
    document.querySelector(`.nav-links li[data-tab="${tabId}"]`).classList.add('active');

    if (tabId === 'tab-spread' && pendingSpreadModelsData && !spreadChartsRendered) {
        setupSpreadCharts(pendingSpreadModelsData, pendingSpreadTimeLabels);
    }
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
    spreadChartsRendered = true;
    const spreadTimeLabels = (timeLabels || []).slice(0, SPREAD_MAX_HOURS);
    ['spread-temp', 'spread-t-td', 'spread-wind', 'spread-rain'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    });

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
        
        for (const t of spreadTimeLabels) {
            const ts = new Date(t.replace(' ', 'T') + 'Z').getTime();
            const vals = [];
            for (const [model, modelVals] of Object.entries(modelsData[param] || {})) {
                if (modelVals[t] !== undefined && modelVals[t] !== null) {
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

    const createBarSeries = (param) => {
        const series = [];
        for (const [model, modelVals] of Object.entries(modelsData[param] || {})) {
            const dataPts = [];
            for (const t of spreadTimeLabels) {
                const ts = new Date(t.replace(' ', 'T') + 'Z').getTime();
                if (modelVals[t] !== undefined && modelVals[t] !== null) {
                    dataPts.push([ts, modelVals[t]]);
                }
            }
            series.push({ name: model, data: dataPts, type: 'bar' });
        }
        return series;
    };

    const spreadOptions = (title, yAxisLabel, isBar=false) => ({
        ...TITAN_BASE,
        chart: { ...TITAN_BASE.chart, type: isBar ? 'bar' : 'area', height: 280, stacked: isBar, animations: { enabled: false } },
        title: { text: title, style: { fontSize: '13px', fontWeight: 'bold', color: 'var(--text-primary)' } },
        stroke: { curve: 'smooth', width: isBar ? 0 : [0, 0, 2] },
        dataLabels: { enabled: false },
        tooltip: { enabled: false },
        markers: { size: 0 },
        xaxis: { type: 'datetime', labels: { style: { colors: 'var(--text-secondary)' } } },
        yaxis: { title: { text: yAxisLabel }, labels: { style: { colors: 'var(--text-secondary)' }, formatter: (val) => val.toFixed(1) } },
        legend: { position: 'top' },
        fill: isBar ? { opacity: 0.85 } : { opacity: [0.15, 0.4, 1] }
    });

    const tempOptions = spreadOptions('Temperature Plume (°C)', '°C');
    tempOptions.colors = ['#ef4444', '#ef4444', '#ef4444'];
    new ApexCharts(document.querySelector('#spread-temp'), { ...tempOptions, series: createPlumeSeries('Temperature') }).render();
    
    // T - Td Spread (Fog Risk) Plume
    const createSpreadTTdPlume = () => {
        const rangeMaxMin = [];
        const rangeIQR = [];
        const medLine = [];
        for (const t of spreadTimeLabels) {
            const ts = new Date(t.replace(' ', 'T') + 'Z').getTime();
            const vals = [];
            for (const [model, modelVals] of Object.entries(modelsData['Temperature'] || {})) {
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
    
    const rainOptions = spreadOptions('Rainfall Spread (Bar)', 'mm', true);
    new ApexCharts(document.querySelector('#spread-rain'), { ...rainOptions, series: createBarSeries('Rainfall') }).render();
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
            const models = Object.keys(lbMetrics[param].overall || {});
            
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
            const models = Object.keys(lbMetrics[param].overall || {});
            
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

function setupDiurnalClimatology(clim) {
    if(!clim || !clim.metadata) return;

    const fmt = (v, d=1) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '-' : Number(v).toFixed(d);
    const roundValue = (v, d=1) => (v === null || v === undefined || Number.isNaN(Number(v))) ? null : Number(Number(v).toFixed(d));
    const roundArray = (arr, d=1) => (arr || []).map(v => roundValue(v, d));
    const peakHour = (arr) => {
        if (!arr || !arr.length) return null;
        let bestIdx = -1;
        let bestVal = -Infinity;
        arr.forEach((v, idx) => {
            const n = Number(v);
            if (!Number.isNaN(n) && n > bestVal) {
                bestVal = n;
                bestIdx = idx;
            }
        });
        return bestIdx < 0 ? null : bestIdx;
    };
    const meanValid = (arr) => {
        const vals = (arr || []).map(Number).filter(v => !Number.isNaN(v));
        if (!vals.length) return null;
        return vals.reduce((a, b) => a + b, 0) / vals.length;
    };
    const hours = Array.from({length: 24}, (_, i) => `${String(i).padStart(2, '0')}:00`);
    const tempStats = clim.climatology?.temperature?.stats || {};
    const dewStats = clim.climatology?.dewpoint?.stats || {};
    const humStats = clim.climatology?.humidity?.stats || {};
    const windStats = clim.climatology?.wind_speed?.stats || {};
    const tempMean = roundArray(Array.from({length: 24}, (_, h) => tempStats[String(h)]?.mean ?? null), 1);
    const dewMean = roundArray(Array.from({length: 24}, (_, h) => dewStats[String(h)]?.mean ?? null), 1);
    const humMean = roundArray(Array.from({length: 24}, (_, h) => humStats[String(h)]?.mean ?? null), 1);
    const windMean = roundArray(Array.from({length: 24}, (_, h) => windStats[String(h)]?.mean ?? null), 1);
    const rainFreq = roundArray(clim.rain_diurnal_cycle?.frequency_pct || [], 1);
    const rainIntensity = roundArray(clim.rain_diurnal_cycle?.intensity_mmh || [], 2);
    const gustFreq = roundArray(clim.gust_diurnal_cycle?.frequency_pct || [], 1);
    const gustIntensity = roundArray(clim.gust_diurnal_cycle?.intensity_kt || [], 1);
    const fogScore = roundArray((clim.fog_low_cloud_proxy?.hourly || []).map(x => x.mean_score), 2);
    const fogFreq = roundArray((clim.fog_low_cloud_proxy?.hourly || []).map(x => x.high_risk_frequency_pct), 1);

    const totalEl = document.getElementById('clim-total-obs');
    if (totalEl) totalEl.innerText = Number(clim.metadata.total_observations || 0).toLocaleString();
    const periodEl = document.getElementById('clim-period');
    if (periodEl) {
        const p = clim.metadata.data_period || {};
        periodEl.innerText = `${(p.start || '').substring(0,10)} to ${(p.end || '').substring(0,10)}`;
    }
    const convEl = document.getElementById('clim-convective-window');
    if (convEl) convEl.innerText = clim.operational_briefing?.convective_window_label || '--';
    const seaEl = document.getElementById('clim-seabreeze');
    if (seaEl) seaEl.innerText = clim.sea_breeze_regime?.confidence || '--';

    const tempMin = Math.min(...tempMean.filter(v => v !== null));
    const tempMax = Math.max(...tempMean.filter(v => v !== null));
    const rainPeak = peakHour(rainFreq);
    const gustPeak = peakHour(gustFreq);
    const fogPeak = peakHour(fogScore);
    const extremes = clim.extreme_event_summary || {};
    const pct = extremes.percentiles || {};
    const events = extremes.events || {};
    const setText = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.innerText = text;
    };
    setText('clim-temp-range', Number.isFinite(tempMin) && Number.isFinite(tempMax) ? `${fmt(tempMin)}-${fmt(tempMax)} C` : '--');
    setText('clim-wet-freq', `${fmt(meanValid(rainFreq))}%`);
    setText('clim-peak-rain', rainPeak === null ? '--' : `${String(rainPeak).padStart(2, '0')} WITA`);
    setText('clim-mean-wind', `${fmt(meanValid(windMean))} kt`);
    setText('clim-peak-gust', gustPeak === null ? '--' : `${String(gustPeak).padStart(2, '0')} WITA`);
    setText('clim-fog-peak', fogPeak === null ? '--' : `${String(fogPeak).padStart(2, '0')} WITA`);
    setText('clim-p95-temp', `${fmt(pct.temperature_c?.p95)} C`);
    setText('clim-p95-gust', `${fmt(pct.wind_gust_kt?.p95)} kt`);
    setText('clim-p95-rain', `${fmt(pct.rain_wet_hour_mm?.p95, 2)} mm`);
    setText('clim-heavy-rain', `${fmt(events.rain_ge_10mm_pct, 2)}%`);
    setText('clim-gust-event', `${fmt(events.gust_event_pct, 1)}%`);
    setText('clim-dominant-wind', extremes.dominant_wind?.sector
        ? `${extremes.dominant_wind.sector} ${fmt(extremes.dominant_wind.frequency_pct, 1)}%`
        : '--');

    const briefing = clim.operational_briefing?.bullets || [];
    document.getElementById('climatology-text').innerHTML = briefing.length
        ? `<ul class="climatology-briefing-list">${briefing.map(b => `<li>${b}</li>`).join('')}</ul>`
        : 'No climatology briefing is available.';

    const baseAxis = {
        chart: { background: 'transparent', toolbar: { show: false }, animations: { enabled: false } },
        xaxis: { categories: hours, labels: { style: { colors: '#94a3b8' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        legend: { position: 'top', labels: { colors: '#94a3b8' } },
        theme: { mode: 'dark' },
        dataLabels: { enabled: false },
        grid: { borderColor: 'rgba(148,163,184,0.15)' }
    };

    const renderChart = (selector, options) => {
        const el = document.querySelector(selector);
        if (!el) return;
        el.innerHTML = '';
        new ApexCharts(el, options).render();
    };

    renderChart('#clim-temp-rh-chart', {
        ...baseAxis,
        series: [
            { name: 'Temp C', data: tempMean },
            { name: 'Dewpoint C', data: dewMean },
            { name: 'RH %', data: humMean }
        ],
        chart: { ...baseAxis.chart, type: 'line', height: 320 },
        stroke: { width: 3, curve: 'smooth' },
        colors: ['#ef4444', '#3b82f6', '#a855f7']
    });

    renderChart('#clim-rain-chart', {
        ...baseAxis,
        series: [
            { name: 'Rain Frequency %', type: 'column', data: rainFreq },
            { name: 'Wet-Hour Intensity mm', type: 'line', data: rainIntensity }
        ],
        chart: { ...baseAxis.chart, height: 320 },
        stroke: { width: [0, 3], curve: 'smooth' },
        colors: ['#0ea5e9', '#f59e0b']
    });

    renderChart('#clim-wind-chart', {
        ...baseAxis,
        series: [
            { name: 'Wind Speed kt', type: 'line', data: windMean },
            { name: 'Gust Event %', type: 'column', data: gustFreq },
            { name: 'Mean Event Gust kt', type: 'line', data: gustIntensity }
        ],
        chart: { ...baseAxis.chart, height: 320 },
        stroke: { width: [3, 0, 3], curve: 'smooth' },
        colors: ['#10b981', '#f59e0b', '#ef4444']
    });

    renderChart('#clim-fog-chart', {
        ...baseAxis,
        series: [
            { name: 'Proxy Score', type: 'line', data: fogScore },
            { name: 'High-Risk Frequency %', type: 'column', data: fogFreq }
        ],
        chart: { ...baseAxis.chart, height: 320 },
        stroke: { width: [3, 0], curve: 'smooth' },
        colors: ['#a855f7', '#64748b']
    });

    if(clim.wind_rose?.sectors) {
        const roseAll = clim.wind_rose.all || [];
        const roseFreq = roseAll.length
            ? roseAll.map(d => Number(fmt(d.freq_pct, 2)))
            : (clim.wind_rose.wet || []).map(d => Number(fmt(d.freq_pct, 2)));
        const roseColors = [
            '#38bdf8', '#22d3ee', '#2dd4bf', '#34d399',
            '#a3e635', '#facc15', '#fb923c', '#f97316',
            '#ef4444', '#f43f5e', '#e879f9', '#c084fc',
            '#818cf8', '#60a5fa', '#0ea5e9', '#06b6d4'
        ];
        renderChart('#chart-wind-rose', {
            series: roseFreq,
            chart: { type: 'polarArea', height: 390, background: 'transparent', toolbar: { show: false } },
            labels: clim.wind_rose.sectors,
            stroke: { colors: ['rgba(15,23,42,0.75)'], width: 1 },
            fill: { opacity: 0.86 },
            colors: roseColors,
            theme: { mode: 'dark' },
            legend: { position: 'right', labels: { colors: '#94a3b8' } },
            yaxis: { labels: { formatter: value => `${Number(value).toFixed(1)}%` } },
            plotOptions: {
                polarArea: {
                    rings: { strokeWidth: 1, strokeColor: 'rgba(148,163,184,0.22)' },
                    spokes: { strokeWidth: 1, connectorColors: 'rgba(148,163,184,0.22)' }
                }
            },
            tooltip: {
                y: {
                    formatter: (value, opts) => {
                        const detail = roseAll[opts.seriesIndex] || {};
                        return `${Number(value).toFixed(2)}% | mean ${fmt(detail.mean_speed_kt)} kt | gust ${fmt(detail.gust_event_pct)}%`;
                    }
                }
            }
        });
    }

    const rainMatrix = clim.monthly_hourly_matrices?.rain_1h?.matrix;
    if (rainMatrix) {
        const monthLabels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const series = rainMatrix.map((row, idx) => ({
            name: monthLabels[idx],
            data: row.map((v, h) => ({ x: `${String(h).padStart(2,'0')}`, y: v === null ? 0 : Number(Number(v).toFixed(2)) }))
        }));
        renderChart('#clim-rain-heatmap', {
            chart: { type: 'heatmap', height: 360, background: 'transparent', toolbar: { show: false } },
            series,
            dataLabels: { enabled: false },
            plotOptions: {
                heatmap: {
                    shadeIntensity: 0.75,
                    radius: 2,
                    enableShades: false,
                    colorScale: {
                        ranges: [
                            { from: 0, to: 0.05, color: '#111827', name: 'Dry' },
                            { from: 0.06, to: 0.5, color: '#0ea5e9', name: 'Light' },
                            { from: 0.51, to: 1.5, color: '#22c55e', name: 'Wet' },
                            { from: 1.51, to: 3, color: '#facc15', name: 'Mod' },
                            { from: 3.01, to: 7, color: '#f97316', name: 'Heavy' },
                            { from: 7.01, to: 999, color: '#ef4444', name: 'Extreme' }
                        ]
                    }
                }
            },
            xaxis: { labels: { style: { colors: '#94a3b8' } } },
            yaxis: { labels: { style: { colors: '#94a3b8' } } },
            theme: { mode: 'dark' }
        });
    }
}

function setupHealthFreshness(freshnessRows) {
    const fmtInt = v => Number(v || 0).toLocaleString();
    const fmtHours = v => (v === null || v === undefined || Number.isNaN(Number(v))) ? '-' : `${Number(v).toFixed(Number(v) % 1 === 0 ? 0 : 1)} h`;
    const parseUtc = value => {
        if (!value) return null;
        const dt = new Date(String(value).endsWith('Z') ? value : String(value).replace(' ', 'T') + 'Z');
        return Number.isNaN(dt.getTime()) ? null : dt;
    };
    const liveAgeHours = row => {
        const scraped = parseUtc(row.latest_scraped_at);
        if (!scraped) return row.age_hours === null ? null : Number(row.age_hours);
        return Math.max(0, (Date.now() - scraped.getTime()) / 36e5);
    };
    const tbody = document.querySelector('#health-freshness-table tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    (freshnessRows || []).forEach(row => {
        const age = liveAgeHours(row);
        const cadence = Number(row.provider_update_frequency_hours || 6);
        const status = age === null || Number.isNaN(age)
            ? "unknown"
            : (age <= cadence * 1.25 ? "fresh" : (age <= cadence * 2.0 ? "aging" : "stale"));
        const color = status === 'fresh' ? 'var(--green)' : (status === 'aging' ? 'var(--amber)' : 'var(--crimson)');
        const quality = row.quality_status || 'unknown';
        const qualityColor = quality === 'ok' ? 'var(--green)' : (quality === 'unknown' ? 'var(--text-secondary)' : 'var(--amber)');
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${row.model}</td>
            <td>${row.latest_run_init_utc || '-'}</td>
            <td>${row.latest_scraped_at || '-'}</td>
            <td>${age === null || Number.isNaN(age) ? '-' : Number(age).toFixed(1) + ' h'}</td>
            <td>${fmtHours(row.detected_interval_hours ?? row.expected_output_interval_hours)}</td>
            <td>${fmtHours(row.provider_update_frequency_hours)}</td>
            <td>${fmtInt(row.row_count)}</td>
            <td>${row.min_lead_hours ?? '-'}-${row.max_lead_hours ?? '-'} h</td>
            <td title="${row.quality_notes || row.hourly_output_note || ''}" style="color:${qualityColor}; font-weight:700;">${quality}</td>
            <td style="color:${color}; font-weight:700;">${status}</td>
        `;
        tbody.appendChild(tr);
    });
}

function setupQMProvenance(prov) {
    const pct = prov?.percent_by_layer || {};
    const artifact = prov?.artifact_status || {};
    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.innerText = value;
    };
    const fmtPct = v => `${Number(v || 0).toFixed(1)}%`;
    setText('qm-historical-prior', fmtPct(pct.historical_prior));
    setText('qm-operational-residual', fmtPct(pct.operational_residual));
    setText('qm-raw', fmtPct(pct.raw));
    setText('qm-low-confidence', Number(prov?.low_confidence || 0).toLocaleString());

    const note = document.getElementById('qm-provenance-note');
    if (note && prov) {
        if (Number(prov.total_values || 0) === 0) {
            if (artifact.reason) {
                note.innerText = `No QM correction was applied in this runner export. ${artifact.reason}. Current consensus is using raw model values plus preserved dynamic weights.`;
            } else {
                note.innerText = 'No QM correction was applied in this runner export. Current consensus is using raw model values plus preserved dynamic weights; full QM provenance requires the historical QM runtime artifact or local qm_cdfs table.';
            }
        } else {
            const artifactText = artifact.imported
                ? ` Runtime artifact loaded (${Number(artifact.imported_cdfs || 0).toLocaleString()} CDF rows).`
                : '';
            note.innerText = `${Number(prov.lead_aware_pending || 0).toLocaleString()} values are using historical prior while lead-aware residual QM is still pending.${artifactText} Rain amount correction remains strict/pending; occurrence risk is handled separately.`;
        }
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
        const cadenceBadge = document.getElementById('model-cadence-badge');
        const meta = modelsData['Model_Metadata']?.[modelName] || {};
        if (initBadge) {
            let pullStr = modelsData['Data_Pull']?.[modelName] || "Unknown";
            if (pullStr === "Unknown") {
                initBadge.innerText = "Data pull: Unknown";
                initBadge.style.color = "var(--text-secondary)";
                initBadge.style.borderColor = "var(--border-glass)";
            } else {
                if (!pullStr.endsWith('Z')) {
                    pullStr = pullStr.replace(' ', 'T') + 'Z';
                }
                const d = new Date(pullStr);
                const day = String(d.getUTCDate()).padStart(2, '0');
                const hrs = String(d.getUTCHours()).padStart(2, '0');
                const mns = String(d.getUTCMinutes()).padStart(2, '0');
                initBadge.innerText = `Data pull: ${day} ${hrs}${mns}Z`;
                initBadge.style.color = "var(--cyan)";
                initBadge.style.borderColor = "rgba(0, 212, 255, 0.25)";
            }
        }
        if (cadenceBadge) {
            const output = meta.expected_output_interval_hours ?? 1;
            const update = meta.provider_update_frequency_hours ?? '-';
            const provider = meta.provider || 'Unknown provider';
            const note = meta.hourly_output_note || 'Open-Meteo hourly output grid; provider update frequency may differ.';
            cadenceBadge.innerText = `${provider} | Output grid: ${output}h | Provider update: ${update}h | ${note}`;
        }
        
        timeLabels.forEach(dt => {
            const temp = modelsData['Temperature']?.[modelName]?.[dt];
            const dew = modelsData['Dewpoint']?.[modelName]?.[dt];
            const pres = modelsData['Pressure']?.[modelName]?.[dt];
            const windDir = modelsData['Wind Dir.']?.[modelName]?.[dt];
            const windSpd = modelsData['Wind Speed']?.[modelName]?.[dt];
            const gust = modelsData['Wind Gust']?.[modelName]?.[dt];
            const rain = modelsData['Rainfall']?.[modelName]?.[dt];
            const probRain = modelsData['Precip Probability']?.[modelName]?.[dt] ?? modelsData['Prob Precip 1.0mm']?.[modelName]?.[dt];
            
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
