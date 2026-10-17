// Read-only GARMENT_DEMO explorer: business names, records with decoded master labels, linked records
// (parents and children through the relationship map), and an expandable relationship diagram.
import { $, $$, esc, show, fmt, plural, busy, drawer, closeDrawer, notify, formView, state } from './util.js';
import { api, id } from './api.js';
import { renderGraph } from './graph.js';

let tables = []; let tablesReady = null; let current = null; let tab = 'records'; let finding = false;
let trail = [];   // records opened one inside another in the drawer, for the Back button
const lastPage = new Map();   // table -> its last page that loaded, so a failed read can still draw the filter bar
const view = { showSys: false, showEmpty: false };   // grid toggles, kept while the app is open
const GROUPS = [['header', 'Order tables'], ['detail', 'Order tables'], ['master', 'Masters'], ['local', 'Local'], ['other', 'Other tables']];
const RANK = { label: 1, reference: 2, measure: 3, date: 4 };
const APPROX = 'Approximate count from Oracle statistics. Very large tables are not counted row by row, so pages stay fast. Filter the table to get an exact count.';
const NOT_COUNTED = 'There are more linked rows than listed here. Counting them all took too long on this database, so no exact number is shown.';
const blank = v => v == null || v === '';
const confirmed = s => s === 'verified' || s === 'declared';
const count = (n, approx) => approx ? `<span class="ex-approx" title="${esc(APPROX)}">≈ ${fmt(n, 0)}</span>` : fmt(n, 0);

// busy() drops a second action while one runs; explorer reads are safe to overlap, so they still run.
const run = (message, fn) => state.busy ? fn().catch(e => notify(e.message, 'error')) : busy(message, fn);

function fetchTables(force = false) {
  if (force || !tablesReady) tablesReady = api('/api/explorer/tables').then(t => { tables = t; return t; }, e => { tablesReady = null; throw e; });
  return tablesReady;
}

