/**
 * ABSOLUTE TITAN — ApexCharts Theme Configuration
 * Drop-in chart options for the Consensus Meteogram tab.
 * Requires: ApexCharts >= 3.x
 * Usage: import and spread into your chart options objects.
 *
 * Muhammad Subhan Al Zibrah · BMKG Stamet SNB Kolaka (WAWP)
 */

// ── SHARED PALETTE ──────────────────────────────────────────
const TITAN_COLORS = {
  cyan:    '#00d4ff',
  cyanDim: '#00a3c4',
  amber:   '#f5a623',
  crimson: '#ff3b5c',
  green:   '#00e676',
  tempRed: '#ff6b6b',
  dewBlue: '#74b9ff',
  bg:      '#0a0e1a',
  surface: '#0f1525',
  border:  'rgba(255,255,255,0.06)',
  text:    '#8a9ab8',
  font:    'JetBrains Mono, monospace',
};

// ── BASE THEME (shared by all charts) ───────────────────────
const TITAN_BASE = {
  chart: {
    background:  TITAN_COLORS.bg,
    foreColor:   TITAN_COLORS.text,
    fontFamily:  TITAN_COLORS.font,
    fontSize:    '11px',
    toolbar: { show: false },
    zoom:    { enabled: false },
    animations: {
      enabled: true,
      easing:  'easeinout',
      speed:   700,
    },
  },

  theme: { mode: 'dark' },

  grid: {
    borderColor:  'rgba(255,255,255,0.05)',
    strokeDashArray: 4,
    xaxis: { lines: { show: false } },  // Remove vertical gridlines
    yaxis: { lines: { show: true  } },  // Keep soft horizontal rules only
    padding: { top: 0, right: 16, bottom: 0, left: 8 },
  },

  xaxis: {
    type: 'datetime',
    labels: {
      format:     'HH:mm',
      style: {
        colors:     TITAN_COLORS.text,
        fontSize:   '10px',
        fontFamily: TITAN_COLORS.font,
      },
      datetimeUTC: true,
    },
    axisBorder:  { show: false },
    axisTicks:   { show: false },
    crosshairs: {
      stroke: {
        color:  TITAN_COLORS.cyan,
        width:  1,
        dashArray: 3,
      },
      fill: { type: 'solid', color: 'rgba(0,212,255,0.06)' },
    },
  },

  tooltip: {
    theme:       'dark',
    shared:       true,
    intersect:    false,
    style: {
      fontSize:   '11px',
      fontFamily: TITAN_COLORS.font,
    },
    x: { format: 'dd MMM HH:mm UTC' },
  },

  legend: {
    position:     'top',
    horizontalAlign: 'right',
    fontSize:     '11px',
    fontFamily:   TITAN_COLORS.font,
    labels:   { colors: TITAN_COLORS.text },
    markers: { size: 6, shape: 'circle' },
    itemMargin: { horizontal: 12 },
  },

  markers: {
    size:         0,             // Hidden at rest
    hover: {
      size:       6,
      sizeOffset: 2,
    },
  },

  stroke: {
    curve:  'smooth',
    lineCap: 'round',
  },

  noData: {
    text:         'Loading forecast…',
    align:        'center',
    verticalAlign: 'middle',
    style: {
      color:      TITAN_COLORS.text,
      fontSize:   '13px',
      fontFamily: TITAN_COLORS.font,
    },
  },
};

// ── CHART 1: TEMPERATURE + DEWPOINT ─────────────────────────
const CHART_TEMP_OPTIONS = {
  ...TITAN_BASE,
  chart: {
    ...TITAN_BASE.chart,
    id:     'titan-temp',
    type:   'line',
    height: 220,
    group:  'meteogram',    // Synchronized x-axis with other charts
  },

  title: {
    text:   'TEMPERATURE / DEWPOINT',
    align:  'left',
    style: {
      fontSize:   '10px',
      fontWeight: '600',
      fontFamily: TITAN_COLORS.font,
      color:      'rgba(138,154,184,0.7)',
      letterSpacing: '0.1em',
    },
    offsetY: 4,
  },

  series: [
    { name: 'Temp (°C)',  data: [] },
    { name: 'Dewpt (°C)', data: [] },
  ],

  colors: [TITAN_COLORS.tempRed, TITAN_COLORS.dewBlue],

  stroke: {
    curve:  'smooth',
    width:  [2.5, 2],
    dashArray: [0, 4],         // Solid temp, dashed dewpoint
  },

  // Glow effect via SVG filter on the line
  // (ApexCharts supports filter via custom dropShadow)
  dropShadow: {
    enabled: true,
    enabledOnSeries: [0],      // Glow only on temp line
    top:    0,
    left:   0,
    blur:   8,
    color:  TITAN_COLORS.tempRed,
    opacity: 0.4,
  },

  yaxis: {
    labels: {
      style: {
        colors:     TITAN_COLORS.text,
        fontSize:   '10px',
        fontFamily: TITAN_COLORS.font,
      },
      formatter: (v) => `${Math.round(v)}°`,
    },
    tickAmount: 4,
  },

  fill: { type: 'solid', opacity: 1 },

  annotations: {
    yaxis: [{
      y:           0,
      borderColor: 'rgba(255,255,255,0.08)',
      borderWidth: 1,
      strokeDashArray: 2,
    }],
  },
};

