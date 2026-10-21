'use strict';
/* Database: flow diagram of real relationships, table explorer, guided table creation,
   relationship designer and the node-based Excel → Oracle mapping designer. */
const DB = { tab: 'flow', graph: null, orders: null, order: null, detail: null, view: { x: 40, y: 30, k: 1 }, q: '', rels: true, sel: null, ptab: 'data' };
const DB_TABS = [['flow', 'Structure', 'flow'], ['tables', 'Tables', 'table'], ['links', 'Relationships', 'link'], ['designer', 'Mapping designer', 'sparkle'], ['catalog', 'Catalog', 'shield']];
const LOOKUPS = new Set(['BUYMAS', 'STYLENUM', 'STYLEMAS', 'SIZE_MAS', 'COLORMAS', 'UNITMAS', 'MERCHMAS', 'PARTYMAS']);
const NODE_ICON = { BUYMAS: 'users', PARTYMAS: 'users', STYLEMAS: 'shirt', STYLENUM: 'shirt', ORDSTYLE: 'shirt', ORDSIZE: 'grid', ORDSHIP: 'truck', ORDDOC: 'file', ORDFAB: 'layers', ORDYARN: 'activity', COLORMAS: 'sparkle', SIZE_MAS: 'grid' };

route('database', async (view, [tab, arg]) => {
  DB.tab = DB_TABS.some(t => t[0] === tab) ? tab : 'flow';
  setTitle('Database', 'Explore your Oracle database structure, relationships, and order-linked records.',
    `<div class="seg">${DB_TABS.map(([k, l, ic]) => `<button type="button" data-tab="${k}" class="${DB.tab === k ? 'active' : ''}">${icon(ic)} ${l}</button>`).join('')}</div>
     <span class="chip-box">${icon('database')}<span><small>Schema</small>${esc(App.state.health?.database?.owner || 'GARMENT_DEMO')}</span></span>`);
  DB.graph = await api('/api/db/graph');
  page(view, '<div id="db-body"></div>');
  $$('[data-tab]', view).forEach(b => b.onclick = () => go(`#/database/${b.dataset.tab}`));
  const body = $('#db-body', view);
  if (DB.tab === 'flow') return flowTab(body, arg ? Number(arg) : DB.order);
  const bar = document.createElement('div'); bar.className = 'row'; bar.style.justifyContent = 'flex-end'; bar.style.marginBottom = '12px';
  bar.innerHTML = `<button class="btn primary" id="create-table" type="button">${icon('plus')} Create Table</button>`; body.before(bar); paintIcons(bar);
  $('#create-table', view).onclick = () => createTable();
  if (DB.tab === 'tables') return tablesTab(body, arg);
  if (DB.tab === 'links') return linksTab(body, arg);
  if (DB.tab === 'catalog') return catalogTab(body, arg);
  return designerTab(body, arg);
});

/* ------------------------------------------------------------------ structure: diagram + docked panel */
const NW = 210, NH = 78;
function radial(tables, edges, main) {
  const pos = {}; const others = tables.filter(t => t.name !== main);
  const linked = new Set(edges.flatMap(e => [e.parent, e.child]));
  const near = others.filter(t => linked.has(t.name)), far = others.filter(t => !linked.has(t.name));
  pos[main] = { x: 0, y: 0 };
  const ring = near.length; const rx = Math.max(300, 90 * ring), ry = Math.max(190, 46 * ring);
  near.sort((a, b) => (LOOKUPS.has(b.name) - LOOKUPS.has(a.name)) || a.name.localeCompare(b.name))
    .forEach((t, i) => { const ang = -Math.PI / 2 + i * 2 * Math.PI / ring; pos[t.name] = { x: Math.round(rx * Math.cos(ang)), y: Math.round(ry * Math.sin(ang)) }; });
  far.forEach((t, i) => { pos[t.name] = { x: -rx + (i % 4) * (NW + 30), y: ry + 150 + Math.floor(i / 4) * (NH + 26) }; });
  return pos;
}

