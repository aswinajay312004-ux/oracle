// Database → All tables as a mind map (like NotebookLM's): GARMENT_DEMO on the left, split into Order tables, Names
// (master tables) and Other tables; each order table sits under the table it belongs to (Order → Order line → Size
// quantity …). Every node with branches has a ▸ / ◂ button to open or close them; clicking a table shows its rows.
// Lines never cross: each table has one place in the tree. Other links (a size row also belongs to the order, a
// colour is used by order lines) are listed in the node's tooltip.
import { esc, fmt } from './util.js';

const W = 208, H = 46, GAP_X = 50, GAP_Y = 12, PAD = 24, ROOT = '__root';
const LEVEL_W = [156, 186];                    // the root and the groups are narrower than a table
const widthAt = depth => LEVEL_W[depth] ?? W;
const xAt = depth => PAD + Array.from({ length: depth }, (_, i) => widthAt(i) + GAP_X).reduce((a, b) => a + b, 0);
const GROUPS = [['__orders', 'Order tables'], ['__masters', 'Master tables'], ['__other', 'Other tables']];

// ---------------------------------------------------------------- the tree (pure; unit-tested in Node)
// tables: [{table, business, kind, rows, approx}], joins: [{parent, child, status}]. Returns {nodes: {id: node}, root}.
export function buildTree(tables, joins, owner = 'GARMENT_DEMO') {
  const shown = tables.filter(t => t.kind !== 'local');
  const byId = new Map(shown.map(t => [t.table, t]));
  const links = joins.filter(j => j.status !== 'rejected' && j.parent !== j.child && byId.has(j.parent) && byId.has(j.child));
  const masters = new Set(shown.filter(t => t.kind === 'master').map(t => t.table));
  const head = shown.find(t => t.kind === 'header')?.table || (byId.has('ORDMAS') ? 'ORDMAS' : null);
  // depth of each order table = longest chain of links from the order header (cycle-safe), so a size row sits under
  // its order line, not directly under the order
  const depth = new Map(head ? [[head, 0]] : []);
  for (let pass = 0; pass < shown.length; pass++) {
    let moved = false;
    for (const j of links) {
      if (masters.has(j.parent) || masters.has(j.child) || j.child === head || !depth.has(j.parent)) continue;
      const d = depth.get(j.parent) + 1;
      if (d < shown.length && d > (depth.get(j.child) ?? -1)) { depth.set(j.child, d); moved = true; }
    }
    if (!moved) break;
  }
  const nodes = { [ROOT]: { id: ROOT, title: owner, sub: `${fmt(shown.length, 0)} tables`, kind: 'root', children: [] } };
  GROUPS.forEach(([id, title]) => { nodes[id] = { id, title, kind: 'group', children: [], parent: ROOT }; });
  const confirmed = s => s === 'verified' || s === 'declared';
  for (const t of shown) {
    const n = { id: t.table, title: t.business || t.table, code: t.table, kind: masters.has(t.table) ? 'master' : t.table === head ? 'header' : 'detail',
      rows: t.rows, approx: t.approx, children: [], table: true,
      links: links.filter(j => j.parent === t.table || j.child === t.table).map(j => j.parent === t.table ? `→ ${j.child}` : `← ${j.parent}`) };
    nodes[t.table] = n;
  }
  for (const t of shown) {
    const n = nodes[t.table];
    if (n.kind === 'master') n.parent = '__masters';
    else if (!depth.has(t.table)) n.parent = '__other';
    else if (t.table === head) n.parent = '__orders';
    else {
      const ups = links.filter(j => j.child === t.table && depth.has(j.parent) && depth.get(j.parent) === depth.get(t.table) - 1);
      ups.sort((a, b) => !confirmed(a.status) - !confirmed(b.status) || a.parent.localeCompare(b.parent));
      n.parent = ups[0]?.parent || head;
      n.dashed = !!ups[0] && !confirmed(ups[0].status);
    }
  }
  const order = n => [n.kind === 'header' ? 0 : 1, -(n.children.length), n.title];
  for (const n of Object.values(nodes)) if (n.parent) nodes[n.parent].children.push(n.id);
  for (const n of Object.values(nodes)) {
    if (n.id === ROOT) continue;                     // the groups keep their own order
    n.children.sort((a, b) => { const x = order(nodes[a]), y = order(nodes[b]); return x[0] - y[0] || x[1] - y[1] || String(x[2]).localeCompare(y[2]); });
  }
  GROUPS.forEach(([id]) => { if (!nodes[id].children.length) { nodes[ROOT].children = nodes[ROOT].children.filter(c => c !== id); delete nodes[id]; } });
  for (const [id] of GROUPS) if (nodes[id]) nodes[id].sub = `${count(nodes, id)} tables`;
  return { nodes, root: ROOT };
}
function count(nodes, id) { return nodes[id].children.reduce((n, c) => n + 1 + count(nodes, c), 0); }

