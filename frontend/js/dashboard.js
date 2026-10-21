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
  try { o = await api('/api/overview'); } catch (e) { $('#dash-saved').innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  $('#dash-saved').innerHTML = o.recent_orders.length ? `<div class="tbl-wrap flush"><table class="ov-orders"><thead><tr><th scope="col">Order ID</th><th scope="col">Order name</th>
      <th scope="col" class="opt">Buyer</th><th scope="col" class="num">Pieces</th><th scope="col">Saved</th></tr></thead><tbody>
    ${o.recent_orders.map(r => `<tr class="clickable" data-order-panel="${esc(r.ordmasid)}"><th scope="row"><button class="link" data-order-panel="${esc(r.ordmasid)}">${esc(r.order_ref || '—')}</button></th>
      <td>${esc(name(r))}</td><td class="opt">${esc(r.buyer || '—')}</td><td class="num">${r.pieces == null ? '—' : esc(fmt(r.pieces, 0))}</td>
      <td class="nowrap">${esc(timeAgo(r.saved_at))}<div class="muted small-text">${esc(r.saved_by || '')}</div></td></tr>`).join('')}</tbody></table></div>`
    : '<p class="empty">No orders saved yet. Click <b>＋ New order</b> to upload an Excel file.</p>';
  $('#dash-activity').innerHTML = o.activity.length ? o.activity.map(a => `<li><span class="act-who">${esc(a.who)}</span> ${(WHAT[a.action] || (() => esc(a.action)))(a)}
      <time class="muted small-text" datetime="${esc(a.at)}" title="${esc(new Date(a.at).toLocaleString())}">${esc(timeAgo(a.at))}</time></li>`).join('')
    : '<li class="muted">Nothing yet.</li>';
  $('#dash-waiting-card').hidden = !o.queue.length;
  $('#dash-queue').innerHTML = o.queue.length ? `<div class="tbl-wrap flush"><table><thead><tr><th scope="col">File</th><th scope="col">Order</th><th scope="col" class="num">Pieces</th><th scope="col">Status</th><th scope="col">Uploaded</th></tr></thead><tbody>
    ${o.queue.slice(0, 8).map(d => `<tr class="clickable" data-open-draft="${esc(d.id)}"><td><b>${esc(d.filename)}</b></td>
      <td>${esc([...(d.order_refs || []), ...(d.styles || [])].filter(Boolean).slice(0, 3).join(' · ') || '—')}</td>
      <td class="num">${d.pieces == null ? '—' : esc(fmt(d.pieces, 0))}</td>
      <td><span class="pill ${STATUS_PILL[d.status] ?? ''}">${esc(STATUS_TEXT[d.status] || d.status)}</span></td>
      <td class="nowrap">${d.created_at ? esc(timeAgo(d.created_at)) : '—'} <span class="muted small-text">${esc(d.uploaded_by || '')}</span></td></tr>`).join('')}</tbody></table></div>` : '';
}

// One compact line: Oracle (saving), Ask AI (read-only account), local AI assistant.
function drawStatus() {
  const h = state.health || {}; const db = h.database || {}; const ro = h.readonly || {}; const ai = h.ollama || {};
  const item = (ok, title, tip = '') => `<span class="st ${ok === true ? 'ok' : ok === false ? 'off' : 'idle'}" ${tip ? `title="${esc(tip)}"` : ''}><i class="dot"></i>${esc(title)}</span>`;
  $('#dash-status').innerHTML = [
    item(!!db.connected, db.connected ? `Oracle connected · ${db.owner || ''}` : 'Oracle offline · saving paused',
      db.connected ? `${db.tables || ''} tables · ${db.dsn || ''}` : `${db.error || ''} Start the Oracle database (Docker Desktop → oracle26ai) and refresh.`),
    item(db.connected ? !!ro.connected : null, ro.connected ? 'Database view ready' : 'Database view needs Oracle', ro.error || ''),
    item(ai.available ? true : null, ai.available ? `AI assistant on · ${ai.model}` : 'AI assistant off (optional)'),
  ].join('');
}
