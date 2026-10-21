// Database → Orders: the orders in Oracle, newest first, by Order ID (OC No) and name. Clicking an Order ID opens the
// panel on the right with everything Oracle holds for that order: its details and colour × size table, every row of
// every order table (all columns that have a value), the Excel as uploaded and the SQL to check it in SQL Developer.
import { $, $$, esc, fmt, plural, notify, state, busy, drawer } from './util.js';
import { api, id } from './api.js';
import { sheetHtml, niceValue } from './ordersheet.js';

const view = { q: '', list: [], next: null, loaded: false, error: null, seq: 0 };   // seq: the newest read wins
const panel = { order: null, tab: 'details', hidden: false, system: false };
const run = (message, fn) => state.busy ? fn().catch(e => notify(e.message, 'error')) : busy(message, fn);
const when = v => v ? new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
const name = o => [niceValue(o.style), niceValue(o.description)].filter(x => x && x !== '—').join(' · ') || '—';

export async function loadOrders(force = false) {
  if (view.loaded && !force) return drawList();
  await search(view.q);
}

async function search(q, more = false) {
  const mine = ++view.seq;
  await run('Reading orders from Oracle…', async () => {
    const qs = new URLSearchParams();
    if (q) qs.set('q', q);
    if (more && view.next != null) qs.set('after', view.next);
    try {
      const page = await api(`/api/orders?${qs}`);
      if (mine !== view.seq) return;
      view.list = more ? view.list.concat(page.orders) : page.orders; view.next = page.next_after; view.error = null;
    } catch (e) { if (mine !== view.seq) return; if (!more) view.list = []; view.error = e.message; }
    view.q = q; view.loaded = true;
    drawList();
  });
}

function drawList() {
  const box = $('#db-orders'); const rows = view.list;
  box.innerHTML = `<section class="card db-list">
    <form class="db-search" id="db-search" role="search">
      <input id="db-q" type="search" maxlength="60" value="${esc(view.q)}" placeholder="Search Order ID, buyer, style or PO" aria-label="Search orders">
      <button class="primary">Search</button>${view.q ? '<button type="button" class="ghost" data-dbx="clear">Show all</button>' : ''}
    </form>
    ${view.error ? `<div class="ex-empty err" role="alert"><b>Orders could not be read.</b><p>${esc(view.error)}</p></div>`
    : !rows.length ? `<div class="ex-empty"><b>${view.q ? `No order matches “${esc(view.q)}”.` : 'No orders in Oracle yet.'}</b></div>`
    : `<div class="tbl-wrap"><table class="db-table"><caption class="sr-only">Orders in Oracle, newest first</caption>
      <thead><tr><th scope="col">Order ID</th><th scope="col">Order name</th><th scope="col">Buyer</th><th scope="col" class="opt">PO</th><th scope="col">Order date</th><th scope="col" class="num">Pieces</th><th scope="col">Entered</th></tr></thead>
      <tbody>${rows.map(o => `<tr class="clickable" data-order-panel="${esc(o.id)}"><th scope="row"><button class="link" data-order-panel="${esc(o.id)}">${esc(o.order_ref || '—')}</button></th>
        <td>${esc(name(o))}</td><td>${esc(niceValue(o.buyer))}</td><td class="opt">${esc(niceValue(o.po))}</td><td class="nowrap">${esc(niceValue(o.date))}</td>
        <td class="num">${o.pieces == null ? '—' : esc(fmt(o.pieces))}</td>
        <td class="nowrap">${o.poppys ? `${esc(when(o.poppys.saved_at))}<div class="muted small-text">POPPYS · ${esc(o.poppys.filename || '')}</div>` : '<span class="muted">in Axpert</span>'}</td></tr>`).join('')}</tbody></table></div>
      <div class="pager"><span>${plural(rows.length, 'order')} · newest first · click an Order ID to see everything saved for it</span>${view.next != null ? '<button class="secondary small" data-dbx="more">Load more</button>' : ''}</div>`}
  </section>`;
  $('#db-search').addEventListener('submit', e => { e.preventDefault(); search($('#db-q').value.trim()); });
  box.querySelectorAll('[data-dbx]').forEach(b => b.addEventListener('click', () => b.dataset.dbx === 'clear' ? search('') : search(view.q, true)));
}

