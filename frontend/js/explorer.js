// Database screen. Orders (dborders.js) lists the orders; "All tables" is the flowchart of every table and how they
// link, with the rows of the table clicked shown underneath. A table a recent save wrote rows into blinks until it is
// opened on this computer (remembered in this browser only).
import { $, $$, esc, show, fmt, plural, busy, notify, state } from './util.js';
import { api, id } from './api.js';
import { renderGraph } from './graph.js';
import { loadOrders, orderPanel } from './dborders.js';

let tables = []; let tablesReady = null; let current = null; let tab = 'records';
let mode = 'orders';   // 'orders' | 'tables'
let updates = {};   // table -> newest POPPYS save that wrote rows into it
const lastPage = new Map();   // table -> its last page that loaded, so a failed read can still draw the filter bar
const view = { showSys: false, showEmpty: false };   // grid toggles, kept while the app is open
const RANK = { label: 1, reference: 2, measure: 3, date: 4 };
const APPROX = 'Approximate count from Oracle statistics. Very large tables are not counted row by row, so pages stay fast. Filter the table to get an exact count.';
const NOT_COUNTED = 'There are more linked rows than listed here. Counting them all took too long on this database, so no exact number is shown.';
const blank = v => v == null || v === '';
const confirmed = s => s === 'verified' || s === 'declared';
const SEEN = 'poppys.seen-tables';
const count = (n, approx) => approx ? `<span class="ex-approx" title="${esc(APPROX)}">≈ ${fmt(n, 0)}</span>` : fmt(n, 0);

// busy() drops a second action while one runs; explorer reads are safe to overlap, so they still run.
const run = (message, fn) => state.busy ? fn().catch(e => notify(e.message, 'error')) : busy(message, fn);

function fetchTables(force = false) {
  if (force || !tablesReady) tablesReady = api('/api/explorer/tables').then(t => { tables = t; return t; }, e => { tablesReady = null; throw e; });
  return tablesReady;
}

function setMode(next) {
  mode = next;
  $$('[data-dbview]').forEach(b => { b.classList.toggle('active', b.dataset.dbview === mode); b.setAttribute('aria-selected', String(b.dataset.dbview === mode)); });
  $('#db-orders').hidden = mode !== 'orders'; $('#db-tables').hidden = mode !== 'tables';
}

export async function loadExplorer(force = false) {
  setMode(mode);
  return mode === 'orders' ? loadOrders(force) : loadTables(force);
}

async function loadTables(force = false) {
  if (tables.length && !force) { await drawFlow(); if (current) return load(); return; }
  const ok = await run('Reading the tables from Oracle…', async () => {
    try { await fetchTables(force); }
    catch (e) { $('#db-flow').innerHTML = `<p class="empty">${esc(e.message)}</p>`; $('#table-view').innerHTML = '<p class="empty">Connect Oracle to see the tables.</p>'; return false; }
    await drawFlow(); return true;
  });
  if (ok && !current && tables.some(t => t.table === 'ORDMAS')) await openTable('ORDMAS');
}

// ------------------------------------------------------------------ the flowchart
function seen() { try { return JSON.parse(localStorage.getItem(SEEN) || '{}'); } catch (_) { return {}; } }
function markSeen(table) {
  if (!updates[table]) return;
  const s = seen(); s[table] = updates[table].at;
  try { localStorage.setItem(SEEN, JSON.stringify(s)); } catch (_) { /* this browser keeps nothing: it blinks again next time */ }
  $(`#db-flow .gnode[data-id="${CSS.escape(table)}"]`)?.classList.remove('blink');
  note();
}
const fresh = t => !!updates[t] && (seen()[t] || '') < updates[t].at;
function note() {
  const blinking = tables.filter(t => fresh(t.table)).map(t => t.table);
  $('#flow-note').textContent = blinking.length ? `Blinking: tables a recent save wrote rows into (${blinking.join(', ')}). Open one to stop it blinking.`
    : 'Click a table to see its rows below. Lines show which tables are linked.';
}

