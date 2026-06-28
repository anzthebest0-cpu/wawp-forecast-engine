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
                <div class="label">Wind Direction</div>
                <div class="val">${bg.dir}°</div>
            </div>
            <div class="param-card">
                <div class="label">Wind Speed</div>
                <div class="val">${bg.spd} kt</div>
            </div>
            <div class="param-card">
                <div class="label">Visibility</div>
                <div class="val">${bg.vis} m</div>
            </div>
            <div class="param-card">
                <div class="label">Weather</div>
                <div class="val">${bg.wx || 'NIL'}</div>
            </div>
            <div class="param-card">
                <div class="label">Cloud</div>
                <div class="val">${bg.cloud}</div>
            </div>
        `;
        document.getElementById('bg-params').innerHTML = bgHtml;
        
        const warnings = intel.warnings;
        if(warnings && warnings.length > 0) {
            document.getElementById('taf-warnings').innerHTML = warnings.map(w => `<div style="color:var(--warning); margin-top:10px;">⚠ ${w}</div>`).join('');
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
        const times = data.map(d => d.Datetime.substring(11, 16));
        const temps = data.map(d => d.Temperature);
        const dews = data.map(d => d.Dewpoint);
        const winds = data.map(d => d.Wind);
        const rains = data.map(d => d.Rain);
        
        new ApexCharts(document.querySelector("#chart-temp"), {
            series: [{ name: 'Temperature', data: temps }, { name: 'Dewpoint', data: dews }],
            chart: { type: 'line', height: 250, toolbar: { show: false }, foreColor: '#94a3b8' },
            stroke: { curve: 'smooth', width: 2 },
            colors: ['#ef4444', '#3b82f6'],
            xaxis: { categories: times, tooltip: { enabled: false } },
            yaxis: { title: { text: '°C' } },
            grid: { borderColor: 'rgba(255,255,255,0.1)' },
            title: { text: 'Temperature & Dewpoint', style: { color: '#f8fafc' } }
        }).render();

        new ApexCharts(document.querySelector("#chart-wind"), {
            series: [{ name: 'Wind Speed', data: winds }],
            chart: { type: 'area', height: 200, toolbar: { show: false }, foreColor: '#94a3b8' },
            stroke: { curve: 'smooth', width: 2 },
            fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.7, opacityTo: 0.1 } },
            colors: ['#10b981'],
            xaxis: { categories: times, tooltip: { enabled: false } },
            yaxis: { title: { text: 'Knots' }, min: 0 },
            grid: { borderColor: 'rgba(255,255,255,0.1)' },
            title: { text: 'Wind', style: { color: '#f8fafc' } }
        }).render();

        new ApexCharts(document.querySelector("#chart-rain"), {
            series: [{ name: 'Rainfall', data: rains }],
            chart: { type: 'bar', height: 200, toolbar: { show: false }, foreColor: '#94a3b8' },
            plotOptions: { bar: { borderRadius: 2, columnWidth: '60%' } },
            colors: ['#0ea5e9'],
            xaxis: { categories: times, tooltip: { enabled: false } },
            yaxis: { title: { text: 'mm' }, min: 0 },
            grid: { borderColor: 'rgba(255,255,255,0.1)' },
            title: { text: 'Precipitation', style: { color: '#f8fafc' } }
        }).render();
        
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

// Tab Switching
document.querySelectorAll('.nav-links li').forEach(li => {
    li.addEventListener('click', () => {
        document.querySelectorAll('.nav-links li').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
        
        li.classList.add('active');
        document.getElementById(li.getAttribute('data-tab')).classList.add('active');
    });
});

loadDashboard();
