// New-order workspace: Upload → Review (Axpert-style entry) → Check & approve → Saved.
// Problems are shown on the field or table cell they belong to (red outline + one short reason),
// never as a separate list at the bottom of the page.
import { $, $$, esc, clone, num, fmt, plural, state, can, notify, busy, source, confirmDialog, drawer } from './util.js';
import { api, id, orderFields } from './api.js';
import { renderMapping } from './mapping.js';
import { flowCard, mountFlow } from './flowview.js';

const HEADER = [
  // key, label, required, datalist, input type
  ['exporter', 'Exporter', true], ['against', 'Against'], ['__orderno', 'Order No'], ['order_ref', 'OC No / Order reference', true],
  ['buyer', 'Buyer', true, 'buyer'], ['department', 'Department'], ['style', 'Style #', true, 'style'], ['description', 'Description', true, 'description'],
  ['order_date', 'Ord Confm Dt', true, null, 'date'], ['po_number', 'PO / Contract No', true], ['ship_date', 'Ship Dt', true, null, 'date'], ['sample_type', 'Sample type', true],
  ['merch', 'Merchandiser', false, 'merch'], ['season', 'Season'], ['packing_type', 'Packing type'], ['components', 'Components', false, null, 'number'],
  ['gender', 'Gender'],
];
const DEFAULT_REQUIRED = HEADER.filter(h => h[2]).map(h => h[0]);
const LABEL = Object.fromEntries(HEADER.map(h => [h[0], h[1]]));
const UNIT_WORDS = { PACK: /\b(packs?|pks?|sets?|ratio)\b/i, PCS: /\b(pcs|pieces?|pc)\b/i };
let nav = 0;

// Required fields follow Setup → Order fields (Axpert-locked ones are always required).
function isRequired(k) { const f = (state.fields || []).find(x => x.key === k); return f ? !!f.required : DEFAULT_REQUIRED.includes(k); }
function requiredKeys() { return HEADER.map(h => h[0]).filter(k => k !== '__orderno' && isRequired(k)); }
async function loadFields() { try { state.fields = await orderFields(true); } catch (_) { /* the screen falls back to the Axpert defaults */ } }

