'use strict';
/* Dashboard: orders processed, waiting for approval and saved to Oracle, from stored data only. Every figure opens its records. */
const monthKey = d => d.toISOString().slice(0, 7);
function delta(now, before, unit = 'vs last month') {
  if (!before && !now) return `<span class="delta">No activity yet</span>`;
  if (!before) return `<span class="delta"><span class="up">new</span> this month</span>`;
  const pct = Math.round((now - before) / before * 100);
  return `<span class="delta"><span class="${pct >= 0 ? 'up' : 'down'}">${pct >= 0 ? '↑' : '↓'} ${Math.abs(pct)}%</span> ${unit}</span>`;
}
const ACT_ICON = a => /upload/i.test(a) ? ['upload', ''] : /sign|password/i.test(a) ? ['edit', 'violet'] : /saved|approved/i.test(a) ? ['check', ''] : /issue|fail|fix/i.test(a) ? ['alert', 'amber'] : ['activity', 'blue'];
const STATUS_CLASS = { 'Needs a fix': 'red', 'Ready to approve': 'green', 'Needs a look': 'blue' };

route('overview', async view => {
  const today = new Date();
  setTitle('Dashboard', 'Orders processed, waiting for approval and saved to Oracle, from real data.',
    `<span class="chip-box">${icon('calendar')}<span><small>Today</small>${today.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })}</span></span>`);
  const o = await api('/api/overview');
  const thisM = monthKey(today), lastM = monthKey(new Date(today.getFullYear(), today.getMonth() - 1, 1));
  const saved = o.by_month || {};
  const syncPct = o.sync && o.sync.checked ? Math.round(o.sync.verified / o.sync.checked * 100) : null;
  const kpis = [
    ['saved', 'Orders processed', o.oracle ? fmt(o.saved_orders) : '—', delta(saved[thisM] || 0, saved[lastM] || 0), 'file', 'blue'],
    ['pending', 'Waiting for approval', fmt(o.pending_count), `<span class="delta">Uploaded, not saved yet</span>`, 'clock', 'amber'],
    ['unresolved', 'Need a fix', fmt(o.unresolved || 0), `<span class="delta">${o.unresolved ? '<span class="down">A value must be corrected</span>' : 'Nothing blocked'}</span>`, 'alert', 'red'],
    ['sync', 'Saved & verified', syncPct == null ? '—' : `${syncPct}%`, `<span class="delta">${o.sync && o.sync.checked ? `${o.sync.verified} of ${o.sync.checked} saves read back identical` : 'Shown after the first save'}</span>`, 'checkcircle', ''],
    ['uploads', 'Uploads', fmt(o.drafts), `<span class="delta">Excel files received</span>`, 'upload', 'violet'],
  ];
  const first = (o.pending || [])[0];
  // last 14 days: uploads and saves per day (real dates)
  const days = Array.from({ length: 14 }, (_, i) => { const d = new Date(today); d.setDate(d.getDate() - 13 + i); return d.toISOString().slice(0, 10); });
  const up = days.map(d => (o.uploads_by_day || {})[d] || 0), sv = days.map(d => (o.saved_by_day || {})[d] || 0);
  const top = Math.max(4, ...up, ...sv); const ticks = [top, Math.round(top * .75), Math.round(top / 2), Math.round(top / 4), 0];
  page(view, `<div class="stack">
    <div class="kpis">${kpis.map(([k, l, v, d, ic, tone]) => `<button class="kpi2" type="button" data-kpi="${k}"><span class="tile-ico ${tone}">${icon(ic)}</span><span><span class="label">${l}</span><strong>${esc(v)}</strong>${d}</span></button>`).join('')}</div>
    <div class="row">
      <a class="btn primary" href="#/new">${icon('upload')} Upload new order</a>
      ${first ? `<a class="btn" href="#/new/${esc(first.id)}">${icon('eye')} Continue review</a>` : `<button class="btn" type="button" disabled title="Nothing is waiting for review">${icon('eye')} Continue review</button>`}
      <a class="btn" href="#/orders">${icon('file')} View recent orders</a>
      <a class="btn" href="#/ai">${icon('sparkle')} Ask AI</a></div>
    ${o.oracle ? '' : `<div class="callout info">${icon('info')}<div>${esc(o.oracle_message || 'Oracle is not connected.')}</div></div>`}
    <div class="ov-grid"><div class="stack">
      <section class="card"><div class="card-title"><span class="tile-ico">${icon('file')}</span><div><h2>Recently saved orders</h2><p>Latest orders saved to Oracle and read back</p></div><a class="btn right" href="#/orders">View all orders ${icon('arrow')}</a></div>
        ${(o.recent_orders || []).length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Order</th><th>Buyer</th><th>Style</th><th class="num">Pieces</th><th>Saved by</th><th>Saved on</th><th></th></tr></thead><tbody>
          ${o.recent_orders.map(r => `<tr><td><a href="#/orders/${r.ordmasid}"><strong>${esc(r.order_ref)}</strong></a></td><td>${esc(r.buyer || '—')}</td><td>${esc(r.style || '—')}</td><td class="num">${fmt(r.quantity)}</td>
            <td><span class="who"><i>${esc((r.approver || '?').slice(0, 1).toUpperCase())}</i>${esc(r.approver)}</span></td><td>${when(r.saved_at)}</td>
            <td><a class="dots-btn" href="#/orders/${r.ordmasid}" title="View" aria-label="View ${esc(r.order_ref)}">${icon('eye')}</a></td></tr>`).join('')}</tbody></table></div>`
          : `<div class="empty">${o.oracle ? 'No orders saved yet. Upload a buyer Excel to start.' : 'Available when Oracle is connected.'}</div>`}</section>
      <section class="card"><div class="card-title"><span class="tile-ico amber">${icon('clock')}</span><div><h2>Waiting for review</h2><p>Uploaded files not saved to Oracle yet</p></div><a class="btn primary right" href="#/new">${icon('plus')} Upload new file</a></div>
        ${(o.pending || []).length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>File</th><th>Order</th><th class="num">Pieces</th><th>Columns filled</th><th>Uploaded</th><th>Status</th><th></th></tr></thead><tbody>
          ${o.pending.slice(0, 6).map(d => `<tr><td><a href="#/new/${esc(d.id)}">${esc(d.filename)}</a></td><td>${esc(d.detected || '—')}</td><td class="num">${d.pieces ? fmt(d.pieces) : '—'}</td>
            <td>${d.columns ? `${fmt(d.filled)} of ${fmt(d.columns)}` : '—'}</td><td>${when(d.created_at)}${d.uploaded_by ? `<span class="sub">${esc(d.uploaded_by)}</span>` : ''}</td>
            <td><span class="status-chip ${STATUS_CLASS[d.status] || 'blue'}">${esc(d.status)}</span></td><td><a class="dots-btn" href="#/new/${esc(d.id)}" title="Review" aria-label="Review ${esc(d.filename)}">${icon('eye')}</a></td></tr>`).join('')}</tbody></table></div>`
          : `<div class="empty">${icon('check')}<p>Everything uploaded has been reviewed.</p></div>`}</section>
      <section class="card"><div class="card-title"><span class="tile-ico">${icon('chart')}</span><div><h2>Last 14 days</h2><p>Uploads and orders saved to Oracle. Click a day to see them.</p></div><div class="legend-row right"><span><i style="background:#0f8a5f"></i>Saved to Oracle</span><span><i style="background:#bfe8d3"></i>Uploaded</span></div></div>
        <div class="chart">${ticks.map(t => `<div class="gridline" style="bottom:${t / top * 150}px"><span>${t}</span></div>`).join('')}${days.map((d, i) => `<button type="button" class="col" data-day="${d}" title="${d}: ${up[i]} uploaded, ${sv[i]} saved" aria-label="${new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}: ${up[i]} uploaded, ${sv[i]} saved"><i class="up" style="height:${Math.max(0, up[i] - sv[i]) / top * 150}px"></i><i class="sv" style="height:${sv[i] / top * 150}px"></i></button>`).join('')}</div>
        <div class="chart-x">${days.map((d, i) => `<span>${i % 2 === 0 ? new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : ''}</span>`).join('')}</div></section>
    </div>
    <section class="card"><div class="card-title"><span class="tile-ico">${icon('activity')}</span><div><h2>Activity</h2><p>Uploads, edits, approvals and Oracle saves</p></div></div>
      ${(o.activity || []).length ? `<ul class="feed">${o.activity.map(a => { const [ic, tone] = ACT_ICON(a.action); return `<li><span class="fi ${tone}">${icon(ic)}</span><span><strong>${esc(a.actor || 'system')}</strong> ${esc(a.action.toLowerCase())}${a.subject ? `<br><span class="muted">${esc(a.subject)}</span>` : ''}</span><time>${ago(a.at)}</time></li>`; }).join('')}</ul>` : '<div class="empty">No activity yet.</div>'}</section></div></div>`);
  $$('[data-kpi]', view).forEach(b => b.addEventListener('click', () => kpiDrawer(b.dataset.kpi, o)));
  $$('[data-day]', view).forEach(b => b.addEventListener('click', () => dayDrawer(b.dataset.day)));
});

