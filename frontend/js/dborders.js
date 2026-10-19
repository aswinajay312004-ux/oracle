// Database screen, Orders: the orders in Oracle by business name, newest first, and one order as people check it — the
// order sheet read from Oracle now and, for an order POPPYS saved, each value compared with the Excel file it came from.
import { $, $$, esc, fmt, plural, notify, state, busy } from './util.js';
import { api, id } from './api.js';
import { sheetHtml, niceValue, openFullView, LABELS } from './ordersheet.js';
import { showRecord } from './explorer.js';

const view = { q: '', list: [], next: null, loaded: false, error: null, order: null, seq: 0, tab: 'excel', hidden: false };   // seq: the newest read wins
const run = (message, fn) => state.busy ? fn().catch(e => notify(e.message, 'error')) : busy(message, fn);
const when = v => v ? new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';

export async function loadOrders(force = false) {
  if (view.order && !force) return drawOrder();
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
    view.q = q; view.loaded = true; view.order = null;
    drawList();
  });
}

function drawList() {
  const box = $('#db-orders');
  const rows = view.list;
  box.innerHTML = `<section class="card db-list">
    <form class="db-search" id="db-search" role="search">
      <input id="db-q" type="search" maxlength="60" value="${esc(view.q)}" placeholder="Search OC No, buyer, style or PO" aria-label="Search orders">
      <button class="primary">Search</button>${view.q ? '<button type="button" class="ghost" data-dbx="clear">Show all</button>' : ''}
    </form>
    ${view.error ? `<div class="ex-empty err" role="alert"><b>Orders could not be read.</b><p>${esc(view.error)}</p></div>`
    : !rows.length ? `<div class="ex-empty"><b>${view.q ? `No order matches “${esc(view.q)}”.` : 'No orders in Oracle yet.'}</b>${view.q ? '<p>Try part of the OC No (for example 1321), the buyer or the style.</p>' : ''}</div>`
    : `<div class="tbl-wrap"><table class="db-table"><caption class="sr-only">Orders in Oracle, newest first</caption>
      <thead><tr><th scope="col">OC No</th><th scope="col">Date</th><th scope="col">Buyer</th><th scope="col">Style</th><th scope="col" class="opt">Description</th><th scope="col" class="opt">PO</th><th scope="col" class="num">Pieces</th><th scope="col">Saved by</th></tr></thead>
      <tbody>${rows.map((o, i) => `<tr class="clickable" data-oid="${i}"><th scope="row"><button class="link" data-oid="${i}">${esc(o.order_ref || '—')}</button></th>
        <td class="nowrap">${esc(niceValue(o.date))}</td><td>${esc(niceValue(o.buyer))}</td><td>${esc(niceValue(o.style))}</td><td class="opt">${esc(niceValue(o.description))}</td><td class="opt">${esc(niceValue(o.po))}</td>
        <td class="num">${o.pieces == null ? '—' : esc(fmt(o.pieces))}</td>
        <td>${o.poppys ? `<span class="pill ok" title="${esc(`From ${o.poppys.filename} · ${when(o.poppys.saved_at)}`)}">POPPYS</span>` : '<span class="pill" title="Not saved by POPPYS on this computer">Axpert</span>'}</td></tr>`).join('')}</tbody></table></div>
      <div class="pager"><span>${plural(rows.length, 'order')} · newest first · click an order to check it</span>${view.next != null ? '<button class="secondary small" data-dbx="more">Load more</button>' : ''}</div>`}
  </section>`;
  $('#db-search').addEventListener('submit', e => { e.preventDefault(); search($('#db-q').value.trim()); });
  box.querySelectorAll('[data-dbx]').forEach(b => b.addEventListener('click', () => b.dataset.dbx === 'clear' ? search('') : search(view.q, true)));
  $$('tr[data-oid]', box).forEach(tr => tr.addEventListener('click', () => openOrder(rows[Number(tr.dataset.oid)].id)));
}

export async function openOrder(ordmasid) {
  const mine = ++view.seq;
  await run('Reading the order from Oracle…', async () => {
    let order;
    try { order = await api(`/api/orders/${id(ordmasid)}`); }
    catch (e) { if (mine === view.seq) notify(`The order could not be read. ${e.message}`, 'error'); return; }
    if (mine !== view.seq) return;
    view.order = order; view.tab = order.saved?.grid ? 'excel' : 'oracle'; view.hidden = false;
    drawOrder();
    $('#view-explorer')?.scrollIntoView({ block: 'start' });
  });
}

