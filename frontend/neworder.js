'use strict';
/* New Order: Upload → (automatic JSON + mapping + preview) → Review → Approve → Saved. */
const NO = { draft: null, showEmpty: false, showJson: false };
const MASTER_OF = { 'ORDMAS.BUYER': 'BUYMAS', 'ORDMAS.STYLENO': 'STYLENUM', 'ORDMAS.STYLEDESC': 'STYLEMAS', 'ORDMAS.UOM': 'UNITMAS', 'ORDMAS.MERCH': 'MERCHMAS',
  'ORDSTYLE.STNO': 'STYLENUM', 'ORDSTYLE.STYLENAME': 'STYLEMAS', 'ORDSTYLE.COMBO': 'COLORMAS', 'ORDSTYLE.PUOM': 'UNITMAS', 'ORDSIZE.GSIZE': 'SIZE_MAS' };
const TABLE_ORDER = ['ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'BUYMAS', 'STYLENUM', 'STYLEMAS', 'COLORMAS', 'SIZE_MAS', 'UNITMAS', 'MERCHMAS'];
const ORIGIN = { excel: ['From Excel', 'green'], master: ['Linked record', 'blue'], generated: ['New ID', 'blue'], derived: ['Calculated', 'violet'], manual: ['Entered', 'violet'] };

function stepper(active) {
  const steps = ['Upload Excel', 'Review mapping', 'Approve', 'Saved & verified'];
  return `<ol class="stepper">${steps.map((s, i) => `<li class="${i + 1 < active ? 'done' : i + 1 === active ? 'active' : ''}"><b>${i + 1 < active ? '✓' : i + 1}</b><span>${s}</span></li>`).join('')}</ol>`;
}

route('new', async (view, [id]) => {
  setTitle('New Order', 'Upload an Excel order, review it, approve it');
  if (!id) return renderUpload(view);
  if (!NO.draft || NO.draft.id !== id) NO.draft = await api(`/api/drafts/${encodeURIComponent(id)}`);
  // Always show the Oracle preview when a draft is opened; it is rebuilt after every change.
  if (!NO.draft.plan && !NO.draft.result) await preparePreview(true);
  renderReview(view);
});

async function renderUpload(view) {
  view.innerHTML = `${stepper(1)}<div class="split">
    <section class="card card-pad"><label class="dropzone" id="drop" tabindex="0"><span class="big-ico">${icon('upload')}</span>
      <strong>Drop the buyer's Excel order here</strong><span class="muted">or click to choose a file · .xlsx or .xlsm</span>
      <input type="file" id="file" accept=".xlsx,.xlsm" class="visually-hidden"></label>
      <div class="assurances"><span>${icon('shield')}Original file kept unchanged</span><span>${icon('sparkle')}Mapped to Oracle automatically</span><span>${icon('check')}Nothing saved until you approve</span></div></section>
    <section class="card"><div class="card-head"><h2>Continue a previous upload</h2></div><div id="recent"><div class="empty"><span class="spinner"></span></div></div></section></div>`;
  const drop = $('#drop', view), input = $('#file', view);
  input.addEventListener('change', () => upload(input.files[0]));
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  ['dragenter', 'dragover'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { if (e.dataTransfer.files.length !== 1) return toast('Please drop one Excel file at a time.', 'attention'); upload(e.dataTransfer.files[0]); });
  const drafts = (await api('/api/drafts').catch(() => [])).filter(d => !d.committed).slice(0, 8);
  $('#recent', view).innerHTML = drafts.length ? drafts.map(d => `<div class="list-row"><span class="file-ico">XLS</span><div class="grow"><strong>${esc(d.filename)}</strong><small class="muted">${esc(d.format)} · ${when(d.created_at)}</small></div><a class="btn small soft" href="#/new/${esc(d.id)}">Review</a></div>`).join('') : `<div class="empty">No unfinished uploads.</div>`;
}

async function upload(file) {
  if (!file) return;
  if (!/\.(xlsx|xlsm)$/i.test(file.name)) return toast(/\.xls$/i.test(file.name) ? 'This is an old .xls file. Open it in Excel and "Save as" .xlsx, then upload it.' : 'Please choose an Excel .xlsx or .xlsm file.', 'attention');
  await busy('Reading the workbook and mapping it to Oracle…', async () => {
    const form = new FormData(); form.append('file', file);
    NO.draft = await api('/api/uploads', { method: 'POST', body: form });
    await preparePreview(true);
    go(`#/new/${NO.draft.id}`);
  });
}

async function preparePreview(quiet = false) {
  try { NO.draft = await api(`/api/drafts/${NO.draft.id}/preview`, { method: 'POST', body: { version: NO.draft.version } }); }
  catch (e) { if (!quiet) throw e; NO.previewError = e.message; return; }
  NO.previewError = null;
}

async function saveOrders(orders, message = 'Updating the preview…') {
  await busy(message, async () => {
    NO.draft = await api(`/api/drafts/${NO.draft.id}`, { method: 'PUT', body: { version: NO.draft.version, orders } });
    await preparePreview(true); renderReview($('#view'));
  });
}

function masterLabels(plan) {
  const map = {};
  for (const op of plan.operations) if (!['ORDMAS', 'ORDSTYLE', 'ORDSIZE'].includes(op.table)) {
    const [pk, label] = Object.keys(op.values); const id = op.values[pk];
    map[`${op.table}:${id}`] = { label: op.values[label], action: op.action };
  }
  return map;
}
function shown(table, col, value, labels) {
  if (value == null) return '<span class="blank">left blank</span>';
  const master = MASTER_OF[`${table}.${col}`];
  if (value === '$approver') return '<span class="generated">You, when you approve</span>';
  if (value === '$job') return '<span class="generated">Import ID · given when saved</span>';
  if (typeof value === 'string' && value.startsWith('$')) {
    if (master) { const m = labels[`${master}:${value}`]; return `<span class="value-new">${esc(m?.label ?? '')}</span> <span class="badge blue">new ${esc(tableLabel(master).replace(/s$/, '').toLowerCase())}</span>`; }
    return '<span class="generated">New ID · assigned when saved</span>';
  }
  if (master) { const m = labels[`${master}:${value}`]; return `<span class="value-new">${esc(m?.label ?? value)}</span> <span class="faint mono">#${esc(value)}</span>`; }
  return `<span class="value-new">${esc(val(col, value))}</span>`;
}
const originBadge = info => { const o = ORIGIN[info?.origin]; return o ? `<span class="origin">${o[0]}</span>` : ''; };

function renderReview(view) {
  const d = NO.draft; const plan = d.plan;
  if (d.result) return renderResult(view, d.result);
  const orders = d.orders || []; const errors = plan?.errors || []; const ready = plan && !errors.length;
  const status = !plan ? ['Preview not ready', 'amber'] : errors.length ? [`${plural(errors.length, 'item')} to check`, 'amber'] : ['Ready to approve', 'green'];
  const first = orders[0] || {};
  const lines = plan ? plan.totals.lines : orders.reduce((n, o) => n + (o.lines || []).length, 0);
  const sizes = plan ? plan.operations.filter(o => plan.mode === 'table' ? o.values.SIZES != null : o.table === 'ORDSIZE').length : orders.reduce((n, o) => n + (o.lines || []).reduce((m, l) => m + (l.sizes || []).filter(s => s.quantity != null && s.quantity !== '').length, 0), 0);
  view.innerHTML = `${stepper(ready ? 3 : 2)}<div class="stack">
    <section class="card"><div class="review-bar"><div class="file"><span class="file-ico">XLS</span><div><strong>${esc(d.filename)}</strong>
      <small class="muted">${esc(d.format)} · ${plural((d.sheets || []).length, 'sheet')} · uploaded ${when(d.created_at)}</small></div>
      <span class="badge ${status[1]}">${esc(status[0])}</span>${d.template ? `<span class="badge violet" title="Saved mapping reused">${icon('save')} Template ${esc(d.template.name)} v${d.template.version}</span>` : ''}</div>
      <div class="row"><button class="btn" id="edit-map" type="button">${icon('edit')} Edit mapping</button><button class="btn primary" id="approve" type="button" ${ready ? '' : 'disabled'}>${icon('check')} Approve</button></div></div>
      <div class="summary-tiles"><div><span>Order reference</span><strong>${esc(orders.length > 1 ? plural(orders.length, 'order') : first.order_ref || '—')}</strong></div>
        <div><span>Buyer</span><strong>${esc(first.buyer || '—')}</strong></div><div><span>Style</span><strong>${esc(first.style || '—')}</strong></div>
        <div><span>Total quantity</span><strong>${plan ? fmt(plan.totals.pieces) : '—'}</strong></div><div><span>Lines · sizes</span><strong>${fmt(lines)} · ${fmt(sizes)}</strong></div></div></section>
    ${d.template?.missing?.length ? `<div class="callout attention">${icon('alert')}<div><strong>The layout differs from template “${esc(d.template.name)}”.</strong> ${plural(d.template.missing.length, 'mapped cell')} did not match and ${d.template.missing.length === 1 ? 'was' : 'were'} not applied. Check them in Edit mapping.</div></div>` : ''}
    ${NO.previewError ? `<div class="callout info">${icon('info')}<div><strong>The Oracle preview is not available.</strong> ${esc(NO.previewError)} The workbook was read and converted; the preview appears when Oracle is reachable. <button class="btn small" id="retry">Try again</button></div></div>` : ''}
    ${quantityQuestion(orders)}
    ${errors.length ? attention(errors, orders) : ''}
    ${plan?.warnings?.length ? `<div class="callout attention">${icon('info')}<div><strong>Please check</strong><ul>${plan.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div></div>` : ''}
    ${plan?.info?.length ? `<details class="quiet"><summary>Good to know · ${plural(plan.info.length, 'note')} (optional details, nothing to fix)</summary><div class="callout" style="margin-top:8px">${icon('info')}<ul>${plan.info.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div></details>` : ''}
    ${plan ? (plan.mode === 'table' ? singleTable(plan) : oracleTables(plan)) : ''}
    <section class="card"><div class="card-head"><div><h2>Structured JSON from Excel</h2><span class="sub">Exactly what was read, with sheet and cell for every value</span></div><button class="btn small" id="toggle-json" type="button">${NO.showJson ? 'Hide' : 'Show'}</button></div>
      ${NO.showJson ? `<div class="card-pad"><div class="json-view">${esc(JSON.stringify(orders.map(cleanOrder), null, 2))}</div></div>` : ''}</section>
    <div class="action-bar"><span class="muted">${icon('shield')} Nothing is written until you approve. All records are saved together, or none are.</span>
      <div class="row"><button class="btn" id="edit-map2" type="button">${icon('edit')} Edit mapping</button><button class="btn primary large" id="approve2" type="button" ${ready ? '' : 'disabled'}>Approve &amp; save to Oracle ${icon('arrow')}</button></div></div>
  </div>`;
  paintIcons(view);
  $$('#edit-map,#edit-map2', view).forEach(b => b.addEventListener('click', () => Mapper.open(NO.draft, async draft => { NO.draft = draft; await busy('Updating the preview…', async () => { await preparePreview(true); renderReview($('#view')); }); })));
  $$('#approve,#approve2', view).forEach(b => b.addEventListener('click', approve));
  $('#retry', view)?.addEventListener('click', () => busy('Preparing the preview…', async () => { await preparePreview(); renderReview(view); }));
  $('#toggle-json', view).addEventListener('click', () => { NO.showJson = !NO.showJson; renderReview(view); });
  $('#show-empty', view)?.addEventListener('change', e => { NO.showEmpty = e.target.checked; renderReview(view); });
  view.querySelectorAll('[data-basis]').forEach(b => b.addEventListener('click', () => {
    const orders = structuredClone(NO.draft.orders); const o = orders[Number(b.dataset.order)]; o.quantity_basis = b.dataset.basis;
    if (b.dataset.basis === 'sample' && o.sample_lines?.length) o.lines = structuredClone(o.sample_lines);
    if (b.dataset.basis === 'order' && o.order_lines?.length) o.lines = structuredClone(o.order_lines);
    saveOrders(orders);
  }));
  view.querySelectorAll('[data-ppu]').forEach(inp => inp.addEventListener('change', () => { const orders = structuredClone(NO.draft.orders); orders[Number(inp.dataset.ppu)].pieces_per_unit = inp.value === '' ? null : Number(inp.value); saveOrders(orders); }));
  $('#quick-fix', view)?.addEventListener('submit', e => { e.preventDefault(); const orders = structuredClone(NO.draft.orders);
    $$('[data-fix]', e.target).forEach(i => { if (i.value.trim()) orders[Number(i.dataset.order)][i.dataset.fix] = i.value.trim(); }); saveOrders(orders); });
}
const cleanOrder = o => { const c = structuredClone(o); delete c.id; (c.lines || []).forEach(l => { delete l.id; (l.sizes || []).forEach(s => delete s.id); }); delete c.order_lines; delete c.sample_lines; return c; };

function quantityQuestion(orders) {
  const ask = orders.map((o, i) => [o, i]).filter(([o]) => (o.sample_lines?.length && o.order_lines?.length) || !['sample', 'order', 'order_demo'].includes(o.quantity_basis));
  if (!ask.length) return '';
  return `<section class="card card-pad"><div class="row between"><div><h3>Which quantities should be saved?</h3><p class="muted">This workbook has more than one quantity section. Choose one; the other is kept for reference.</p></div></div>
    ${ask.map(([o, i]) => `<div class="row" style="margin-top:12px">${orders.length > 1 ? `<strong>${esc(o.order_ref || o.style || 'Order ' + (i + 1))}</strong>` : ''}
      <div class="seg"><button type="button" data-order="${i}" data-basis="order" class="${['order', 'order_demo'].includes(o.quantity_basis) ? 'active' : ''}">Order quantities</button>
      <button type="button" data-order="${i}" data-basis="sample" class="${o.quantity_basis === 'sample' ? 'active' : ''}">Sample section</button></div>
      <label class="field" style="flex-direction:row;align-items:center;gap:8px">Pieces per unit <span class="faint">(optional)</span><input type="number" min="1" step="1" style="width:90px" data-ppu="${i}" value="${esc(o.pieces_per_unit ?? '')}"></label></div>`).join('')}</section>`;
}

function attention(errors, orders) {
  const missingRef = orders.map((o, i) => [o, i]).filter(([o]) => !String(o.order_ref || '').trim());
  return `<div class="callout attention">${icon('alert')}<div style="flex:1"><strong>Before approving, please check ${errors.length === 1 ? 'this' : 'these'}</strong>
    <ul>${errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul>
    ${missingRef.length ? `<form id="quick-fix" class="row" style="margin-top:10px">${missingRef.map(([o, i]) => `<label class="field">Order reference${orders.length > 1 ? ` · ${esc(o.style || 'order ' + (i + 1))}` : ''}<input data-fix="order_ref" data-order="${i}" placeholder="e.g. PK/26-27/S/1320" required></label>`).join('')}<button class="btn primary small" style="align-self:flex-end">Save</button></form>` : ''}
    <p style="margin-top:8px">Other fixes: use <strong>Edit mapping</strong> to drag the right Excel cell or type the value.</p></div></div>`;
}

function oracleTables(plan) {
  const labels = masterLabels(plan); const groups = new Map();
  plan.operations.forEach(op => { if (!groups.has(op.table)) groups.set(op.table, []); groups.get(op.table).push(op); });
  const tables = [...groups.keys()].sort((a, b) => (TABLE_ORDER.indexOf(a) + 99) % 99 - (TABLE_ORDER.indexOf(b) + 99) % 99);
  const order = tables.filter(t => ['ORDMAS', 'ORDSTYLE', 'ORDSIZE'].includes(t)); const masters = tables.filter(t => !order.includes(t));
  return `<div class="row between"><div><h2>Oracle preview</h2><p class="muted">These are the exact records and columns that will be written. This is a proposal: nothing is in Oracle yet.</p></div>
    <label class="row muted" style="gap:6px"><input type="checkbox" id="show-empty" ${NO.showEmpty ? 'checked' : ''}> Show empty optional columns</label></div>
    ${order.map(t => tableCard(t, groups.get(t), plan, labels)).join('')}
    ${masters.length ? `<section class="card oracle-table"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('link')}</span><div><h3>Linked master records</h3><span class="sub">Buyer, style, colour and size are stored as links to master tables</span></div></div></div>
      <div class="table-wrap"><table><thead><tr><th>Table</th><th>Value</th><th>What happens</th><th>From Excel</th></tr></thead><tbody>${masters.flatMap(t => groups.get(t).map(op => { const [pk, col] = Object.keys(op.values);
        return `<tr><td><span class="col-name">${esc(t)}</span><span class="col-label">${esc(tableLabel(t))}</span></td><td><strong>${esc(op.values[col])}</strong></td><td>${op.action === 'reuse' ? `<span class="badge green">Existing record #${esc(op.values[pk])}</span>` : '<span class="badge blue">New master record</span>'}</td><td>${sourceChip(op.source)}</td></tr>`; })).join('')}</tbody></table></div></section>` : ''}`;
}

function tableCard(table, ops, plan, labels) {
  const meta = plan.tables[table]; const used = [...new Set(ops.flatMap(op => Object.keys(op.values)))];
  const empty = (meta?.columns || []).filter(c => !used.includes(c.name));
  const head = `<div class="card-head"><div class="table-name"><span class="table-ico">${icon('table')}</span><div><span class="tname">${esc(table)}</span> <span class="muted">· ${esc(meta?.label || tableLabel(table))}</span><span class="sub" style="display:block">${esc(meta?.purpose || '')}</span></div></div>
    <span class="badge green">${plural(ops.length, 'new record')}</span></div>`;
  if (ops.length === 1) {
    const op = ops[0];
    return `<section class="card oracle-table">${head}<div class="table-wrap"><table class="kv"><thead><tr><th>Oracle column</th><th>Value to save</th><th>Source</th></tr></thead><tbody>
      ${used.map(col => { const info = op.columns?.[col] || {};
        return `<tr><td><span class="col-name">${esc(col)}</span><span class="col-label">${esc(COLUMN_LABELS[col] || '')}</span></td><td>${shown(table, col, op.values[col], labels)}${info.raw != null && String(info.raw) !== String(op.values[col]) ? `<span class="sub">Excel: ${esc(info.raw)}</span>` : ''}${info.note ? `<span class="sub">${esc(info.note)}</span>` : ''}</td><td>${sourceChip(info.source) || originBadge(info)}</td></tr>`; }).join('')}
      ${NO.showEmpty ? empty.map(c => `<tr><td><span class="col-name">${esc(c.name)}</span><span class="col-label">${esc(c.type)}${c.required ? ' · required' : ' · optional'}</span></td><td><span class="blank">left blank</span></td><td></td></tr>`).join('') : ''}
      </tbody></table></div>${!NO.showEmpty && empty.length ? `<p class="faint card-pad" style="padding-top:8px;padding-bottom:12px">${plural(empty.length, 'other optional column')} stay blank (NULL). Tick “Show empty optional columns” to list them.</p>` : ''}</section>`;
  }
  return `<section class="card oracle-table">${head}<div class="table-wrap" style="max-height:440px"><table><thead><tr>${used.map(c => `<th title="${esc(COLUMN_LABELS[c] || '')}">${esc(c)}</th>`).join('')}</tr></thead><tbody>
    ${ops.map(op => `<tr>${used.map(col => { const info = op.columns?.[col] || {}; return `<td>${shown(table, col, op.values[col], labels)}${info.source?.cell ? `<span class="sub">${sourceChip(info.source)}</span>` : ''}</td>`; }).join('')}</tr>`).join('')}
    </tbody></table></div>${empty.length ? `<p class="faint card-pad" style="padding-top:8px;padding-bottom:12px">${plural(empty.length, 'other optional column')} stay blank.</p>` : ''}</section>`;
}

async function approve() {
  const d = NO.draft; if (!d.plan || d.plan.errors.length) return;
  const ok = await confirmDialog('Approve and save to Oracle?', `${plural(d.plan.operations.filter(o => o.action === 'insert').length, 'record')} will be written to Oracle in one transaction and then read back to verify them.`, 'Approve & save');
  if (!ok) return;
  await busy('Saving to Oracle and verifying…', async () => {
    try { const result = await api(`/api/drafts/${d.id}/accept`, { method: 'POST', body: { version: d.version, plan_hash: d.plan.hash } }); NO.draft = await api(`/api/drafts/${d.id}`); NO.draft.result ||= result; }
    catch (e) { NO.draft = await api(`/api/drafts/${d.id}`).catch(() => d); if (!NO.draft.result) { renderReview($('#view')); throw new Error(e.message + '\nNo partial order was saved.'); } }
    renderResult($('#view'), NO.draft.result);
  });
}

function renderResult(view, result) {
  const verified = result.verified !== false;
  view.innerHTML = `${stepper(5)}<section class="card result-card"><span class="success-mark">${icon('check')}</span>
    <h2>${result.already_committed ? 'This order was already saved' : 'Order saved to Oracle'}</h2>
    <p class="muted">${verified ? `${plural(result.rows.length, 'record')} written in one transaction and read back from Oracle — values match.` : 'Saved. Some values could not be re-read; open the order to check.'}</p>
    <div class="row" style="justify-content:center">${result.orders.map(o => `<span class="badge green">${esc(o.order_ref)} · ORDMASID ${esc(o.ordmasid)}</span>`).join('')}</div>
    <div class="row" style="justify-content:center;margin-top:8px">${result.orders.slice(0, 1).map(o => `<a class="btn primary" href="#/orders/${o.ordmasid}">${icon('eye')} View order</a><a class="btn" href="#/database/flow/${o.ordmasid}">${icon('flow')} Database flow</a>`).join('')}
      <a class="btn" href="#/new">${icon('plus')} New order</a></div></section>
    <section class="card" style="margin-top:16px"><div class="card-head"><h2>Saved records</h2><span class="sub">${when(result.committed_at)}</span></div>
      <div class="table-wrap" style="max-height:420px"><table><thead><tr><th>Table</th><th>ID</th><th>Saved values</th></tr></thead><tbody>${result.rows.map(r => `<tr><td><span class="col-name">${esc(r.table)}</span><span class="col-label">${esc(tableLabel(r.table))}</span></td><td class="mono">${esc(r.id ?? '')}</td><td>${Object.entries(r.values).map(([k, v]) => `<span class="mono faint">${esc(k)}</span> ${esc(val(k, v))}`).join(' · ')}</td></tr>`).join('')}</tbody></table></div></section>`;
  paintIcons(view);
}

/* Single-table mode: one Oracle table, one row per size. Order details are shown once ("same in every row"). */
const ROW_COLS = ['ORDSTYLEROW', 'GARMENT', 'COMBO', 'SIZES', 'ORDERQTY', 'QTYTYPE', 'SOURCE_CELL'];
const AUTO_COLS = ['ENTRY_ID', 'ORDER_KEY', 'IMPORT_ID', 'SOURCE_FILE', 'CREATEDBY', 'CREATEDON'];
function singleTable(plan) {
  const meta = plan.tables[plan.table]; const byOrder = new Map();
  plan.operations.forEach(op => { if (!byOrder.has(op.order)) byOrder.set(op.order, []); byOrder.get(op.order).push(op); });
  const level = Object.fromEntries(meta.columns.map(c => [c.name, c]));
  return `<div class="row between"><div><h2>Oracle preview</h2><p class="muted">Everything goes into <strong>one</strong> Oracle table. This is a proposal: nothing is in Oracle until you approve.</p></div>
    <label class="row muted" style="gap:6px"><input type="checkbox" id="show-empty" ${NO.showEmpty ? 'checked' : ''}> Show every Oracle column</label></div>
    ${[...byOrder.values()].map(ops => {
      const first = ops[0]; const orderCols = meta.columns.filter(c => c.level === 'order');
      const filled = orderCols.filter(c => first.values[c.name] != null); const blanks = orderCols.filter(c => first.values[c.name] == null);
      const total = ops.reduce((n, op) => n + num(op.values.ORDERQTY), 0);
      return `<section class="card oracle-table"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('table')}</span><div><span class="tname">${esc(plan.table)}</span> <span class="muted">· ${esc(meta.label)}</span><span class="sub" style="display:block">${esc(meta.purpose)}</span></div></div>
        <span class="badge green">${plural(ops.length, 'new row')}</span></div>
        <div class="card-pad" style="padding-bottom:6px"><h3>Order details <span class="muted" style="font-weight:500">· same in every row</span></h3></div>
        <div class="table-wrap"><table class="kv"><thead><tr><th>Oracle column</th><th>Value to save</th><th>From Excel</th></tr></thead><tbody>
        ${filled.map(c => { const info = first.columns[c.name] || {}; return `<tr><td><span class="col-name">${esc(c.name)}</span><span class="col-label">${esc(c.label)}</span></td>
          <td><span class="value-new">${esc(val(c.name, first.values[c.name]))}</span>${info.raw != null && String(info.raw) !== String(first.values[c.name]) ? `<span class="sub">Excel: ${esc(info.raw)}</span>` : ''}</td>
          <td>${sourceChip(info.source) || originBadge(info)}${info.source?.via && info.source.via.startsWith('Excel label') ? `<span class="sub">${esc(info.source.via)}</span>` : ''}</td></tr>`; }).join('')}</tbody></table></div>
        ${blanks.length ? `<p class="card-pad" style="padding-top:10px;padding-bottom:4px"><span class="muted">Not in this Excel — left blank (that's fine):</span> ${blanks.map(c => `<span class="badge">${esc(c.label)}</span>`).join(' ')}</p>` : ''}
        <div class="card-pad" style="padding-bottom:6px"><h3>Rows <span class="muted" style="font-weight:500">· one per size · total ${fmt(total)} pieces</span></h3></div>
        <div class="table-wrap" style="max-height:460px"><table><thead><tr><th>#</th>${(NO.showEmpty ? meta.columns.map(c => c.name).filter(n => !ROW_COLS.includes(n)) : []).concat(ROW_COLS).map(n => `<th title="${esc(level[n]?.label || '')}">${esc(n)}<span class="col-label" style="text-transform:none;letter-spacing:0">${esc(level[n]?.label || '')}</span></th>`).join('')}</tr></thead><tbody>
          ${ops.map((op, i) => `<tr><td class="faint">${i + 1}</td>${(NO.showEmpty ? meta.columns.map(c => c.name).filter(n => !ROW_COLS.includes(n)) : []).concat(ROW_COLS).map(n => `<td>${op.values[n] == null ? '<span class="blank">—</span>' : AUTO_COLS.includes(n) ? shown(plan.table, n, op.values[n], {}) : esc(val(n, op.values[n]))}</td>`).join('')}</tr>`).join('')}
        </tbody></table></div>
        <p class="faint card-pad" style="padding-top:8px;padding-bottom:12px">${AUTO_COLS.join(', ')} are filled automatically when you approve (row ID, order ID, import, file, who approved, date).</p></section>`; }).join('')}`;
}
