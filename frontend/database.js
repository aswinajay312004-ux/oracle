'use strict';
/* Database: flow diagram of real relationships, table explorer, guided table creation,
   relationship designer and the node-based Excel → Oracle mapping designer. */
const DB = { tab: 'flow', graph: null, orders: null, order: null, detail: null, view: { x: 40, y: 30, k: 1 }, q: '' };
const DB_TABS = [['flow', 'Flow diagram', 'flow'], ['tables', 'Tables', 'table'], ['links', 'Relationships', 'link'], ['designer', 'Mapping designer', 'sparkle']];

route('database', async (view, [tab, arg]) => {
  setTitle('Database', 'Your Oracle tables, how they connect, and what each order stored');
  DB.tab = DB_TABS.some(t => t[0] === tab) ? tab : 'flow';
  view.innerHTML = `<div class="stack"><div class="row between"><div class="seg">${DB_TABS.map(([k, l, ic]) => `<button type="button" data-tab="${k}" class="${DB.tab === k ? 'active' : ''}">${icon(ic)} ${l}</button>`).join('')}</div>
    <button class="btn" id="create-table" type="button">${icon('fileplus')} Create table</button></div><div id="db-body"></div></div>`;
  $$('[data-tab]', view).forEach(b => b.onclick = () => go(`#/database/${b.dataset.tab}`));
  $('#create-table', view).onclick = () => createTable();
  DB.graph = await api('/api/db/graph');
  const body = $('#db-body', view);
  if (DB.tab === 'flow') return flowTab(body, arg ? Number(arg) : DB.order);
  if (DB.tab === 'tables') return tablesTab(body, arg);
  if (DB.tab === 'links') return linksTab(body, arg);
  return designerTab(body, arg);
});

/* ------------------------------------------------------------------ flow diagram */
const NODE_W = 196, NODE_H = 62;
function layout(tables, edges) {
  const names = tables.map(t => t.name); const parents = {}; names.forEach(n => { parents[n] = []; });
  edges.forEach(e => { if (parents[e.child] && names.includes(e.parent) && e.parent !== e.child && !parents[e.child].includes(e.parent)) parents[e.child].push(e.parent); });
  const depth = {}; const visit = (n, seen = new Set()) => { if (depth[n] != null) return depth[n]; if (seen.has(n)) return 0; seen.add(n);
    depth[n] = parents[n].length ? 1 + Math.max(...parents[n].map(p => visit(p, seen))) : 0; return depth[n]; };
  names.forEach(n => visit(n));
  const linked = new Set(edges.flatMap(e => [e.parent, e.child]));
  const layers = {}; names.forEach(n => { const d = linked.has(n) ? depth[n] : 'free'; (layers[d] ||= []).push(n); });
  // Wide layers wrap into rows of four so the diagram stays readable without zooming out.
  const pos = {}; const keys = Object.keys(layers).filter(k => k !== 'free').map(Number).sort((a, b) => a - b); const PER = 4; let y = 0;
  const place = (list, per) => { const sorted = list.sort((a, b) => (ORDER_RANK(a) - ORDER_RANK(b)) || a.localeCompare(b));
    for (let i = 0; i < sorted.length; i += per) { const row = sorted.slice(i, i + per); const offset = (PER - row.length) * (NODE_W + 28) / 2;
      row.forEach((n, j) => { pos[n] = { x: offset + j * (NODE_W + 28), y }; }); y += i + per < sorted.length ? 96 : 140; } };
  keys.forEach(k => place(layers[k], PER));
  const freeY = y + 10; y += 40;
  if (layers.free) place(layers.free, PER);
  return { pos, freeY, hasFree: !!layers.free };
}
const ORDER_RANK = n => ({ ORDMAS: 0, ORDSTYLE: 1, ORDSIZE: 2 }[n] ?? 5);

