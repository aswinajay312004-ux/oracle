'use strict';
/* Orders: everything POPPYS saved to Oracle. Open an order to see its values (with their Excel cells), change them
   with approval, and read its history. Values always come from Oracle as it is now. */
const ORD = { list: [], q: '', filter: 'all', id: null, data: null, editing: false, edits: {}, rowEdits: {} };
const DERIVED_COLS = new Set(['ORDSTYLEROW', 'ORDSIZEROW', 'COMBOQTY']);
const SIZE_COLS = ['COMBO', 'SIZES', 'ORDERQTY'];

route('orders', async (view, [id]) => {
  setTitle('Orders', 'Orders saved to Oracle by POPPYS. Open one to see its values, edit them with approval, and see its history.',
    `<a class="btn primary" href="#/new">${icon('upload')} New order</a>`);
  ORD.list = await api('/api/orders');
  page(view, `<section class="card"><div class="orders-bar">
      <div class="search">${icon('search')}<input id="o-q" placeholder="Search OC number, buyer, style or file" value="${esc(ORD.q)}" aria-label="Search orders"></div>
      <div class="seg">${[['all', 'All'], ['editable', 'Editable'], ['earlier', 'Earlier versions']].map(([k, l]) =>
        `<button type="button" data-of="${k}" class="${ORD.filter === k ? 'active' : ''}">${l}</button>`).join('')}</div>
      <span class="muted" id="o-count" style="margin-left:auto"></span></div>
    <div id="o-table"></div></section>`);
  const draw = () => drawOrders($('#o-table', view));
  $('#o-q', view).oninput = e => { ORD.q = e.target.value; draw(); };
  $$('[data-of]', view).forEach(b => b.onclick = () => { ORD.filter = b.dataset.of; $$('[data-of]', view).forEach(x => x.classList.toggle('active', x === b)); draw(); });
  draw();
  if (id) openOrder(Number(id));
});

function drawOrders(el) {
  const q = ORD.q.trim().toLowerCase();
  const items = ORD.list.filter(o => (ORD.filter === 'all' || (ORD.filter === 'editable' ? o.editable : !o.editable))
    && (!q || `${o.order_ref} ${o.buyer || ''} ${o.style || ''} ${o.filename || ''}`.toLowerCase().includes(q)));
  $('#o-count').textContent = plural(items.length, 'order');
  if (!ORD.list.length) { el.innerHTML = `<div class="empty">${icon('file')}<p>No orders saved yet.</p><p><a class="btn primary" href="#/new">${icon('upload')} Upload a buyer Excel</a></p></div>`; paintIcons(el); return; }
  if (!items.length) { el.innerHTML = '<div class="empty">No order matches the search.</div>'; return; }
  el.innerHTML = `<div class="table-wrap"><table class="data-table"><thead><tr><th>OC number</th><th>Buyer</th><th>Style</th><th class="num">Pieces</th><th class="num">Rows</th>
    <th>Saved on</th><th>Saved by</th><th>Status</th><th><span class="visually-hidden">Open</span></th></tr></thead><tbody>
    ${items.map(o => `<tr class="clickable" data-open="${o.ordmasid}"><td><strong>${esc(o.order_ref)}</strong>${o.mode !== 'entry' ? ' <span class="badge">earlier version</span>' : ''}
        ${o.filename ? `<span class="sub">${esc(o.filename)}</span>` : ''}</td><td>${esc(o.buyer || '—')}</td><td>${esc(o.style || '—')}</td>
      <td class="num">${fmt(o.quantity)}</td><td class="num">${fmt(o.rows)}</td><td>${when(o.saved_at)}${o.edits ? `<span class="sub">${plural(o.edits, 'approved change')}</span>` : ''}</td>
      <td><span class="who"><i>${esc((o.approver || '?').slice(0, 1).toUpperCase())}</i>${esc(o.approver || '—')}</span></td>
      <td><span class="status-chip ${o.status === 'Saved' ? 'green' : 'red'}">${esc(o.status)}</span></td>
      <td><button class="dots-btn" type="button" data-open="${o.ordmasid}" aria-label="Open order ${esc(o.order_ref)}">${icon('eye')}</button></td></tr>`).join('')}</tbody></table></div>`;
  paintIcons(el);
  $$('tr[data-open]', el).forEach(r => r.onclick = () => go(`#/orders/${r.dataset.open}`));
}

