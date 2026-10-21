'use strict';
/* Orders: saved order history, details, visualization, approved edits and audit trail. */
const OR = { q: '', filter: 'all', sort: 'new', items: null };

route('orders', async (view, [id, action]) => {
  setTitle('Orders', 'Every approved order, read live from Oracle');
  if (id && action === 'edit') return renderEdit(view, Number(id));
  OR.items = await api('/api/orders');
  const drafts = (await api('/api/drafts')).filter(d => !d.committed);
  view.innerHTML = `<div class="stack"><section class="card"><div class="toolbar">
      <label class="search">${icon('search')}<input id="o-q" placeholder="Search order, buyer, style, file…" value="${esc(OR.q)}" aria-label="Search orders"></label>
      <div class="seg" id="o-filter">${[['all', 'All'], ['edited', 'Edited'], ['pending', `Awaiting approval (${drafts.length})`]].map(([k, l]) => `<button type="button" data-f="${k}" class="${OR.filter === k ? 'active' : ''}">${l}</button>`).join('')}</div>
      <select id="o-sort" aria-label="Sort">${[['new', 'Newest first'], ['old', 'Oldest first'], ['qty', 'Largest quantity'], ['ref', 'Order reference']].map(([k, l]) => `<option value="${k}" ${OR.sort === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <a class="btn primary" href="#/new">${icon('plus')} New order</a></div>
    <div id="o-table"></div></section></div>`;
  const draw = () => { $('#o-table', view).innerHTML = OR.filter === 'pending' ? pendingTable(drafts) : ordersTable(); paintIcons(view); wireMenus(view); };
  $('#o-q', view).addEventListener('input', e => { OR.q = e.target.value; draw(); });
  $('#o-sort', view).addEventListener('change', e => { OR.sort = e.target.value; draw(); });
  $$('[data-f]', view).forEach(b => b.onclick = () => { OR.filter = b.dataset.f; $$('[data-f]', view).forEach(x => x.classList.toggle('active', x === b)); draw(); });
  draw();
  if (id) openOrder(Number(id), action === 'visual' ? 'visual' : action === 'history' ? 'history' : 'details');
});

function ordersTable() {
  const q = OR.q.trim().toLowerCase();
  let items = OR.items.filter(o => !q || [o.order_ref, o.buyer, o.style, o.filename, String(o.ordmasid)].some(v => String(v || '').toLowerCase().includes(q)));
  if (OR.filter === 'edited') items = items.filter(o => o.edits);
  const sorters = { new: (a, b) => String(b.saved_at).localeCompare(String(a.saved_at)), old: (a, b) => String(a.saved_at).localeCompare(String(b.saved_at)), qty: (a, b) => num(b.quantity) - num(a.quantity), ref: (a, b) => String(a.order_ref).localeCompare(String(b.order_ref)) };
  items.sort(sorters[OR.sort]);
  if (!items.length) return `<div class="empty">${icon('list')}<p>${OR.items.length ? 'No orders match your search.' : 'No orders saved yet. Upload an Excel order to get started.'}</p></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Order</th><th>Buyer</th><th>Style</th><th class="num">Quantity</th><th>Order date</th><th>Saved</th><th>Status</th><th></th></tr></thead><tbody>
    ${items.map(o => `<tr><td class="clickable" data-open="${o.ordmasid}"><strong>${esc(o.order_ref)}</strong><span class="sub">${esc(o.filename || '')} · ID ${o.ordmasid}</span></td><td>${esc(o.buyer || '—')}</td><td>${esc(o.style || '—')}</td>
      <td class="num">${fmt(o.quantity)}</td><td>${esc(o.order_date || '—')}</td><td>${when(o.saved_at)}<span class="sub">by ${esc(o.approver)}</span></td>
      <td>${o.status === 'Saved' ? '<span class="badge green">Saved</span>' : `<span class="badge amber">${esc(o.status)}</span>`}${o.edits ? ` <span class="badge blue">${plural(o.edits, 'edit')}</span>` : ''}</td>
      <td class="num"><div class="menu-wrap"><button class="icon-btn" data-menu="${o.ordmasid}" aria-label="Actions for ${esc(o.order_ref)}" aria-haspopup="menu">${icon('dots')}</button></div></td></tr>`).join('')}</tbody></table></div>`;
}
function pendingTable(drafts) {
  if (!drafts.length) return `<div class="empty">${icon('check')}<p>Nothing is waiting for approval.</p></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Workbook</th><th>Orders</th><th>Uploaded</th><th></th></tr></thead><tbody>${drafts.map(d => `<tr><td><strong>${esc(d.filename)}</strong><span class="sub">${esc(d.format)}</span></td><td>${esc(d.refs.filter(Boolean).join(', ') || plural(d.orders, 'order'))}</td><td>${when(d.created_at)}</td><td class="num"><a class="btn small soft" href="#/new/${esc(d.id)}">Review &amp; approve</a></td></tr>`).join('')}</tbody></table></div>`;
}
function wireMenus(view) {
  $$('[data-open]', view).forEach(td => td.onclick = () => openOrder(Number(td.dataset.open)));
  $$('[data-menu]', view).forEach(btn => btn.onclick = e => {
    e.stopPropagation(); $$('.menu').forEach(m => m.remove()); const id = btn.dataset.menu;
    const menu = document.createElement('div'); menu.className = 'menu'; menu.setAttribute('role', 'menu');
    menu.innerHTML = [['eye', 'View details', () => openOrder(Number(id))], ['edit', 'Edit order', () => go(`#/orders/${id}/edit`)], ['flow', 'View database flow', () => go(`#/database/flow/${id}`)],
      ['chart', 'Visualization', () => openOrder(Number(id), 'visual')], ['history', 'View change history', () => openOrder(Number(id), 'history')]].map(([ic, l], i) => `<button type="button" role="menuitem" data-i="${i}">${icon(ic)}${l}</button>`).join('');
    const actions = [() => openOrder(Number(id)), () => go(`#/orders/${id}/edit`), () => go(`#/database/flow/${id}`), () => openOrder(Number(id), 'visual'), () => openOrder(Number(id), 'history')];
    $$('button', menu).forEach(b => b.onclick = () => { menu.remove(); actions[Number(b.dataset.i)](); });
    document.body.appendChild(menu); const r = btn.getBoundingClientRect();
    menu.style.position = 'fixed'; menu.style.top = `${Math.min(r.bottom + 4, innerHeight - 230)}px`; menu.style.left = `${Math.max(8, r.right - 214)}px`; menu.style.right = 'auto';
    menu.querySelector('button').focus();
  });
}
document.addEventListener('click', () => $$('.menu').forEach(m => m.remove()));
document.addEventListener('keydown', e => { if (e.key === 'Escape') $$('.menu').forEach(m => m.remove()); });