function dayDrawer(day) {
  const label = new Date(day).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  Drawer.open({ eyebrow: 'Dashboard', title: label, render: async () => {
    const [drafts, orders] = await Promise.all([api('/api/drafts').catch(() => []), api('/api/orders').catch(() => [])]);
    const ups = drafts.filter(d => String(d.created_at || '').slice(0, 10) === day);
    const saved = orders.filter(o => String(o.saved_at || '').slice(0, 10) === day);
    return `<section class="card"><div class="card-head"><h2>Saved to Oracle</h2><span class="badge green">${saved.length}</span></div>
        ${saved.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Order</th><th>Buyer</th><th class="num">Pieces</th><th>Saved</th></tr></thead><tbody>
          ${saved.map(x => `<tr><td><a href="#/orders/${x.ordmasid}"><strong>${esc(x.order_ref)}</strong></a></td><td>${esc(x.buyer || '—')}</td><td class="num">${fmt(x.quantity)}</td><td>${when(x.saved_at)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No order was saved on this day.</div>'}</section>
      <section class="card"><div class="card-head"><h2>Uploaded</h2><span class="badge">${ups.length}</span></div>
        ${ups.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>File</th><th>Order</th><th>Status</th></tr></thead><tbody>
          ${ups.map(d => `<tr><td><a href="#/new/${esc(d.id)}">${esc(d.filename)}</a><span class="sub">${when(d.created_at)}</span></td><td>${esc((d.refs || []).filter(Boolean).join(', ') || '—')}</td>
            <td>${d.committed ? '<span class="status-chip green">Saved</span>' : '<span class="status-chip amber">Waiting for review</span>'}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No file was uploaded on this day.</div>'}</section>`;
  }, after: body => $$('a', body).forEach(a => a.addEventListener('click', () => Drawer.close())) });
}

function kpiDrawer(kind, o) {
  const titles = { saved: 'Orders processed', uploads: 'Uploaded workbooks', pending: 'Waiting for approval', sync: 'Orders processed', unresolved: 'Uploads that need a fix' };
  Drawer.open({ eyebrow: 'Dashboard', title: titles[kind], render: async () => {
    if (['pending', 'uploads', 'unresolved'].includes(kind)) {
      const drafts = kind === 'uploads' ? await api('/api/drafts') : (o.pending || []).filter(d => kind === 'pending' || d.status === 'Needs a fix');
      return drafts.length ? `<div class="card table-wrap"><table class="data-table"><thead><tr><th>Workbook</th><th>Order</th><th>Status</th><th></th></tr></thead><tbody>${drafts.map(d => `<tr><td><strong>${esc(d.filename)}</strong><span class="sub">${when(d.created_at)}</span></td><td>${esc(d.detected || (d.refs || []).filter(Boolean).join(', ') || plural(d.orders, 'order'))}</td><td>${d.committed ? '<span class="status-chip green">Saved</span>' : `<span class="status-chip ${STATUS_CLASS[d.status] || 'amber'}">${esc(d.status || 'Waiting for approval')}</span>`}</td><td><a class="btn small" href="#/new/${esc(d.id)}">Open</a></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Nothing here.</div>';
    }
    const orders = await api('/api/orders');
    return orders.length ? `<div class="card table-wrap"><table class="data-table"><thead><tr><th>Order</th><th>Buyer</th><th class="num">Pieces</th><th>Saved</th></tr></thead><tbody>${orders.map(x => `<tr class="clickable" data-order="${x.ordmasid}"><td><strong>${esc(x.order_ref)}</strong><span class="sub">${esc(x.status || '')}</span></td><td>${esc(x.buyer || '—')}</td><td class="num">${fmt(x.quantity)}</td><td>${when(x.saved_at)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No saved orders yet.</div>';
  }, after: body => { $$('[data-order]', body).forEach(r => r.addEventListener('click', () => go(`#/orders/${r.dataset.order}`))); $$('a', body).forEach(a => a.addEventListener('click', () => Drawer.close())); } });
}
