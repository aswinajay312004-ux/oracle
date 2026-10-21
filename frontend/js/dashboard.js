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
