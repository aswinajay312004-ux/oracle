'use strict';
/* New order: drop the buyer's Excel -> it is read, kept as JSON and matched to the Oracle columns -> preview ->
   compare with Excel / edit -> Save & Process (approval) -> saved and read back from Oracle. */
const NO = { draft: null, tab: 'preview', filter: 'all', q: '', editing: false, edits: {}, rowEdits: {}, open: {}, flowTable: null };
const STAGES = [['read', 'Reading your Excel file'], ['identify', 'Identifying order information'], ['match', 'Matching Oracle fields'], ['preview', 'Preparing your order preview']];

route('new', async (view, [id]) => {
  if (!id) { NO.draft = null; return uploadPage(view); }
  if (!NO.draft || NO.draft.id !== id) { NO.draft = await api(`/api/drafts/${encodeURIComponent(id)}`); Object.assign(NO, { tab: 'preview', editing: false, edits: {}, rowEdits: {}, open: {}, flowTable: null, filter: 'all', q: '' }); }
  if (NO.draft.result) return resultPage(view, NO.draft.result);
  previewPage(view);
});

/* ------------------------------------------------------------------ upload */
async function uploadPage(view) {
  setTitle('New order', 'Upload the buyer’s Excel. POPPYS reads every cell, fills the Oracle columns and shows you the order. Nothing is saved until you approve it.');
  const [drafts, samples] = await Promise.all([api('/api/drafts').catch(() => []), api('/api/samples').catch(() => [])]);
  const waiting = drafts.filter(d => !d.committed).slice(0, 6);
  page(view, `<div class="steps">${stepsHtml(1)}</div>
    <div class="upload-grid">
      <section class="card card-pad stack">
        <label class="dropzone" id="drop" tabindex="0"><span class="xls">${icon('xls')}</span><strong>Upload buyer order</strong>
          <span class="muted">Drag and drop your Excel file or browse to upload.</span><small>.xlsx or .xlsm, up to 12 MB. The file stays on this computer.</small>
          <input type="file" id="file" accept=".xlsx,.xlsm" class="visually-hidden"></label>
        <div id="progress" hidden></div>
      </section>
      <div class="stack">
        <section class="card"><div class="card-title"><span class="tile-ico amber">${icon('clock')}</span><div><h2>Waiting for review</h2><p>Uploads not saved to Oracle yet</p></div></div>
          ${waiting.length ? `<ul class="pending-list">${waiting.map(d => `<li><a href="#/new/${esc(d.id)}"><strong>${esc(d.filename)}</strong><small>${when(d.created_at)}</small><span class="status-chip amber">Review</span></a></li>`).join('')}</ul>`
            : '<p class="muted card-pad" style="padding-top:0">Nothing waiting. Upload a buyer Excel to start.</p>'}</section>
        <section class="card"><div class="card-title"><span class="tile-ico">${icon('checkcircle')}</span><div><h2>What happens</h2></div></div>
          <ol class="how-list"><li><span><strong>Upload</strong>POPPYS keeps the original file and converts every cell to JSON.</span></li>
            <li><span><strong>Check</strong>Every ORDERENTRY column is shown as a box, filled from Excel. Compare it with the Excel side by side.</span></li>
            <li><span><strong>Save &amp; Process</strong>After your approval the rows are saved in one transaction and read back from Oracle.</span></li></ol></section>
        ${samples.length ? `<section class="card card-pad stack tight"><h3>No file at hand?</h3><p class="muted">Download a sample order with fictional data, then upload it.</p>
          ${samples.map(s => `<a class="btn small" href="/api/samples/${encodeURIComponent(s.name)}" download>${icon('xls')} ${esc(s.name.replace(/_/g, ' ').replace('.xlsx', ''))}</a>`).join('')}</section>` : ''}
      </div></div>`);
  const drop = $('#drop', view), input = $('#file', view);
  input.onchange = () => input.files[0] && upload(input.files[0], view);
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) upload(f, view); });
}
function stepsHtml(active) {
  return [['Upload', 1], ['Check the order', 2], ['Save & Process', 3]].map(([t, n], i) => `${i ? `<span class="bar ${n <= active ? 'done' : ''}"></span>` : ''}<span class="s ${n < active ? 'done' : n === active ? 'active' : ''}"><b>${n < active ? '✓' : n}</b>${t}</span>`).join('');
}
function upload(file, view) {
  const box = $('#progress', view); box.hidden = false;
  const state = Object.fromEntries(STAGES.map(([k]) => [k, { status: 'wait', detail: '' }]));
  const draw = (sent, error) => {
    box.innerHTML = `<div class="row between"><div><strong>${esc(file.name)}</strong> <span class="muted">${fileSize(file.size)}</span></div><span class="muted">${sent < 100 ? `Uploading ${sent}%` : ''}</span></div>
      <ul class="proc">${STAGES.map(([k, label]) => { const s = state[k];
        return `<li class="${s.status === 'done' || s.status === 'warn' ? 'done' : s.status === 'start' ? 'on' : ''}"><span class="mark">${s.status === 'done' ? icon('check') : s.status === 'start' ? '<span class="spinner small"></span>' : s.status === 'warn' ? icon('alert') : ''}</span>
          <span>${label}${s.detail ? `<small class="muted" style="display:block;font-weight:500">${esc(s.detail)}</small>` : ''}</span></li>`; }).join('')}
        <li class="${state.preview.status === 'done' ? 'done' : ''}"><span class="mark">${state.preview.status === 'done' ? icon('check') : ''}</span><span>Ready for review</span></li></ul>
      ${error ? `<div class="callout attention">${icon('alert')}<div>${esc(error)}</div></div>` : ''}`;
    paintIcons(box);
  };
  draw(0);
  const xhr = new XMLHttpRequest(); let seen = 0, sent = 0, done = null, failed = null;
  xhr.open('POST', '/api/uploads?stream=1'); xhr.withCredentials = true;
  xhr.upload.onprogress = e => { if (e.lengthComputable) { sent = Math.round(e.loaded / e.total * 100); draw(sent); } };
  const read = () => {
    const lines = xhr.responseText.slice(seen).split('\n'); const complete = lines.pop(); seen = xhr.responseText.length - complete.length;
    for (const line of lines) { if (!line.trim()) continue; let m; try { m = JSON.parse(line); } catch (_) { continue; }
      if (m.error) failed = m.error; else if (m.done) done = m; else if (state[m.stage]) state[m.stage] = { status: m.status, detail: m.detail || state[m.stage].detail }; }
    draw(100, failed);
  };
  xhr.onprogress = read;
  xhr.onload = () => { read();
    if (xhr.status >= 400) { let msg = 'The file could not be uploaded.'; try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (_) { /* plain text */ } draw(100, msg); return; }
    if (done) { NO.draft = null; refreshBell?.(); setTimeout(() => go(`#/new/${done.id}`), 450); } };
  xhr.onerror = () => draw(sent, 'The connection to POPPYS was interrupted. Upload the file again.');
  const form = new FormData(); form.append('file', file); xhr.send(form);
}

