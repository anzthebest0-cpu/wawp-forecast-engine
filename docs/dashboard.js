async function loadDashboard() {
    try {
        const [intelRes, weightsRes, perfRes, guidanceRes] = await Promise.all([
            fetch('data/tafor_intel.json'),
            fetch('data/latest_weights.json'),
            fetch('data/latest_performance.json'),
            fetch('data/taf_guidance.json')
        ]);
        
        const intel = await intelRes.json();
        const weights = await weightsRes.json();
        const perf = await perfRes.json();
        const guidance = await guidanceRes.json();

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