// ------------------------------------------------------------------ the order panel (right side)
export async function orderPanel(ordmasid) {
  const mine = ++view.seq;
  await run('Reading the order from Oracle…', async () => {
    let order;
    try { order = await api(`/api/orders/${id(ordmasid)}`); }
    catch (e) { if (mine === view.seq) notify(`The order could not be read. ${e.message}`, 'error'); return; }
    if (mine !== view.seq) return;
    Object.assign(panel, { order, tab: 'details', hidden: false, system: false });
    drawPanel(true);
  });
}

const tick = ok => `<span class="db-ic ${ok ? 'ok' : 'bad'}" aria-hidden="true">${ok ? '✓' : '!'}</span>`;

function checkHtml(d) {
  const s = d.saved;
  if (!s) {
    return `<section class="card db-check plain"><p><b>Entered outside POPPYS.</b> There is no Excel file to compare with, so the order is shown as Oracle has it now.</p></section>`;
  }
  const file = `From <b>${esc(s.filename)}</b> · saved ${esc(when(s.saved_at))}${s.saved_by ? ` by ${esc(s.saved_by)}` : ''} · <a href="/api/drafts/${esc(id(s.job_id))}/original" download>Open the Excel file</a>`;
  const c = s.check;
  if (!c) return `<section class="card db-check plain"><p>${file}</p><p class="muted">The save record of this order has no rows to compare with.</p></section>`;
  const n = c.details.different.length + c.cells.different.length + c.missing_lines.length + c.extra_lines.length + (c.pieces.excel === c.pieces.oracle ? 0 : 1);
  const diffs = [
    ...c.details.different.map(x => [x.label, x.excel, x.oracle]),
    ...c.cells.different.map(x => [`${x.colour} · size ${x.size}`, x.excel, x.oracle]),
    ...c.missing_lines.map(x => [`Colour ${x}`, 'in the Excel', 'missing']),
    ...c.extra_lines.map(x => [`Colour ${x}`, 'not in the Excel', 'added']),
  ];
  const lines = [
    [!c.details.different.length, `Order details: <b>${c.details.count - c.details.different.length} of ${c.details.count}</b> same as the Excel`],
    [c.pieces.excel === c.pieces.oracle, `Pieces: Excel <b>${esc(fmt(c.pieces.excel))}</b> · Oracle <b>${esc(fmt(c.pieces.oracle))}</b>`],
    [!c.missing_lines.length && !c.extra_lines.length, `Colours: Excel <b>${c.lines.excel}</b> · Oracle <b>${c.lines.oracle}</b>`],
    [!c.cells.different.length, `Colour × size quantities: <b>${c.cells.count - c.cells.different.length} of ${c.cells.count}</b> same`],
  ];
  return `<section class="card db-check ${c.same ? 'ok' : 'bad'}" aria-live="polite">
    <div class="db-check-head">${tick(c.same)}<div><h2>${c.same ? 'Same as the Excel file' : `${plural(n, 'difference')} from the Excel file`}</h2><p class="muted small-text">${file}</p></div></div>
    <ul class="db-checks">${lines.map(([ok, text]) => `<li>${tick(ok)}<span>${text}</span></li>`).join('')}
      ${s.not_saved.length ? `<li class="muted"><span class="db-ic" aria-hidden="true">–</span><span>Not saved in Oracle (kept in POPPYS): ${esc(s.not_saved.map(x => x.replace(/ (rows|split rows)$/i, '')).join(', '))}</span></li>` : ''}</ul>
    ${diffs.length ? `<div class="tbl-wrap"><table class="db-diffs"><caption class="sr-only">Differences</caption><thead><tr><th scope="col">What</th><th scope="col">Excel</th><th scope="col">Oracle now</th></tr></thead>
      <tbody>${diffs.map(([w, e, o]) => `<tr><th scope="row">${esc(w)}</th><td>${esc(niceValue(e))}</td><td><b>${esc(niceValue(o))}</b></td></tr>`).join('')}</tbody></table></div>` : ''}
  </section>`;
}

