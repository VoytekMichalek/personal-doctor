const chart = document.querySelector('#steps-chart');
const svgNode = (name, attrs = {}) => {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
};
async function drawChart() {
  try {
    const response = await fetch(`/api/trends?days=${chart.dataset.days}`, {redirect: 'error'});
    if (!response.ok) throw new Error();
    const rows = await response.json();
    const valid = rows.filter(row => typeof row.steps === 'number');
    if (!valid.length) {
      chart.textContent = 'Your movement history will appear as records are imported.';
      chart.classList.add('empty'); return;
    }
    const width = 700, height = 235, left = 48, top = 20, bottom = 34;
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Daily step counts'});
    const peak = Math.max(5000, Math.ceil(Math.max(...valid.map(r => r.steps)) / 5000) * 5000);
    const first = new Date(rows[0].day + 'T00:00:00Z').getTime();
    const last = new Date(rows.at(-1).day + 'T00:00:00Z').getTime();
    const count = Math.max(1, Math.round((last - first) / 86400000) + 1);
    const barWidth = (width - left - 12) / count;
    for (let i = 0; i <= 3; i++) {
      const y = top + (height - top - bottom) * i / 3;
      svg.append(svgNode('line', {x1: left, y1: y, x2: width, y2: y, class: 'grid-line'}));
      const label = svgNode('text', {x: left - 10, y: y + 4, 'text-anchor': 'end', class: 'chart-label'});
      label.textContent = `${Math.round(peak * (1 - i / 3) / 1000)}k`; svg.append(label);
    }
    for (const row of valid) {
      const position = Math.round((new Date(row.day+'T00:00:00Z').getTime() - first) / 86400000);
      const h = row.steps / peak * (height - top - bottom);
      const bar = svgNode('rect', {x: left + position * barWidth + 1, y: height - bottom - h,
        width: Math.max(.5, barWidth * .68), height: Math.max(1, h), rx: Math.min(3, barWidth/4), class: 'chart-bar'});
      const title = svgNode('title'); title.textContent = `${row.day}: ${row.steps.toLocaleString()} steps`;
      bar.append(title); svg.append(bar);
    }
    for (const [day,x,anchor] of [[rows[0].day,left,'start'],[rows.at(-1).day,width-10,'end']]) {
      const label = svgNode('text',{x,y:height-6,'text-anchor':anchor,class:'chart-label'});
      label.textContent = new Date(day+'T12:00:00').toLocaleDateString('en-AU',{day:'numeric',month:'short'}); svg.append(label);
    }
    chart.replaceChildren(svg);
  } catch { chart.textContent = 'The chart couldn’t load. Reload this page to try again.'; }
}
if (chart) drawChart();
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
        if (oldStatus !== job.status && !['queued','running'].includes(job.status)) {
          location.reload(); return;
        }
        card.querySelector('[data-status]').textContent = job.status;
        card.querySelector('[data-progress]').textContent = `${job.phase} · ${job.requests} checkpoints · ${job.records} records processed`;
        card.querySelector('[data-error]').textContent = job.error || '';
      }
    } catch {
      const button = document.querySelector('[data-reload]');
      if (button) button.textContent = 'Connection interrupted · Retry ⟳';
    } finally { pending = false; }
  }, 10000);
}