async function flowTab(body, orderId) {
  DB.orders = await api('/api/orders').catch(() => []);
  const main = DB.graph.mode === 'table' ? DB.graph.order_table : 'ORDMAS';
  if (!DB.order || !DB.orders.some(o => o.ordmasid === DB.order)) DB.order = orderId || DB.orders[0]?.ordmasid || null;
  if (orderId) DB.order = orderId;
  DB.sel = DB.sel && DB.graph.tables.some(t => t.name === DB.sel) ? DB.sel : main;
  body.innerHTML = `<section class="card db-toolbar"><label class="search">${icon('search')}<input id="f-q" placeholder="Search tables (e.g. ${esc(main)})" value="${esc(DB.q)}" aria-label="Search tables"></label>
      <select aria-label="Schema"><option>${esc(App.state.health?.database?.owner || 'GARMENT_DEMO')}</option></select>
      <label class="toggle"><input type="checkbox" id="f-rels" ${DB.rels ? 'checked' : ''}> Show Relationships</label>
      <span class="zoom-box"><button type="button" id="z-out" aria-label="Zoom out">−</button><span id="z-pct">100%</span><button type="button" id="z-in" aria-label="Zoom in">+</button></span>
      <button class="icon-btn" id="z-fit" title="Fit to screen" aria-label="Fit to screen">${icon('fit')}</button>
      <span style="flex:1"></span><button class="btn primary" id="create-table" type="button">${icon('plus')} Create Table</button>
      <a class="btn" href="#/database/designer">${icon('sparkle')} Open Mapping Designer</a></section>
    <div class="db2"><section class="card canvas-card"><div class="card-title" style="position:absolute;z-index:3;background:rgba(255,255,255,.92);border-radius:14px 0 14px 0"><span class="tile-ico">${icon('database')}</span><div><h2>Order Data Structure</h2><p>Tables and relationships for orders in ${esc(App.state.health?.database?.owner || 'GARMENT_DEMO')}</p></div></div>
        <div class="legend-row" style="position:absolute;right:16px;top:20px;z-index:3"><span><i style="background:#0f8a5f"></i>Main Table</span><span><i style="background:#7fd1a8"></i>Related Table</span><span><i style="background:#8fb4ee"></i>Lookup Table</span></div>
        <svg class="canvas" id="f-svg" role="img" aria-label="Database structure diagram"><g id="f-vp"></g></svg></section>
      <aside class="panel-dock" id="dock"></aside></div>`;
  paintIcons(body);
  $('#create-table', body).onclick = () => createTable();
  const pos = radial(DB.graph.tables, DB.graph.relationships, main);
  const svg = $('#f-svg', body), vp = $('#f-vp', body);
  const applyView = () => { vp.setAttribute('transform', `translate(${DB.view.x},${DB.view.y}) scale(${DB.view.k})`); $('#z-pct', body).textContent = `${Math.round(DB.view.k * 100)}%`; };
  const draw = () => {
    const q = DB.q.trim().toUpperCase(); const counts = orderCounts();
    const edges = !DB.rels ? '' : DB.graph.relationships.filter(e => pos[e.parent] && pos[e.child] && e.parent !== e.child).map(e => {
      const a = pos[e.parent], b = pos[e.child]; const x1 = a.x + NW / 2, y1 = a.y + NH / 2, x2 = b.x + NW / 2, y2 = b.y + NH / 2; const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      return `<path class="edge ${e.kind}" d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" marker-end="url(#arrow)"><title>${esc(e.parent)}.${esc(e.parent_column)} → ${esc(e.child)}.${esc(e.child_column)}</title></path>
        <rect x="${mx - 42}" y="${my - 15}" width="84" height="30" rx="7" fill="#fff" stroke="#e2ebe6"/><text class="rel-label" x="${mx}" y="${my - 2}" text-anchor="middle">${esc(e.child_column.slice(0, 12))}</text><text class="rel-label" x="${mx}" y="${my + 10}" text-anchor="middle">1 : M</text>`; }).join('');
    const nodes = DB.graph.tables.map(t => { const p = pos[t.name]; const kind = t.name === main ? 'main' : LOOKUPS.has(t.name) ? 'lookup' : 'related';
      const dim = q && !t.name.includes(q) && !String(t.label).toUpperCase().includes(q); const c = counts[t.name];
      return `<g class="node ${kind} ${DB.sel === t.name ? 'selected' : ''} ${dim ? 'dim' : ''} ${q && !dim ? 'match' : ''}" data-node="${esc(t.name)}" transform="translate(${p.x},${p.y})" tabindex="0" role="button" aria-label="${esc(t.name)}">
        <rect class="box" width="${NW}" height="${NH}" rx="12"/><rect class="nicon" x="14" y="17" width="44" height="44" rx="10"/>
        <g transform="translate(24,27) scale(1)"><path class="nicon-path" d="${ICONS[NODE_ICON[t.name] || 'database']}"/></g>
        <text class="t1" x="70" y="32">${esc(t.name.length > 16 ? t.name.slice(0, 15) + '…' : t.name)}</text><text class="t2" x="70" y="50">${esc((t.label !== t.name ? t.label : plural(t.columns, 'column')).slice(0, 22))}</text>
        <text class="t2" x="70" y="65" style="font-size:10.5px">${kind === 'main' ? (c ? `${c} record${c === 1 ? '' : 's'} selected` : 'Main table') : c ? `${c} linked` : esc((t.purpose || '').slice(0, 24))}</text></g>`; }).join('');
    vp.innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#8aa398"/></marker></defs>${edges}${nodes}`;
    $$('[data-node]', vp).forEach(n => { const pick = () => { DB.sel = n.dataset.node; draw(); dock(); }; n.addEventListener('click', pick); n.addEventListener('keydown', e => { if (e.key === 'Enter') pick(); }); });
    applyView();
  };
  const fit = () => { const xs = Object.values(pos); const minX = Math.min(...xs.map(p => p.x)), minY = Math.min(...xs.map(p => p.y)); const w = Math.max(...xs.map(p => p.x)) + NW - minX, h = Math.max(...xs.map(p => p.y)) + NH - minY;
    const r = svg.getBoundingClientRect(); const k = Math.min(1.1, (r.width - 60) / w, (r.height - 150) / h); DB.view = { k, x: (r.width - w * k) / 2 - minX * k, y: 100 + (r.height - 130 - h * k) / 2 - minY * k }; applyView(); };
  const zoom = f => { const r = svg.getBoundingClientRect(); const cx = r.width / 2, cy = r.height / 2; const k = Math.min(2.5, Math.max(.3, DB.view.k * f)); DB.view = { k, x: cx - (cx - DB.view.x) * k / DB.view.k, y: cy - (cy - DB.view.y) * k / DB.view.k }; applyView(); };
  $('#z-in', body).onclick = () => zoom(1.2); $('#z-out', body).onclick = () => zoom(1 / 1.2); $('#z-fit', body).onclick = fit;
  svg.addEventListener('wheel', e => { e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1); }, { passive: false });
  let drag = null;
  svg.addEventListener('pointerdown', e => { if (e.target.closest('[data-node]')) return; drag = { x: e.clientX, y: e.clientY, vx: DB.view.x, vy: DB.view.y }; svg.classList.add('dragging'); svg.setPointerCapture(e.pointerId); });
  svg.addEventListener('pointermove', e => { if (!drag) return; DB.view.x = drag.vx + e.clientX - drag.x; DB.view.y = drag.vy + e.clientY - drag.y; applyView(); });
  svg.addEventListener('pointerup', () => { drag = null; svg.classList.remove('dragging'); });
  $('#f-q', body).addEventListener('input', e => { DB.q = e.target.value; draw(); });
  $('#f-rels', body).addEventListener('change', e => { DB.rels = e.target.checked; draw(); });

  async function dock() {
    const el = $('#dock', body); const t = DB.graph.tables.find(x => x.name === DB.sel); if (!t) { el.innerHTML = ''; return; }
    const rels = DB.graph.relationships.filter(e => e.parent === t.name || e.child === t.name);
    el.innerHTML = `<section class="card"><div class="card-title"><span class="tile-ico ${LOOKUPS.has(t.name) ? 'blue' : ''}">${icon(NODE_ICON[t.name] || 'database')}</span><div><h2>${esc(t.name)}</h2><p>${esc(t.label !== t.name ? t.label : 'Oracle table')}${t.purpose ? ' — ' + esc(t.purpose) : ''}</p></div>
        ${t.name !== main ? `<button class="icon-btn right" id="dock-close" aria-label="Back to main table">${icon('x')}</button>` : ''}</div>
      <div class="seg2"><button type="button" data-ptab="data" class="${DB.ptab === 'data' ? 'active' : ''}">${icon('database')} Data</button><button type="button" data-ptab="visual" class="${DB.ptab === 'visual' ? 'active' : ''}">${icon('chart')} Visualization</button></div>
      <div id="dock-body" class="card-pad"><div class="empty"><span class="spinner"></span></div></div></section>
      <div class="mini-cards" id="dock-mini"></div>`;
    paintIcons(el);
    $('#dock-close', el)?.addEventListener('click', () => { DB.sel = main; draw(); dock(); });
    $$('[data-ptab]', el).forEach(b => b.onclick = () => { DB.ptab = b.dataset.ptab; dock(); });
    const bodyEl = $('#dock-body', el);
    const orderPick = `<div class="row between" style="margin-bottom:10px"><div><h3>Record Preview</h3><small class="muted">${DB.order ? `Records from ${esc(t.name)} for the selected order` : 'No saved orders yet'}</small></div>
      ${DB.orders.length ? `<select id="dock-order" aria-label="Order">${DB.orders.map(o => `<option value="${o.ordmasid}" ${o.ordmasid === DB.order ? 'selected' : ''}>Order No: ${esc(o.order_ref)}</option>`).join('')}</select>` : ''}</div>`;
    try {
      if (DB.ptab === 'visual') { bodyEl.innerHTML = DB.order ? orderDashboard(await loadOrder(DB.order)) : '<div class="empty">Save an order to see its dashboard.</div>'; paintIcons(bodyEl); }
      else if (!DB.order) {
        const r = await api(`/api/db/tables/${encodeURIComponent(t.name)}`);
        bodyEl.innerHTML = orderPick + columnsValues(r.columns, r.rows[0] || {}, null, r.total ? `First of ${fmt(r.total)} records` : 'No records yet');
      } else {
        const [r, d] = await Promise.all([api(`/api/orders/${DB.order}/tables/${encodeURIComponent(t.name)}`), loadOrder(DB.order, true)]); DB.detail = d; draw();
        const first = r.rows[0] || {}; const pk = r.columns.find(c => c.name === 'ENTRY_ID' || c.name === t.name + 'ID')?.name || r.columns[0]?.name;
        bodyEl.innerHTML = orderPick + (r.rows.length ? columnsValues(r.columns, first, d.sources?.[`${t.name}:${first[pk]}`], r.rows.length > 1 ? `Row 1 of ${r.rows.length} for this order${r.via ? ` · linked by ${r.via}` : ''}` : r.via ? `Linked by ${r.via}` : '')
          + (r.rows.length > 1 && t.name === main && DB.graph.mode === 'table' ? `<details class="quiet" style="margin-top:10px"><summary>All ${r.rows.length} size rows</summary><div class="table-wrap" style="max-height:260px;margin-top:8px"><table class="data-table"><thead><tr><th>Colour</th><th>Size</th><th class="num">Qty</th><th>Excel cell</th></tr></thead><tbody>${r.rows.map(x => `<tr><td>${esc(x.COMBO || '—')}</td><td>${esc(x.SIZES || '—')}</td><td class="num">${fmt(x.ORDERQTY)}</td><td class="mono">${esc(x.SOURCE_CELL || '')}</td></tr>`).join('')}</tbody></table></div></details>` : '')
          : '<div class="empty">No records of this order in this table.</div>');
        const src = Object.values(d.sources?.[`${t.name}:${first[pk]}`] || {}).find(x => x?.source?.cell)?.source;
        $('#dock-mini', el).innerHTML = `<section class="card"><div class="row" style="margin-bottom:8px"><span class="tile-ico">${icon('xls')}</span><strong>Source Excel Reference</strong></div>
            ${d.import ? `<strong>${esc(d.import.filename || '')}</strong><div class="muted">${src ? `Sheet: ${esc(src.sheet)}<br>Cell: ${esc(src.cell)}` : 'Excel cells are listed with each value'}</div>${d.import.job_id ? `<a class="btn small" style="margin-top:10px" href="/api/drafts/${esc(d.import.job_id)}/file">Open in Excel ${icon('arrow')}</a>` : ''}` : '<span class="muted">Not saved from Excel in this workspace.</span>'}</section>
          <section class="card"><div class="row" style="margin-bottom:8px"><span class="tile-ico">${icon('link')}</span><strong>Relationships</strong></div>
            ${rels.length ? rels.map(e => `<div class="row between" style="padding:4px 0"><span class="mono" style="font-size:12px">1 : M</span><span>${esc(e.parent === t.name ? e.child : e.parent)}</span>${icon('arrow')}</div>`).join('') : '<span class="muted">No links yet. Add one under Relationships.</span>'}</section>`;
        paintIcons(el);
      }
      $('#dock-order', el)?.addEventListener('change', e => { DB.order = Number(e.target.value); history.replaceState(null, '', `#/database/flow/${DB.order}`); dock(); });
    } catch (e) { bodyEl.innerHTML = `<div class="callout attention">${icon('alert')}<div>${esc(e.message)}</div></div>`; paintIcons(bodyEl); }
  }
  function columnsValues(columns, row, sources, note) {
    const types = Object.fromEntries(columns.map(c => [c.name, c.type + (c.type.includes('CHAR') ? `(${c.length})` : c.precision ? `(${c.precision}${c.scale ? ',' + c.scale : ''})` : '')]));
    const names = Object.keys(row).filter(k => row[k] != null);
    return `${note ? `<small class="muted">${esc(note)}</small>` : ''}<div class="table-wrap" style="max-height:440px;margin-top:6px"><table class="data-table"><thead><tr><th>Column Name</th><th>Value</th><th>Data Type</th></tr></thead><tbody>
      ${names.map(k => `<tr><td><span class="col-name">${esc(k)}</span></td><td>${esc(val(k, row[k]))}${sources?.[k]?.source?.cell ? ` <span class="chip">${esc(sources[k].source.cell)}</span>` : ''}</td><td class="muted mono" style="font-size:11.5px">${esc(types[k] || '')}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">Empty</td></tr>'}</tbody></table></div>`;
  }
  DB.redraw = draw; draw(); fit(); dock();
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
  const tables = DB.graph.tables; selected = selected || (DB.graph.mode === 'table' ? DB.graph.order_table : 'ORDMAS');
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
  const tables = DB.graph.tables.map(t => t.name); const L = { parent: DB.graph.mode === 'table' ? DB.graph.order_table : 'ORDMAS', child: preset && tables.includes(preset) ? preset : '', pc: null, cc: null, kind: 'application', result: null };
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

