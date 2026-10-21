'use strict';
/* Overview: business figures from stored data only. Each figure opens its records. */
const monthKey = d => d.toISOString().slice(0, 7);
function delta(now, before, unit = 'vs last month') {
  if (!before && !now) return `<span class="delta">No activity yet</span>`;
  if (!before) return `<span class="delta"><span class="up">new</span> this month</span>`;
  const pct = Math.round((now - before) / before * 100);
  return `<span class="delta"><span class="${pct >= 0 ? 'up' : 'down'}">${pct >= 0 ? '↑' : '↓'} ${Math.abs(pct)}%</span> ${unit}</span>`;
}
const ACT_ICON = a => /upload/i.test(a) ? ['upload', ''] : /sign|password/i.test(a) ? ['edit', 'violet'] : /saved|approved/i.test(a) ? ['check', ''] : /issue|fail/i.test(a) ? ['alert', 'amber'] : ['activity', 'blue'];
const STATUS_CLASS = { 'Needs a look': 'amber', 'Ready to approve': 'green', 'Validation issues': 'red', Analyzing: 'blue' };

route('overview', async view => {
  const today = new Date();
  setTitle('Overview', 'Your garment orders, AI-assisted data entry and Oracle integration at a glance.',
    `<span class="chip-box">${icon('calendar')}<span><small>Today</small>${today.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })}</span></span>`);
  const o = await api('/api/overview');
  const thisM = monthKey(today), lastM = monthKey(new Date(today.getFullYear(), today.getMonth() - 1, 1));
  const saved = o.by_month || {};
  const syncPct = o.sync && o.sync.checked ? Math.round(o.sync.verified / o.sync.checked * 100) : null;
  const kpis = [
    ['saved', 'Total Orders', o.oracle ? fmt(o.saved_orders) : '—', delta(saved[thisM] || 0, saved[lastM] || 0), 'file', 'blue'],
    ['uploads', 'Successfully Saved', o.oracle ? fmt(o.imports) : '—', `<span class="delta">Approved imports saved to Oracle</span>`, 'checkcircle', ''],
    ['pending', 'Pending Review', fmt(o.pending_count), `<span class="delta">Awaiting approval</span>`, 'clock', 'amber'],
    ['sync', 'Oracle Sync Success', syncPct == null ? '—' : `${syncPct}%`, `<span class="delta">${o.sync ? `${o.sync.verified} of ${o.sync.checked} saves read back identical` : 'Shown after the first save'}</span>`, 'database', ''],
    ['unresolved', 'Unresolved Items', fmt(o.unresolved || 0), `<span class="delta">${o.unresolved ? '<span class="down">Need attention</span>' : 'Nothing blocked'}</span>`, 'alert', 'red'],
  ];
  // last 14 days: uploads and saves per day (real dates)
  const days = Array.from({ length: 14 }, (_, i) => { const d = new Date(today); d.setDate(d.getDate() - 13 + i); return d.toISOString().slice(0, 10); });
  const up = days.map(d => (o.uploads_by_day || {})[d] || 0), sv = days.map(d => (o.saved_by_day || {})[d] || 0);
  const top = Math.max(4, ...up, ...sv); const ticks = [top, Math.round(top * .75), Math.round(top / 2), Math.round(top / 4), 0];
  page(view, `<div class="stack">
    <div class="kpis">${kpis.map(([k, l, v, d, ic, tone]) => `<button class="kpi2" type="button" data-kpi="${k}"><span class="tile-ico ${tone}">${icon(ic)}</span><span><span class="label">${l}</span><strong>${esc(v)}</strong>${d}</span></button>`).join('')}</div>
    ${o.oracle ? '' : `<div class="callout info">${icon('info')}<div>${esc(o.oracle_message || 'Oracle is not connected.')}</div></div>`}
    <div class="ov-grid"><div class="stack">
      <section class="card"><div class="card-title"><span class="tile-ico">${icon('file')}</span><div><h2>Recently Saved Orders</h2><p>Latest orders successfully saved to Oracle</p></div><a class="btn right" href="#/orders">View all orders ${icon('arrow')}</a></div>
        ${(o.recent_orders || []).length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Order ID</th><th>Buyer</th><th>Style / Order Name</th><th class="num">Pieces</th><th>Saved By</th><th>Saved Date</th><th>Actions</th></tr></thead><tbody>
          ${o.recent_orders.map(r => `<tr><td><a href="#/orders/${r.ordmasid}"><strong>${esc(r.order_ref)}</strong></a></td><td>${esc(r.buyer || '—')}</td><td>${esc(r.style || '—')}</td><td class="num">${fmt(r.quantity)}</td>
            <td><span class="who"><i>${esc((r.approver || '?').slice(0, 1).toUpperCase())}</i>${esc(r.approver)}</span></td><td>${when(r.saved_at)}</td>
            <td><a class="dots-btn" href="#/orders/${r.ordmasid}" title="View" aria-label="View ${esc(r.order_ref)}">${icon('eye')}</a></td></tr>`).join('')}</tbody></table></div>`
          : `<div class="empty">${o.oracle ? 'No orders saved yet. Upload a buyer Excel to start.' : 'Available when Oracle is connected.'}</div>`}</section>
      <section class="card"><div class="card-title"><span class="tile-ico amber">${icon('clock')}</span><div><h2>Waiting for Review / Not Saved Yet</h2><p>Uploaded files that are pending review, validation or approval</p></div><a class="btn primary right" href="#/new">${icon('plus')} Upload New File</a></div>
        ${o.pending.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>File Name</th><th>Order (Detected)</th><th class="num">Pieces</th><th>Uploaded By</th><th>Uploaded Date</th><th>Status</th><th>Actions</th></tr></thead><tbody>
          ${o.pending.slice(0, 6).map(d => `<tr><td><a href="#/new/${esc(d.id)}">${esc(d.filename)}</a></td><td>${esc(d.detected || '—')}</td><td class="num">${d.pieces ? fmt(d.pieces) : '—'}</td><td>${esc(d.uploaded_by || '—')}</td><td>${when(d.created_at)}</td>
            <td><span class="status-chip ${STATUS_CLASS[d.status] || 'blue'}">${esc(d.status)}</span></td><td><a class="dots-btn" href="#/new/${esc(d.id)}" title="Review" aria-label="Review ${esc(d.filename)}">${icon('eye')}</a></td></tr>`).join('')}</tbody></table></div>`
          : `<div class="empty">${icon('check')}<p>Everything uploaded has been reviewed.</p></div>`}</section>
    </div>
    <section class="card"><div class="card-title"><span class="tile-ico">${icon('activity')}</span><div><h2>Activity</h2><p>Recent uploads, edits, approvals and Oracle saves</p></div></div>
      ${o.activity.length ? `<ul class="feed">${o.activity.map(a => { const [ic, tone] = ACT_ICON(a.action); return `<li><span class="fi ${tone}">${icon(ic)}</span><span><strong>${esc(a.actor || 'system')}</strong> ${esc(a.action.toLowerCase())}${a.subject ? `<br><span class="muted">${esc(a.subject)}</span>` : ''}</span><time>${ago(a.at)}</time></li>`; }).join('')}</ul>` : '<div class="empty">No activity yet.</div>'}</section></div>
    <div class="ov-grid">
      <section class="card"><div class="card-title"><span class="tile-ico">${icon('chart')}</span><div><h2>Order Throughput</h2><p>Uploads and orders saved to Oracle, last 14 days</p></div><div class="legend-row right"><span><i style="background:#0f8a5f"></i>Saved to Oracle</span><span><i style="background:#bfe8d3"></i>Uploaded</span></div></div>
        <div class="chart">${ticks.map(t => `<div class="gridline" style="bottom:${t / top * 150}px"><span>${t}</span></div>`).join('')}${days.map((d, i) => `<div class="col" title="${d}: ${up[i]} uploaded, ${sv[i]} saved"><i class="up" style="height:${Math.max(0, up[i] - sv[i]) / top * 150}px"></i><i class="sv" style="height:${sv[i] / top * 150}px"></i></div>`).join('')}</div>
        <div class="chart-x">${days.map((d, i) => `<span>${i % 2 === 0 ? new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : ''}</span>`).join('')}</div></section>
      <section class="card"><div class="card-title"><span class="tile-ico">${icon('settings')}</span><div><h2>How It Works</h2><p>AI-powered flow from Excel upload to Oracle save</p></div></div>
        <div class="how">${[['upload', 'Upload', 'Upload Excel files with order data', ''], ['sparkle', 'Analyze', 'AI reads & validates data', 'violet'], ['file', 'Preview', 'Review extracted data before save', 'blue'], ['check', 'Approve', 'Confirm and approve', ''], ['database', 'Save to Oracle', 'Saved and read back from Oracle', 'red']]
          .map(([ic, t, s, tone], i) => `${i ? '<i>→</i>' : ''}<div><span class="tile-ico ${tone}">${icon(ic)}</span><strong>${t}</strong>${s}</div>`).join('')}</div></section></div></div>`);
  $$('[data-kpi]', view).forEach(b => b.addEventListener('click', () => kpiDrawer(b.dataset.kpi, o)));
});