export async function loadExplorer(force = false) {
  if (tables.length && !force) { drawTables(); if (current) return load(); return; }
  const ok = await run('Reading the table list from Oracle…', async () => {
    try { await fetchTables(force); }
    catch (e) { $('#tables').innerHTML = `<p class="empty">${esc(e.message)}</p>`; $('#table-view').innerHTML = '<p class="empty">Connect Oracle to browse tables.</p>'; return false; }
    drawTables(); return true;
  });
  if (ok && !current && !finding && tables.some(t => t.table === 'ORDMAS')) await openTable('ORDMAS');
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
      <b>${esc(t.business || t.table)}</b><span class="n" title="${esc(t.approx ? APPROX : 'Exact row count')}">${t.rows == null ? '' : (t.approx ? '≈ ' : '') + fmt(t.rows, 0)}</span><code>${esc(t.table)}</code></button>`).join('');
  }
  const approx = list.some(t => t.approx && t.rows != null);
  $('#tables').innerHTML = (html || '<p class="empty">No tables match.</p>') + (approx ? `<p class="ex-note"><b>≈</b> ${esc(APPROX)}</p>` : '');
}

// params: column, value, exact (the filter), tab, and for screens that know better than the server's
// wording: explain(error) -> text for a failed read, empty -> text when nothing matches.
export async function openTable(table, params = {}) {
  current = { table, column: params.column || '', value: params.value || '', exact: !!params.exact, after: null, explain: params.explain, empty: params.empty };
  tab = params.tab || 'records';
  drawTables(); await load();
}

// A fresh count from reading a table keeps the table list (read once per session) and the relationship
// map in step with the header above them.
function noteCount(table, rows, approx) {
  const t = tables.find(x => x.table === table);
  if (!t || rows == null || (t.rows === rows && !!t.approx === !!approx)) return false;
  t.rows = rows; t.approx = !!approx; return true;
}

// One read at a time per table view: a second click on Load more while a page is still coming would read
// the same page again and show its rows twice. A read that fails replaces the rows with the error (old rows
// under a new filter would mislead), and Load more never repeats a failed read.
async function load(more = false) {
  const cur = current;
  if (!cur || cur.loading || (more && (!cur.data || cur.moreError))) return;
  cur.loading = true;
  try {
    await run(`Reading ${cur.table}…`, async () => {
      const filtered = !!(cur.column && cur.value);
      const qs = new URLSearchParams({ limit: 50 });
      if (filtered) { qs.set('column', cur.column); qs.set('value', cur.value); if (cur.exact) qs.set('exact', 'true'); }
      if (more) { if (cur.data.key) qs.set('after', cur.after); else qs.set('offset', cur.data.offset + 50); }
      let data;
      try { data = await api(`/api/explorer/tables/${id(cur.table)}?${qs}`); }
      catch (e) {
        if (more) cur.moreError = e.message;
        else { cur.data = null; cur.error = cur.explain?.(e) || e.message; cur.tryExact = e.status === 409 && filtered && !cur.exact && !cur.explain; }
        if (cur === current) draw();
        return;
      }
      if (more) {
        data.rows = cur.data.rows.concat(data.rows);
        for (const [c, m] of Object.entries(cur.data.decoded)) data.decoded[c] = { ...m, ...(data.decoded[c] || {}) };
      }
      cur.data = data; cur.after = data.next_after; cur.error = null;
      lastPage.set(cur.table, data);
      if (cur !== current) return;   // a newer table or filter was opened meanwhile
      if (!filtered && !more && noteCount(cur.table, data.total, data.approx)) drawTables();
      draw();
    });
  } finally { cur.loading = false; }
}

// A value as people read it: the master's name with its ID as a small chip when the ID is known.
// decoded[col] lists every ID that was looked up: a found record with no name comes back as null, a missing
// record not at all, so only a missing one is marked "(not found)".
function cellHtml(data, col, v, compact = false) {
  if (blank(v)) return '<span class="muted">—</span>';
  const names = data.decoded?.[col]; const found = !!names && Object.hasOwn(names, String(v)); const label = found ? names[String(v)] : null;
  if (!blank(label) && compact) return `<span title="Record ID ${esc(v)}">${esc(label)}</span>`;
  if (!blank(label)) return `${esc(label)} <span class="chip label" title="Record ID ${esc(v)}">${esc(v)}</span>`;
  if (found) return compact ? `<span title="The linked record has no name">${esc(v)}</span>` : `${esc(v)} <span class="muted small-text" title="The linked record exists but has no name">(no name)</span>`;
  if (names && typeof v === 'number') return `${esc(v)} <span class="muted small-text" title="No linked record has this ID">(not found)</span>`;
  return esc(show(v));
}

// Business order: the catalog's display columns, then names, master links, quantities and dates, then the rest.
function weight(d, c) {
  const at = (d.display || []).indexOf(c.name);
  if (at >= 0) return at;
  return 100 + (RANK[c.role] || (d.decoded?.[c.name] ? RANK.reference : c.role === 'system' ? 10 : 9));
}
const ordered = d => [...d.columns].sort((a, b) => weight(d, a) - weight(d, b));   // stable: ties keep Oracle order

// No rows to show: a short message where the grid would be (a full-width row under every column header
// would put it far off to the side on a wide table), with the way out.
function emptyHtml(cur, d) {
  const filtered = !!(cur.column && cur.value);
  const col = d.columns.find(c => c.name === cur.column); const what = col ? col.label || col.name : cur.column;
  const clear = filtered ? '<button type="button" class="ghost small" data-x="clear">Clear the filter</button>' : '';
  if (cur.error) {
    return `<div class="ex-empty err" role="alert"><b>${filtered ? 'This filter could not be run.' : 'The records could not be read.'}</b><p>${esc(cur.error)}</p>
      <div class="row wrap">${cur.tryExact ? `<button type="button" class="secondary small" data-x="exact">Look for exactly “${esc(cur.value)}”</button>` : ''}${clear || '<button type="button" class="secondary small" data-x="retry">Try again</button>'}</div></div>`;
  }
  if (!filtered) return '<div class="ex-empty"><b>No records.</b><p>This table is empty.</p></div>';
  return `<div class="ex-empty"><b>No records match this filter.</b><p>${cur.empty ? esc(cur.empty) : `${esc(what)} ${cur.exact ? 'is exactly' : 'contains'} “${esc(cur.value)}”.${cur.exact ? ' Choose “contains” to look for part of a value.' : ''}`}</p>
    <div class="row wrap">${clear}</div></div>`;
}

function draw() {
  const cur = current; const meta = tables.find(t => t.table === cur.table) || {};
  // after a failed read there is no page: the table's last good page still gives its name and columns
  const d = cur.data || { ...(lastPage.get(cur.table) || { table: cur.table, business: meta.business, description: meta.description, key: meta.key, columns: [], display: [] }),
    rows: [], total: null, approx: false, next_after: null, decoded: {} };
  const all = ordered(d);
  const sys = all.filter(c => c.role === 'system');
  let empty = all.filter(c => c.role !== 'system' && d.rows.length && d.rows.every(r => blank(r[c.name])));
  if (empty.length === all.length - sys.length) empty = [];   // nothing would be left to show
  const shown = all.filter(c => c.role === 'system' ? view.showSys : view.showEmpty || !empty.includes(c));
  const filtered = !!(cur.column && cur.value);
  const more = !cur.moreError && (d.key ? d.next_after != null : d.rows.length < (d.total || 0));
  $('#table-view').innerHTML = `<div class="ex-head"><div style="flex:1;min-width:0"><div class="row wrap"><h2>${esc(d.business || d.table)}</h2><code class="muted">${esc(d.table)}</code>
      ${d.total == null ? '' : `<span class="pill" title="${esc(d.approx ? APPROX : 'Exact row count')}">${d.approx ? '≈ ' : ''}${plural(d.total, filtered ? 'matching row' : 'row', filtered ? 'matching rows' : 'rows')}</span>`}${meta.links ? `<span class="pill brand">${plural(meta.links, 'link')}</span>` : ''}</div>
      <p class="muted small-text" style="margin-top:2px">${esc(d.description || '')} · read-only${d.approx ? ` · <span class="ex-approx" title="${esc(APPROX)}">≈ approximate count (Oracle statistics)</span>` : ''}</p></div></div>
    <nav class="ex-tabs tabs">${[['records', 'Records'], ['columns', d.columns.length ? `Columns (${d.columns.length})` : 'Columns'], ['links', 'Relationships']].map(([k, n]) => `<button class="${tab === k ? 'active' : ''}" data-xtab="${k}">${n}</button>`).join('')}</nav>
    <div data-xbody></div>`;
  const body = $('[data-xbody]');
  if (tab === 'records' || (tab === 'columns' && !d.columns.length)) {
    body.innerHTML = `${all.length ? `<form class="filter-bar" id="filter-form"><div class="f"><label class="f-label" for="f-col">Column</label><select id="f-col"><option value="">— choose —</option>${all.filter(c => c.role !== 'system').map(c => `<option value="${esc(c.name)}" ${c.name === cur.column ? 'selected' : ''}>${esc(c.label || c.name)} (${esc(c.name)})</option>`).join('')}</select></div>
        <div class="f"><label class="f-label" for="f-op">Match</label><select id="f-op"><option value="">contains</option><option value="exact" ${cur.exact ? 'selected' : ''}>is exactly</option></select></div>
        <div class="f"><label class="f-label" for="f-val">Value</label><input id="f-val" value="${esc(cur.value)}" maxlength="100"></div><button class="secondary">Filter</button>${cur.value ? '<button type="button" class="ghost" id="f-clear">Clear</button>' : ''}
        <span class="spacer"></span><div class="ex-toggles">${empty.length ? `<label class="check" title="Columns with no value in any of the ${d.rows.length} rows shown"><input type="checkbox" id="show-empty" ${view.showEmpty ? 'checked' : ''}> Show empty columns (${empty.length})</label>` : ''}
        ${sys.length && d.rows.length ? `<label class="check"><input type="checkbox" id="show-sys" ${view.showSys ? 'checked' : ''}> Show Axpert system columns (${sys.length})</label>` : ''}</div></form>` : ''}
      ${d.rows.length ? `<div class="grid-scroll"><table><thead><tr>${shown.map(c => `<th title="${esc([c.type, c.role, c.references && 'links to ' + c.references].filter(Boolean).join(' · '))}">${esc(c.label || c.name)}<span class="code">${esc(c.name)}</span></th>`).join('')}</tr></thead><tbody>
        ${d.rows.map((r, i) => `<tr class="clickable" data-row="${i}">${shown.map(c => `<td>${cellHtml(d, c.name, r[c.name])}</td>`).join('')}</tr>`).join('')}
      </tbody></table></div>
      <div class="pager"><span>${plural(d.rows.length, 'row')} shown${d.total != null ? ` of ${count(d.total, d.approx)}` : ''} · ${d.key ? 'newest first' : 'Oracle order'} · click a row to see linked records</span>
        ${cur.moreError ? `<span class="ex-more-err" role="alert">More rows could not be read: ${esc(cur.moreError)}</span>` : more ? '<button class="secondary small" id="more">Load more</button>' : ''}</div>` : emptyHtml(cur, d)}`;
    $('#filter-form')?.addEventListener('submit', e => { e.preventDefault(); openTable(cur.table, { column: $('#f-col').value, value: $('#f-val').value.trim(), exact: $('#f-op').value === 'exact' }); });
    $('#f-clear')?.addEventListener('click', () => openTable(cur.table));
    $('#show-sys')?.addEventListener('change', e => { view.showSys = e.target.checked; draw(); });
    $('#show-empty')?.addEventListener('change', e => { view.showEmpty = e.target.checked; draw(); });
    $('#more')?.addEventListener('click', e => { e.currentTarget.disabled = true; e.currentTarget.textContent = 'Loading…'; load(true); });
    body.querySelectorAll('[data-x]').forEach(b => b.addEventListener('click', () => ({
      clear: () => openTable(cur.table), retry: () => openTable(cur.table),
      exact: () => openTable(cur.table, { column: cur.column, value: cur.value, exact: true }),
    })[b.dataset.x]()));
    $$('#table-view tr[data-row]').forEach(tr => tr.onclick = () => { const r = d.rows[Number(tr.dataset.row)]; openRecord(cur.table, r[d.key || meta.key], { row: r, data: d }); });
  } else if (tab === 'columns') {
    body.innerHTML = `<p class="muted small-text ex-cols-note">In business order: the columns shown first in Records, then names, master links, quantities and dates, then the rest.</p>
      <div class="grid-scroll"><table><thead><tr><th>Business name</th><th>Column</th><th>Type</th><th>Role</th></tr></thead><tbody>
      ${all.map(c => `<tr><td><b>${esc(c.label || '—')}</b>${(d.display || []).includes(c.name) ? ' <span class="pill brand">shown first</span>' : ''}</td><td class="mono">${esc(c.name)}</td><td class="muted">${esc(c.type)}</td><td>${c.role ? `<span class="pill ${c.role === 'system' ? '' : c.role === 'reference' ? 'master' : c.role === 'measure' ? 'brand' : 'info'}">${esc(c.role)}</span>` : ''}${c.references ? ` <span class="muted small-text">→ ${esc(c.references)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
  } else {
    body.innerHTML = `<div class="flow-wrap" data-rel style="min-height:240px"></div><div class="legend"><span>Click a table to open it · ＋ adds its linked tables</span><span><i style="background:#d39a2c"></i>Unconfirmed link</span><span data-approx hidden title="${esc(APPROX)}">≈ approximate row count (Oracle statistics)</span></div>`;
    relationshipMap(body.querySelector('[data-rel]'), current.table, body.querySelector('[data-approx]'));
  }
  $$('[data-xtab]').forEach(b => b.onclick = () => { tab = b.dataset.xtab; draw(); });
}

const rowsText = (n, approx) => n == null ? '' : `${approx ? '≈ ' : ''}${fmt(n, 0)} rows`;

async function relationshipMap(el, focus, approxNote) {
  const nodes = new Map(); const edges = [];
  const add = async table => {
    const n = await api(`/api/knowledge/neighbours/${id(table)}`);
    // the open table shows the count in its header pill (read just now), not the session's table list
    const page = table === focus && current?.table === focus && !(current.column && current.value) && current.data;
    const own = page && page.total != null ? { rows: page.total, approx: page.approx } : tables.find(t => t.table === table) || {};
    if (!nodes.has(table)) nodes.set(table, { id: table, title: n.business, code: table, kind: n.kind, base: table === focus, approx: own.approx, status: { text: rowsText(own.rows, own.approx), cls: 'muted' } });
    n.neighbours.forEach(x => {
      if (!nodes.has(x.table)) nodes.set(x.table, { id: x.table, title: x.business, code: x.table, kind: x.kind, line3: x.label, approx: x.approx, status: { text: rowsText(x.rows, x.approx), cls: 'muted' } });
      const from = x.direction === 'up' ? x.table : table, to = x.direction === 'up' ? table : x.table;
      if (!edges.some(e => e.from === from && e.to === to)) edges.push({ from, to, label: '', title: `${x.label} · ${x.on} (${x.status})`, cls: confirmed(x.status) ? 'link' : 'proposed' });
    });
    approxNote.hidden = ![...nodes.values()].some(x => x.approx && x.status.text);
  };
  const draw = () => renderGraph(el, { nodes: [...nodes.values()], edges }, { selected: focus, onSelect: t => { if (t !== current.table) openTable(t, { tab: 'links' }); },
    onExpand: async t => { try { await add(t); draw(); } catch (e) { notify(e.message, 'error'); } }, nodeW: 200, nodeH: 72, gapX: 80 });
  try { await add(focus); draw(); } catch (e) { el.innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

// --- one record in the drawer: its fields, the masters it belongs to, and its child rows ------------------
// A record's name: its label column (master name when decoded), else its record ID.
function nameOf(data, row, pk) {
  const label = data.label && row[data.label];
  return blank(label) ? (pk == null ? '' : `#${pk}`) : String(data.decoded?.[data.label]?.[String(label)] ?? label);
}
const titleOf = (data, row, pk) => `${data.business || data.table} ${nameOf(data, row, pk) || 'record'}`;

async function openRecord(table, pk, hint = {}, nested = false) {
  const meta = tables.find(t => t.table === table) || {};
  const base = { table, business: meta.business || table, key: meta.key, label: meta.label, display: [], decoded: {}, ...(hint.data || {}) };
  const row = hint.row || {};
  if (!nested) trail = [];
  trail.push({ table, pk, title: titleOf(base, row, pk) });
  const token = openRecord.token = {};
  const body = drawer(trail.at(-1).title, recordHtml({ ...base, record: row, columns: base.columns || Object.keys(row).map(n => ({ name: n })), related: null }));
  bindRecord(body, null);
  if (pk == null) { body.querySelector('[data-related]').innerHTML = '<p class="muted">This table has no single record ID, so linked records cannot be listed.</p>'; return; }
  let rel;
  try { rel = await api(`/api/explorer/related/${id(table)}/${id(pk)}`); }
  catch (e) { if (openRecord.token === token) body.querySelector('[data-related]').innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  if (openRecord.token !== token || $('#drawer').hidden) return;   // another record was opened, or the drawer closed
  trail.at(-1).title = titleOf(rel, rel.record, pk);
  $('#drawer-title').textContent = trail.at(-1).title;
  body.innerHTML = recordHtml(rel); bindRecord(body, rel);
}

// Child tables in reading order: a table other rows hang under comes first, followed by those rows
// (order lines, then their size rows), then any other child tables.
function arrange(groups) {
  const down = groups.filter(g => g.direction === 'down'); const present = new Set(down.map(g => g.table));
  const parentOf = g => g.under && present.has(g.under.table) ? g.under.table : null;
  const kids = t => down.filter(c => parentOf(c) === t).sort((a, b) => !confirmed(a.under.status) - !confirmed(b.under.status));
  const out = []; const seen = new Set();
  const visit = g => { if (seen.has(g.table)) return; seen.add(g.table); out.push(g); kids(g.table).forEach(visit); };
  down.filter(g => !parentOf(g)).sort((a, b) => !kids(a.table).length - !kids(b.table).length).forEach(visit);
  down.forEach(visit);   // anything left in a loop of links
  return out;
}

function recordHtml(rel) {
  const fields = ordered(rel).filter(c => !blank(rel.record[c.name]));
  let facts = fields.filter(c => (rel.display || []).includes(c.name) || c.name === rel.key);
  if (facts.length < 4) facts = fields;
  const rest = fields.filter(c => !facts.includes(c));
  const field = c => ({ label: c.label || c.name, code: c.name, html: cellHtml(rel, c.name, rel.record[c.name]) });
  const back = trail.length > 1 ? `<button class="ghost small ex-back" data-back>← Back to ${esc(trail.at(-2).title)}</button>` : '';
  if (!rel.related) return `${back}${formView(facts.map(field))}<div class="record-sections" data-related><p class="muted">Loading linked records…</p></div>`;
  const downs = arrange(rel.related); const ups = rel.related.filter(g => g.direction === 'up');
  const byTable = Object.fromEntries(downs.map(g => [g.table, g]));
  return `${back}${formView(facts.map(field))}
    ${rest.length ? `<details class="ex-more"><summary>All fields (${rest.length} more)</summary>${formView(rest.map(field))}</details>` : ''}
    ${ups.length ? `<div class="ex-masters"><span class="ex-sub">Belongs to</span>${ups.map(g => g.rows.map((r, ri) => `<button class="ex-master ${confirmed(g.status) ? '' : 'unconfirmed'}" data-open="${rel.related.indexOf(g)}:${ri}" title="${esc(`${g.on}${confirmed(g.status) ? '' : ' · unconfirmed link'}`)}"><small>${esc(g.business)}</small><b>${esc(nameOf(g, r, r[g.key]) || '—')}</b></button>`).join('')).join('')}</div>` : ''}
    ${downs.length ? `<nav class="ex-jump" aria-label="Linked records">${downs.map(g => `<button class="secondary small" data-jump="${esc(g.table)}">${esc(g.business)} <span class="count-badge" ${g.total == null ? `title="${esc(NOT_COUNTED)}"` : ''}>${g.total == null ? `${fmt(g.rows.length, 0)}+` : fmt(g.total, 0)}</span></button>`).join('')}</nav>` : ''}
    <div class="record-sections" data-related>${downs.map(g => childHtml(rel, g, byTable)).join('') || (ups.length ? '' : '<p class="muted">No linked records found.</p>')}</div>`;
}

// Line number + name of each row of a table other rows hang under, keyed by the linking values.
const linkKey = (r, cols) => cols.map(c => String(r[c])).join('|');
const badge = n => `<span class="ex-line" title="Row ${n}">${n}</span>`;

// One child table of the record. Rows that hang under another child table (sizes under order lines)
// start with that line's number and name, sorted line by line; the line table numbers its rows to match.
function childHtml(rel, g, byTable) {
  const parent = g.under && byTable[g.under.table];
  const lineAt = parent && new Map(parent.rows.map((r, i) => [linkKey(r, g.under.pairs.map(([, p]) => p)), { n: i + 1, name: nameOf(parent, r, null) }]));
  const child = Object.values(byTable).find(c => c.under?.table === g.table);   // rows of another table hang under this one
  const hide = new Set([g.key, ...(g.pairs || []).map(([c]) => c), ...(parent ? g.under.pairs.map(([c]) => c) : []), ...(child ? child.under.pairs.map(([, p]) => p) : [])]);
  const cols = ordered(g).filter(c => !hide.has(c.name) && g.rows.some(r => !blank(r[c.name]))).slice(0, parent ? 7 : 8);
  const rows = g.rows.map((r, i) => ({ r, i, line: lineAt?.get(linkKey(r, g.under.pairs.map(([c]) => c))) }));
  if (parent) rows.sort((a, b) => (a.line?.n ?? 1e9) - (b.line?.n ?? 1e9) || a.i - b.i);
  const measures = cols.filter(c => c.role === 'measure' && g.rows.every(r => blank(r[c.name]) || typeof r[c.name] === 'number'));
  const lead = [parent && `<th>${esc(parent.business)}</th>`, child && '<th title="Row number">#</th>'].filter(Boolean);
  const leadCells = (i, line, repeat) => (parent ? `<td class="nowrap">${line ? `${badge(line.n)} ${repeat ? '' : esc(line.name)}` : '<span class="muted" title="No matching row in the table above">—</span>'}</td>` : '') + (child ? `<td>${badge(i + 1)}</td>` : '');
  const foot = !g.more && g.rows.length > 1 && measures.length ? `<tfoot><tr>${lead.map((_, i) => `<td>${i ? '' : 'Total'}</td>`).join('')}${cols.map((c, ci) =>
    `<td>${measures.includes(c) ? fmt(g.rows.reduce((s, r) => s + (Number(r[c.name]) || 0), 0)) : !lead.length && !ci ? 'Total' : ''}</td>`).join('')}</tr></tfoot>` : '';
  const counted = g.total != null;   // null: there are more rows than listed, but counting them ran out of time
  const showing = counted ? `Showing the first ${fmt(g.rows.length, 0)} of ${fmt(g.total, 0)}` : `Showing the first ${fmt(g.rows.length, 0)}. There are more, but counting them all took too long`;
  return `<section class="rel-section" id="rel-${esc(g.table)}"><h4>${esc(g.business)} <code class="muted">${esc(g.table)}</code>${counted ? `<span class="pill">${plural(g.total, 'row')}</span>` : `<span class="pill" title="${esc(NOT_COUNTED)}">more than ${plural(g.rows.length, 'row')}</span>`}
      <span class="pill ${confirmed(g.status) ? 'ok' : 'warn'}">${confirmed(g.status) ? 'confirmed link' : 'unconfirmed link'}</span></h4>
    <p class="muted small-text ex-on">${esc(g.on)}${parent ? ` · grouped by ${esc(parent.business.toLowerCase())}${confirmed(g.under.status) ? '' : ' (unconfirmed link)'}` : ''}</p>
    <div class="tbl-wrap ex-rel-table"><table><thead><tr>${lead.join('')}${cols.map(c => `<th>${esc(c.label)}<span class="code">${esc(c.name)}</span></th>`).join('')}</tr></thead><tbody>
    ${rows.map(({ r, i, line }, k) => `<tr class="clickable" data-open="${rel.related.indexOf(g)}:${i}">${leadCells(i, line, k > 0 && line && rows[k - 1].line === line)}${cols.map(c => `<td>${cellHtml(g, c.name, r[c.name], true)}</td>`).join('')}</tr>`).join('')}</tbody>${foot}</table></div>
    ${g.more ? `<p class="muted small-text">${showing}${measures.length ? ', so no total is shown here' : ''}.${g.filter ? ` <button class="link" data-all="${esc(g.table)}">Show all${counted ? ` ${fmt(g.total, 0)}` : ''} in the table</button>` : ''}</p>` : ''}</section>`;
}

function bindRecord(body, rel) {
  body.querySelector('[data-back]')?.addEventListener('click', () => { trail.pop(); const prev = trail.pop(); openRecord(prev.table, prev.pk, {}, true); });
  if (!rel) return;
  $$('[data-open]', body).forEach(el => el.onclick = () => {
    const [gi, ri] = el.dataset.open.split(':').map(Number); const g = rel.related[gi]; const r = g.rows[ri];
    openRecord(g.table, g.key ? r[g.key] : null, { row: r, data: g }, true);
  });
  $$('[data-jump]', body).forEach(b => b.onclick = () => body.querySelector(`#rel-${CSS.escape(b.dataset.jump)}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  $$('[data-all]', body).forEach(b => b.onclick = () => {
    const g = rel.related.find(x => x.table === b.dataset.all); closeDrawer();
    openTable(g.table, { column: g.filter.column, value: String(g.filter.value), exact: true });
  });
}

// "Find an order": an exact order reference or record ID opens that order straight away; anything else
// lists the orders whose reference contains it.
// Every outcome is one message: a failed lookup a single toast, no match or a failed partial search the
// grid's own empty state (never the previous search's rows under the new text).
const ORDER_REF_MAX = 100;   // the server's limit for a filter value; #order-id has the same maxlength
export async function exploreOrder(ref) {
  ref = String(ref ?? '').trim(); if (!ref) return;
  if (ref.length > ORDER_REF_MAX) return notify(`That is too long for an order reference (at most ${ORDER_REF_MAX} characters). Check what was pasted into Find an order.`, 'warning');
  finding = true;
  try {
    await fetchTables();
    const meta = tables.find(t => t.table === 'ORDMAS');
    if (!meta) return notify('The order table (ORDMAS) is not available on this database.', 'warning');
    const key = meta.key || 'ORDMASID', label = meta.label || 'DOCID';
    const exact = column => api(`/api/explorer/tables/ORDMAS?${new URLSearchParams({ column, value: ref, exact: 'true', limit: 2 })}`);
    let page = null, failed = null;
    await run('Finding the order…', async () => {
      try {
        if (/^\d+$/.test(ref)) { const p = await exact(key); if (p.rows.length) page = p; }   // a record ID
        if (!page) { const p = await exact(label); if (p.rows.length) page = p; }            // the order reference, any case
      } catch (e) { failed = e; }
    });
    if (failed) return notify(`The order could not be looked up. ${failed.message}`, 'error');
    if (page?.rows.length === 1) {
      const row = page.rows[0];
      await openTable('ORDMAS', blank(row[label]) ? { column: key, value: String(row[key]), exact: true } : { column: label, value: String(row[label]), exact: true });
      return openRecord('ORDMAS', row[key], { row, data: page });
    }
    if (page) return openTable('ORDMAS', { column: label, value: ref, exact: true });   // the same reference on several orders
    // no whole match: list the orders whose reference contains it. Find already tried the exact lookup, so a
    // partial search that runs out of time on a very large table must not advise "is exactly" again.
    const big = meta.approx || (meta.rows || 0) > 1e6;
    await openTable('ORDMAS', { column: label, value: ref,
      empty: `No order reference is or contains “${ref}”. Check it, or search for a part of it such as 9001.`,
      explain: e => e.status !== 409 ? null : big
        ? `No order has the reference “${ref}”. Looking for it inside every order reference took too long on this very large table, so type the whole reference (for example PK/26-27/S/9001) or the order’s record ID.`
        : `No order has the reference “${ref}”, and the search for references containing it did not finish. ${e.message}` });
  } catch (e) { notify(e.message, 'error'); }
  finally { finding = false; }
}

export function bindExplorer() {
  $('#table-filter').addEventListener('input', drawTables);
  $('#tables').addEventListener('click', e => { const b = e.target.closest('[data-table]'); if (b) openTable(b.dataset.table); });
  $('#order-lookup').addEventListener('submit', e => { e.preventDefault(); exploreOrder($('#order-id').value); });
}