/* ------------------------------------------------------------------ catalog: table and column ID cards */
const STATUS_TEXT = { DISCOVERED: ['Discovered', ''], DOCUMENTED: ['Documented', 'blue'], RELATIONSHIPS_VERIFIED: ['Joins verified', 'violet'], BUSINESS_APPROVED: ['Business approved', 'amber'], AI_READY: ['AI ready', 'green'] };
const statusBadge = st => `<span class="badge ${STATUS_TEXT[st]?.[1] || ''}">${esc(STATUS_TEXT[st]?.[0] || st)}</span>`;
async function catalogTab(body, open) {
  let cat = await api('/api/catalog'); let q = ''; let metrics = await api('/api/metrics');
  const draw = () => {
    const list = cat.tables.filter(t => !q || `${t.name} ${t.business_name || ''} ${t.purpose || ''}`.toLowerCase().includes(q.toLowerCase()));
    const counts = Object.fromEntries(cat.statuses.map(st => [st, cat.tables.filter(t => t.status === st).length]));
    body.innerHTML = `<div class="stack">
      <div class="callout info">${icon('info')}<div><strong>The data catalog is the foundation for a future AI assistant.</strong> Every Oracle table gets an ID card. Structure is read from Oracle automatically; meaning (business name, purpose, what one row is) is written by you. Only <strong>AI ready</strong> tables, verified joins and approved metrics will ever be used to answer questions.</div></div>
      <div class="row between"><div class="row">${cat.statuses.map(st => `${statusBadge(st)} <span class="muted">${counts[st]}</span>`).join(' &nbsp; ')}</div>
        <button class="btn primary" id="c-refresh" type="button">${icon('database')} Refresh from Oracle</button></div>
      ${cat.changes.length ? `<section class="card"><div class="card-head"><h3>Oracle structure changes</h3><span class="sub">Detected automatically on each refresh</span></div><ul class="timeline">${cat.changes.map(c => `<li><span class="dot"></span><span>${c.tables} tables · ${c.columns} columns${c.added.length ? ` · <strong>new:</strong> ${esc(c.added.slice(0, 8).join(', '))}${c.added.length > 8 ? ` +${c.added.length - 8}` : ''}` : ''}${c.removed.length ? ` · <strong>removed:</strong> ${esc(c.removed.join(', '))}` : ''}${c.changed.length ? ` · <strong>changed:</strong> ${esc(c.changed.join(', '))}` : ''}</span><time>${when(c.taken_at)}</time></li>`).join('')}</ul></section>` : ''}
      <div class="split"><section class="card"><div class="toolbar"><label class="search">${icon('search')}<input id="c-q" placeholder="Search tables by name or meaning" value="${esc(q)}"></label></div>
        ${list.length ? `<div class="table-wrap" style="max-height:620px"><table><thead><tr><th>Table</th><th>Business name</th><th>One row is</th><th>Status</th></tr></thead><tbody>${list.slice(0, 300).map(t => `<tr class="clickable" data-card="${esc(t.name)}"><td><span class="col-name">${esc(t.name)}</span><span class="col-label">${t.columns} columns${t.row_estimate != null ? ` · ≈${fmt(t.row_estimate)} rows` : ''}</span></td><td>${esc(t.business_name || '—')}</td><td class="muted">${esc(t.grain || '—')}</td><td>${statusBadge(t.status)}</td></tr>`).join('')}</tbody></table></div>${list.length > 300 ? `<p class="faint card-pad">Showing 300 of ${fmt(list.length)}. Search to narrow.</p>` : ''}`
          : `<div class="empty">${cat.tables.length ? 'No table matches.' : 'The catalog is empty. Press “Refresh from Oracle”.'}</div>`}</section>
      <section class="card"><div class="card-head"><div><h2>Business metrics</h2><span class="sub">Approved definitions a future AI must use</span></div></div>
        ${metrics.length ? metrics.map(m => `<div class="list-row"><div class="grow"><strong>${esc(m.name)} <span class="badge">v${m.version}</span> <span class="badge ${m.status === 'APPROVED' ? 'green' : m.status === 'PROPOSED' ? 'amber' : ''}">${esc(m.status.toLowerCase())}</span></strong>
          <small class="muted" style="display:block;white-space:normal">${esc(m.definition)}${m.formula ? ` · <code>${esc(m.formula)}</code>` : ''}${m.duplicate_handling ? `<br>⚠ ${esc(m.duplicate_handling)}` : ''}</small></div>${m.status === 'PROPOSED' ? `<button class="btn small" data-approve-metric="${m.id}">Approve</button>` : ''}</div>`).join('') : '<div class="empty">No metrics yet. Refresh the catalog to get the proposed starting definitions.</div>'}</section></div></div>`;
    paintIcons(body);
    $('#c-q', body).addEventListener('input', e => { q = e.target.value; const pos = e.target.selectionStart; draw(); const i = $('#c-q', body); i.focus(); i.setSelectionRange(pos, pos); });
    $('#c-refresh', body).onclick = () => busy('Reading the Oracle structure…', async () => { const r = await api('/api/catalog/refresh', { method: 'POST' }); cat = await api('/api/catalog'); metrics = await api('/api/metrics');
      toast(`Catalog refreshed: ${fmt(r.tables)} tables${r.added.length ? `, ${r.added.length} new` : ''}${r.changed.length ? `, ${r.changed.length} changed` : ''}.`); draw(); });
    $$('[data-card]', body).forEach(r => r.onclick = () => openCard(r.dataset.card, async () => { cat = await api('/api/catalog'); draw(); }));
    $$('[data-approve-metric]', body).forEach(b => b.onclick = async () => { if (await confirmDialog('Approve this metric?', 'A future AI will use exactly this definition when the metric is asked for.', 'Approve')) busy('Approving…', async () => { await api(`/api/metrics/${b.dataset.approveMetric}/approve`, { method: 'POST' }); metrics = await api('/api/metrics'); draw(); }); });
  };
  draw(); if (open) openCard(open.toUpperCase(), async () => { cat = await api('/api/catalog'); draw(); });
}

