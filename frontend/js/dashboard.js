// Overview: one line of system status, the orders saved lately (by OC No and name, never a record ID), who did what,
// and the uploads not saved yet. Every figure comes from /api/overview or /api/health; nothing is estimated.
import { $, esc, fmt, state, timeAgo } from './util.js';
import { api } from './api.js';

const WHAT = {
  login: () => 'signed in',
  upload: a => `uploaded ${file(a)}`,
  upload_unreadable: a => `uploaded ${file(a)}, which could not be read`,
  oracle_mapping: a => `mapped ${file(a)} to Oracle`,
  mapping_applied: a => `chose the layout of ${file(a)}`,
  template_saved: () => 'saved a layout for next time',
  edit: a => `changed ${file(a)}`,
  approved_saved: a => `saved order ${orders(a)} in Oracle`,
  approve_failed: a => `tried to save ${file(a)}; Oracle refused it`,
  approve_outcome_unknown: a => `saved ${file(a)}; Oracle did not confirm yet`,
  user_created: () => 'added a login',
  user_changed: () => 'changed a login',
};
const file = a => `<b>${esc(a.filename || 'a file')}</b>`;
const orders = a => `<b>${esc((a.orders || []).join(', ') || a.filename || '')}</b>`;
const name = o => [o.style, o.description].filter(Boolean).join(' · ') || o.filename || '—';
const STATUS_PILL = { 'Save outcome unknown': 'err', Saving: 'warn', 'Needs attention': 'err', 'Ready to approve': 'ok', 'Checked offline': 'warn', Draft: '' };
const STATUS_TEXT = { 'Ready to approve': 'Ready to save', 'Needs attention': 'Needs a look', 'Checked offline': 'Checked without Oracle', Draft: 'Uploaded' };