// "Open in Database" from the Saved screen: the order with that OC No, or the orders matching it.
export async function findOrder(ref) {
  ref = String(ref ?? '').trim().slice(0, 60);
  await search(ref);
  const same = view.list.filter(o => String(o.order_ref || '').toUpperCase() === ref.toUpperCase());
  if (same.length === 1) await openOrder(same[0].id);
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

// Every value of the Excel file and where it is now: cell → value → Oracle, then what POPPYS filled in (not in the
// file) and what is in the file but not in Oracle yet, so a person can tick the file off line by line.
function mapHtml(x) {
  if (!x) return '';
  const name = l => LABELS[l] || l;
  const cell = c => c ? `<span class="db-cell">${esc(c)}</span>` : '<span class="muted">—</span>';
  const group = (title, note, rows) => rows.length ? `<tbody><tr class="rl-group"><th colspan="4" scope="colgroup">${esc(title)}${note ? ` <span class="muted">· ${esc(note)}</span>` : ''}</th></tr>${rows.join('')}</tbody>` : '';
  return `<section class="card db-map" aria-labelledby="db-map-title">
    <div class="card-head"><div><h2 id="db-map-title">Your Excel → Oracle</h2><p class="muted small-text">Each value with the Excel cell it was read from. Open the Excel file beside this to tick them off.</p></div></div>
    <div class="tbl-wrap"><table class="db-maptable"><thead><tr><th scope="col">What</th><th scope="col">Excel cell</th><th scope="col">In the Excel</th><th scope="col">In Oracle now</th></tr></thead>
    ${group('Saved in Oracle', '', x.from_file.map(r => `<tr><th scope="row">${r.state === 'changed' ? '<span class="db-ic info" aria-hidden="true">↔</span>' : tick(r.state === 'same')} ${esc(name(r.label))}</th><td data-label="Excel cell">${cell(r.cell)}</td><td data-label="In the Excel">${esc(niceValue(r.excel))}</td>
      <td class="${r.state === 'different' ? 'db-bad' : ''}" data-label="In Oracle now"><b>${esc(niceValue(r.oracle))}</b>${r.state === 'changed' ? '<small class="db-note">changed before saving (edited, or matched to an existing name)</small>' : r.state === 'different' ? '<small class="db-note">changed in Oracle after saving</small>' : ''}</td></tr>`))}
    ${group('Filled in by POPPYS', 'not in the Excel', x.filled.map(r => `<tr><th scope="row"><span class="db-ic" aria-hidden="true">+</span> ${esc(name(r.label))}</th><td data-label="Excel cell">${cell(null)}</td><td class="muted" data-label="In the Excel">${esc(r.why)}</td><td data-label="In Oracle now"><b>${esc(niceValue(r.value))}</b></td></tr>`))}
    ${group('Not in Oracle yet', 'kept in POPPYS with this order', x.kept.map(r => `<tr class="db-kept"><th scope="row"><span class="db-ic" aria-hidden="true">–</span> ${esc(name(r.label.replace(/ (rows|split rows)$/i, '')))}</th><td data-label="Excel cell">${cell(r.cell)}</td><td data-label="In the Excel">${esc(r.excel)}</td><td class="muted" data-label="In Oracle now">${esc(r.why)}</td></tr>`))}
    </table></div></section>`;
}

// The Excel sheet as uploaded, cell by cell, each cell coloured by where its value is now (Oracle, kept in POPPYS, not used).
const GRID_KEYS = [['oracle', 'Saved in Oracle'], ['diff', 'Different in Oracle now'], ['kept', 'Not in Oracle yet · kept in POPPYS'],
  ['unused', 'Not used · calculation column'], ['none', 'Nothing to save · all 0 or a copy']];
const colName = c => { let s = ''; while (c) { const r = (c - 1) % 26; s = String.fromCharCode(65 + r) + s; c = (c - 1 - r) / 26; } return s; };
const cellRef = a => { const m = /^([A-Z]{1,3})(\d+)$/.exec(a); if (!m) return null; let c = 0; for (const ch of m[1]) c = c * 26 + ch.charCodeAt(0) - 64; return [Number(m[2]), c]; };
const show = v => typeof v === 'number' ? fmt(v) : typeof v === 'string' && /^\d{4}-\d{2}-\d{2}(T00:00(:00)?)?$/.test(v) ? niceValue(v) : String(v ?? '');

function gridHtml(g) {
  const hidR = new Set(view.hidden ? [] : g.hidden_rows), hidC = new Set(view.hidden ? [] : g.hidden_cols);
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
    ${hiddenN ? `<p class="small-text muted">${view.hidden ? 'Showing' : 'Hidden in your Excel:'} ${g.hidden_rows.length ? `rows ${esc(g.hidden_rows.join(', '))}` : ''}${g.hidden_cols.length ? ` columns ${esc(g.hidden_cols.map(colName).join(', '))}` : ''} · <button class="link" data-dbx="hidden">${view.hidden ? 'Hide them again' : 'Show them'}</button></p>` : ''}
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

function badge(d) {
  if (!d.saved?.check) return '<span class="pill">Read from Oracle</span>';
  return d.saved.check.same ? '<span class="pill ok">✓ Same as the Excel</span>' : '<span class="pill err">⚠ Different from the Excel</span>';
}

function drawOrder() {
  const d = view.order; const box = $('#db-orders');
  const differs = new Map((d.saved?.check?.details.different || []).map(x => [x.label, x.excel]));
  const sheet = { ...d.sheet, header: d.sheet.header.map(f => differs.has(f.label) ? { ...f, excel: differs.get(f.label) } : f) };
  box.innerHTML = `<div class="db-order">
    <div class="db-order-bar"><button class="ghost small" data-dbx="back">← All orders</button><span class="spacer"></span>
      <button class="secondary small" data-dbx="full">Full screen · Print</button><button class="ghost small" data-dbx="tech" title="The Oracle tables, columns and record IDs behind this order">Oracle tables &amp; IDs</button></div>
    ${checkHtml(d)}
    <div class="subnav db-tabs" role="tablist" aria-label="How to show this order">${[
      ...(d.saved?.grid ? [['excel', 'As in your Excel']] : []), ...(d.saved?.excel ? [['values', 'Value by value']] : []), ['oracle', 'As in Oracle'],
      ...(d.sql?.length ? [['sql', 'Check in SQL Developer']] : [])].map(([k, t]) =>
      `<button role="tab" aria-selected="${view.tab === k}" class="${view.tab === k ? 'active' : ''}" data-tab="${k}">${t}</button>`).join('')}</div>
    ${view.tab === 'excel' && d.saved?.grid ? gridHtml(d.saved.grid) : view.tab === 'values' && d.saved?.excel ? mapHtml(d.saved.excel)
      : view.tab === 'sql' && d.sql?.length ? sqlHtml(d)
      : `<div class="card db-sheet">${sheetHtml(sheet, { badge: badge(d) })}<p class="muted small-text db-read">Read from Oracle ${esc(when(d.read_at))}</p></div>`}
  </div>`;
  box.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => { view.tab = b.dataset.tab; drawOrder(); box.querySelector(`[data-tab="${view.tab}"]`)?.focus(); }));
  box.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', () => copySql(b, b.dataset.copy === 'all'
    ? d.sql.map(q => `-- ${q.title} (expect: ${q.expect})\n${q.sql}`).join('\n\n') : d.sql[Number(b.dataset.copy)].sql)));
  box.querySelectorAll('td[data-xg]').forEach(td => { td.addEventListener('click', () => cellInfo(td)); td.addEventListener('focus', () => cellInfo(td)); });
  box.querySelectorAll('[data-dbx]').forEach(b => b.addEventListener('click', () => ({
    back: () => { view.order = null; drawList(); },
    full: () => openFullView([sheet], { title: `Order ${d.sheet.order_ref || ''}`.trim(), readAt: d.read_at, badge: badge(d) }),
    tech: () => showRecord('ORDMAS', d.sheet.ordmasid),
    hidden: () => { view.hidden = !view.hidden; drawOrder(); box.querySelector('[data-dbx="hidden"]')?.focus(); },
  })[b.dataset.dbx]()));
  if (!box.contains(document.activeElement)) box.querySelector('[data-dbx="back"]').focus();
}
