const SPREAD_MAX_HOURS = 96;
let pendingSpreadModelsData = null;
let pendingSpreadTimeLabels = [];
let spreadChartsRendered = false;
let pendingWeightHistoryText = null;
let weightDriftChart = null;
let pendingPreviewChartData = null;
let previewChartsRendered = false;
function renderPreviewChartsIfVisible() {
    if (!pendingPreviewChartData || previewChartsRendered || !document.getElementById('tab-meteogram')?.classList.contains('active')) return;
    if (!previewIsCurrent(pendingPreviewChartData.manifest)) return;
    previewChartsRendered = true;
    try {
        if (typeof ApexCharts !== 'function') throw Error('Chart library unavailable');
        initTitanCharts(pendingPreviewChartData.chartData);
        renderWindBarbs(pendingPreviewChartData.rows);
    } catch (err) {
        const panel=document.getElementById('tab-meteogram');
        panel.replaceChildren();const note=document.createElement('p');note.textContent='Meteogram unavailable. Use the hourly matrix and individual model tab if available.';panel.append(note);
        console.error('Meteogram render failed',err);
    }
}

const WEIGHT_DRIFT_COLORS = ['#0ea5e9', '#0284c7', '#f5a623', '#d97706', '#ec4899', '#be185d', '#10b981', '#94a3b8'];
const WEIGHT_DRIFT_MODEL_LABELS = {
    ECMWF_HRES: 'ECMWF',
    GFS_GLOBAL: 'GFS',
    ICON_SEAMLESS: 'ICON',
    GEM_GLOBAL: 'GEM',
    CMA_GRAPES_GLOBAL: 'CMA',
    JMA_GSM: 'JMA',
    METEOFRANCE_ARPEGE_WORLD: 'ARPEGE',
    UKMO_GLOBAL_10KM: 'UKMO',
};

function parseWeightHistory(historyText) {
    return String(historyText || '').split(/\r?\n/)
        .map(line => line.trim())
        .filter(Boolean)
        .map(line => {
            try { return JSON.parse(line); } catch (_) { return null; }
        })
        .filter(snapshot => snapshot && snapshot.timestamp && snapshot.weights)
        .map(snapshot => {
            const rawTimestamp = String(snapshot.timestamp).trim();
            const normalizedTimestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(rawTimestamp)
                ? rawTimestamp
                : `${rawTimestamp}Z`;
            return { ...snapshot, timeMs: new Date(normalizedTimestamp).getTime() };
        })
        .filter(snapshot => Number.isFinite(snapshot.timeMs))
        .sort((a, b) => a.timeMs - b.timeMs);
}

function weightHistoryForParameter(snapshots, parameter) {
    const records = snapshots
        .filter(snapshot => snapshot.weights && snapshot.weights[parameter])
        .map(snapshot => ({
            timeMs: snapshot.timeMs,
            values: snapshot.weights[parameter],
        }));
    if (!records.length) return [];

    const signature = record => Object.entries(record.values || {})
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([model, value]) => `${model}:${Number(value || 0).toFixed(6)}`)
        .join('|');
    const changePoints = [];
    let previous = null;
    let previousSignature = null;

    records.forEach(record => {
        const currentSignature = signature(record);
        if (previousSignature === null || currentSignature !== previousSignature) {
            if (previous && changePoints[changePoints.length - 1]?.timeMs !== previous.timeMs) {
                changePoints.push(previous);
            }
            changePoints.push(record);
            previousSignature = currentSignature;
        }
        previous = record;
    });
    if (previous && changePoints[changePoints.length - 1]?.timeMs !== previous.timeMs) {
        changePoints.push(previous);
    }
    return changePoints;
}

function countWeightTransitions(snapshots, parameter) {
    let previousSignature = null;
    let transitions = 0;
    snapshots.forEach(snapshot => {
        const values = snapshot.weights?.[parameter];
        if (!values) return;
        const signature = Object.entries(values)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([model, value]) => `${model}:${Number(value || 0).toFixed(6)}`)
            .join('|');
        if (previousSignature !== null && signature !== previousSignature) transitions += 1;
        previousSignature = signature;
    });
    return transitions;
}

function renderWeightDriftChart() {
    const select = document.getElementById('weight-drift-parameter');
    const summary = document.getElementById('weight-drift-summary');
    const chartEl = document.getElementById('weight-drift-chart');
    const empty = document.getElementById('weight-drift-empty');
    if (!select || !summary || !chartEl || !empty) return;

    const snapshots = parseWeightHistory(pendingWeightHistoryText);
    const parameters = [...new Set(snapshots.flatMap(snapshot => Object.keys(snapshot.weights || {})))];
    if (!parameters.length) {
        summary.textContent = 'No recorded weight history is available yet.';
        chartEl.hidden = true;
        empty.hidden = false;
        empty.textContent = 'The chart will appear after the pipeline writes a valid weight-history snapshot.';
        return;
    }

    const prior = select.value;
    select.replaceChildren();
    parameters.forEach(parameter => {
        const option = document.createElement('option');
        option.value = parameter;
        option.textContent = parameter;
        select.appendChild(option);
    });
    select.value = parameters.includes(prior) ? prior : parameters[0];
    chartEl.hidden = false;
    empty.hidden = true;

    const draw = () => {
        const parameter = select.value;
        const history = weightHistoryForParameter(snapshots, parameter);
        const models = [...new Set(history.flatMap(record => Object.keys(record.values || {})))].sort();
        const actualUpdates = countWeightTransitions(snapshots, parameter);
        const latest = snapshots[snapshots.length - 1];
        const latestLabel = latest ? new Date(latest.timeMs).toLocaleString('en-GB', { timeZone: 'UTC', timeZoneName: 'short' }) : 'unknown';
        summary.textContent = `${snapshots.length} recorded runs; ${actualUpdates} actual weight transition${actualUpdates === 1 ? '' : 's'}; latest ${latestLabel}. Consecutive identical snapshots are collapsed.`;

        if (weightDriftChart) {
            weightDriftChart.destroy();
            weightDriftChart = null;
        }
        const series = models.map(model => ({
            name: WEIGHT_DRIFT_MODEL_LABELS[model] || model,
            data: history.map(record => [record.timeMs, Number(record.values?.[model] || 0) * 100]),
        }));
        weightDriftChart = new ApexCharts(chartEl, {
            ...TITAN_BASE,
            chart: {
                ...TITAN_BASE.chart,
                id: 'weight-drift',
                type: 'line',
                height: 340,
                toolbar: { show: false },
            },
            series,
            colors: WEIGHT_DRIFT_COLORS,
            stroke: { curve: 'stepline', width: 2, dashArray: [0, 5, 0, 5, 0, 5, 2, 8] },
            xaxis: {
                ...TITAN_BASE.xaxis,
                type: 'datetime',
                labels: { ...TITAN_BASE.xaxis.labels, format: 'dd MMM HH:mm' },
            },
            yaxis: {
                min: 0,
                max: 100,
                tickAmount: 5,
                labels: { formatter: value => `${Number(value).toFixed(0)}%` },
            },
            legend: {
                ...TITAN_BASE.legend,
                position: 'bottom',
                horizontalAlign: 'left',
            },
            tooltip: {
                ...TITAN_BASE.tooltip,
                y: { formatter: value => `${Number(value).toFixed(1)}%` },
            },
            noData: { text: 'No weight history for this parameter.' },
        });
        weightDriftChart.render();
    };
    select.onchange = draw;
    draw();
}

