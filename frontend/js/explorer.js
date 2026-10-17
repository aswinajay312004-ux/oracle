// Read-only GARMENT_DEMO explorer: business names, records with decoded master labels, linked records
// (parents and children through the relationship map), and an expandable relationship diagram.
import { $, $$, esc, show, fmt, plural, busy, drawer, notify, formView } from './util.js';
import { api, id } from './api.js';
import { renderGraph } from './graph.js';

let tables = []; let current = null; let tab = 'records';
const GROUPS = [['header', 'Order tables'], ['detail', 'Order tables'], ['master', 'Masters'], ['local', 'Local'], ['other', 'Other tables']];

export async function loadExplorer(force = false) {
  if (tables.length && !force) { drawTables(); if (current) return load(); return; }
  const ok = await busy('Reading the table list from Oracle…', async () => {
    try { tables = await api('/api/explorer/tables'); }
    catch (e) { $('#tables').innerHTML = `<p class="empty">${esc(e.message)}</p>`; $('#table-view').innerHTML = '<p class="empty">Connect Oracle to browse tables.</p>'; return false; }
    drawTables(); return true;
  });
  if (ok && !current && tables.some(t => t.table === 'ORDMAS')) await openTable('ORDMAS');
}

function drawTables() {
  const q = $('#table-filter').value.trim().toUpperCase();
  const list = tables.filter(t => !q || t.table.includes(q) || (t.business || '').toUpperCase().includes(q));
  const seen = new Set(); let html = '';
  for (const [kind, title] of GROUPS) {
    const items = list.filter(t => (t.kind === kind) || (kind === 'other' && !['header', 'detail', 'master', 'local'].includes(t.kind)));
    if (!items.length) continue;
    if (!seen.has(title)) { html += `<div class="tl-group">${title}</div>`; seen.add(title); }
    html += items.sort((a, b) => a.table.localeCompare(b.table)).map(t => `<button class="tl-item ${current?.table === t.table ? 'active' : ''}" data-table="${esc(t.table)}" title="${esc(t.description || '')}">
      <b>${esc(t.business || t.table)}</b><span class="n" title="${t.approx ? 'Approximate (Oracle statistics)' : 'Exact row count'}">${t.rows == null ? '' : (t.approx ? '≈ ' : '') + fmt(t.rows, 0)}</span><code>${esc(t.table)}</code></button>`).join('');
  }
  $('#tables').innerHTML = html || '<p class="empty">No tables match.</p>';
}

export async function openTable(table, params = {}) {
  current = { table, column: params.column || '', value: params.value || '', after: null, pages: [] };
  tab = params.tab || 'records';
  drawTables(); await load();
}

async function load(more = false) {
  await busy(`Reading ${current.table}…`, async () => {
    const qs = new URLSearchParams({ limit: 50, ...(current.column && current.value ? { column: current.column, value: current.value } : {}), ...(more && current.after != null ? { after: current.after } : {}), ...(more && current.data && !current.data.key ? { offset: current.data.offset + 50 } : {}) });
    const data = await api(`/api/explorer/tables/${id(current.table)}?${qs}`);
    if (more && current.data) { data.rows = current.data.rows.concat(data.rows); Object.entries(data.decoded).forEach(([c, m]) => { data.decoded[c] = { ...(current.data.decoded[c] || {}), ...m }; }); Object.entries(current.data.decoded).forEach(([c, m]) => { data.decoded[c] ||= m; }); }
    current.data = data; current.after = data.next_after;
    draw();
  });
}

function cellHtml(data, col, v) {
  const label = data.decoded?.[col]?.[String(v)];
  if (v == null || v === '') return '<span class="muted">—</span>';
  if (label != null) return `${esc(label)} <span class="chip label">${esc(v)}</span>`;
  return esc(show(v));
}