const orderCache = {};
async function loadOrder(id, fresh = false) { if (fresh || !orderCache[id]) orderCache[id] = await api(`/api/orders/${id}`); return orderCache[id]; }

function openOrder(id, tab = 'details') {
  Drawer.open({ eyebrow: 'Order', title: `Order ${id}`, tabs: [['details', 'Details'], ['visual', 'Visualization'], ['history', 'Change history'], ['trail', 'Data trail']], active: tab, wide: true,
    after: (body, t) => { if (t === 'trail') wireTrail(body, id); }, render: async t => { const d = await loadOrder(id, true); $('#drawer-title').textContent = d.summary.order_ref || `Order ${id}`;
      if (t === 'visual') return orderDashboard(d);
      if (t === 'history') return historyHtml(await api(`/api/orders/${id}/history`));
      if (t === 'trail') return trailHtml(id, await api(`/api/orders/${id}/foundation`));
      return orderDetails(d); } });
}

function valueText(d, table, col, v) {
  const lab = d.labels[`${table}.${col}`]; if (lab && v != null) return `${esc(lab[String(v)] ?? v)} <span class="faint mono">#${esc(v)}</span>`;
  return v == null ? '<span class="blank">blank</span>' : esc(val(col, v));
}
function orderDetails(d) {
  if (d.mode === 'table') return tableOrderDetails(d);
  const s = d.summary; const head = d.rows.ORDMAS[0]; const src = d.sources || {};
  const filled = Object.entries(head).filter(([, v]) => v != null);
  return `<div class="row"><a class="btn primary small" href="#/orders/${d.ordmasid}/edit">${icon('edit')} Edit order</a><a class="btn small" href="#/database/flow/${d.ordmasid}">${icon('flow')} Database flow</a></div>
    <div class="dash-tiles"><div><span>Buyer</span><strong>${esc(s.buyer || '—')}</strong></div><div><span>Style</span><strong>${esc(s.style || '—')}</strong></div><div><span>Order quantity</span><strong>${fmt(s.total)}</strong></div><div><span>Order date</span><strong>${esc(s.order_date || '—')}</strong></div></div>
    ${d.import ? `<div class="callout success">${icon('check')}<div>Saved from <strong>${esc(d.import.filename || 'workbook')}</strong> on ${when(d.import.committed_at)} by ${esc(d.import.approver)}. Values below are read live from Oracle.</div></div>` : ''}
    <section class="card"><div class="card-head"><div class="table-name"><span class="tname">ORDMAS</span><span class="muted">· Order header · ${plural(filled.length, 'filled column')}</span></div></div>
      <div class="table-wrap"><table class="kv"><tbody>${filled.map(([k, v]) => `<tr><td><span class="col-name">${esc(k)}</span><span class="col-label">${esc(COLUMN_LABELS[k] || '')}</span></td><td>${valueText(d, 'ORDMAS', k, v)}</td><td>${sourceChip(src[`ORDMAS:${head.ORDMASID}`]?.[k]?.source)}</td></tr>`).join('')}</tbody></table></div></section>
    ${rowsTable(d, 'ORDSTYLE', ['SNO', 'COMBO', 'PONUM', 'ORDQTY', 'PRODQTY', 'SHIPDT', 'SUBNO'], 'ORDSTYLEID')}
    ${rowsTable(d, 'ORDSIZE', ['SUB3_SUBNO', 'GSIZE', 'RQTY', 'PQTY', 'ORDSIZEROW'], 'ORDSIZEID')}`;
}
function rowsTable(d, table, preferred, pk) {
  const rows = d.rows[table]; if (!rows.length) return `<section class="card"><div class="card-head"><span class="tname mono">${table}</span><span class="sub">No records for this order</span></div></section>`;
  const cols = [...new Set([...preferred.filter(c => rows.some(r => r[c] != null)), ...Object.keys(rows[0]).filter(c => c !== pk && !preferred.includes(c) && c !== 'ORDMASID' && rows.some(r => r[c] != null))])];
  return `<section class="card"><div class="card-head"><div class="table-name"><span class="tname mono">${table}</span><span class="muted">· ${esc(tableLabel(table))} · ${plural(rows.length, 'record')}</span></div></div>
    <div class="table-wrap" style="max-height:360px"><table><thead><tr><th>${pk}</th>${cols.map(c => `<th title="${esc(COLUMN_LABELS[c] || '')}">${c}</th>`).join('')}</tr></thead><tbody>
    ${rows.map(r => `<tr><td class="mono">${esc(r[pk])}</td>${cols.map(c => { const s = d.sources?.[`${table}:${r[pk]}`]?.[c]?.source; return `<td>${valueText(d, table, c, r[c])}${s?.cell ? `<span class="sub">${sourceChip(s)}</span>` : ''}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}

const PALETTE = ['#0f8a5f', '#2b6a99', '#9a6512', '#6b4fa3', '#1fa971', '#c2413b', '#4c9ac9', '#b98a2e'];
function orderDashboard(d) {
  const s = d.summary; const maxSize = Math.max(1, ...s.sizes.map(x => num(x.quantity))); const maxLine = Math.max(1, ...s.lines.map(l => num(l.quantity)));
  const recTotal = Object.values(s.records).reduce((a, b) => a + b, 0);
  let angle = 0; const total = s.lines.reduce((a, l) => a + num(l.quantity), 0);
  const arcs = total ? s.lines.map((l, i) => { const a0 = angle; angle += num(l.quantity) / total * Math.PI * 2; const a1 = angle; const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (a, r) => `${60 + r * Math.sin(a)},${60 - r * Math.cos(a)}`;
    if (s.lines.length === 1) return `<circle cx="60" cy="60" r="44" fill="none" stroke="${PALETTE[0]}" stroke-width="22"/>`;
    return `<path d="M${p(a0, 55)} A55 55 0 ${large} 1 ${p(a1, 55)} L${p(a1, 33)} A33 33 0 ${large} 0 ${p(a0, 33)} Z" fill="${PALETTE[i % PALETTE.length]}"/>`; }).join('') : '';
  return `<div class="dash-tiles"><div><span>Order quantity</span><strong>${fmt(s.total)}</strong></div><div><span>Lines</span><strong>${fmt(s.lines.length)}</strong></div>
      <div><span>Sizes</span><strong>${fmt(s.sizes.length)}</strong></div><div><span>Ship date${s.ship_dates.length > 1 ? 's' : ''}</span><strong>${esc(s.ship_dates.join(', ') || '—')}</strong></div></div>
    <div class="callout ${s.reconciled ? 'success' : 'attention'}">${icon(s.reconciled ? 'check' : 'alert')}<div>${s.reconciled ? `Order quantity matches the sum of the size quantities (${fmt(s.size_total)}).` : `Order quantity ${fmt(s.total)} differs from the sum of sizes ${fmt(s.size_total)}.`}</div></div>
    <section class="card card-pad"><h3>Quantity by size</h3>${s.sizes.length ? `<div class="vbars">${s.sizes.map(x => `<div class="vb"><b>${fmt(x.quantity)}</b><i style="height:${Math.max(2, num(x.quantity) / maxSize * 120)}px"></i></div>`).join('')}</div><div class="vlabels">${s.sizes.map(x => `<span>${esc(x.size)}</span>`).join('')}</div>` : '<p class="muted">No size records.</p>'}</section>
    <div class="grid g2"><section class="card card-pad"><h3>Lines / colours</h3>${total ? `<div class="donut-wrap"><svg viewBox="0 0 120 120" width="130" height="130" role="img" aria-label="Quantity by line">${arcs}</svg><div class="legend">${s.lines.map((l, i) => `<span><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(l.label)} · ${fmt(l.quantity)} (${Math.round(num(l.quantity) / total * 100)}%)</span>`).join('')}</div></div>` : '<p class="muted">No line quantities.</p>'}</section>
      <section class="card card-pad"><h3>Records in Oracle</h3><div class="bars" style="margin-top:10px">${Object.entries(s.records).map(([t, n]) => `<div class="bar-row"><span class="lbl mono">${t}</span><span class="track"><span class="fill" style="width:${recTotal ? n / Math.max(...Object.values(s.records)) * 100 : 0}%;display:block"></span></span><span class="val">${n}</span></div>`).join('')}</div>
        <p class="faint" style="margin-top:10px">Counted per table separately, so nothing is double counted.</p></section></div>
    ${s.lines.length ? `<section class="card card-pad"><h3>Line detail</h3><div class="bars" style="margin-top:10px">${s.lines.map(l => `<div class="bar-row"><span class="lbl" title="${esc(l.label)}">${esc(l.label)}</span><span class="track"><span class="fill" style="width:${num(l.quantity) / maxLine * 100}%;display:block"></span></span><span class="val">${fmt(l.quantity)}</span></div><div class="faint" style="font-size:12px;margin:-4px 0 4px 100px">${l.sizes.map(z => `${esc(z.size)}: ${fmt(z.quantity)}`).join(' · ')}${l.ship_date ? ` · ships ${esc(l.ship_date)}` : ''}</div>`).join('')}</div></section>` : ''}
    <section class="card card-pad"><h3>Completeness</h3><p class="muted" style="margin:4px 0 10px">Columns that hold a value. Blank optional columns are normal when the buyer did not send that information.</p>
      <div class="bars">${Object.entries(s.filled).map(([t, cols]) => `<div class="bar-row"><span class="lbl mono">${t}</span><span class="track"><span class="fill" style="width:${Math.min(100, cols.length / Math.max(1, ...Object.values(s.filled).map(c => c.length)) * 100)}%;display:block"></span></span><span class="val">${cols.length}</span></div>`).join('')}</div></section>`;
}

function historyHtml(h) {
  const groups = {}; h.changes.forEach(c => { (groups[c.changeset] ||= []).push(c); });
  return `${h.import_ ? `<div class="callout success">${icon('upload')}<div><strong>Created from Excel</strong> · ${esc(h.import_.filename || '')} · approved by ${esc(h.import_.approver)} on ${when(h.import_.committed_at)}</div></div>` : ''}
    ${Object.keys(groups).length ? Object.values(groups).map(items => `<section class="card"><div class="card-head"><div><strong>Approved change</strong><span class="sub">${when(items[0].changed_at)} · by ${esc(items[0].approver)}</span></div><span class="badge blue">${plural(items.length, 'value')}</span></div>
      <div class="table-wrap"><table><thead><tr><th>Column</th><th>Old value</th><th></th><th>New value</th></tr></thead><tbody>${items.map(c => `<tr><td><span class="col-name">${esc(c.table)}.${esc(c.column)}</span><span class="col-label">record ${esc(c.row_id)}</span></td><td>${c.old == null ? '<span class="blank">blank</span>' : esc(c.old)}</td><td class="arrow">→</td><td><strong>${c.new == null ? '<span class="blank">blank</span>' : esc(c.new)}</strong></td></tr>`).join('')}</tbody></table></div></section>`).join('') : '<div class="empty">No changes since the order was saved.</div>'}`;
}

/* ---------------- edit with preview + approval ---------------- */
const EDIT_HEAD = ['DOCID', 'BUYER', 'STYLENO', 'STYLEDESC', 'BONO', 'ODATE', 'SAMPLETYPE', 'MERCH', 'REMARKS'];
const REF_ROLE = { 'ORDMAS.BUYER': 'buyer', 'ORDMAS.STYLENO': 'style', 'ORDMAS.STYLEDESC': 'description', 'ORDMAS.MERCH': 'merch', 'ORDSTYLE.COMBO': 'color', 'ORDSIZE.GSIZE': 'size' };
async function renderEdit(view, id) {
  const d0 = await loadOrder(id, true); if (d0.mode === 'table') return renderTableEdit(view, id, d0);
  const d = d0; const masters = await api('/api/masters').catch(() => ({}));
  setTitle(`Edit ${d.summary.order_ref || 'order'}`, 'Proposed changes are previewed and approved before Oracle is updated');
  const head = d.rows.ORDMAS[0]; const src = d.sources || {};
  const excel = (t, rid, col) => { const s = src[`${t}:${rid}`]?.[col]; const v = s?.raw ?? s?.source?.value; return v == null ? '<span class="faint">—</span>' : `${esc(fmt(v))} ${sourceChip(s.source)}`; };
  const current = (t, col, v) => { const lab = d.labels[`${t}.${col}`]; return v == null ? '' : lab ? (lab[String(v)] ?? String(v)) : String(v); };
  const input = (t, rid, col, v) => { const role = REF_ROLE[`${t}.${col}`]; return `<input data-t="${t}" data-id="${rid}" data-c="${col}" data-orig="${esc(current(t, col, v))}" value="${esc(current(t, col, v))}" ${role ? `list="ml-${role}"` : ''} ${col.endsWith('DATE') || col === 'ODATE' || col === 'SHIPDT' ? 'placeholder="YYYY-MM-DD"' : 'placeholder="blank"'}>`; };
  const row = (t, rid, col, v, label) => `<tr data-row><td><span class="col-name">${esc(col)}</span><span class="col-label">${esc(label || COLUMN_LABELS[col] || '')}</span></td><td>${excel(t, rid, col)}</td><td>${v == null ? '<span class="blank">blank</span>' : esc(current(t, col, v))}</td><td>${input(t, rid, col, v)}</td></tr>`;
  const extraHead = Object.keys(head).filter(k => head[k] != null && !EDIT_HEAD.includes(k) && !['ORDMASID', 'OQTY', 'UOM', 'ORDCATEGORY'].includes(k));
  view.innerHTML = `<div class="stack"><div class="row between"><a class="btn ghost" href="#/orders/${id}">${icon('back')} Back to order</a><span class="muted">Oracle is not changed until you approve the preview.</span></div>
    ${Object.entries(masters).map(([role, list]) => `<datalist id="ml-${role}">${list.filter(m => !m.cancelled).map(m => `<option value="${esc(m.label)}"></option>`).join('')}</datalist>`).join('')}
    <section class="card"><div class="card-head"><div><h2>Order header</h2><span class="sub">ORDMAS · record ${id}</span></div></div><div class="table-wrap"><table class="compare"><thead><tr><th>Field</th><th>Original Excel</th><th>Current Oracle</th><th>Proposed</th></tr></thead><tbody>
      ${[...EDIT_HEAD, ...extraHead].map(c => row('ORDMAS', id, c, head[c])).join('')}</tbody></table></div></section>
    ${d.rows.ORDSTYLE.map(l => { const sizes = d.rows.ORDSIZE.filter(s => s.SUB3_SUBNO === l.SUBNO);
      return `<section class="card"><div class="card-head"><div><h2>Line ${esc(l.SNO ?? '')} ${l.COMBO != null ? '· ' + esc(current('ORDSTYLE', 'COMBO', l.COMBO)) : ''}</h2><span class="sub">ORDSTYLE · record ${l.ORDSTYLEID} · line quantity ${fmt(l.ORDQTY)} (recalculated from sizes)</span></div></div>
        <div class="table-wrap"><table class="compare"><thead><tr><th>Field</th><th>Original Excel</th><th>Current Oracle</th><th>Proposed</th></tr></thead><tbody>
        ${['COMBO', 'SHIPDT', 'PONUM'].map(c => row('ORDSTYLE', l.ORDSTYLEID, c, l[c])).join('')}
        ${sizes.map(s => row('ORDSIZE', s.ORDSIZEID, 'RQTY', s.RQTY, `Quantity · size ${current('ORDSIZE', 'GSIZE', s.GSIZE)}`)).join('')}</tbody></table></div></section>`; }).join('')}
    <div class="action-bar"><span class="muted" id="e-count">No changes yet.</span><div class="row"><a class="btn" href="#/orders/${id}">Cancel</a><button class="btn primary" id="e-review" disabled>${icon('eye')} Review changes</button></div></div></div>`;
  paintIcons(view);
  const changes = () => $$('input[data-t]', view).filter(i => i.value.trim() !== i.dataset.orig);
  view.addEventListener('input', () => { const n = changes().length; $$('[data-row]', view).forEach(r => r.classList.toggle('changed', !!$('input', r) && $('input', r).value.trim() !== $('input', r).dataset.orig));
    $('#e-count', view).textContent = n ? `${plural(n, 'value')} changed — not saved yet.` : 'No changes yet.'; $('#e-review', view).disabled = !n; });
  $('#e-review', view).onclick = () => busy('Checking the changes against Oracle…', async () => {
    const list = changes().flatMap(i => { const base = { table: i.dataset.t, id: Number(i.dataset.id), column: i.dataset.c, value: i.value.trim() };
      if (base.table === 'ORDSIZE' && base.column === 'RQTY') { const s = d.rows.ORDSIZE.find(x => x.ORDSIZEID === base.id); if (s && s.PQTY === s.RQTY) return [base, { ...base, column: 'PQTY' }]; }
      return [base]; });
    const p = await api(`/api/orders/${id}/changes`, { method: 'POST', body: { changes: list } });
    const body = Modal.open('Review changes before approval', `
      ${p.errors.length ? `<div class="callout attention">${icon('alert')}<div><strong>Please correct</strong><ul>${p.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` : ''}
      ${p.changes.length ? `<div class="table-wrap"><table><thead><tr><th>Oracle column</th><th>Record</th><th>Current</th><th></th><th>After approval</th></tr></thead><tbody>${p.changes.map(c => `<tr><td><span class="col-name">${esc(c.table)}.${esc(c.column)}</span>${c.derived ? '<span class="col-label">recalculated total</span>' : ''}</td><td class="mono">${esc(c.id)}</td><td>${c.old == null ? '<span class="blank">blank</span>' : esc(c.old_label || fmt(c.old))}</td><td class="arrow">→</td><td><strong>${c.new == null ? '<span class="blank">blank</span>' : esc(c.new_label || fmt(c.new))}</strong></td></tr>`).join('')}</tbody></table></div>` : ''}
      ${p.notes.length ? `<div class="callout">${icon('info')}<ul>${p.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul></div>` : ''}
      <div class="modal-foot"><button class="btn" data-cancel>Keep editing</button><button class="btn primary" data-approve ${p.errors.length ? 'disabled' : ''}>${icon('check')} Approve &amp; update Oracle</button></div>`);
    $('[data-cancel]', body).onclick = () => Modal.close();
    $('[data-approve]', body).onclick = () => busy('Updating Oracle and verifying…', async () => {
      await api(`/api/changes/${p.id}/approve`, { method: 'POST', body: { hash: p.hash } }); Modal.close();
      toast(`${plural(p.changes.length, 'value')} updated in Oracle and verified. The change is in the order history.`); go(`#/orders/${id}/history`);
    });
  });
}

