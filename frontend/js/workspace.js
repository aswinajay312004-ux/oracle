// New order: Upload → Map to Oracle (mapper.js) → Preview → Saved.
// The preview is the Oracle tables themselves: table name, its columns, and the row(s) each will receive, with the
// values exactly as they will be written. A blank stays blank; the person may type a value into a blank order column.
import { $, $$, esc, fmt, plural, state, notify, busy, confirmDialog } from './util.js';
import { api, id } from './api.js';
import { openMapper, saveMapping, setTyped, catalog } from './mapper.js';

const STAGES = ['upload', 'map', 'preview', 'saved'];
const TITLES = { upload: 'New order', map: 'Map the Excel to Oracle', preview: 'Preview — what Oracle will receive', saved: 'Saved in Oracle' };
const ORDER_OF = { ORDMAS: 0, ORDSTYLE: 1, ORDSIZE: 2, ORDFAB: 3, ORDYARN: 4, ORDACC: 5 };

function stage(name) {
  STAGES.forEach(s => { $(`#stage-${s}`).hidden = s !== name; });
  $$('.stepper li[data-step]').forEach(li => { const n = Number(li.dataset.step), at = STAGES.indexOf(name) + 1; li.classList.toggle('active', n === at); li.classList.toggle('done', n < at); });
  $('#ws-title').textContent = TITLES[name];
  try {
    const want = name === 'upload' ? '#workspace' : `#workspace/${name}`;
    if (location.hash !== want) window.history.pushState(null, '', want);
  } catch (_) { /* file previews */ }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

export function resetWorkspace() {
  state.draft = null; state.dirty = false;
  $('#file-input').value = ''; stage('upload'); recent();
}

export async function recent() {
  try {
    const drafts = (await api('/api/drafts')).filter(d => !d.committed);
    $('#recent').innerHTML = drafts.length ? `<div class="tbl-wrap flush"><table><thead><tr><th scope="col">File</th><th scope="col">Order</th><th scope="col">Uploaded</th></tr></thead><tbody>
      ${drafts.slice(0, 8).map(d => `<tr class="clickable" data-open-draft="${esc(d.id)}"><td><b>${esc(d.filename)}</b></td>
        <td>${esc([...(d.styles || []), ...(d.buyers || [])].filter(Boolean).slice(0, 3).join(' · ') || '—')}</td>
        <td class="nowrap">${esc(d.uploaded_by || '')} <span class="muted small-text">${d.created_at ? esc(new Date(d.created_at).toLocaleString()) : ''}</span></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="empty">Nothing waiting.</p>';
  } catch (e) { $('#recent').innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

export async function upload(file) {
  if (!file) return;
  if (!/\.(xlsx|xlsm|xltx|xltm|xls|xlsb|ods|xml|htm|html|csv|tsv|txt|json)$/i.test(file.name)) return notify('Choose an Excel file (.xlsx, .xls, .xlsm, .xlsb, .ods) or CSV.', 'error');
  await busy(`Reading ${file.name}…`, async () => {
    const form = new FormData(); form.append('file', file);
    state.draft = await api('/api/uploads', { method: 'POST', form });
    await showMap();
  });
}

export async function openDraft(draftId) {
  await busy('Opening…', async () => {
    state.draft = await api(`/api/drafts/${id(draftId)}`);
    if (state.draft.result) return showSaved(state.draft.result);
    await showMap();
  });
}

const mapHooks = { onContinue: () => toPreview(), onNew: () => resetWorkspace(), onDraft: d => { state.draft = d; } };
async function showMap(order = 0) {
  await openMapper($('#stage-map'), state.draft, mapHooks, order);
  stage('map');
}

// Browser Back / Forward inside New order.
export function onHistory(sub) {
  if (!state.draft) return;
  if (sub === 'map' && $('#stage-map').hidden) stage('map');
  else if (sub === 'preview' && state.draft.plan && $('#stage-preview').hidden) { drawPreview(); stage('preview'); }
  else if (!sub && $('#stage-upload').hidden) stage('upload');
}

async function toPreview() {
  await busy('Checking the values against Oracle…', async () => {
    state.draft = await saveMapping();
    state.draft = await api(`/api/drafts/${id(state.draft.id)}/preview`, { method: 'POST', body: { version: state.draft.version } });
    await nextNumbers();
    drawPreview(); stage('preview');
  });
}

async function nextNumbers() {
  state.nextOc = [];
  if (!state.draft.orders.some(o => !String(o.order_ref || '').trim())) return;
  try { state.nextOc = (await api(`/api/oc/next?count=${state.draft.orders.length}`)).numbers || []; } catch (_) { /* shown as "given on save" */ }
}

// ------------------------------------------------------------------ preview: the Oracle tables
function columnMeta(table, name) { return catalog().find(t => t.table === table)?.columns.find(c => c.name === name); }

function cellHtml(node, record, col, idx) {
  const v = record.live?.[col];
  if (col === node.key) return '<span class="pv-auto" title="Given by Oracle when saved">new</span>';
  if (typeof v === 'string' && v.includes('(OC No. given on save)')) {
    const n = state.nextOc?.[Number(String(record.path || '').split('.')[1]) || 0];
    return `<b>${esc(v.replace('(OC No. given on save)', n || 'next OC No'))}</b> <span class="pv-auto" title="The Excel has no OC No, so the next number of the company series is given when the order is saved. Type one to use your own.">given on save</span>`;
  }
  if (record.decoded?.[col] != null) return `${esc(record.decoded[col])}${typeof v === 'string' && v.startsWith('$m') ? ' <span class="pv-new" title="A new name is added to the master table">new name</span>' : ''}`;
  if (typeof v === 'string' && v.startsWith('$')) return `<span class="pv-auto" title="Link to the new ${esc(v.includes('l') && node.table === 'ORDSIZE' ? 'colour line' : 'order')} row">→ new row</span>`;
  if (v == null || v === '') return '';
  return esc(typeof v === 'number' ? fmt(v) : v);
}

function editable(table, col, order) {
  const c = columnMeta(table, col);
  return table === 'ORDMAS' && c && (c.kind === 'field' || c.kind === 'extra') && order != null;
}

function nodeColumns(node) {
  const cols = new Set(node.records.flatMap(r => r.written_columns || []));
  if (node.table === 'ORDMAS') catalog().find(t => t.table === 'ORDMAS')?.columns.filter(c => c.kind === 'field').forEach(c => cols.add(c.name));
  const t = catalog().find(x => x.table === node.table);
  const rank = c => c === node.key ? -2 : (t?.columns.findIndex(x => x.name === c) ?? 999);
  return [...cols].sort((a, b) => rank(a) - rank(b));
}

function tableHtml(node) {
  const rows = node.records.filter(r => r.kind === 'written');
  const cols = nodeColumns(node);
  const label = c => columnMeta(node.table, c)?.label || node.labels?.[c] || c;
  const master = node.kind === 'master';
  return `<section class="pv-table card ${master ? 'pv-master' : ''}" data-table="${esc(node.table)}">
    <header><code class="pv-name">${esc(node.table)}</code><span>${esc(master ? `${node.business} — a new name is added` : node.business)}</span>
      <span class="pill brand">${plural(rows.length, 'new row')}</span></header>
    <div class="tbl-wrap"><table class="pv-grid"><thead><tr><th class="rn" scope="col">#</th>${cols.map(c => `<th scope="col" title="${esc(label(c))}"><code>${esc(c)}</code><small>${esc(label(c))}</small></th>`).join('')}</tr></thead>
    <tbody>${rows.map((r, i) => {
      const order = r.path?.startsWith('orders.') ? Number(r.path.split('.')[1]) : null;
      return `<tr><th class="rn" scope="row">${i + 1}</th>${cols.map(c => {
        const html = cellHtml(node, r, c, i); const blank = html === '';
        const given = String(r.live?.[c] ?? '').includes('(OC No. given on save)');
        const edit = editable(node.table, c, order) && (blank || given || (state.draft.oracle_map?.[order]?.typed || {})[`${node.table}.${c}`] != null);
        return `<td class="${blank ? 'blank' : ''}${edit ? ' editable' : ''}" ${edit ? `data-edit="${esc(`${node.table}.${c}`)}" data-order="${order}" tabindex="0" title="${given ? 'Click to type your own OC No' : 'Blank in the Excel — click to type a value'}"` : ''}>${html || (edit ? '<span class="pv-type">type…</span>' : '')}</td>`;
      }).join('')}</tr>`;
    }).join('')}</tbody></table></div></section>`;
}

function unitAsk(p) {
  const ask = (p.issues || []).filter(i => i.severity === 'error' && /\.(qty_unit|pieces_per_unit)$/.test(i.path || ''));
  return ask.map(i => {
    const oi = Number(i.path.split('.')[1]);
    return `<div class="banner warning unit-ask"><span class="b-ic">?</span><div><b>${esc(i.message)}</b>
      <div class="row wrap" style="margin-top:8px"><button class="secondary small" data-unit="PCS" data-oi="${oi}">Pieces</button>
      <span class="muted small-text">or packs of</span><input type="number" min="2" max="10000" class="unit-n" id="unit-n-${oi}" placeholder="pieces"><button class="secondary small" data-unit="PACK" data-oi="${oi}">Packs</button></div></div></div>`;
  }).join('');
}

function drawPreview() {
  const d = state.draft; const p = d.plan; const root = $('#stage-preview');
  const nodes = (p.graph?.nodes || []).filter(n => !n.reference_only && n.records.some(r => r.kind === 'written'))
    .sort((a, b) => (a.kind === 'master') - (b.kind === 'master') || (ORDER_OF[a.table] ?? 9) - (ORDER_OF[b.table] ?? 9));
  const unit = unitAsk(p);
  const errors = (p.issues || []).filter(i => i.severity === 'error' && !/\.(qty_unit|pieces_per_unit)$/.test(i.path || ''));
  const warnings = p.warnings || [];
  const ex = p.exceptions || {};
  const kept = [...(ex.not_saved || []).map(x => `${x.label}: ${x.values.join(', ')}${x.count > x.values.length ? ` (+${x.count - x.values.length})` : ''}`),
    ...(ex.exclusions || [])];
  const rows = nodes.reduce((n, x) => n + x.records.filter(r => r.kind === 'written').length, 0);
  const ready = p.live && !(p.errors || []).length;
  root.innerHTML = `<div class="map-bar card"><div class="map-file"><b>${esc(d.filename)}</b>
      <span class="muted small-text">${plural(nodes.length, 'Oracle table')} · ${plural(rows, 'new row')} · ${esc(fmt(p.totals.pieces, 0))} pieces</span></div>
      <span class="spacer"></span><button class="secondary" data-p="back">← Back to mapping</button>
      <button class="primary" data-p="save" ${ready ? '' : 'disabled'}>Confirm and save to Oracle</button></div>
    ${p.live ? '' : '<div class="banner warning"><span class="b-ic">!</span><div>Oracle is not connected, so saving is paused. Start the database and press <b>Back to mapping</b>, then Continue again.</div></div>'}
    ${unit}
    ${errors.length ? `<div class="banner error"><span class="b-ic">!</span><div><b>Fix before saving</b><ul>${errors.map(i => `<li>${esc(i.message)}</li>`).join('')}</ul>
      <p class="small-text">Change the mapping (Back to mapping) or type the value in the blank cell below.</p></div></div>` : ''}
    ${warnings.length ? `<details class="banner info pv-notes"><summary><span class="b-ic">i</span><b>${plural(warnings.length, 'note')} to read</b></summary><ul>${warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : ''}
    <p class="map-help">These are the Oracle tables exactly as they will be filled. Empty cells stay empty (NULL). Click a dashed cell of the order to type a value.</p>
    <div class="pv-tables">${nodes.map(tableHtml).join('') || '<p class="empty">Nothing is mapped yet. Go back and map the Excel cells to Oracle columns.</p>'}</div>
    ${kept.length ? `<details class="card pv-kept"><summary><b>${plural(kept.length, 'thing')} from the Excel ${kept.length === 1 ? 'is' : 'are'} not in any Oracle column</b> <span class="muted small-text">kept with this upload in POPPYS · show</span></summary>
      <ul>${kept.map(k => `<li>${esc(k)}</li>`).join('')}</ul><p class="muted small-text">To save one of them, go back to the mapping and give its cells to an Oracle column.</p></details>` : ''}
    <div class="action-bar"><p>${ready ? 'Saved in one step — if anything fails, nothing is saved.' : !p.live ? 'Oracle is offline — saving is paused.' : 'Fix the items above first.'}</p>
      <button class="secondary big" data-p="back">← Back to mapping</button><button class="primary big" data-p="save" ${ready ? '' : 'disabled'}>Confirm and save to Oracle</button></div>`;
}

async function typeValue(td) {
  const target = td.dataset.edit; const order = Number(td.dataset.order);
  const now = state.draft.oracle_map?.[order]?.typed?.[target] || '';
  const c = columnMeta(...target.split('.'));
  td.innerHTML = `<input class="pv-input" value="${esc(now)}" ${c?.type === 'DATE' ? 'type="date"' : c?.type === 'NUMBER' ? 'inputmode="decimal"' : ''} maxlength="${c?.length || 4000}" aria-label="${esc(c?.label || target)}">`;
  const input = td.querySelector('input'); input.focus();
  let done = false;
  const commit = async () => {
    if (done) return; done = true;
    if (input.value.trim() === now) return drawPreview();
    await busy('Checking again…', async () => {
      state.draft = await setTyped(state.draft, order, target, input.value);
      state.draft = await api(`/api/drafts/${id(state.draft.id)}/preview`, { method: 'POST', body: { version: state.draft.version } });
      drawPreview();
    });
  };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { done = true; drawPreview(); } });
  input.addEventListener('blur', commit);
}

async function setUnit(b) {
  const oi = Number(b.dataset.oi); const unit = b.dataset.unit;
  const per = unit === 'PACK' ? Number($(`#unit-n-${oi}`).value) : 1;
  if (unit === 'PACK' && !(per >= 2)) return notify('How many pieces are in one pack? Type 2 or more.', 'warning');
  await busy('Checking again…', async () => {
    const orders = state.draft.orders.map((o, i) => i === oi ? { ...o, qty_unit: unit, pieces_per_unit: per } : o);
    const e = await api(`/api/drafts/${id(state.draft.id)}`, { method: 'PUT', body: { version: state.draft.version, orders } });
    state.draft = await api(`/api/drafts/${id(e.id)}/preview`, { method: 'POST', body: { version: e.version } });
    drawPreview();
  });
}

async function save() {
  const d = state.draft; const p = d.plan;
  const counts = {}; p.operations.filter(o => o.action === 'insert').forEach(o => { counts[o.table] = (counts[o.table] || 0) + 1; });
  const ok = await confirmDialog('Save to Oracle?', `<p><b>${esc(d.orders.map(o => [o.order_ref, o.style, o.buyer].filter(Boolean).join(' · ') || d.filename).join(' / '))}</b> · ${fmt(p.totals.pieces, 0)} pieces</p>
    <ul>${Object.entries(counts).map(([t, n]) => `<li><code>${esc(t)}</code> — ${plural(n, 'new row')}</li>`).join('')}</ul>
    <p class="muted">Existing orders are never changed.</p>`, 'Yes, save');
  if (!ok) return;
  await busy('Saving in one transaction…', async () => {
    let result;
    try { result = await api(`/api/drafts/${id(d.id)}/accept`, { method: 'POST', body: { version: d.version, plan_hash: p.hash, exceptions_seen: p.exceptions?.digest || null } }); }
    catch (e) {
      try { state.draft = await api(`/api/drafts/${id(d.id)}`); } catch (_) { /* keep */ }
      if (state.draft.result) return showSaved(state.draft.result);
      throw e;
    }
    state.draft.result = result;
    await showSaved(result);
  });
}

// ------------------------------------------------------------------ saved
export async function showSaved(result) {
  const refs = (result.orders || []).map(o => o.order_ref).filter(Boolean);
  const tables = {}; (result.rows || []).forEach(r => { tables[r.table] = (tables[r.table] || 0) + 1; });
  const root = $('#stage-saved');
  root.innerHTML = `<section class="card saved-hero"><span class="tick" data-tick>✓</span><div>
      <h1>Order ${esc(refs.join(', ') || '')} is saved in Oracle.</h1>
      <p class="muted">${esc(result.approver || '')} · ${esc(new Date(result.committed_at).toLocaleString())} · <span data-check>checking it in Oracle…</span></p>
      <ul class="saved-tables">${Object.entries(tables).map(([t, n]) => `<li><code>${esc(t)}</code> ${plural(n, 'row')}</li>`).join('')}</ul></div>
      <div class="saved-actions">${(result.orders || []).map(o => `<button class="primary" data-order-panel="${esc(o.ordmasid)}">Open order ${esc(o.order_ref)}</button>`).join('')}
        <button class="secondary" data-s="flow">See the tables in the flowchart</button><button class="secondary" data-s="another">＋ New order</button></div></section>
    ${result.local_record ? `<div class="banner warning"><span class="b-ic">!</span><div>${esc(result.local_record)}</div></div>` : ''}`;
  stage('saved');
  root.querySelector('[data-s="another"]').addEventListener('click', resetWorkspace);
  root.querySelector('[data-s="flow"]').addEventListener('click', () => document.dispatchEvent(new CustomEvent('poppys:tables')));
  try {
    const g = await api(`/api/history/${id(result.job_id)}/graph`);
    root.querySelector('[data-check]').textContent = g.verified ? 'read back from Oracle: every row is there ✓' : 'read back from Oracle: some values differ — open the order to see them';
    if (!g.verified) root.querySelector('[data-tick]').classList.add('warn');
  } catch (e) { root.querySelector('[data-check]').textContent = `not checked yet (${e.message})`; }
}

export function bindWorkspace() {
  $('#stage-preview').addEventListener('click', e => {
    if (state.busy) return;
    const b = e.target.closest('[data-p],[data-unit],td[data-edit]'); if (!b) return;
    if (b.dataset.p === 'back') return busy('Opening the mapping…', () => showMap());
    if (b.dataset.p === 'save') return save();
    if (b.dataset.unit) return setUnit(b);
    if (b.dataset.edit && !b.querySelector('input')) return typeValue(b);
  });
  $('#stage-preview').addEventListener('keydown', e => {
    const td = e.target.closest?.('td[data-edit]');
    if (td && e.target === td && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); typeValue(td); }
  });
}