/* ------------------------------------------------------------------ one order */
function openOrder(id) {
  Object.assign(ORD, { id, data: null, editing: false, edits: {}, rowEdits: {} });
  const o = ORD.list.find(x => x.ordmasid === id);
  Drawer.open({ eyebrow: 'Saved order', title: o ? `Order ${o.order_ref}` : 'Order', wide: true,
    tabs: [['details', 'Details'], ['rows', 'Saved rows'], ['history', 'History']], active: 'details',
    render: async tab => {
      if (!ORD.data || ORD.data.ordmasid !== id) ORD.data = await api(`/api/orders/${id}`);
      $('#drawer-title').textContent = `Order ${ORD.data.summary?.order_ref || o?.order_ref || id}`;
      if (tab === 'rows') return rowsTab(ORD.data);
      if (tab === 'history') return historyTab(await api(`/api/orders/${id}/history`), ORD.data);
      return detailsTab(ORD.data);
    },
    after: (body, tab) => { if (tab === 'details') wireDetails(body); } });
}

function boxesFor(d, t) {
  const rows = (d.rows[t] || []).filter(r => !r._missing); const srcs = d.sources?.columns?.[t] || {};
  return (d.columns[t] || []).map(c => {
    const vals = rows.map(r => r[c.name]); const present = vals.filter(v => v != null && v !== '');
    const distinct = [...new Set(present.map(String))];
    const level = distinct.length > 1 ? 'row' : 'order'; const src = srcs[c.name];
    const state = !present.length ? (c.system ? 'system' : 'blank') : c.system ? 'system' : src ? 'excel' : DERIVED_COLS.has(c.name) ? 'derived' : 'typed';
    const nums = present.filter(v => typeof v === 'number');
    return { col: c, box: { level, count: present.length, values: distinct.slice(0, 8), distinct: distinct.length, value: level === 'order' ? present[0] ?? null : null,
      total: level === 'row' && c.name === 'ORDERQTY' && nums.length ? nums.reduce((a, b) => a + b, 0) : null, state, source: src || (state === 'derived' ? { origin: 'Calculated from Excel' } : null),
      note: state === 'typed' ? 'Value in Oracle' : state === 'system' ? 'Filled by the ERP' : null } };
  });
}
function canEdit(d) { return d.editable && Object.values(d.can_update || {}).some(Boolean); }