async function flowTab(body, orderId) {
  DB.orders = DB.orders || await api('/api/orders').catch(() => []);
  body.innerHTML = `<div class="db-layout"><section class="card order-pick"><div class="card-head"><div><h2>Orders</h2><span class="sub">Select one to see its records</span></div></div>
      <div style="padding:10px 12px"><label class="search">${icon('search')}<input id="f-oq" placeholder="Search orders" aria-label="Search orders"></label></div><div class="items" id="f-orders"></div></section>
    <section class="card canvas-card"><label class="search canvas-search">${icon('search')}<input id="f-q" placeholder="Find a table" value="${esc(DB.q)}" aria-label="Find a table"></label>
      <div class="canvas-tools"><button class="btn small" id="z-in" title="Zoom in" aria-label="Zoom in">${icon('zoomin')}</button><button class="btn small" id="z-out" title="Zoom out" aria-label="Zoom out">${icon('zoomout')}</button><button class="btn small" id="z-fit" title="Fit to screen">${icon('fit')} Fit</button></div>
      <svg class="canvas" id="f-svg" role="img" aria-label="Database flow diagram"><g id="f-vp"></g></svg>
      <div class="canvas-legend"><span><i style="border-color:#3aa77a"></i>Verified by POPPYS writer</span><span><i style="border-color:#3b5b4d"></i>Oracle foreign key</span><span><i style="border-color:#6b4fa3;border-top-style:dashed"></i>Admin-defined link</span><span><span class="badge green">n</span> records for the selected order</span></div></section></div>`;
  const listOrders = () => { const q = $('#f-oq', body).value.trim().toLowerCase();
    $('#f-orders', body).innerHTML = DB.orders.filter(o => !q || `${o.order_ref} ${o.buyer} ${o.style}`.toLowerCase().includes(q)).map(o => `<button class="order-item ${o.ordmasid === DB.order ? 'active' : ''}" data-o="${o.ordmasid}"><strong>${esc(o.order_ref)}</strong><small>${esc(o.buyer || '—')} · ${fmt(o.quantity)} pcs</small></button>`).join('') || '<div class="empty">No saved orders yet.</div>';
    $$('[data-o]', body).forEach(b => b.onclick = () => selectOrder(Number(b.dataset.o))); };
  $('#f-oq', body).addEventListener('input', listOrders);
  const { pos, freeY, hasFree } = layout(DB.graph.tables, DB.graph.relationships); DB.pos = pos;
  const draw = () => {
    const counts = orderCounts(); const q = DB.q.trim().toUpperCase();
    const edges = DB.graph.relationships.filter(e => pos[e.parent] && pos[e.child]).map(e => { const a = pos[e.parent], b = pos[e.child];
      const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y; const self = e.parent === e.child;
      const d = self ? `M${a.x + NODE_W} ${a.y + 20} c40 0 40 30 0 30` : `M${x1} ${y1} C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}`;
      return `<path class="edge ${e.kind} ${q && !(e.parent.includes(q) || e.child.includes(q)) ? 'dim' : ''}" d="${d}"><title>${esc(e.parent)}.${esc(e.parent_column)} → ${esc(e.child)}.${esc(e.child_column)} (${esc(e.kind)})</title></path>`; }).join('');
    const nodes = DB.graph.tables.map(t => { const p = pos[t.name]; const c = counts[t.name]; const cls = [['ORDMAS', 'ORDSTYLE', 'ORDSIZE'].includes(t.name) ? 'order-table' : '', c ? 'has-data' : '', q && !t.name.includes(q) && !String(t.label).toUpperCase().includes(q) ? 'dim' : '', q && t.name.includes(q) ? 'match' : '', DB.selected === t.name ? 'selected' : ''].join(' ');
      return `<g class="node ${cls}" data-node="${esc(t.name)}" transform="translate(${p.x},${p.y})" tabindex="0" role="button" aria-label="${esc(t.name)} ${esc(t.label)}">
        <rect class="box" width="${NODE_W}" height="${NODE_H}" rx="10"/><rect class="hdr" width="6" height="${NODE_H}" rx="3"/>
        <text class="t1" x="16" y="25">${esc(t.name)}</text><text class="t2" x="16" y="44">${esc(t.label !== t.name ? t.label : plural(t.columns, 'column'))}</text>
        ${DB.order ? `<rect class="cnt-bg ${c ? '' : 'zero'}" x="${NODE_W - 40}" y="10" width="30" height="18" rx="9"/><text class="cnt" x="${NODE_W - 25}" y="23" text-anchor="middle">${c ?? 0}</text>` : ''}</g>`; }).join('');
    $('#f-vp', body).innerHTML = edges + (hasFree ? `<text x="0" y="${freeY}" class="edge-label">Not linked yet — add a relationship to connect these tables</text>` : '') + nodes;
    $$('[data-node]', body).forEach(n => { n.addEventListener('click', () => openTable(n.dataset.node)); n.addEventListener('keydown', e => { if (e.key === 'Enter') openTable(n.dataset.node); }); });
    applyView();
  };
  const svg = $('#f-svg', body); const applyView = () => $('#f-vp', body).setAttribute('transform', `translate(${DB.view.x},${DB.view.y}) scale(${DB.view.k})`);
  const fit = () => { const xs = Object.values(pos); if (!xs.length) return; const w = Math.max(...xs.map(p => p.x)) + NODE_W, h = Math.max(...xs.map(p => p.y)) + NODE_H;
    const r = svg.getBoundingClientRect(); const k = Math.min(1.2, (r.width - 60) / w, (r.height - 110) / h); DB.view = { k, x: (r.width - w * k) / 2, y: 60 + (r.height - 110 - h * k) / 2 }; applyView(); };
  const zoom = f => { const r = svg.getBoundingClientRect(); const cx = r.width / 2, cy = r.height / 2; const k = Math.min(2.5, Math.max(.25, DB.view.k * f));
    DB.view = { k, x: cx - (cx - DB.view.x) * k / DB.view.k, y: cy - (cy - DB.view.y) * k / DB.view.k }; applyView(); };
  $('#z-in', body).onclick = () => zoom(1.2); $('#z-out', body).onclick = () => zoom(1 / 1.2); $('#z-fit', body).onclick = fit;
  svg.addEventListener('wheel', e => { e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1); }, { passive: false });
  let drag = null;
  svg.addEventListener('pointerdown', e => { if (e.target.closest('[data-node]')) return; drag = { x: e.clientX, y: e.clientY, vx: DB.view.x, vy: DB.view.y }; svg.classList.add('dragging'); svg.setPointerCapture(e.pointerId); });
  svg.addEventListener('pointermove', e => { if (!drag) return; DB.view.x = drag.vx + e.clientX - drag.x; DB.view.y = drag.vy + e.clientY - drag.y; applyView(); });
  svg.addEventListener('pointerup', () => { drag = null; svg.classList.remove('dragging'); });
  $('#f-q', body).addEventListener('input', e => { DB.q = e.target.value; draw(); });
  async function selectOrder(id) {
    DB.order = id; history.replaceState(null, '', `#/database/flow/${id}`); listOrders();
    DB.detail = await busy('Loading the order from Oracle…', () => loadOrder(id, true)); draw();
  }
  DB.redraw = draw; listOrders(); draw(); fit();
  if (orderId) selectOrder(orderId);
}
function orderCounts() {
  if (!DB.order || !DB.detail || DB.detail.ordmasid !== DB.order) return {};
  const counts = { ...DB.detail.summary.records }; const ids = {};
  for (const [key, labels] of Object.entries(DB.detail.labels)) { const master = MASTER_OF[key]; if (master) Object.keys(labels).forEach(id => (ids[master] ||= new Set()).add(id)); }
  Object.entries(ids).forEach(([t, set]) => { counts[t] = set.size; });
  return counts;
}