// The Excel sheet as uploaded, cell by cell, each cell coloured by where its value is now (Oracle, kept in POPPYS, not used).
const GRID_KEYS = [['oracle', 'Saved in Oracle'], ['diff', 'Different in Oracle now'], ['kept', 'Not in Oracle yet · kept in POPPYS'],
  ['unused', 'Not used · calculation column'], ['none', 'Nothing to save · all 0 or a copy']];
const colName = c => { let s = ''; while (c) { const r = (c - 1) % 26; s = String.fromCharCode(65 + r) + s; c = (c - 1 - r) / 26; } return s; };
const cellRef = a => { const m = /^([A-Z]{1,3})(\d+)$/.exec(a); if (!m) return null; let c = 0; for (const ch of m[1]) c = c * 26 + ch.charCodeAt(0) - 64; return [Number(m[2]), c]; };
const show = v => typeof v === 'number' ? fmt(v) : typeof v === 'string' && /^\d{4}-\d{2}-\d{2}(T00:00(:00)?)?$/.test(v) ? niceValue(v) : String(v ?? '');

function gridHtml(g) {
  const hidR = new Set(panel.hidden ? [] : g.hidden_rows), hidC = new Set(panel.hidden ? [] : g.hidden_cols);
  const cells = new Map(g.cells.map(x => [`${x[0]},${x[1]}`, x]));
  const used = new Set(g.cells.map(x => x[0]));
  const cols = []; for (let c = 1; c <= g.cols; c++) if (!hidC.has(c)) cols.push(c);
  const span = new Map(), covered = new Set(), keep = new Set();
  for (const rng of g.merged) {
    const [a, b] = String(rng).split(':').map(cellRef);
    if (!a || !b || hidR.has(a[0]) || hidC.has(a[1])) continue;
    let rs = 0, cs = 0;
    for (let r = a[0]; r <= b[0]; r++) if (!hidR.has(r)) rs++;
    for (let c = a[1]; c <= b[1]; c++) if (!hidC.has(c)) cs++;
    if (rs * cs < 2) continue;
    span.set(`${a[0]},${a[1]}`, [rs, cs]);
    for (let r = a[0]; r <= b[0]; r++) keep.add(r);
    for (let r = a[0]; r <= b[0]; r++) for (let c = a[1]; c <= b[1]; c++) if (r !== a[0] || c !== a[1]) covered.add(`${r},${c}`);
  }
  const count = Object.fromEntries(GRID_KEYS.map(([k]) => [k, 0])); g.cells.forEach(x => { if (x[3]) count[x[3]]++; });
  const rows = []; let gap = null;
  const flush = () => { if (gap) { rows.push(gap[1] - gap[0] >= 2 ? `<tr class="xg-gap"><th scope="row">${gap[0]}–${gap[1]}</th><td colspan="${cols.length}">empty rows</td></tr>` : ''); if (gap[1] - gap[0] < 2) for (let r = gap[0]; r <= gap[1]; r++) rows.push(row(r)); gap = null; } };
  function row(r) {
    return `<tr><th scope="row">${r}</th>${cols.map(c => {
      const k = `${r},${c}`; if (covered.has(k)) return '';
      const x = cells.get(k); const sp = span.get(k);
      const attrs = sp ? `${sp[0] > 1 ? ` rowspan="${sp[0]}"` : ''}${sp[1] > 1 ? ` colspan="${sp[1]}"` : ''}` : '';
      if (!x) return `<td${attrs}></td>`;
      const v = show(x[2]); const where = `${colName(c)}${r}`;
      return `<td${attrs} class="${x[3] ? `xg-${x[3]}` : ''}${typeof x[2] === 'number' ? ' num' : ''}" tabindex="0" data-xg="${esc(where)}" data-v="${esc(v)}" data-n="${esc(x[4] || '')}" title="${esc(`${where}: ${v}${x[4] ? ` — ${x[4]}` : ''}`)}">${esc(v)}</td>`;
    }).join('')}</tr>`;
  }
  for (let r = 1; r <= g.rows; r++) {
    if (hidR.has(r)) { flush(); continue; }
    if (!used.has(r) && !keep.has(r)) { gap = gap ? [gap[0], r] : [r, r]; continue; }
    flush(); rows.push(row(r));
  }
  flush();
  const hiddenN = g.hidden_rows.length + g.hidden_cols.length;
  return `<section class="card db-grid" aria-labelledby="db-grid-title">
    <div class="card-head"><div><h2 id="db-grid-title">Your Excel, as uploaded · sheet “${esc(g.name)}”</h2>
      <p class="muted small-text">The same rows and columns as your file. The colour of each cell says where that value is now. Click a cell to see it in words.</p></div></div>
    <ul class="xg-legend">${GRID_KEYS.filter(([k]) => count[k] || k === 'oracle').map(([k, t]) => `<li><span class="xg-key xg-${k}" aria-hidden="true"></span>${esc(t)} <b>${fmt(count[k], 0)}</b></li>`).join('')}
      <li><span class="xg-key" aria-hidden="true"></span>Headings, labels and totals</li></ul>
    <p class="xg-info" id="xg-info" aria-live="polite">Click a cell to see where its value is.</p>
    ${hiddenN ? `<p class="small-text muted">${panel.hidden ? 'Showing' : 'Hidden in your Excel:'} ${g.hidden_rows.length ? `rows ${esc(g.hidden_rows.join(', '))}` : ''}${g.hidden_cols.length ? ` columns ${esc(g.hidden_cols.map(colName).join(', '))}` : ''} · <button class="link" data-dbx="hidden">${panel.hidden ? 'Hide them again' : 'Show them'}</button></p>` : ''}
    <div class="xg-wrap" tabindex="0" aria-label="Excel sheet ${esc(g.name)}"><table class="xg"><thead><tr><th></th>${cols.map(c => `<th scope="col">${colName(c)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>
    ${g.truncated ? '<p class="small-text muted">Only the first 2,000 rows and 60 columns are shown. Open the Excel file for the rest.</p>' : ''}
  </section>`;
}

function cellInfo(td) {
  const n = td.dataset.n;
  $('#xg-info').innerHTML = `<span class="db-cell">${esc(td.dataset.xg)}</span> <b>${esc(td.dataset.v)}</b> — ${n ? esc(n) : 'a heading, label or total in your Excel'}`;
  $('#xg-info').className = `xg-info ${[...td.classList].find(c => c.startsWith('xg-')) || ''}`;
}

// The same order straight from Oracle in SQL Developer: read-only SELECTs for this order, with what each should show.
function sqlHtml(d) {
  return `<section class="card db-sql" aria-labelledby="db-sql-title">
    <div class="card-head"><div><h2 id="db-sql-title">Check this order in SQL Developer</h2>
      <p class="muted small-text">Proof straight from Oracle, without POPPYS. These only read (SELECT); they change nothing.</p></div>
      <button class="secondary small" data-copy="all">Copy all ${d.sql.length}</button></div>
    <ol class="db-sql-steps"><li>Open SQL Developer and the <b>GARMENT_DEMO</b> connection (the worksheet).</li>
      <li>Copy a query below and paste it in the worksheet.</li>
      <li>Put the cursor in it and press <kbd>Ctrl</kbd>+<kbd>Enter</kbd>. The result grid should show what is written next to the query. (<kbd>F5</kbd> runs all of them at once.)</li></ol>
    ${d.sql.map((q, i) => `<div class="db-sql-q"><div class="db-sql-head"><b>${esc(q.title)}</b><span class="pill ok">You should see: ${esc(q.expect)}</span>
      <button class="ghost small" data-copy="${i}">Copy</button></div><pre tabindex="0">${esc(q.sql)}</pre></div>`).join('')}
  </section>`;
}

async function copySql(button, text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const pre = button.closest('.db-sql-q')?.querySelector('pre');
    if (pre) { const r = document.createRange(); r.selectNodeContents(pre); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }
    return notify('Copy is blocked by the browser. The query is selected: press Ctrl+C.', 'error');
  }
  const was = button.textContent; button.textContent = 'Copied ✓'; setTimeout(() => { button.textContent = was; }, 1500);
}


function tablesHtml(d) {
  const shown = t => t.columns.filter(c => panel.system || !c.system || c.name === t.key);
  const hiddenN = d.tables.reduce((n, t) => n + t.columns.filter(c => c.system && c.name !== t.key).length, 0);
  const val = (t, c, v) => {
    if (v == null || v === '') return '';
    const name = t.decoded?.[c]?.[String(v)];
    return name != null ? `${esc(name)} <span class="chip label" title="Record ID in the master table">${esc(v)}</span>` : esc(v);   // exactly as Oracle holds it
  };
  return `<p class="muted small-text">Every row Oracle holds for this order, with every column that has a value. Names are shown for linked records.
      ${hiddenN ? `<label class="check"><input type="checkbox" data-sys ${panel.system ? 'checked' : ''}> Show Axpert system columns (${hiddenN})</label>` : ''}</p>
    ${d.tables.map(t => `<section class="op-table"><h3><code>${esc(t.table)}</code> ${esc(t.business)} <span class="pill">${plural(t.rows.length, 'row')}</span></h3>
      <div class="tbl-wrap"><table class="pv-grid"><thead><tr>${shown(t).map(c => `<th scope="col" title="${esc(c.label)}"><code>${esc(c.name)}</code><small>${esc(c.label)}</small></th>`).join('')}</tr></thead>
      <tbody>${t.rows.map(r => `<tr>${shown(t).map(c => `<td>${val(t, c.name, r[c.name])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`).join('') || '<p class="empty">No rows.</p>'}`;
}

function drawPanel(first = false) {
  const d = panel.order; const s = d.sheet;
  const head = Object.fromEntries(s.header.map(f => [f.label, f.value]));
  const tabs = [['details', 'Details'], ['tables', `Oracle tables (${d.tables?.length || 0})`], ...(d.saved?.grid ? [['excel', 'Excel as uploaded']] : []), ...(d.sql?.length ? [['sql', 'Check in SQL Developer']] : [])];
  const body = drawer(`Order ${s.order_ref || ''}`, `<div class="op-head">
      <div><span class="op-id">${esc(s.order_ref || '—')}</span><span class="op-name">${esc(name({ style: head['Style number'], description: head['Garment description'] }))}</span>
        <span class="muted small-text">${esc(niceValue(head.Buyer))} · ${esc(fmt(s.total))} pieces · ${d.saved ? `saved by POPPYS ${esc(when(d.saved.saved_at))}` : 'entered in Axpert'}</span></div>
      ${badge(d)}</div>
    <div class="subnav db-tabs" role="tablist" aria-label="Order views">${tabs.map(([k, t]) => `<button role="tab" aria-selected="${panel.tab === k}" class="${panel.tab === k ? 'active' : ''}" data-tab="${k}">${t}</button>`).join('')}</div>
    <div class="op-body">${panel.tab === 'tables' ? tablesHtml(d) : panel.tab === 'excel' ? gridHtml(d.saved.grid) : panel.tab === 'sql' ? sqlHtml(d)
      : `${checkHtml(d)}<div class="db-sheet">${sheetHtml(s)}</div><p class="muted small-text db-read">Read from Oracle ${esc(when(d.read_at))}</p>`}</div>`);
  $('#drawer').classList.add('wide');
  body.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => { panel.tab = b.dataset.tab; drawPanel(); $(`#drawer [data-tab="${panel.tab}"]`)?.focus(); }));
  body.querySelector('[data-sys]')?.addEventListener('change', e => { panel.system = e.target.checked; drawPanel(); });
  body.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', () => copySql(b, b.dataset.copy === 'all'
    ? d.sql.map(q => `-- ${q.title} (expect: ${q.expect})\n${q.sql}`).join('\n\n') : d.sql[Number(b.dataset.copy)].sql)));
  body.querySelectorAll('td[data-xg]').forEach(td => { td.addEventListener('click', () => cellInfo(td)); td.addEventListener('focus', () => cellInfo(td)); });
  body.querySelector('[data-dbx="hidden"]')?.addEventListener('click', () => { panel.hidden = !panel.hidden; drawPanel(); });
  if (first) body.scrollTop = 0;
}

function badge(d) {
  if (!d.saved?.check) return '<span class="pill">Read from Oracle</span>';
  return d.saved.check.same ? '<span class="pill ok">✓ Same as the Excel</span>' : '<span class="pill err">⚠ Different from the Excel</span>';
}