// Places of the open part of the tree: leaves one under the other, a parent in the middle of its children.
export function layout(nodes, open, root = ROOT) {
  const pos = {}; let y = PAD;
  const visit = (id, depth) => {
    const n = nodes[id]; const kids = open.has(id) ? n.children : [];
    const x = xAt(depth), w = widthAt(depth);
    if (!kids.length) { pos[id] = { x, y, w }; y += H + GAP_Y; return; }
    kids.forEach(k => visit(k, depth + 1));
    const first = pos[kids[0]].y, last = pos[kids.at(-1)].y;
    pos[id] = { x, y: (first + last) / 2, w };
  };
  visit(root, 0);
  return { pos, width: Math.max(...Object.values(pos).map(p => p.x + p.w)) + PAD, height: y - GAP_Y + PAD };
}

// ---------------------------------------------------------------- drawing
const trunc = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const curve = (a, b) => { const x1 = a.x + a.w, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2; return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`; };

export function mindMap(el, tables, joins, { selected = null, blink = new Set(), notes = {}, onSelect = () => {}, owner } = {}) {
  const { nodes, root } = buildTree(tables, joins, owner);
  const open = new Set([root, '__orders']);
  // the order tables are open; a blinking table's branch is opened so it can be seen
  const openTo = id => { for (let p = nodes[id]?.parent; p; p = nodes[p]?.parent) open.add(p); };
  Object.values(nodes).filter(n => n.kind === 'header' || (n.table && n.kind !== 'master' && n.children.length)).forEach(n => open.add(n.id));
  blink.forEach(openTo);
  let sel = selected; let zoom = 1;
  el.classList.add('mm-wrap');
  el.innerHTML = `<div class="mm-tools" role="toolbar" aria-label="Mind map"><button type="button" class="ghost small" data-mm="open">Open all</button>
      <button type="button" class="ghost small" data-mm="close">Close all</button><span class="spacer"></span>
      <button type="button" class="icon-btn" data-mm="out" aria-label="Smaller">−</button><span class="mm-zoom" data-zl>100%</span><button type="button" class="icon-btn" data-mm="in" aria-label="Bigger">＋</button></div>
    <div class="mm-scroll"><svg class="mm" role="tree" aria-label="Database tables, split from ${esc(owner || 'GARMENT_DEMO')}"><g class="mm-edges"></g><g class="mm-nodes"></g></svg></div>`;
  const svg = el.querySelector('svg'), gE = svg.querySelector('.mm-edges'), gN = svg.querySelector('.mm-nodes');

  function nodeHtml(n, w) {
    const kids = n.children.length; const isOpen = open.has(n.id);
    const line2 = n.table ? `${n.code}${n.rows == null ? '' : ` · ${n.approx ? '≈ ' : ''}${fmt(n.rows, 0)} rows`}` : n.sub || '';
    const tip = n.table ? `${n.title} (${n.code})${notes[n.id] ? ` — ${notes[n.id]}` : ''}${n.links.length ? `\nLinks: ${n.links.join(', ')}` : ''}` : n.title;
    return `<title>${esc(tip)}</title><rect class="mm-box" width="${w}" height="${H}" rx="${H / 2}"/>
      <text class="mm-t1" x="18" y="${line2 ? 20 : 27}">${esc(trunc(n.title, Math.floor((w - (kids ? 44 : 26)) / 7.4)))}</text>
      ${line2 ? `<text class="mm-t2" x="18" y="36">${esc(trunc(notes[n.id] ? `New rows · ${n.code}` : line2, 32))}</text>` : ''}
      ${kids ? `<g class="mm-toggle" data-toggle="${esc(n.id)}" role="button" tabindex="-1" aria-label="${isOpen ? 'Close' : 'Open'} ${esc(n.title)}" transform="translate(${w - 4},${H / 2})">
        <circle r="11"/><text text-anchor="middle" y="4">${isOpen ? '‹' : '›'}</text>${!isOpen ? `<text class="mm-count" x="16" y="4">${kids}</text>` : ''}</g>` : ''}`;
  }

  function draw() {
    const { pos, width, height } = layout(nodes, open, root);
    svg.setAttribute('width', width * zoom); svg.setAttribute('height', height * zoom); svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    el.querySelector('[data-zl]').textContent = `${Math.round(zoom * 100)}%`;
    const ids = Object.keys(pos);
    // edges: one curve from each open parent to each child
    const lines = new Map([...gE.children].map(p => [p.dataset.id, p]));
    for (const [id, p] of lines) if (!pos[id] || !pos[nodes[id].parent]) p.remove();
    for (const id of ids.filter(i => nodes[i].parent && pos[nodes[i].parent])) {
      let p = lines.get(id);
      const to = `path('${curve(pos[nodes[id].parent], pos[id])}')`;
      if (!p) {
        p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.dataset.id = id;
        const from = pos[nodes[id].parent];
        p.style.d = `path('${curve(from, from)}')`; gE.appendChild(p);
      }
      p.setAttribute('class', `mm-edge ${nodes[id].kind} ${nodes[id].dashed ? 'dashed' : ''}`);
      requestAnimationFrame(() => { p.style.d = to; });
    }
    // nodes are kept between draws, so opening a branch slides the others to their new place
    const have = new Map([...gN.children].map(g => [g.dataset.id, g]));
    for (const [id, g] of have) if (!pos[id]) g.remove();
    for (const id of ids) {
      const n = nodes[id]; let g = have.get(id);
      if (!g) {
        g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.dataset.id = id; g.setAttribute('role', 'treeitem'); g.tabIndex = 0;
        const from = pos[n.parent] || pos[id];
        g.style.transform = `translate(${from.x}px, ${from.y}px)`;
        gN.appendChild(g);
      }
      g.setAttribute('class', `mm-node ${n.kind} ${n.dashed ? 'dashed' : ''} ${blink.has(id) ? 'blink' : ''} ${id === sel ? 'sel' : ''}`);
      if (n.children.length) g.setAttribute('aria-expanded', String(open.has(id))); else g.removeAttribute('aria-expanded');
      g.setAttribute('aria-label', `${n.title}${n.code ? ` (${n.code})` : ''}${n.children.length ? `, ${n.children.length} branches` : ''}`);
      g.innerHTML = nodeHtml(n, pos[id].w);
      requestAnimationFrame(() => { g.style.transform = `translate(${pos[id].x}px, ${pos[id].y}px)`; });
    }
  }

  const toggle = id => { open.has(id) ? closeBranch(id) : open.add(id); draw(); el.querySelector(`.mm-node[data-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true }); };
  const closeBranch = id => { open.delete(id); nodes[id].children.forEach(c => open.has(c) && closeBranch(c)); };
  const choose = id => {
    const n = nodes[id];
    if (!n.table) return toggle(id);
    sel = id; blink.delete(id); draw(); onSelect(id);
  };
  el.addEventListener('click', e => {
    const t = e.target.closest('[data-toggle]'); if (t) { e.stopPropagation(); return toggle(t.dataset.toggle); }
    const m = e.target.closest('[data-mm]');
    if (m) {
      const a = m.dataset.mm;
      if (a === 'open') Object.values(nodes).forEach(n => n.children.length && open.add(n.id));
      if (a === 'close') { open.clear(); open.add(root); }
      if (a === 'in') zoom = Math.min(1.6, zoom + 0.15);
      if (a === 'out') zoom = Math.max(0.5, zoom - 0.15);
      return draw();
    }
    const g = e.target.closest('.mm-node'); if (g) choose(g.dataset.id);
  });
  el.addEventListener('keydown', e => {
    const g = e.target.closest?.('.mm-node'); if (!g) return;
    const n = nodes[g.dataset.id];
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(n.id); }
    else if (e.key === 'ArrowRight' && n.children.length && !open.has(n.id)) { e.preventDefault(); toggle(n.id); }
    else if (e.key === 'ArrowLeft' && open.has(n.id) && n.children.length) { e.preventDefault(); toggle(n.id); }
    else if (e.key === 'ArrowLeft' && n.parent) { e.preventDefault(); el.querySelector(`.mm-node[data-id="${CSS.escape(n.parent)}"]`)?.focus(); }
  });
  const first = layout(nodes, open, root).width; const box = el.clientWidth || first;
  zoom = Math.max(0.7, Math.min(1, Math.round((box - 4) / first * 20) / 20));   // the whole width in view when it fits at 70 % or more
  draw();
  return { seen: id => { blink.delete(id); draw(); } };
}