/* ------------------------------------------------------------------ preview */
const mainTable = d => d.plan?.table || 'ORDERENTRY';
const viewOf = (d, t) => d.plan?.orders?.[0]?.tables?.[t];
const colsOf = (d, t) => d.plan?.tables?.[t]?.columns || [];

function previewPage(view) {
  const d = NO.draft, plan = d.plan, v = plan?.orders?.[0] || {};
  const title = v.ref ? `Order ${v.ref}` : d.filename;
  setTitle(title, '', `<a class="btn" href="/api/drafts/${esc(d.id)}/file">${icon('xls')} Open Excel</a><a class="btn" href="#/new">${icon('upload')} Upload another</a>`);
  const facts = [['Buyer', v.buyer], ['Order number', v.ref], ['Style', v.style], ['Order quantity', v.pieces != null ? `${fmt(v.pieces)} pieces` : null],
    ['Order date', boxValueOf(d, 'ORDERDATE')], ['Ship date', boxValueOf(d, 'SHIPDT')], ['Delivery date', boxValueOf(d, 'DELDATE')]].filter(([, x]) => x != null && x !== '');
  const counts = d.document?.counts;
  page(view, `<div class="steps">${stepsHtml(2)}</div>
    <div class="facts" style="margin:-4px 0 14px">${facts.map(([k, x]) => `<div><span>${k}</span><strong>${esc(x)}</strong></div>`).join('')}
      <div><span>Excel file</span><strong>${esc(d.filename)}</strong></div></div>
    ${!plan ? `<div class="callout info">${icon('info')}<div>${esc(d.plan_error || 'The preview is being prepared.')} <button class="btn small" id="refresh">${icon('undo')} Refresh</button></div></div>` : ''}
    ${plan ? statusLine(d, counts) : ''}
    <div class="row between" style="margin:12px 0"><div class="seg" role="tablist">${[['preview', 'Order preview'], ['mapping', 'Data mapping'], ['json', 'Excel as JSON']].map(([k, l]) =>
      `<button type="button" role="tab" data-tab="${k}" class="${NO.tab === k ? 'active' : ''}" aria-selected="${NO.tab === k}">${l}</button>`).join('')}</div>
      ${plan && NO.tab === 'preview' ? toolbarHtml(d) : ''}</div>
    <div id="tabbody"></div>
    ${plan ? actionBar(d) : ''}`);
  $('#refresh', view)?.addEventListener('click', () => busy('Building the preview…', async () => { NO.draft = await api(`/api/drafts/${d.id}/preview`, { method: 'POST', body: { version: d.version } }); previewPage(view); }));
  $$('[data-tab]', view).forEach(b => b.onclick = () => { NO.tab = b.dataset.tab; previewPage(view); });
  const body = $('#tabbody', view);
  if (NO.tab === 'mapping') renderFlow(body, d);
  else if (NO.tab === 'json') renderJson(body, d);
  else renderPreview(body, d);
  wireToolbar(view, d); wireActions(view, d);
  if (plan && d.agent?.ai?.status === 'pending' && !NO.aiStarted) { NO.aiStarted = d.id; runAgentAI(view); }
}
function boxValueOf(d, col) { const b = viewOf(d, mainTable(d))?.boxes?.[col]; return b && b.level === 'order' ? b.value : null; }
function statusLine(d, counts) {
  const plan = d.plan, t = plan.totals; const ai = d.agent?.ai;
  const tables = Object.entries(t.tables || {}).filter(([, n]) => n).map(([k, n]) => `${n} row${n === 1 ? '' : 's'} into ${k}`).join(', ');
  return `<div class="callout ${plan.errors.length ? 'attention' : 'success'}">${icon(plan.errors.length ? 'alert' : 'checkcircle')}<div>
    ${plan.errors.length ? `<strong>${plural(plan.errors.length, 'thing')} to fix before saving.</strong><ul>${plan.errors.slice(0, 6).map(e => `<li>${esc(e)}</li>`).join('')}</ul>`
      : `<strong>Mapped automatically.</strong> ${t.filled} of ${t.columns} ${esc(plan.table)} columns are filled from this Excel; ${tables || 'no rows'}.`}
    ${counts ? ` All ${fmt(counts.cells)} Excel cells are kept in the JSON${counts.missing ? ` (${counts.missing} could not be placed)` : ''}.` : ''}
    ${ai?.status === 'pending' ? ' <span class="muted">Local AI is checking the cells no rule could place…</span>' : ai?.detail ? ` <span class="muted">${esc(ai.detail.replace(/\.$/, ''))}.</span>` : ''}
    ${plan.warnings.length ? `<details class="quiet" style="margin-top:6px"><summary>${plural(plan.warnings.length, 'note')} to check</summary><ul>${plan.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : ''}</div></div>`;
}
function toolbarHtml(d) {
  const b = Object.values(viewOf(d, mainTable(d))?.boxes || {});
  const n = { all: b.length, filled: b.filter(x => x.count).length, blank: b.filter(x => !x.count).length, check: b.filter(x => ['ai', 'error', 'typed'].includes(x.state)).length };
  return `<div class="toolbar"><div class="search">${icon('search')}<input id="q" placeholder="Find a column or value" value="${esc(NO.q)}" aria-label="Find a column or value"></div>
    <div class="seg">${[['all', 'All'], ['filled', 'Filled'], ['blank', 'Blank'], ['check', 'To check']].map(([k, l]) => `<button type="button" data-filter="${k}" class="${NO.filter === k ? 'active' : ''}">${l}<b>${n[k]}</b></button>`).join('')}</div></div>`;
}
function wireToolbar(view, d) {
  $('#q', view)?.addEventListener('input', e => { NO.q = e.target.value; renderPreview($('#tabbody', view), d); });
  $$('[data-filter]', view).forEach(b => b.onclick = () => { NO.filter = b.dataset.filter; previewPage(view); });
}
function visible(col, b) {
  const q = NO.q.trim().toLowerCase();
  if (q && !`${col.name} ${col.label} ${b.value ?? ''} ${b.values?.join(' ')} ${b.excel_label ?? ''}`.toLowerCase().includes(q)) return false;
  if (NO.filter === 'filled') return !!b.count; if (NO.filter === 'blank') return !b.count; if (NO.filter === 'check') return ['ai', 'error', 'typed'].includes(b.state);
  return true;
}
function renderPreview(body, d) {
  const plan = d.plan; if (!plan) { body.innerHTML = ''; return; }
  const tables = Object.entries(plan.tables);
  body.innerHTML = `<div class="stack">${tables.map(([t, info], i) => tableSection(d, t, info, i === 0)).join('')}${preservedCard(d)}</div>`;
  paintIcons(body); wirePreview(body, d);
}
function tableSection(d, t, info, main) {
  const v = viewOf(d, t) || { boxes: {}, rows: 0 }; const cols = colsOf(d, t); const open = main || NO.open[t];
  const shown = cols.filter(c => visible(c, v.boxes[c.name] || {}));
  const fixed = shown.filter(c => (v.boxes[c.name] || {}).level !== 'row'), perRow = shown.filter(c => (v.boxes[c.name] || {}).level === 'row');
  const filled = fixed.filter(c => v.boxes[c.name].count), blank = fixed.filter(c => !v.boxes[c.name].count);
  const target = (d.targets || []).find(x => x.table === t) || {};
  const box = c => boxHtml(c, v.boxes[c.name] || {}, { editing: NO.editing, edit: NO.edits[t]?.[c.name] });
  return `<section class="card" data-table="${esc(t)}">
    <div class="card-title" style="padding-bottom:12px"><span class="tile-ico ${main ? '' : 'blue'}">${icon(main ? 'table' : 'layers')}</span>
      <div class="grow"><h2>${esc(tableLabel(t))} <code style="font-size:13px;color:var(--muted);font-weight:600">${esc(t)}</code></h2>
        <p>${main ? 'Main order table. ' : ''}${esc(info.rows_label)}${info.section ? ` from the Excel table “${esc(info.section.title)}”` : ''}: <strong>${fmt(v.rows)} row${v.rows === 1 ? '' : 's'}</strong> will be added.
          ${v.filled} of ${cols.length} columns filled.${target.auto ? ` <span class="badge blue" title="${esc(target.why || '')}">${icon('sparkle')} Added automatically</span>` : ''}</p></div>
      ${main ? '' : `<button class="btn small" data-toggle="${esc(t)}">${open ? 'Hide columns' : 'Show columns'}</button>`}</div>
    ${open ? `<div class="card-pad stack" style="padding-top:0">
      ${filled.length ? `<div class="group"><div class="group-head"><h3>Filled from Excel</h3><p>${filled.length} column${filled.length === 1 ? '' : 's'}, same value on every row</p></div><div class="boxes">${filled.map(box).join('')}</div></div>` : ''}
      ${perRow.length ? `<div class="group"><div class="group-head"><h3>${t === mainTable(d) ? 'Size rows' : 'Row values'}</h3><p>A different value on each of the ${fmt(v.rows)} rows</p></div><div class="boxes">${perRow.map(box).join('')}</div>${rowsTable(d, t, v, perRow)}</div>` : ''}
      ${blank.length ? `<div class="group"><div class="group-head"><h3>Left blank</h3><p>${blank.length} column${blank.length === 1 ? ' is' : 's are'} not in this Excel. They stay empty in Oracle; nothing is invented.</p></div><div class="boxes">${blank.map(box).join('')}</div></div>` : ''}
      ${!shown.length ? '<div class="empty">No column matches the filter.</div>' : ''}</div>` : ''}</section>`;
}
function rowsTable(d, t, v, perRow) {
  if (!v.rows) return '';
  const keys = v.row_keys, vals = v.row_values || [], issues = (d.plan.issues || []).filter(i => i.table === t && i.row);
  const cols = perRow.slice(0, 12); const limit = Math.min(keys.length, NO.allRows === t ? keys.length : 60);
  const qty = cols.find(c => (v.boxes[c.name].binding || {}).field === 'quantity');
  return `<div class="card rows-card" style="box-shadow:none"><div class="table-wrap"><table class="data-table"><thead><tr><th>#</th>${cols.map(c => `<th title="${esc(c.label)}">${esc(c.name)}</th>`).join('')}</tr></thead><tbody>
    ${keys.slice(0, limit).map((k, i) => `<tr><td class="muted">${i + 1}</td>${cols.map(c => { const val = NO.rowEdits[t]?.[k]?.[c.name] ?? vals[i]?.[c.name]; const bad = issues.some(x => x.row === k && x.column === c.name);
      return `<td class="${typeof val === 'number' ? 'num' : ''}${bad ? ' err' : ''}">${NO.editing ? `<input data-row="${esc(k)}" data-rcol="${esc(c.name)}" value="${esc(val ?? '')}" aria-label="${esc(c.label)} row ${i + 1}">` : esc(val == null ? '' : isCode(c.name) ? String(val) : fmt(val))}</td>`; }).join('')}</tr>`).join('')}</tbody>
    ${qty ? `<tfoot><tr><td></td>${cols.map(c => `<td class="num">${c === qty ? fmt(v.boxes[c.name].total) : ''}</td>`).join('')}</tr></tfoot>` : ''}</table></div>
    ${keys.length > limit ? `<div class="card-pad" style="padding:10px 14px"><button class="btn small" data-allrows="${esc(t)}">Show all ${keys.length} rows</button></div>` : ''}</div>`;
}
function preservedCard(d) {
  const plan = d.plan, doc = d.document; if (!doc) return '';
  const linked = new Set(Object.values(plan.tables).map(x => x.section?.id).filter(Boolean));
  const sizeBlocks = doc.tables.filter(t => t.kind === 'order');
  const others = doc.tables.filter(t => t.kind !== 'order' && !linked.has(t.id));
  const unused = plan.unused || [];
  if (!others.length && !unused.length) return '';
  return `<section class="card"><div class="card-title"><span class="tile-ico violet">${icon('shield')}</span><div><h2>Preserved — not mapped to Oracle</h2>
      <p>Kept with this upload in the JSON (nothing is lost). They are not written to ${esc(plan.table)} because it has no column for them.</p></div></div>
    <div class="card-pad stack" style="padding-top:0">
      ${unused.length ? `<div class="group"><div class="group-head"><h3>Order details without a column</h3></div><div class="boxes">${unused.map(u => `<div class="box s-blank" data-cellref="${esc(u.sheet)}!${esc(u.cell)}" tabindex="0" role="button">
        <div class="top"><span class="lab">${esc(u.label)}</span><span class="code">${esc(u.cell)}</span></div><span class="val" style="color:var(--ink)">${esc(fmtCell(u.value))}</span>
        <div class="src"><span class="pin"></span><span>Preserved — not mapped</span></div></div>`).join('')}</div></div>` : ''}
      ${others.length ? `<div class="group"><div class="group-head"><h3>Excel tables kept for reference</h3><p>${others.length} table${others.length === 1 ? '' : 's'}${sizeBlocks.length ? `; the size blocks fill the size rows above` : ''}</p></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Excel table</th><th>Kind</th><th class="num">Rows</th><th>Columns</th></tr></thead><tbody>
        ${others.map(t => `<tr><td><strong>${esc(t.title)}</strong>${t.note ? `<span class="sub">${esc(t.note)}</span>` : ''}</td><td><span class="badge" style="border-color:${groupColor(t.kind === 'embroidery' ? 'printing' : t.kind === 'costing' ? 'commercial' : t.kind)}">${esc(t.kind)}</span></td>
          <td class="num">${t.rows.filter(r => !r.total).length}</td><td class="muted">${esc(t.columns.slice(0, 6).join(', '))}${t.columns.length > 6 ? '…' : ''}</td></tr>`).join('')}</tbody></table></div></div>` : ''}</div></section>`;
}
function wirePreview(body, d) {
  $$('[data-toggle]', body).forEach(b => b.onclick = () => { NO.open[b.dataset.toggle] = !NO.open[b.dataset.toggle]; renderPreview(body, d); });
  $$('[data-allrows]', body).forEach(b => b.onclick = () => { NO.allRows = b.dataset.allrows; renderPreview(body, d); });
  $$('.box[data-col]', body).forEach(el => {
    const t = el.closest('[data-table]').dataset.table;
    if (NO.editing) return;
    const open = () => openCompare(t, el.dataset.col); el.onclick = open; el.onkeydown = e => { if (e.key === 'Enter') open(); };
  });
  $$('[data-cellref]', body).forEach(el => { el.onclick = () => openCompare(null, null, el.dataset.cellref); });
  if (NO.editing) {
    $$('[data-edit]', body).forEach(inp => inp.oninput = () => { const t = inp.closest('[data-table]').dataset.table; (NO.edits[t] ||= {})[inp.dataset.edit] = inp.value; inp.closest('.box').classList.add('changed'); updateEditBar(); });
    $$('[data-row]', body).forEach(inp => inp.oninput = () => { const t = inp.closest('[data-table]').dataset.table; ((NO.rowEdits[t] ||= {})[inp.dataset.row] ||= {})[inp.dataset.rcol] = inp.value; updateEditBar(); });
  }
}

/* ------------------------------------------------------------------ actions: Compare / Edit / Save & Process */
function actionBar(d) {
  const errors = d.plan.errors.length;
  if (NO.editing) return `<div class="editbar" id="actionbar"><span class="grow" id="editcount">Edit the values in the boxes and size rows, then save your changes.</span>
    <button class="btn" id="edit-cancel">Cancel</button><button class="btn primary" id="edit-save">${icon('save')} Save changes</button></div>`;
  return `<div class="actionbar" id="actionbar"><span class="grow">${errors ? `<strong>${plural(errors, 'thing')} to fix</strong> <span class="muted">before saving</span>` : `<strong>Ready to save.</strong> <span class="muted">${fmt(d.plan.totals.rows)} rows into ${Object.keys(d.plan.tables).length} table${Object.keys(d.plan.tables).length === 1 ? '' : 's'}</span>`}</span>
    <button class="btn" id="act-compare">${icon('eye')} Compare with Excel</button><button class="btn" id="act-edit">${icon('edit')} Edit</button>
    <button class="btn primary large" id="act-save" ${errors ? 'disabled' : ''}>${icon('database')} Save &amp; Process</button></div>`;
}
function updateEditBar() {
  const n = Object.values(NO.edits).reduce((a, x) => a + Object.keys(x).length, 0) + Object.values(NO.rowEdits).reduce((a, x) => a + Object.values(x).reduce((b, y) => b + Object.keys(y).length, 0), 0);
  const el = $('#editcount'); if (el) el.textContent = n ? `${n} change${n === 1 ? '' : 's'} not saved yet. Your original Excel is not changed.` : 'Edit the values in the boxes and size rows, then save your changes.';
}
function wireActions(view, d) {
  $('#act-compare', view)?.addEventListener('click', () => openCompare(mainTable(d)));
  $('#act-edit', view)?.addEventListener('click', () => { NO.editing = true; NO.edits = {}; NO.rowEdits = {}; NO.tab = 'preview'; NO.filter = 'all'; previewPage(view); });
  $('#edit-cancel', view)?.addEventListener('click', () => { NO.editing = false; NO.edits = {}; NO.rowEdits = {}; previewPage(view); });
  $('#edit-save', view)?.addEventListener('click', () => saveEdits(view));
  $('#act-save', view)?.addEventListener('click', () => saveAndProcess(view));
}
async function saveEdits(view) {
  await busy('Saving your changes and rebuilding the preview…', async () => {
    let d = NO.draft;
    for (const t of new Set([...Object.keys(NO.edits), ...Object.keys(NO.rowEdits)])) {
      const rows = Object.entries(NO.rowEdits[t] || {}).flatMap(([row, cols]) => Object.entries(cols).map(([column, value]) => ({ row, column, value })));
      d = await api(`/api/drafts/${d.id}/values`, { method: 'POST', body: { version: d.version, table: t, values: NO.edits[t] || {}, rows } });
    }
    NO.draft = d; NO.editing = false; NO.edits = {}; NO.rowEdits = {}; previewPage(view); toast('Changes saved in the preview. The original Excel file is unchanged.');
  });
}
function saveAndProcess(view) {
  const d = NO.draft, plan = d.plan; const tables = Object.entries(plan.totals.tables || {});
  const fields = Object.values(plan.orders[0]?.tables || {}).reduce((a, v) => a + Object.values(v.boxes).filter(b => b.count).length, 0);
  const body = Modal.open('Save & Process', `<p>Please check what will be written to Oracle (${esc(plan.table === 'ORDERENTRY' ? 'GARMENT_DEMO' : 'Oracle')}). Everything is saved in one transaction: either all of it or nothing.</p>
    <div class="table-wrap card" style="box-shadow:none"><table class="data-table"><thead><tr><th>Oracle table</th><th>Action</th><th class="num">Rows</th><th class="num">Columns with values</th></tr></thead><tbody>
      ${tables.map(([t, n]) => `<tr><td><strong>${esc(t)}</strong><span class="sub">${esc(tableLabel(t))}</span></td><td>Add new rows</td><td class="num">${fmt(n)}</td><td class="num">${Object.values(viewOf(d, t)?.boxes || {}).filter(b => b.count).length}</td></tr>`).join('')}</tbody></table></div>
    <ul class="checks"><li><span class="ok">${icon('check')}</span><span>Order <strong>${esc(plan.orders[0]?.ref || '(no OC number)')}</strong>${plan.orders[0]?.buyer ? ` for ${esc(plan.orders[0].buyer)}` : ''}${plan.orders[0]?.pieces != null ? `, ${fmt(plan.orders[0].pieces)} pieces` : ''}.</span></li>
      <li><span class="ok">${icon('check')}</span><span>${fmt(fields)} column values will be written. Blank columns stay blank.</span></li>
      <li><span class="ok">${icon('check')}</span><span>${fmt(d.document?.counts?.cells || 0)} Excel cells are kept with the upload; ${fmt(d.coverage?.counts?.kept || 0)} of them are preserved without an Oracle column.</span></li>
      <li><span class="ok">${icon('check')}</span><span>Nothing already in Oracle is changed. Existing orders are not touched.</span></li>
      ${plan.warnings.map(w => `<li><span class="warn">${icon('alert')}</span><span>${esc(w)}</span></li>`).join('')}</ul>
    <div class="modal-foot"><button class="btn" data-no>Cancel</button><button class="btn primary large" data-yes>${icon('database')} Approve and save</button></div>`);
  $('[data-no]', body).onclick = () => Modal.close();
  $('[data-yes]', body).onclick = () => { Modal.close(); busy('Saving to Oracle and reading it back…', async () => {
    const r = await api(`/api/drafts/${d.id}/accept`, { method: 'POST', body: { version: d.version, plan_hash: plan.hash } });
    NO.draft = await api(`/api/drafts/${d.id}`); refreshBell?.(); resultPage(view, r);
  }); };
}
function resultPage(view, r) {
  const d = NO.draft; const o = r.orders?.[0] || {};
  const byTable = {}; for (const row of r.rows || []) byTable[row.table] = (byTable[row.table] || 0) + 1;
  setTitle(`Order ${o.order_ref || ''} saved`, '', `<a class="btn" href="#/new">${icon('upload')} Upload next order</a>`);
  page(view, `<div class="steps">${stepsHtml(4)}</div><section class="card"><div class="result-hero">
      <span class="tick">${icon(r.verified === false ? 'alert' : 'check')}</span>
      <h2>${r.verified === false ? 'Saved, but the read-back found a difference' : 'Saved to Oracle and checked'}</h2>
      <p class="muted" style="max-width:640px">${Object.entries(byTable).map(([t, n]) => `${fmt(n)} row${n === 1 ? '' : 's'} added to ${esc(t)}`).join(', ')}.
        ${r.verified === false ? `${r.verified_rows} of ${r.rows.length} rows read back the same. Open the order and compare.` : 'Every saved row was read back from Oracle and is identical.'}</p>
      <div class="row" style="justify-content:center"><a class="btn primary" href="#/orders/${o.ordmasid}">${icon('eye')} Open the order</a><a class="btn" href="#/ai?q=${encodeURIComponent('Show me order ' + (o.order_ref || ''))}">${icon('sparkle')} Ask AI about it</a></div></div></section>
    <section class="card" style="margin-top:16px"><div class="card-title"><span class="tile-ico">${icon('table')}</span><div><h2>Saved rows</h2><p>${esc(d?.filename || '')} · approved by ${esc(App.state.user || 'admin')} · ${when(r.committed_at)}</p></div></div>
      <div class="table-wrap" style="max-height:420px"><table class="data-table"><thead><tr><th>Table</th>${['IONO', 'BUYERNAME', 'STYLENO', 'COMBO', 'SIZES', 'ORDERQTY'].map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>
      ${(r.rows || []).slice(0, 80).map(x => `<tr><td>${esc(x.table)}</td>${['IONO', 'BUYERNAME', 'STYLENO', 'COMBO', 'SIZES', 'ORDERQTY'].map(c => `<td>${esc(x.values[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`);
}

/* ------------------------------------------------------------------ data mapping flowchart */
function renderFlow(body, d) {
  const plan = d.plan, doc = d.document; if (!plan || !doc) { body.innerHTML = '<div class="empty">The mapping appears when Oracle is connected.</div>'; return; }
  const tables = Object.keys(plan.tables); const sel = NO.flowTable || tables[0];
  // Excel parts on the left: order details, size blocks, each linked Excel table, everything kept without a column.
  const linkedIds = new Set(Object.values(plan.tables).map(x => x.section?.id).filter(Boolean));
  const parts = [{ id: 'details', label: 'Order details', sub: `${doc.order_details.length} label/value pairs`, to: [plan.table] },
    ...doc.tables.filter(t => t.kind === 'order').slice(0, 1).map(t => ({ id: 'sizes', label: 'Size blocks', sub: `${doc.tables.filter(x => x.kind === 'order').length} blocks, ${fmt(plan.totals.pieces)} pieces`, to: [plan.table] })),
    ...doc.tables.filter(t => linkedIds.has(t.id)).map(t => ({ id: t.id, label: t.title, sub: `${t.rows.filter(r => !r.total).length} rows`, to: Object.entries(plan.tables).filter(([, x]) => x.section?.id === t.id).map(([k]) => k) })),
    { id: 'kept', label: 'Other Excel tables and values', sub: `${doc.tables.filter(t => t.kind !== 'order' && !linkedIds.has(t.id)).length} tables, ${(plan.unused || []).length} values`, to: ['__kept'] }];
  const targets = [...tables.map(t => ({ id: t, label: t, sub: `${fmt(plan.totals.tables[t] || 0)} rows · ${viewOf(d, t)?.filled || 0} columns`, kind: 'table' })),
    { id: '__kept', label: 'Preserved in JSON', sub: `${fmt(d.coverage?.counts?.kept || 0)} cells, not mapped to Oracle`, kind: 'kept' }];
  body.innerHTML = `<div class="stack"><section class="card flow-card"><div class="card-title"><span class="tile-ico">${icon('flow')}</span><div><h2>Where the Excel goes</h2><p>Click a table to see each Excel field and the Oracle column it fills.</p></div></div>
      <div class="flow" id="flow"><div class="flow-col">${`<div class="flow-node src"><b>${esc(d.filename)}</b><small>${fmt(doc.counts.cells)} cells, ${doc.file.sheets.length} sheet${doc.file.sheets.length === 1 ? '' : 's'}</small></div>`}
        ${parts.map(p => `<div class="flow-node part" data-part="${esc(p.id)}" data-to="${esc(p.to.join(','))}"><b>${esc(p.label)}</b><small>${esc(p.sub)}</small></div>`).join('')}</div>
        <div class="flow-col right">${targets.map(t => `<button type="button" class="flow-node ${t.kind}${t.id === sel ? ' active' : ''}" data-target="${esc(t.id)}"><b>${t.kind === 'table' ? `${esc(t.label)}` : esc(t.label)}</b><small>${t.kind === 'table' ? esc(tableLabel(t.id)) + ' · ' : ''}${esc(t.sub)}</small></button>`).join('')}</div>
        <svg class="flow-lines" id="flow-lines" aria-hidden="true"></svg></div></section>
    <section class="card" id="flow-detail"></section></div>`;
  paintIcons(body);
  const draw = () => drawFlow(body);
  requestAnimationFrame(draw); window.addEventListener('resize', draw, { once: true });
  $$('[data-target]', body).forEach(b => b.onclick = () => { NO.flowTable = b.dataset.target; renderFlow(body, d); });
  flowDetail($('#flow-detail', body), d, sel);
}
function drawFlow(body) {
  const flow = $('#flow', body), svg = $('#flow-lines', body); if (!flow || !svg) return;
  const box = flow.getBoundingClientRect(); svg.setAttribute('width', box.width); svg.setAttribute('height', box.height);
  const src = $('.flow-node.src', flow).getBoundingClientRect();
  let paths = '';
  for (const p of $$('.flow-node.part', flow)) {
    const a = p.getBoundingClientRect();
    paths += `<path d="M ${src.left - box.left + 18} ${src.bottom - box.top} V ${a.top - box.top + a.height / 2} H ${a.left - box.left}" class="stem"/>`;
    for (const t of p.dataset.to.split(',')) {
      const node = $(`[data-target="${CSS.escape(t)}"]`, flow); if (!node) continue; const b = node.getBoundingClientRect();
      const x1 = a.right - box.left, y1 = a.top - box.top + a.height / 2, x2 = b.left - box.left, y2 = b.top - box.top + b.height / 2, mx = (x1 + x2) / 2;
      paths += `<path d="M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" class="${t === '__kept' ? 'kept' : ''}${node.classList.contains('active') ? ' active' : ''}"/>`;
    }
  }
  svg.innerHTML = paths;
}
function flowDetail(el, d, t) {
  if (t === '__kept') {
    const unused = d.plan.unused || []; const linkedIds = new Set(Object.values(d.plan.tables).map(x => x.section?.id).filter(Boolean));
    const tables = d.document.tables.filter(x => x.kind !== 'order' && !linkedIds.has(x.id));
    el.innerHTML = `<div class="card-title"><span class="tile-ico violet">${icon('shield')}</span><div><h2>Preserved — not mapped to Oracle</h2><p>These stay with the upload in the JSON. No Oracle column receives them.</p></div></div>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Excel field</th><th>Excel value</th><th>Cell</th><th>Status</th></tr></thead><tbody>
      ${unused.map(u => `<tr><td>${esc(u.label)}</td><td>${esc(fmtCell(u.value))}</td><td><span class="chip">${esc(u.cell)}</span></td><td><span class="status-chip grey">Preserved — not mapped</span></td></tr>`).join('')}
      ${tables.map(x => `<tr><td><strong>${esc(x.title)}</strong> <span class="muted">(Excel table)</span></td><td class="muted">${x.rows.filter(r => !r.total).length} rows: ${esc(x.columns.slice(0, 5).join(', '))}…</td><td><span class="chip">row ${x.header_row}</span></td><td><span class="status-chip grey">Preserved — not mapped</span></td></tr>`).join('')}</tbody></table></div>`;
    paintIcons(el); return;
  }
  const v = viewOf(d, t), cols = colsOf(d, t); if (!v) { el.innerHTML = ''; return; }
  const rows = cols.map(c => ({ c, b: v.boxes[c.name] })).filter(x => x.b && (x.b.count || ['empty_label', 'error'].includes(x.b.state)));
  el.innerHTML = `<div class="card-title"><span class="tile-ico">${icon('table')}</span><div class="grow"><h2>${esc(t)} <span class="muted" style="font-weight:500">${esc(tableLabel(t))}</span></h2>
      <p>${rows.length} mapped column${rows.length === 1 ? '' : 's'}, ${fmt(v.rows)} row${v.rows === 1 ? '' : 's'}. ${cols.length - rows.length} other columns stay blank.</p></div>
      <div class="search" style="width:220px">${icon('search')}<input id="fd-q" placeholder="Search fields" aria-label="Search fields"></div></div>
    <div class="table-wrap"><table class="data-table" id="fd-table"><thead><tr><th>Excel field</th><th>Excel value</th><th>Oracle column</th><th>Oracle value</th><th>Status</th><th>Cell</th></tr></thead><tbody>
    ${rows.map(({ c, b }) => { const src = b.source || {}; const ex = src.value ?? (b.level === 'row' ? b.values.slice(0, 3).join(', ') : '');
      const val = b.level === 'row' ? `${b.values.slice(0, 3).join(', ')}${b.distinct > 3 ? '…' : ''} (${b.count} rows)` : fmtCell(b.value);
      return `<tr data-q="${esc((c.name + ' ' + c.label + ' ' + (b.excel_label || '')).toLowerCase())}"><td><span class="gdot" style="background:${groupColor(b.group)}" title="${esc(groupName(b.group))}"></span>${esc(b.excel_label || (src.origin || '—'))}</td>
        <td>${esc(fmtCell(ex))}</td><td><strong>${esc(c.name)}</strong><span class="sub">${esc(c.label)}</span></td><td>${esc(val)}</td>
        <td><span class="status-chip ${b.state === 'error' ? 'red' : b.state === 'ai' ? 'blue' : b.state === 'typed' ? 'amber' : 'green'}">${esc(STATE_TEXT[b.state] || b.state)}</span></td>
        <td>${src.cell ? `<button class="chip" data-goto="${esc(t)}|${esc(c.name)}">${esc(src.cell)}</button>` : '<span class="muted">—</span>'}</td></tr>`; }).join('')}</tbody></table></div>`;
  paintIcons(el);
  $('#fd-q', el).oninput = e => { const q = e.target.value.toLowerCase(); $$('#fd-table tbody tr', el).forEach(r => { r.hidden = q && !r.dataset.q.includes(q); }); };
  $$('[data-goto]', el).forEach(b => b.onclick = () => { const [tt, col] = b.dataset.goto.split('|'); openCompare(tt, col); });
}

/* ------------------------------------------------------------------ Excel as JSON */
function renderJson(body, d) {
  const doc = d.document; if (!doc) { body.innerHTML = '<div class="empty">No JSON for this upload.</div>'; return; }
  const c = doc.counts;
  body.innerHTML = `<div class="stack"><section class="card card-pad stack tight"><div class="row between"><div><h2>The whole Excel as structured JSON</h2>
      <p class="muted">Every filled cell is in it exactly once: ${fmt(c.cells)} cells — ${fmt(c.in_order_details)} in order details, ${fmt(c.in_tables)} in Excel tables, ${fmt(c.other)} other. ${c.missing ? `<strong>${c.missing} missing.</strong>` : 'None missing.'}</p></div>
      <button class="btn" id="json-dl">${icon('backup')} Download JSON</button></div></section>
    <section class="card"><div class="card-title"><span class="tile-ico">${icon('list')}</span><div><h2>Order details</h2><p>Label and value pairs from the top of the sheet</p></div></div>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Label</th><th>Value</th><th>Cell</th></tr></thead><tbody>${doc.order_details.map(x => `<tr><td>${esc(x.label)}</td><td><strong>${esc(fmtCell(x.value))}</strong></td><td><span class="chip">${esc(x.cell)}</span></td></tr>`).join('')}</tbody></table></div></section>
    ${doc.tables.map(t => `<details class="card json-table"><summary><strong>${esc(t.title)}</strong> <span class="muted">${t.rows.length} rows · ${t.columns.length} columns · ${esc(t.kind)}</span></summary>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Row</th>${t.columns.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>
      ${t.rows.map(r => `<tr><td class="muted">${r.excel_row}</td>${t.columns.map(h => `<td>${esc(fmtCell(r.values[h]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`).join('')}
    ${doc.other_cells.length ? `<section class="card card-pad"><h3>Other cells</h3><p class="muted">${doc.other_cells.map(x => `${x.cell}: ${fmtCell(x.value)}`).map(esc).join(' · ')}</p></section>` : ''}</div>`;
  paintIcons(body);
  $('#json-dl', body).onclick = async () => { const data = await api(`/api/drafts/${d.id}/document`); const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); a.download = d.filename.replace(/\.xls[xm]$/i, '') + '.json'; a.click(); };
}

/* ------------------------------------------------------------------ local AI step */
async function runAgentAI(view) {
  const d = NO.draft;
  try { const next = await api(`/api/drafts/${d.id}/agent/ai`, { method: 'POST', body: { version: d.version } });
    if (NO.draft?.id === d.id && location.hash.startsWith(`#/new/${d.id}`) && !NO.editing) { NO.draft = next; previewPage(view); } }
  catch (_) { /* the rule-based preview stays; the AI step is optional */ }
}