function detailsTab(d) {
  const s = d.summary || {}; const imp = d.import_ || {};
  const facts = [['OC number', s.order_ref], ['Buyer', s.buyer], ['Style', s.style], ['Pieces', s.size_total ? fmt(s.size_total) : null], ['Ship date', s.ship_date],
    ['Saved by', imp.approver], ['Saved on', imp.committed_at && when(imp.committed_at)]].filter(([, v]) => v != null && v !== '');
  const head = `<div class="row between"><div class="facts" style="margin:0">${facts.map(([k, v]) => `<div><span>${k}</span><strong>${esc(v)}</strong></div>`).join('')}
      ${imp.filename ? `<div><span>Excel file</span><strong><a href="/api/drafts/${esc(imp.job_id)}/file">${esc(imp.filename)}</a></strong></div>` : ''}</div>
    ${canEdit(d) && !ORD.editing ? `<button class="btn primary" id="o-edit">${icon('edit')} Edit</button>` : ''}</div>`;
  const notes = [d.mode !== 'entry' ? `<div class="callout info">${icon('info')}<div>Saved by an earlier version of POPPYS; view only.</div></div>` : '',
    s.missing ? `<div class="callout attention">${icon('alert')}<div>${plural(s.missing, 'saved row')} of this order ${s.missing === 1 ? 'is' : 'are'} no longer in Oracle (changed or removed outside POPPYS).</div></div>` : '',
    s.reconciled === false ? `<div class="callout attention">${icon('alert')}<div>The size quantities add up to ${fmt(s.size_total)}, but the total pieces (OQTY) is ${fmt(s.total)}.</div></div>` : '',
    ORD.editing ? `<div class="callout">${icon('info')}<div>Change a box to change it on every row of the order, or change a size row. Nothing is saved until you review and approve the changes.</div></div>` : ''].join('');
  const tables = Object.keys(d.rows || {}).map(t => {
    const items = boxesFor(d, t); const filled = items.filter(x => x.box.count), blank = items.filter(x => !x.box.count);
    const editing = ORD.editing && d.can_update?.[t];
    const box = x => boxHtml(x.col, x.box, editing && x.box.level === 'order' && !x.col.system ? { editing: true, edit: ORD.edits[t]?.[x.col.name] } : {});
    return `<section class="stack tight" data-table="${esc(t)}"><div class="group-head"><h3>${esc(tableLabel(t))} <code style="color:var(--muted)">${esc(t)}</code></h3>
        <p>${plural((d.rows[t] || []).length, 'row')}, ${filled.length} of ${items.length} columns with a value</p></div>
      <div class="boxes">${filled.map(box).join('')}</div>
      ${editing && SIZE_COLS.some(c => (d.columns[t] || []).some(x => x.name === c)) ? sizeEditor(d, t) : ''}
      ${blank.length ? `<details class="quiet"><summary>Blank columns (${blank.length})</summary><div class="boxes" style="margin-top:10px">${blank.map(box).join('')}</div></details>` : ''}</section>`;
  }).join('');
  return `${head}${notes}${tables}${ORD.editing ? `<div class="editbar"><span class="grow" id="o-count-edits">No changes yet.</span>
    <button class="btn" id="o-cancel">Cancel</button><button class="btn primary" id="o-review">${icon('eye')} Review changes</button></div>` : ''}`;
}
function sizeEditor(d, t) {
  const cols = SIZE_COLS.filter(c => (d.columns[t] || []).some(x => x.name === c));
  const rows = (d.rows[t] || []).filter(r => !r._missing);
  return `<div class="card rows-card" style="box-shadow:none"><div class="table-wrap"><table class="data-table"><thead><tr><th>#</th>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>
    ${rows.map((r, i) => `<tr><td class="muted">${i + 1}</td>${cols.map(c => `<td><input data-rid="${esc(r._id)}" data-rcol="${esc(c)}" value="${esc(ORD.rowEdits[t]?.[r._id]?.[c] ?? r[c] ?? '')}" aria-label="${esc(c)} row ${i + 1}"></td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;
}
function countEdits() {
  const n = Object.values(ORD.edits).reduce((a, x) => a + Object.keys(x).length, 0) + Object.values(ORD.rowEdits).reduce((a, x) => a + Object.values(x).reduce((b, y) => b + Object.keys(y).length, 0), 0);
  const el = $('#o-count-edits'); if (el) el.textContent = n ? `${plural(n, 'change')} to review.` : 'No changes yet.';
  return n;
}
function wireDetails(body) {
  $('#o-edit', body)?.addEventListener('click', () => { Object.assign(ORD, { editing: true, edits: {}, rowEdits: {} }); Drawer.show('details'); });
  $('#o-cancel', body)?.addEventListener('click', () => { Object.assign(ORD, { editing: false, edits: {}, rowEdits: {} }); Drawer.show('details'); });
  $('#o-review', body)?.addEventListener('click', reviewChanges);
  $$('[data-edit]', body).forEach(inp => inp.oninput = () => { const t = inp.closest('[data-table]').dataset.table;
    (ORD.edits[t] ||= {})[inp.dataset.edit] = inp.value; inp.closest('.box').classList.add('changed'); countEdits(); });
  $$('[data-rid]', body).forEach(inp => inp.oninput = () => { const t = inp.closest('[data-table]').dataset.table;
    ((ORD.rowEdits[t] ||= {})[inp.dataset.rid] ||= {})[inp.dataset.rcol] = inp.value; countEdits(); });
  $$('.box[data-col]', body).forEach(el => { if (ORD.editing) return; const t = el.closest('[data-table]')?.dataset.table;
    const src = ORD.data?.sources?.columns?.[t]?.[el.dataset.col]; if (src?.cell) el.title = `From Excel ${src.sheet}!${src.cell}${src.value != null ? ' = ' + src.value : ''}`; });
}
async function reviewChanges() {
  const d = ORD.data; const changes = [];
  for (const [t, cols] of Object.entries(ORD.edits)) for (const [column, value] of Object.entries(cols)) changes.push({ table: t, id: 'all', column, value });
  for (const [t, rows] of Object.entries(ORD.rowEdits)) for (const [id, cols] of Object.entries(rows)) for (const [column, value] of Object.entries(cols)) {
    const row = (d.rows[t] || []).find(r => String(r._id) === String(id)); if (row && String(row[column] ?? '') === String(value)) continue;
    changes.push({ table: t, id: row ? row._id : id, column, value });
  }
  if (!changes.length) { toast('Nothing changed yet.'); return; }
  const cs = await busy('Checking your changes against Oracle…', () => api(`/api/orders/${d.ordmasid}/changes`, { method: 'POST', body: { changes } }));
  if (!cs) return;
  const ok = !cs.errors.length;
  const body = Modal.open('Review changes', `${cs.errors.length ? `<div class="callout attention">${icon('alert')}<div><strong>Please correct</strong><ul>${cs.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` : ''}
    ${cs.changes.length ? `<p>${plural(cs.changes.length, 'value')} will change in Oracle. Each row is checked again when you approve; if someone changed it meanwhile, nothing is saved.</p>
      <div class="stack tight">${groupChanges(cs.changes).map(g => `<div class="card card-pad" style="box-shadow:none;padding:12px 14px"><div class="row between"><strong>${esc(g.label)} <code style="color:var(--muted)">${esc(g.column)}</code></strong>
        <span class="muted">${g.table}${g.rows > 1 ? `, ${g.rows} rows` : ''}${g.derived ? ' · follows the sizes' : ''}</span></div>
        <div class="diff" style="margin-top:6px"><span class="old">${esc(fmtCell(g.old) || 'blank')}</span><span>${icon('arrow')}</span><span class="new">${esc(fmtCell(g.new) || 'blank')}</span></div></div>`).join('')}</div>` : ''}
    ${cs.notes?.length ? `<div class="callout info">${icon('info')}<div>${cs.notes.map(esc).join('<br>')}</div></div>` : ''}
    <div class="modal-foot"><button class="btn" data-no>Back</button>${ok ? `<button class="btn primary" data-yes>${icon('check')} Approve changes</button>` : ''}</div>`);
  $('[data-no]', body).onclick = () => Modal.close();
  $('[data-yes]', body)?.addEventListener('click', () => busy('Saving the changes in Oracle and reading them back…', async () => {
    await api(`/api/changes/${cs.id}/approve`, { method: 'POST', body: { hash: cs.hash } });
    Modal.close(); Object.assign(ORD, { editing: false, edits: {}, rowEdits: {}, data: null });
    toast('Changes saved and read back from Oracle.'); Drawer.show('details');
    api('/api/orders').then(list => { ORD.list = list; const el = $('#o-table'); if (el) drawOrders(el); }).catch(() => {});
  }));
}
function groupChanges(changes) {
  const out = new Map();
  for (const c of changes) { const k = `${c.table}|${c.column}|${c.old}|${c.new}`;
    if (!out.has(k)) out.set(k, { table: c.table, column: c.column, label: c.label || c.column, old: c.old, new: c.new, derived: c.derived, rows: 0 }); out.get(k).rows++; }
  return [...out.values()];
}

function rowsTab(d) {
  return Object.entries(d.rows || {}).map(([t, rows]) => {
    const present = rows.filter(r => !r._missing);
    const cols = (d.columns[t] || []).map(c => c.name).filter(c => present.some(r => r[c] != null && r[c] !== '')).slice(0, 14);
    const labels = Object.fromEntries((d.columns[t] || []).map(c => [c.name, c.label]));
    return `<section class="stack tight"><div class="group-head"><h3>${esc(tableLabel(t))} <code style="color:var(--muted)">${esc(t)}</code></h3><p>${plural(rows.length, 'row')} saved by POPPYS</p></div>
      <div class="card" style="box-shadow:none"><div class="table-wrap" style="max-height:520px"><table class="data-table"><thead><tr><th>#</th>${cols.map(c => `<th title="${esc(labels[c] || c)}">${esc(c)}</th>`).join('')}</tr></thead><tbody>
      ${rows.map((r, i) => r._missing ? `<tr><td class="muted">${i + 1}</td><td colspan="${cols.length || 1}"><span class="status-chip red">No longer in Oracle</span></td></tr>`
        : `<tr><td class="muted">${i + 1}</td>${cols.map(c => `<td class="${typeof r[c] === 'number' && !isCode(c) ? 'num' : ''}">${esc(val(c, r[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div></section>`;
  }).join('') || '<div class="empty">No rows.</div>';
}

function historyTab(h, d) {
  const imp = h.import_ || d.import_ || {};
  const sets = new Map();
  for (const c of h.changes || []) { if (!sets.has(c.changeset)) sets.set(c.changeset, []); sets.get(c.changeset).push(c); }
  return `<ul class="history-list">
    ${[...sets.values()].map(list => { const first = list[0]; const cols = new Map();
      for (const c of list) { const k = JSON.stringify([`${c.table}.${c.column}`, c.old, c.new]); cols.set(k, (cols.get(k) || 0) + 1); }
      return `<li><div class="row between"><strong>${icon('edit')} Changed by ${esc(first.approver)}</strong><span class="muted">${when(first.changed_at)}</span></div>
        <div class="stack tight" style="margin-top:8px">${[...cols].map(([k, n]) => { const [col, old, nw] = JSON.parse(k);
          return `<div class="diff"><span><code>${esc(col)}</code>${n > 1 ? ` <span class="muted">(${n} rows)</span>` : ''}</span><span></span><span><span class="old">${esc(old ?? 'blank')}</span> → <span class="new">${esc(nw ?? 'blank')}</span></span></div>`; }).join('')}</div></li>`; }).join('')}
    <li><div class="row between"><strong>${icon('upload')} Saved from ${esc(imp.filename || 'an Excel upload')} by ${esc(imp.approver || '—')}</strong><span class="muted">${when(imp.committed_at)}</span></div>
      <p class="muted" style="margin-top:4px">${plural(Object.values(d.rows || {}).reduce((a, r) => a + r.length, 0), 'row')} added in one transaction and read back from Oracle.${imp.job_id ? ` Import ${esc(String(imp.job_id).slice(0, 8))}.` : ''}</p></li></ul>`;
}