function previewIsCurrent(manifest, now = Date.now()) {
    if (!manifest || manifest.contract !== 'raw-model-preview-v1' || manifest.status !== 'model_only') return false;
    const start = Date.parse(manifest.as_of_utc), end = Date.parse(manifest.expires_at_utc);
    return Number.isFinite(start) && Number.isFinite(end) && start <= now && now < end;
}

function displayNumber(value, decimals = 1, suffix = '') {
    return value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(decimals) + suffix;
}

function updatePullStatus(timestamp) {
    const statusEl = document.getElementById('pull-status');
    if (!statusEl) return;

    if (!timestamp || timestamp === "Unknown") {
        statusEl.innerText = "No pull data";
        statusEl.className = "pull-status stale";
        return;
    }

    const raw = String(timestamp).trim().replace(' ', 'T');
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? raw : raw + 'Z';
    const pulledAt = new Date(normalized);
    if (Number.isNaN(pulledAt.getTime())) {
        statusEl.innerText = "Pull time unknown";
        statusEl.className = "pull-status stale";
        return;
    }

    const ageHours = (Date.now() - pulledAt.getTime()) / 36e5;
    if (ageHours < 0) {statusEl.innerText='Future pull time';statusEl.className='pull-status stale';return;}
    if (ageHours <= 3) {
        statusEl.innerText = "Fresh";
        statusEl.className = "pull-status fresh";
    } else {
        statusEl.innerText = `Stale archive ${ageHours.toFixed(1)}h`;
        statusEl.className = "pull-status stale";
    }
}