/* ---------------- single-table orders (EXCEL_ORDERS) ---------------- */
const ROW_LEVEL = new Set(['ORDSTYLEROW', 'GARMENT', 'COMBO', 'ORDSIZEROW', 'SIZES', 'ORDERQTY', 'QTYTYPE', 'SOURCE_CELL']);
const SYSTEM_COLS = new Set(['ENTRY_ID', 'ORDER_KEY', 'IMPORT_ID', 'SOURCE_FILE', 'CREATEDBY', 'CREATEDON']);
function tableOrderDetails(d) {
  const s = d.summary; const rows = d.rows[d.table]; const head = rows[0]; const src = d.sources || {};
  const order = Object.keys(head).filter(k => head[k] != null && !ROW_LEVEL.has(k) && !SYSTEM_COLS.has(k));
  return `<div class="row"><a class="btn primary small" href="#/orders/${d.ordmasid}/edit">${icon('edit')} Edit order</a><a class="btn small" href="#/database/flow/${d.ordmasid}">${icon('flow')} Database</a></div>
    <div class="dash-tiles"><div><span>Buyer</span><strong>${esc(s.buyer || '—')}</strong></div><div><span>Style</span><strong>${esc(s.style || '—')}</strong></div><div><span>Total pieces</span><strong>${fmt(s.total)}</strong></div><div><span>Ship date</span><strong>${esc(s.ship_dates.join(', ') || '—')}</strong></div></div>
    ${d.import ? `<div class="callout success">${icon('check')}<div>Saved from <strong>${esc(d.import.filename || 'workbook')}</strong> on ${when(d.import.committed_at)} by ${esc(d.import.approver)} into <code>${esc(d.table)}</code>. Values below are read live from Oracle.</div></div>` : ''}
    <section class="card"><div class="card-head"><div class="table-name"><span class="tname">${esc(d.table)}</span><span class="muted">· order details (same in every row)</span></div></div>
      <div class="table-wrap"><table class="kv"><tbody>${order.map(k => `<tr><td><span class="col-name">${esc(k)}</span><span class="col-label">${esc(COLUMN_LABELS[k] || '')}</span></td><td>${esc(val(k, head[k]))}</td><td>${sourceChip(src[`${d.table}:${head.ENTRY_ID}`]?.[k]?.source)}</td></tr>`).join('')}</tbody></table></div></section>
    ${rowsTable(d, d.table, ['ORDSTYLEROW', 'GARMENT', 'COMBO', 'SIZES', 'ORDERQTY', 'QTYTYPE', 'SOURCE_CELL'], 'ENTRY_ID').replace(/<th>(\w+)<\/th>/, '<th>Row ID</th>')}`;
}