function draw() {
  const d = current.data; const meta = tables.find(t => t.table === current.table) || {};
  const cols = d.columns.filter(c => c.role !== 'system');
  const sys = d.columns.filter(c => c.role === 'system');
  $('#table-view').innerHTML = `<div class="ex-head"><div style="flex:1;min-width:0"><div class="row wrap"><h2>${esc(d.business || d.table)}</h2><code class="muted">${esc(d.table)}</code>
      <span class="pill">${d.total == null ? '' : (d.approx ? '≈ ' : '') + plural(d.total, 'row')}</span>${meta.links ? `<span class="pill brand">${plural(meta.links, 'link')}</span>` : ''}</div>
      <p class="muted small-text" style="margin-top:2px">${esc(d.description || '')} · read-only</p></div></div>
    <nav class="ex-tabs tabs">${[['records', 'Records'], ['columns', `Columns (${d.columns.length})`], ['links', 'Relationships']].map(([k, n]) => `<button class="${tab === k ? 'active' : ''}" data-xtab="${k}">${n}</button>`).join('')}</nav>
    <div data-xbody></div>`;
  const body = $('[data-xbody]');
  if (tab === 'records') {
    body.innerHTML = `<form class="filter-bar" id="filter-form"><div class="f"><label class="f-label" for="f-col">Column</label><select id="f-col"><option value="">— choose —</option>${cols.map(c => `<option value="${esc(c.name)}" ${c.name === current.column ? 'selected' : ''}>${esc(c.label || c.name)} (${esc(c.name)})</option>`).join('')}</select></div>
        <div class="f"><label class="f-label" for="f-val">Contains</label><input id="f-val" value="${esc(current.value)}" maxlength="100"></div><button class="secondary">Filter</button>${current.value ? '<button type="button" class="ghost" id="f-clear">Clear</button>' : ''}
        <span class="spacer"></span>${sys.length ? `<label class="check"><input type="checkbox" id="show-sys" ${current.showSys ? 'checked' : ''}> Show Axpert system columns (${sys.length})</label>` : ''}</form>
      <div class="grid-scroll"><table><thead><tr>${(current.showSys ? d.columns : cols).map(c => `<th title="${esc(c.type)}">${esc(c.label || c.name)}<span class="code">${esc(c.name)}</span></th>`).join('')}</tr></thead><tbody>
        ${d.rows.map((r, i) => `<tr class="clickable" data-row="${i}">${(current.showSys ? d.columns : cols).map(c => `<td>${cellHtml(d, c.name, r[c.name])}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${cols.length}" class="empty">No records.</td></tr>`}
      </tbody></table></div>
      <div class="pager"><span>${plural(d.rows.length, 'row')} shown${d.total != null ? ` of ${d.approx ? '≈ ' : ''}${fmt(d.total, 0)}` : ''} · newest first · click a row to see linked records</span>
        ${(d.key ? d.next_after != null : d.rows.length < (d.total || 0)) ? '<button class="secondary small" id="more">Load more</button>' : ''}</div>`;
    $('#filter-form').onsubmit = e => { e.preventDefault(); openTable(current.table, { column: $('#f-col').value, value: $('#f-val').value }); };
    $('#f-clear')?.addEventListener('click', () => openTable(current.table));
    $('#show-sys')?.addEventListener('change', e => { current.showSys = e.target.checked; draw(); });
    $('#more')?.addEventListener('click', () => load(true));
    $$('#table-view tr[data-row]').forEach(tr => tr.onclick = () => openRecord(current.table, d.rows[Number(tr.dataset.row)], d));
  } else if (tab === 'columns') {
    body.innerHTML = `<div class="grid-scroll"><table><thead><tr><th>Business name</th><th>Column</th><th>Type</th><th>Role</th></tr></thead><tbody>
      ${d.columns.map(c => `<tr><td><b>${esc(c.label || '—')}</b></td><td class="mono">${esc(c.name)}</td><td class="muted">${esc(c.type)}</td><td>${c.role ? `<span class="pill ${c.role === 'system' ? '' : c.role === 'reference' ? 'master' : c.role === 'measure' ? 'brand' : 'info'}">${esc(c.role)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
  } else {
    body.innerHTML = `<div class="flow-wrap" data-rel style="min-height:240px"></div><div class="legend"><span>Click a table to open it · ＋ adds its linked tables</span><span><i style="background:#d39a2c"></i>Unconfirmed link</span></div>`;
    relationshipMap(body.querySelector('[data-rel]'), current.table);
  }
  $$('[data-xtab]').forEach(b => b.onclick = () => { tab = b.dataset.xtab; draw(); });
}

async function relationshipMap(el, focus) {
  const nodes = new Map(); const edges = [];
  const add = async table => {
    const n = await api(`/api/knowledge/neighbours/${id(table)}`);
    if (!nodes.has(table)) nodes.set(table, { id: table, title: n.business, code: table, kind: n.kind, base: table === focus });
    n.neighbours.forEach(x => {
      if (!nodes.has(x.table)) nodes.set(x.table, { id: x.table, title: x.business, code: x.table, kind: x.kind, line3: x.label, status: { text: x.rows != null ? `${fmt(x.rows, 0)} rows` : '', cls: 'muted' } });
      const from = x.direction === 'up' ? x.table : table, to = x.direction === 'up' ? table : x.table;
      if (!edges.some(e => e.from === from && e.to === to)) edges.push({ from, to, label: '', title: `${x.label} · ${x.on} (${x.status})`, cls: x.status === 'verified' || x.status === 'declared' ? 'link' : 'proposed' });
    });
  };
  const draw = () => renderGraph(el, { nodes: [...nodes.values()], edges }, { selected: focus, onSelect: t => { if (t !== current.table) openTable(t, { tab: 'links' }); },
    onExpand: async t => { try { await add(t); draw(); } catch (e) { notify(e.message, 'error'); } }, nodeW: 200, nodeH: 72, gapX: 80 });
  try { await add(focus); draw(); } catch (e) { el.innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

async function openRecord(table, row, data) {
  const meta = data || { columns: Object.keys(row).map(n => ({ name: n })), decoded: {} };
  const fields = meta.columns.filter(c => c.role !== 'system' && row[c.name] != null && row[c.name] !== '')
    .map(c => ({ label: c.label || c.name, code: c.name, html: cellHtml(meta, c.name, row[c.name]) }));
  const body = drawer(`${meta.business || table} record`, `${formView(fields)}<div class="record-sections" data-related><p class="muted">Loading linked records…</p></div>`);
  const key = meta.key || tables.find(t => t.table === table)?.key;
  const pk = row[key] ?? row[`${table}ID`];
  if (pk == null) { body.querySelector('[data-related]').innerHTML = '<p class="muted">This table has no single record ID, so linked records cannot be listed.</p>'; return; }
  try {
    const rel = await api(`/api/explorer/related/${id(table)}/${id(pk)}`);
    body.querySelector('[data-related]').innerHTML = rel.related.length ? rel.related.map((g, gi) => {
      const cols = g.columns.filter(c => g.rows.some(r => r[c.name] != null && r[c.name] !== '')).slice(0, 10);
      return `<section class="rel-section"><h4>${g.direction === 'up' ? '↑ Belongs to' : '↓ Has'} ${esc(g.business)} <code class="muted">${esc(g.table)}</code><span class="pill ${g.status === 'verified' || g.status === 'declared' ? 'ok' : 'warn'}">${esc(g.status === 'verified' || g.status === 'declared' ? 'confirmed link' : 'unconfirmed link')}</span><span class="muted small-text">${esc(g.on)}</span></h4>
        <div class="tbl-wrap" style="max-height:240px"><table><thead><tr>${cols.map(c => `<th>${esc(c.label)}<span class="code">${esc(c.name)}</span></th>`).join('')}</tr></thead><tbody>
        ${g.rows.map((r, ri) => `<tr class="clickable" data-rel-open="${gi}:${ri}">${cols.map(c => `<td>${cellHtml(g, c.name, r[c.name])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${g.more ? '<p class="muted small-text">Showing the first 20.</p>' : ''}</section>`;
    }).join('') : '<p class="muted">No linked records found.</p>';
    $$('[data-rel-open]', body).forEach(tr => tr.onclick = () => {
      const [gi, ri] = tr.dataset.relOpen.split(':').map(Number); const g = rel.related[gi];
      openRecord(g.table, g.rows[ri], { business: g.business, key: g.key, columns: g.columns, decoded: g.decoded });
    });
  } catch (e) { body.querySelector('[data-related]').innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

export async function exploreOrder(ref) {
  ref = String(ref).trim(); if (!ref) return;
  if (/^\d+$/.test(ref)) {
    await busy('Collecting every row linked to this order…', async () => {
      const rows = await api(`/api/explorer/tables/ORDMAS?limit=1&column=ORDMASID&value=${id(ref)}`);
      const row = rows.rows.find(r => String(r.ORDMASID) === ref);
      if (!row) return notify(`No order with record ID ${ref}.`, 'warning');
      openRecord('ORDMAS', row, rows);
    });
  } else {
    if (!tables.length) await loadExplorer();
    await openTable('ORDMAS', { column: 'DOCID', value: ref });
  }
}

export function bindExplorer() {
  $('#table-filter').addEventListener('input', drawTables);
  $('#tables').addEventListener('click', e => { const b = e.target.closest('[data-table]'); if (b) openTable(b.dataset.table); });
  $('#order-lookup').addEventListener('submit', e => { e.preventDefault(); exploreOrder($('#order-id').value); });
}
