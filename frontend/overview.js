'use strict';
/* Overview: business figures from stored data only. Each metric opens its records. */
route('overview', async view => {
  setTitle('Overview', 'Orders saved from Excel into Oracle');
  const o = await api('/api/overview');
  const hour = new Date().getHours(); const greet = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  const kpis = [
    ['saved', 'Saved orders', o.oracle ? fmt(o.saved_orders) : '—', o.oracle ? `${plural(o.imports, 'approved import')}` : 'Oracle not connected', 'check', ''],
    ['pieces', 'Quantity saved', o.oracle ? fmt(o.pieces) : '—', 'Total of saved order quantities', 'box', 'blue'],
    ['pending', 'Waiting for approval', fmt(o.pending_count), o.pending_count ? 'Uploaded, not yet saved' : 'Nothing waiting', 'clock', 'amber'],
    ['uploads', 'Excel files uploaded', fmt(o.drafts), 'Kept unchanged in this workspace', 'file', 'violet'],
  ];
  view.innerHTML = `<div class="stack">
    <section class="hero"><div><span class="eyebrow">${esc(greet)}, ${esc(App.state.user || 'admin')}</span><h2>Turn a buyer Excel into an Oracle order in three steps.</h2>
      <div class="flow-steps"><span>${icon('upload')} Upload</span><i>→</i><span>${icon('eye')} Review</span><i>→</i><span>${icon('check')} Approve</span></div></div>
      <a class="btn primary large" href="#/new">${icon('plus')} New order</a></section>
    ${o.oracle ? '' : `<div class="callout info">${icon('info')}<div>${esc(o.oracle_message || 'Oracle is not connected.')}</div></div>`}
    <div class="grid g4">${kpis.map(([k, label, value, sub, ic, tone]) => `<button class="card kpi" type="button" data-kpi="${k}"><span class="kpi-top">${esc(label)}<span class="kpi-icon ${tone}">${icon(ic)}</span></span><strong>${esc(value)}</strong><small>${esc(sub)}</small></button>`).join('')}</div>
    <div class="split">
      <section class="card"><div class="card-head"><div><h2>Waiting for your approval</h2><span class="sub">Uploaded workbooks that are not saved to Oracle yet</span></div><a class="btn small" href="#/new">Upload</a></div>
        ${o.pending.length ? o.pending.slice(0, 6).map(d => `<div class="list-row"><span class="file-ico">XLS</span><div class="grow"><strong>${esc(d.filename)}</strong><small class="muted">${esc(d.format)} · ${plural(d.orders, 'order')}${d.refs.filter(Boolean).length ? ' · ' + esc(d.refs.filter(Boolean).join(', ')) : ''} · ${when(d.created_at)}</small></div><a class="btn small soft" href="#/new/${esc(d.id)}">Review</a></div>`).join('') : `<div class="empty">${icon('check')}<p>Everything uploaded has been reviewed.</p></div>`}
      </section>
      <section class="card"><div class="card-head"><h2>System</h2></div><div class="status-list">
        <div><span class="status-dot ${o.oracle ? 'on' : 'off'}"></span>Oracle database ${o.oracle ? 'connected' : 'not connected'}</div>
        <div><span class="status-dot ${App.state.health?.ollama?.available ? 'on' : ''}"></span>Local AI ${App.state.health?.ollama?.available ? 'ready' : 'not running (optional)'}</div>
        <div>${icon('shield')}Runs offline. Files stay on this computer.</div></div></section>
    </div>
    <div class="grid g2">
      <section class="card"><div class="card-head"><h2>Recent activity</h2></div>
        ${o.activity.length ? `<ul class="timeline">${o.activity.map(a => `<li><span class="dot"></span><span><strong>${esc(a.action)}</strong>${a.subject ? ` · <span class="muted">${esc(a.subject)}</span>` : ''}</span><time>${when(a.at)}</time></li>`).join('')}</ul>` : `<div class="empty">No activity yet.</div>`}</section>
      <section class="card"><div class="card-head"><h2>Recent database operations</h2><a class="btn small ghost" href="#/orders">All orders</a></div>
        ${dbOps(o)}</section>
    </div></div>`;
  $$('[data-kpi]', view).forEach(b => b.addEventListener('click', () => kpiDrawer(b.dataset.kpi, o)));
});

function dbOps(o) {
  const ops = [...(o.recent_imports || []).map(i => ({ at: i.committed_at, text: `Saved ${i.orders.map(x => x.order_ref).join(', ')}`, sub: `${plural(i.rows, 'record')} written and verified`, kind: 'Insert' })),
    ...(o.recent_changes || []).map(c => ({ at: c.at, text: `Updated order ${c.ordmasid}`, sub: `${plural(c.values, 'value')} changed · approved by ${c.approver}`, kind: 'Update' }))]
    .sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, 8);
  if (!ops.length) return `<div class="empty">${o.oracle ? 'No orders saved yet. Your first approved order will appear here.' : 'Available when Oracle is connected.'}</div>`;
  return `<ul class="timeline">${ops.map(x => `<li><span class="dot"></span><span><span class="badge ${x.kind === 'Insert' ? 'green' : 'blue'}">${x.kind}</span> <strong>${esc(x.text)}</strong> <span class="sub muted" style="display:block">${esc(x.sub)}</span></span><time>${when(x.at)}</time></li>`).join('')}</ul>`;
}

function kpiDrawer(kind, o) {
  const titles = { saved: 'Saved orders', pieces: 'Saved orders and quantities', pending: 'Waiting for approval', uploads: 'Uploaded workbooks' };
  Drawer.open({ eyebrow: 'Overview', title: titles[kind], render: async () => {
    if (kind === 'pending' || kind === 'uploads') {
      const drafts = kind === 'pending' ? o.pending : await api('/api/drafts');
      return drafts.length ? `<div class="card table-wrap"><table><thead><tr><th>Workbook</th><th>Orders</th><th>Status</th><th></th></tr></thead><tbody>${drafts.map(d => `<tr><td><strong>${esc(d.filename)}</strong><span class="sub">${when(d.created_at)}</span></td><td>${esc(d.refs.filter(Boolean).join(', ') || plural(d.orders, 'order'))}</td><td>${d.committed ? '<span class="badge green">Saved</span>' : '<span class="badge amber">Awaiting approval</span>'}</td><td><a class="btn small" href="#/new/${esc(d.id)}">Open</a></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Nothing here yet.</div>';
    }
    const orders = await api('/api/orders');
    return orders.length ? `<div class="card table-wrap"><table><thead><tr><th>Order</th><th>Buyer</th><th class="num">Quantity</th><th>Saved</th></tr></thead><tbody>${orders.map(x => `<tr class="clickable" data-order="${x.ordmasid}"><td><strong>${esc(x.order_ref)}</strong><span class="sub">ORDMASID ${x.ordmasid}</span></td><td>${esc(x.buyer || '—')}</td><td class="num">${fmt(x.quantity)}</td><td>${when(x.saved_at)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No saved orders yet.</div>';
  }, after: body => $$('[data-order]', body).forEach(r => r.addEventListener('click', () => go(`#/orders/${r.dataset.order}`))) });
}