export async function loadDashboard() {
  drawStatus();
  let o;
  try { o = await api('/api/overview'); } catch (e) { drawCards(null); $('#dash-saved').innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  drawCards(o);
  $('#dash-saved').innerHTML = o.recent_orders.length ? `<div class="tbl-wrap flush"><table class="ov-orders"><thead><tr><th scope="col">Order ID</th><th scope="col">Order name</th>
      <th scope="col" class="opt">Buyer</th><th scope="col" class="num">Pieces</th><th scope="col">Saved</th></tr></thead><tbody>
    ${o.recent_orders.map(r => `<tr class="clickable" data-order-panel="${esc(r.ordmasid)}"><th scope="row"><button class="link" data-order-panel="${esc(r.ordmasid)}">${esc(r.order_ref || '—')}</button></th>
      <td>${esc(name(r))}</td><td class="opt">${esc(r.buyer || '—')}</td><td class="num">${r.pieces == null ? '—' : esc(fmt(r.pieces, 0))}</td>
      <td class="nowrap">${esc(timeAgo(r.saved_at))}<div class="muted small-text">${esc(r.saved_by || '')}</div></td></tr>`).join('')}</tbody></table></div>`
    : '<p class="empty">No orders saved yet. Click <b>＋ New order</b> to upload an Excel file.</p>';
  $('#dash-activity').innerHTML = o.activity.length ? o.activity.map((a, i) => `<li class="act-row${i === 0 ? ' latest' : ''}" style="--i:${Math.min(i, 10)}">
      <span class="act-icon ${ACT_TONE[a.action] || ''}" aria-hidden="true">${actIcon(a.action)}</span><span class="act-line" aria-hidden="true"><i></i></span>
      <span class="act-body"><span class="act-who">${esc(a.who)}</span><span class="act-what">${(WHAT[a.action] || (() => esc(a.action)))(a)}</span></span>
      <time class="act-time" datetime="${esc(a.at)}" title="${esc(new Date(a.at).toLocaleString())}">${i === 0 ? '<i class="act-live" aria-hidden="true"></i>' : CLOCK}${esc(timeAgo(a.at))}</time></li>`).join('')
    : '<li class="act-empty muted">Nothing yet.</li>';
  const setupNav = document.querySelector('.nav button[data-view="admin"]'); $('#dash-activity-all').hidden = !setupNav || setupNav.hidden;
  $('#dash-waiting-card').hidden = !o.queue.length;
  queueRows = o.queue; drawQueue();
}

// Oracle (saving), database view (Ask AI's read-only account) and the local AI assistant, as three status cards.
const SICON = {
  db: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
  view: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4M9 13h.01M15 13h.01M9 17h6"/>',
};
function drawStatus() {
  const h = state.health || {}; const db = h.database || {}; const ro = h.readonly || {}; const ai = h.ollama || {};
  const card = (i, ok, icon, title, sub, pill, tip = '') => `<div class="scard ${ok === true ? 'ok' : ok === false ? 'off' : 'idle'}" style="--i:${i}" ${tip ? `title="${esc(tip)}"` : ''}>
      <span class="scard-icon"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SICON[icon]}</svg></span>
      <span class="scard-text"><strong>${esc(title)}</strong><small>${esc(sub)}</small></span>
      <span class="scard-pill"><i class="dot" aria-hidden="true"></i>${esc(pill)}</span></div>`;
  $('#dash-status').innerHTML = [
    card(0, !!db.connected, 'db', db.connected ? 'Oracle connected' : 'Oracle offline', db.connected ? `Saving enabled · ${db.owner || ''}` : 'Saving paused',
      db.connected ? 'Connected' : 'Offline', db.connected ? `${db.tables || ''} tables · ${db.dsn || ''}` : `${db.error || ''} Start the Oracle database (Docker Desktop → oracle26ai) and refresh.`),
    card(1, db.connected ? !!ro.connected : null, 'view', ro.connected ? 'Database view ready' : 'Database view needs Oracle', ro.connected ? 'Read-only access' : 'Starts when Oracle is connected',
      ro.connected ? 'Ready' : 'Waiting', ro.error || ''),
    card(2, ai.available ? true : null, 'bot', ai.available ? 'AI assistant on' : 'AI assistant off', ai.available ? ai.model || 'Local model' : 'Optional', ai.available ? 'Active' : 'Off'),
  ].join('');
}

// Four figure cards, all from /api/overview: saved orders (Oracle), files not saved yet, and two queue counts.
const ARROW = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M8 7h9v9"/></svg>';
function drawCards(o) {
  const saved = o?.saved || {}; const counts = o?.counts || {}; const n = v => v == null ? '—' : fmt(v, 0);
  const card = (cls, title, value, note, goto, badge = '') => `<article class="dcard ${cls}"><div class="dcard-top"><h3>${esc(title)}</h3>
      <button class="dcard-go" data-goto="${goto}" aria-label="Open ${esc(title)}">${ARROW}</button></div>
    <strong class="dcard-value">${esc(value)}</strong><p class="dcard-note">${badge}${esc(note)}</p></article>`;
  const week = saved.available ? `<span class="dcard-badge">${esc(n(saved.last_7_days))}</span>` : '';
  $('#dash-cards').innerHTML = [
    card('hero', 'Saved orders', saved.available ? n(saved.total) + (saved.capped ? '+' : '') : '—', saved.available ? 'saved in the last 7 days' : 'Oracle is offline', 'explorer', week),
    card('', 'Not saved yet', n(o?.open), 'Uploaded, waiting to be checked and saved', 'workspace'),
    card('', 'Needs a look', n(o ? counts['Needs attention'] || 0 : null), 'Files with something to fix', 'workspace'),
    card('', 'Ready to save', n(o ? counts['Ready to approve'] || 0 : null), 'Checked, waiting for approval', 'workspace'),
  ].join('');
}

// Activity rows: an icon per kind of action, a timeline dot, who and what, and when.
const svg = d => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const CLOCK = `<svg class="act-clock" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`;
const ICONS = {
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  map: '<path d="M4 6h6M4 12h6M4 18h6M14 6h6M14 12h6M14 18h6"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
};
const ACT_ICON = { login: 'user', user_created: 'user', user_changed: 'user', upload: 'upload', upload_unreadable: 'alert', edit: 'edit', oracle_mapping: 'map', mapping_applied: 'map', template_saved: 'map', approved_saved: 'check', approve_failed: 'alert', approve_outcome_unknown: 'alert' };
const ACT_TONE = { approved_saved: 'ok', approve_failed: 'bad', upload_unreadable: 'bad', approve_outcome_unknown: 'warn' };
const actIcon = action => svg(ICONS[ACT_ICON[action] || 'user']);

// "View all" opens the full audit history in Setup (only shown when Setup is available to this login).
export function bindActivity() {
  const all = $('#dash-activity-all'); if (!all) return;
  all.addEventListener('click', async () => {
    document.querySelector('.nav button[data-view="admin"]')?.click();
    for (let n = 0; n < 50 && state.busy; n++) await new Promise(r => setTimeout(r, 50));
    document.querySelector('.subnav [data-admin="audit"]')?.click();
  });
}

// "Not saved yet": file type icon, order chips, status pill, who uploaded; headers sort the rows in the browser.
let queueRows = []; let queueSort = { key: null, dir: 1 };
const fileExt = f => ((f || '').match(/\.([a-z0-9]+)$/i)?.[1] || 'file').toUpperCase();
const refsOf = d => [...(d.order_refs || []), ...(d.styles || [])].filter(Boolean).slice(0, 3);
const SORTERS = { file: d => (d.filename || '').toLowerCase(), order: d => refsOf(d).join(' ').toLowerCase(), pieces: d => d.pieces ?? -1,
  status: d => STATUS_TEXT[d.status] || d.status || '', uploaded: d => d.created_at ? Date.parse(d.created_at) : 0 };
const SORT_ICON = '<svg class="q-sort-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path class="up" d="m8 9 4-4 4 4"/><path class="down" d="m8 15 4 4 4-4"/></svg>';
const STATUS_ICON = { err: '<path d="M12 8v5M12 16.5h.01"/>', warn: '<path d="M12 8v5M12 16.5h.01"/>', ok: '<path d="m8 12 3 3 5-6"/>', '': '<path d="M12 7v5l3 2"/>' };
function fileIcon(name) {
  const ext = fileExt(name); const excel = /^(XLSX|XLSM|XLS|XLSB|ODS|CSV)$/.test(ext);
  return excel ? `<span class="q-file x" aria-hidden="true"><b>X</b></span>`
    : `<span class="q-file doc" aria-hidden="true"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 8h10M7 12h10M7 16h6"/></svg><small>${esc(ext.slice(0, 4))}</small></span>`;
}
function drawQueue() {
  let rows = queueRows.slice();
  if (queueSort.key) { const f = SORTERS[queueSort.key]; rows.sort((a, b) => (f(a) > f(b) ? 1 : f(a) < f(b) ? -1 : 0) * queueSort.dir); }
  const th = (key, label, cls = '') => { const on = queueSort.key === key; const sort = on ? (queueSort.dir > 0 ? 'ascending' : 'descending') : 'none';
    return `<th scope="col" class="${cls}" aria-sort="${sort}"><button type="button" class="q-sort ${on ? (queueSort.dir > 0 ? 'asc' : 'desc') : ''}" data-q-sort="${key}">${label}${SORT_ICON}</button></th>`; };
  $('#dash-queue').innerHTML = rows.length ? `<div class="tbl-wrap flush"><table class="q-table"><thead><tr>${th('file', 'File')}${th('order', 'Order')}${th('pieces', 'Pieces', 'num')}${th('status', 'Status')}${th('uploaded', 'Uploaded')}<th scope="col"><span class="sr-only">Open</span></th></tr></thead><tbody>
    ${rows.slice(0, 8).map(d => { const tone = STATUS_PILL[d.status] ?? ''; const refs = refsOf(d);
      return `<tr class="clickable" data-open-draft="${esc(d.id)}">
      <td><div class="q-filecell">${fileIcon(d.filename)}<span class="q-name"><b>${esc(d.filename)}</b><small>${esc(fileExt(d.filename))}${d.format ? ` · ${esc(d.format)}` : ''}</small></span></div></td>
      <td>${refs.length ? `<span class="q-ref">${esc(refs.join(' · '))}</span>` : '<span class="q-ref empty">—</span>'}</td>
      <td class="num q-num">${d.pieces == null ? '—' : esc(fmt(d.pieces, 0))}</td>
      <td><span class="q-status ${tone}"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10" class="q-status-bg"/>${STATUS_ICON[tone] || STATUS_ICON['']}</svg>${esc(STATUS_TEXT[d.status] || d.status)}</span></td>
      <td><div class="q-when"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M3 10h18M8 3v4M16 3v4"/></svg><span><span>${d.created_at ? esc(timeAgo(d.created_at)) : '—'}</span>${d.uploaded_by ? `<small>${esc(d.uploaded_by)}</small>` : ''}</span></div></td>
      <td class="q-more"><button type="button" class="icon-btn" data-open-draft="${esc(d.id)}" aria-label="Open ${esc(d.filename)}" title="Open"><svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg></button></td></tr>`; }).join('')}</tbody></table></div>` : '';
}
export function bindQueue() {
  $('#dash-queue')?.addEventListener('click', e => {
    const b = e.target.closest('[data-q-sort]'); if (!b) return;
    e.stopPropagation(); const key = b.dataset.qSort;
    queueSort = queueSort.key === key ? { key, dir: -queueSort.dir } : { key, dir: 1 }; drawQueue();
  });
}
