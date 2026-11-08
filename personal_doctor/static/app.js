const svgNode = (name, attrs = {}) => {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
};
const chart = document.querySelector('#trends-chart');
const metricConfig = {
  battery: {label: 'Peak Body Battery', color: '#56d7af', unit: '/ 100', max: 100},
  sleep: {label: 'Garmin sleep score', color: '#8aaeff', unit: '/ 100', max: 100},
  steps: {label: 'Daily steps', color: '#f3ba69', unit: 'steps', max: null},
  hrv: {label: 'Overnight HRV', color: '#c0a6ed', unit: 'ms', max: null},
};
let metric = 'battery';
let chartRows = [];
let chartRequest = 0;
const shortDate = day => new Date(`${day}T12:00:00`).toLocaleDateString('en-AU', {day:'numeric', month:'short'});
const formatNumber = value => new Intl.NumberFormat('en-AU', {maximumFractionDigits:0}).format(value);
function renderChart() {
  const config = metricConfig[metric];
  const valid = chartRows.filter(row => Number.isFinite(row[metric]));
  document.querySelector('[data-chart-label]').textContent = config.label;
  document.querySelector('[data-chart-average]').textContent = valid.length
    ? formatNumber(valid.reduce((sum, row) => sum + row[metric], 0) / valid.length) : '—';
  document.querySelector('[data-chart-unit]').textContent = `AVG ${config.unit.toUpperCase()}`;
  document.querySelector('.legend-dot').style.backgroundColor = config.color;
  document.querySelectorAll('[data-metric]').forEach(button => {
    const selected = button.dataset.metric === metric;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
    button.style.color = selected ? config.color : '';
  });
  chart.replaceChildren();
  if (!valid.length) {
    const empty = document.createElement('p'); empty.className = 'empty';
    empty.textContent = `No ${config.label.toLowerCase()} readings in this period. Try another period or import more history.`;
    chart.append(empty); return;
  }
  const width = 650, height = 220, left = 34, right = 16, top = 20, bottom = 34;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const peak = config.max || (metric === 'steps'
    ? Math.max(5000, Math.ceil(Math.max(...valid.map(r => r[metric])) / 5000) * 5000)
    : Math.max(50, Math.ceil(Math.max(...valid.map(r => r[metric])) / 25) * 25));
  const x = i => left + (i + .5) / chartRows.length * plotWidth;
  const y = value => top + plotHeight * (1 - Math.min(peak, Math.max(0, value)) / peak);
  const svg = svgNode('svg', {viewBox:`0 0 ${width} ${height}`, role:'img', 'aria-label':`${config.label}, ${chartRows.length} days. Average ${document.querySelector('[data-chart-average]').textContent} ${config.unit}. Focus or hover a point for its reading.`});
  const defs = svgNode('defs');
  const gradient = svgNode('linearGradient', {id:'chart-fill', x1:'0', y1:'0', x2:'0', y2:'1'});
  gradient.append(svgNode('stop', {offset:'0%', 'stop-color':config.color, 'stop-opacity':'.16'}), svgNode('stop', {offset:'100%', 'stop-color':config.color, 'stop-opacity':'0'}));
  defs.append(gradient); svg.append(defs);
  for (let i = 0; i <= 4; i++) {
    const level = peak * i / 4, py = y(level);
    svg.append(svgNode('line', {x1:left, y1:py, x2:width-right, y2:py, class:'grid-line'}));
    const label = svgNode('text', {x:left-9, y:py+3, 'text-anchor':'end', class:'chart-label'});
    label.textContent = metric === 'steps' ? `${Math.round(level/1000)}k` : String(Math.round(level)); svg.append(label);
  }
  if (metric === 'steps') {
    chartRows.forEach((row, i) => {
      if (!Number.isFinite(row[metric])) return;
      const barWidth = Math.max(1, plotWidth / chartRows.length * .48);
      svg.append(svgNode('rect', {x:x(i)-barWidth/2, y:y(row[metric]), width:barWidth, height:height-bottom-y(row[metric]), rx:Math.min(3,barWidth/3), fill:config.color, class:'chart-bar'}));
    });
  } else {
    let segment = [];
    const drawSegment = () => {
      if (!segment.length) return;
      const line = segment.map(([px, py], i) => `${i ? 'L' : 'M'}${px},${py}`).join(' ');
      const area = `${line} L${segment.at(-1)[0]},${height-bottom} L${segment[0][0]},${height-bottom} Z`;
      svg.append(svgNode('path', {d:area, fill:'url(#chart-fill)'}), svgNode('path', {d:line, stroke:config.color, class:'chart-line'}));
      segment = [];
    };
    chartRows.forEach((row, i) => { if (Number.isFinite(row[metric])) segment.push([x(i), y(row[metric])]); else drawSegment(); });
    drawSegment();
    if (chartRows.length <= 31) chartRows.forEach((row, i) => {
      if (Number.isFinite(row[metric])) svg.append(svgNode('circle', {cx:x(i), cy:y(row[metric]), r:4.5, fill:config.color, class:'chart-point'}));
    });
  }
  const labelStride = Math.max(1, Math.ceil(chartRows.length / 7));
  chartRows.forEach((row, i) => {
    if (i % labelStride === 0 || i === chartRows.length-1 && chartRows.length % labelStride > labelStride/2) {
      const label = svgNode('text', {x:x(i), y:height-9, 'text-anchor':'middle', class:'chart-label'});
      label.textContent = chartRows.length <= 7
        ? new Date(`${row.day}T12:00:00`).toLocaleDateString('en-AU',{weekday:'short'}).toUpperCase() : shortDate(row.day);
      svg.append(label);
    }
    const value = row[metric];
    const description = `${shortDate(row.day)} · ${Number.isFinite(value) ? `${formatNumber(value)} ${config.unit}` : 'No reading'}`;
    const hit = svgNode('rect', {x:x(i)-plotWidth/chartRows.length/2, y:top, width:plotWidth/chartRows.length, height:plotHeight, class:'chart-hit', tabindex:chartRows.length <= 31 || i % labelStride === 0 ? '0' : '-1', 'aria-label':description});
    const title = svgNode('title'); title.textContent = description; hit.append(title);
    const show = () => {
      chart.querySelector('.chart-tooltip')?.remove();
      const tooltip = document.createElement('div'); tooltip.className = 'chart-tooltip'; tooltip.textContent = description;
      tooltip.style.left = `${Math.max(19, Math.min(81, x(i)/width*100))}%`; chart.append(tooltip);
    };
    hit.addEventListener('mouseenter', show); hit.addEventListener('focus', show);
    hit.addEventListener('mouseleave', () => chart.querySelector('.chart-tooltip')?.remove());
    hit.addEventListener('blur', () => chart.querySelector('.chart-tooltip')?.remove());
    svg.append(hit);
  });
  chart.prepend(svg);
}
async function loadChart(days) {
  const request = ++chartRequest;
  chart.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(`/api/trends?days=${days}&on=${encodeURIComponent(chart.dataset.day)}`, {redirect:'error'});
    if (!response.ok) throw new Error();
    const rows = await response.json();
    if (request !== chartRequest) return;
    chartRows = rows;
    chart.dataset.days = days;
    document.querySelectorAll('[data-range]').forEach(link => {
      const selected = link.dataset.range === String(days);
      link.classList.toggle('selected', selected);
      if (selected) link.setAttribute('aria-current', 'true'); else link.removeAttribute('aria-current');
    });
    document.querySelector('[data-chart-period]').textContent = `${days}-day view · gaps indicate missing readings`;
    renderChart();
    const url = new URL(location.href); url.searchParams.set('days', days); history.replaceState(null, '', url);
    document.querySelectorAll('.week-day[href], .date-navigation a, .week-today').forEach(link => {
      const target = new URL(link.href); target.searchParams.set('days', days); link.href = target.href;
    });
  } catch {
    if (request !== chartRequest) return;
    chart.replaceChildren();
    const message = document.createElement('p'); message.className = 'empty'; message.textContent = 'The chart couldn’t load. ';
    const retry = document.createElement('button'); retry.className = 'text-button'; retry.textContent = 'Try again'; retry.addEventListener('click', () => loadChart(days));
    message.append(retry); chart.append(message);
  } finally { if (request === chartRequest) chart.removeAttribute('aria-busy'); }
}
if (chart) {
  loadChart(chart.dataset.days);
  document.querySelectorAll('[data-range]').forEach(link => link.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); loadChart(link.dataset.range);
  }));
  document.querySelectorAll('[data-metric]').forEach(button => button.addEventListener('click', () => {metric = button.dataset.metric; renderChart();}));
}
const clock = document.querySelector('[data-clock]');
if (clock) {
  const updateClock = () => {if (!document.hidden) clock.textContent = new Date().toLocaleTimeString('en-GB', {timeZone:document.body.dataset.timezone, hour12:false});};
  updateClock(); setInterval(updateClock, 1000);
}
const liveValue = document.querySelector('[data-live-value]');
if (document.body.dataset.demo === 'true' && liveValue) {
  const button = document.querySelector('[data-stream-toggle]');
  const card = document.querySelector('.live-card');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let paused = reducedMotion.matches, tick = 0;
  const setPaused = state => {
    paused = state; card.classList.toggle('stream-paused', paused);
    button.textContent = paused ? '▶' : 'Ⅱ';
    button.setAttribute('aria-pressed', String(paused));
    button.setAttribute('aria-label', `${paused ? 'Resume' : 'Pause'} simulated live readings`);
    document.querySelector('[data-live-status]').textContent = paused ? 'Simulation paused' : 'Updating every 3s';
  };
  setPaused(paused); button.addEventListener('click', () => setPaused(!paused));
  reducedMotion.addEventListener('change', event => {if (event.matches) setPaused(true);});
  setInterval(() => {
    if (paused || document.hidden) return;
    tick++; liveValue.textContent = String([64,65,64,63,62,64,66,65,64,63,65,64][tick % 12]);
  }, 3000);
}
document.querySelectorAll('[data-auto-submit]').forEach(input => input.addEventListener('change', () => input.form.requestSubmit()));
document.querySelectorAll('[data-reload]').forEach(button => button.addEventListener('click', () => location.reload()));
if (document.querySelector('[data-jobs]')) {
  let pending = false;
  setInterval(async () => {
    if (document.hidden || pending) return;
    pending = true;
    try {
      const response = await fetch('/api/sync', {redirect:'error'});
      if (!response.ok) throw new Error();
      for (const job of await response.json()) {
        const card = document.querySelector(`[data-job-id="${CSS.escape(job.id)}"]`);
        if (!card) continue;
        const oldStatus = card.querySelector('[data-status]').textContent;
        if (oldStatus !== job.status && !['queued','running'].includes(job.status)) {location.reload(); return;}
        card.querySelector('[data-status]').textContent = job.status;
        card.querySelector('[data-progress]').textContent = `${job.phase} · ${job.requests} checkpoints · ${job.records} records processed`;
        card.querySelector('[data-error]').textContent = job.error || '';
      }
    } catch {
      const button = document.querySelector('[data-reload]'); if (button) button.textContent = 'Connection interrupted · Retry ⟳';
    } finally { pending = false; }
  }, 10000);
}