function kpiDrawer(kind, o) {
  const titles = { saved: 'Saved orders', uploads: 'Uploaded workbooks', pending: 'Waiting for approval', sync: 'Saved orders', unresolved: 'Uploads with validation issues' };
  Drawer.open({ eyebrow: 'Overview', title: titles[kind], render: async () => {
    if (['pending', 'uploads', 'unresolved'].includes(kind)) {
      const drafts = kind === 'uploads' ? await api('/api/drafts') : o.pending.filter(d => kind === 'pending' || d.status === 'Validation issues');
      return drafts.length ? `<div class="card table-wrap"><table class="data-table"><thead><tr><th>Workbook</th><th>Orders</th><th>Status</th><th></th></tr></thead><tbody>${drafts.map(d => `<tr><td><strong>${esc(d.filename)}</strong><span class="sub">${when(d.created_at)}</span></td><td>${esc(d.refs.filter(Boolean).join(', ') || plural(d.orders, 'order'))}</td><td>${d.committed ? '<span class="status-chip green">Saved</span>' : `<span class="status-chip ${STATUS_CLASS[d.status] || 'amber'}">${esc(d.status || 'Awaiting approval')}</span>`}</td><td><a class="btn small" href="#/new/${esc(d.id)}">Open</a></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Nothing here.</div>';
    }
    const orders = await api('/api/orders');
    return orders.length ? `<div class="card table-wrap"><table class="data-table"><thead><tr><th>Order</th><th>Buyer</th><th class="num">Pieces</th><th>Saved</th></tr></thead><tbody>${orders.map(x => `<tr class="clickable" data-order="${x.ordmasid}"><td><strong>${esc(x.order_ref)}</strong><span class="sub">ID ${x.ordmasid}</span></td><td>${esc(x.buyer || '—')}</td><td class="num">${fmt(x.quantity)}</td><td>${when(x.saved_at)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No saved orders yet.</div>';
  }, after: body => $$('[data-order]', body).forEach(r => r.addEventListener('click', () => go(`#/orders/${r.dataset.order}`))) });
}
