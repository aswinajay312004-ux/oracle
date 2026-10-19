// A saved order as people read it: the order details and a colour × size table, every value from the fresh read of
// Oracle (graph.sheets, built in backend/erp/readback.order_sheets). Shown on the Saved screen and full screen.
import { esc, fmt } from './util.js';

const DATE = /^\d{4}-\d{2}-\d{2}/;
export function niceValue(v) {
  if (v == null || v === '') return '—';
  if (typeof v === 'string' && DATE.test(v)) {
    const d = new Date(v.slice(0, 10) + 'T00:00:00');
    if (!Number.isNaN(d.getTime())) return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  return typeof v === 'number' ? fmt(v) : String(v);
}

// Labels written for the order screen; anything else keeps the write mapping's own label.
const LABELS = { 'Order reference (OC number)': 'OC No', 'Buyer PO / contract number': 'PO / Contract No', 'Order confirmation date': 'Order confirmed on',
  'Total order quantity (pieces)': 'Total pieces', 'Garment description': 'Description', 'Style number': 'Style #' };

function details(sheet) {
  const head = sheet.header.filter(f => f.label !== 'Order reference (OC number)');
  // a value that differs from the Excel file (Database screen) also says what the Excel had
  return `<dl class="os-details">${head.map(f => `<div title="${esc(f.column)}" ${'excel' in f ? 'class="os-diff"' : ''}><dt>${esc(LABELS[f.label] || f.label)}</dt><dd>${esc(niceValue(f.value))}${'excel' in f ? `<small>Excel: ${esc(niceValue(f.excel))}</small>` : ''}</dd></div>`).join('')}</dl>`;
}

// a quantity that differs from the Excel file (line.excel, Database screen) is marked and says what the Excel had
function cell(l, s) {
  const v = l.sizes[s] == null ? '<span class="muted">–</span>' : esc(fmt(l.sizes[s]));
  if (!l.excel || !Object.hasOwn(l.excel, s)) return `<td class="num">${v}</td>`;
  const was = l.excel[s] == null ? '–' : fmt(l.excel[s]);
  return `<td class="num os-diff" title="Excel: ${esc(was)}"><b>${v}</b><small>Excel ${esc(was)}</small></td>`;
}

function sizeTable(sheet) {
  if (!sheet.lines.length) return '<p class="muted">No colour lines were read back for this order.</p>';
  const ships = new Set(sheet.lines.map(l => l.ship_date || ''));
  const showShip = ships.size > 1;
  return `<div class="tbl-wrap os-table"><table>
    <caption class="sr-only">Pieces by colour and size</caption>
    <thead><tr><th scope="col">Colour</th>${showShip ? '<th scope="col">Ship date</th>' : ''}${sheet.sizes.map(s => `<th scope="col" class="num">${esc(s)}</th>`).join('')}<th scope="col" class="num">Total</th></tr></thead>
    <tbody>${sheet.lines.map(l => `<tr ${l.extra ? 'class="os-extra"' : ''}><th scope="row">${esc(niceValue(l.colour))}${l.extra ? ' <small>not in the Excel</small>' : ''}</th>${showShip ? `<td class="nowrap">${esc(niceValue(l.ship_date))}</td>` : ''}
      ${sheet.sizes.map(s => cell(l, s)).join('')}<td class="num"><b>${esc(fmt(l.total))}</b></td></tr>`).join('')}</tbody>
    <tfoot><tr><th scope="row">Total</th>${showShip ? '<td></td>' : ''}${sheet.sizes.map(s => `<td class="num">${esc(fmt(sheet.size_totals[s] ?? 0))}</td>`).join('')}<td class="num"><b>${esc(fmt(sheet.total))}</b></td></tr></tfoot>
  </table></div>`;
}

export function sheetHtml(sheet, { shipNote = true, planned = false, series = '', next = '', badge = '' } = {}) {
  const ship = [...new Set(sheet.lines.map(l => l.ship_date).filter(Boolean))];
  return `<article class="order-sheet">
    <header class="os-head"><div><span class="eyebrow">OC No</span>${planned && /given on save/.test(sheet.order_ref || '')
      ? (next ? `<h2 class="os-ref">${esc(next)}</h2><p class="muted small-text">Next number — confirmed when saved</p>`
        : `<h2 class="os-ref os-pending">Assigned when saved</h2>${series ? `<p class="muted small-text">Next number in ${esc(series)}</p>` : ''}`)
      : `<h2 class="os-ref">${esc(sheet.order_ref || '—')}</h2>`}</div>
      ${badge || (planned ? '<span class="pill info">Preview · not saved yet</span>'
    : sheet.verified == null ? '<span class="pill">Read from Oracle</span>'
    : `<span class="pill ${sheet.verified ? 'ok' : 'err'}">${sheet.verified ? '✓ Verified in Oracle' : '⚠ Differences found'}</span>`)}</header>
    ${details(sheet)}
    <h3 class="os-sub">Pieces by colour and size${shipNote && ship.length === 1 ? ` <span class="muted">· ship ${esc(niceValue(ship[0]))}</span>` : ''}</h3>
    ${sizeTable(sheet)}
  </article>`;
}

// Full-screen view of the saved order(s), with print. A native <dialog> keeps the keyboard inside and closes on Esc.
export function openFullView(sheets, { title = 'Order saved in Oracle', readAt = '', badge = '' } = {}) {
  document.querySelector('dialog.fullview')?.remove();
  const d = document.createElement('dialog');
  d.className = 'fullview'; d.setAttribute('aria-label', title);
  d.innerHTML = `<div class="fv-bar"><div><span class="eyebrow">As Oracle has it now</span><h1>${esc(title)}</h1>
      ${readAt ? `<p class="muted small-text">Read from Oracle ${esc(new Date(readAt).toLocaleString())}</p>` : ''}</div>
      <div class="fv-actions"><button class="secondary" data-fv="print">Print</button><button class="primary" data-fv="close">Close</button></div></div>
    <div class="fv-body">${sheets.length ? sheets.map(s => sheetHtml(s, { badge })).join('') : '<p class="empty">Oracle returned no order rows for this save.</p>'}</div>`;
  d.addEventListener('click', e => {
    const b = e.target.closest('[data-fv]'); if (!b) return;
    if (b.dataset.fv === 'print') window.print(); else d.close();
  });
  d.addEventListener('close', () => d.remove());
  document.body.appendChild(d); d.showModal();
  d.querySelector('[data-fv="close"]').focus();
  return d;
}

// The popup before saving: each Oracle table that receives values, open and in business words. Record IDs and the links
// between new records are left out (they are generated on save); masters show their names.
function grid(n, nexts, same = new Set()) {
  const rows = n.records.filter(r => r.kind === 'written');
  const linkCol = c => /link|belongs to/i.test((n.labels || {})[c] || '');
  const shown = c => c !== n.key && !linkCol(c) && rows.some(r => { const v = r.live?.[c]; return (v != null && v !== '' && !(typeof v === 'string' && v.startsWith('$'))) || r.decoded?.[c] != null; });
  const label = c => (n.labels || {})[c] || c;
  const cell = (r, c, i) => {
    const v = r.decoded?.[c] ?? r.live?.[c];
    if (typeof v === 'string' && v.includes('(OC No. given on save)') && n.kind === 'header') return nexts[i] || 'Given when saved';
    return niceValue(v);
  };
  // a line column that only repeats a value of the order (its PO, style, unit…) is not shown again
  const cols = [...new Set(rows.flatMap(r => r.written_columns || []))].filter(shown)
    .filter(c => !(same.size && rows.every((r, i) => same.has(cell(r, c, i)))));
  if (rows.length === 1) {
    return `<dl class="os-details">${cols.map(c => `<div title="${esc(n.table + '.' + c)}"><dt>${esc(label(c))}</dt><dd>${esc(cell(rows[0], c, 0))}</dd></div>`).join('')}</dl>`;
  }
  return `<div class="tbl-wrap"><table><thead><tr>${cols.map(c => `<th scope="col" title="${esc(n.table + '.' + c)}">${esc(label(c))}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr>${cols.map(c => `<td>${esc(cell(r, c, i))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function sizeMatrix(sheet) {
  return `<div class="tbl-wrap os-table"><table><thead><tr><th scope="col">Colour</th>${sheet.sizes.map(s => `<th scope="col" class="num">${esc(s)}</th>`).join('')}<th scope="col" class="num">Total</th></tr></thead>
    <tbody>${sheet.lines.map(l => `<tr><th scope="row">${esc(niceValue(l.colour))}</th>${sheet.sizes.map(s => `<td class="num">${l.sizes[s] == null ? '–' : esc(fmt(l.sizes[s]))}</td>`).join('')}<td class="num"><b>${esc(fmt(l.total))}</b></td></tr>`).join('')}</tbody>
    <tfoot><tr><th scope="row">Total</th>${sheet.sizes.map(s => `<td class="num">${esc(fmt(sheet.size_totals[s] ?? 0))}</td>`).join('')}<td class="num"><b>${esc(fmt(sheet.total))}</b></td></tr></tfoot></table></div>`;
}

const section = (title, table, count, body) => `<section class="pv"><h3>${esc(title)} <span class="pv-to">→ Oracle ${esc(table)}${count ? ` · ${esc(count)}` : ''}</span></h3>${body}</section>`;

export function previewHtml(graph, nexts = {}) {
  const nodes = (graph?.nodes || []).filter(n => !n.reference_only && (n.records || []).some(r => r.kind === 'written'));
  const header = nodes.find(n => n.kind === 'header');
  const sizes = nodes.find(n => n.table === 'ORDSIZE');
  const details = nodes.filter(n => n.kind === 'detail' && n !== sizes);
  const masters = nodes.filter(n => n.kind === 'master');
  const sheets = graph?.sheets || [];
  const rows = n => n.records.filter(r => r.kind === 'written').length;
  const out = [];
  if (header) out.push(section('Order', header.table, '', grid(header, nexts)));
  const orderValues = new Set(header ? header.records.filter(r => r.kind === 'written').flatMap(r =>
    Object.keys(r.live || {}).map(c => niceValue(r.decoded?.[c] ?? r.live?.[c]))) : []);
  details.forEach(n => out.push(section(n.business.replace(/^Order Entry — /, '').replace(/^./, c => c.toUpperCase()), n.table, `${rows(n)} rows`, grid(n, nexts, orderValues))));
  if (sizes) out.push(section('Size quantities', sizes.table, `${rows(sizes)} rows`, sheets.map(sizeMatrix).join('') || grid(sizes, nexts)));
  const names = masters.flatMap(n => n.records.filter(r => r.kind === 'written').map(r => {
    const name = Object.entries(r.live || {}).find(([c, v]) => c !== n.key && typeof v === 'string' && !v.startsWith('$'));
    return { what: (n.business || n.table).replace(/ master$/i, ''), name: name ? name[1] : '', table: n.table };
  }));
  if (names.length) {
    out.push(section('New names added', 'masters', `${names.length}`, `<div class="tbl-wrap"><table><thead><tr><th scope="col">What</th><th scope="col">Name</th><th scope="col">Oracle table</th></tr></thead>
      <tbody>${names.map(x => `<tr><td>${esc(x.what)}</td><td><b>${esc(x.name)}</b></td><td class="muted">${esc(x.table)}</td></tr>`).join('')}</tbody></table></div>`));
  }
  return out.join('');
}
