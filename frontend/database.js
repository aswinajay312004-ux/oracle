'use strict';
/* Database: the order table POPPYS fills (ORDERENTRY), the tables linked to it, their data, columns and links.
   Every chart bar opens the rows behind it. Creating a table is the only structural change, and it needs confirmation. */
const DB = { overview: null, table: null, tab: 'overview', meta: null, filter: null, q: '', offset: 0, by: null };
const DB_TABS = [['overview', 'Overview', 'chart'], ['data', 'Data', 'table'], ['columns', 'Columns', 'grid'], ['links', 'Links', 'link']];
const ROLE_BADGE = { main: ['Main order table', 'green'], linked: ['Linked by mapping', 'blue'], created: ['Created here', 'violet'], browse: ['Read only', ''] };

route('database', async (view, [table, tab]) => {
  setTitle('Database', 'GARMENT_DEMO: the order table POPPYS fills and the tables linked to it.',
    `<button class="btn primary" type="button" id="db-create">${icon('fileplus')} Create table</button>`);
  DB.overview = await api('/api/db/overview');
  const name = (table || DB.table || DB.overview.main).toUpperCase();
  const changed = name !== DB.table;
  if (changed) Object.assign(DB, { table: name, filter: null, q: '', offset: 0, by: null, meta: null });
  DB.tab = DB_TABS.some(([k]) => k === tab) ? tab : (changed || !DB.tab ? 'overview' : DB.tab);
  try { DB.meta = await api(`/api/db/tables/${encodeURIComponent(DB.table)}/columns`); }
  catch (e) { DB.meta = null; }
  const o = DB.overview, known = o.tables.map(t => t.name);
  page(view, `<div class="db">
    <aside class="db-list" aria-label="Tables">
      ${o.tables.map(t => { const [label, tone] = ROLE_BADGE[t.role] || ROLE_BADGE.browse;
        return `<button type="button" class="db-item${t.name === DB.table ? ' active' : ''}" data-table="${esc(t.name)}"><span class="tile-ico ${t.role === 'main' ? '' : 'blue'}">${icon(t.role === 'main' ? 'table' : 'layers')}</span>
          <span><b>${esc(t.name)}</b><small>${esc(t.label || tableLabel(t.name))}</small><small>${plural(t.rows, 'row')} · <span class="badge ${tone}">${esc(label)}</span></small></span></button>`; }).join('')}
      ${!known.includes(DB.table) ? `<button type="button" class="db-item active"><span class="tile-ico violet">${icon('eye')}</span><span><b>${esc(DB.table)}</b><small>${esc(tableLabel(DB.table))}</small><small><span class="badge">Read only</span></small></span></button>` : ''}
      <label class="field" style="margin-top:6px">Browse another table<select id="db-browse"><option value="">Choose a table…</option>${o.all_tables.filter(t => !known.includes(t)).map(t => `<option ${t === DB.table ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></label>
      <p class="muted" style="font-size:12.5px">${plural(o.saved_by_poppys, 'order')} saved by POPPYS${o.last_saved ? `, last on ${when(o.last_saved)}` : ''}.</p>
    </aside>
    <section class="card db-panel">${DB.meta ? headHtml() : `<div class="empty">${icon('alert')}<p>${esc(DB.table)} could not be read.</p></div>`}
      ${DB.meta ? `<div class="tabs" role="tablist">${DB_TABS.map(([k, l, ic]) => `<button type="button" role="tab" data-tab="${k}" class="${DB.tab === k ? 'active' : ''}" aria-selected="${DB.tab === k}">${icon(ic)} ${l}</button>`).join('')}</div>` : ''}
      <div id="db-body"></div></section></div>`);
  $('#db-create', view).onclick = createTable;
  $$('[data-table]', view).forEach(b => b.onclick = () => go(`#/database/${encodeURIComponent(b.dataset.table)}`));
  $('#db-browse', view).onchange = e => e.target.value && go(`#/database/${encodeURIComponent(e.target.value)}`);
  $$('[data-tab]', view).forEach(b => b.onclick = () => { DB.tab = b.dataset.tab; history.replaceState(null, '', `#/database/${encodeURIComponent(DB.table)}/${DB.tab}`); showTab(view); });
  if (DB.meta) showTab(view);
});

function headHtml() {
  const m = DB.meta, filled = m.columns.filter(c => c.filled).length;
  return `<div class="db-head"><div><h2>${esc(tableLabel(m.table))}<code>${esc(m.table)}</code></h2>
      <p class="muted">${m.can_insert ? '<span class="badge green">Can add rows</span>' : '<span class="badge">Read only</span>'} ${m.can_update ? '<span class="badge green">Approved edits allowed</span>' : ''}
        ${m.primary?.length ? `<span class="badge">Key: ${esc(m.primary.join(', '))}</span>` : '<span class="badge">No primary key (rows found by ROWID)</span>'}</p></div>
    <div class="stats"><div><strong>${fmt(m.total)}</strong><span>Rows</span></div><div><strong>${fmt(m.columns.length)}</strong><span>Columns</span></div><div><strong>${fmt(filled)}</strong><span>Columns with data</span></div></div></div>`;
}
function showTab(view) {
  $$('[data-tab]', view).forEach(b => { b.classList.toggle('active', b.dataset.tab === DB.tab); b.setAttribute('aria-selected', b.dataset.tab === DB.tab); });
  const body = $('#db-body', view); body.innerHTML = '<div class="empty"><span class="spinner"></span></div>';
  ({ overview: chartsTab, data: dataTab, columns: columnsTab, links: linksTab2 })[DB.tab](body, view);
}
const hasCol = c => DB.meta.columns.some(x => x.name === c);
function openFilter(view, group) { DB.filter = { ...group.filter, label: group.label }; DB.offset = 0; DB.tab = 'data'; history.replaceState(null, '', `#/database/${encodeURIComponent(DB.table)}/data`); showTab(view); }

/* ------------------------------------------------------------------ overview: charts (every bar opens its rows) */
async function chartsTab(body, view) {
  const m = DB.meta;
  if (!m.total) { body.innerHTML = `<div class="empty">${icon('table')}<p>${esc(m.table)} has no rows yet. Save an order from New Order and it appears here.</p></div>`; paintIcons(body); return; }
  const specs = [];
  if (hasCol('BUYERNAME') && hasCol('ORDERQTY')) specs.push({ title: 'Pieces by buyer', sub: 'Sum of size quantities (ORDERQTY) per buyer', by: 'BUYERNAME', measure: 'sum:ORDERQTY', kind: 'h' });
  if (hasCol('SHIPDT') && hasCol('IONO')) specs.push({ title: 'Orders by ship month', sub: 'Number of OC numbers (IONO) shipping each month', by: 'SHIPDT', measure: 'orders', kind: 'v' });
  if (hasCol('SIZES') && hasCol('ORDERQTY')) specs.push({ title: 'Pieces by size', sub: 'Sum of ORDERQTY per size name, as written in Excel', by: 'SIZES', measure: 'sum:ORDERQTY', kind: 'h' });
  let picker = '';
  if (!specs.length) {
    const texts = m.columns.filter(c => ['VARCHAR2', 'CHAR', 'DATE'].includes(c.type) && c.filled);
    DB.by = DB.by && texts.some(c => c.name === DB.by) ? DB.by : texts.find(c => c.type !== 'DATE')?.name || texts[0]?.name;
    if (!DB.by) { body.innerHTML = '<div class="empty">No text or date column with values to group by.</div>'; return; }
    specs.push({ title: `Rows by ${tableLabel(DB.by).toLowerCase()}`, sub: `Number of rows per ${DB.by} value`, by: DB.by, measure: 'rows', kind: texts.find(c => c.name === DB.by)?.type === 'DATE' ? 'v' : 'h' });
    picker = `<label class="field" style="padding:16px 18px 0;max-width:340px">Group rows by<select id="db-by">${texts.map(c => `<option value="${esc(c.name)}" ${c.name === DB.by ? 'selected' : ''}>${esc(c.label)} (${esc(c.name)})</option>`).join('')}</select></label>`;
  }
  const results = await Promise.all(specs.map(s => api(`/api/db/tables/${encodeURIComponent(m.table)}/chart?by=${encodeURIComponent(s.by)}&measure=${encodeURIComponent(s.measure)}`).catch(e => ({ groups: [], error: e.message }))));
  body.innerHTML = `${picker}<div class="charts">${specs.map((s, i) => chartCard(s, results[i], i)).join('')}</div>
    <p class="muted" style="padding:0 18px 16px;font-size:12.5px">Click any bar to see the rows behind it.</p>`;
  paintIcons(body);
  $('#db-by', body)?.addEventListener('change', e => { DB.by = e.target.value; chartsTab(body, view); });
  $$('[data-bar]', body).forEach(b => b.onclick = () => { const [i, j] = b.dataset.bar.split(':').map(Number); openFilter(view, results[i].groups[j]); });
}
function chartCard(s, r, i) {
  const groups = r.groups || []; const top = Math.max(1, ...groups.map(g => g.value));
  const bars = !groups.length ? `<div class="empty">${esc(r.error || 'No values yet.')}</div>` : s.kind === 'v'
    ? `<div class="vbars">${groups.map((g, j) => `<button type="button" class="vbar" data-bar="${i}:${j}" title="${esc(g.label)}: ${fmt(g.value)} — click to see the rows" aria-label="${esc(g.label)}: ${fmt(g.value)}">
        <b>${fmt(g.value)}</b><i style="height:${Math.max(2, g.value / top * 120)}px"></i><small>${esc(monthLabel(g.label))}</small></button>`).join('')}</div>`
    : `<div class="hbars">${groups.map((g, j) => `<button type="button" class="hbar" data-bar="${i}:${j}" title="${esc(g.label)}: ${fmt(g.value)} — click to see the rows" aria-label="${esc(g.label)}: ${fmt(g.value)}">
        <span class="l">${esc(g.label)}</span><span class="t"><i style="width:${Math.max(1, g.value / top * 100)}%"></i></span><span class="v">${fmt(g.value)}</span></button>`).join('')}</div>`;
  return `<div class="chart-card"><h3>${esc(s.title)}</h3><p>${esc(s.sub)}</p>${bars}</div>`;
}
const monthLabel = l => /^\d{4}-\d{2}$/.test(l) ? new Date(l + '-01T00:00:00').toLocaleDateString('en-IN', { month: 'short', year: '2-digit' }) : l;

/* ------------------------------------------------------------------ data */
async function dataTab(body, view) {
  const m = DB.meta, f = DB.filter || {};
  const qs = new URLSearchParams({ offset: DB.offset, limit: 50 });
  if (DB.q) qs.set('q', DB.q);
  if (f.col) { qs.set('col', f.col); if (f.month) qs.set('month', f.month); else if (f.isnull) qs.set('isnull', 'true'); else qs.set('val', f.val); }
  let data;
  try { data = await api(`/api/db/tables/${encodeURIComponent(m.table)}/rows?${qs}`); }
  catch (e) { body.innerHTML = `<div class="callout attention" style="margin:16px">${icon('alert')}<div>${esc(e.message)}</div></div>`; paintIcons(body); return; }
  const cols = data.columns.filter(c => m.columns.find(x => x.name === c.name)?.filled).slice(0, 14);
  const shown = cols.length ? cols : data.columns.slice(0, 14);
  body.innerHTML = `<div class="orders-bar"><div class="search">${icon('search')}<input id="db-q" placeholder="Search text in this table" value="${esc(DB.q)}" aria-label="Search text in this table"></div>
      ${f.col ? `<span class="filter-chip">${esc(tableLabel(f.col))}: ${esc(f.month ? monthLabel(f.month) : f.label)}<button type="button" id="db-unfilter" aria-label="Remove filter">${icon('x')}</button></span>` : ''}
      <span class="muted right" style="margin-left:auto">${data.total ? `${fmt(data.offset + 1)}–${fmt(Math.min(data.offset + data.rows.length, data.total))} of ${fmt(data.total)} rows` : 'No rows'}</span></div>
    ${data.rows.length ? `<div class="table-wrap" style="max-height:560px"><table class="data-table"><thead><tr>${shown.map(c => `<th title="${esc(c.label)}">${esc(c.name)}</th>`).join('')}<th></th></tr></thead><tbody>
      ${data.rows.map((r, i) => `<tr class="clickable" data-row="${i}">${shown.map(c => `<td class="${typeof r[c.name] === 'number' && !isCode(c.name) ? 'num' : ''}">${esc(val(c.name, r[c.name]))}</td>`).join('')}
        <td>${r._order ? `<span class="badge green" title="Saved by POPPYS">${esc(r._order.ref || 'POPPYS order')}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : `<div class="empty">${icon('search')}<p>No rows match${f.col || DB.q ? ' this filter' : ''}.</p></div>`}
    <div class="row between" style="padding:12px 18px"><span class="muted" style="font-size:12.5px">Click a row to see every column.</span>
      <div class="row"><button class="btn small" id="db-prev" ${DB.offset ? '' : 'disabled'}>Previous</button><button class="btn small" id="db-next" ${data.offset + data.rows.length < data.total ? '' : 'disabled'}>Next</button></div></div>`;
  paintIcons(body);
  let timer; $('#db-q', body).oninput = e => { clearTimeout(timer); timer = setTimeout(() => { DB.q = e.target.value.trim(); DB.offset = 0; dataTab(body, view).then(() => { const q = $('#db-q', body); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }); }, 350); };
  $('#db-unfilter', body)?.addEventListener('click', () => { DB.filter = null; DB.offset = 0; dataTab(body, view); });
  $('#db-prev', body).onclick = () => { DB.offset = Math.max(0, DB.offset - 50); dataTab(body, view); };
  $('#db-next', body).onclick = () => { DB.offset += 50; dataTab(body, view); };
  $$('[data-row]', body).forEach(tr => tr.onclick = () => rowDrawer(data.rows[Number(tr.dataset.row)], data.columns));
}
function rowDrawer(r, cols) {
  Drawer.open({ eyebrow: `${DB.meta.table} row`, title: r.IONO ? `Order ${r.IONO}` : tableLabel(DB.meta.table), render: () => `
    ${r._order ? `<div class="callout success">${icon('check')}<div>Saved by POPPYS as part of order <strong>${esc(r._order.ref || '')}</strong>. <button class="btn small" id="row-order">${icon('eye')} Open order</button></div></div>` : ''}
    <div class="table-wrap card"><table class="kv"><tbody>${cols.map(c => `<tr><td><strong>${esc(c.label)}</strong><span class="sub mono">${esc(c.name)}</span></td><td>${r[c.name] == null ? '<span class="faint">blank</span>' : esc(val(c.name, r[c.name]))}</td></tr>`).join('')}</tbody></table></div>`,
    after: body => $('#row-order', body)?.addEventListener('click', () => go(`#/orders/${r._order.key}`)) });
}

/* ------------------------------------------------------------------ columns */
function typeText(c) {
  if (['VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR'].includes(c.type)) return `${c.type}(${c.length})`;
  if (c.type === 'NUMBER') return c.precision ? `NUMBER(${c.precision}${c.scale ? ',' + c.scale : ''})` : 'NUMBER';
  return c.type;
}
function columnsTab(body) {
  const m = DB.meta, total = m.total || 0;
  body.innerHTML = `<p class="muted" style="padding:16px 18px 0">${plural(m.columns.length, 'column')} in ${esc(m.table)}. The bar shows how many of the ${fmt(total)} rows have a value.</p>
    <div class="colgrid">${m.columns.map(c => { const pct = total ? Math.round(c.filled / total * 100) : 0;
      return `<div class="colbox"><div class="top"><span class="lab">${esc(c.label)}</span>${c.required ? '<span class="badge amber">Required</span>' : c.system ? '<span class="badge">ERP</span>' : ''}</div>
        <span class="code">${esc(c.name)}</span><span class="type">${esc(typeText(c))}${c.default ? ` · default ${esc(c.default)}` : ''}</span>
        <div class="fill" title="${fmt(c.filled || 0)} of ${fmt(total)} rows"><i style="width:${pct}%"></i></div><span class="type">${total ? `${pct}% filled` : 'No rows yet'}</span></div>`; }).join('')}</div>`;
}

/* ------------------------------------------------------------------ links */
function linksTab2(body, view) {
  const t = DB.meta.table, o = DB.overview;
  const links = o.links.filter(l => l.parent === t || l.child === t);
  const node = name => `<div class="link-node${name === o.main ? ' main' : ''}"><b>${esc(name)}</b><small>${esc(tableLabel(name))}</small></div>`;
  body.innerHTML = `<div class="linkmap"><div class="row between"><p class="muted">How ${esc(t)} connects to other tables. Mapping links are how POPPYS keeps linked rows with their order; Oracle keys are enforced by the database.</p>
      <button class="btn" id="db-addlink">${icon('link')} Add link</button></div>
    ${links.length ? links.map(l => `<div class="link-row">${node(l.parent)}<div class="link-edge"><span>${esc(Object.entries(l.columns || {}).map(([c, p]) => `${p} = ${c}`).join(', ') || 'linked')}</span>
        <small style="display:block;margin-top:6px">${l.kind === 'mapping' ? 'Linked by POPPYS mapping' : l.kind === 'physical' ? 'Oracle foreign key' : l.kind === 'verified' ? 'Verified ERP link' : 'Admin link'}</small></div>${node(l.child)}</div>`).join('')
      : `<div class="empty">${icon('link')}<p>No links for ${esc(t)} yet. ${t === o.main ? 'Add a linked table to an upload in New Order (for example fabrics or printing), or add a link here.' : 'Add a link to connect it to the order table.'}</p></div>`}</div>`;
  paintIcons(body);
  $('#db-addlink', body).onclick = () => addLink(t === o.main ? '' : t);
}
function addLink(child) {
  const o = DB.overview, tables = [...new Set([...o.tables.map(t => t.name), ...o.all_tables])];
  const L = { parent: o.main, child: child || '', pc: '', cc: '', kind: 'application', result: null }; const cache = {};
  const cols = async t => t ? (cache[t] ||= (await api(`/api/db/tables/${encodeURIComponent(t)}/columns`)).columns) : [];
  const body = Modal.open('Add a link between two tables', '<div id="lk"></div>');
  const spec = () => ({ parent: L.parent, parent_column: L.pc, child: L.child, child_column: L.cc, kind: L.kind, note: $('#lk-note', body)?.value || '' });
  const draw = async () => {
    const [pc, cc] = await Promise.all([cols(L.parent), cols(L.child)]);
    const opts = (list, sel) => `<option value="">Choose…</option>${list.map(c => `<option value="${esc(c.name)}" ${c.name === sel ? 'selected' : ''}>${esc(c.name)} — ${esc(c.label)}</option>`).join('')}`;
    $('#lk', body).innerHTML = `<div class="stack"><p class="muted">A link says which column of the child table holds the parent's value (for example FABRICS.IONO holds ORDERENTRY.IONO). POPPYS checks it against the data in Oracle before saving.</p>
      <div class="grid g2"><label class="field">Parent table (one)<select id="lk-p">${tables.map(t => `<option ${t === L.parent ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></label>
        <label class="field">Parent column<select id="lk-pc">${opts(pc, L.pc)}</select></label>
        <label class="field">Child table (many)<select id="lk-c"><option value="">Choose…</option>${tables.map(t => `<option ${t === L.child ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></label>
        <label class="field">Child column<select id="lk-cc" ${L.child ? '' : 'disabled'}>${opts(cc, L.cc)}</select></label></div>
      <div class="seg"><button type="button" data-kind="application" class="${L.kind === 'application' ? 'active' : ''}">Link in POPPYS only</button><button type="button" data-kind="physical" class="${L.kind === 'physical' ? 'active' : ''}">Oracle foreign key</button></div>
      <p class="muted">${L.kind === 'application' ? 'Recorded in POPPYS. Oracle is not changed.' : 'Adds a real constraint in Oracle. Existing data must already match. Needs your confirmation.'}</p>
      <label class="field">Note (optional)<input id="lk-note" maxlength="300" placeholder="e.g. Fabric rows belong to an order"></label>
      <div id="lk-result"></div>
      <div class="modal-foot"><button class="btn" id="lk-cancel">Cancel</button><button class="btn primary" id="lk-check" ${L.pc && L.cc ? '' : 'disabled'}>${icon('check')} Check link</button></div></div>`;
    paintIcons(body);
    $('#lk-p', body).onchange = e => { L.parent = e.target.value; L.pc = ''; L.result = null; draw(); };
    $('#lk-c', body).onchange = e => { L.child = e.target.value; L.cc = ''; L.result = null; draw(); };
    $('#lk-pc', body).onchange = e => { L.pc = e.target.value; L.result = null; draw(); };
    $('#lk-cc', body).onchange = e => { L.cc = e.target.value; L.result = null; draw(); };
    $$('[data-kind]', body).forEach(b => b.onclick = () => { L.kind = b.dataset.kind; L.result = null; draw(); });
    $('#lk-cancel', body).onclick = () => Modal.close();
    $('#lk-check', body).onclick = () => busy('Checking the link against Oracle data…', async () => { L.result = await api('/api/db/relationships/validate', { method: 'POST', body: spec() }); result(); });
    if (L.result) result();
  };
  const result = () => { const r = L.result, el = $('#lk-result', body);
    el.innerHTML = `${r.errors.length ? `<div class="callout attention">${icon('alert')}<div><strong>This link cannot be saved</strong><ul>${r.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` : `<div class="callout success">${icon('check')}<div>Valid ${esc(r.cardinality)} link.</div></div>`}
      ${r.warnings.length ? `<div class="callout">${icon('info')}<ul>${r.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
      ${r.ddl && !r.errors.length ? `<div class="ddl">${esc(r.ddl)}</div><label class="row"><input type="checkbox" id="lk-confirm"> I confirm adding this constraint to Oracle</label>` : ''}
      ${r.errors.length ? '' : `<div class="modal-foot"><button class="btn primary" id="lk-save">${icon('save')} Save link</button></div>`}`;
    paintIcons(el);
    $('#lk-save', el)?.addEventListener('click', () => busy('Saving the link…', async () => {
      await api('/api/db/relationships', { method: 'POST', body: { spec: spec(), hash: r.hash, confirm: !!$('#lk-confirm', el)?.checked } });
      Modal.close(); toast('Link saved.'); navigate(); })); };
  draw();
}

/* ------------------------------------------------------------------ create table (top button only) */
function createTable() {
  const cols = [{ name: 'ID', type: 'NUMBER', required: true, pk: true }, { name: 'IONO', type: 'VARCHAR2', length: 30 }, { name: '', type: 'VARCHAR2', length: 100 }];
  const body = Modal.open('Create a new Oracle table', '<div id="ct"></div>'); let preview = null;
  const spec = () => ({ name: $('#ct-name', body).value.trim().toUpperCase(), columns: cols.filter(c => c.name.trim()).map(c => ({ name: c.name.trim().toUpperCase(), type: c.type, length: c.length, precision: c.precision, scale: c.scale, required: !!c.required, default: c.default || null })),
    primary_key: cols.filter(c => c.pk && c.name.trim()).map(c => c.name.trim().toUpperCase()) });
  const draw = () => {
    $('#ct', body).innerHTML = `<div class="stack"><p class="muted">Type the table name and its columns like a spreadsheet. Keep an IONO column so the rows can be linked to their order. POPPYS checks everything and shows the exact Oracle statement before anything is created.</p>
      <label class="field">Table name<input id="ct-name" placeholder="e.g. FABRICS" value="${esc(DB.ctName || '')}" style="max-width:360px;text-transform:uppercase"></label>
      <div class="table-wrap card" style="box-shadow:none"><table class="coledit"><thead><tr><th>Column name</th><th>Type</th><th>Size</th><th>Required</th><th>Key</th><th>Default</th><th></th></tr></thead><tbody>
      ${cols.map((c, i) => `<tr><td><input data-i="${i}" data-k="name" value="${esc(c.name)}" placeholder="COLUMN_NAME" style="text-transform:uppercase" aria-label="Column name"></td>
        <td><select data-i="${i}" data-k="type" aria-label="Type">${[['VARCHAR2', 'Text'], ['NUMBER', 'Number'], ['DATE', 'Date'], ['CHAR', 'Code (fixed)'], ['TIMESTAMP', 'Date & time'], ['CLOB', 'Long text']].map(([v, l]) => `<option value="${v}" ${c.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select></td>
        <td>${['VARCHAR2', 'CHAR'].includes(c.type) ? `<input data-i="${i}" data-k="length" type="number" min="1" value="${esc(c.length ?? '')}" placeholder="length" aria-label="Length">` : c.type === 'NUMBER' ? `<div class="row" style="flex-wrap:nowrap;gap:4px"><input data-i="${i}" data-k="precision" type="number" min="1" max="38" value="${esc(c.precision ?? '')}" placeholder="digits" aria-label="Digits"><input data-i="${i}" data-k="scale" type="number" min="0" value="${esc(c.scale ?? '')}" placeholder="decimals" aria-label="Decimals"></div>` : '<span class="faint">—</span>'}</td>
        <td style="text-align:center"><input type="checkbox" data-i="${i}" data-k="required" ${c.required ? 'checked' : ''} aria-label="Required"></td>
        <td style="text-align:center"><input type="checkbox" data-i="${i}" data-k="pk" ${c.pk ? 'checked' : ''} aria-label="Primary key"></td>
        <td><input data-i="${i}" data-k="default" value="${esc(c.default ?? '')}" placeholder="${c.type === 'DATE' ? 'today' : 'optional'}" aria-label="Default"></td>
        <td><button class="icon-btn" data-del="${i}" aria-label="Remove column">${icon('x')}</button></td></tr>`).join('')}</tbody></table></div>
      <div><button class="btn small" id="ct-add">${icon('plus')} Add column</button></div>
      <div id="ct-preview"></div>
      <div class="modal-foot"><button class="btn" id="ct-cancel">Cancel</button><button class="btn primary" id="ct-check">${icon('eye')} Check the table</button></div></div>`;
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
       <div class="row"><input id="ct-confirm" placeholder="${esc(preview.table)}" style="max-width:280px" aria-label="Type the table name to confirm"><button class="btn primary" id="ct-create" disabled>${icon('database')} Create table</button></div>` :
       `<div class="callout info">${icon('info')}<div>Table creation is switched off on this database. The administrator can enable it once by running <code>python scripts/upgrade_local.py</code>.</div></div>`}</div>`;
    paintIcons(el);
    $('#ct-confirm', el)?.addEventListener('input', e => { $('#ct-create', el).disabled = e.target.value.trim().toUpperCase() !== preview.table; });
    $('#ct-create', el)?.addEventListener('click', () => busy('Creating the table in Oracle…', async () => {
      const r = await api('/api/db/tables', { method: 'POST', body: { spec: preview.spec, hash: preview.hash, confirm: $('#ct-confirm', el).value } });
      DB.ctName = ''; Modal.close(); toast(`Table ${r.created} created. POPPYS can add rows to it now.`); go(`#/database/${encodeURIComponent(r.created)}`);
    }));
  };
  draw();
}
