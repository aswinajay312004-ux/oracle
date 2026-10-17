// Interactive table flowchart (SVG): used for the approval preview, the saved read-back, AI chat
// answers and the database relationship map. Layered left→right by parent→child links; master
// tables can sit in a top band whose links appear on hover/selection so the picture stays readable.
import { esc } from './util.js';

const trunc = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

export function renderGraph(el, model, opts = {}) {
  const W = opts.nodeW || 214, H = opts.nodeH || 76, GX = opts.gapX || 92, GY = opts.gapY || 22, PAD = 26;
  const MW = 184, MH = 62, MG = 12;
  const nodes = model.nodes; const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  if (!nodes.length) { el.innerHTML = '<p class="empty">No tables to show.</p>'; return { select() {} }; }
  const band = opts.masterBand ? nodes.filter(n => n.kind === 'master') : [];
  const bandIds = new Set(band.map(n => n.id));
  const tree = nodes.filter(n => !bandIds.has(n.id));
  const inner = model.edges.filter(e => byId[e.from] && byId[e.to] && !bandIds.has(e.from) && !bandIds.has(e.to));

  // rank = longest path from a root (cycle-safe)
  const rank = {};
  const r = (id, seen = new Set()) => {
    if (rank[id] != null) return rank[id];
    if (seen.has(id)) return 0; seen.add(id);
    const ps = inner.filter(e => e.to === id && e.from !== id).map(e => e.from);
    return (rank[id] = ps.length ? Math.max(...ps.map(p => r(p, seen))) + 1 : 0);
  };
  tree.forEach(n => r(n.id));
  const cols = {};
  tree.forEach(n => (cols[rank[n.id]] ||= []).push(n));
  const pos = {};
  // masters band
  const perRow = Math.max(1, Math.min(band.length, opts.bandPerRow || 6));
  const bandH = band.length ? 30 + Math.ceil(band.length / perRow) * (MH + MG) : 0;
  band.sort((a, b) => String(a.title).localeCompare(String(b.title))).forEach((n, i) => {
    pos[n.id] = { x: PAD + (i % perRow) * (MW + MG), y: PAD + 22 + Math.floor(i / perRow) * (MH + MG), w: MW, h: MH };
  });
  const top = PAD + bandH + (band.length ? 34 : 0);
  const ranks = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const tallest = Math.max(...ranks.map(k => cols[k].length));
  const treeH = tallest * H + (tallest - 1) * GY;
  ranks.forEach(k => {
    const list = cols[k];
    // order by the average position of parents (fewer crossings), then base first, then name
    list.sort((a, b) => {
      const avg = n => { const ps = inner.filter(e => e.to === n.id && pos[e.from]).map(e => pos[e.from].y); return ps.length ? ps.reduce((x, y) => x + y, 0) / ps.length : 0; };
      return avg(a) - avg(b) || (b.base ? 1 : 0) - (a.base ? 1 : 0) || String(a.title).localeCompare(String(b.title));
    });
    const total = list.length * H + (list.length - 1) * GY; const y0 = top + (treeH - total) / 2;
    list.forEach((n, i) => { pos[n.id] = { x: PAD + k * (W + GX), y: y0 + i * (H + GY), w: W, h: H }; });
  });
  const width = Math.max(PAD * 2 + ranks.length * W + (ranks.length - 1) * GX, band.length ? PAD * 2 + perRow * (MW + MG) : 0, opts.minWidth || 0);
  const height = top + treeH + PAD;

  const curve = (a, b) => {
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    if (x2 <= x1) { const yA = a.y + a.h, yB = b.y + b.h; return [`M${a.x + a.w / 2},${yA} C${a.x + a.w / 2},${yA + 40} ${b.x + b.w / 2},${yB + 40} ${b.x + b.w / 2},${yB}`, (x1 + x2) / 2, Math.max(yA, yB) + 30]; }
    const mx = (x1 + x2) / 2; return [`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, mx, (y1 + y2) / 2];
  };
  const down = (a, b) => `M${a.x + a.w / 2},${a.y + a.h} C${a.x + a.w / 2},${a.y + a.h + 50} ${b.x + b.w / 2},${b.y - 50} ${b.x + b.w / 2},${b.y}`;
  const edges = model.edges.filter(e => pos[e.from] && pos[e.to]).map(e => {
    const fromBand = bandIds.has(e.from) || bandIds.has(e.to);
    const cls = `gedge ${e.cls || 'link'} ${fromBand ? 'bandedge hide' : ''}`;
    if (fromBand) return `<g class="${cls}" data-a="${esc(e.from)}" data-b="${esc(e.to)}"><title>${esc(e.title || e.label || '')}</title><path d="${down(pos[e.from], pos[e.to])}" marker-end="url(#arr)"/></g>`;
    const [p, lx, ly] = curve(pos[e.from], pos[e.to]);
    return `<g class="${cls}" data-a="${esc(e.from)}" data-b="${esc(e.to)}"><title>${esc(e.title || e.label || '')}</title><path d="${p}" marker-end="url(#arr)"/>
      ${e.label && opts.edgeLabels !== false ? `<text x="${lx}" y="${ly - 6}" text-anchor="middle">${esc(trunc(e.label, 26))}</text>` : ''}</g>`;
  }).join('');

  const node = n => {
    const p = pos[n.id]; const small = bandIds.has(n.id);
    const classes = ['gnode', n.kind || 'detail', n.dashed ? 'dashed' : '', n.base ? 'base' : '', n.dim ? 'dim' : '', n.id === opts.selected ? 'sel' : ''].join(' ');
    const st = n.status || {};
    return `<g class="${classes}" tabindex="0" role="button" data-id="${esc(n.id)}" transform="translate(${p.x},${p.y})" aria-label="${esc(`${n.title} (${n.code || n.id}) ${n.line3 || ''} ${st.text || ''}`)}">
      <rect class="box" width="${p.w}" height="${p.h}" rx="10"/><rect class="band" width="5" height="${p.h}" rx="2.5"/>
      <text class="t1" x="16" y="${small ? 21 : 23}">${esc(trunc(n.title, small ? 22 : 25))}</text>
      <text class="t2" x="16" y="${small ? 36 : 38}">${esc(trunc(n.code || n.id, 28))}</text>
      ${small ? `<text class="t4 ${esc(st.cls || '')}" x="16" y="53">${esc(trunc(st.text || n.line3 || '', 26))}</text>` :
        `<text class="t3" x="16" y="${p.h - 22}">${esc(trunc(n.line3 || '', 32))}</text><text class="t4 ${esc(st.cls || '')}" x="16" y="${p.h - 8}">${esc(trunc(st.text || '', 32))}</text>`}
      ${opts.onExpand && !small ? `<g class="gplus" data-plus="${esc(n.id)}" transform="translate(${p.w - 17},17)" role="button" tabindex="0" aria-label="Show tables linked to ${esc(n.title)}"><title>Show linked tables</title><circle r="10"/><text text-anchor="middle" y="5">+</text></g>` : ''}</g>`;
  };

  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" style="width:100%;max-width:${width}px" role="img" aria-label="${esc(opts.label || 'Table relationships')}">
    <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#8fb1ab"/></marker></defs>
    ${edges}
    ${band.length ? `<text class="lane" x="${PAD}" y="${PAD + 10}">${esc(opts.bandTitle || 'MASTER DATA (point at a table to see its links)')}</text>` : ''}
    ${band.length && opts.treeTitle ? `<text class="lane" x="${PAD}" y="${top - 12}">${esc(opts.treeTitle)}</text>` : ''}
    ${nodes.map(node).join('')}</svg>`;
  const svg = el.querySelector('svg');
  const focus = id => svg.querySelectorAll('.bandedge').forEach(g => g.classList.toggle('hide', !(id && (g.dataset.a === id || g.dataset.b === id))));
  const select = id => {
    svg.querySelectorAll('.gnode').forEach(g => g.classList.toggle('sel', g.dataset.id === id)); focus(id);
  };
  svg.querySelectorAll('.gnode').forEach(g => {
    g.addEventListener('click', e => {
      if (e.target.closest('[data-plus]')) return;
      select(g.dataset.id); opts.onSelect?.(g.dataset.id);
    });
    g.addEventListener('mouseenter', () => focus(g.dataset.id));
    g.addEventListener('mouseleave', () => focus(svg.querySelector('.gnode.sel')?.dataset.id));
    g.addEventListener('focus', () => focus(g.dataset.id));
    g.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target === g) { e.preventDefault(); select(g.dataset.id); opts.onSelect?.(g.dataset.id); } });
  });
  svg.querySelectorAll('[data-plus]').forEach(b => {
    const go = e => { e.stopPropagation(); opts.onExpand?.(b.dataset.plus); };
    b.addEventListener('click', go);
    b.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); } });
  });
  if (opts.selected) select(opts.selected);
  // keep the starting table in view when the diagram is taller than its box
  const base = nodes.find(n => n.base) || nodes.find(n => n.id === opts.selected);
  if (base && pos[base.id] && el.scrollHeight > el.clientHeight) {
    const scale = svg.getBoundingClientRect().width / width || 1;
    el.scrollTop = Math.max(0, (pos[base.id].y + pos[base.id].h / 2) * scale - el.clientHeight / 2);
  }
  return { select };
}