function renderThunderstormProxy(proxy, renderData) {
    const tsProbVal = document.getElementById('ts-prob-val');
    const tsProbTime = document.getElementById('ts-prob-time');
    const audit = document.getElementById('ts-proxy-audit');
    const method = document.getElementById('ts-proxy-method');
    const peak = proxy?.peak;

    if (!peak) {
        if (tsProbVal) tsProbVal.innerText = '--%';
        if (tsProbTime) tsProbTime.innerText = 'No proxy calculation available';
        if (audit) audit.innerText = 'No peak calculation available.';
        return;
    }

    const total = Number(peak.total || 0);
    if (tsProbVal) {
        tsProbVal.innerText = `${total.toFixed(0)}%`;
        tsProbVal.style.color = total >= 30 ? 'var(--red)' : 'var(--amber)';
    }
    if (tsProbTime) {
        tsProbTime.innerText = `Peak window: ${String(peak.datetime || '').substring(11, 16)} WITA`;
    }

    if (audit) {
        const rows = (peak.components || []).map(component => {
            const input = component.input === null || component.input === undefined ? '-' : component.input;
            const unit = component.unit ? ` ${component.unit}` : '';
            const contribution = Number(component.contribution || 0);
            const contributionText = contribution > 0 ? `+${contribution.toFixed(2)}` : contribution.toFixed(2);
            return `<tr><td>${component.label}</td><td>${input}${unit}</td><td>${component.rule}</td><td style="font-weight:700; color:${contribution > 0 ? 'var(--amber)' : 'var(--text-secondary)'}; text-align:right;">${contributionText}</td></tr>`;
        }).join('');
        const override = peak.weather_code_override
            ? `<div style="margin-top:10px; color:var(--red);">WMO thunderstorm code applies an 85-point minimum.</div>`
            : '';
        audit.innerHTML = `
            <div style="display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:12px; color:var(--text-secondary);">
                <span>Peak: <strong style="color:var(--text-primary);">${peak.datetime} WITA</strong></span>
                <span>WMO ${peak.weather_code ?? '-'}: <strong style="color:var(--text-primary);">${peak.weather_description || 'Unknown'}</strong></span>
            </div>
            <table class="data-table" style="min-width:680px;"><thead><tr><th>Signal</th><th>Input</th><th>Rule</th><th style="text-align:right;">Score</th></tr></thead>
            <tbody>${rows}<tr><td colspan="3" style="font-weight:700;">Raw proxy score</td><td style="font-weight:700; text-align:right;">${Number(peak.raw_score || 0).toFixed(2)}</td></tr>
            <tr><td colspan="3" style="font-weight:700; color:var(--amber);">Final TS proxy score</td><td style="font-weight:700; color:var(--amber); text-align:right;">${total.toFixed(2)} / 100</td></tr></tbody></table>${override}`;
    }

    if (method) {
        const codes = (proxy.weather_code_legend || []).map(entry =>
            `<div><strong>${entry.code}</strong> ${entry.description}</div>`
        ).join('');
        method.innerHTML = `
            <p>${proxy.formula || ''}</p>
            <p><strong>Interpretation:</strong> this is a convective-support proxy, not a calibrated thunderstorm probability. TAF TS groups are decided separately by aviation guidance logic.</p>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:6px 18px; margin-top:12px;">${codes}</div>`;
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
            fetch('data/system_workflow.json' + cb).then(r => r.json()),
            fetch('data/operational_residuals.json' + cb).then(r => r.json()),
            fetch('data/tafor_shadow.json' + cb).then(r => r.json()),
            fetch('data/weight_history.jsonl' + cb).then(response => {
                if (!response.ok) throw new Error(`weight history HTTP ${response.status}`);
                return response.text();
            })
        ]);
        
        const intelData = results[0].status === 'fulfilled' && results[0].value ? results[0].value : {};
        const hasValidatedDefault = intelData.default?.default_selection?.selection_status === 'selected';
        let currentIssuance = hasValidatedDefault ? "default" : "2300";
        let intel = intelData[currentIssuance] || intelData || {};
        const weights = results[1].status === 'fulfilled' && results[1].value ? results[1].value : {weights: {}};
        const perf = results[2].status === 'fulfilled' ? results[2].value : null;
        const guidanceJson = results[3].status === 'fulfilled' && results[3].value ? results[3].value : {data: []};
        const data = guidanceJson.data || [];
        const generatedAt = guidanceJson.metadata?.latest_data_pull_utc || "Unknown";

        // Filter data to only show current and future hours
        const now = new Date();
        const localNow = new Date(now.getTime() + (8 * 60 * 60 * 1000));
        const witaHourStr = localNow.toISOString().replace('T', ' ').substring(0, 13) + ':00:00';
        
        const futureData = Array.isArray(data) ? data.filter(d => d && Number.isFinite(previewValidEpoch(d.Datetime))) : [];
        const previewManifest = guidanceJson.metadata?.preview_manifest;
        const candidateRows = TitanPreview.windowRows(futureData,now.getTime(),titanPreviewSettings().hours);
        const currentPreview = previewIsCurrent(previewManifest) && candidateRows.length > 0;
        const renderData = currentPreview ? candidateRows : [];
        renderBriefHourly(renderData, currentPreview ? previewManifest : null);
        const safetyBanner = document.getElementById('preview-safety-banner');
        if (safetyBanner) safetyBanner.textContent = currentPreview
            ? `RAW MODEL PREVIEW — ${Object.keys(previewManifest.models || {}).length} fresh model collections; unverified equal-model mean of original models (IFS 9 km independent), not an operational forecast. TAF withheld. Expires ${previewManifest.expires_at_utc}. Historical verification remains research-only.`
            : 'EXPIRED OR UNAVAILABLE — no current model guidance. Automated TAF withheld. Historical verification is research-only.';
        if (currentPreview) setTimeout(() => {
            if (safetyBanner) safetyBanner.textContent = 'EXPIRED — current guidance unavailable. Reloading…';
            for (const id of ['tab-meteogram', 'tab-spread', 'tab-matrix', 'tab-models']) {
                const el = document.getElementById(id); if (el) el.replaceChildren();
            }
            pendingPreviewChartData=null;pendingSpreadModelsData=null;pendingSpreadTimeLabels=[];
            renderBriefHourly([],null);
            window.location.reload();
        }, Math.max(0, Date.parse(previewManifest.expires_at_utc) - Date.now()));

        const clim_data = results[4].status === 'fulfilled' ? results[4].value : null;
        const modelsData = currentPreview && results[5].status === 'fulfilled' &&
            JSON.stringify(results[5].value?.Preview_Manifest) === JSON.stringify(previewManifest) ? results[5].value : null;
        const diurnalData = results[8].status === 'fulfilled' ? results[8].value : null;
        const workflowData = results[9].status === 'fulfilled' ? results[9].value : null;
        const residualData = results[10].status === 'fulfilled' ? results[10].value : null;
        const shadowTafData = results[11].status === 'fulfilled' ? results[11].value : null;
        pendingWeightHistoryText = results[12].status === 'fulfilled' ? results[12].value : null;
        
        const healthData = results[6].status === 'fulfilled' ? results[6].value : null;
        if (healthData) {
            const fcElem = document.getElementById('db-forecast-count');
            const histFcElem = document.getElementById('db-historical-forecast-count');
            const obsElem = document.getElementById('db-obs-count');
            const obs1mElem = document.getElementById('db-obs-1min-count');
            const qmRuntimeElem = document.getElementById('db-runtime-qm-count');
            const sizeElem = document.getElementById('db-size');
            const syncElem = document.getElementById('db-last-sync');
            const obsFreshnessElem = document.getElementById('db-obs-freshness');
            const verificationElem = document.getElementById('db-verification-status');
            
            if (fcElem) fcElem.innerText = Number(healthData.current_forecast_records ?? healthData.forecast_records ?? 0).toLocaleString();
            if (histFcElem) histFcElem.innerText = Number(healthData.historical_forecast_records ?? healthData.openmeteo_records ?? 0).toLocaleString();
            if (obsElem) obsElem.innerText = Number(healthData.observation_records || 0).toLocaleString();
            if (obs1mElem) obs1mElem.innerText = Number(healthData.observation_1min_records || 0).toLocaleString();
            if (qmRuntimeElem) qmRuntimeElem.innerText = Number(healthData.runtime_qm_cdfs_enabled ?? healthData.qm_cdfs_enabled ?? 0).toLocaleString();
            if (sizeElem) sizeElem.innerText = healthData.size_mb + ' MB';
            if (syncElem) syncElem.innerText = (healthData.latest_data_pull_utc || healthData.last_sync_utc || healthData.latest_model_run_init_utc) + ' UTC';
            const observationFreshness = healthData.observation_freshness || {};
            const hourlyObservation = observationFreshness.hourly || {};
            if (obsFreshnessElem) {
                const stamp = hourlyObservation.latest_obs_utc;
                const parsed = stamp ? Date.parse(stamp.replace(' ', 'T') + (stamp.endsWith('Z') || stamp.includes('+') ? '' : 'Z')) : NaN;
                const age = (Date.now() - parsed) / 36e5;
                obsFreshnessElem.innerText = Number.isFinite(age) && age >= 0
                    ? `Last archived hourly observation (${age.toFixed(1)} h ago)` : 'Unknown';
                obsFreshnessElem.style.color = 'var(--amber)';
            }
            if (verificationElem) {
                verificationElem.innerText = 'Historical verification only — frozen pending review';
                verificationElem.style.color = 'var(--amber)';
            }
            setupHealthFreshness([]); // precomputed legacy status is not current evidence
            setupSelectionAndObservationQuality(
                healthData.forecast_selection || {},
                healthData.awos_quality || {}
            );
            setupQMProvenance(healthData.qm_provenance || null);
            setupOperationalResiduals(residualData || {metadata: healthData.operational_residuals || {}});
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
                
                const diff = Math.max(0, target - now);
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
        
        renderThunderstormProxy(null, []);

        // 1. Update Header
        document.getElementById('update-time').innerText = generatedAt + " UTC";
        updatePullStatus(currentPreview ? generatedAt : 'Unknown');
        
        // All automated drafts, including legacy and experimental payloads,
        // are withheld in the raw-model preview release.
        document.getElementById('taf-text-display').textContent = 'Automated TAF withheld pending independent meteorological validation.';
        document.getElementById('bg-params').replaceChildren();
        document.getElementById('taf-warnings').textContent = 'Use station observations, authorized forecasts and forecaster judgement.';
        document.getElementById('taf-manual-boxes').replaceChildren();
        for (const id of ['toggle-manual-btn', 'copy-taf-btn', 'taf-issuance-select']) {
            const el = document.getElementById(id); if (el) { el.disabled = true; el.hidden = true; }
        }
        const narration = document.getElementById('taf-narration');
        if (narration) narration.textContent = 'Automated TAF unavailable.';
        const shadowPanel = document.getElementById('shadow-taf-panel');
        if (shadowPanel) shadowPanel.hidden = true;

        
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
        const eventDiagnostics = weights.metadata?.event_weight_diagnostics || {};
        const eventWeightNotes = ['Rainfall', 'Wind Gust']
            .map(param => {
                const diag = eventDiagnostics[param];
                if (!diag) return '';
                const status = diag.applied ? 'event-window blend active' : 'event-window blend pending';
                const reason = diag.reason || 'waiting for enough verified events';
                const blend = diag.blend_factor !== undefined ? ` (${(Number(diag.blend_factor) * 100).toFixed(0)}% event nudge)` : '';
                return `<div><strong>${param}:</strong> ${status}${blend}. ${reason}</div>`;
            })
            .filter(Boolean)
            .join('');
        if (eventWeightNotes) {
            wHtml += `
                <div class="weight-status-card">
                    ${eventWeightNotes}
                </div>
            `;
        }
        for (const [param, modelWeights] of Object.entries(weights.weights || {})) {
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
        if (document.getElementById('tab-weighter')?.classList.contains('active')) {
            renderWeightDriftChart();
        }
        // Render data is already filtered above

        // 4. Meteogram Tab (using apexcharts from guidance)
        // Convert to [timestamp_ms, value] format expected by apexcharts-theme.js
        const chartData = {
            tempData: renderData.map(d => [previewValidEpoch(d.Datetime), d.Temperature]),
            dewData: renderData.map(d => [previewValidEpoch(d.Datetime), d.Dewpoint]),
            windData: renderData.map(d => [previewValidEpoch(d.Datetime), d.Wind]),
            gustData: renderData.map(d => [previewValidEpoch(d.Datetime), d.Gust ?? null]),
            rainData: renderData.map(d => [previewValidEpoch(d.Datetime), d.Rain]),
            windDirData: renderData.map(d => [previewValidEpoch(d.Datetime), d['Wind Dir.'] ?? null]),
            highCloudData: renderData.map(d => [previewValidEpoch(d.Datetime), d['High Clouds'] ?? null]),
            midCloudData: renderData.map(d => [previewValidEpoch(d.Datetime), d['Mid Clouds'] ?? null]),
            lowCloudData: renderData.map(d => [previewValidEpoch(d.Datetime), d['Low Clouds'] ?? null]),
            cumulativeRain: TitanPreview.cumulative(renderData),
            ttdData: renderData.map(d=>[previewValidEpoch(d.Datetime), TitanPreview.numeric(d.Temperature)===null||TitanPreview.numeric(d.Dewpoint)===null?null:d.Temperature-d.Dewpoint]),
            pressureData: renderData.map(d => [previewValidEpoch(d.Datetime), d.Pressure ?? null]),
            condData: renderData.map(d => [previewValidEpoch(d.Datetime), d.Condition ?? null])
        };
        
        // Initialize charts using the new TITAN_BASE design system
        if (currentPreview && renderData.length) { pendingPreviewChartData={chartData,rows:renderData,manifest:previewManifest}; renderPreviewChartsIfVisible(); }
        else for (const id of ['tab-meteogram', 'tab-spread', 'tab-matrix', 'tab-models']) {
            const panel = document.getElementById(id);
            if (panel) { const notice = document.createElement('p'); notice.textContent = 'Current model guidance unavailable.'; panel.prepend(notice); }
        }
        
        // 5. Matrix Tab
        const tbody = document.querySelector("#data-table tbody");
        renderData.forEach((d, idx) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>
                    ${titanPreviewLabel(previewValidEpoch(d.Datetime))}<br>
                    <span style="font-size:0.8em; color:var(--text-secondary)">${d.Model_Count ?? '—'} models</span>
                </td>
                <td style="color: #ef4444">${displayNumber(d.Temperature)}</td>
                <td style="color: #3b82f6">${displayNumber(d.Dewpoint)}</td>
                <td style="color: #a855f7">${displayNumber(d.Pressure)}</td>
                <td>${displayNumber(d['Wind Dir.'], 0, '°')}</td>
                <td style="color: #10b981">${displayNumber(d.Wind)}</td>
                <td style="color: #f59e0b">${displayNumber(d.Gust)}</td>
                <td style="color: #0ea5e9">${displayNumber(d.Rain)}</td>
                <td>${displayNumber(d['Precip Probability'], 1, '%')}</td>
                <td>${d.Condition || '-'}</td>
            `;
            const timeCell=tr.firstElementChild;
            if(timeCell){const link=document.createElement('button');link.type='button';link.className='point-spatial-link';link.textContent='Rain map';link.title='Open spatial rain at this valid time, if available; accumulation windows may differ';link.onclick=()=>openSpatialDiagnostic('rain','kolaka',new Date(previewValidEpoch(d.Datetime)).toISOString());timeCell.appendChild(link);}
            tbody.appendChild(tr);
        });
        
        // 5.5 Individual Models
        if (modelsData && renderData.length) {
            setupIndividualModels(modelsData, renderData.map(d=>d.Datetime));
            pendingSpreadModelsData = modelsData;
            pendingSpreadTimeLabels = renderData.map(d=>d.Datetime).slice(0, SPREAD_MAX_HOURS);
            spreadChartsRendered = false;
            if (document.getElementById('tab-spread')?.classList.contains('active')) {
                setupSpreadCharts(pendingSpreadModelsData, pendingSpreadTimeLabels);
            }
        }
        
        else if (currentPreview) { for(const id of ['tab-spread','tab-models']) {const p=document.getElementById(id);if(p){const n=document.createElement('p');n.className='section-note';n.textContent='Matching individual model payload unavailable.';p.prepend(n);}} }
        // 6. Regional & Climatology
        try {
            if(document.getElementById('tab-regional')?.classList.contains('active'))setupRegionalCharts();
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
                setupHealthFreshness([]); // never promote legacy freshness rows to current evidence
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
        if (tafDisplay) tafDisplay.textContent = 'Automated TAF withheld. Dashboard data load failed.';
        pendingPreviewChartData=null;pendingSpreadModelsData=null;pendingSpreadTimeLabels=[];
        const banner=document.getElementById('preview-safety-banner');if(banner)banner.textContent='UNAVAILABLE — dashboard data load failed. Automated TAF withheld.';
        for(const id of ['tab-meteogram','tab-spread','tab-matrix','tab-models']){const panel=document.getElementById(id);if(panel){panel.replaceChildren();const note=document.createElement('p');note.textContent='Current model guidance unavailable.';panel.append(note);}}
    }
}

function syncSpatialTheme() {
    const frame = document.getElementById('spatial-viewer');
    if (frame?.contentWindow && frame.hasAttribute('src')) {
        frame.contentWindow.postMessage({type:'titan-spatial-theme',theme:document.documentElement.classList.contains('light-mode')?'light':'dark'},location.origin);
    }
}
let spatialSelectionHash='';
function openSpatialDiagnostic(product,domain,validUtc){
    const allowed=TitanWorkflow.diagnostics.flatMap(x=>x.products.map(p=>p.id));
    if(!allowed.includes(product)||!['kolaka','sulawesi','regional'].includes(domain))return;
    const frame=document.getElementById('spatial-viewer');
    const q=new URLSearchParams(spatialSelectionHash.slice(1));q.set('product',product);q.set('domain',domain);
    if(validUtc&&Number.isFinite(Date.parse(validUtc))){q.set('time',validUtc);q.set('window','[]');}
    const src=new URL(frame.dataset.src,location.href);
    src.searchParams.set('theme',document.documentElement.classList.contains('light-mode')?'light':'dark');
    src.hash=q.toString();spatialSelectionHash=src.hash;
    frame.src=src.href;switchTab('tab-spatial');
}
function switchTab(tabId) {
    document.body.classList.toggle('spatial-active', tabId === 'tab-spatial');
    document.body.classList.toggle('observations-active',tabId==='tab-observations');
    document.body.classList.toggle('regional-active',tabId==='tab-regional');
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-links li').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tabId).classList.add('active');
    document.querySelector(`.nav-links li[data-tab="${tabId}"]`).classList.add('active');
    document.querySelectorAll('.nav-links li[data-tab]').forEach(el=>el.setAttribute('aria-current',el.getAttribute('data-tab')===tabId?'page':'false'));
    updateWorkspaceNavigation(tabId);
    if(window.location.hash !== '#'+tabId) window.history.replaceState(null,'','#'+tabId);
    setTimeout(()=>window.dispatchEvent(new Event('resize')),0);

    if(tabId==='tab-observations'||tabId==='tab-regional') {document.querySelector('.content').scrollTop=0;}
    if(tabId==='tab-regional') setupRegionalCharts();
    if (tabId === 'tab-spatial') {
        const frame = document.getElementById('spatial-viewer');
        if (frame && !frame.hasAttribute('src')) {
            const src = new URL(frame.dataset.src,location.href);
            src.searchParams.set('theme',document.documentElement.classList.contains('light-mode')?'light':'dark');
            frame.src = src.href;
        }
        document.querySelector('.content').scrollTop=0;
        syncSpatialTheme();
    }
    if (tabId === 'tab-meteogram') renderPreviewChartsIfVisible();
    if (tabId === 'tab-spread' && pendingSpreadModelsData && !spreadChartsRendered) {
        try { setupSpreadCharts(pendingSpreadModelsData, pendingSpreadTimeLabels); } catch (err) { console.error('Spread render failed',err); renderRainHeatmap(pendingSpreadModelsData,pendingSpreadTimeLabels); }
    }
    if (tabId === 'tab-weighter') {
        if (weightDriftChart) {
            setTimeout(() => window.dispatchEvent(new Event('resize')), 0);
        } else {
            renderWeightDriftChart();
        }
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
            syncSpatialTheme();
        });
    }

    const spatialFrame = document.getElementById('spatial-viewer');
    spatialFrame?.addEventListener('load',syncSpatialTheme);
    window.addEventListener('message',e=>{
        if(e.origin!==location.origin || e.source!==spatialFrame?.contentWindow || e.data?.type!=='titan-spatial-state')return;
        const hash=e.data.hash;
        if(typeof hash!=='string'||hash.length>2048||!hash.startsWith('#'))return;
        spatialSelectionHash=hash;
        const link=document.getElementById('spatial-fullscreen');
        if(link){const url=new URL('./titan/',location.href);url.hash=hash;link.href=url.href;}
    });
    document.querySelectorAll('.nav-links li[data-tab]').forEach(li => {
        li.setAttribute('role','button'); li.tabIndex = 0;
        const activate = () => switchTab(li.getAttribute('data-tab'));
        li.addEventListener('click', activate);
        li.addEventListener('keydown', e => { if(e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } });
    });
    document.querySelectorAll('[data-open-tab]').forEach(btn=>btn.addEventListener('click',()=>switchTab(btn.getAttribute('data-open-tab'))));
    const initialTab = window.location.hash.slice(1);
    if (/^tab-[a-z]+$/.test(initialTab) && document.getElementById(initialTab) && document.querySelector('.nav-links li[data-tab="'+initialTab+'"]')) switchTab(initialTab);
    
    setupPreviewControls();
    setupAnalysisWorkspace();
    setupObservationPanel();
    setupRemoteObservations();
    setupForecastWorkflow();
    setupWorkstationShell();
    loadDashboard();
});

function setupSpreadCharts(modelsData, timeLabels) {
    spreadChartsRendered = true;
    const spreadTimeLabels = (timeLabels || []).slice(0, SPREAD_MAX_HOURS);
    ['spread-temp', 'spread-t-td', 'spread-wind', 'spread-rain'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    });

    const percentile=(vals,p)=>{
        if(!vals.length)return null;
        const sorted=[...vals].sort((a,b)=>a-b), i=(sorted.length-1)*p, lo=Math.floor(i),hi=Math.ceil(i);
        return sorted[lo]+(sorted[hi]-sorted[lo])*(i-lo);
    };
    const makeDistribution=(param,paired=false)=>spreadTimeLabels.map(t=>{
        const vals=[];
        for(const [model,values] of Object.entries(modelsData[param]||{})){
            const v=TitanPreview.numeric(values?.[t]);
            if(v===null)continue;
            if(paired){const dew=TitanPreview.numeric(modelsData.Dewpoint?.[model]?.[t]);if(dew===null)continue;vals.push(v-dew);}
            else vals.push(v);
        }
        return {x:previewValidEpoch(t),vals};
    });
    const draw=(id,title,unit,param,paired=false)=>{
        const el=document.getElementById(id),points=makeDistribution(param,paired);
        if(!points.some(p=>p.vals.length)){el.textContent='No matched model values.';return;}
        const series=[['Minimum',0],['P25',.25],['Median',.5],['P75',.75],['Maximum',1]].map(([name,p])=>({name,data:points.map(d=>[d.x,percentile(d.vals,p)])}));
        const note=document.createElement('p');note.className='section-note';
        const counts=points.map(d=>d.vals.length);
        note.textContent='Contributing models per hour: '+Math.min(...counts)+'–'+Math.max(...counts)+'. Quantiles are descriptive; with one model all five lines coincide.';
        el.append(note);
        const mount=document.createElement('div');el.append(mount);
        const chart=new ApexCharts(mount,{
            ...TITAN_BASE,chart:{...TITAN_BASE.chart,id:id+'-distribution',type:'line',height:280,animations:{enabled:false}},
            title:{text:title},series,colors:['#94a3b8','#38bdf8','#0ea5e9','#38bdf8','#94a3b8'],
            stroke:{curve:'straight',width:[1,1.5,3,1.5,1],dashArray:[4,2,0,2,4]},
            dataLabels:{enabled:false},xaxis:previewXAxis(),tooltip:{...TITAN_BASE.tooltip,shared:true},
            yaxis:{title:{text:unit},labels:{formatter:v=>Number.isFinite(v)?v.toFixed(1):'—'}}
        });
        chart.render().catch(err=>{console.error('Distribution render failed: '+id,err);mount.textContent='Chart rendering failed; use individual model table.';});
    };
    draw('spread-temp','Temperature model distribution','°C','Temperature');
    draw('spread-t-td','Temperature − dewpoint distribution','°C','Temperature',true);
    draw('spread-wind','Wind speed model distribution','kt','Wind Speed');
    renderRainHeatmap(modelsData,spreadTimeLabels);
}

function setupRegionalCharts() {
    const entries=[
        ['regional-olr-img','regional-olr-status','https://cews.bmkg.go.id/operational-early-warning-pdi/0_Latest/olr.cfs.all.indonesia.2_latest.png'],
        ['sat-img','regional-sat-status','https://inderaja.bmkg.go.id/IMAGE/HIMA/H08_EH_Indonesia.png']
    ];
    for(const [id,statusId,url] of entries){
        const img=document.getElementById(id),status=document.getElementById(statusId);if(!img||!status||img.hasAttribute('src'))continue;
        img.hidden=true;status.textContent='Requesting source image…';
        img.addEventListener('load',()=>{img.hidden=false;status.textContent='Image received. Check printed issue/valid time; receipt is not product time.';});
        img.addEventListener('error',()=>{img.hidden=true;status.textContent='Image unavailable now. Use the official source link; no stale image displayed.';});
        img.src=url;
    }
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
    document.getElementById('diurnal-param-select').addEventListener('change', renderVerification);
    
    renderVerification();
}

function renderVerification() {
    if(!window.verifyData) return;
    const lbMetrics = window.verifyData.metrics[window.currentLookback];
    if(!lbMetrics) return;
    
    if (window.currentVerifySub === 'verify-skill') {
        renderHistoricalScorecard(lbMetrics);
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

        renderEventWindowVerification(lbMetrics);
    }
    else if (window.currentVerifySub === 'verify-leadtime') {
        document.getElementById('verify-leadtime-chart').textContent='No validated lead-time results available. Missing initialization timestamps must be excluded and paired sample counts verified before this diagnostic can be restored.';
    }
    else if (window.currentVerifySub === 'verify-diurnal') {
        const param = document.getElementById('diurnal-param-select').value;
        const dData = lbMetrics[param]?.diurnal_bias;
        const legacyBias=document.getElementById('verify-diurnal-chart');
        legacyBias.textContent='Legacy diurnal bias plot withheld: the historical exporter filled absent hourly groups and circular errors with zero. Per-hour counts are not exported, so true zero bias cannot be distinguished from missing evidence.';
        return; // preserve archive; do not reinterpret placeholder values
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
                    <p style="margin-top: 10px; font-weight: bold; color: var(--text-secondary);">
                        ${TitanResearch.significance(sigData.fused.p_value)}
                    </p>
                </div>
            `;
            document.getElementById('verify-significancy-content').innerHTML = html;
        } else {
            document.getElementById('verify-significancy-content').innerText = 'No significancy test data available.';
        }
    }
}