async function renderTableEdit(view, id, d) {
  const meta = await api('/api/schema/order-columns'); const rows = d.rows[d.table]; const head = rows[0]; const src = d.sources || {};
  setTitle(`Edit ${d.summary.order_ref || 'order'}`, 'Proposed changes are previewed and approved before Oracle is updated');
  const cols = (meta.columns || []).filter(c => c.level === 'order');
  const excel = (rid, col) => { const x = src[`${d.table}:${rid}`]?.[col]; const v = x?.raw ?? x?.source?.value; return v == null ? '<span class="faint">—</span>' : `${esc(fmt(v))} ${sourceChip(x.source)}`; };
  const cur = v => v == null ? '' : String(v);
  view.innerHTML = `<div class="stack"><div class="row between"><a class="btn ghost" href="#/orders/${id}">${icon('back')} Back to order</a><span class="muted">Oracle is not changed until you approve the preview.</span></div>
    <section class="card"><div class="card-head"><div><h2>Order details</h2><span class="sub">${esc(d.table)} · a change here updates all ${rows.length} rows of this order</span></div></div>
      <div class="table-wrap"><table class="compare"><thead><tr><th>Field</th><th>Original Excel</th><th>Current Oracle</th><th>Proposed</th></tr></thead><tbody>
      ${cols.map(c => `<tr data-row><td><span class="col-name">${esc(c.name)}</span><span class="col-label">${esc(c.label)}</span></td><td>${excel(head.ENTRY_ID, c.name)}</td><td>${head[c.name] == null ? '<span class="blank">blank</span>' : esc(val(c.name, head[c.name]))}</td>
        <td><input data-id="all" data-c="${esc(c.name)}" data-orig="${esc(cur(head[c.name]))}" value="${esc(cur(head[c.name]))}" placeholder="${c.type === 'DATE' ? 'YYYY-MM-DD' : 'blank'}"></td></tr>`).join('')}</tbody></table></div></section>
    <section class="card"><div class="card-head"><div><h2>Quantities</h2><span class="sub">One row per size · total pieces is recalculated</span></div></div>
      <div class="table-wrap"><table class="compare"><thead><tr><th>Line · colour</th><th>Size</th><th>Original Excel</th><th>Current Oracle</th><th>Proposed</th></tr></thead><tbody>
      ${rows.filter(r => r.SIZES != null).map(r => `<tr data-row><td>${esc(r.ORDSTYLEROW ?? '')} · ${esc(r.COMBO || '')}</td><td><strong>${esc(r.SIZES)}</strong></td><td>${excel(r.ENTRY_ID, 'ORDERQTY')}</td><td>${esc(fmt(r.ORDERQTY))}</td>
        <td><input data-id="${r.ENTRY_ID}" data-c="ORDERQTY" data-orig="${esc(cur(r.ORDERQTY))}" value="${esc(cur(r.ORDERQTY))}" inputmode="numeric"></td></tr>`).join('')}</tbody></table></div></section>
    <div class="action-bar"><span class="muted" id="e-count">No changes yet.</span><div class="row"><a class="btn" href="#/orders/${id}">Cancel</a><button class="btn primary" id="e-review" disabled>${icon('eye')} Review changes</button></div></div></div>`;
  paintIcons(view);
  const changed = () => $$('input[data-c]', view).filter(i => i.value.trim() !== i.dataset.orig);
  view.addEventListener('input', () => { const n = changed().length; $$('[data-row]', view).forEach(r => { const i = $('input', r); r.classList.toggle('changed', !!i && i.value.trim() !== i.dataset.orig); });
    $('#e-count', view).textContent = n ? `${plural(n, 'value')} changed — not saved yet.` : 'No changes yet.'; $('#e-review', view).disabled = !n; });
  $('#e-review', view).onclick = () => busy('Checking the changes against Oracle…', async () => {
    const list = changed().map(i => ({ id: i.dataset.id === 'all' ? 'all' : Number(i.dataset.id), column: i.dataset.c, value: i.value.trim() }));
    const p = await api(`/api/orders/${id}/changes`, { method: 'POST', body: { changes: list } });
    const grouped = {}; p.changes.forEach(c => { const k = `${c.column}|${c.old}|${c.new}`; (grouped[k] ||= { ...c, rows: 0 }).rows++; });
    const body = Modal.open('Review changes before approval', `
      ${p.errors.length ? `<div class="callout attention">${icon('alert')}<div><strong>Please correct</strong><ul>${p.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` : ''}
      ${p.changes.length ? `<div class="table-wrap"><table><thead><tr><th>Oracle column</th><th>Rows</th><th>Current</th><th></th><th>After approval</th></tr></thead><tbody>${Object.values(grouped).map(c => `<tr><td><span class="col-name">${esc(c.column)}</span><span class="col-label">${esc(c.label || '')}${c.derived ? ' · recalculated' : ''}</span></td><td>${c.rows}</td><td>${c.old == null ? '<span class="blank">blank</span>' : esc(fmt(c.old))}</td><td class="arrow">→</td><td><strong>${c.new == null ? '<span class="blank">blank</span>' : esc(fmt(c.new))}</strong></td></tr>`).join('')}</tbody></table></div>` : ''}
      ${p.notes.length ? `<div class="callout">${icon('info')}<ul>${p.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul></div>` : ''}
      <div class="modal-foot"><button class="btn" data-cancel>Keep editing</button><button class="btn primary" data-approve ${p.errors.length ? 'disabled' : ''}>${icon('check')} Approve &amp; update Oracle</button></div>`);
    $('[data-cancel]', body).onclick = () => Modal.close();
    $('[data-approve]', body).onclick = () => busy('Updating Oracle and verifying…', async () => {
      await api(`/api/changes/${p.id}/approve`, { method: 'POST', body: { hash: p.hash } }); Modal.close();
      toast(`${plural(p.changes.length, 'value')} updated in Oracle and verified. The change is in the order history.`); go(`#/orders/${id}/history`);
    });
  });
}

/* ---------------- data trail: identity, blank meanings, quality checks, value history ---------------- */
const NULL_TEXT = { PROVIDED: ['Provided', 'green', 'A value from Excel or typed by you was saved.'], NOT_PROVIDED: ['Not in Excel', '', 'The Excel does not mention it. Left blank.'],
  NOT_APPLICABLE: ['Not applicable', 'violet', 'You marked it as not applicable.'], UNKNOWN: ['Unknown', 'amber', 'The Excel has the label but the value is empty.'], INVALID: ['Invalid', 'red', 'The Excel value could not be used.'] };
function trailHtml(id, f) {
  return `<div class="callout">${icon('shield')}<div>Order identity <code>${esc(f.uuid)}</code> · saved from <strong>${esc(f.filename || '')}</strong> (sha256 ${esc(String(f.file_hash).slice(0, 12))}…) · approved by ${esc(f.approved_by)} on ${when(f.approved_at)} · ${f.links.map(l => `${esc(l.table_name)}: ${l.rows} rows`).join(', ')}</div></div>
    <section class="card"><div class="card-head"><div><h3>Every field and what its value means</h3><span class="sub">Blank is not the same as “no”: click a field to see where its value came from and every change since</span></div></div>
      ${f.fields.length ? '' : '<p class="muted card-pad">Field meanings are recorded for orders approved in this workspace.</p>'}
      <div class="table-wrap"><table><tbody>${f.fields.map(x => `<tr class="clickable" data-trail="${esc(x.column_name)}"><td><span class="col-name">${esc(x.column_name)}</span><span class="col-label">${esc(COLUMN_LABELS[x.column_name] || '')}</span></td><td><span class="badge ${NULL_TEXT[x.status]?.[1] || ''}">${esc(NULL_TEXT[x.status]?.[0] || x.status)}</span></td><td class="muted">${esc(x.note || NULL_TEXT[x.status]?.[2] || '')}</td></tr>`).join('')}</tbody></table></div></section>
    <section class="card"><div class="card-head"><h3>Checks that ran for this order</h3></div><div class="table-wrap"><table><tbody>${f.quality.map(q => `<tr><td><span class="badge ${q.outcome === 'PASS' ? 'green' : q.outcome === 'WARN' ? 'amber' : q.outcome === 'SKIP' ? '' : 'red'}">${esc(q.outcome.toLowerCase())}</span></td><td><strong>${esc(q.rule)}</strong><span class="sub">${esc(q.detail)}</span></td></tr>`).join('')}</tbody></table></div></section>
    <div id="trail-detail"></div>`;
}
function wireTrail(body, id) {
  $$('[data-trail]', body).forEach(r => r.onclick = () => busy('Reading the data trail…', async () => {
    const col = r.dataset.trail; const t = await api(`/api/orders/${id}/lineage?column=${encodeURIComponent(col)}`); const first = t.lineage[0];
    $('#trail-detail', body).innerHTML = `<section class="card"><div class="card-head"><h3>${esc(col)} · ${esc(COLUMN_LABELS[col] || '')}</h3><span class="sub">${plural(t.lineage.length, 'saved row')}</span></div><div class="card-pad stack" style="gap:8px">
      ${first ? `<div>${first.cell ? `From Excel <span class="chip">${esc(first.sheet)}!${esc(first.cell)}</span> raw value <strong>${esc(first.raw_value ?? '—')}</strong>` : `Origin: <strong>${esc(first.via || first.origin || 'not from Excel')}</strong>`} → saved <strong>${esc(first.saved_value ?? 'blank')}</strong>${first.transformed ? ' <span class="badge violet">converted</span>' : ''} · approved by ${esc(first.approved_by)} · ${first.verified ? '<span class="badge green">read back OK</span>' : '<span class="badge amber">not verified</span>'}</div>` : '<div class="muted">No saved value: this field was blank when the order was approved.</div>'}
      ${t.history.length > 1 ? `<div><strong>Versions</strong><ul class="timeline" style="padding:4px 0">${t.history.map(h => `<li><span class="dot"></span><span>v${h.version}: <strong>${esc(h.value ?? 'blank')}</strong>${h.previous != null ? ` <span class="muted">(was ${esc(h.previous)})</span>` : h.source ? ` <span class="muted">from ${esc(h.source)}</span>` : ''} · ${esc(h.by)}</span><time>${when(h.at)}</time></li>`).join('')}</ul></div>` : ''}</div></section>`;
    $('#trail-detail', body).scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }));
}