function openCard(name, onSaved) {
  Drawer.open({ eyebrow: 'Table ID card', title: name, wide: true, render: async () => {
    const c = await api(`/api/catalog/tables/${encodeURIComponent(name)}`); const next = STATUS_TEXT && ['DISCOVERED', 'DOCUMENTED', 'RELATIONSHIPS_VERIFIED', 'BUSINESS_APPROVED', 'AI_READY'];
    const idx = next.indexOf(c.status); const nextStatus = next[idx + 1];
    return `<div class="row">${next.map((st, i) => `<span class="badge ${i <= idx ? (STATUS_TEXT[st][1] || 'green') : ''}">${i < idx ? '✓ ' : ''}${esc(STATUS_TEXT[st][0])}</span>`).join('<span class="arrow">→</span>')}</div>
      <section class="card card-pad stack" style="gap:10px"><div class="grid g2"><label class="field">Business name<input id="k-name" value="${esc(c.business_name || '')}" placeholder="e.g. Excel orders"></label>
        <label class="field">Business domain<input id="k-domain" value="${esc(c.domain || '')}" placeholder="e.g. Merchandising"></label></div>
        <label class="field">Purpose<textarea id="k-purpose" rows="2" placeholder="What this table is for">${esc(c.purpose || '')}</textarea></label>
        <label class="field">One row is (grain)<input id="k-grain" value="${esc(c.grain || '')}" placeholder="e.g. one size of one order line"></label>
        <div class="row"><span class="muted">Schema ${esc(c.schema)} · first seen ${when(c.first_seen)} · ${c.row_estimate != null ? `≈${fmt(c.row_estimate)} rows` : 'row count not gathered'}</span></div></section>
      <section class="card"><div class="card-head"><h3>Ready for AI? Checklist</h3></div><div class="status-list">${c.checklist.map(x => `<div>${x.done ? `<span class="badge green">${icon('check')}</span>` : '<span class="badge">○</span>'} ${esc(x.step)}</div>`).join('')}</div></section>
      <section class="card"><div class="card-head"><div><h3>Column ID cards</h3><span class="sub">Type, nullability and keys come from Oracle; names, synonyms and meaning from you</span></div></div>
        <div class="table-wrap" style="max-height:420px"><table class="coledit"><thead><tr><th>Column</th><th>Business name</th><th>Synonyms (comma separated)</th><th>Meaning</th><th>Unit</th><th>Sensitive</th></tr></thead><tbody>
        ${c.columns.map(col => `<tr data-col="${esc(col.column_name)}"><td><span class="col-name">${esc(col.column_name)}</span><span class="col-label">${esc(col.data_type)}${col.nullable === 'N' ? ' · required' : ''}${col.is_key ? ' · key' : ''}</span></td>
          <td><input data-k="business_name" value="${esc(col.business_name || '')}"></td><td><input data-k="synonyms" value="${esc((col.synonyms || []).join(', '))}"></td>
          <td><input data-k="description" value="${esc(col.description || '')}"></td><td><input data-k="unit" value="${esc(col.unit || '')}" style="width:80px"></td><td style="text-align:center"><input type="checkbox" data-k="sensitive" ${col.sensitive ? 'checked' : ''}></td></tr>`).join('')}</tbody></table></div></section>
      ${c.relationships.length ? `<section class="card"><div class="card-head"><h3>Joins</h3></div><div class="table-wrap"><table><tbody>${c.relationships.map(r => `<tr><td class="mono">${esc(r.parent)}.${esc(r.parent_column)} → ${esc(r.child)}.${esc(r.child_column)}</td><td>${r.join_verified ? '<span class="badge green">verified</span>' : '<span class="badge amber">not verified</span>'}</td></tr>`).join('')}</tbody></table></div></section>` : ''}
      <div class="row"><button class="btn" id="k-save">${icon('save')} Save</button>${nextStatus ? `<button class="btn primary" id="k-next">${icon('arrow')} Save and mark “${esc(STATUS_TEXT[nextStatus][0])}”</button>` : '<span class="badge green">AI ready</span>'}</div>`;
  }, after: body => {
    const payload = status => ({ business_name: $('#k-name', body).value, domain: $('#k-domain', body).value, purpose: $('#k-purpose', body).value, grain: $('#k-grain', body).value,
      ...(status ? { status } : {}), columns: $$('[data-col]', body).map(r => ({ column_name: r.dataset.col, business_name: $('[data-k=business_name]', r).value, synonyms: $('[data-k=synonyms]', r).value.split(',').map(x => x.trim()).filter(Boolean),
        description: $('[data-k=description]', r).value, unit: $('[data-k=unit]', r).value, sensitive: $('[data-k=sensitive]', r).checked })) });
    const save = status => busy('Saving the ID card…', async () => { await api(`/api/catalog/tables/${encodeURIComponent(name)}`, { method: 'POST', body: payload(status) }); toast('ID card saved.'); Drawer.show(null); onSaved?.(); });
    $('#k-save', body)?.addEventListener('click', () => save(null));
    $('#k-next', body)?.addEventListener('click', () => { const order = ['DISCOVERED', 'DOCUMENTED', 'RELATIONSHIPS_VERIFIED', 'BUSINESS_APPROVED', 'AI_READY']; save(null).then(async () => {
      const c = await api(`/api/catalog/tables/${encodeURIComponent(name)}`); save(order[order.indexOf(c.status) + 1]); }); });
  } });
}