async function drawFlow() {
  let joins = [];
  try { [joins, updates] = await Promise.all([api('/api/knowledge/joins'), api('/api/tables/updates').catch(() => ({}))]); }
  catch (e) { $('#db-flow').innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const shown = tables.filter(t => t.kind !== 'local');
  const ids = new Set(shown.map(t => t.table));
  const nodes = shown.map(t => ({ id: t.table, title: t.business || t.table, code: t.table, kind: t.kind === 'header' ? 'header' : t.kind === 'master' ? 'master' : 'detail',
    line3: fresh(t.table) ? `New rows: ${updates[t.table].order_ref || 'saved'}` : '', cls: fresh(t.table) ? 'blink' : '',
    status: { text: t.rows == null ? '' : `${t.approx ? '≈ ' : ''}${fmt(t.rows, 0)} rows`, cls: 'muted' } }));
  const edges = [];
  for (const j of joins) {
    if (!ids.has(j.parent) || !ids.has(j.child) || j.parent === j.child || j.status === 'rejected') continue;
    if (edges.some(e => e.from === j.parent && e.to === j.child)) continue;
    edges.push({ from: j.parent, to: j.child, label: '', cls: confirmed(j.status) ? 'link' : 'proposed',
      title: `${j.label || ''} · ${j.pairs.map(([c, p]) => `${j.child}.${c} = ${j.parent}.${p}`).join(' AND ')}` });
  }
  note();
  renderGraph($('#db-flow'), { nodes, edges }, { selected: current?.table, masterBand: true, bandTitle: 'NAMES (MASTER TABLES) — point at one to see what uses it',
    treeTitle: 'ORDER TABLES', nodeW: 196, nodeH: 70, gapX: 70, label: 'Database tables and how they link',
    onSelect: t => { markSeen(t); openTable(t); $('#table-view').scrollIntoView({ behavior: 'smooth', block: 'start' }); } });
}

// params: column, value, exact (the filter), tab, and for screens that know better than the server's
// wording: explain(error) -> text for a failed read, empty -> text when nothing matches.
export async function openTable(table, params = {}) {
  current = { table, column: params.column || '', value: params.value || '', exact: !!params.exact, after: null, explain: params.explain, empty: params.empty };
  tab = params.tab || 'records';
  await load();
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
      if (!filtered && !more) noteCount(cur.table, data.total, data.approx);
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
    <nav class="ex-tabs tabs">${[['records', 'Rows'], ['columns', d.columns.length ? `Columns (${d.columns.length})` : 'Columns']].map(([k, n]) => `<button class="${tab === k ? 'active' : ''}" data-xtab="${k}">${n}</button>`).join('')}</nav>
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
      <div class="pager"><span>${plural(d.rows.length, 'row')} shown${d.total != null ? ` of ${count(d.total, d.approx)}` : ''} · ${d.key ? 'newest first' : 'Oracle order'} · ${orderLink(d) ? 'click a row to open its order' : 'read-only'}</span>
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
    const link = orderLink(d);
    $$('#table-view tr[data-row]').forEach(tr => { if (!link) { tr.classList.remove('clickable'); return; } tr.onclick = () => { const v = d.rows[Number(tr.dataset.row)][link]; if (v != null) orderPanel(v); }; });
  } else if (tab === 'columns') {
    body.innerHTML = `<p class="muted small-text ex-cols-note">In business order: the columns shown first in Records, then names, master links, quantities and dates, then the rest.</p>
      <div class="grid-scroll"><table><thead><tr><th>Business name</th><th>Column</th><th>Type</th><th>Role</th></tr></thead><tbody>
      ${all.map(c => `<tr><td><b>${esc(c.label || '—')}</b>${(d.display || []).includes(c.name) ? ' <span class="pill brand">shown first</span>' : ''}</td><td class="mono">${esc(c.name)}</td><td class="muted">${esc(c.type)}</td><td>${c.role ? `<span class="pill ${c.role === 'system' ? '' : c.role === 'reference' ? 'master' : c.role === 'measure' ? 'brand' : 'info'}">${esc(c.role)}</span>` : ''}${c.references ? ` <span class="muted small-text">→ ${esc(c.references)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
  }
  $$('[data-xtab]').forEach(b => b.onclick = () => { tab = b.dataset.xtab; draw(); });
}

// A row of an order table opens its order in the panel (the order's record ID column).
const orderLink = d => d.columns.some(c => c.name === 'ORDMASID') ? 'ORDMASID' : null;

// "See the tables in the flowchart" after a save.
export function showTables() { setMode('tables'); return loadExplorer(); }

export function bindExplorer() {
  $$('[data-dbview]').forEach(b => b.addEventListener('click', () => { setMode(b.dataset.dbview); loadExplorer(); }));
}
