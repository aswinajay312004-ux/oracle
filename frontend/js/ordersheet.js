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
  return `<dl class="os-details">${head.map(f => `<div title="${esc(f.column)}"><dt>${esc(LABELS[f.label] || f.label)}</dt><dd>${esc(niceValue(f.value))}</dd></div>`).join('')}</dl>`;
}

function sizeTable(sheet) {
  if (!sheet.lines.length) return '<p class="muted">No colour lines were read back for this order.</p>';
  const ships = new Set(sheet.lines.map(l => l.ship_date || ''));
  const showShip = ships.size > 1;
  return `<div class="tbl-wrap os-table"><table>
    <caption class="sr-only">Pieces by colour and size</caption>
    <thead><tr><th scope="col">Colour</th>${showShip ? '<th scope="col">Ship date</th>' : ''}${sheet.sizes.map(s => `<th scope="col" class="num">${esc(s)}</th>`).join('')}<th scope="col" class="num">Total</th></tr></thead>
    <tbody>${sheet.lines.map(l => `<tr><th scope="row">${esc(niceValue(l.colour))}</th>${showShip ? `<td class="nowrap">${esc(niceValue(l.ship_date))}</td>` : ''}
      ${sheet.sizes.map(s => `<td class="num">${l.sizes[s] == null ? '<span class="muted">–</span>' : esc(fmt(l.sizes[s]))}</td>`).join('')}<td class="num"><b>${esc(fmt(l.total))}</b></td></tr>`).join('')}</tbody>
    <tfoot><tr><th scope="row">Total</th>${showShip ? '<td></td>' : ''}${sheet.sizes.map(s => `<td class="num">${esc(fmt(sheet.size_totals[s] ?? 0))}</td>`).join('')}<td class="num"><b>${esc(fmt(sheet.total))}</b></td></tr></tfoot>
  </table></div>`;
}

export function sheetHtml(sheet, { shipNote = true, planned = false, series = '' } = {}) {
  const ship = [...new Set(sheet.lines.map(l => l.ship_date).filter(Boolean))];
  return `<article class="order-sheet">
    <header class="os-head"><div><span class="eyebrow">OC No</span>${planned && /given on save/.test(sheet.order_ref || '')
      ? `<h2 class="os-ref os-pending">Assigned when saved</h2>${series ? `<p class="muted small-text">Next number in ${esc(series)}</p>` : ''}`
      : `<h2 class="os-ref">${esc(sheet.order_ref || '—')}</h2>`}</div>
      ${planned ? '<span class="pill info">Preview · not saved yet</span>'
    : `<span class="pill ${sheet.verified ? 'ok' : 'err'}">${sheet.verified ? '✓ Verified in Oracle' : '⚠ Differences found'}</span>`}</header>
    ${details(sheet)}
    <h3 class="os-sub">Pieces by colour and size${shipNote && ship.length === 1 ? ` <span class="muted">· ship ${esc(niceValue(ship[0]))}</span>` : ''}</h3>
    ${sizeTable(sheet)}
  </article>`;
}

// Full-screen view of the saved order(s), with print. A native <dialog> keeps the keyboard inside and closes on Esc.
export function openFullView(sheets, { title = 'Order saved in Oracle', readAt = '' } = {}) {
  document.querySelector('dialog.fullview')?.remove();
  const d = document.createElement('dialog');
  d.className = 'fullview'; d.setAttribute('aria-label', title);
  d.innerHTML = `<div class="fv-bar"><div><span class="eyebrow">As Oracle has it now</span><h1>${esc(title)}</h1>
      ${readAt ? `<p class="muted small-text">Read from Oracle ${esc(new Date(readAt).toLocaleString())}</p>` : ''}</div>
      <div class="fv-actions"><button class="secondary" data-fv="print">Print</button><button class="primary" data-fv="close">Close</button></div></div>
    <div class="fv-body">${sheets.length ? sheets.map(s => sheetHtml(s)).join('') : '<p class="empty">Oracle returned no order rows for this save.</p>'}</div>`;
  d.addEventListener('click', e => {
    const b = e.target.closest('[data-fv]'); if (!b) return;
    if (b.dataset.fv === 'print') window.print(); else d.close();
  });
  d.addEventListener('close', () => d.remove());
  document.body.appendChild(d); d.showModal();
  d.querySelector('[data-fv="close"]').focus();
  return d;
}