function renderEventWindowVerification(lbMetrics) {
    const el = document.getElementById('verify-event-window-table');
    if (!el) return;
    const rows = [];
    const collect = (param, label) => {
        const models = lbMetrics[param]?.event_windows?.models || {};
        Object.entries(models).forEach(([model, metrics]) => {
            ['pm0h', 'pm1h', 'pm2h', '3h_block'].forEach(key => {
                const metric = metrics?.[key];
                if (!metric || Number(metric.sample_size || 0) <= 0) return;
                rows.push({
                    parameter: label,
                    model,
                    window: key === 'pm0h'
                        ? 'Strict hour'
                        : (key === 'pm1h'
                            ? 'Episode +/-1h'
                            : (key === 'pm2h'
                                ? 'Episode +/-2h'
                                : (metric.aggregation === 'sum' ? '3h accumulation' : '3h peak'))),
                    unit: metric.verification_unit || '-',
                    hss: metric.HSS,
                    csi: metric.CSI,
                    pod: metric.POD,
                    far: metric.FAR,
                    timing: metric.mean_abs_timing_error_h,
                    events: metric.observed_events,
                    hits: metric.hits,
                    falseAlarms: metric.false_alarms
                });
            });
        });
    };
    collect('Rainfall', 'Rain');
    collect('Wind Gust', 'Gust');

    if (!rows.length) {
        el.innerText = 'No event-window verification data yet.';
        return;
    }

    const windowOrder = {
        'Strict hour': 0,
        'Episode +/-1h': 1,
        'Episode +/-2h': 2,
        '3h accumulation': 3,
        '3h peak': 3
    };
    rows.sort((a, b) => {
        if (a.parameter !== b.parameter) return a.parameter.localeCompare(b.parameter);
        if (a.window !== b.window) return windowOrder[a.window] - windowOrder[b.window];
        return Number(b.hss || 0) - Number(a.hss || 0);
    });

    const fmt = value => (value === null || value === undefined || Number.isNaN(Number(value))) ? '-' : Number(value).toFixed(3);
    const fmtTiming = value => (value === null || value === undefined || Number.isNaN(Number(value))) ? '-' : `${Number(value).toFixed(2)} h`;
    el.innerHTML = `
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Param</th>
                        <th>Model</th>
                        <th>Window</th>
                        <th>Unit</th>
                        <th>HSS</th>
                        <th>CSI</th>
                        <th>POD</th>
                        <th>FAR</th>
                        <th>Timing Err</th>
                        <th>Obs Events</th>
                        <th>Hits</th>
                        <th>False Alarms</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map(row => `
                        <tr>
                            <td>${row.parameter}</td>
                            <td>${row.model}</td>
                            <td>${row.window}</td>
                            <td>${row.unit}</td>
                            <td>${fmt(row.hss)}</td>
                            <td>${fmt(row.csi)}</td>
                            <td>${fmt(row.pod)}</td>
                            <td>${fmt(row.far)}</td>
                            <td>${fmtTiming(row.timing)}</td>
                            <td>${Number(row.events || 0).toLocaleString()}</td>
                            <td>${Number(row.hits || 0).toLocaleString()}</td>
                            <td>${Number(row.falseAlarms || 0).toLocaleString()}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function setupPersistency(persData) {
    if(!persData || persData.length === 0) {
        document.getElementById('persistency-temp-chart').innerText = 'No historical observation data found.';
        document.getElementById('persistency-rain-chart').innerText = 'No historical observation data found.';
        document.getElementById('persistency-pressure-chart').innerText = 'No historical observation data found.';
        return;
    }
    
    const times = persData.map(d => new Date(d.Datetime.replace(' ', 'T') + 'Z').getTime());
    const temp = persData.map(d => d.temperature);
    const dew = persData.map(d => d.dewpoint);
    const rain = persData.map(d => d.rain_1h);
    const wind = persData.map(d => d.wind_speed);
    const pressure = persData.map(d => d.pressure);

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

    const pressureOptions = {
        series: [{ name: 'Pressure (hPa)', data: pressure }],
        chart: { type: 'line', height: 260, background: 'transparent', toolbar: { show: false } },
        stroke: { width: 2, curve: 'smooth' },
        colors: ['#8b5cf6'],
        dataLabels: { enabled: false },
        xaxis: { type: 'datetime', categories: times, labels: { style: { colors: '#94a3b8' } } },
        yaxis: { title: { text: 'Pressure (hPa)' }, labels: { style: { colors: '#8b5cf6' } } },
        theme: { mode: 'dark' }
    };
    document.getElementById('persistency-pressure-chart').innerHTML = '';
    new ApexCharts(document.getElementById('persistency-pressure-chart'), pressureOptions).render();
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
    const peakHour = TitanResearch.peak;
    const meanValid = TitanResearch.mean;
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
        colors: ['#ef4444', '#3b82f6', '#a855f7'],
        yaxis: [{seriesName:'Temp C',title:{text:'Temperature / dewpoint (°C)'}},{seriesName:'Temp C',show:false},{seriesName:'RH %',opposite:true,min:0,max:100,title:{text:'RH (%)'}}]
    });

    renderChart('#clim-rain-chart', {
        ...baseAxis,
        series: [
            { name: 'Rain Frequency %', type: 'column', data: rainFreq },
            { name: 'Wet-Hour Intensity mm', type: 'line', data: rainIntensity }
        ],
        chart: { ...baseAxis.chart, height: 320 },
        stroke: { width: [0, 3], curve: 'smooth' },
        colors: ['#0ea5e9', '#f59e0b'],
        yaxis: [{seriesName:'Rain Frequency %',min:0,max:100,title:{text:'Frequency (%)'}},{seriesName:'Wet-Hour Intensity mm',opposite:true,min:0,title:{text:'Intensity (mm)'}}]
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
        colors: ['#10b981', '#f59e0b', '#ef4444'],
        yaxis: [{seriesName:'Wind Speed kt',min:0,title:{text:'Wind (kt)'}},{seriesName:'Gust Event %',opposite:true,min:0,max:100,title:{text:'Frequency (%)'}},{seriesName:'Wind Speed kt',show:false,min:0}]
    });

    renderChart('#clim-fog-chart', {
        ...baseAxis,
        series: [
            { name: 'Proxy Score', type: 'line', data: fogScore },
            { name: 'High-Risk Frequency %', type: 'column', data: fogFreq }
        ],
        chart: { ...baseAxis.chart, height: 320 },
        stroke: { width: [3, 0], curve: 'smooth' },
        colors: ['#a855f7', '#64748b'],
        yaxis:[{seriesName:'Proxy Score',title:{text:'Proxy score (unitless)'}},{seriesName:'High-Risk Frequency %',opposite:true,min:0,max:100,title:{text:'Frequency (%)'}}]
    });

    renderDirectionTable(clim);

    const rainMatrix = clim.monthly_hourly_matrices?.rain_1h?.matrix;
    if (rainMatrix) {
        const monthLabels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const series = rainMatrix.map((row, idx) => ({
            name: monthLabels[idx],
            data: row.map((v, h) => ({ x: `${String(h).padStart(2,'0')}`, y: v === null || v === undefined ? null : Number(Number(v).toFixed(2)) }))
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
                            { from: 0, to: 0.05, color: '#111827', name: '0–0.05 mm' },
                            { from: 0.06, to: 0.5, color: '#0ea5e9', name: '0.06–0.5 mm' },
                            { from: 0.51, to: 1.5, color: '#22c55e', name: '0.51–1.5 mm' },
                            { from: 1.51, to: 3, color: '#facc15', name: '1.51–3 mm' },
                            { from: 3.01, to: 7, color: '#f97316', name: '3.01–7 mm' },
                            { from: 7.01, to: 999, color: '#ef4444', name: '>7 mm' }
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

function setupSelectionAndObservationQuality(selection, quality) {
    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.innerText = value;
    };
    const fmtRatio = (matched, total) => {
        const denominator = Number(total || 0);
        if (!denominator) return 'No overlap';
        return `${Number(matched || 0).toLocaleString()} / ${denominator.toLocaleString()}`;
    };
    const mode = selection.mode || 'observe_only';
    setText('selection-mode', selection.applied ? 'Enabled' : mode.replace('_', ' '));
    setText('selection-model-count', Number(selection.selected_model_count || 0).toLocaleString());
    setText('selection-changed-count', Number(selection.selection_changed_model_count || 0).toLocaleString());
    setText('awos-quality-status', quality.status || 'unknown');
    setText('awos-missing-hours', Number(quality?.hourly?.missing_hour_count || 0).toLocaleString());
    setText('awos-gust-match', fmtRatio(
        quality?.cross_grain?.gust_match_hours,
        quality?.cross_grain?.gust_comparable_hours
    ));
    setText('awos-rain-match', fmtRatio(
        quality?.cross_grain?.rain_boundary_match_hours,
        quality?.cross_grain?.rain_boundary_comparable_hours
    ));
    const status = document.getElementById('awos-quality-status');
    if (status) {
        status.style.color = quality.status === 'ok'
            ? 'var(--green)' : (quality.status === 'blocked' ? 'var(--crimson)' : 'var(--amber)');
    }
    const note = document.getElementById('selection-quality-note');
    if (note) {
        const changed = (selection.selection_changed_models || []).join(', ');
        const selectionText = selection.applied
            ? 'Deterministic clean-cycle selection is enabled.'
            : 'Deterministic clean-cycle selection is observe-only and does not alter TAF guidance.';
        const changedText = changed ? ` Candidate differs for: ${changed}.` : '';
        const rainText = ' One-minute rain is treated as a rolling one-hour snapshot and is never summed.';
        note.innerText = `${selectionText}${changedText}${rainText}`;
    }
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
            const residualText = prov.operational_residual_mode === 'observe_only'
                ? ` ${Number(prov.operational_residual_available || 0).toLocaleString()} values have an operational residual available, but residual QM is observe-only and not applied.`
                : '';
            note.innerText = `${Number(prov.lead_aware_pending || 0).toLocaleString()} values are using historical prior while lead-aware residual QM is still pending.${residualText}${artifactText} Rain amount correction remains strict/pending; occurrence risk is handled separately.`;
        }
    }
}

function setupOperationalResiduals(payload) {
    const metadata = payload?.metadata || {};
    const parameters = payload?.parameters || {};
    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.innerText = value;
    };
    const fmtInt = value => Number(value || 0).toLocaleString();
    const fmtSkill = value => (value === null || value === undefined || Number.isNaN(Number(value))) ? '-' : Number(value).toFixed(3);

    setText('residual-mode', (metadata.mode || 'observe_only').replace('_', ' '));
    setText('residual-total-pairs', fmtInt(metadata.total_pairs));
    setText('residual-ready-rows', fmtInt(metadata.ready_rows));
    setText('residual-enabled-rows', fmtInt(metadata.enabled_rows));

    const note = document.getElementById('residual-note');
    if (note) {
        const generated = metadata.generated_at ? ` Generated ${metadata.generated_at} UTC.` : '';
        note.innerText = `${metadata.note || 'Operational residuals are observe-only and do not change live forecasts yet.'}${generated}`;
    }

    const tbody = document.querySelector('#residual-summary-table tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    const names = ['Temperature', 'Dewpoint', 'Pressure', 'Rainfall', 'Wind Speed', 'Wind Dir.', 'Wind Gust'];
    names.forEach(name => {
        const row = parameters[name] || {};
        const status = row.status || 'pending';
        const color = status === 'ready_observe_only'
            ? 'var(--green)'
            : (status === 'disabled_observe_only' ? 'var(--amber)' : 'var(--text-secondary)');
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${name}</td>
            <td>${fmtInt(row.total_pairs)}</td>
            <td>${fmtInt(row.ready_rows)}</td>
            <td>${fmtInt(row.pending_rows)}</td>
            <td>${fmtInt(row.disabled_rows)}</td>
            <td>${fmtSkill(row.best_skill_score)}</td>
            <td style="color:${color}; font-weight:700;">${status.replaceAll('_', ' ')}</td>
        `;
        tbody.appendChild(tr);
    });
}

function setupIndividualModels(modelsData, timeLabels) {
    const modelSelect = document.getElementById('model-select');
    const tbody = document.querySelector('#model-table tbody');
    if(!modelSelect || !tbody) return;
    const keys=['Temperature','Dewpoint','Pressure','Wind Dir.','Wind Speed','Wind Gust','Rainfall','Precip Probability','Prob Precip 1.0mm'];
    const present=[...new Set(keys.flatMap(k=>Object.keys(modelsData[k]||{})))];
    const names={ECMWF_HRES:'ECMWF IFS 0.25°',ECMWF_IFS_9KM:'ECMWF IFS HRES 9 km (independent)',GFS_GLOBAL:'NOAA GFS Global',ICON_SEAMLESS:'DWD ICON Seamless',GEM_GLOBAL:'ECCC GEM Global',CMA_GRAPES_GLOBAL:'CMA GRAPES Global',JMA_GSM:'JMA GSM',METEOFRANCE_ARPEGE_WORLD:'Météo-France ARPEGE World',UKMO_GLOBAL_10KM:'UKMO Global 10 km'};
    modelSelect.replaceChildren();
    for(const key of present){const opt=document.createElement('option');opt.value=key;opt.textContent=names[key]||key;modelSelect.append(opt);}
    modelSelect.disabled=!present.length;
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
            const output = meta.expected_output_interval_hours;
            const update = meta.provider_update_frequency_hours;
            const provider = meta.provider || 'Unknown provider';
            const note = meta.hourly_output_note || 'Open-Meteo hourly output grid; provider update frequency may differ.';
            cadenceBadge.innerText = `${provider} | Output grid: ${output==null?'Not reported':output+' h'} | Provider update: ${update==null?'Not reported':update+' h'} | ${meta.hourly_output_note || 'Native time step and update frequency may differ; inspect provider metadata.'}`;
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
            
            if([temp, dew, pres, windDir, windSpd, gust, rain, probRain].some(v=>v!==undefined && v!==null)) {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${titanPreviewLabel(previewValidEpoch(dt))}</td>
                    <td style="color: #ef4444">${displayNumber(temp)}</td>
                    <td style="color: #3b82f6">${displayNumber(dew)}</td>
                    <td style="color: #a855f7">${displayNumber(pres)}</td>
                    <td>${displayNumber(windDir, 0, '°')}</td>
                    <td style="color: #10b981">${displayNumber(windSpd)}</td>
                    <td style="color: #f59e0b">${displayNumber(gust)}</td>
                    <td style="color: #6366f1">${displayNumber(rain)}</td>
                    <td>${displayNumber(probRain, 1, '%')}</td>
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

// Remove legacy browser-stored credentials even though the upload form is gone.
document.addEventListener('DOMContentLoaded', () => {
    try { localStorage.removeItem('gh_wawp_pat'); } catch (_) {}
});