// ── CHART 2: WIND SPEED ──────────────────────────────────────
const CHART_WIND_OPTIONS = {
  ...TITAN_BASE,
  chart: {
    ...TITAN_BASE.chart,
    id:     'titan-wind',
    type:   'area',
    height: 200,
    group:  'meteogram',
  },

  title: {
    text:   'WIND SPEED',
    align:  'left',
    style: {
      fontSize:   '10px',
      fontWeight: '600',
      fontFamily: TITAN_COLORS.font,
      color:      'rgba(138,154,184,0.7)',
      letterSpacing: '0.1em',
    },
    offsetY: 4,
  },

  series: [
    { name: 'Wind (kt)',  data: [] },
    { name: 'Gust (kt)',  data: [] },
  ],

  colors: [TITAN_COLORS.cyan, TITAN_COLORS.amber],

  stroke: {
    curve:  'smooth',
    width:  [2, 1.5],
    dashArray: [0, 3],
  },

  dropShadow: {
    enabled: true,
    enabledOnSeries: [0],
    top:    0,
    left:   0,
    blur:   10,
    color:  TITAN_COLORS.cyan,
    opacity: 0.5,
  },

  fill: {
    type:    ['gradient', 'solid'],
    opacity: [1, 0],
    gradient: {
      shade:          'dark',
      type:           'vertical',
      shadeIntensity:  0.0,
      opacityFrom:     0.22,
      opacityTo:       0.0,
      stops:           [0, 100],
      colorStops: [[
        { offset: 0,   color: TITAN_COLORS.cyan,    opacity: 0.22 },
        { offset: 100, color: TITAN_COLORS.cyanDim, opacity: 0    },
      ]],
    },
  },

  yaxis: {
    labels: {
      style: {
        colors:     TITAN_COLORS.text,
        fontSize:   '10px',
        fontFamily: TITAN_COLORS.font,
      },
      formatter: (v) => `${Math.round(v)}kt`,
    },
    tickAmount: 4,
    min: 0,
  },
};

// ── CHART 3: PRECIPITATION ───────────────────────────────────
const CHART_RAIN_OPTIONS = {
  ...TITAN_BASE,
  chart: {
    ...TITAN_BASE.chart,
    id:     'titan-rain',
    type:   'bar',
    height: 180,
    group:  'meteogram',
  },

  title: {
    text:   'PRECIPITATION',
    align:  'left',
    style: {
      fontSize:   '10px',
      fontWeight: '600',
      fontFamily: TITAN_COLORS.font,
      color:      'rgba(138,154,184,0.7)',
      letterSpacing: '0.1em',
    },
    offsetY: 4,
  },

  series: [{ name: 'Rain (mm)', data: [] }],

  // Cyan-to-blue gradient per bar
  fill: {
    type:    'gradient',
    gradient: {
      shade:          'dark',
      type:           'vertical',
      opacityFrom:     0.9,
      opacityTo:       0.5,
      colorStops: [[
        { offset: 0,   color: TITAN_COLORS.cyan,    opacity: 0.9 },
        { offset: 100, color: TITAN_COLORS.cyanDim, opacity: 0.5 },
      ]],
    },
  },

  plotOptions: {
    bar: {
      columnWidth:  '55%',
      borderRadius:  3,
      dataLabels:   { position: 'top' },
    },
  },

  dataLabels: {
    enabled: true,
    offsetY: -14,
    style: {
      fontSize:   '9px',
      fontFamily: TITAN_COLORS.font,
      colors:     [TITAN_COLORS.text],
    },
    formatter: (v) => v > 0 ? `${v.toFixed(1)}` : '',
  },

  yaxis: {
    labels: {
      style: {
        colors:     TITAN_COLORS.text,
        fontSize:   '10px',
        fontFamily: TITAN_COLORS.font,
      },
      formatter: (v) => `${v.toFixed(1)}mm`,
    },
    tickAmount: 3,
    min: 0,
  },

  colors: [TITAN_COLORS.cyan],
};

// ── CHART INIT HELPER ────────────────────────────────────────
/**
 * Initialize all three meteogram charts.
 * Call this after your data is ready.
 *
 * @param {object} data  - Object with arrays: tempData, dewData,
 *                         windData, gustData, rainData (each an
 *                         array of [timestamp_ms, value] pairs)
 */
function initTitanCharts(data) {
  // Temp / Dew
  const tempSeries = [
    { name: 'Temp (°C)',  data: data.tempData  || [] },
    { name: 'Dewpt (°C)', data: data.dewData   || [] },
  ];
  const chartTemp = new ApexCharts(
    document.querySelector('#chart-temp'),
    { ...CHART_TEMP_OPTIONS, series: tempSeries }
  );
  chartTemp.render();

  // Wind
  const windSeries = [
    { name: 'Wind (kt)', data: data.windData || [] },
    { name: 'Gust (kt)', data: data.gustData || [] },
  ];
  const chartWind = new ApexCharts(
    document.querySelector('#chart-wind'),
    { ...CHART_WIND_OPTIONS, series: windSeries }
  );
  chartWind.render();

  // Rain
  const rainSeries = [{ name: 'Rain (mm)', data: data.rainData || [] }];
  const chartRain = new ApexCharts(
    document.querySelector('#chart-rain'),
    { ...CHART_RAIN_OPTIONS, series: rainSeries }
  );
  chartRain.render();

  return { chartTemp, chartWind, chartRain };
}
