// New-order workspace: Upload → Auto-read → Review → Approve → Saved.
import { $, $$, esc, clone, num, fmt, show, plural, state, can, notify, busy, source, confirmDialog } from './util.js';
import { api, id } from './api.js';
import { renderFlow } from './flow.js';
import { renderMapping } from './mapping.js';

const REQUIRED = ['exporter', 'buyer', 'style', 'description', 'order_ref', 'order_date', 'po_number', 'ship_date', 'sample_type'];
const HEADER = [
  ['exporter', 'Exporter', true], ['against', 'Against'], ['__orderno', 'Order No'],
  ['buyer', 'Buyer', true, 'buyer'], ['department', 'Department'], ['order_ref', 'OC No / Order reference', true],
  ['style', 'Style #', true, 'style'], ['description', 'Description', true, 'description'], ['order_date', 'Ord Confm Dt', true, null, 'date'],
  ['po_number', 'PO / Contract No', true], ['packing_type', 'Packing type'], ['components', 'Components', false, null, 'number'],
  ['merch', 'Merch', false, 'merch'], ['season', 'Season'], ['ship_date', 'Ship Dt', true, null, 'date'],
  ['sample_type', 'Sample type', true], ['gender', 'Gender'],
];

export function step(n) {
  $$('.stepper li').forEach((li, i) => { li.classList.toggle('active', i + 1 === n); li.classList.toggle('done', i + 1 < n); li.toggleAttribute('aria-current', i + 1 === n); });
}
function stage(name) {
  ['upload', 'read', 'review', 'approve', 'saved'].forEach(s => { $(`#stage-${s}`).hidden = s !== name; });
  step({ upload: 1, read: 2, review: 3, approve: 4, saved: 5 }[name]);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

export function resetWorkspace() {
  state.draft = null; state.dirty = false; state.saved = false; state.tab = {};
  $('#file-input').value = ''; stage('upload'); recent();
}

export async function recent() {
  try {
    const drafts = await api('/api/drafts');
    $('#recent').innerHTML = drafts.length ? drafts.slice(0, 8).map(d => `<div class="recent">
      <span class="file-badge ${d.kind === 'pdf' ? 'pdf' : ''}">${d.kind === 'pdf' ? 'PDF' : 'XLS'}</span>
      <div><strong>${esc(d.filename)}</strong><div class="muted">${esc(d.format)} · ${esc((d.styles || []).filter(Boolean).join(', ') || 'no style yet')} · ${esc(d.uploaded_by || '')} ${d.created_at ? '· ' + esc(new Date(d.created_at).toLocaleString()) : ''}</div></div>
      <span class="pill ${d.committed ? 'ok' : d.status === 'Preview ready' ? 'info' : ''}">${esc(d.status)}</span>
      <button class="secondary small" data-open-draft="${esc(d.id)}">Open</button></div>`).join('') : '<p class="empty">Your uploads will appear here.</p>';
  } catch (e) { $('#recent').innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

export async function upload(file) {
  if (!file) return;
  if (!/\.(xlsx|xlsm|pdf)$/i.test(file.name)) return notify('Choose an Excel workbook (.xlsx / .xlsm) or a PDF.', 'error');
  if (!can('upload')) return notify('Your role can view orders but not upload. Ask an administrator.', 'error');
  stage('read'); $('#read-title').textContent = `Reading ${file.name}…`; $('#read-steps').innerHTML = ''; $('#read-actions').hidden = true;
  await busy('Reading the file and finding the order…', async () => {
    const form = new FormData(); form.append('file', file);
    try { state.draft = await api('/api/uploads', { method: 'POST', form }); }
    catch (e) { stage('upload'); throw e; }
    state.dirty = false; state.saved = false; state.tab = {};
    showReading();
  });
}

export async function openDraft(draftId) {
  await busy('Opening the saved draft…', async () => {
    state.draft = await api(`/api/drafts/${id(draftId)}`); state.dirty = false; state.saved = !!state.draft.result; state.tab = {};
    if (state.draft.result) return showSaved(state.draft.result);
    renderReview(); stage('review');
  });
}

function showReading() {
  const d = state.draft; const o = d.orders[0];
  const items = [];
  items.push(['ok', `Opened ${d.kind === 'pdf' ? 'PDF' : 'Excel workbook'}`, `${plural(d.sheets.length, d.kind === 'pdf' ? 'page' : 'sheet')} · ${fmt(d.sheets.reduce((n, s) => n + s.cells.length, 0), 0)} filled cells read, formulas and source cells kept`]);
  if (!d.orders.length) {
    items.push(['warn', 'This layout is new to POPPYS AI', d.suggestion ? 'We proposed a mapping from the layout. Check it once — it is saved as a template so next time it is automatic.' : 'Show us once where the order details are; it is saved as a template for next time.']);
  } else {
    items.push(['ok', `Layout recognised: ${d.format}`, `${plural(d.orders.length, 'order')} found`]);
    const found = ['buyer', 'style', 'description', 'po_number', 'ship_date', 'season', 'merch'].filter(k => o[k]);
    items.push(['ok', `Order details found (${found.length}/7)`, found.map(k => `${label(k)}: ${o[k]}`).join(' · ') || 'none']);
    const pcs = l => l.reduce((n, x) => n + x.sizes.reduce((m, s) => m + num(s.quantity), 0), 0);
    items.push([o.lines.length ? 'ok' : 'warn', `Size breakdown: ${plural(o.lines.length, 'garment line')}, ${plural(new Set(o.lines.flatMap(l => l.sizes.map(s => s.size))).size, 'size')}`,
      `Buyer order quantity ${fmt(pcs(o.order_lines || []), 0)} pcs${(o.sample_lines || []).length ? ` · sample section ${fmt(pcs(o.sample_lines), 0)} pcs` : ''}${o.packs ? ` · ${fmt(o.packs.packs, 0)} packs × ${fmt(o.packs.pieces / o.packs.packs, 0)} = ${fmt(o.packs.pieces, 0)} pcs` : ''}`]);
    items.push([o.fabrics?.length ? 'ok' : 'miss', `Fabric & yarn: ${plural(o.fabrics?.length || 0, 'fabric row')}`, o.fabrics?.length ? `${fmt(o.fabrics.reduce((n, f) => n + num(f.total), 0))} kg in the workbook · yarn split suggested from fabric content` : 'No fabric table in this file']);
    items.push([o.accessories?.length ? 'ok' : 'miss', `Trims & accessories: ${plural(o.accessories?.length || 0, 'row')}`, o.accessories?.length ? `${new Set(o.accessories.map(a => a.block)).size} trim tables: ${[...new Set(o.accessories.map(a => a.category))].slice(0, 8).join(', ')}…` : 'No accessory tables in this file']);
    const need = d.orders.flatMap(x => REQUIRED.filter(k => !x[k]).map(label));
    items.push([need.length ? 'warn' : 'ok', need.length ? `Needs you: ${[...new Set(need)].join(', ')}` : 'All required order details present', need.length ? 'These are not in the file. You will fill them on the next screen.' : 'Review the quantities and approve.']);
  }
  $('#read-title').textContent = d.orders.length ? 'Here is what we found' : 'We need one quick hint';
  $('#read-steps').innerHTML = items.map(([k, t, s], i) => `<li class="${k === 'ok' ? '' : k}" style="animation-delay:${i * 90}ms"><span class="ic">${k === 'ok' ? '✓' : k === 'warn' ? '!' : '–'}</span><div><strong>${esc(t)}</strong><small>${esc(s)}</small></div></li>`).join('');
  $('#read-actions').hidden = false; $('#go-review').focus();
}
export function goReview() { renderReview(); stage('review'); }

function label(k) { return ({ order_ref: 'Order reference', order_date: 'Confirmation date', po_number: 'PO number', ship_date: 'Ship date', sample_type: 'Sample type', merch: 'Merchandiser' })[k] || (HEADER.find(h => h[0] === k)?.[1]) || k; }

// ---------------- review ----------------
export function renderReview() {
  const d = state.draft;
  $('#file-kind').textContent = d.kind === 'pdf' ? 'PDF' : 'XLS'; $('#file-kind').className = `file-badge ${d.kind === 'pdf' ? 'pdf' : ''}`;
  $('#file-name').textContent = d.filename;
  $('#file-meta').textContent = `${d.format} · uploaded by ${d.uploaded_by || '—'} · version ${d.version}`;
  setState();
  const locked = !!d.result;
  const needMap = !d.orders.length || /Needs column mapping/.test(d.format);
  $('#mapping-panel').hidden = !needMap && !state.showMapping;
  if (!$('#mapping-panel').hidden) renderMapping($('#mapping-panel'), d, applyMapping);
  $('#orders').innerHTML = d.orders.map((o, i) => orderCard(o, i)).join('') +
    (locked ? '' : `<button class="ghost addrow" data-act="add-order">＋ Add another order manually</button>` +
      (needMap ? '' : ` <button class="link" data-act="show-mapping">Read this file differently…</button>`));
  if (locked) $$('#orders input, #orders select, #orders button').forEach(e => { e.disabled = true; });
  renderQuestions();
}

function setState() {
  const d = state.draft; const p = d.plan;
  $('#draft-state').textContent = d.result ? 'Saved to Oracle' : state.dirty ? 'Unsaved changes' : p ? (p.errors.length ? `${p.errors.length} to fix` : 'Ready to approve') : 'Draft';
  $('#draft-state').className = `pill ${d.result ? 'ok' : p && !state.dirty ? (p.errors.length ? 'err' : 'ok') : state.dirty ? 'warn' : ''}`;
}

function srcOf(o, k) {
  const s = o.provenance?.[k];
  if (s) return source(s);
  return o[k] ? '<span class="chip default">Entered</span>' : '';
}

function field(o, oi, [k, text, req, list, type]) {
  if (k === '__orderno') return `<label class="field"><span>${text}</span><input readonly value="Generated by Oracle on save"><span class="src"><span class="chip default">automatic</span></span></label>`;
  const v = o[k] ?? ''; const missing = req && (v === '' || v == null);
  const issue = issueFor(`orders.${oi}.${k}`);
  return `<label class="field ${missing ? 'missing' : ''} ${issue ? 'problem' : ''}"><span>${esc(text)}${req ? ' <span class="req">*</span>' : ''}</span>
    <input data-path="orders.${oi}.${k}" ${type ? `type="${type}"` : ''} value="${esc(v)}" ${list ? `list="list-${list}"` : ''} ${k === 'order_ref' ? 'placeholder="e.g. PK/26-27/S/1320" maxlength="30"' : ''}>
    <span class="src">${missing ? '<span class="chip need">Needs input</span>' : srcOf(o, k)}</span></label>`;
}

function issueFor(path) { return (state.draft.plan?.issues || []).find(i => i.severity === 'error' && i.path && (i.path === path)); }

function lineQty(l) { return (l.sizes || []).reduce((n, s) => n + num(s.quantity), 0); }

function orderCard(o, oi) {
  const tab = state.tab[oi] || 'order';
  const pcs = (o.lines || []).reduce((n, l) => n + lineQty(l), 0) * (num(o.pieces_per_unit) || 1);
  const ref = state.draft.plan?.reference_only || [];
  return `<section class="card order" id="order-${oi}">
    <div class="order-head"><span class="pill brand">ORDER ${oi + 1}</span><h2>${esc(o.style || 'New order')} <span class="muted">${esc(o.buyer || '')}</span></h2>
      <span class="spacer"></span><span class="pill">${fmt(pcs, 0)} pcs</span>
      ${state.draft.orders.length > 1 ? `<button class="danger small" data-act="remove-order" data-oi="${oi}">Remove</button>` : ''}</div>
    <div class="order-tabs" role="tablist">
      ${[['order', 'Order entry', ''], ['fabric', 'Fabric & yarn', o.fabrics?.length || 0], ['trims', 'Trims & accessories', o.accessories?.length || 0]].map(([t, n, c]) =>
        `<button role="tab" class="order-tab ${tab === t ? 'active' : ''}" aria-selected="${tab === t}" data-act="tab" data-oi="${oi}" data-tab="${t}">${n}${c !== '' ? ` <span class="pill">${c}</span>` : ''}</button>`).join('')}
    </div>
    <div class="order-body">${tab === 'order' ? orderTab(o, oi) : tab === 'fabric' ? fabricTab(o, oi, ref) : trimsTab(o, oi, ref)}</div></section>`;
}

function orderTab(o, oi) {
  const basis = o.quantity_basis || 'unconfirmed';
  const sum = ls => (ls || []).reduce((n, l) => n + lineQty(l), 0);
  const hasSample = (o.sample_lines || []).length; const hasOrder = (o.order_lines || []).length;
  return `<div class="screen-title"><b>Sample Order Entry</b><span class="muted">— same fields as the Axpert screen, filled from the file</span></div>
    <div class="erp-grid">${HEADER.map(h => field(o, oi, h)).join('')}</div>
    <div class="basis"><h3>Which quantities should this sample order use? <span class="req">*</span></h3>
      <div class="segmented" role="radiogroup">
        ${hasSample ? `<button role="radio" aria-checked="${basis === 'sample'}" class="${basis === 'sample' ? 'on' : ''}" data-act="basis" data-oi="${oi}" data-basis="sample">Sample section · ${fmt(sum(o.sample_lines), 0)} pcs</button>` : ''}
        ${hasOrder ? `<button role="radio" aria-checked="${basis === 'order_demo'}" class="${basis === 'order_demo' ? 'on' : ''}" data-act="basis" data-oi="${oi}" data-basis="order_demo">Buyer order quantities · ${fmt(sum(o.order_lines), 0)} pcs</button>` : ''}
        <button role="radio" aria-checked="${basis === 'sample' && !hasSample}" class="${basis === 'sample' && !hasSample ? 'on' : ''}" data-act="basis" data-oi="${oi}" data-basis="manual">I will type sample quantities</button>
      </div>
      <p>${basis === 'order_demo' ? 'The buyer’s order quantities will be saved as this sample order (demo). Check the sizes below.' : basis === 'sample' ? 'The sample quantities below will be saved. Zero quantities must be corrected.' : 'Choose a quantity source to continue.'}</p>
      <label class="field" style="max-width:240px;margin-top:8px"><span>Pieces in one entered unit <span class="req">*</span></span><input type="number" min="1" step="1" data-path="orders.${oi}.pieces_per_unit" value="${esc(o.pieces_per_unit ?? '')}"><span class="src">${o.pieces_per_unit === 1 ? '<span class="chip default">1 = quantities are pieces</span>' : ''}</span></label>
    </div>
    <div class="lines">${(o.lines || []).map((l, li) => lineCard(o, oi, l, li)).join('')}</div>
    <button class="ghost addrow small" data-act="add-line" data-oi="${oi}">＋ Add garment / colour line</button>
    ${(o.warnings || []).length ? `<div class="warnings"><strong>Notes from reading the file</strong><ul>${o.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}`;
}

function lineCard(o, oi, l, li) {
  const total = lineQty(l); const declared = l.declared_total;
  const mismatch = declared != null && num(declared) !== total;
  return `<div class="line" id="line-${oi}-${li}">
    <div class="line-head"><span class="line-no">${li + 1}</span>
      <label class="field">Garment / component<input data-path="orders.${oi}.lines.${li}.label" value="${esc(l.label)}"></label>
      <label class="field ${!l.color ? 'missing' : ''}"><span>Combo / colour <span class="req">*</span></span><input data-path="orders.${oi}.lines.${li}.color" list="list-color" value="${esc(l.color)}"><span class="src">${source(l.provenance?.color)}</span></label>
      <span class="pill">${esc(o.po_number || 'PO —')}</span>
      <button class="ghost small" data-act="remove-line" data-oi="${oi}" data-li="${li}" aria-label="Remove line ${li + 1}">Remove</button></div>
    <div class="sizes">${(l.sizes || []).map((s, si) => `<div class="size">
        <input class="sz" aria-label="Size name" data-path="orders.${oi}.lines.${li}.sizes.${si}.size" list="list-size" value="${esc(s.size)}">
        <input aria-label="Quantity for ${esc(s.size)}" type="number" min="0" step="1" data-path="orders.${oi}.lines.${li}.sizes.${si}.quantity" value="${esc(s.quantity ?? '')}" title="${esc(s.source?.cell ? 'From ' + s.source.cell : '')}">
        <button class="x" data-act="remove-size" data-oi="${oi}" data-li="${li}" data-si="${si}" aria-label="Remove size ${esc(s.size)}">remove</button></div>`).join('')}
      <button class="ghost small" data-act="add-size" data-oi="${oi}" data-li="${li}">＋ Size</button></div>
    <div class="line-foot"><span>Total <strong data-total="${oi}-${li}">${fmt(total, 0)}</strong> pcs${num(o.pieces_per_unit) > 1 ? ` × ${o.pieces_per_unit}` : ''}</span>
      ${mismatch ? `<span class="bad">File total says ${fmt(declared, 0)}</span>` : declared != null ? '<span class="pill ok">Matches file total</span>' : ''}</div></div>`;
}

function refBanner(ref, tables) {
  const r = ref.filter(x => tables.includes(x.table));
  const m = state.mappingStatus || {};
  const unverified = tables.filter(t => m[t] && m[t] !== 'verified');
  if (!r.length && !unverified.length) return '';
  return `<div class="ref-only">These rows are checked and shown for review. They are <b>not saved to Oracle yet</b> because the ${unverified.join(' / ') || r.map(x => x.table).join(' / ')} column mapping is not verified. An administrator can verify it in <b>Setup → Database mapping</b>.</div>`;
}

function lineOptions(o, sel) {
  return `<option value="">— choose —</option>` + (o.lines || []).map((l, i) => `<option value="${i + 1}" ${Number(sel) === i + 1 ? 'selected' : ''}>${i + 1}. ${esc((l.label || '').slice(0, 24))} ${esc(l.color ? '· ' + l.color.slice(0, 18) : '')}</option>`).join('');
}

function fabricTab(o, oi, ref) {
  const fabrics = o.fabrics || [];
  const derived = state.draft.plan?.derived || {};
  return `<div class="screen-title"><b>Fabric Planning</b><span class="muted">— fabric per garment portion, consumption and yarn split</span></div>${refBanner(ref, ['ORDFAB', 'ORDYARN'])}
    ${fabrics.length ? fabrics.map((f, fi) => {
      const line = o.lines?.[num(f.line) - 1]; const pcs = line ? lineQty(line) * (num(o.pieces_per_unit) || 1) : 0;
      const kg = pcs * num(f.yy) / 1000 * (num(f.rejection) || 1);
      const ysum = (f.yarns || []).reduce((n, y) => n + num(y.percent), 0);
      const p = `orders.${oi}.fabrics.${fi}`;
      return `<details class="group" open><summary><span><strong>${esc(f.component || 'Fabric ' + (fi + 1))}</strong> <span class="muted">${esc(f.quality)} · ${esc(f.gsm)} GSM · ${esc(f.color)}</span></span>
        <span class="pill ${derived[p] ? 'ok' : ''}">${fmt(derived[p]?.total_kg ?? kg)} kg to save</span><span class="pill">file ${fmt(f.total)} kg</span></summary>
        <div class="inner"><div class="erp-grid">
          <label class="field ${issueFor(p + '.line') ? 'problem' : ''}"><span>Garment line <span class="req">*</span></span><select data-path="${p}.line" data-type="int">${lineOptions(o, f.line)}</select><span class="src">${source(f.source?.component)}</span></label>
          <label class="field">Portion<input data-path="${p}.portion" value="${esc(f.portion)}"></label>
          <label class="field"><span>Fabric colour <span class="req">*</span></span><input data-path="${p}.color" list="list-color" value="${esc(f.color)}"><span class="src">${source(f.source?.color)}</span></label>
          <label class="field">Fabric quality<input data-path="${p}.quality" value="${esc(f.quality)}"><span class="src">${source(f.source?.quality)}</span></label>
          <label class="field">GSM<input data-path="${p}.gsm" value="${esc(f.gsm)}"><span class="src">${source(f.source?.gsm)}</span></label>
          <label class="field">Dia<input data-path="${p}.dia" value="${esc(f.dia)}"><span class="src">${source(f.source?.dia)}</span></label>
          <label class="field">Content<input data-path="${p}.content" value="${esc(f.content)}"><span class="src">${source(f.source?.content)}</span></label>
          <label class="field ${issueFor(p + '.yy') ? 'problem' : ''}"><span>Consumption (g / piece) <span class="req">*</span></span><input type="number" step="any" min="0" data-path="${p}.yy" value="${esc(f.yy ?? '')}"><span class="src">${source(f.source?.yy)}</span></label>
          <label class="field">Rejection factor<input type="number" step="any" min="1" data-path="${p}.rejection" value="${esc(f.rejection ?? '')}"><span class="src">${source(f.source?.rejection)}</span></label>
        </div>
        <p class="formula">${esc(derived[p]?.formula || `${fmt(pcs, 0)} pcs × ${fmt(f.yy)} g ÷ 1000 × ${fmt(f.rejection || 1)} = ${fmt(kg)} kg`)}</p>
        <div class="table-wrap yarns"><table><thead><tr><th>Yarn count</th><th>Yarn colour</th><th class="num">%</th><th class="num">kg</th><th></th></tr></thead><tbody>
          ${(f.yarns || []).map((y, yi) => `<tr><td><input data-path="${p}.yarns.${yi}.count" value="${esc(y.count)}" title="${esc(y.source?.derivation || '')}"></td>
            <td><input data-path="${p}.yarns.${yi}.color" list="list-color" value="${esc(y.color)}"></td>
            <td><input type="number" step="any" min="0" max="100" data-path="${p}.yarns.${yi}.percent" value="${esc(y.percent ?? '')}"></td>
            <td class="num">${fmt(derived[`${p}.yarns.${yi}`]?.kg ?? kg * num(y.percent) / 100)}</td>
            <td><button class="ghost small" data-act="remove-yarn" data-path="${p}" data-yi="${yi}">Remove</button></td></tr>`).join('')}
          <tr><td colspan="2"><button class="link" data-act="add-yarn" data-path="${p}">＋ Add yarn</button>${(f.yarns || []).some(y => y.source?.derivation) ? ' <span class="muted">Suggested from content — confirm</span>' : ''}</td>
            <td class="num"><strong class="${Math.abs(ysum - 100) > 1e-9 && f.yarns?.length ? 'bad' : ''}">${fmt(ysum)}%</strong></td><td></td><td></td></tr></tbody></table></div>
        <div><button class="danger small" data-act="remove-fabric" data-oi="${oi}" data-fi="${fi}">Remove fabric row</button></div></div></details>`;
    }).join('') : '<p class="empty">No fabric table was found in this file.</p>'}
    <button class="ghost small addrow" data-act="add-fabric" data-oi="${oi}">＋ Add fabric row</button>`;
}

function trimsTab(o, oi, ref) {
  const rows = o.accessories || [];
  const derived = state.draft.plan?.derived || {};
  const groups = new Map();
  rows.forEach((a, ai) => { const k = `${a.block ?? 'm'}|${a.category}`; if (!groups.has(k)) groups.set(k, []); groups.get(k).push([a, ai]); });
  return `<div class="screen-title"><b>Accessories / trims</b><span class="muted">— grouped exactly as the trim tables in the file</span></div>${refBanner(ref, ['ORDACC'])}
    ${groups.size ? [...groups.values()].map(list => {
      const [first] = list[0]; const total = list.reduce((n, [a]) => n + num(a.total), 0);
      const descs = new Set(list.map(([a]) => a.description));
      return `<details class="group"><summary><span><strong>${esc(first.category || first.name || 'Accessory')}</strong> <span class="muted">${esc(first.supplier || '')}</span></span>
        <span class="pill">${list.length} row${list.length === 1 ? '' : 's'}</span><span class="pill">${fmt(total)} ${esc(first.uom || '')}</span></summary>
        <div class="inner">${descs.size === 1 ? `<label class="field">Description for all ${list.length} rows (saved as accessory description)<input data-group-desc="${list.map(([, ai]) => ai).join(',')}" data-oi="${oi}" value="${esc(first.description)}" maxlength="120"><span class="src">${esc(String(first.description || '').length)} characters</span></label>` : ''}
        <div class="table-wrap"><table><thead><tr><th>Garment</th><th>Size</th><th>Colour</th><th>Code</th>${descs.size > 1 ? '<th>Description</th>' : ''}<th>Unit</th><th class="num">File qty</th><th class="num">To save</th><th></th></tr></thead><tbody>
        ${list.map(([a, ai]) => { const p = `orders.${oi}.accessories.${ai}`; return `<tr>
          <td><select data-path="${p}.line" data-type="int"><option value="">All garments</option>${(o.lines || []).map((l, i) => `<option value="${i + 1}" ${num(a.line) === i + 1 ? 'selected' : ''}>${i + 1}. ${esc((l.color || l.label || '').slice(0, 18))}</option>`).join('')}</select></td>
          <td><input data-path="${p}.size" list="list-size" value="${esc(a.size)}"></td><td><input data-path="${p}.color" list="list-color" value="${esc(a.color)}"></td>
          <td><input data-path="${p}.code" value="${esc(a.code)}"></td>${descs.size > 1 ? `<td><input data-path="${p}.description" value="${esc(a.description)}"></td>` : ''}
          <td><input data-path="${p}.uom" value="${esc(a.uom)}" style="width:70px"></td>
          <td class="num"><input type="number" step="any" min="0" data-path="${p}.total" value="${esc(a.total ?? '')}" style="width:100px" title="${esc(a.source?.total?.cell ? 'From ' + a.source.total.cell : '')}"></td>
          <td class="num">${derived[p] ? fmt(derived[p].quantity) : '—'}</td>
          <td><button class="ghost small" data-act="remove-acc" data-oi="${oi}" data-ai="${ai}">Remove</button></td></tr>`; }).join('')}
        </tbody></table></div></div></details>`;
    }).join('') : '<p class="empty">No accessory tables were found in this file.</p>'}`;
}

// ---------------- questions ----------------
function clientChecks() {
  const out = [];
  state.draft.orders.forEach((o, oi) => {
    REQUIRED.forEach(k => { if (!o[k]) out.push({ severity: 'error', message: `Order ${oi + 1}: enter ${label(k).toLowerCase()}.`, path: `orders.${oi}.${k}` }); });
    if (!['sample', 'order_demo'].includes(o.quantity_basis)) out.push({ severity: 'error', message: `Order ${oi + 1}: choose which quantities to use.`, path: `orders.${oi}.quantity_basis` });
    (o.lines || []).forEach((l, li) => { if (lineQty(l) === 0) out.push({ severity: 'error', message: `Order ${oi + 1}, line ${li + 1}: quantities total zero.`, path: `orders.${oi}.lines.${li}.sizes` }); });
  });
  return out;
}

export function renderQuestions() {
  const d = state.draft; const p = !state.dirty && d.plan;
  const items = p ? p.issues : clientChecks();
  const errors = items.filter(i => i.severity === 'error'); const warns = items.filter(i => i.severity !== 'error');
  $('#question-count').textContent = errors.length; $('#question-count').className = `count ${errors.length ? 'err' : p ? 'ok' : ''}`;
  $('#questions').innerHTML = (errors.concat(warns).slice(0, 80).map(i => `<div class="q ${i.severity}"><span>${esc(i.message)}</span>${i.path ? `<button data-act="fix" data-path="${esc(i.path)}">${i.severity === 'error' ? 'Fix' : 'Show'}</button>` : ''}</div>`).join('')
    + (items.length > 80 ? `<p class="muted">…and ${items.length - 80} more.</p>` : '')) ||
    (p ? '<div class="q okmsg">✓ No open questions. Review the exact Oracle entry and approve.</div>' : '<div class="q okmsg">Required order details are filled. Press <b>Check &amp; prepare</b>.</div>');
  const t = p ? p.totals : null;
  $('#totals').innerHTML = t ? [['Orders', t.orders], ['Pieces', fmt(t.pieces, 0)], ['Fabric kg', fmt(t.fabric_kg)], ['Trim rows', t.accessories]].map(([a, b]) => `<div><small>${a}</small><strong>${b}</strong></div>`).join('') : '';
  $('#prepare').disabled = !!d.result || !can('preview');
  $('#prepare').textContent = p && !errors.length ? 'Open the approval preview →' : 'Check & prepare entry →';
}

export function fix(path) {
  const parts = path.split('.'); const oi = Number(parts[1]);
  const tab = parts[2] === 'fabrics' ? 'fabric' : parts[2] === 'accessories' ? 'trims' : 'order';
  if ((state.tab[oi] || 'order') !== tab) { state.tab[oi] = tab; renderReview(); }
  let el = null; let p = path;
  const acc = path.match(/accessories\.(\d+)\.description$/);
  if (acc) el = $$('[data-group-desc]').find(i => i.dataset.groupDesc.split(',').includes(acc[1])) || null;
  while (!el && p.includes('.')) { el = $(`[data-path="${CSS.escape(p)}"]`) || (p.endsWith('quantity_basis') ? $(`[data-act="basis"][data-oi="${oi}"]`) : null); p = p.slice(0, p.lastIndexOf('.')); }
  el ||= $(`#line-${parts[1]}-${parts[3]}`) || $(`#order-${oi}`);
  const details = el?.closest('details'); if (details) details.open = true;
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const f = el?.closest('.field') || el; f?.classList.add('flash'); setTimeout(() => f?.classList.remove('flash'), 2600);
  if (el?.focus) el.focus({ preventScroll: true });
}

// ---------------- editing ----------------
function setPath(path, value) {
  const keys = path.split('.'); let obj = state.draft;
  keys.slice(0, -1).forEach(k => { obj = obj[k]; });
  obj[keys.at(-1)] = value;
}
function getPath(path) { return path.split('.').reduce((o, k) => o?.[k], state.draft); }

export function markDirty() {
  state.dirty = true; state.draft.plan = null; setState(); renderQuestions();
}

export function onInput(e) {
  const el = e.target;
  if (el.dataset.groupDesc != null) {
    const o = state.draft.orders[Number(el.dataset.oi)];
    el.dataset.groupDesc.split(',').forEach(ai => { o.accessories[Number(ai)].description = el.value; });
    el.closest('.field').querySelector('.src').textContent = `${el.value.length} characters`;
    return markDirty();
  }
  const path = el.dataset.path; if (!path) return;
  let v = el.value;
  if (el.type === 'number') v = v === '' ? null : Number(v);
  if (el.dataset.type === 'int') v = v === '' ? null : Number(v);
  setPath(path, v);
  if (/\.sizes\.\d+\.quantity$/.test(path)) {
    const [, oi, , li] = path.split('.'); const line = state.draft.orders[oi].lines[li];
    const t = $(`[data-total="${oi}-${li}"]`); if (t) t.textContent = fmt(lineQty(line), 0);
  }
  const fld = el.closest('.field'); if (fld) { fld.classList.toggle('missing', fld.querySelector('.req') && (v === '' || v == null)); fld.classList.remove('problem'); }
  markDirty();
}

export function onChange(e) { if (e.target.tagName === 'SELECT' && e.target.dataset.path) { onInput(e); renderReview(); } }

export async function onAction(btn) {
  const a = btn.dataset.act; const d = state.draft; if (!a || !d) return false;
  const oi = Number(btn.dataset.oi); const o = d.orders[oi];
  const emptyLine = () => ({ label: '', color: '', declared_total: null, sizes: [{ size: '', quantity: null }] });
  switch (a) {
    case 'tab': state.tab[oi] = btn.dataset.tab; renderReview(); return true;
    case 'basis': {
      const b = btn.dataset.basis;
      if (b === 'sample' && o.sample_lines?.length) o.lines = clone(o.sample_lines);
      if (b === 'order_demo' && o.order_lines?.length) o.lines = clone(o.order_lines);
      o.quantity_basis = b === 'manual' ? 'sample' : b;
      if (b === 'manual' && !o.lines.length) o.lines = [emptyLine()];
      break;
    }
    case 'add-order': d.orders.push({ buyer: '', style: '', description: '', po_number: '', order_ref: '', order_date: '', ship_date: '', exporter: state.health?.settings?.company_exporter || '', sample_type: state.health?.settings?.default_sample_type || '', quantity_basis: 'sample', pieces_per_unit: 1, lines: [emptyLine()], fabrics: [], accessories: [], provenance: {}, warnings: [] }); break;
    case 'remove-order': if (!await confirmDialog('Remove this order?', '<p>It will not be imported. You can add it back by uploading again.</p>', 'Remove')) return true; d.orders.splice(oi, 1); break;
    case 'add-line': o.lines.push(emptyLine()); break;
    case 'remove-line': o.lines.splice(Number(btn.dataset.li), 1); break;
    case 'add-size': { const l = o.lines[Number(btn.dataset.li)]; l.sizes.push({ size: '', quantity: null }); l.declared_total = null; break; }
    case 'remove-size': { const l = o.lines[Number(btn.dataset.li)]; l.sizes.splice(Number(btn.dataset.si), 1); l.declared_total = null; break; }
    case 'add-fabric': (o.fabrics ||= []).push({ component: '', line: o.lines.length === 1 ? 1 : null, portion: 'BODY', color: '', quality: '', gsm: '', dia: '', content: '', yy: null, rejection: 1.03, yarns: [{ count: '', color: 'GREIGE', percent: 100 }] }); break;
    case 'remove-fabric': o.fabrics.splice(Number(btn.dataset.fi), 1); break;
    case 'add-yarn': getPath(btn.dataset.path).yarns ||= []; getPath(btn.dataset.path).yarns.push({ count: '', color: 'GREIGE', percent: null }); break;
    case 'remove-yarn': getPath(btn.dataset.path).yarns.splice(Number(btn.dataset.yi), 1); break;
    case 'remove-acc': o.accessories.splice(Number(btn.dataset.ai), 1); break;
    case 'show-mapping': state.showMapping = true; renderReview(); $('#mapping-panel').scrollIntoView({ behavior: 'smooth' }); return true;
    case 'fix': fix(btn.dataset.path); return true;
    default: return false;
  }
  markDirty(); renderReview(); return true;
}

async function saveEdits() {
  if (!state.dirty) return;
  state.draft = await api(`/api/drafts/${id(state.draft.id)}`, { method: 'PUT', body: { version: state.draft.version, orders: state.draft.orders } });
  state.dirty = false;
}

async function applyMapping(body) {
  await busy('Reading the file with this layout…', async () => {
    await saveEdits();
    state.draft = await api(`/api/drafts/${id(state.draft.id)}/map`, { method: 'POST', body: { version: state.draft.version, ...body } });
    state.showMapping = false; state.tab = {};
    notify(body.save_template ? 'Layout applied and saved as a template. Files with this layout will now be read automatically.' : 'Layout applied. Check the order details.', 'success');
    renderReview();
  });
}

export async function prepare() {
  const d = state.draft;
  if (d.plan && !state.dirty && !d.plan.errors.length) return showApprove();
  await busy('Checking every value against Oracle masters and rules…', async () => {
    await saveEdits();
    state.draft = await api(`/api/drafts/${id(d.id)}/preview`, { method: 'POST', body: { version: state.draft.version } });
    const p = state.draft.plan;
    if (p.errors.length) { renderReview(); notify(`${plural(p.errors.length, 'question')} to resolve before approval. Use “Fix” to jump to each one.`, 'warning'); }
    else showApprove();
  });
}

export async function ask(question) {
  await busy('Asking the local assistant…', async () => {
    const r = await api(`/api/drafts/${id(state.draft.id)}/assist`, { method: 'POST', body: { question } });
    $('#answer').textContent = r.answer; $('#answer').hidden = false;
  });
}

// ---------------- approve ----------------
function opIndex(plan) { return Object.fromEntries(plan.operations.map(op => [op.key, op])); }

function valueCell(v, ops, meta) {
  if (meta?.master && v != null && !(typeof v === 'string' && v.startsWith('$'))) {
    const cols = state.draft?.plan?.columns || {};
    // Match on the master role as well as the ID: IDs from different master tables can coincide.
    const reuse = Object.values(ops).find(op => op.action === 'reuse' && Object.values(op.values)[0] === v
      && cols[op.table]?.[Object.keys(op.values)[0]]?.master === meta.master);
    if (reuse) return `${esc(show(Object.values(reuse.values)[1]))}<span class="reusetag">existing · ID ${esc(v)}</span>`;
  }
  if (typeof v === 'string' && v.startsWith('$')) {
    const op = ops[v.slice(1)];
    if (!op) return '<span class="gen">generated</span>';
    if (/^m\d+$/.test(op.key)) {
      const label = Object.values(op.values).find(x => !(typeof x === 'string' && x.startsWith('$')) && typeof x !== 'number');
      return `${esc(show(label))}${op.action === 'reuse' ? '<span class="reusetag">existing</span>' : '<span class="newtag">new master</span>'}`;
    }
    return op.key === v.slice(1) && op.values && Object.values(op.values)[0] === v ? '<span class="gen">new ID — generated on save</span>' : `<span class="gen">→ ${esc(op.table)} ${esc(op.where || '')}</span>`;
  }
  return esc(show(v));
}

function fromCell(op, col, plan) {
  const meta = plan.columns?.[op.table]?.[col]; const src = op.source || {};
  const f = meta?.field?.split('.')[1];
  const s = (f && src[f]) || (src.cell ? src : null);
  return s ? source(s) : meta?.field?.startsWith('calc.') ? '<span class="chip default">calculated</span>' : '';
}

function headerTable(op, plan, ops) {
  const meta = plan.columns?.[op.table] || {};
  return `<div class="table-wrap"><table class="colmap"><thead><tr><th>Business field</th><th>Value to save</th><th>Oracle column</th><th>From</th></tr></thead><tbody>
    ${Object.entries(op.values).map(([c, v]) => `<tr><td>${esc(meta[c]?.label || c)}</td><td>${valueCell(v, ops, meta[c])}</td><td class="dest">${esc(op.table)}.${esc(c)}</td><td class="from">${fromCell(op, c, plan)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

function rowsTable(list, plan, ops, limit = 60) {
  if (!list.length) return '';
  const cols = Object.keys(list[0].values); const meta = plan.columns?.[list[0].table] || {};
  return `<div class="table-wrap" style="max-height:420px"><table><thead><tr><th>Row</th>${cols.map(c => `<th title="${esc(list[0].table + '.' + c)}">${esc(meta[c]?.label || c)}<br><code>${esc(c)}</code></th>`).join('')}</tr></thead><tbody>
    ${list.slice(0, limit).map(op => `<tr><td class="muted">${esc(op.where || op.key)}</td>${cols.map(c => `<td>${valueCell(op.values[c], ops, meta[c])}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div>${list.length > limit ? `<p class="muted" style="margin-top:6px">Showing ${limit} of ${list.length} rows. All ${list.length} will be saved.</p>` : ''}`;
}

export function showApprove() {
  const d = state.draft; const plan = d.plan; const ops = opIndex(plan);
  const inserts = plan.operations.filter(o => o.action === 'insert'); const reuse = plan.operations.filter(o => o.action === 'reuse');
  const by = t => plan.operations.filter(o => o.table === t);
  const tables = [...new Set(inserts.map(o => o.table))];
  const masters = plan.operations.filter(o => /^m\d+$/.test(o.key));
  const ref = plan.reference_only || [];
  const block = (title, sub, body) => `<section class="card screen-block"><div class="card-head"><div><h2>${title}</h2><span class="muted">${sub}</span></div></div><div class="body">${body}</div></section>`;
  const refNote = t => { const r = ref.find(x => x.table === t); return r ? `<div class="ref-only"><b>${esc(r.business)}</b>: ${r.rows} rows checked but <b>not saved</b> — ${esc(r.reason)}</div>` : ''; };
  const canApprove = plan.live && !plan.errors.length && can('approve');
  $('#stage-approve').innerHTML = `
    <div class="approve-head"><div><span class="eyebrow">STEP 4 · APPROVAL</span><h1>This is exactly what will be saved.</h1>
      <p class="lead">Nothing is in Oracle yet. Check the values, the destination columns and where each value came from in your file.</p></div>
      <button class="ghost" data-act2="back-review">← Back to review</button></div>
    ${plan.live ? '' : '<div class="notice warning">Oracle is not connected. This preview used the saved database structure, so existing masters were not checked. Start the database and prepare the preview again to approve.</div>'}
    <div class="kpis">${[['New records', inserts.length], ['Oracle tables', tables.length], ['Existing masters reused', reuse.length], ['Pieces', fmt(plan.totals.pieces, 0)], ['Fabric (kg)', fmt(plan.totals.fabric_kg)]].map(([a, b]) => `<div class="kpi"><small>${a}</small><strong>${b}</strong></div>`).join('')}</div>
    ${plan.warnings.length ? `<div class="warnings"><strong>Please read before approving</strong><ul>${plan.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
    ${by('ORDMAS').map(op => block(`Order Entry · ${esc(op.values.DOCID || '')}`, 'ORDMAS — order header', headerTable(op, plan, ops))).join('')}
    ${block('Style / colour lines', `ORDSTYLE — ${plural(by('ORDSTYLE').length, 'row')}`, rowsTable(by('ORDSTYLE'), plan, ops))}
    ${block('Size quantities', `ORDSIZE — ${plural(by('ORDSIZE').length, 'row')}`, rowsTable(by('ORDSIZE'), plan, ops, 120))}
    ${block('Fabric planning', `ORDFAB / ORDYARN — ${plural(by('ORDFAB').length, 'fabric row')}, ${plural(by('ORDYARN').length, 'yarn row')}`, refNote('ORDFAB') + refNote('ORDYARN') + rowsTable(by('ORDFAB'), plan, ops) + rowsTable(by('ORDYARN'), plan, ops) || '<p class="muted">No fabric rows.</p>')}
    ${block('Accessories', `ORDACC — ${plural(by('ORDACC').length, 'row')}`, refNote('ORDACC') + rowsTable(by('ORDACC'), plan, ops, 40) || '<p class="muted">No accessory rows.</p>')}
    ${block('Master records', `${plural(masters.filter(m => m.action === 'insert').length, 'new master')} · ${plural(masters.filter(m => m.action === 'reuse').length, 'existing master')} reused`,
      `<div class="table-wrap"><table><thead><tr><th>Master</th><th>Value</th><th>Action</th><th>Oracle table</th></tr></thead><tbody>${masters.map(m => { const vals = Object.entries(m.values); return `<tr><td>${esc(m.business)}</td><td>${esc(show(vals.find(([, v]) => typeof v === 'string' && !v.startsWith('$'))?.[1]))}</td><td>${m.action === 'reuse' ? `<span class="reusetag">existing ID ${esc(vals[0][1])}</span>` : '<span class="newtag">will be created</span>'}</td><td class="dest">${esc(m.table)}</td></tr>`; }).join('')}</tbody></table></div>`)}
    <div class="approve-bar"><p>${canApprove ? `Approving writes these ${plural(inserts.length, 'record')} in <b>one Oracle transaction</b>, reads every row back to confirm it, and records who approved it. If anything fails, nothing is saved.` :
      !plan.live ? 'Saving is disabled until Oracle is connected.' : plan.errors.length ? 'Resolve the open questions first.' : 'Your role cannot approve. An approver or administrator must approve this entry.'}</p>
      <button class="primary big-action" data-act2="approve" ${canApprove ? '' : 'disabled'}>Approve &amp; save to Oracle</button></div>`;
  stage('approve');
}

export async function approve() {
  const d = state.draft; const plan = d.plan;
  const inserts = plan.operations.filter(o => o.action === 'insert');
  const counts = {}; inserts.forEach(o => { counts[o.table] = (counts[o.table] || 0) + 1; });
  const ok = await confirmDialog('Save this order to Oracle?', `<p>${esc(d.orders.map(o => `${o.order_ref} · ${o.style} · ${o.buyer}`).join('\n'))}</p>
    <ul>${Object.entries(counts).map(([t, n]) => `<li><b>${esc(t)}</b>: ${n} new record${n === 1 ? '' : 's'}</li>`).join('')}</ul>
    <p class="muted">This cannot be undone from POPPYS AI. Existing orders are never overwritten.</p>`, 'Yes, save to Oracle');
  if (!ok) return;
  await busy('Saving in one transaction and reading every row back…', async () => {
    let result;
    try { result = await api(`/api/drafts/${id(d.id)}/accept`, { method: 'POST', body: { version: d.version, plan_hash: plan.hash } }); }
    catch (e) {
      try { state.draft = await api(`/api/drafts/${id(d.id)}`); } catch (_) { /* keep current */ }
      throw new Error(`${e.message}\nNothing was partially saved. Prepare the preview again and retry.`);
    }
    state.draft.result = result; state.saved = true;
    await showSaved(result);
  });
}

export async function showSaved(result, target = $('#stage-saved'), standalone = false) {
  target.innerHTML = `<section class="card saved-hero"><span class="tick">✓</span><div><span class="eyebrow">${result.already_committed ? 'ALREADY SAVED' : 'SAVED AND VERIFIED'}</span>
      <h1>${result.already_committed ? 'This order is in Oracle.' : 'The order is saved in Oracle.'}</h1>
      <p class="muted">Approved by ${esc(result.approver || '—')} · ${esc(new Date(result.committed_at).toLocaleString())} · ${plural((result.rows || []).length, 'new record')}${(result.reused || []).length ? `, ${plural(result.reused.length, 'existing master')} linked` : ''}</p>
      <div class="ids">${(result.orders || []).map(o => `<span><b>${esc(o.order_ref)}</b> · ORDMASID <code>${esc(o.ordmasid)}</code></span>`).join('')}</div></div>
      <div style="display:grid;gap:8px">${(result.orders || []).map(o => `<button class="secondary" data-explore-order="${esc(o.ordmasid)}">Open in Database</button>`).join('')}${standalone ? '' : '<button class="primary" data-act2="another">Start another order</button>'}</div></section>
    <section class="card flow-card"><div class="card-head"><div><h2>Where your data was saved</h2><span class="muted" id="flow-sub">Reading the rows back from Oracle…</span></div><span class="pill" id="flow-verified">checking</span></div>
      <div class="flow-wrap"><div class="flow" id="flow"></div></div>
      <div class="legend"><span><i style="background:#c2410c"></i>Order header</span><span><i style="background:#0b5d57"></i>Order detail tables</span><span><i style="background:#7c3aed"></i>Master tables</span><span>Click a table to see its exact saved records</span></div></section>
    ${(result.reference_only || []).length ? `<div class="ref-only" style="margin-top:12px">Not saved (mapping not verified yet): ${result.reference_only.map(r => `${esc(r.business)} (${r.rows} rows)`).join(', ')}.</div>` : ''}`;
  if (!standalone) stage('saved');
  try {
    const graph = await api(`/api/history/${id(result.job_id)}/graph`);
    target.querySelector('#flow-sub').textContent = `${plural(graph.nodes.length, 'table')} affected · values below come from a fresh SELECT, not from the save receipt`;
    const v = target.querySelector('#flow-verified');
    v.textContent = graph.verified ? '✓ Verified in Oracle' : '⚠ Differences found'; v.className = `pill ${graph.verified ? 'ok' : 'err'}`;
    renderFlow(target.querySelector('#flow'), graph);
  } catch (e) {
    target.querySelector('#flow-sub').textContent = `Could not read back from Oracle: ${e.message}`;
    target.querySelector('#flow-verified').textContent = 'not verified'; target.querySelector('#flow-verified').className = 'pill warn';
  }
}