// ---------------------------------------------------------------- stages
function step(n) {
  $$('.stepper li[data-step]').forEach(li => { const s = Number(li.dataset.step); li.classList.toggle('active', s === n); li.classList.toggle('done', s < n); });
}
function stage(name) {
  ['upload', 'read', 'review', 'approve', 'saved'].forEach(s => { $(`#stage-${s}`).hidden = s !== name; });
  step({ upload: 1, read: 1, review: 2, approve: 3, saved: 4 }[name]);
  $('#ws-title').textContent = { upload: 'New sample order', read: 'Reading the file', review: 'Review the order entry', approve: 'Check before saving', saved: 'Saved in Oracle' }[name];
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

export function resetWorkspace() {
  state.draft = null; state.dirty = false; state.saved = false; state.tab = {}; state.planIssues = []; state.showMapping = false;
  $('#file-input').value = ''; stage('upload'); recent();
}

export async function recent() {
  try {
    const drafts = await api('/api/drafts');
    $('#recent').innerHTML = drafts.length ? `<div class="tbl-wrap flush"><table><thead><tr><th>File</th><th>Order</th><th>Uploaded</th><th>Status</th><th></th></tr></thead><tbody>
      ${drafts.slice(0, 8).map(d => `<tr class="clickable" data-open-draft="${esc(d.id)}"><td><span class="row"><span class="file-badge">XLS</span><span><b>${esc(d.filename)}</b><br><span class="muted small-text">${esc(d.format)}</span></span></span></td>
        <td>${esc((d.styles || []).filter(Boolean).join(', ') || '—')}<br><span class="muted small-text">${esc((d.buyers || []).filter(Boolean).join(', '))}</span></td>
        <td class="nowrap">${esc(d.uploaded_by || '')}<br><span class="muted small-text">${d.created_at ? esc(new Date(d.created_at).toLocaleString()) : ''}</span></td>
        <td><span class="pill ${({ 'Saved to Oracle': 'ok', 'Ready to approve': 'info', 'Needs attention': 'err', 'Checked offline': 'warn' })[d.status] || ''}">${esc(d.status)}</span></td><td><button class="secondary small" data-open-draft="${esc(d.id)}">Open</button></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="empty">Your uploads will appear here.</p>';
  } catch (e) { $('#recent').innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

export async function upload(file) {
  if (!file) return;
  if (!/\.(xlsx|xlsm|xltx|xltm|xls|xlsb|ods|xml|htm|html|csv|tsv|txt|json)$/i.test(file.name)) return notify('Choose the buyer Excel file (.xlsx, .xls, .xlsm, .xlsb, .ods, CSV, Excel XML/HTML export) or a JSON file.', 'error');
  if (!can('upload')) return notify('Your role can view orders but not upload. Ask an administrator.', 'error');
  stage('read'); $('#read-title').textContent = `Reading ${file.name}…`; $('#read-steps').innerHTML = ''; $('#read-actions').hidden = true;
  await busy('Reading the file and finding the order…', async () => {
    const form = new FormData(); form.append('file', file);
    try { [state.draft] = await Promise.all([api('/api/uploads', { method: 'POST', form }), loadFields()]); }
    catch (e) { stage('upload'); throw e; }
    state.dirty = false; state.saved = false; state.tab = {}; state.planIssues = []; state.showMapping = false;
    showReading();
  });
}

export async function openDraft(draftId) {
  await busy('Opening the saved draft…', async () => {
    [state.draft] = await Promise.all([api(`/api/drafts/${id(draftId)}`), loadFields()]); state.dirty = false; state.saved = !!state.draft.result; state.tab = {}; state.showMapping = false;
    state.planIssues = state.draft.plan?.issues || [];
    if (state.draft.result) return showSaved(state.draft.result);
    renderReview(); stage('review');
  });
}

function showReading() {
  const d = state.draft; const o = d.orders[0]; const items = [];
  items.push(['ok', `Opened: ${d.source_format || 'Excel workbook'}`, `${plural(d.sheets.length, 'sheet')} · ${fmt(d.sheets.reduce((n, s) => n + s.cells.length, 0), 0)} filled cells read, each value keeps its source cell`]);
  (d.templates_refused || []).forEach(t => items.push(['warn', `Saved layout “${t.name}” (version ${t.version}) was not used`, `${(t.reasons || []).join('; ')} — so nothing from the saved layout was carried over.`]));
  if (!d.orders.length) {
    items.push(['warn', 'This layout is new to POPPYS AI', d.suggestion ? 'A layout was proposed from the labels and values in the file. Check it once — it is saved as a template so next time it is automatic.' : 'Show us once where the order details are; it is saved as a template for next time.']);
  } else {
    items.push(['ok', d.template ? `Read with your saved layout “${d.template.name}” (version ${d.template.version})` : `Layout recognised: ${d.format}`, `${plural(d.orders.length, 'order')} found`]);
    const found = ['buyer', 'style', 'description', 'po_number', 'ship_date', 'season', 'merch'].filter(k => o[k]);
    items.push(['ok', `Order details found (${found.length} of 7)`, found.map(k => `${LABEL[k] || k}: ${o[k]}`).join(' · ') || 'none']);
    const pcs = l => l.reduce((n, x) => n + x.sizes.reduce((m, s) => m + num(s.quantity), 0), 0);
    items.push([o.lines.length ? 'ok' : 'warn', `Size breakdown: ${plural(o.lines.length, 'colour line')}, ${plural(new Set(o.lines.flatMap(l => l.sizes.map(s => s.size))).size, 'size')}`,
      `Buyer order ${fmt(pcs(o.order_lines || []), 0)} pcs${(o.sample_lines || []).length ? ` · sample section ${fmt(pcs(o.sample_lines), 0)} pcs` : ''}${o.packs ? ` · ${fmt(o.packs.packs, 0)} packs × ${fmt(o.packs.pieces / o.packs.packs, 0)} = ${fmt(o.packs.pieces, 0)} pcs` : ''}`]);
    items.push([o.fabrics?.length ? 'ok' : 'miss', `Fabric & yarn: ${plural(o.fabrics?.length || 0, 'fabric row')}`, o.fabrics?.length ? `${fmt(o.fabrics.reduce((n, f) => n + num(f.total), 0))} kg in the workbook · yarn split suggested from fabric content` : 'No fabric table in this file']);
    items.push([o.accessories?.length ? 'ok' : 'miss', `Trims & accessories: ${plural(o.accessories?.length || 0, 'row')}`, o.accessories?.length ? `${new Set(o.accessories.map(a => a.block)).size} trim tables: ${[...new Set(o.accessories.map(a => a.category))].slice(0, 6).join(', ')}…` : 'No accessory tables in this file']);
    const unknown = d.orders.filter(x => 'qty_unit' in x && !x.qty_unit);
    if (unknown.length) items.push(['warn', 'Pieces or packs? The file does not say', `${unknown[0].provenance?.qty_unit?.question || 'Are these quantities pieces or packs?'} You choose it on the next screen.`]);
    const need = [...new Set(d.orders.flatMap(x => requiredKeys().filter(k => !x[k]).map(k => LABEL[k])))];
    items.push([need.length ? 'warn' : 'ok', need.length ? `${plural(need.length, 'field')} not in the file: ${need.join(', ')}` : 'All required order details present',
      need.length ? 'They are outlined in red on the next screen — type them there.' : 'Review the quantities and approve.']);
  }
  $('#read-title').textContent = d.orders.length ? 'Here is what we found' : 'We need one quick hint';
  $('#read-steps').innerHTML = items.map(([k, t, s], i) => `<li class="${k === 'ok' ? '' : k}" style="animation-delay:${i * 80}ms"><span class="ic">${k === 'ok' ? '✓' : k === 'warn' ? '!' : '–'}</span><div><strong>${esc(t)}</strong><small>${esc(s)}</small></div></li>`).join('');
  $('#read-actions').hidden = false; $('#go-review').focus();
}
export function goReview() { renderReview(); stage('review'); }

// ---------------------------------------------------------------- issues → fields
const strip = m => { const t = String(m).replace(/^Order \d+(, [^:]{1,60})?: /, ''); return t.charAt(0).toUpperCase() + t.slice(1); };
const lineQty = l => (l.sizes || []).reduce((n, s) => n + num(s.quantity), 0);

function clientIssues() {
  const out = [];
  state.draft.orders.forEach((o, oi) => {
    // a value the file holds but the reader would not trust (a description in the style cell, an unclear date) says why
    requiredKeys().forEach(k => { if (o[k] == null || String(o[k]).trim() === '') out.push({ severity: 'error', message: o.provenance?.[k]?.question || (DEFAULT_REQUIRED.includes(k) ? 'Not in the file — please enter it.' : `${LABEL[k]} is required for every order — enter it.`), path: `orders.${oi}.${k}`, client: true }); });
    if (!['sample', 'order_demo'].includes(o.quantity_basis)) out.push({ severity: 'error', message: 'Choose which quantities this sample order uses.', path: `orders.${oi}.quantity_basis`, client: true });
    if ('qty_unit' in o) {   // drafts saved before the unit question keep the old pieces-per-unit rule
      const ppu = o.pieces_per_unit == null || o.pieces_per_unit === '' ? null : Number(o.pieces_per_unit);
      if (!['PCS', 'PACK'].includes(o.qty_unit)) out.push({ severity: 'error', message: o.provenance?.qty_unit?.question || 'Are these quantities pieces or packs?', path: `orders.${oi}.qty_unit`, client: true });
      else if (o.qty_unit === 'PACK' && !(Number.isInteger(ppu) && ppu >= 2)) out.push({ severity: 'error', message: ppu === 1 ? 'A pack holds at least 2 pieces — how many pieces are in one pack?' : 'How many pieces are in one pack?', path: `orders.${oi}.pieces_per_unit`, client: true });
    }
    (o.lines || []).forEach((l, li) => {
      if (!String(l.color || '').trim()) out.push({ severity: 'error', message: 'Enter the colour / combo.', path: `orders.${oi}.lines.${li}.color`, client: true });
      if (lineQty(l) === 0) out.push({ severity: 'error', message: 'All quantities are zero — type the quantities or choose another quantity source.', path: `orders.${oi}.lines.${li}.sizes`, client: true });
    });
  });
  return out;
}

function allIssues() {
  // live checks on what is on screen win over the last server check for the same field
  const client = clientIssues();
  const plan = (state.planIssues || []).filter(i => i.path && !client.some(c => c.path === i.path));
  return [...client, ...plan];
}
function fileIssues() { return (state.planIssues || []).filter(i => !i.path); }
function at(path) {
  const list = allIssues().filter(i => i.path === path);
  return list.find(i => i.severity === 'error') || list[0];
}
function under(prefix) { return allIssues().filter(i => i.path === prefix || i.path.startsWith(prefix + '.')); }
function tabOf(path) { return /\.fabrics(\.|$)/.test(path) ? 'fabric' : /\.accessories(\.|$)/.test(path) ? 'trims' : 'order'; }
function cls(issue) { return issue ? (issue.severity === 'error' ? 'err' : 'warn') : ''; }
function msgHtml(issue) { return issue ? `<div class="f-msg"><span>${esc(strip(issue.message))}</span></div>` : ''; }
function cellMsgs(list) { return list.map(i => `<div class="cell-msg ${cls(i)}">${esc(strip(i.message))}</div>`).join(''); }

// ---------------------------------------------------------------- review
export function renderReview() {
  const d = state.draft; const root = $('#stage-review');
  const locked = !!d.result;
  const needMap = !d.orders.length || /Needs column mapping/.test(d.format);
  const files = fileIssues();
  const p = d.plan;
  root.innerHTML = `
    <div class="card doc-bar"><span class="file-badge">XLS</span>
      <div><span class="name">${esc(d.filename)}</span><small>${d.template ? `Saved layout “${esc(d.template.name)}” (v${esc(d.template.version)})` : esc(d.format)} · uploaded by ${esc(d.uploaded_by || '—')} · version ${d.version}</small></div><span class="spacer"></span>
      <span class="pill ${d.result ? 'ok' : state.dirty ? 'warn' : p ? (p.errors.length ? 'err' : 'ok') : ''}">${d.result ? 'Saved to Oracle' : state.dirty ? 'Changes not checked yet' : p ? (p.errors.length ? 'Needs attention' : 'Checked · ready') : 'Draft'}</span>
      <button class="ghost small" data-act="ask-file">Ask about this file</button>${needMap ? '' : '<button class="ghost small" data-act="show-mapping">Read this file differently</button>'}<button class="secondary small" data-act="new-upload">New upload</button></div>
    ${files.length ? `<div class="banner error"><span class="b-ic">!</span><div><b>Before this can be saved</b><ul>${files.map(i => `<li>${esc(i.message)}</li>`).join('')}</ul></div></div>` : ''}
    ${p && !p.live ? '<div class="banner warning"><span class="b-ic">!</span><div>Oracle is not connected, so existing masters and order numbers could not be checked. Start the database and press <b>Check &amp; continue</b> again.</div></div>' : ''}
    <section id="mapping-panel" class="card" ${needMap || state.showMapping ? '' : 'hidden'} style="margin-bottom:14px"></section>
    <div id="orders">${d.orders.map((o, i) => orderScreen(o, i)).join('')}
      ${locked ? '' : '<button class="secondary" data-act="add-order">＋ Add another order manually</button>'}</div>
    <div class="action-bar review-bar" id="review-side"></div>`;
  if (needMap || state.showMapping) renderMapping($('#mapping-panel'), d, applyMapping);
  if (locked) $$('#orders input, #orders select, #orders button').forEach(e => { e.disabled = true; });
  renderSide();
}

function counts(oi) {
  const out = { order: 0, fabric: 0, trims: 0 };
  allIssues().filter(i => i.severity === 'error' && i.path.startsWith(`orders.${oi}.`)).forEach(i => { out[tabOf(i.path)]++; });
  return out;
}

function orderScreen(o, oi) {
  const tab = state.tab[oi] || 'order'; const c = counts(oi);
  const pcs = (o.lines || []).reduce((n, l) => n + lineQty(l), 0) * (num(o.pieces_per_unit) || 1);
  const tabs = [['order', 'Order entry', c.order], ['fabric', `Fabric & yarn · ${o.fabrics?.length || 0}`, c.fabric], ['trims', `Trims · ${o.accessories?.length || 0}`, c.trims]];
  return `<section class="card screen" id="order-${oi}" style="margin-bottom:14px">
    <div class="screen-head"><span class="title">Sample Order Entry${state.draft.orders.length > 1 ? ` · ${oi + 1}` : ''}</span>
      <nav class="tabs" role="tablist">${tabs.map(([t, n, k]) => `<button role="tab" class="${tab === t ? 'active' : ''}" aria-selected="${tab === t}" data-act="tab" data-oi="${oi}" data-tab="${t}">${n}${k ? ` <span class="count-badge err">${k}</span>` : ''}</button>`).join('')}</nav>
      <span class="spacer"></span><span class="pill brand">${fmt(pcs, 0)} pcs</span>
      ${state.draft.orders.length > 1 ? `<button class="ghost small" data-act="remove-order" data-oi="${oi}">Remove</button>` : ''}</div>
    <div class="screen-body">${tab === 'order' ? orderTab(o, oi) : tab === 'fabric' ? fabricTab(o, oi) : trimsTab(o, oi)}</div></section>`;
}

function srcOf(o, k) {
  const s = o.provenance?.[k];
  if (s?.cell && s.derivation && ['ship_date', 'order_date'].includes(k)) return dateChip(s);
  if (s) return source(s);
  return o[k] ? '<span class="chip calc">entered</span>' : '';
}

// A date read from a cell and converted: the chip keeps the cell and says how it was read (§7 of the spec).
function dateChip(s) {
  const where = `${s.sheet ? s.sheet + '!' : ''}${s.cell}`; const d = String(s.derivation);
  const ambiguous = /day\/month/.test(d);
  const how = ambiguous ? 'day/month?' : /month\/day/.test(d) ? 'month/day' : /Excel date/i.test(d) ? 'Excel date' : 'converted';
  return `<span class="chip cell date-note ${ambiguous ? 'ambiguous' : ''}" title="${esc(`${d}${ambiguous ? ' — check it' : ''} · read from ${where}`)}">${esc(where.length > 12 ? '…' + where.slice(-10) : where)} · ${how}</span>`;
}

// Where the quantity unit came from: the file's heading, the saved layout, or the person.
function unitSrc(o) {
  const p = o.provenance?.qty_unit; if (!o.qty_unit) return '';
  const said = String(p?.said ?? p?.value ?? '');
  const fileSays = UNIT_WORDS.PACK.test(said) ? 'PACK' : UNIT_WORDS.PCS.test(said) ? 'PCS' : null;
  if (state.unitTouched?.has(o) || !p || !['file', 'template', 'person'].includes(p.reason) || (p.reason === 'file' && fileSays && fileSays !== o.qty_unit)) return '<span class="chip calc">entered</span>';
  if (p.reason === 'template') return `<span class="chip calc" title="${esc(p.derivation || p.evidence || 'Restored from the saved layout')}">saved layout</span>`;
  if (p.reason === 'person') return `<span class="chip calc" title="${esc(p.derivation || '')}">chosen with the layout</span>`;
  const where = p.cell ? `${p.sheet ? p.sheet + '!' : ''}${p.cell}` : '';
  return `<span class="chip cell" title="${esc(`${p.evidence || ''}${where ? ' · read from ' + where : ''}`)}">${esc(where ? where + ' · ' : '')}“${esc(said.length > 18 ? said.slice(0, 17) + '…' : said)}”</span>`;
}

function field(o, oi, [k, text, req, list, type]) {
  if (k === '__orderno') return `<div class="f"><label class="f-label">${text}<span class="src"><span class="chip calc">automatic</span></span></label><input readonly value="Generated by Oracle on save"></div>`;
  const path = `orders.${oi}.${k}`; const v = o[k] ?? ''; const issue = at(path); void req;
  return `<div class="f ${cls(issue)}" data-field="${esc(path)}"><label class="f-label" for="f-${esc(path)}"><span>${esc(text)}</span>${isRequired(k) ? '<span class="req" title="Required">*</span>' : ''}<span class="src">${issue ? '' : srcOf(o, k)}</span></label>
    <input id="f-${esc(path)}" data-path="${esc(path)}" ${type ? `type="${type}"` : ''} value="${esc(v)}" ${list ? `list="list-${list}"` : ''} ${k === 'order_ref' ? 'placeholder="e.g. PK/26-27/S/1320" maxlength="30"' : ''} ${issue ? 'aria-invalid="true"' : ''}>${msgHtml(issue)}</div>`;
}

function orderTab(o, oi) {
  const basis = o.quantity_basis || '';
  const sum = ls => (ls || []).reduce((n, l) => n + lineQty(l), 0);
  const bIssue = at(`orders.${oi}.quantity_basis`);
  const packs = under(`orders.${oi}.packs`);
  return `<div class="block"><div class="block-head"><h3>Order header</h3><span class="sub">Same fields as the Axpert screen · filled from the file · the cell each value came from is shown on the right</span></div>
      <div class="form-grid">${HEADER.map(h => field(o, oi, h)).join('')}</div></div>
    <div class="block"><div class="block-head"><h3>Style &amp; size quantities</h3><span class="sub">${esc(o.po_number || '')}</span></div>
      <div class="qty-bar"><div class="qty-row"><span class="lbl" id="basis-lbl-${oi}">Quantities to save <span style="color:var(--err)">*</span></span>
        <div class="segmented ${cls(bIssue)}" role="radiogroup" aria-labelledby="basis-lbl-${oi}">
          ${(o.order_lines || []).length ? `<button role="radio" aria-checked="${basis === 'order_demo'}" class="${basis === 'order_demo' ? 'on' : ''}" data-act="basis" data-oi="${oi}" data-basis="order_demo">Buyer order · ${fmt(sum(o.order_lines), 0)} ${o.qty_unit === 'PACK' ? 'packs' : 'pcs'}</button>` : ''}
          ${(o.sample_lines || []).length ? `<button role="radio" aria-checked="${basis === 'sample' && o._manual !== true}" class="${basis === 'sample' && !o._manual ? 'on' : ''}" data-act="basis" data-oi="${oi}" data-basis="sample">Sample section · ${fmt(sum(o.sample_lines), 0)} ${o.qty_unit === 'PACK' ? 'packs' : 'pcs'}</button>` : ''}
          <button role="radio" aria-checked="${!!o._manual}" class="${o._manual ? 'on' : ''}" data-act="basis" data-oi="${oi}" data-basis="manual">Type them</button></div>
        ${bIssue ? `<div class="cell-msg ${cls(bIssue)}">${esc(strip(bIssue.message))}</div>` : ''}</div>
        ${unitRow(o, oi)}</div>
      ${matrix(o, oi)}
      ${packs.length ? `<div class="banner warning" style="margin-top:10px"><span class="b-ic">!</span><div>${packs.map(i => esc(strip(i.message))).join('<br>')}</div></div>` : ''}
      ${notesHtml(o, oi)}</div>`;
}

function notesHtml(o, oi) {
  // the unit and basis questions are asked on their fields, so they are not repeated here
  const onField = [o.provenance?.qty_unit?.question, 'Confirm whether these are sample quantities.'];
  const notes = [...new Set([...(oi === 0 ? state.draft.notes || [] : []), ...(o.warnings || []).filter(w => !onField.includes(w))])];
  return notes.length ? `<details class="notes"><summary class="muted small-text" style="cursor:pointer">Notes from reading the file (${notes.length})</summary><ul class="small-text">${notes.map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : '';
}

function unitRow(o, oi) {
  const pp = `orders.${oi}.pieces_per_unit`; const ppIssue = at(pp);
  if (!('qty_unit' in o)) {   // drafts saved before the unit question: the old pieces-per-unit field
    return `<div class="qty-row"><div class="f ${cls(ppIssue)}"><label class="f-label" for="ppu-${oi}">Pieces per unit</label><input id="ppu-${oi}" type="number" min="1" step="1" data-path="${pp}" value="${esc(o.pieces_per_unit ?? '')}" title="1 = quantities are pieces; 3 = each quantity is a pack of 3">${msgHtml(ppIssue)}</div></div>`;
  }
  const u = o.qty_unit || ''; const path = `orders.${oi}.qty_unit`; const issue = at(path);
  return `<div class="qty-row unit-row" data-field="${esc(path)}"><span class="lbl" id="unit-lbl-${oi}">Quantity unit <span style="color:var(--err)">*</span></span>
    <div class="segmented ${cls(issue)}" role="radiogroup" aria-labelledby="unit-lbl-${oi}" ${issue ? `aria-describedby="unit-msg-${oi}"` : ''}>
      ${[['PCS', 'Pieces'], ['PACK', 'Packs']].map(([v, l]) => `<button role="radio" aria-checked="${u === v}" class="${u === v ? 'on' : ''}" data-act="unit" data-oi="${oi}" data-unit="${v}">${l}</button>`).join('')}</div>
    ${u === 'PACK' ? `<div class="f ${cls(ppIssue)}"><label class="f-label" for="ppu-${oi}">Pieces in one pack <span class="req">*</span></label><input id="ppu-${oi}" type="number" min="2" step="1" data-path="${pp}" value="${esc(o.pieces_per_unit ?? '')}" ${ppIssue ? 'aria-invalid="true"' : ''}>${msgHtml(ppIssue)}</div>` : ''}
    ${issue ? `<div class="cell-msg ${cls(issue)}" id="unit-msg-${oi}">${esc(strip(issue.message))}</div>` : `<span class="unit-src">${unitSrc(o)}</span>`}</div>`;
}

function sizeNames(o) {
  const names = [];
  (o.lines || []).forEach(l => (l.sizes || []).forEach(s => { if (!names.includes(s.size ?? '')) names.push(s.size ?? ''); }));
  return names;
}

function fresh() { return !!state.draft.plan && !state.draft.plan.stale && !state.dirty; }

function msgRow(list, span, rowCls) {
  return list.length ? `<tr class="msg-row ${rowCls}"><td colspan="${span}"><div class="msg-wrap">${cellMsgs(list)}</div></td></tr>` : '';
}

function matrix(o, oi) {
  const lines = o.lines || []; const sizes = sizeNames(o); const ppu = num(o.pieces_per_unit) || 1;
  const colTotal = s => lines.reduce((n, l) => n + num(l.sizes.find(x => (x.size ?? '') === s)?.quantity), 0);
  const grand = lines.reduce((n, l) => n + lineQty(l), 0);
  const span = sizes.length + 7;
  return `<div class="tbl-wrap"><table class="grid-input matrix"><thead><tr><th style="width:44px">#</th><th style="min-width:190px">Garment / component</th><th style="min-width:210px">Colour / combo <span style="color:var(--err)">*</span></th>
      ${sizes.map(s => `<th class="size"><div class="size-head"><input data-sizecol="${oi}" data-old="${esc(s)}" value="${esc(s)}" aria-label="Size name" class="${s === '' ? 'err' : ''}" placeholder="size" title="${esc(s)} — click to rename"><button class="th-x" data-act="remove-size-col" data-oi="${oi}" data-size="${esc(s)}" title="Remove size ${esc(s)}" aria-label="Remove size ${esc(s)}">×</button></div></th>`).join('')}
      <th class="add-col"><input data-addsize="${oi}" placeholder="＋ size" aria-label="Add a size column"></th>
      <th class="num">Total</th><th class="num">File total</th><th></th></tr></thead><tbody>
    ${lines.map((l, li) => {
      const lp = `orders.${oi}.lines.${li}`; const rowIss = under(lp); const total = lineQty(l);
      const bad = l.declared_total != null && num(l.declared_total) !== total;
      const rowCls = rowIss.some(i => i.severity === 'error') ? 'row-err' : rowIss.length ? 'row-warn' : '';
      const colorIss = at(`${lp}.color`);
      return `<tr class="${rowCls} ${rowIss.length ? 'has-msg' : ''}" id="line-${oi}-${li}"><td><span class="rowno">${li + 1}</span></td>
        <td><input data-path="${lp}.label" value="${esc(l.label)}" title="${esc(l.label)}" aria-label="Garment for line ${li + 1}"></td>
        <td><input data-path="${lp}.color" list="list-color" value="${esc(l.color)}" class="${cls(colorIss)}" aria-label="Colour for line ${li + 1}" title="${esc(l.color)}${l.provenance?.color?.cell ? ' · from ' + esc(l.provenance.color.cell) : ''}"></td>
        ${sizes.map(s => { const si = l.sizes.findIndex(x => (x.size ?? '') === s); const sp = `${lp}.sizes.${si}`;
          const iss = si >= 0 ? (at(`${sp}.quantity`) || at(`${sp}.size`)) : null; const sz = l.sizes[si];
          return `<td class="num">${si >= 0 ? `<input type="number" min="0" step="1" data-path="${sp}.quantity" value="${esc(sz.quantity ?? '')}" class="${cls(iss)}" title="${esc(iss ? strip(iss.message) : sz.source?.cell ? 'From ' + (sz.source.sheet ? sz.source.sheet + '!' : '') + sz.source.cell : '')}" aria-label="${esc(s)} quantity line ${li + 1}">`
            : `<input type="number" min="0" step="1" data-newsize="${oi}|${li}" data-size="${esc(s)}" placeholder="–" aria-label="${esc(s)} quantity line ${li + 1}">`}</td>`; }).join('')}
        <td></td><td class="total" data-total="${oi}-${li}">${fmt(total, 0)}</td>
        <td class="num file-total ${l.declared_total == null ? '' : bad ? 'bad' : 'good'}">${l.declared_total == null ? '—' : `${fmt(l.declared_total, 0)} ${bad ? '≠' : '✓'}`}</td>
        <td><button class="icon-btn" data-act="remove-line" data-oi="${oi}" data-li="${li}" title="Remove line ${li + 1}" aria-label="Remove line ${li + 1}">✕</button></td></tr>
        ${msgRow(rowIss, span, rowCls)}`;
    }).join('') || `<tr><td colspan="${span}" class="empty">No lines yet. Add a colour line.</td></tr>`}
    </tbody><tfoot><tr><td></td><td>${o.qty_unit === 'PACK' ? 'Total packs' : 'Total pieces'}</td><td class="muted">${ppu > 1 ? `${o.qty_unit === 'PACK' ? `packs × ${ppu} pieces` : `× ${ppu} per unit`} = <b>${fmt(grand * ppu, 0)}</b> pcs` : ''}</td>${sizes.map(s => `<td class="num">${fmt(colTotal(s), 0)}</td>`).join('')}<td></td><td class="num">${fmt(grand, 0)}</td><td></td><td></td></tr></tfoot></table></div>
    <div class="tbl-actions"><button class="secondary small" data-act="add-line" data-oi="${oi}">＋ Add colour line</button><span class="muted small-text" style="align-self:center">Tip: type a new size name in “＋ size” to add a column.</span></div>`;
}

function lineOptions(o, sel, all = false) {
  return `<option value="">${all ? 'All garments' : '— choose —'}</option>` + (o.lines || []).map((l, i) => `<option value="${i + 1}" ${Number(sel) === i + 1 ? 'selected' : ''}>${i + 1}. ${esc((l.color || l.label || '').slice(0, 22))}</option>`).join('');
}

function refBanner(tables) {
  const ref = (state.draft.plan?.reference_only || []).filter(x => tables.includes(x.table));
  const m = state.mappingStatus || {};
  const unverified = tables.filter(t => m[t] && m[t] !== 'verified');
  if (!ref.length && !unverified.length) return '';
  return `<div class="banner info"><span class="b-ic">i</span><div>These rows are checked and shown for review. They are <b>not saved to Oracle yet</b> because the ${esc((unverified.length ? unverified : ref.map(x => x.table)).join(' / '))} mapping is not verified. An administrator can verify it in <b>Setup → Write mapping</b>.</div></div>`;
}

function fabricTab(o, oi) {
  const fabrics = o.fabrics || []; const derived = fresh() ? state.draft.plan.derived || {} : {};
  state.openFab ||= new Set();
  return `${refBanner(['ORDFAB', 'ORDYARN'])}<div class="block-head"><h3>Fabric planning</h3><span class="sub">Fabric per garment portion · kg = pieces × g/pc ÷ 1000 × rejection</span></div>
    <div class="tbl-wrap"><table class="grid-input fabric-table"><thead><tr><th>#</th><th>Garment line <span style="color:var(--err)">*</span></th><th>Portion</th><th>Fabric colour <span style="color:var(--err)">*</span></th><th>Quality</th><th>GSM</th><th>Dia</th><th>Content</th>
      <th class="num">g / pc <span style="color:var(--err)">*</span></th><th class="num">Rejection</th><th class="num">Pieces</th><th class="num">Kg to save</th><th class="num">File kg</th><th>Yarn</th><th></th></tr></thead><tbody>
    ${fabrics.map((f, fi) => {
      const p = `orders.${oi}.fabrics.${fi}`; const iss = under(p);
      const line = o.lines?.[num(f.line) - 1]; const pcs = line ? lineQty(line) * (num(o.pieces_per_unit) || 1) : 0;
      const kg = derived[p]?.total_kg ?? pcs * num(f.yy) / 1000 * (num(f.rejection) || 1);
      const ysum = (f.yarns || []).reduce((n, y) => n + num(y.percent), 0); const open = state.openFab.has(`${oi}.${fi}`);
      const inp = (k, extra = '') => `<input data-path="${p}.${k}" value="${esc(f[k] ?? '')}" class="${cls(at(`${p}.${k}`))}" ${extra} title="${esc(f.source?.[k]?.cell ? 'From ' + f.source[k].cell : '')}">`;
      const rc = iss.some(i => i.severity === 'error') ? 'row-err' : iss.length ? 'row-warn' : '';
      return `<tr class="${rc} ${iss.length ? 'has-msg' : ''}"><td><span class="rowno">${fi + 1}</span></td>
        <td class="w-line"><select data-path="${p}.line" data-type="int" class="${cls(at(`${p}.line`))}">${lineOptions(o, f.line)}</select></td>
        <td>${inp('portion')}</td><td class="w-wide">${inp('color', 'list="list-color"')}</td><td class="w-wide">${inp('quality')}</td><td>${inp('gsm')}</td><td>${inp('dia')}</td><td class="w-wide">${inp('content')}</td>
        <td class="num"><input type="number" step="any" min="0" data-path="${p}.yy" value="${esc(f.yy ?? '')}" class="${cls(at(`${p}.yy`))}" style="width:76px"></td>
        <td class="num"><input type="number" step="any" min="1" data-path="${p}.rejection" value="${esc(f.rejection ?? '')}" class="${cls(at(`${p}.rejection`))}" style="width:70px"></td>
        <td class="num">${fmt(pcs, 0)}</td><td class="num"><b>${fmt(Math.round(kg * 1000) / 1000)}</b></td><td class="num muted">${fmt(f.total)}</td>
        <td><button class="link yarn-btn" data-act="toggle-yarn" data-key="${oi}.${fi}">${(f.yarns || []).map(y => `${esc(y.count || '?')} ${fmt(y.percent)}%`).join(' + ') || 'Add yarn'} <span class="${Math.abs(ysum - 100) > 1e-9 && f.yarns?.length ? 'cell-msg err' : 'muted'}">${open ? '▴' : '▾'}</span></button></td>
        <td><button class="icon-btn" data-act="remove-fabric" data-oi="${oi}" data-fi="${fi}" title="Remove fabric row" aria-label="Remove fabric row ${fi + 1}">✕</button></td></tr>
        ${msgRow(iss, 15, rc)}
        ${open ? `<tr class="sub-row"><td colspan="15"><div class="block-head"><h3>Yarn split for fabric ${fi + 1}</h3><span class="sub">${f.yarns?.some(y => y.source?.derivation) ? 'Suggested from the fabric content — please confirm' : ''}</span></div>
          <table class="grid-input" style="max-width:640px"><thead><tr><th>Yarn count</th><th>Yarn colour</th><th class="num">%</th><th class="num">kg</th><th></th></tr></thead><tbody>
          ${(f.yarns || []).map((y, yi) => `<tr><td><input data-path="${p}.yarns.${yi}.count" value="${esc(y.count)}" class="${cls(at(`${p}.yarns.${yi}.count`))}"></td><td><input data-path="${p}.yarns.${yi}.color" list="list-color" value="${esc(y.color)}"></td>
            <td class="num"><input type="number" step="any" min="0" max="100" data-path="${p}.yarns.${yi}.percent" value="${esc(y.percent ?? '')}" style="width:80px"></td>
            <td class="num">${fmt(derived[`${p}.yarns.${yi}`]?.kg ?? kg * num(y.percent) / 100)}</td><td><button class="icon-btn" data-act="remove-yarn" data-path="${p}" data-yi="${yi}" aria-label="Remove yarn">✕</button></td></tr>`).join('')}
          </tbody><tfoot><tr><td colspan="2"><button class="link" data-act="add-yarn" data-path="${p}">＋ Add yarn</button></td><td class="num ${Math.abs(ysum - 100) > 1e-9 && f.yarns?.length ? 'cell-msg err' : ''}">${fmt(ysum)}%</td><td></td><td></td></tr></tfoot></table></td></tr>` : ''}`;
    }).join('') || '<tr><td colspan="15" class="empty">No fabric table was found in this file.</td></tr>'}
    </tbody></table></div><div class="tbl-actions"><button class="secondary small" data-act="add-fabric" data-oi="${oi}">＋ Add fabric row</button></div>`;
}

function trimsTab(o, oi) {
  const rows = o.accessories || []; const derived = fresh() ? state.draft.plan.derived || {} : {};
  state.openGroups ||= new Set();
  const groups = new Map();
  rows.forEach((a, ai) => { const k = `${a.block ?? 'm'}|${a.category}`; if (!groups.has(k)) groups.set(k, []); groups.get(k).push([a, ai]); });
  const only = !!state.onlyProblems;
  return `${refBanner(['ORDACC'])}<div class="block-head"><h3>Trims &amp; accessories</h3><span class="sub">Grouped exactly as the trim tables in the file</span><span class="spacer"></span>
      <label class="check"><input type="checkbox" data-act="only-problems" ${only ? 'checked' : ''}> Only rows that need attention</label></div>
    <div class="tbl-wrap"><table class="grid-input"><thead><tr><th>Garment</th><th>Size</th><th>Colour</th><th>Code</th><th>Description</th><th>Unit</th><th class="num">File qty</th><th class="num">To save</th><th></th></tr></thead><tbody>
    ${[...groups.entries()].map(([key, list]) => {
      const [first] = list[0]; const total = list.reduce((n, [a]) => n + num(a.total), 0);
      const issues = list.flatMap(([, ai]) => under(`orders.${oi}.accessories.${ai}`));
      if (only && !issues.length) return '';
      const open = state.openGroups.has(key) || (issues.some(i => i.severity === 'error'));
      const descs = new Set(list.map(([a]) => a.description));
      const descIss = list.map(([, ai]) => at(`orders.${oi}.accessories.${ai}.description`)).find(Boolean);
      return `<tr class="group-row ${open ? 'open' : ''}" data-act="toggle-group" data-key="${esc(key)}"><td colspan="4"><span class="caret">▸</span> ${esc(first.category || first.name || 'Accessory')} <span class="muted" style="font-weight:500">${esc(first.supplier || '')} · ${plural(list.length, 'row')}</span>
          ${issues.length ? ` <span class="count-badge ${issues.some(i => i.severity === 'error') ? 'err' : 'warn'}" title="${issues.length} to check">${issues.length}</span>` : ''}</td>
        <td>${descs.size === 1 ? `<input data-group-desc="${list.map(([, ai]) => ai).join(',')}" data-oi="${oi}" value="${esc(first.description)}" maxlength="120" class="${cls(descIss)}" title="Description saved for all ${list.length} rows">` : '<span class="muted">varies</span>'}</td>
        <td>${esc(first.uom || '')}</td><td class="num">${fmt(total)}</td><td></td><td></td></tr>
        ${descIss ? msgRow([descIss], 9, descIss.severity === 'error' ? 'row-err' : 'row-warn') : ''}
        ${open ? list.map(([a, ai]) => { const p = `orders.${oi}.accessories.${ai}`; const iss = under(p).filter(i => !i.path.endsWith('.description') || descs.size > 1);
          if (only && !iss.length) return '';
          const rc = iss.some(i => i.severity === 'error') ? 'row-err' : iss.length ? 'row-warn' : '';
          return `<tr class="${rc} ${iss.length ? 'has-msg' : ''}">
          <td><select data-path="${p}.line" data-type="int">${lineOptions(o, a.line, true)}</select></td>
          <td><input data-path="${p}.size" list="list-size" value="${esc(a.size)}" style="width:80px"></td><td><input data-path="${p}.color" list="list-color" value="${esc(a.color)}"></td>
          <td><input data-path="${p}.code" value="${esc(a.code)}" style="width:100px"></td>
          <td>${descs.size > 1 ? `<input data-path="${p}.description" value="${esc(a.description)}" class="${cls(at(`${p}.description`))}">` : ''}</td>
          <td><input data-path="${p}.uom" value="${esc(a.uom)}" class="${cls(at(`${p}.uom`))}" style="width:64px"></td>
          <td class="num"><input type="number" step="any" min="0" data-path="${p}.total" value="${esc(a.total ?? '')}" style="width:96px" class="${cls(at(`${p}.total`))}"></td>
          <td class="num">${derived[p] ? fmt(derived[p].quantity) : '—'}</td>
          <td><button class="icon-btn" data-act="remove-acc" data-oi="${oi}" data-ai="${ai}" aria-label="Remove row">✕</button></td></tr>${msgRow(iss, 9, rc)}`; }).join('') : ''}`;
    }).join('') || '<tr><td colspan="9" class="empty">No accessory tables were found in this file.</td></tr>'}
    </tbody></table></div>`;
}

function renderSide() {
  const d = state.draft; const p = !state.dirty && d.plan && !d.plan.stale;
  $('#review-side').hidden = !d.orders.length;   // nothing to check until the layout is read
  const errs = allIssues().filter(i => i.severity === 'error').length + fileIssues().length;
  const t = p ? p.totals : null;
  const pcs = d.orders.reduce((n, o) => n + (o.lines || []).reduce((m, l) => m + lineQty(l), 0) * (num(o.pieces_per_unit) || 1), 0);
  const kg = t ? t.fabric_kg : d.orders.reduce((n, o) => n + (o.fabrics || []).reduce((m, f) => m + num(f.total), 0), 0);
  const state_ = errs ? `<div class="ready-state todo"><span class="big-n">${errs}</span><span>${errs === 1 ? 'place needs' : 'places need'} your input<br><small>outlined in red</small></span></div>
      <button class="secondary" data-act="next-issue">Show me the next one ›</button>`
    : p ? '<div class="ready-state ok"><span class="big-n">✓</span><span>All checks passed<br><small>see the exact Oracle entry next</small></span></div>'
      : `<div class="ready-state warn"><span class="big-n">•</span><span>${state.dirty ? 'Changes made' : 'Not checked yet'}<br><small>checking never writes to Oracle</small></span></div>`;
  $('#review-side').innerHTML = `${state_}
    <div class="bar-stats"><span><b>${fmt(pcs, 0)}</b> pcs</span><span><b>${d.orders.reduce((n, o) => n + (o.lines || []).length, 0)}</b> colour lines</span>
      <span><b>${new Set(d.orders.flatMap(o => sizeNames(o))).size}</b> sizes</span><span><b>${fmt(kg)}</b> kg fabric</span><span><b>${d.orders.reduce((n, o) => n + (o.accessories || []).length, 0)}</b> trim rows</span></div>
    <button class="primary big" id="prepare" ${d.result || !can('preview') ? 'disabled' : ''}>${p && !errs ? 'Open the approval view →' : 'Check & continue →'}</button>`;
}

function askFile() {
  const body = drawer('Ask about this file', `<p class="muted">The local AI explains this upload using only what was read from the file and the last check. It cannot change or save anything.</p>
    <form id="ask-form" style="display:grid;gap:8px"><textarea id="ask" rows="3" maxlength="1000" placeholder="Why is the sample quantity zero?" required></textarea>
    <button class="primary" type="submit">Ask</button></form><div id="answer" class="answer-box" hidden></div>`);
  $('#ask-form', body).onsubmit = e => { e.preventDefault(); ask($('#ask').value); };
  $('#ask').focus();
}

export function nextIssue() {
  const d = state.draft;
  const order = ['order', 'fabric', 'trims'];
  let els = $$('#orders .f.err, #orders input.err, #orders select.err, #orders tr.row-err, #orders .segmented.err');
  if (!els.length) {
    // switch to the first tab that has errors
    for (let oi = 0; oi < d.orders.length; oi++) {
      const c = counts(oi); const t = order.find(k => c[k]);
      if (t) { state.tab[oi] = t; renderReview(); els = $$('#orders .f.err, #orders input.err, #orders select.err, #orders tr.row-err, #orders .segmented.err'); break; }
    }
  }
  if (!els.length) { if (fileIssues().length) window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
  const el = els[nav++ % els.length];
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const target = el.matches('input,select') ? el : el.querySelector('input.err, select.err, input, select, button');
  (el.closest('.f') || el).classList.add('flash'); setTimeout(() => (el.closest('.f') || el).classList.remove('flash'), 2300);
  target?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- editing
function setPath(path, value) {
  const keys = path.split('.'); let obj = state.draft;
  keys.slice(0, -1).forEach(k => { obj = obj[k]; });
  obj[keys.at(-1)] = value;
}
function getPath(path) { return path.split('.').reduce((o, k) => o?.[k], state.draft); }

function clearIssues(path) {
  state.planIssues = (state.planIssues || []).filter(i => !i.path || !(i.path === path || path.startsWith(i.path + '.') || i.path.startsWith(path + '.')));
}

function markDirty() {
  state.dirty = true;
  if (state.draft.plan) state.draft.plan = { ...state.draft.plan, stale: true };
}

function refreshInPlace(el) {
  // update this field's red state and the side panel without re-rendering the whole screen (keeps focus)
  const f = el.closest('.f');
  if (f) {
    const issue = at(el.dataset.path);
    f.classList.remove('err', 'warn'); if (issue) f.classList.add(cls(issue));
    f.querySelector('.f-msg')?.remove(); if (issue) f.insertAdjacentHTML('beforeend', msgHtml(issue));
  } else if (el.dataset.path) {
    const issue = at(el.dataset.path); el.classList.remove('err', 'warn'); if (issue) el.classList.add(cls(issue));
  }
  $$('#orders .screen').forEach((s, oi) => { const c = counts(oi); $$('.tabs button', s).forEach(b => { const n = c[b.dataset.tab]; const badge = b.querySelector('.count-badge'); if (n && !badge) b.insertAdjacentHTML('beforeend', ` <span class="count-badge err">${n}</span>`); else if (badge) { if (n) badge.textContent = n; else badge.remove(); } }); });
  renderSide();
}

export function onInput(e) {
  const el = e.target; const d = state.draft; if (!d) return;
  if (el.dataset.groupDesc != null) {
    const o = d.orders[Number(el.dataset.oi)];
    el.dataset.groupDesc.split(',').forEach(ai => { o.accessories[Number(ai)].description = el.value; clearIssues(`orders.${el.dataset.oi}.accessories.${ai}.description`); });
    el.classList.remove('err', 'warn'); markDirty(); renderSide(); return;
  }
  if (el.dataset.newsize) {   // typing into an empty size cell creates that size for the line
    const [oi, li] = el.dataset.newsize.split('|').map(Number); const line = d.orders[oi].lines[li];
    line.sizes.push({ size: el.dataset.size, quantity: el.value === '' ? null : Number(el.value) }); line.declared_total = null;
    el.dataset.path = `orders.${oi}.lines.${li}.sizes.${line.sizes.length - 1}.quantity`; delete el.dataset.newsize;
    clearIssues(`orders.${oi}.lines.${li}.sizes`); markDirty(); updateTotals(oi, li); refreshInPlace(el); return;
  }
  const path = el.dataset.path; if (!path) return;
  let v = el.value;
  if (el.type === 'number') v = v === '' ? null : Number(v);
  if (el.dataset.type === 'int') v = v === '' ? null : Number(v);
  setPath(path, v); clearIssues(path); markDirty();
  const m = path.match(/^orders\.(\d+)\.lines\.(\d+)\.sizes\.\d+\.quantity$/);
  if (m) { clearIssues(`orders.${m[1]}.lines.${m[2]}.sizes`); updateTotals(Number(m[1]), Number(m[2])); }
  refreshInPlace(el);
}

function updateTotals(oi, li) {
  const line = state.draft.orders[oi].lines[li];
  const t = $(`[data-total="${oi}-${li}"]`); if (t) t.textContent = fmt(lineQty(line), 0);
  const row = $(`#line-${oi}-${li}`);
  if (row && lineQty(line) > 0) { row.classList.remove('row-err'); row.querySelectorAll('.cell-msg').forEach(x => { if (/zero/.test(x.textContent)) x.remove(); }); }
}

export function onChange(e) {
  const el = e.target; const d = state.draft; if (!d) return;
  if (el.dataset.sizecol != null) {
    const oi = Number(el.dataset.sizecol); const old = el.dataset.old; const name = el.value.trim();
    d.orders[oi].lines.forEach(l => l.sizes.forEach(s => { if ((s.size ?? '') === old) s.size = name; }));
    clearIssues(`orders.${oi}.lines`); markDirty(); renderReview(); return;
  }
  if (el.dataset.addsize != null) {
    const oi = Number(el.dataset.addsize); const name = el.value.trim(); if (!name) return;
    d.orders[oi].lines.forEach(l => { if (!l.sizes.some(s => s.size === name)) l.sizes.push({ size: name, quantity: null }); l.declared_total = null; });
    markDirty(); renderReview(); return;
  }
  if (el.tagName === 'SELECT' && el.dataset.path) { onInput(e); renderReview(); }
}

export async function onAction(btn) {
  const a = btn.dataset.act; const d = state.draft; if (!a || !d) return false;
  const oi = Number(btn.dataset.oi); const o = d.orders[oi];
  const emptyLine = () => ({ label: '', color: '', declared_total: null, sizes: sizeNames(o || {}).map(s => ({ size: s, quantity: null })) });
  switch (a) {
    case 'tab': state.tab[oi] = btn.dataset.tab; renderReview(); return true;
    case 'next-issue': nextIssue(); return true;
    case 'ask-file': askFile(); return true;
    case 'new-upload': if (state.dirty && !await confirmDialog('Discard your corrections?', '<p>Corrections not yet checked will be lost.</p>', 'Discard')) return true; resetWorkspace(); return true;
    case 'show-mapping': state.showMapping = true; renderReview(); $('#mapping-panel').scrollIntoView({ behavior: 'smooth' }); return true;
    case 'hide-mapping': state.showMapping = false; renderReview(); return true;
    case 'unit': {
      const u = btn.dataset.unit; o.qty_unit = u;
      if (u === 'PCS') o.pieces_per_unit = 1;
      if (u === 'PACK' && !(Number.isInteger(Number(o.pieces_per_unit)) && Number(o.pieces_per_unit) >= 2)) o.pieces_per_unit = null;
      (state.unitTouched ||= new WeakSet()).add(o);
      clearIssues(`orders.${oi}.qty_unit`); clearIssues(`orders.${oi}.pieces_per_unit`);
      markDirty(); renderReview(); if (u === 'PACK' && o.pieces_per_unit == null) $(`#ppu-${oi}`)?.focus();
      return true;
    }
    case 'toggle-yarn': { const k = btn.dataset.key; state.openFab.has(k) ? state.openFab.delete(k) : state.openFab.add(k); renderReview(); return true; }
    case 'toggle-group': { const k = btn.dataset.key; state.openGroups.has(k) ? state.openGroups.delete(k) : state.openGroups.add(k); renderReview(); return true; }
    case 'only-problems': state.onlyProblems = btn.checked; renderReview(); return true;
    case 'basis': {
      const b = btn.dataset.basis; o._manual = b === 'manual';
      if (b === 'sample' && o.sample_lines?.length) o.lines = clone(o.sample_lines);
      if (b === 'order_demo' && o.order_lines?.length) o.lines = clone(o.order_lines);
      o.quantity_basis = b === 'manual' ? 'sample' : b;
      if (b === 'manual') { if (!o.lines.length) o.lines = [emptyLine()]; o.lines.forEach(l => { l.sizes.forEach(s => { s.quantity = null; }); l.declared_total = null; }); }
      clearIssues(`orders.${oi}.quantity_basis`); clearIssues(`orders.${oi}.lines`);
      break;
    }
    case 'add-order': d.orders.push({ buyer: '', style: '', description: '', po_number: '', order_ref: '', order_date: '', ship_date: '', exporter: state.health?.settings?.company_exporter || '', sample_type: state.health?.settings?.default_sample_type || '', quantity_basis: 'sample', _manual: true, qty_unit: '', pieces_per_unit: null, lines: [{ label: '', color: '', declared_total: null, sizes: [{ size: '', quantity: null }] }], fabrics: [], accessories: [], provenance: {}, warnings: [] }); break;
    case 'remove-order': if (!await confirmDialog('Remove this order?', '<p>It will not be imported. You can add it back by uploading again.</p>', 'Remove')) return true; d.orders.splice(oi, 1); break;
    case 'add-line': o.lines.push(emptyLine()); break;
    case 'remove-line': o.lines.splice(Number(btn.dataset.li), 1); clearIssues(`orders.${oi}.lines`); break;
    case 'remove-size-col': { const s = btn.dataset.size; o.lines.forEach(l => { l.sizes = l.sizes.filter(x => (x.size ?? '') !== s); l.declared_total = null; }); clearIssues(`orders.${oi}.lines`); break; }
    case 'add-fabric': (o.fabrics ||= []).push({ component: '', line: o.lines.length === 1 ? 1 : null, portion: 'BODY', color: '', quality: '', gsm: '', dia: '', content: '', yy: null, rejection: 1.03, yarns: [{ count: '', color: 'GREIGE', percent: 100 }] }); break;
    case 'remove-fabric': o.fabrics.splice(Number(btn.dataset.fi), 1); clearIssues(`orders.${oi}.fabrics`); break;
    case 'add-yarn': (getPath(btn.dataset.path).yarns ||= []).push({ count: '', color: 'GREIGE', percent: null }); break;
    case 'remove-yarn': getPath(btn.dataset.path).yarns.splice(Number(btn.dataset.yi), 1); clearIssues(btn.dataset.path + '.yarns'); break;
    case 'remove-acc': o.accessories.splice(Number(btn.dataset.ai), 1); clearIssues(`orders.${oi}.accessories`); break;
    default: return false;
  }
  markDirty(); renderReview(); return true;
}

async function saveEdits() {
  if (!state.dirty) return;
  const orders = state.draft.orders.map(({ _manual, ...o }) => o);
  const manual = state.draft.orders.map(o => o._manual);
  state.draft = await api(`/api/drafts/${id(state.draft.id)}`, { method: 'PUT', body: { version: state.draft.version, orders } });
  state.draft.orders.forEach((o, i) => { if (manual[i]) o._manual = true; });
  state.dirty = false;
}

// Returns {ok} or {rejected: [...]} (409: each wrong choice, shown on its field in the layout check).
async function applyMapping(body) {
  return busy('Reading the file with this layout…', async () => {
    await saveEdits();
    try { state.draft = await api(`/api/drafts/${id(state.draft.id)}/map`, { method: 'POST', body: { version: state.draft.version, ...body } }); }
    catch (e) {
      if (e.status === 409 && e.data?.rejected?.length) return { rejected: e.data.rejected };
      if (e.status === 409 && /pack/i.test(e.message)) return { rejected: [{ field: 'qty_unit', reason: e.message }] };
      throw e;
    }
    state.showMapping = false; state.tab = {}; state.planIssues = [];
    const t = state.draft.template;
    notify(body.save_template && t ? `Layout saved as “${t.name}” (version ${t.version}). The next file with this layout is read automatically.` : 'Layout applied. Check the order details.', 'success');
    renderReview();
    return { ok: true };
  });
}

export async function prepare() {
  const d = state.draft;
  if (d.plan && !d.plan.stale && !state.dirty && !d.plan.errors.length) return showApprove();
  await busy('Checking every value against Oracle masters and rules…', async () => {
    await saveEdits();
    const manual = state.draft.orders.map(o => o._manual);
    state.draft = await api(`/api/drafts/${id(d.id)}/preview`, { method: 'POST', body: { version: state.draft.version } });
    state.draft.orders.forEach((o, i) => { if (manual[i]) o._manual = true; });
    const p = state.draft.plan; state.planIssues = p.issues || [];
    if (p.errors.length) {
      renderReview(); nav = 0;
      notify(`${plural(p.errors.length, 'thing')} to fix — each one is outlined in red where it belongs.`, 'warning');
      nextIssue();
    } else showApprove();
  });
}

export async function ask(question) {
  await busy('Asking the local assistant…', async () => {
    const r = await api(`/api/drafts/${id(state.draft.id)}/assist`, { method: 'POST', body: { question } });
    $('#answer').textContent = r.answer; $('#answer').hidden = false;
  });
}

// ---------------------------------------------------------------- approve
export function showApprove() {
  const d = state.draft; const plan = d.plan;
  const inserts = plan.operations.filter(o => o.action === 'insert'); const reuse = plan.operations.filter(o => o.action === 'reuse');
  const tables = new Set(inserts.map(o => o.table));
  const canApprove = plan.live && !plan.errors.length && can('approve');
  const root = $('#stage-approve');
  root.innerHTML = `
    <div class="row" style="margin-bottom:12px"><button class="ghost" data-act2="back-review">← Back to the order entry</button><span class="spacer"></span>
      <span class="muted small-text">${esc(d.filename)} · ${esc(d.orders.map(o => `${o.order_ref} · ${o.style}`).join(', '))}</span></div>
    ${plan.live ? '' : '<div class="banner warning"><span class="b-ic">!</span><div>Oracle is not connected. This view used the saved database structure, so existing masters were not checked. Start the database and check again to approve.</div></div>'}
    <div class="kpis">${[['New records', inserts.length], ['Oracle tables', tables.size], ['Existing masters reused', reuse.length], ['Pieces', fmt(plan.totals.pieces, 0)], ['Fabric (kg)', fmt(plan.totals.fabric_kg)]].map(([a, b]) => `<div class="card kpi"><small>${a}</small><strong>${b}</strong></div>`).join('')}</div>
    ${plan.warnings.length ? `<div class="banner warning"><span class="b-ic">!</span><div><b>Please read before approving</b><ul>${plan.warnings.map(w => `<li>${esc(d.orders.length === 1 ? strip(w) : w)}</li>`).join('')}</ul></div></div>` : ''}
    ${flowCard('Where this order will be saved', 'Nothing is in Oracle yet. Click a table to see the exact rows and values that will be written.')}
    <div class="action-bar"><p>${canApprove ? `Accepting writes these ${plural(inserts.length, 'record')} in <b>one Oracle transaction</b>, reads every row back to confirm it, and records who approved it. If anything fails, nothing is saved.` :
      !plan.live ? 'Saving is disabled until Oracle is connected.' : plan.errors.length ? 'Fix the outlined fields first.' : 'Your role cannot approve. An approver or administrator must accept this entry.'}</p>
      <button class="primary big" data-act2="approve" ${canApprove ? '' : 'disabled'}>Accept and save to Oracle →</button></div>`;
  mountFlow(root, plan.graph || { nodes: [], edges: [] }, { planned: true, columns: plan.columns, onReferenceOnly: t => backToReview(t === 'ORDACC' ? 'trims' : 'fabric') });
  stage('approve');
}

export function backToReview(tab) {
  if (tab) state.draft.orders.forEach((_, i) => { state.tab[i] = tab; });
  renderReview(); stage('review');
}

export async function approve() {
  const d = state.draft; const plan = d.plan;
  const inserts = plan.operations.filter(o => o.action === 'insert');
  const counts = {}; inserts.forEach(o => { counts[o.table] = (counts[o.table] || 0) + 1; });
  const ok = await confirmDialog('Save this order to Oracle?', `<p><b>${esc(d.orders.map(o => `${o.order_ref} · ${o.style} · ${o.buyer}`).join(' / '))}</b></p>
    <ul>${Object.entries(counts).map(([t, n]) => `<li><b>${esc(t)}</b>: ${n} new record${n === 1 ? '' : 's'}</li>`).join('')}</ul>
    <p class="muted">This cannot be undone from POPPYS AI. Existing orders are never overwritten.</p>`, 'Yes, save to Oracle');
  if (!ok) return;
  await busy('Saving in one transaction and reading every row back…', async () => {
    let result;
    try { result = await api(`/api/drafts/${id(d.id)}/accept`, { method: 'POST', body: { version: d.version, plan_hash: plan.hash } }); }
    catch (e) {
      try { state.draft = await api(`/api/drafts/${id(d.id)}`); } catch (_) { /* keep current */ }
      throw new Error(`${e.message}\nNothing was partially saved. Check again and retry.`);
    }
    state.draft.result = result; state.saved = true;
    await showSaved(result);
  });
}

// ---------------------------------------------------------------- saved
export async function showSaved(result, target = $('#stage-saved'), standalone = false) {
  target.innerHTML = `<section class="card saved-hero"><span class="tick">✓</span><div><span class="eyebrow">${result.already_committed ? 'Already saved' : 'Saved and verified'}</span>
      <h1>${result.already_committed ? 'This order is in Oracle.' : 'The order is saved in Oracle.'}</h1>
      <p class="muted">Approved by ${esc(result.approver || '—')} · ${esc(new Date(result.committed_at).toLocaleString())} · ${plural((result.rows || []).length, 'new record')}${(result.reused || []).length ? `, ${plural(result.reused.length, 'existing master')} linked` : ''}</p>
      <div class="ids">${(result.orders || []).map(o => `<span><b>${esc(o.order_ref)}</b> · ORDMASID <code>${esc(o.ordmasid)}</code></span>`).join('')}</div></div>
      <div style="display:grid;gap:8px">${(result.orders || []).map(o => `<button class="secondary" data-explore-order="${esc(o.ordmasid)}">Open in Database</button>`).join('')}${standalone ? '' : '<button class="primary" data-act2="another">Start another order</button>'}</div></section>
    ${flowCard('Where your data was saved', 'Reading the rows back from Oracle…', '<span class="pill" data-flow-pill>checking</span>')}
    ${(result.reference_only || []).length ? `<div class="banner info" style="margin-top:12px"><span class="b-ic">i</span><div>Not saved (mapping not verified yet): ${result.reference_only.map(r => `${esc(r.business)} (${r.rows} rows)`).join(', ')}.</div></div>` : ''}`;
  if (!standalone) stage('saved');
  try {
    const graph = await api(`/api/history/${id(result.job_id)}/graph`);
    target.querySelector('[data-flow-sub]').textContent = `${plural(graph.nodes.length, 'table')} · the values below come from a fresh SELECT, not from the save receipt`;
    const pill = target.querySelector('[data-flow-pill]');
    pill.textContent = graph.verified ? '✓ Verified in Oracle' : '⚠ Differences found'; pill.className = `pill ${graph.verified ? 'ok' : 'err'}`;
    mountFlow(target, graph, { planned: false });
  } catch (e) {
    target.querySelector('[data-flow-sub]').textContent = `Could not read back from Oracle: ${e.message}`;
    const pill = target.querySelector('[data-flow-pill]'); pill.textContent = 'not verified'; pill.className = 'pill warn';
  }
}

export function bindWorkspace() {
  const root = $('#stage-review');
  root.addEventListener('input', onInput);
  root.addEventListener('change', e => { if (e.target.dataset.act === 'only-problems') return onAction(e.target); onChange(e); });
  root.addEventListener('click', async e => {
    if (state.busy) return;
    if (e.target.closest('#prepare')) return prepare();
    if (e.target.closest('input, select, textarea')) return;
    const b = e.target.closest('[data-act]'); if (!b) return;
    await onAction(b);
  });
  root.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.dataset.addsize != null) { e.preventDefault(); e.target.blur(); }
    const row = e.target.closest('tr.group-row'); if (row && (e.key === 'Enter' || e.key === ' ') && e.target === row) { e.preventDefault(); onAction(row); }
  });
}
