// Overview: the work queue (drafts not yet saved), recent Oracle saves read from the receipts, and the
// connection lights. Every figure comes from /api/overview or /api/health; nothing is estimated.
import { $, esc, fmt, state, timeAgo } from './util.js';
import { api } from './api.js';

const STATUS_PILL = { 'Save outcome unknown': 'err', Saving: 'warn', 'Needs attention': 'err', 'Ready to approve': 'ok', 'Checked offline': 'warn', Draft: '' };

export async function loadDashboard() {
  let o;
  try { o = await api('/api/overview'); } catch (e) { $('#dash-queue').innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const s = o.saved;
  const tiles = [
    ['Needs attention', o.counts['Needs attention'], 'Orders with fields to correct'],
    ['Ready to approve', o.counts['Ready to approve'], 'Checked against live Oracle'],
    ['Drafts', o.counts['Draft'] + o.counts['Checked offline'], 'Uploaded, not yet checked'],
    ['Saved in last 7 days', s.available ? s.last_7_days : '—', s.available ? 'Read from Oracle receipts' : 'Oracle offline'],
  ];
  $('#dash-kpis').innerHTML = tiles.map(([label, value, hint]) =>
    `<div class="card kpi"><small>${esc(label)}</small><b>${esc(typeof value === 'number' ? fmt(value, 0) : value)}</b><span class="muted small-text">${esc(hint)}</span></div>`).join('');
  $('#dash-queue').innerHTML = o.queue.length ? `<div class="tbl-wrap flush"><table><thead><tr><th>File</th><th>Order / style</th><th class="num">Pieces</th><th>Status</th><th>Uploaded</th><th></th></tr></thead><tbody>
    ${o.queue.map(d => `<tr class="clickable" data-open-draft="${esc(d.id)}"><td><b>${esc(d.filename)}</b><div class="muted small-text">${esc(d.format || '')}</div></td>
      <td>${esc((d.order_refs || []).filter(Boolean).join(', ') || '—')}<div class="muted small-text">${esc([...(d.buyers || []), ...(d.styles || [])].filter(Boolean).slice(0, 4).join(' · '))}</div></td>
      <td class="num">${d.pieces == null ? '—' : esc(fmt(d.pieces, 0))}</td>
      <td><span class="pill ${STATUS_PILL[d.status] ?? ''}">${esc(d.status)}</span></td>
      <td class="nowrap">${d.created_at ? esc(timeAgo(d.created_at)) : '—'}<div class="muted small-text">${esc(d.uploaded_by || '')}</div></td>
      <td><button class="secondary small" data-open-draft="${esc(d.id)}">Open →</button></td></tr>`).join('')}</tbody></table></div>`
    : '<p class="empty">Nothing waiting. Upload an Excel order to start.</p>';
  $('#dash-saved').innerHTML = !s.available ? `<p class="empty">${esc(s.note || 'Oracle is offline.')}</p>`
    : s.recent.length ? `<div class="tbl-wrap flush"><table><thead><tr><th>File</th><th>Order</th><th>Approved by</th><th>Saved</th><th></th></tr></thead><tbody>
      ${s.recent.map(r => `<tr><td><b>${esc(r.filename || r.job_id)}</b></td><td>${esc((r.orders || []).filter(Boolean).join(', ') || '—')}</td><td>${esc(r.approver)}</td>
        <td class="nowrap">${esc(new Date(r.committed_at).toLocaleString())}</td><td><button class="secondary small" data-receipt-go="${esc(r.job_id)}">View order</button></td></tr>`).join('')}</tbody></table></div>`
      : '<p class="empty">No orders have been saved to Oracle yet.</p>';
  drawStatus(o.accuracy);
}

function drawStatus(acc) {
  const h = state.health || {}; const db = h.database || {}; const ro = h.readonly || {}; const ai = h.ollama || {};
  const row = (ok, title, detail, tip = '') => `<div class="dash-light ${ok === true ? 'ok' : ok === false ? 'off' : 'idle'}" ${tip ? `title="${esc(tip)}"` : ''}><i class="dot"></i><div><b>${esc(title)}</b><small>${esc(detail)}</small></div></div>`;
  $('#dash-status').innerHTML = [
    row(!!db.connected, db.connected ? 'Oracle connected' : 'Oracle offline', db.connected ? 'Saving enabled' : 'Saving is paused until Oracle is back',
      db.connected ? `${db.owner} · ${db.tables} tables` : `${db.error || ''} Start the Oracle database (Docker Desktop → oracle26ai) and refresh.`),
    row(db.connected ? !!ro.connected : null, ro.connected ? 'Ask AI ready' : 'Ask AI not ready', ro.connected ? 'Read-only access' : 'Needs Oracle', ro.error || ''),
    row(ai.available ? true : null, ai.available ? 'AI assistant on' : 'AI assistant off', ai.available ? ai.model : 'Optional'),
    row(acc ? acc.correct === acc.total : null, acc ? `Answer accuracy ${acc.correct}/${acc.total}` : 'Answer accuracy not checked',
      acc ? `Checked ${timeAgo(acc.at)}` : 'Setup → Answer accuracy', acc ? `Compared with hand-written SQL${acc.live ? '' : ' (offline snapshot)'}` : ''),
  ].join('');
}
