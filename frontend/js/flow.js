// Interactive flowchart of the Oracle tables an approved import touched (from a live read-back).
// Layout: master tables as a band on top; order tables as a left-to-right tree below.
// Master links stay hidden until a table is hovered or selected, so the picture stays readable.
import { esc, plural, drawer, record } from './util.js';

const W = 236, H = 92, GX = 116, GY = 28, PAD = 28;
const MW = 196, MH = 66, MG = 14;

export function renderFlow(el, graph) {
  if (!graph.nodes.length) { el.innerHTML = '<p class="empty">No rows were found for this import.</p>'; return; }
  const byTable = Object.fromEntries(graph.nodes.map(n => [n.table, n]));
  const masters = graph.nodes.filter(n => n.kind === 'master').sort((a, b) => a.table.localeCompare(b.table));
  const orderNodes = graph.nodes.filter(n => n.kind !== 'master');
  const inner = graph.edges.filter(e => byTable[e.source]?.kind !== 'master' && byTable[e.target]?.kind !== 'master');

  // Depth = longest path from the header; the tree parent is the deepest parent (e.g. ORDSIZE → ORDSTYLE).
  const depth = {};
  const d = (t, seen = new Set()) => {
    if (depth[t] != null) return depth[t];
    if (seen.has(t)) return 0; seen.add(t);
    const ps = inner.filter(e => e.target === t).map(e => e.source);
    return (depth[t] = ps.length ? Math.max(...ps.map(p => d(p, seen))) + 1 : 0);
  };
  orderNodes.forEach(n => d(n.table));
  const parentOf = t => inner.filter(e => e.target === t).sort((a, b) => depth[b.source] - depth[a.source])[0];
  const tree = orderNodes.map(n => parentOf(n.table)).filter(Boolean);

  // Masters band
  const perRow = 6;
  const bandRows = Math.ceil(masters.length / perRow);
  const bandH = masters.length ? 34 + bandRows * (MH + MG) : 0;
  const pos = {};
  masters.forEach((n, i) => { pos[n.table] = { x: PAD + (i % perRow) * (MW + MG), y: PAD + 26 + Math.floor(i / perRow) * (MH + MG), w: MW, h: MH }; });

  // Order tree
  const cols = {};
  orderNodes.forEach(n => (cols[depth[n.table]] ||= []).push(n));
  Object.values(cols).forEach(list => list.sort((a, b) => a.table.localeCompare(b.table)));
  const tallest = Math.max(...Object.values(cols).map(l => l.length));
  const treeTop = PAD + bandH + 40;
  const treeH = tallest * H + (tallest - 1) * GY;
  Object.entries(cols).forEach(([c, list]) => {
    const total = list.length * H + (list.length - 1) * GY; const y0 = treeTop + (treeH - total) / 2;
    list.forEach((n, i) => { pos[n.table] = { x: PAD + Number(c) * (W + GX), y: y0 + i * (H + GY), w: W, h: H }; });
  });
  const ncols = Object.keys(cols).length;
  const width = Math.max(PAD * 2 + ncols * W + (ncols - 1) * GX, PAD * 2 + Math.min(masters.length, perRow) * (MW + MG));
  const height = treeTop + treeH + PAD;

  const path = (a, b) => {
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2, mx = (x1 + x2) / 2;
    return [`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, mx, (y1 + y2) / 2];
  };
  const masterPath = (a, b) => {
    const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y;
    return `M${x1},${y1} C${x1},${y1 + 60} ${x2},${y2 - 60} ${x2},${y2}`;
  };
  const treeEdges = tree.map(e => { const [p, lx, ly] = path(pos[e.source], pos[e.target]);
    return `<g class="tedge" data-a="${esc(e.source)}" data-b="${esc(e.target)}"><title>${esc(e.label)} (${esc(e.on)})</title><path class="edge strong" d="${p}" marker-end="url(#arrow)"/>
      <text class="edge-label" x="${lx}" y="${ly - 8}" text-anchor="middle">${esc(e.on)}</text></g>`; }).join('');
  const masterEdges = graph.edges.filter(e => byTable[e.source]?.kind === 'master' && pos[e.target]).map(e =>
    `<g class="medge" data-a="${esc(e.source)}" data-b="${esc(e.target)}"><title>${esc(e.target)}.${esc(e.on)} uses ${esc(e.source)}</title><path class="edge master-edge" d="${masterPath(pos[e.source], pos[e.target])}" marker-end="url(#arrow)"/></g>`).join('');

  const node = n => {
    const p = pos[n.table]; const m = n.kind === 'master';
    const counts = [n.inserted ? `${n.inserted} new` : '', n.reused ? `${n.reused} existing` : ''].filter(Boolean).join(' · ');
    const name = m ? n.business.replace(/ master$/, '') : n.business;
    return `<g class="node ${n.kind}" tabindex="0" role="button" data-table="${esc(n.table)}" transform="translate(${p.x},${p.y})" aria-label="${esc(n.table)} ${esc(name)}: ${esc(counts)}">
      <rect class="box" width="${p.w}" height="${p.h}" rx="12"/><rect class="band" width="6" height="${p.h}" rx="3"/>
      <text class="t1" x="18" y="${m ? 24 : 26}">${esc(n.table)}</text>
      <text class="t2" x="18" y="${m ? 41 : 45}">${esc(name.length > (m ? 26 : 34) ? name.slice(0, m ? 25 : 33) + '…' : name)}</text>
      ${m ? `<text class="t3 ${n.verified ? '' : 'bad'}" x="18" y="58">${n.verified ? '✓' : '⚠'} ${esc(counts)}</text>` :
        `<text class="t2" x="18" y="66">${esc(counts)}</text><text class="t3 ${n.verified ? '' : 'bad'}" x="18" y="82">${n.verified ? '✓ matches Oracle' : '⚠ check differences'}</text>`}</g>`;
  };

  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Affected Oracle tables">
    <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#7fa29e"/></marker></defs>
    ${masters.length ? `<text class="lane" x="${PAD}" y="${PAD + 12}">MASTER DATA USED (hover a table to see its links)</text>` : ''}
    <text class="lane" x="${PAD}" y="${treeTop - 14}">ORDER RECORDS · linked by ORDMASID and line numbers (SUBNO)</text>
    ${masterEdges}${treeEdges}${masters.map(node).join('')}${orderNodes.map(node).join('')}</svg>`;
  const svg = el.querySelector('svg');
  const focus = table => svg.querySelectorAll('.medge').forEach(g => g.classList.toggle('show', !!table && (g.dataset.a === table || g.dataset.b === table)));

  const open = table => {
    svg.querySelectorAll('.node').forEach(g => g.classList.toggle('sel', g.dataset.table === table)); focus(table);
    const n = byTable[table];
    const links = graph.edges.filter(e => e.source === table || e.target === table);
    drawer(`${n.table} · ${n.business}`, `<p class="muted">${plural(n.records.length, 'record')} read live from Oracle just now. Columns written by POPPYS AI are highlighted.</p>
      ${links.length ? `<p style="margin:8px 0;display:flex;flex-wrap:wrap;gap:6px">${links.map(e => `<span class="pill">${esc(e.source)} → ${esc(e.target)} · ${esc(e.on)}</span>`).join('')}</p>` : ''}
      <label class="check toggle-empty"><input type="checkbox" id="show-empty"> Show empty columns too</label>
      <div id="records">${records(n, true)}</div>`);
    document.getElementById('show-empty').onchange = e => { document.getElementById('records').innerHTML = records(n, !e.target.checked); };
  };
  svg.querySelectorAll('.node').forEach(g => {
    g.addEventListener('click', () => open(g.dataset.table));
    g.addEventListener('mouseenter', () => focus(g.dataset.table));
    g.addEventListener('mouseleave', () => focus(svg.querySelector('.node.sel')?.dataset.table));
    g.addEventListener('focus', () => focus(g.dataset.table));
    g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(g.dataset.table); } });
  });
}

function records(n, hideEmpty) {
  return n.records.map(r => `<div class="record"><div class="record-head"><span><b>${esc(n.key)} = ${esc(r.pk)}</b> ${r.where ? `· ${esc(r.where)}` : ''}</span>
    <span>${r.kind === 'written' ? '<span class="pill brand">new record</span>' : '<span class="pill">existing master</span>'}
    ${r.found ? (r.matches ? '<span class="pill ok">matches</span>' : `<span class="pill err">differs: ${esc(r.mismatched.join(', '))}</span>`) : '<span class="pill err">not found in Oracle</span>'}</span></div>
    ${record(r.live, r.written_columns, { hideEmpty, decoded: r.decoded || {} })}</div>`).join('');
}