function openTable(name) {
  const t = DB.graph.tables.find(x => x.name === name); DB.selected = name; DB.redraw?.();
  const rels = DB.graph.relationships.filter(e => e.parent === name || e.child === name);
  Drawer.open({ eyebrow: `${t.label !== name ? t.label + ' · ' : ''}Oracle table`, title: name, tabs: [['data', 'Data'], ['visual', 'Visualization']], wide: true,
    render: async tab => {
      if (tab === 'visual') { if (!DB.order) return `<div class="empty">${icon('chart')}<p>Select an order on the left to see its dashboard.</p></div>`; return orderDashboard(await loadOrder(DB.order)); }
      const intro = `${t.purpose ? `<p class="muted">${esc(t.purpose)}</p>` : ''}<div class="row"><span class="badge">${plural(t.columns, 'column')}</span>${t.primary_key.length ? `<span class="badge">Key: ${esc(t.primary_key.join(', '))}</span>` : ''}${t.rows_estimate != null ? `<span class="badge">≈ ${fmt(t.rows_estimate)} rows</span>` : ''}</div>
        ${rels.length ? `<section class="card"><div class="card-head"><h3>Relationships</h3></div><div class="table-wrap"><table><tbody>${rels.map(e => `<tr><td><span class="col-name">${esc(e.parent)}.${esc(e.parent_column)}</span></td><td class="arrow">→</td><td><span class="col-name">${esc(e.child)}.${esc(e.child_column)}</span></td><td>${kindBadge(e.kind)}</td></tr>`).join('')}</tbody></table></div></section>` : `<div class="callout">${icon('info')}<div>No relationships yet. Add one under Relationships.</div></div>`}`;
      if (DB.order) {
        const r = await api(`/api/orders/${DB.order}/tables/${encodeURIComponent(name)}`); const d = await loadOrder(DB.order);
        const order = DB.orders.find(o => o.ordmasid === DB.order);
        return `${intro}<div class="callout success">${icon('database')}<div>Records stored for order <strong>${esc(order?.order_ref || DB.order)}</strong>${r.via ? ` · linked by <code>${esc(r.via)}</code>` : ''}. Read-only values from Oracle.</div></div>${recordsHtml(name, r.rows, d)}`;
      }
      const r = await api(`/api/db/tables/${encodeURIComponent(name)}`);
      return `${intro}<div class="callout info">${icon('info')}<div>No order selected — showing the first ${plural(r.rows.length, 'record')} of ${fmt(r.total)} in this table.</div></div>${recordsHtml(name, r.rows, null)}`;
    } });
}
const kindBadge = k => k === 'physical' ? '<span class="badge">Oracle foreign key</span>' : k === 'verified' ? '<span class="badge green">Verified</span>' : '<span class="badge violet">Admin link</span>';
function recordsHtml(table, rows, d) {
  if (!rows.length) return `<div class="empty">${icon('table')}<p>No records ${d ? 'for this order' : ''} in ${esc(table)}.</p></div>`;
  const pk = Object.keys(rows[0]).find(k => k === table + 'ID') || Object.keys(rows[0])[0];
  const cols = Object.keys(rows[0]).filter(c => rows.some(r => r[c] != null));
  if (rows.length === 1) return `<section class="card"><div class="table-wrap"><table class="kv"><thead><tr><th>Column</th><th>Saved value</th><th>Excel source</th></tr></thead><tbody>${cols.map(c => `<tr><td><span class="col-name">${esc(c)}</span><span class="col-label">${esc(COLUMN_LABELS[c] || '')}</span></td><td>${d ? valueText(d, table, c, rows[0][c]) : esc(val(c, rows[0][c]))}</td><td>${sourceChip(d?.sources?.[`${table}:${rows[0][pk]}`]?.[c]?.source)}</td></tr>`).join('')}</tbody></table></div></section>`;
  return `<section class="card"><div class="table-wrap" style="max-height:60vh"><table><thead><tr>${cols.map(c => `<th title="${esc(COLUMN_LABELS[c] || '')}">${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => { const s = d?.sources?.[`${table}:${r[pk]}`]?.[c]?.source; return `<td>${d ? valueText(d, table, c, r[c]) : esc(val(c, r[c]))}${s?.cell ? `<span class="sub">${sourceChip(s)}</span>` : ''}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}

/* ------------------------------------------------------------------ tables */
async function tablesTab(body, selected) {
  const tables = DB.graph.tables; selected = selected || 'ORDMAS';
  body.innerHTML = `<div class="db-layout"><section class="card order-pick"><div style="padding:10px 12px"><label class="search">${icon('search')}<input id="t-q" placeholder="Search tables" aria-label="Search tables"></label></div><div class="items" id="t-list"></div></section><section id="t-detail"></section></div>`;
  const list = () => { const q = $('#t-q', body).value.trim().toUpperCase(); $('#t-list', body).innerHTML = tables.filter(t => !q || t.name.includes(q) || t.label.toUpperCase().includes(q)).map(t => `<button class="order-item ${t.name === selected ? 'active' : ''}" data-t="${esc(t.name)}"><strong class="mono">${esc(t.name)}</strong><small>${esc(t.label !== t.name ? t.label + ' · ' : '')}${plural(t.columns, 'column')}</small></button>`).join('');
    $$('[data-t]', body).forEach(b => b.onclick = () => { selected = b.dataset.t; history.replaceState(null, '', `#/database/tables/${selected}`); list(); show(0); }); };
  const show = async offset => { const r = await api(`/api/db/tables/${encodeURIComponent(selected)}?offset=${offset}`); const el = $('#t-detail', body);
    el.innerHTML = `<div class="stack"><section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('table')}</span><div><span class="tname">${esc(r.name)}</span> <span class="muted">· ${esc(tableLabel(r.name))}</span><span class="sub" style="display:block">${fmt(r.total)} records · ${plural(r.columns.length, 'column')}</span></div></div><a class="btn small" href="#/database/links/${esc(r.name)}">${icon('link')} Link this table</a></div>
      <div class="table-wrap" style="max-height:300px"><table><thead><tr><th>Column</th><th>Type</th><th>Required</th><th>Key</th></tr></thead><tbody>${r.columns.map(c => `<tr><td><span class="col-name">${esc(c.name)}</span><span class="col-label">${esc(COLUMN_LABELS[c.name] || '')}</span></td><td class="mono">${esc(c.type)}${c.type.includes('CHAR') ? `(${c.length})` : c.precision ? `(${c.precision},${c.scale || 0})` : ''}</td><td>${c.nullable === 'N' ? '<span class="badge amber">Required</span>' : '<span class="faint">Optional</span>'}</td><td>${r.keys.filter(k => k.column === c.name).map(k => `<span class="badge">${k.type}</span>`).join(' ')}</td></tr>`).join('')}</tbody></table></div></section>
      <section class="card"><div class="card-head"><h3>Records (read-only)</h3><div class="row"><button class="btn small" ${offset ? '' : 'disabled'} data-page="${Math.max(0, offset - 50)}">Previous</button><span class="muted">${r.total ? `${offset + 1}–${offset + r.rows.length} of ${fmt(r.total)}` : 'empty'}</span><button class="btn small" ${offset + 50 < r.total ? '' : 'disabled'} data-page="${offset + 50}">Next</button></div></div>
        ${r.rows.length ? `<div class="table-wrap" style="max-height:420px"><table><thead><tr>${Object.keys(r.rows[0]).filter(c => r.rows.some(x => x[c] != null)).map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${r.rows.map(x => `<tr>${Object.keys(r.rows[0]).filter(c => r.rows.some(y => y[c] != null)).map(c => `<td>${esc(val(c, x[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<div class="empty">No records.</div>'}</section></div>`;
    paintIcons(el); $$('[data-page]', el).forEach(b => b.onclick = () => show(Number(b.dataset.page))); };
  $('#t-q', body).addEventListener('input', list); list(); show(0);
}

/* ------------------------------------------------------------------ create table */
function createTable() {
  const cols = [{ name: 'ID', type: 'NUMBER', required: true, pk: true }, { name: '', type: 'VARCHAR2', length: 100 }];
  const body = Modal.open('Create a new Oracle table', '<div id="ct"></div>'); let preview = null;
  const spec = () => ({ name: $('#ct-name', body).value.trim().toUpperCase(), columns: cols.filter(c => c.name.trim()).map(c => ({ name: c.name.trim().toUpperCase(), type: c.type, length: c.length, precision: c.precision, scale: c.scale, required: !!c.required, default: c.default || null })),
    primary_key: cols.filter(c => c.pk && c.name.trim()).map(c => c.name.trim().toUpperCase()) });
  const draw = () => {
    $('#ct', body).innerHTML = `<div class="stack"><p class="muted">Enter the table name and its columns like a spreadsheet. POPPYS checks everything and shows the exact Oracle statement before anything is created.</p>
      <label class="field">Table name<input id="ct-name" placeholder="e.g. ORDER_PRINTING" value="${esc(DB.ctName || '')}" style="max-width:360px;text-transform:uppercase"></label>
      <div class="table-wrap card"><table class="coledit"><thead><tr><th>Column name</th><th>Type</th><th>Size</th><th>Required</th><th>Key</th><th>Default</th><th></th></tr></thead><tbody>
      ${cols.map((c, i) => `<tr><td><input data-i="${i}" data-k="name" value="${esc(c.name)}" placeholder="COLUMN_NAME" style="text-transform:uppercase"></td>
        <td><select data-i="${i}" data-k="type">${[['VARCHAR2', 'Text'], ['NUMBER', 'Number'], ['DATE', 'Date'], ['CHAR', 'Code (fixed)'], ['TIMESTAMP', 'Date & time'], ['CLOB', 'Long text']].map(([v, l]) => `<option value="${v}" ${c.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select></td>
        <td>${['VARCHAR2', 'CHAR'].includes(c.type) ? `<input data-i="${i}" data-k="length" type="number" min="1" value="${esc(c.length ?? '')}" placeholder="length">` : c.type === 'NUMBER' ? `<div class="row" style="flex-wrap:nowrap;gap:4px"><input data-i="${i}" data-k="precision" type="number" min="1" max="38" value="${esc(c.precision ?? '')}" placeholder="digits"><input data-i="${i}" data-k="scale" type="number" min="0" value="${esc(c.scale ?? '')}" placeholder="decimals"></div>` : '<span class="faint">—</span>'}</td>
        <td style="text-align:center"><input type="checkbox" data-i="${i}" data-k="required" ${c.required ? 'checked' : ''} aria-label="Required"></td>
        <td style="text-align:center"><input type="checkbox" data-i="${i}" data-k="pk" ${c.pk ? 'checked' : ''} aria-label="Primary key"></td>
        <td><input data-i="${i}" data-k="default" value="${esc(c.default ?? '')}" placeholder="${c.type === 'DATE' ? 'today' : 'optional'}"></td>
        <td><button class="icon-btn" data-del="${i}" aria-label="Remove column">${icon('x')}</button></td></tr>`).join('')}</tbody></table></div>
      <div><button class="btn small" id="ct-add">${icon('plus')} Add column</button></div>
      <div id="ct-preview"></div>
      <div class="modal-foot"><button class="btn" id="ct-cancel">Cancel</button><button class="btn primary" id="ct-check">${icon('eye')} Preview structure</button></div></div>`;
    paintIcons(body);
    $('#ct-name', body).oninput = e => { DB.ctName = e.target.value; preview = null; $('#ct-preview', body).innerHTML = ''; };
    $$('[data-k]', body).forEach(el => el.addEventListener(el.type === 'checkbox' || el.tagName === 'SELECT' ? 'change' : 'input', () => { const c = cols[Number(el.dataset.i)]; c[el.dataset.k] = el.type === 'checkbox' ? el.checked : el.value;
      if (el.dataset.k === 'pk' && el.checked) c.required = true; preview = null; if (el.tagName === 'SELECT' || el.type === 'checkbox') draw(); else $('#ct-preview', body).innerHTML = ''; }));
    $$('[data-del]', body).forEach(b => b.onclick = () => { cols.splice(Number(b.dataset.del), 1); draw(); });
    $('#ct-add', body).onclick = () => { cols.push({ name: '', type: 'VARCHAR2', length: 100 }); draw(); };
    $('#ct-cancel', body).onclick = () => Modal.close();
    $('#ct-check', body).onclick = () => busy('Checking the table design…', async () => { const s = spec(); preview = await api('/api/db/tables/preview', { method: 'POST', body: s }); preview.spec = s; showPreview(); });
  };
  const showPreview = () => { const el = $('#ct-preview', body);
    el.innerHTML = preview.errors.length ? `<div class="callout attention">${icon('alert')}<div><strong>Please correct</strong><ul>${preview.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` :
      `<div class="stack"><div class="callout success">${icon('check')}<div>The design is valid. This is the exact statement Oracle will run:</div></div><div class="ddl">${esc(preview.ddl)}</div>
       ${preview.enabled ? `<div class="callout attention">${icon('alert')}<div>Creating a table changes the database structure immediately and cannot be undone from this screen. Type <strong>${esc(preview.table)}</strong> to confirm.</div></div>
       <div class="row"><input id="ct-confirm" placeholder="${esc(preview.table)}" style="max-width:280px"><button class="btn primary" id="ct-create" disabled>${icon('database')} Create table</button></div>` :
       `<div class="callout info">${icon('info')}<div>Table creation is switched off on this database. The administrator can enable it once by running <code>python scripts/upgrade_local.py</code>.</div></div>`}</div>`;
    paintIcons(el);
    $('#ct-confirm', el)?.addEventListener('input', e => { $('#ct-create', el).disabled = e.target.value.trim().toUpperCase() !== preview.table; });
    $('#ct-create', el)?.addEventListener('click', () => busy('Creating the table in Oracle…', async () => {
      const r = await api('/api/db/tables', { method: 'POST', body: { spec: preview.spec, hash: preview.hash, confirm: $('#ct-confirm', el).value } });
      DB.ctName = ''; Modal.close(); toast(`Table ${r.created} created. Link it to other tables next.`); go(`#/database/links/${r.created}`);
    }));
  };
  draw();
}

/* ------------------------------------------------------------------ relationships */
async function linksTab(body, preset) {
  const tables = DB.graph.tables.map(t => t.name); const L = { parent: 'ORDMAS', child: preset && tables.includes(preset) ? preset : '', pc: null, cc: null, kind: 'application', result: null };
  const cache = {}; const columns = async t => (cache[t] ||= (await api(`/api/db/tables/${encodeURIComponent(t)}`)).columns);
  const draw = async () => {
    const [pcols, ccols] = await Promise.all([columns(L.parent), L.child ? columns(L.child) : []]);
    body.innerHTML = `<div class="split"><section class="card"><div class="card-head"><div><h2>Link two tables</h2><span class="sub">Drag a column from the child table onto the column it points to in the parent table</span></div></div><div class="card-pad stack">
      <div class="col-grid"><label class="field">Parent table (one)<select id="l-parent">${tables.map(t => `<option ${t === L.parent ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></label>
        <label class="field">Child table (many)<select id="l-child"><option value="">Choose…</option>${tables.map(t => `<option ${t === L.child ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></label></div>
      <div class="col-grid"><div class="link-col" id="l-pcols">${pcols.map(c => `<button type="button" data-pc="${esc(c.name)}" class="${L.pc === c.name ? 'on' : ''}"><span class="mono">${esc(c.name)}</span><span class="faint">${esc(c.type)}</span></button>`).join('')}</div>
        <div class="link-col">${L.child ? ccols.map(c => `<button type="button" draggable="true" data-cc="${esc(c.name)}" class="${L.cc === c.name ? 'on' : ''}"><span class="mono">${esc(c.name)}</span><span class="faint">${esc(c.type)}</span></button>`).join('') : '<div class="empty">Choose the child table.</div>'}</div></div>
      <div class="row"><strong>${L.pc && L.cc ? `<span class="mono">${esc(L.parent)}.${esc(L.pc)}</span> <span class="arrow">→</span> <span class="mono">${esc(L.child)}.${esc(L.cc)}</span>` : '<span class="muted">Select one column on each side.</span>'}</strong></div>
      <div class="seg"><button type="button" data-kind="application" class="${L.kind === 'application' ? 'active' : ''}">Application link</button><button type="button" data-kind="physical" class="${L.kind === 'physical' ? 'active' : ''}">Oracle foreign key</button></div>
      <p class="muted">${L.kind === 'application' ? 'Recorded in POPPYS only. Oracle is not changed. Use this when the ERP links tables without a database constraint.' : 'Adds a real constraint in Oracle. Existing data must already be consistent. Needs your confirmation.'}</p>
      <label class="field">Note (optional)<input id="l-note" maxlength="300" placeholder="e.g. Printing details belong to an order"></label>
      <div id="l-result"></div>
      <div class="row"><button class="btn primary" id="l-check" ${L.pc && L.cc ? '' : 'disabled'}>${icon('check')} Check link</button></div></div></section>
      <section class="card"><div class="card-head"><h2>Current relationships</h2><span class="sub">${plural(DB.graph.relationships.length, 'link')}</span></div>
        <div class="table-wrap" style="max-height:640px"><table><tbody>${DB.graph.relationships.map(e => `<tr><td><span class="col-name">${esc(e.parent)}.${esc(e.parent_column)}</span><span class="col-label">→ ${esc(e.child)}.${esc(e.child_column)}</span></td><td>${kindBadge(e.kind)}</td><td>${e.kind === 'application' && e.id ? `<button class="btn small ghost danger" data-retire="${e.id}">Remove</button>` : ''}</td></tr>`).join('')}</tbody></table></div></section></div>`;
    paintIcons(body);
    $('#l-parent', body).onchange = e => { L.parent = e.target.value; L.pc = null; L.result = null; draw(); };
    $('#l-child', body).onchange = e => { L.child = e.target.value; L.cc = null; L.result = null; draw(); };
    $$('[data-pc]', body).forEach(b => { b.onclick = () => { L.pc = b.dataset.pc; L.result = null; draw(); };
      b.addEventListener('dragover', e => { e.preventDefault(); b.classList.add('drop-ok'); }); b.addEventListener('dragleave', () => b.classList.remove('drop-ok'));
      b.addEventListener('drop', e => { e.preventDefault(); L.cc = e.dataTransfer.getData('text/plain'); L.pc = b.dataset.pc; L.result = null; draw(); }); });
    $$('[data-cc]', body).forEach(b => { b.onclick = () => { L.cc = b.dataset.cc; L.result = null; draw(); }; b.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', b.dataset.cc)); });
    $$('[data-kind]', body).forEach(b => b.onclick = () => { L.kind = b.dataset.kind; L.result = null; draw(); });
    $$('[data-retire]', body).forEach(b => b.onclick = async () => { if (await confirmDialog('Remove this link?', 'Only the POPPYS application link is removed. Oracle data is not changed.', 'Remove')) busy('Removing…', async () => { await api(`/api/db/relationships/${b.dataset.retire}`, { method: 'DELETE' }); DB.graph = await api('/api/db/graph'); draw(); }); });
    const spec = () => ({ parent: L.parent, parent_column: L.pc, child: L.child, child_column: L.cc, kind: L.kind, note: $('#l-note', body).value });
    $('#l-check', body).onclick = () => busy('Validating against Oracle data…', async () => { L.result = await api('/api/db/relationships/validate', { method: 'POST', body: spec() }); showResult(); });
    const showResult = () => { const r = L.result; const el = $('#l-result', body);
      el.innerHTML = `${r.errors.length ? `<div class="callout attention">${icon('alert')}<div><strong>This link cannot be saved</strong><ul>${r.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` : `<div class="callout success">${icon('check')}<div>Valid ${esc(r.cardinality)} link.</div></div>`}
        ${r.warnings.length ? `<div class="callout">${icon('info')}<ul>${r.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
        ${r.ddl && !r.errors.length ? `<div class="ddl">${esc(r.ddl)}</div><label class="row"><input type="checkbox" id="l-confirm"> I confirm adding this constraint to Oracle</label>` : ''}
        ${r.errors.length ? '' : `<button class="btn primary" id="l-save">${icon('save')} Save link</button>`}`;
      paintIcons(el);
      $('#l-save', el)?.addEventListener('click', () => busy('Saving the link…', async () => { await api('/api/db/relationships', { method: 'POST', body: { spec: spec(), hash: r.hash, confirm: !!$('#l-confirm', el)?.checked } });
        toast('Link saved. It now appears in the flow diagram.'); DB.graph = await api('/api/db/graph'); L.result = null; L.pc = L.cc = null; draw(); })); };
    if (L.result) showResult();
  };
  await draw();
}

/* ------------------------------------------------------------------ mapping designer (node based) */
async function designerTab(body, draftId) {
  const drafts = (await api('/api/drafts')).filter(d => !d.committed);
  const templates = await api('/api/templates');
  const D = { id: draftId || drafts[0]?.id, draft: null, extra: [] };
  const shell = () => `<div class="split" style="grid-template-columns:minmax(0,2.2fr) minmax(280px,.8fr)"><section class="card"><div class="card-head"><div><h2>Excel → Oracle mapping</h2><span class="sub">Drag from an Excel value (●) to an Oracle field. Click a wire to remove it.</span></div>
      <div class="row"><select id="d-draft" aria-label="Workbook">${drafts.map(d => `<option value="${esc(d.id)}" ${d.id === D.id ? 'selected' : ''}>${esc(d.filename)}</option>`).join('') || '<option>No unsaved uploads</option>'}</select><input id="d-find" placeholder="Add cell e.g. B7" style="width:120px" aria-label="Add a cell node"></div></div>
      <div class="designer" id="d-canvas"></div></section>
    <section class="card"><div class="card-head"><h2>Buyer templates</h2></div><div id="d-templates">${templatesHtml(templates)}</div></section></div>`;
  body.innerHTML = shell(); paintIcons(body); wireTemplates(body);
  if (!D.id) { $('#d-canvas', body).innerHTML = `<div class="empty">${icon('upload')}<p>Upload an Excel order first. Its cells appear here as nodes.</p><a class="btn primary" href="#/new">Upload</a></div>`; paintIcons(body); return; }
  $('#d-draft', body).onchange = e => go(`#/database/designer/${e.target.value}`);
  $('#d-find', body).onkeydown = e => { if (e.key === 'Enter') { const v = e.target.value.trim().toUpperCase(); const [sheet, cell] = v.includes('!') ? v.split('!') : [null, v]; const s = D.draft.sheets.find(x => (!sheet || x.name.toUpperCase() === sheet) && x.cells.some(c => c.cell === cell));
    if (!s) return toast(`Cell ${v} is empty or not found.`, 'attention'); D.extra.push({ sheet: s.name, cell }); e.target.value = ''; draw(); } };
  D.draft = await api(`/api/drafts/${encodeURIComponent(D.id)}`);
  const draw = () => {
    const o = D.draft.orders[0] || { provenance: {}, lines: [] }; const canvas = $('#d-canvas', body);
    const links = []; const add = (src, target, line = null) => { if (src?.cell) links.push({ key: `${src.sheet}!${src.cell}`, target, line }); };
    Object.entries(o.provenance || {}).forEach(([k, s]) => add(s, `field:${k}`)); Object.entries(o.extras || {}).forEach(([k, e]) => add(e.source, `column:ORDMAS.${k}`));
    (o.lines || []).forEach((l, li) => { add(l.provenance?.color, `line:${li}:color`); Object.entries(l.extras || {}).forEach(([k, e]) => add(e.source, `column:ORDSTYLE.${k}`, li)); (l.sizes || []).forEach((s, si) => add(s.source, `size:${li}:${si}:quantity`)); });
    // Source nodes: mapped cells, label → value pairs, and cells the admin added.
    const nodes = {}; const push = (sheet, c, label) => { (nodes[sheet] ||= new Map()).set(c.cell, { cell: c.cell, value: c.value, label }); };
    for (const s of D.draft.sheets.filter(x => !x.hidden)) { const at = new Map(s.cells.map(c => [`${c.row}:${c.col}`, c]));
      for (const c of s.cells) { const left = at.get(`${c.row}:${c.col - 1}`); if (left && typeof left.value === 'string' && c.value != null && (nodes[s.name]?.size || 0) < 30) push(s.name, c, String(left.value)); } }
    links.forEach(l => { const [sheet, cell] = l.key.split('!'); const s = D.draft.sheets.find(x => x.name === sheet); const c = s?.cells.find(x => x.cell === cell); if (c) push(sheet, c, nodes[sheet]?.get(cell)?.label || ''); });
    D.extra.forEach(x => { const c = D.draft.sheets.find(s => s.name === x.sheet).cells.find(y => y.cell === x.cell); push(x.sheet, c, 'Added cell'); });
    const linked = new Set(links.map(l => l.key));
    const srcHtml = Object.entries(nodes).map(([sheet, cells]) => `<div class="dnode src"><header>${icon('file')}${esc(sheet)}</header>${[...cells.values()].map(c => `<div class="port-row" data-src="${esc(sheet)}!${esc(c.cell)}"><span><span class="chip">${esc(c.cell)}</span> ${esc(String(c.label).slice(0, 18))}</span><span class="pv">${esc(fmt(c.value))}</span><span class="port out ${linked.has(`${sheet}!${c.cell}`) ? 'linked' : ''}" data-port="${esc(sheet)}!${esc(c.cell)}"></span></div>`).join('')}</div>`).join('');
    const tgt = (target, col, label, value, line = null) => `<div class="port-row" data-target="${esc(target)}" ${line != null ? `data-line="${line}"` : ''}><span class="port in ${links.some(l => l.target === target && (l.line ?? null) === line) ? 'linked' : ''}"></span><span><span class="col-name">${esc(col)}</span> <span class="faint">${esc(label)}</span></span><span class="pv">${esc(fmt(value ?? ''))}</span></div>`;
    const tgtHtml = `<div class="dnode"><header>${icon('table')}ORDMAS · Order header</header>${ORDER_FIELDS.map(([f, col, label]) => tgt(`field:${f}`, col, label, o[f])).join('')}${Object.keys(o.extras || {}).map(k => tgt(`column:ORDMAS.${k}`, k, 'extra', o.extras[k].value)).join('')}</div>
      <div class="dnode"><header>${icon('table')}ORDSTYLE · Order lines</header>${tgt('field:ship_date', 'SHIPDT', 'Ship date', o.ship_date)}${(o.lines || []).map((l, li) => tgt(`line:${li}:color`, 'COMBO', `Line ${li + 1} colour`, l.color)).join('')}</div>
      <div class="dnode"><header>${icon('table')}ORDSIZE · Size quantities</header>${(o.lines || []).flatMap((l, li) => (l.sizes || []).map((s, si) => tgt(`size:${li}:${si}:quantity`, 'RQTY', `L${li + 1} size ${s.size}`, s.quantity))).join('') || '<div class="port-row faint">No sizes yet</div>'}</div>`;
    canvas.innerHTML = `<svg class="wires" id="d-wires"></svg><div class="dcol" style="left:24px">${srcHtml || '<div class="empty">No cells found.</div>'}</div><div class="dcol" style="left:${24 + 260 + 200}px">${tgtHtml}</div>`;
    paintIcons(canvas);
    const wires = () => { const box = canvas.getBoundingClientRect(); const svg = $('#d-wires', canvas); svg.setAttribute('width', canvas.scrollWidth); svg.setAttribute('height', canvas.scrollHeight);
      svg.innerHTML = links.map((l, i) => { const a = $(`[data-port="${CSS.escape(l.key)}"]`, canvas); const b = $(`.port-row[data-target="${CSS.escape(l.target)}"]${l.line != null ? `[data-line="${l.line}"]` : ''} .port.in`, canvas); if (!a || !b) return '';
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect(); const x1 = ra.left + 6 - box.left + canvas.scrollLeft, y1 = ra.top + 6 - box.top + canvas.scrollTop, x2 = rb.left + 6 - box.left + canvas.scrollLeft, y2 = rb.top + 6 - box.top + canvas.scrollTop;
        return `<path data-wire="${i}" d="M${x1} ${y1} C${x1 + 90} ${y1} ${x2 - 90} ${y2} ${x2} ${y2}"><title>${esc(l.key)} → ${esc(l.target)} (click to remove)</title></path>`; }).join('');
      $$('[data-wire]', svg).forEach(p => p.addEventListener('click', async () => { const l = links[Number(p.dataset.wire)]; if (await confirmDialog('Remove this mapping?', `${esc(l.key)} will no longer fill ${esc(l.target)}.`, 'Remove')) bind(l.target, null, null, l.line); })); };
    requestAnimationFrame(wires);
    // drag a wire from a source port to a target row
    let temp = null;
    $$('.port.out', canvas).forEach(p => p.addEventListener('pointerdown', e => { e.preventDefault(); const box = canvas.getBoundingClientRect(); const r = p.getBoundingClientRect();
      temp = { key: p.dataset.port, x: r.left + 6 - box.left + canvas.scrollLeft, y: r.top + 6 - box.top + canvas.scrollTop };
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('class', 'temp'); $('#d-wires', canvas).appendChild(path); temp.path = path; }));
    canvas.onpointermove = e => { if (!temp) return; const box = canvas.getBoundingClientRect(); const x = e.clientX - box.left + canvas.scrollLeft, y = e.clientY - box.top + canvas.scrollTop;
      temp.path.setAttribute('d', `M${temp.x} ${temp.y} C${temp.x + 90} ${temp.y} ${x - 90} ${y} ${x} ${y}`); $$('.port-row.drop-ok', canvas).forEach(r => r.classList.remove('drop-ok'));
      document.elementFromPoint(e.clientX, e.clientY)?.closest('.port-row[data-target]')?.classList.add('drop-ok'); };
    canvas.onpointerup = e => { if (!temp) return; const row = document.elementFromPoint(e.clientX, e.clientY)?.closest('.port-row[data-target]'); const key = temp.key; temp.path.remove(); temp = null;
      if (row) { const [sheet, cell] = key.split('!'); bind(row.dataset.target, sheet, cell, row.dataset.line == null ? null : Number(row.dataset.line)); } };
  };
  const bind = (target, sheet, cell, line) => busy('Mapping…', async () => { D.draft = await api(`/api/drafts/${D.draft.id}/bind`, { method: 'POST', body: { version: D.draft.version, order: Math.min(0, D.draft.orders.length), target, sheet, cell, line } }); draw(); });
  draw();
  const save = document.createElement('div'); save.className = 'card-pad stack'; save.innerHTML = `<label class="field">Save this mapping as a template<input id="d-tname" placeholder="e.g. ABC Textiles order"></label><div class="row"><button class="btn primary small" id="d-tsave">${icon('save')} Save template</button><a class="btn small" href="#/new/${esc(D.id)}">Open preview</a></div>`;
  $('#d-templates', body).prepend(save); paintIcons(save);
  $('#d-tsave', body).onclick = () => busy('Saving template…', async () => { const r = await api('/api/templates', { method: 'POST', body: { draft_id: D.id, name: $('#d-tname', body).value.trim() } }); toast(`Template “${r.name}” saved (version ${r.version}).`); go(`#/database/designer/${D.id}`); });
}
function templatesHtml(templates) {
  if (!templates.length) return '<div class="empty">No templates yet. Map a workbook and save it — the next file with the same layout is mapped automatically.</div>';
  return `<div class="table-wrap"><table><tbody>${templates.map(t => `<tr><td><strong>${esc(t.name)}</strong> <span class="badge">v${t.version}</span><span class="sub">${plural(t.bindings.length, 'mapped cell')} · ${when(t.created_at)}</span></td><td class="num">${t.active ? '<span class="badge green">Active</span>' : `<button class="btn small" data-activate="${t.id}">${templates.some(x => x.name === t.name && x.active) ? 'Roll back to this' : 'Activate'}</button>`}${t.active ? ` <button class="btn small ghost" data-deactivate="${t.id}">Turn off</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
}
function wireTemplates(body) {
  $$('[data-activate],[data-deactivate]', body).forEach(b => b.onclick = () => busy('Updating template…', async () => { await api(`/api/templates/${b.dataset.activate || b.dataset.deactivate}/active`, { method: 'POST', body: { active: !!b.dataset.activate } }); navigate(); }));
}
