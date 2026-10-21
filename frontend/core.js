'use strict';
/* Shared helpers: API, routing, drawer, modal, icons. Plain JavaScript; no external assets (offline). */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const App = { routes: {}, state: { user: null, health: null }, pending: 0 };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = v => v == null || v === '' ? '—' : typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: 5 }) : String(v);
// Identifiers and codes are shown exactly as stored; only quantities and amounts are grouped.
const isCode = col => /(ID|NO|NUM|SUBNO|CODE|KEY|ROW)$/.test(String(col || '')) || ['BUYER', 'MERCH', 'UOM', 'PUOM', 'COMBO', 'GSIZE', 'STNO', 'STYLENAME', 'STYLEDESC', 'SNO', 'ORDSIZEROW'].includes(col);
const val = (col, v) => v == null || v === '' ? '—' : isCode(col) ? String(v) : fmt(v);
const num = v => Number.isFinite(Number(v)) ? Number(v) : 0;
const when = v => { if (!v) return '—'; const d = new Date(String(v).replace(' ', 'T') + (/[zZ+]|\d-\d\d:\d\d$/.test(v) ? '' : 'Z')); return isNaN(d) ? esc(v) : d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }); };
const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? '' : 's'}`;

const ICONS = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z',
  upload: 'M12 16V4m0 0-4 4m4-4 4 4M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  database: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zm8 3v12c0 1.7-3.6 3-8 3s-8-1.3-8-3V6m16 6c0 1.7-3.6 3-8 3s-8-1.3-8-3',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-2.2-1.3L14.3 3h-4l-.4 2.4a7.6 7.6 0 0 0-2.2 1.3l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1c.7.6 1.4 1 2.2 1.3l.4 2.4h4l.4-2.4c.8-.3 1.5-.7 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3z',
  x: 'M6 6l12 12M18 6 6 18', check: 'M5 12.5l4.5 4.5L19 7', plus: 'M12 5v14M5 12h14', search: 'M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm9 3-4.3-4.3',
  file: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zm0 0v5h5', fileplus: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zm0 0v5h5M12 11v6m-3-3h6',
  dots: 'M12 6h.01M12 12h.01M12 18h.01', eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  edit: 'M4 20h4L19 9l-4-4L4 16zm9-13 4 4', flow: 'M5 5h5v4H5zM14 15h5v4h-5zM14 5h5v4h-5zM7.5 9v3.5h9V15M16.5 9v6',
  chart: 'M4 20V10m6 10V4m6 16v-7m4 7H3', history: 'M3 12a9 9 0 1 0 3-6.7M3 4v4h4m5-1v5l3 2', link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  table: 'M3 5h18v14H3zM3 10h18M3 15h18M9 5v14', sparkle: 'M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-5v-5m0-3h.01',
  alert: 'M12 9v4m0 4h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z', zoomin: 'M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm9 3-4.3-4.3M11 8v6M8 11h6',
  zoomout: 'M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm9 3-4.3-4.3M8 11h6', fit: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5', undo: 'M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3',
  redo: 'M15 14l5-5-5-5M20 9H9a5 5 0 0 0 0 10h3', save: 'M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm2 0v6h8V3M7 21v-7h10v7',
  arrow: 'M5 12h14m-5-5 5 5-5 5', back: 'M19 12H5m5-5-5 5 5 5', box: 'M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8', clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-13v4l3 2',
  backup: 'M12 3v12m0 0-4-4m4 4 4-4M5 21h14', key: 'M15 7a4 4 0 1 1-3.9 5H3v3h3v3h3v-3h2.1A4 4 0 0 1 15 7z', cpu: 'M8 8h8v8H8zM4 9h2m-2 6h2m12-6h2m-2 6h2M9 4v2m6-2v2M9 18v2m6-2v2',
};
const icon = (name, cls = '') => `<span class="ico ${cls}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICONS[name] || ''}"/></svg></span>`;
function paintIcons(root = document) { $$('[data-icon]', root).forEach(el => { el.outerHTML = icon(el.dataset.icon, el.className.replace('ico', '').trim()); }); }

function friendly(detail, status) {
  if (Array.isArray(detail)) return detail.map(d => d.msg || String(d)).join('\n');
  if (detail && typeof detail === 'object') return 'The request could not be completed.';
  return String(detail || (status >= 500 ? 'Something went wrong on this computer. Please try again.' : 'The request could not be completed.'));
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const init = { credentials: 'same-origin', ...options, headers };
  if (options.body && !(options.body instanceof FormData)) { headers.set('Content-Type', 'application/json'); init.body = JSON.stringify(options.body); }
  let response;
  try { response = await fetch(path, init); } catch (_) { throw new Error('Connection to the POPPYS application was interrupted. Your work is kept; try again.'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/api/login') && !path.startsWith('/api/setup')) showAuth();
    const err = new Error(friendly(data.detail, response.status)); err.status = response.status; throw err;
  }
  return data;
}
async function busy(message, action) {
  App.pending++; $('#loading-text').textContent = message; const timer = setTimeout(() => { $('#loading').hidden = false; }, 180);
  try { return await action(); }
  catch (e) { toast(e.message, 'attention'); return undefined; }
  finally { clearTimeout(timer); if (--App.pending <= 0) { App.pending = 0; $('#loading').hidden = true; } }
}
let toastTimer;
function toast(message, kind = '') { const t = $('#toast'); t.textContent = message; t.className = `toast ${kind}`; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, kind ? 7000 : 3500); }

/* drawer: right-side detail panel with optional tabs */
const Drawer = {
  open({ eyebrow = '', title = '', tabs = null, active = null, render, after = null, wide = false }) {
    this.render = render; this.after = after; $('#drawer-eyebrow').textContent = eyebrow; $('#drawer-title').textContent = title; $('#drawer').classList.toggle('wide', wide);
    const tabEl = $('#drawer-tabs'); tabEl.hidden = !tabs;
    if (tabs) { tabEl.innerHTML = tabs.map(([k, l]) => `<button type="button" data-tab="${k}">${esc(l)}</button>`).join(''); }
    $('#drawer').hidden = false; $('#drawer-backdrop').hidden = false; this.show(active || (tabs ? tabs[0][0] : null));
    setTimeout(() => $('#drawer-close').focus(), 30);
  },
  async show(tab) { this.tab = tab; $$('#drawer-tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab)); const body = $('#drawer-body'); body.innerHTML = '<div class="empty"><span class="spinner"></span></div>';
    try { const html = await this.render(tab, body); if (typeof html === 'string') body.innerHTML = html; paintIcons(body); if (this.after) this.after(body, tab); } catch (e) { body.innerHTML = `<div class="callout attention">${icon('alert')}<div>${esc(e.message)}</div></div>`; paintIcons(body); } },
  close() { $('#drawer').hidden = true; $('#drawer-backdrop').hidden = true; this.render = null; },
};
const Modal = {
  open(title, html, { full = false, onClose = null } = {}) { $('#modal-title').textContent = title; $('#modal-body').innerHTML = html; $('#modal').classList.toggle('fullscreen', full);
    $('#modal').hidden = false; $('#modal-backdrop').hidden = false; this.onClose = onClose; paintIcons($('#modal')); return $('#modal-body'); },
  close() { if (this.onClose && this.onClose() === false) return; $('#modal').hidden = true; $('#modal-backdrop').hidden = true; $('#modal-body').innerHTML = ''; this.onClose = null; },
};
async function confirmDialog(title, message, action = 'Continue') {
  return new Promise(resolve => {
    const body = Modal.open(title, `<p>${message}</p><div class="modal-foot"><button class="btn" data-no type="button">Cancel</button><button class="btn primary" data-yes type="button">${esc(action)}</button></div>`, { onClose: () => resolve(false) });
    $('[data-no]', body).onclick = () => Modal.close(); $('[data-yes]', body).onclick = () => { Modal.onClose = null; Modal.close(); resolve(true); };
  });
}

/* Business-friendly names for Oracle tables (actual identifiers always shown alongside). */
const TABLE_LABELS = { ORDMAS: 'Order header', ORDSTYLE: 'Order lines', ORDSIZE: 'Size quantities', BUYMAS: 'Buyers', STYLENUM: 'Style numbers', STYLEMAS: 'Garment descriptions',
  SIZE_MAS: 'Sizes', COLORMAS: 'Colours', UNITMAS: 'Units', MERCHMAS: 'Merchandisers', PARTYMAS: 'Parties', ORDSHIP: 'Shipments', ORDFAB: 'Fabrics', ORDYARN: 'Yarns',
  ORDACC: 'Accessories', ORDCOMP: 'Components', ORDDOC: 'Documents', ORDCMTFAB: 'CMT fabrics', ORDERENTRY: 'Order entry', EXCEL_ORDERS: 'Excel orders' };
const COLUMN_LABELS = { DOCID: 'Order reference', BUYER: 'Buyer', STYLENO: 'Style number', STYLEDESC: 'Description', BONO: 'Buyer PO', ODATE: 'Order date', UOM: 'Unit',
  SAMPLETYPE: 'Sample type', ORDCATEGORY: 'Category', MERCH: 'Merchandiser', OQTY: 'Order quantity', REMARKS: 'Remarks', STNO: 'Style', STYLENAME: 'Description',
  PONUM: 'PO number', SUBNO: 'Line key', COMBO: 'Colour', ORDQTY: 'Line quantity', PRODQTY: 'Production qty', SHIPDT: 'Ship date', PUOM: 'Unit', SNO: 'Line no.',
  GSIZE: 'Size', RQTY: 'Quantity', PQTY: 'Production qty', ORDSIZEROW: 'Row', SUB3_SUBNO: 'Line key', ORDMASID: 'Order ID', ORDSTYLEID: 'Line ID', ORDSIZEID: 'Size ID',
  IONO: 'OC number', BUYERNAME: 'Buyer', SEASON: 'Season', DESCOUNTRY: 'Destination', ORDERDATE: 'Order date', BOMDATE: 'BOM date', FABIHDATE: 'Fabric in-house date',
  PACKQTY: 'Packs', PRICE: 'Price', GARMENT: 'Garment', ORDSTYLEROW: 'Line no.', SIZES: 'Size', ORDERQTY: 'Quantity', QTYTYPE: 'Quantity type', SOURCE_FILE: 'Excel file',
  SOURCE_CELL: 'Excel cell', ENTRY_ID: 'Row ID', ORDER_KEY: 'Order ID', IMPORT_ID: 'Import ID', CREATEDBY: 'Approved by', CREATEDON: 'Saved on' };
const tableLabel = t => TABLE_LABELS[t] || t;
const sourceChip = src => { if (!src) return ''; if (src.cell) return `<span class="chip" title="${esc(src.sheet)} ${esc(src.cell)}${src.value != null ? ' = ' + esc(src.value) : ''}${src.formula ? ' · ' + esc(src.formula) : ''}${src.via ? ' · ' + esc(src.via) : ''}">${icon('file')}${esc(src.sheet)}!${esc(src.cell)}</span>`; if (src.origin) return `<span class="chip manual">${esc(src.origin)}</span>`; return ''; };

function route(name, handler) { App.routes[name] = handler; }
function setTitle(title, sub = '') { $('#page-title').textContent = title; $('#page-sub').textContent = sub; document.title = `${title} · POPPYS AI`; }
async function navigate() {
  const [name, ...rest] = (location.hash.replace(/^#\/?/, '') || 'overview').split('/');
  const handler = App.routes[name] || App.routes.overview;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.route === name));
  Drawer.close(); const view = $('#view'); view.innerHTML = '';
  try { await handler(view, rest.map(decodeURIComponent)); } catch (e) { view.innerHTML = `<div class="callout attention">${icon('alert')}<div>${esc(e.message)}</div></div>`; }
  paintIcons(view); view.focus({ preventScroll: true });
}
function go(hash) { if (location.hash === hash) navigate(); else location.hash = hash; }

async function refreshHealth() {
  try { const h = await api('/api/health'); App.state.health = h; const ok = h.database.connected;
    ['#side-dot', '#top-dot'].forEach(s => { $(s).className = `status-dot ${ok ? 'on' : 'off'}`; });
    $('#side-status').textContent = ok ? `Oracle connected · ${h.database.owner}` : 'Oracle not connected';
    $('#oracle-label').textContent = ok ? 'Oracle connected' : 'Oracle offline';
  } catch (_) { /* shown on next action */ }
}
async function showAuth() {
  const s = await fetch('/api/session', { credentials: 'same-origin' }).then(r => r.json());
  App.state.user = s.username; App.state.setup = s.needs_setup;
  $('#auth').hidden = !!s.authenticated;
  $('#auth-title').textContent = s.needs_setup ? 'Create the admin account' : 'Welcome back';
  $('#auth-desc').textContent = s.needs_setup ? 'POPPYS AI has one administrator. Choose the username and password you will use on this computer.' : 'Sign in as the POPPYS AI administrator.';
  $('#auth-submit').textContent = s.needs_setup ? 'Create admin account' : 'Sign in';
  $('#auth-pass').autocomplete = s.needs_setup ? 'new-password' : 'current-password';
  $('#username').textContent = s.username || 'Admin'; $('#avatar').textContent = (s.username || 'A').slice(0, 1).toUpperCase();
  return s.authenticated;
}
