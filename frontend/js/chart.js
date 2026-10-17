// Answer charts drawn from the exact rows the read-only query returned (no extra query, no library, no CDN).
// One measure by one dimension: dates → line over time, anything else → ranked horizontal bars.
// Nothing is drawn when a chart would mislead: a single row, mixed signs, too many categories, or no measure.
import { esc, fmt, show } from './util.js';

const MAX_BARS = 20, MAX_POINTS = 60;

export function answerChart(cols, rows) {
  const m = cols.find(c => c.kind === 'measure'); const d = cols.find(c => c.kind === 'dimension');
  if (!m || !d || rows.length < 2) return '';
  const pts = rows.map(r => ({ x: r[d.index], y: r[m.index] })).filter(p => p.x != null && typeof p.y === 'number' && Number.isFinite(p.y));
  if (pts.length < 2 || pts.some(p => p.y < 0)) return '';
  const unit = m.unit && !String(m.label).includes(`(${m.unit})`) ? ` (${m.unit})` : '';
  const title = `${m.label}${unit} by ${d.label}`;
  const isDate = d.type === 'DATE' || d.role === 'date';
  const svg = isDate ? line(pts, m, d) : bars(pts, m);
  if (!svg) return '';
  return `<figure class="answer-chart" aria-label="${esc(title)}"><figcaption>${esc(title)}${!isDate && pts.length > MAX_BARS ? ` <span class="muted">· top ${MAX_BARS} of ${pts.length}</span>` : ''}</figcaption>${svg}</figure>`;
}

function bars(pts, m) {
  const top = [...pts].sort((a, b) => b.y - a.y).slice(0, MAX_BARS);
  const max = Math.max(...top.map(p => p.y)) || 1;
  const labelW = 150, valueW = 70, barH = 16, gap = 8, W = 640, plotW = W - labelW - valueW;
  const H = top.length * (barH + gap) + gap;
  const rowsSvg = top.map((p, i) => {
    const y = gap + i * (barH + gap); const w = Math.max(2, (p.y / max) * plotW);
    const name = show(p.x); const short = name.length > 22 ? name.slice(0, 21) + '…' : name;
    return `<g class="mark"><title>${esc(name)}: ${esc(fmt(p.y))}</title>
      <rect class="hit" x="0" y="${y - gap / 2}" width="${W}" height="${barH + gap}"/>
      <text class="lbl" x="${labelW - 8}" y="${y + barH / 2}" text-anchor="end" dominant-baseline="middle">${esc(short)}</text>
      <path class="bar" d="M${labelW} ${y}h${w - 4}a4 4 0 0 1 4 4v${barH - 8}a4 4 0 0 1 -4 4h${-(w - 4)}z"/>
      <text class="val" x="${labelW + w + 6}" y="${y + barH / 2}" dominant-baseline="middle">${esc(fmt(p.y))}</text></g>`;
  }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" preserveAspectRatio="xMinYMin meet"><line class="axis" x1="${labelW}" y1="0" x2="${labelW}" y2="${H}"/>${rowsSvg}</svg>`;
}

function line(pts, m, d) {
  const sorted = [...pts].sort((a, b) => String(a.x).localeCompare(String(b.x))).slice(-MAX_POINTS);
  if (sorted.length < 2) return '';
  const W = 640, H = 220, L = 56, R = 16, T = 14, B = 34;
  const max = Math.max(...sorted.map(p => p.y)) || 1;
  const x = i => L + (i / (sorted.length - 1)) * (W - L - R); const y = v => T + (1 - v / max) * (H - T - B);
  const ticks = [0, max / 2, max];
  const path = sorted.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p.y).toFixed(1)}`).join('');
  const every = Math.ceil(sorted.length / 6);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img">
    ${ticks.map(t => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text class="tick" x="${L - 6}" y="${y(t)}" text-anchor="end" dominant-baseline="middle">${esc(fmt(t, 0))}</text>`).join('')}
    <path class="line" d="${path}"/>
    ${sorted.map((p, i) => `<g class="mark"><title>${esc(show(p.x))}: ${esc(fmt(p.y))}</title>
      <rect class="hit" x="${x(i) - 12}" y="${T}" width="24" height="${H - T - B}"/><circle class="dot" cx="${x(i)}" cy="${y(p.y)}" r="4"/>
      ${i % every === 0 || i === sorted.length - 1 ? `<text class="tick" x="${x(i)}" y="${H - B + 16}" text-anchor="middle">${esc(String(show(p.x)).slice(0, 10))}</text>` : ''}</g>`).join('')}
  </svg>`;
}
