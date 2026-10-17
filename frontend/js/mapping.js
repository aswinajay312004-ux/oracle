// Layout check ("show us once") for unfamiliar or changed buyer layouts. The field matcher (and the local AI,
// when it runs) proposes where each order field is; every proposal shows its confidence and evidence, wrong
// ones are listed as "Not used" with the reason, and the person confirms once. Saved as that buyer's template.
import { $, $$, esc, show, notify, busy, state } from './util.js';
import { api, id, orderFields } from './api.js';

const PRIMARY = ['buyer', 'style', 'description', 'po_number', 'order_ref', 'order_date', 'ship_date', 'season', 'merch'];
const DEFAULTED = ['exporter', 'sample_type'];   // filled from Settings when the file does not say
const MATRIX = [['label_col', 'Garment / style'], ['color_col', 'Colour'], ['first_size_col', 'First size', true], ['last_size_col', 'Last size', true], ['total_col', 'Total']];
const FLAT_ORDER = ['size', 'quantity', 'color', 'style', 'buyer', 'po_number', 'ship_date', 'season', 'merch', 'description', 'order_ref'];
const CONF = { high: 'High', medium: 'Medium', low: 'Low' };
const BY = { matcher: 'the field matcher', ai: 'the local AI', both: 'the field matcher and the local AI (they agree)', person: 'you' };
const letter = n => { let s = ''; n = Number(n); while (n > 0) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; };
const colnum = s => { s = String(s || '').trim().toUpperCase(); if (!/^[A-Z]{1,3}$/.test(s)) return null; return [...s].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0); };
const parseRef = s => { const m = /^([A-Z]{1,3})(\d{1,6})$/.exec(String(s || '').trim().toUpperCase()); return m ? { c: colnum(m[1]), r: Number(m[2]) } : null; };
const norm = s => String(s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
const clip = (v, n = 40) => { const t = show(v); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
const emptyDef = () => ({ fields: {}, table: { type: 'matrix' } });

let ctx = null;

export function renderMapping(container, draft, apply) {
  if (ctx?.draftId !== draft.id) {
    const sug = draft.suggestion || null;
    const sheet = sug?.sheet || draft.sheets.find(s => !s.hidden)?.name || draft.sheets[0]?.name;
    ctx = { draftId: draft.id, sheet, sug, def: sug ? structuredClone(sug.definition) : emptyDef(), bySheet: {}, bad: [], pick: null,
      unit: unitFrom(sug, draft), name: '', nameTouched: false, save: true, more: false, templates: null };
    if (!ctx.def.table) ctx.def.table = { type: 'matrix' };
  }
  Object.assign(ctx, { draft, apply, container, fields: state.fields || [] });
  shell();
  if (!state.fields) orderFields().then(f => { state.fields = f; ctx.fields = f; if (ctx.container.isConnected) drawForm(); }).catch(() => {});
  if (!ctx.templates) api('/api/templates').then(t => { ctx.templates = t; if (ctx.container.isConnected) drawSave(); }).catch(() => { ctx.templates = []; });
  autoPropose();
}

// Without the local AI the matcher's proposal is cheap: fetch it for a sheet that has none yet.
function autoPropose() {
  const name = ctx.sheet; const draftId = ctx.draftId;
  const run = () => { if (ctx.draftId !== draftId || ctx.sheet !== name || ctx.sug || ctx.proposed?.[name] || !ctx.container.isConnected) return; if (state.busy) return setTimeout(run, 150); propose(false); };
  if (!ctx.sug && !aiOn() && !ctx.proposed?.[name]) setTimeout(run, 0);
}

const aiOn = () => !!state.health?.ollama?.available;
// The file's own evidence first; else a unit already decided on this draft (saved layout or review answer) is kept.
function unitFrom(sug, draft = ctx?.draft) {
  const u = sug?.unit || {};
  if (u.value) return { qty_unit: u.value, pieces_per_unit: u.pieces_per_unit ?? null, touched: false, origin: 'file' };
  const units = new Set((draft?.orders || []).map(o => `${o.qty_unit || ''}|${o.qty_unit === 'PCS' ? 1 : o.pieces_per_unit ?? ''}`));
  const [one] = units; const [q, n] = (one || '|').split('|');
  if (units.size === 1 && (q === 'PCS' || (q === 'PACK' && Number(n) >= 2))) return { qty_unit: q, pieces_per_unit: Number(n), touched: false, origin: 'draft' };
  return { qty_unit: '', pieces_per_unit: null, touched: false, origin: null };
}
function sheet() { return ctx.draft.sheets.find(s => s.name === ctx.sheet); }
function cells() {
  const s = sheet();
  if (ctx.cellsFor !== s) { ctx.cellsFor = s; ctx.cells = new Map((s?.cells || []).map(c => [`${c.row}:${c.col}`, c.value])); }
  return ctx.cells;
}
const at = (r, c) => cells().get(`${r}:${c}`);
const atRef = ref => { const p = parseRef(ref); return p ? at(p.r, p.c) : undefined; };
const fieldOf = k => ctx.fields.find(f => f.key === k);
const labelOf = k => fieldOf(k)?.label || ({ table: 'Size table', size: 'Size', quantity: 'Quantity', color: 'Colour / combo' })[k] || k.replace(/_/g, ' ');
const isLabel = v => typeof v === 'string' && ctx.fields.some(f => (f.aliases || []).some(a => norm(a) === norm(v)));

// ---------------------------------------------------------------- shell
function shell() {
  const d = ctx.draft; const s = ctx.sug;
  const refused = (d.templates_refused || []).filter(t => t.sheet === ctx.sheet || !ctx.sheet);
  ctx.container.innerHTML = `<div class="card-head map-head"><div class="map-title"><span class="eyebrow">${refused.length ? 'Layout changed · check it once' : d.orders.length ? 'Layout check' : 'New layout · check it once'}</span>
      <h2 id="map-h">Check where the order is in this file</h2>
      <p class="muted" id="map-expl">${esc(s ? s.explanation : 'Click Pick next to a field, then click its value in the sheet. Saved as a template, the next file from this buyer is read automatically.')}</p></div>
    <div class="map-actions">${s ? `<span class="pill ${s.source === 'matcher' ? 'brand' : 'master'}" title="Every proposal was checked: dates must be dates, quantities numbers, sizes sizes">Proposed by ${esc(s.source === 'matcher' ? 'the field matcher' : s.source)}</span>` : ''}
      ${aiOn() ? '<button type="button" class="secondary small" data-m="ai">Suggest with local AI</button>' : s ? '' : '<button type="button" class="secondary small" data-m="propose">Find the fields for me</button>'}
      ${d.orders.length ? '<button type="button" class="ghost small" data-act="hide-mapping">Close</button>' : ''}</div></div>
    ${(d.notes || []).length ? `<div class="banner info map-notes"><span class="b-ic">i</span><div>${d.notes.map(n => `<div>${esc(n)}</div>`).join('')}</div></div>` : ''}
    <div class="mapgrid"><div class="mapform" id="mapform"></div>
      <div class="map-right"><div class="map-sheetbar"><label class="field">Sheet / page<select id="map-sheet">${d.sheets.map(x => `<option value="${esc(x.name)}" ${x.name === ctx.sheet ? 'selected' : ''}>${esc(x.name)}${x.hidden ? ' (hidden)' : ''}</option>`).join('')}</select></label>
        <div class="map-legend" aria-hidden="true"><span class="lg pk">order detail</span><span class="lg hdr">table header</span><span class="lg col">columns read</span><span class="lg rej">not used</span></div></div>
        <p class="muted small-text" id="pick-hint" aria-live="polite">Highlighted cells are what will be read.</p><div class="sheet-grid" id="sheet-grid"></div></div></div>`;
  drawForm(); drawGrid(); bind();
}

function bind() {
  const root = ctx.container;
  root.onclick = e => {
    const b = e.target.closest('[data-m],[data-pick],[data-use],[data-ttype],[data-unit],[data-goto]'); if (!b || state.busy) return;
    if (b.dataset.m === 'ai') return propose(true);
    if (b.dataset.m === 'propose') return propose(false);
    if (b.dataset.m === 'more') { ctx.more = !ctx.more; return drawForm(); }
    if (b.dataset.m === 'morecols') { ctx.moreCols = !ctx.moreCols; return refreshTable(); }
    if (b.dataset.m === 'apply') return submit();
    if (b.dataset.pick) return startPick(b.dataset.pick);
    if (b.dataset.use) { setField(b.dataset.use, b.dataset.cell); drawForm(); drawGrid(); return focusTarget(`field:${b.dataset.use}`); }
    if (b.dataset.ttype) { if (ctx.def.table?.type !== b.dataset.ttype) { ctx.def.table = { type: b.dataset.ttype, header_row: ctx.def.table?.header_row ?? null, ...(b.dataset.ttype === 'flat' ? { columns: {} } : {}) }; clearBad(t => t.startsWith('col:')); } drawForm(); drawGrid(); return; }
    if (b.dataset.unit != null) { setUnit(b.dataset.unit); return; }
    if (b.dataset.goto) return focusTarget(b.dataset.goto);
  };
  root.oninput = e => {
    const el = e.target;
    if (el.dataset.mf) { setField(el.dataset.mf, el.value); refreshRow(`field:${el.dataset.mf}`); drawGrid(); drawSave(); }
    else if (el.dataset.mt) { ctx.def.table.header_row = Number(el.value) || null; clearBad(t => t === 'row'); refreshTable(); drawGrid(); }
    else if (el.dataset.mc) { setCol(el.dataset.mc, colnum(el.value)); refreshRow(`col:${el.dataset.mc}`); drawGrid(); }
    else if (el.id === 'map-ppu') { ctx.unit.pieces_per_unit = el.value === '' ? null : Number(el.value); ctx.unit.touched = true; clearBad(t => t === 'unit'); refreshUnitMsg(); }
    else if (el.id === 'map-name') { ctx.name = el.value; ctx.nameTouched = true; drawVersion(); }
  };
  root.onchange = e => {
    if (e.target.id === 'map-sheet') switchSheet(e.target.value);
    if (e.target.id === 'map-save') { ctx.save = e.target.checked; $('#map-name', root).disabled = !ctx.save; drawVersion(); }
  };
  root.onkeydown = e => { if (e.key === 'Escape' && ctx.pick) { e.stopPropagation(); cancelPick(); } };
}

// ---------------------------------------------------------------- state changes
function setField(key, value) {
  const v = String(value || '').trim().toUpperCase();
  if (v) (ctx.def.fields ||= {})[key] = v; else delete ctx.def.fields?.[key];
  clearBad(t => t === `field:${key}`);
}
function setCol(key, n) {
  const t = ctx.def.table ||= { type: 'matrix' };
  if (t.type === 'flat') { t.columns ||= {}; if (n) t.columns[key] = n; else delete t.columns[key]; } else t[key] = n || null;
  clearBad(x => x === `col:${key}` || (['first_size_col', 'last_size_col'].includes(key) && /^col:(first|last)_size_col$/.test(x)));
}
function setUnit(u) {
  ctx.unit.qty_unit = u; ctx.unit.touched = true;
  if (u === 'PCS') ctx.unit.pieces_per_unit = 1;
  if (u === 'PACK' && !(ctx.unit.pieces_per_unit >= 2)) ctx.unit.pieces_per_unit = null;
  clearBad(t => t === 'unit'); drawUnit();
  if (u === 'PACK') $('#map-ppu', ctx.container)?.focus();
}
function clearBad(test) { ctx.bad = ctx.bad.filter(b => !b.targets.some(test)); }

function switchSheet(name) {
  ctx.bySheet[ctx.sheet] = { sug: ctx.sug, def: ctx.def };
  const saved = ctx.bySheet[name];
  ctx.sheet = name; ctx.pick = null; ctx.bad = [];
  ctx.sug = saved ? saved.sug : (ctx.draft.suggestion?.sheet === name ? ctx.draft.suggestion : null);
  ctx.def = saved ? saved.def : ctx.sug ? structuredClone(ctx.sug.definition) : emptyDef();
  if (!ctx.def.table) ctx.def.table = { type: 'matrix' };
  if (!ctx.unit.touched) ctx.unit = unitFrom(ctx.sug);
  shell(); autoPropose();
}

async function propose(useAi) {
  const sheetName = ctx.sheet;
  await busy(useAi ? 'Asking the local AI — every proposal is checked by the field matcher…' : 'Looking for the order fields in this sheet…', async () => {
    let r;
    try { r = await api(`/api/drafts/${id(ctx.draft.id)}/suggest`, { method: 'POST', body: { sheet: sheetName } }); }
    catch (e) {
      (ctx.proposed ||= {})[sheetName] = true;
      if (e.status === 409) { notify(`${e.message} Use Pick to show where each value is.`, 'warning'); return; }
      throw e;
    }
    (ctx.proposed ||= {})[sheetName] = true;
    if (ctx.sheet !== r.sheet) ctx.bySheet[ctx.sheet] = { sug: ctx.sug, def: ctx.def };
    ctx.sheet = r.sheet; ctx.sug = r; ctx.def = structuredClone(r.definition); ctx.bad = []; ctx.pick = null;
    if (!ctx.def.table) ctx.def.table = { type: 'matrix' };
    if (!ctx.unit.touched) ctx.unit = unitFrom(r);
    shell();
    notify(r.rejected?.length ? `${r.explanation} ${r.rejected.length} proposal${r.rejected.length === 1 ? ' was' : 's were'} not used — see why on the fields.` : r.explanation, 'success');
  });
}

// ---------------------------------------------------------------- picking cells
function startPick(target) {
  if (ctx.pick === target) return cancelPick();
  ctx.pick = target;
  $$('[data-target]', ctx.container).forEach(r => r.classList.toggle('picking', r.dataset.target === target));
  $$('[data-pick]', ctx.container).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.pick === target)));
  $('#sheet-grid', ctx.container).classList.add('picking');
  const [kind, key] = target.split(':');
  $('#pick-hint', ctx.container).innerHTML = `<b>Click the ${kind === 'row' ? 'header row of the size table' : kind === 'col' ? `column for ${esc(colLabel(key))}` : `cell that holds ${esc(labelOf(key))}`}</b> in the sheet · Esc cancels`;
}
function cancelPick() {
  const was = ctx.pick; ctx.pick = null;
  $$('.picking', ctx.container).forEach(r => r.classList.remove('picking'));
  $$('[data-pick]', ctx.container).forEach(b => b.setAttribute('aria-pressed', 'false'));
  $('#pick-hint', ctx.container).textContent = 'Highlighted cells are what will be read.';
  if (was) focusTarget(was);
}
function picked(r, c) {
  const target = ctx.pick; const [kind, key] = target.split(':');
  if (kind === 'field') setField(key, `${letter(c)}${r}`);
  if (kind === 'row') { ctx.def.table.header_row = r; clearBad(t => t === 'row'); }
  if (kind === 'col') { setCol(key, c); if (!ctx.def.table.header_row) ctx.def.table.header_row = r; }
  ctx.pick = null; $('#pick-hint', ctx.container).textContent = 'Highlighted cells are what will be read.';
  drawForm(); drawGrid(); focusTarget(target);
}
function focusTarget(target) {
  const row = $(`[data-target="${CSS.escape(target)}"]`, ctx.container);
  if (!row) return;
  if (row.closest('.mf-more[hidden]')) { ctx.more = true; drawForm(); return focusTarget(target); }
  row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  $('input', row)?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- the form
function proposal(key) { return ctx.sug?.fields?.[key] || null; }
function notUsed(key) { return (ctx.sug?.rejected || []).filter(r => r.field === key); }
function badFor(target) { return ctx.bad.filter(b => b.targets.includes(target)); }
function headerKeys() { return ctx.fields.filter(f => f.where === 'header' && f.kind !== 'unit').map(f => f.key); }

function drawForm() {
  const f = ctx.def.fields || {};
  const keys = headerKeys().length ? headerKeys() : PRIMARY;
  const main = keys.filter(k => PRIMARY.includes(k) || (fieldOf(k)?.required && !DEFAULTED.includes(k)) || f[k] || proposal(k) || notUsed(k).length || badFor(`field:${k}`).length);
  const rest = keys.filter(k => !main.includes(k));
  const rejected = ctx.sug?.rejected || [];
  $('#mapform', ctx.container).innerHTML = `
    ${rejected.length ? `<details class="mf-unused" open><summary>Not used (${rejected.length}) <span class="muted">— these proposals failed the checks</span></summary><ul>
      ${rejected.map(r => `<li><button type="button" class="link" data-goto="${esc(gotoOf(r))}">${esc(labelOf(r.field))}</button> <span class="muted">· ${esc(BY[r.source] ? `from ${BY[r.source]}` : 'proposal')}</span><br><span>${esc(sentence(r.reason))}</span></li>`).join('')}</ul></details>` : ''}
    <section class="mf-section" aria-labelledby="mf-h-details"><div class="mf-head"><h3 id="mf-h-details">Order details</h3><span class="muted small-text">cell that holds each value</span></div>
      ${main.map(fieldRow).join('')}
      ${rest.length ? `<button type="button" class="link mf-more-btn" data-m="more" aria-expanded="${ctx.more}">${ctx.more ? 'Fewer fields' : `More fields (${rest.length})`}</button><div class="mf-more" ${ctx.more ? '' : 'hidden'}>${rest.map(fieldRow).join('')}</div>` : ''}</section>
    <section class="mf-section" id="mf-table" aria-labelledby="mf-h-table"></section>
    <section class="mf-section" id="mf-unit" aria-labelledby="mf-h-unit"></section>
    <section class="mf-section mf-save" id="mf-save"></section>`;
  refreshTable(); drawUnit(); drawSave();
}

function gotoOf(r) {
  const k = r.field;
  if (headerKeys().includes(k) && /^[A-Z]{1,3}\d+$/.test(r.cell || '')) return `field:${k}`;
  if (k === 'table') return 'row';
  if (ctx.def.table?.type === 'flat') return `col:${k}`;
  return k === 'color' ? 'col:color_col' : k === 'style' ? 'col:label_col' : 'col:first_size_col';
}
const sentence = s => { const t = String(s || '').trim(); return t ? t.charAt(0).toUpperCase() + t.slice(1) + (/[.?!]$/.test(t) ? '' : '.') : ''; };

function confChip(id, p, mine) {
  if (mine) return '<span class="conf mine" title="You chose this">Yours</span>';
  if (!p) return '';
  const ev = [...(p.evidence || []), `Proposed by ${BY[p.source] || 'the field matcher'}${p.score != null ? ` · score ${Math.round(p.score * 100)}%` : ''}`];
  return `<span class="conf ${esc(p.confidence)}" tabindex="0" aria-describedby="ev-${esc(id)}">${esc(CONF[p.confidence] || p.confidence)}</span>
    <span class="ev-tip" role="tooltip" id="ev-${esc(id)}"><b>Why ${esc(CONF[p.confidence] || '').toLowerCase()} confidence</b><ul>${ev.map(x => `<li>${esc(x)}</li>`).join('')}</ul></span>`;
}

function fieldRow(k) {
  const fd = fieldOf(k); const req = fd ? fd.required : ['buyer', 'style', 'description', 'po_number', 'order_ref', 'order_date', 'ship_date'].includes(k);
  const target = `field:${k}`; const bad = badFor(target);
  return `<div class="mf-row ${bad.length ? 'err' : ''} ${ctx.pick === target ? 'picking' : ''}" data-target="${esc(target)}">
    <label class="mf-label" for="mf-${esc(k)}">${esc(labelOf(k))}${req ? '<span class="req" aria-hidden="true">*</span>' : ''}</label>
    <input id="mf-${esc(k)}" class="mf-cell" data-mf="${esc(k)}" value="${esc((ctx.def.fields || {})[k] || '')}" placeholder="cell" maxlength="9" autocomplete="off" spellcheck="false" ${bad.length ? 'aria-invalid="true"' : ''}>
    <span class="mf-val">${rowVal(k)}</span><span class="mf-conf">${rowConf(k)}</span>
    <button type="button" class="ghost tiny" data-pick="${esc(target)}" aria-pressed="${ctx.pick === target}" aria-label="Pick the cell for ${esc(labelOf(k))} in the sheet">Pick</button>
    <div class="mf-notes">${rowNotes(k)}</div></div>`;
}
function rowConf(k) {
  const cell = (ctx.def.fields || {})[k]; const p = proposal(k);
  const fromSug = p && cell && p.cell === cell && ctx.sug.definition?.fields?.[k] === cell;
  return confChip(k, fromSug ? p : null, !!cell && !fromSug);
}
function rowVal(k) {
  const ref = (ctx.def.fields || {})[k]; if (!ref) return '<span class="muted">not set</span>';
  const v = atRef(ref);
  return v == null || v === '' ? '<span class="muted">empty</span>' : `<span title="${esc(show(v))}">${esc(clip(v))}</span>`;
}
function rowNotes(k) {
  const ref = (ctx.def.fields || {})[k]; const p = proposal(k); const out = [];
  badFor(`field:${k}`).forEach(b => out.push(`<div class="f-msg"><span>${esc(sentence(b.reason))}</span></div>`));
  if (!out.length && ref) {
    const v = atRef(ref);
    if (!parseRef(ref)) out.push(`<div class="mf-warn">“${esc(ref)}” is not a cell address such as B4.</div>`);
    else if (v == null || v === '') out.push(`<div class="mf-warn">${esc(ref)} is empty.</div>`);
    else if (isLabel(v)) out.push(`<div class="mf-warn">${esc(ref)} holds the label “${esc(clip(v))}” — pick the value next to it.</div>`);
  }
  if (p && p.cell !== ref && !(ctx.sug.definition?.fields?.[k] === ref && ref)) {
    out.push(`<div class="mf-hint"><span class="conf ${esc(p.confidence)} sm">${esc(CONF[p.confidence] || p.confidence)}</span> possible: ${esc(p.cell)} “${esc(clip(p.value, 28))}” <button type="button" class="link" data-use="${esc(k)}" data-cell="${esc(p.cell)}">Use it</button></div>`);
  }
  notUsed(k).forEach(r => out.push(`<div class="mf-unused-note" title="${esc(sentence(r.reason))}">Not used: ${esc(r.cell || '—')}${r.value != null ? ` “${esc(clip(r.value, 24))}”` : ''} from ${esc(r.source === 'ai' ? 'local AI' : r.source === 'person' ? 'you' : 'matcher')} — ${esc(r.reason.replace(/^[A-Z]{1,3}\d+ holds “[^”]*”,? ?(which is )?/, ''))}</div>`));
  return out.join('');
}
function refreshRow(target) {
  const row = $(`[data-target="${CSS.escape(target)}"]`, ctx.container); if (!row) return;
  const [kind, key] = target.split(':');
  const bad = badFor(target).length;
  row.classList.toggle('err', !!bad); $('input', row)?.toggleAttribute('aria-invalid', !!bad);
  if (kind === 'field') {
    $('.mf-conf', row).innerHTML = rowConf(key); $('.mf-val', row).innerHTML = rowVal(key); $('.mf-notes', row).innerHTML = rowNotes(key);
  } else if (kind === 'col') {
    $('.mf-val', row).innerHTML = colVal(key); $('.mf-notes', row).innerHTML = colNotes(target);
  }
}

// ---------------------------------------------------------------- table section
function colLabel(k) { return `${((MATRIX.find(m => m[0] === k) || [])[1] || labelOf(k)).toLowerCase()} column`; }
function flatKeys() {
  const cols = ctx.fields.filter(f => f.column && f.kind !== 'unit').map(f => f.key);
  const keys = cols.length ? cols : ['size', 'quantity', 'color', 'style'];
  return [...FLAT_ORDER.filter(k => keys.includes(k)), ...keys.filter(k => !FLAT_ORDER.includes(k))];
}
function colOf(k) { const t = ctx.def.table || {}; return t.type === 'flat' ? t.columns?.[k] : t[k]; }
function colVal(k) {
  const c = colOf(k); const hr = ctx.def.table?.header_row;
  if (!c) return '<span class="muted">not set</span>';
  const v = hr ? at(hr, c) : null;
  return v == null || v === '' ? `<span class="muted">column ${letter(c)}</span>` : `<span title="${esc(show(v))}">“${esc(clip(v, 24))}”</span>`;
}
function colNotes(target) { return badFor(target).map(b => `<div class="f-msg"><span>${esc(sentence(b.reason))}</span></div>`).join(''); }

function refreshTable() {
  const box = $('#mf-table', ctx.container); if (!box) return;
  const t = ctx.def.table || { type: 'matrix' }; const type = t.type === 'flat' ? 'flat' : 'matrix';
  const st = ctx.sug?.table; const fromSug = st && ctx.sug.definition?.table && ctx.sug.definition.table.type === type && ctx.sug.definition.table.header_row === t.header_row;
  const rowBad = badFor('row');
  const hdrText = t.header_row ? rowText(t.header_row) : '';
  const all = type === 'flat' ? flatKeys().map(k => [k, labelOf(k), ['size', 'quantity'].includes(k)]) : MATRIX;
  // flat tables: size, quantity, colour and style always; other columns only when used (or asked for)
  const shown = ([k]) => type !== 'flat' || ['size', 'quantity', 'color', 'style'].includes(k) || colOf(k) || badFor(`col:${k}`).length || ctx.moreCols;
  const cols = all.filter(shown); const hiddenCols = all.length - cols.length;
  box.innerHTML = `<div class="mf-head"><h3 id="mf-h-table">Size quantities</h3>${confChip('table', fromSug ? { ...st, evidence: st.evidence } : null, !fromSug && !!t.header_row)}</div>
    <div class="segmented" role="radiogroup" aria-label="How the sizes are laid out"><button type="button" role="radio" aria-checked="${type === 'matrix'}" class="${type === 'matrix' ? 'on' : ''}" data-ttype="matrix">Sizes across columns</button><button type="button" role="radio" aria-checked="${type === 'flat'}" class="${type === 'flat' ? 'on' : ''}" data-ttype="flat">One row per size</button></div>
    <div class="mf-row ${rowBad.length ? 'err' : ''} ${ctx.pick === 'row' ? 'picking' : ''}" data-target="row"><label class="mf-label" for="mf-hr">Header row<span class="req" aria-hidden="true">*</span></label>
      <input id="mf-hr" class="mf-cell" type="number" min="1" data-mt="header_row" value="${esc(t.header_row || '')}" placeholder="row" ${rowBad.length ? 'aria-invalid="true"' : ''}>
      <span class="mf-val wide">${hdrText ? `<span title="${esc(hdrText)}">${esc(clip(hdrText, 44))}</span>` : '<span class="muted">not set</span>'}</span>
      <button type="button" class="ghost tiny" data-pick="row" aria-pressed="${ctx.pick === 'row'}" aria-label="Pick the header row in the sheet">Pick</button>
      <div class="mf-notes">${rowBad.map(b => `<div class="f-msg"><span>${esc(sentence(b.reason))}</span></div>`).join('')}</div></div>
    ${cols.map(([k, l, req]) => { const target = `col:${k}`; const bad = badFor(target); const c = colOf(k); return `<div class="mf-row ${bad.length ? 'err' : ''} ${ctx.pick === target ? 'picking' : ''}" data-target="${esc(target)}">
      <label class="mf-label" for="mc-${esc(k)}">${esc(l)}${req ? '<span class="req" aria-hidden="true">*</span>' : ''}</label>
      <input id="mc-${esc(k)}" class="mf-cell" data-mc="${esc(k)}" value="${esc(c ? letter(c) : '')}" placeholder="col" maxlength="3" autocomplete="off" spellcheck="false" ${bad.length ? 'aria-invalid="true"' : ''}>
      <span class="mf-val wide">${colVal(k)}</span>
      <button type="button" class="ghost tiny" data-pick="${esc(target)}" aria-pressed="${ctx.pick === target}" aria-label="Pick the ${esc(l)} in the sheet">Pick</button>
      <div class="mf-notes">${colNotes(target)}</div></div>`; }).join('')}
    ${type === 'flat' && (hiddenCols || ctx.moreCols) ? `<button type="button" class="link mf-more-btn" data-m="morecols" aria-expanded="${!!ctx.moreCols}">${ctx.moreCols ? 'Fewer columns' : `More columns (${hiddenCols})`}</button>` : ''}`;
}
function rowText(r) {
  const s = sheet(); const out = [];
  for (let c = 1; c <= Math.min(s?.cols || 0, 40); c++) { const v = at(r, c); if (v != null && v !== '') out.push(show(v)); }
  return out.length ? `row ${r}: ${out.join(' · ')}` : `row ${r} is empty`;
}

// ---------------------------------------------------------------- unit + save
function drawUnit() {
  const box = $('#mf-unit', ctx.container); if (!box) return;
  const u = ctx.unit; const su = ctx.sug?.unit; const bad = badFor('unit');
  const fromFile = su && !u.touched && su.value && u.origin === 'file';
  box.innerHTML = `<div class="mf-head"><h3 id="mf-h-unit">Quantity unit</h3>${fromFile ? `<span class="mf-conf"><span class="conf high" tabindex="0" aria-describedby="ev-unit">From the file</span><span class="ev-tip" role="tooltip" id="ev-unit">${esc(su.evidence || '')}${su.cell ? ` (${esc(su.cell)})` : ''}</span></span>` : u.touched && u.qty_unit ? '<span class="conf mine">Your choice</span>' : u.origin === 'draft' && u.qty_unit ? '<span class="conf mine" title="Already decided for this order">Kept</span>' : ''}</div>
    <p class="muted small-text">${su?.evidence ? `${esc(sentence(su.evidence)).replace(/\.$/, '')}${su.cell ? ` (${esc(su.cell)})` : ''}.` : 'Are the quantities pieces or packs?'} ${!u.qty_unit ? 'Choose now, or you will be asked on the review screen.' : ''}</p>
    <div class="mf-row unit ${bad.length ? 'err' : ''}" data-target="unit"><div class="unit-line"><div class="segmented" role="radiogroup" aria-label="Quantity unit">
      ${[['PCS', 'Pieces'], ['PACK', 'Packs'], ['', 'Ask on review']].map(([v, l]) => `<button type="button" role="radio" aria-checked="${u.qty_unit === v}" class="${u.qty_unit === v ? 'on' : ''}" data-unit="${v}">${l}</button>`).join('')}</div>
      ${u.qty_unit === 'PACK' ? `<label class="ppu">Pieces in one pack<span class="req" aria-hidden="true">*</span><input id="map-ppu" type="number" min="2" step="1" value="${esc(u.pieces_per_unit ?? '')}" ${bad.length ? 'aria-invalid="true"' : ''}></label>` : ''}</div>
      <div class="mf-notes" id="unit-msg">${bad.map(b => `<div class="f-msg"><span>${esc(sentence(b.reason))}</span></div>`).join('')}</div></div>`;
}
function refreshUnitMsg() {
  const row = $('[data-target="unit"]', ctx.container); if (!row) return;
  const bad = badFor('unit'); row.classList.toggle('err', !!bad.length);
  $('#unit-msg', row).innerHTML = bad.map(b => `<div class="f-msg"><span>${esc(sentence(b.reason))}</span></div>`).join('');
}

function defaultName() {
  const refused = (ctx.draft.templates_refused || []).find(t => t.sheet === ctx.sheet);
  if (refused) return refused.name;
  const ref = (ctx.def.fields || {}).buyer; const v = ref ? atRef(ref) : null;
  const buyer = (typeof v === 'string' && v.trim()) || ctx.draft.orders?.[0]?.buyer || ctx.draft.filename.replace(/\.[^.]+$/, '');
  return `${String(buyer).trim().slice(0, 70)} layout`;
}
function drawSave() {
  const box = $('#mf-save', ctx.container); if (!box) return;
  const on = ctx.save;
  if (!ctx.nameTouched) ctx.name = defaultName();
  box.innerHTML = `<label class="check"><input type="checkbox" id="map-save" ${on ? 'checked' : ''}> Save as this buyer’s layout template</label>
    <label class="field" for="map-name">Template name<input id="map-name" maxlength="80" value="${esc(ctx.name)}" ${on ? '' : 'disabled'} aria-describedby="map-version"></label>
    <p class="muted small-text" id="map-version"></p>
    <button type="button" class="primary" data-m="apply">Read the file with this layout →</button>`;
  drawVersion();
}
function drawVersion() {
  const el = $('#map-version', ctx.container); if (!el) return;
  if (!ctx.save) { el.textContent = 'Only this file is read with the layout; nothing is saved for next time.'; return; }
  const name = norm(ctx.name || defaultName());
  const list = (ctx.templates || []).filter(t => norm(t.name) === name);
  if (!ctx.templates) { el.textContent = ''; return; }
  if (!list.length) { el.textContent = 'Saved as a new layout template (version 1). The next file with this layout is read automatically.'; return; }
  const top = Math.max(...list.map(t => t.version || 1));
  const refused = (ctx.draft.templates_refused || []).some(t => norm(t.name) === name);
  el.innerHTML = `<b>Updates “${esc(list[0].name)}”</b>: saved as version ${top + 1}${refused ? ' because the saved layout no longer matches this file' : ''}. Version ${top} stays in the history (Setup → Layout templates).`;
}

// ---------------------------------------------------------------- apply
function targetsOf(r) {
  const t = ctx.def.table || {}; const f = ctx.def.fields || {}; const k = r.field;
  if (k === 'table') return ['row'];
  if (k === 'qty_unit') return ['unit'];
  if (f[k] && String(r.cell || '').toUpperCase() === String(f[k]).toUpperCase()) return [`field:${k}`];
  if (t.type === 'flat') return t.columns && k in t.columns ? [`col:${k}`] : headerKeys().includes(k) ? [`field:${k}`] : ['row'];
  if (k === 'size' || k === 'quantity') return ['col:first_size_col', 'col:last_size_col'];
  if (k === 'style') return ['col:label_col'];
  if (k === 'color') return ['col:color_col'];
  return headerKeys().includes(k) ? [`field:${k}`] : ['row'];
}

async function submit() {
  if (ctx.pick) cancelPick();
  const def = structuredClone(ctx.def); const t = def.table || { type: 'matrix' }; const bad = [];
  def.fields = Object.fromEntries(Object.entries(def.fields || {}).filter(([, v]) => v));
  if (!t.header_row) bad.push({ targets: ['row'], reason: 'Choose the header row of the size table.' });
  if (t.type === 'flat') {
    t.columns = Object.fromEntries(Object.entries(t.columns || {}).filter(([, v]) => v));
    ['size', 'quantity'].forEach(k => { if (!t.columns[k]) bad.push({ targets: [`col:${k}`], reason: `Choose the ${labelOf(k).toLowerCase()} column.` }); });
  } else {
    if (!t.first_size_col) bad.push({ targets: ['col:first_size_col'], reason: 'Choose the first size column.' });
    if (!t.last_size_col) bad.push({ targets: ['col:last_size_col'], reason: 'Choose the last size column.' });
    if (t.first_size_col && t.last_size_col && t.last_size_col < t.first_size_col) bad.push({ targets: ['col:last_size_col'], reason: 'The last size column must be to the right of the first.' });
  }
  const u = ctx.unit;
  if (u.qty_unit === 'PACK' && !(Number.isInteger(u.pieces_per_unit) && u.pieces_per_unit >= 2)) bad.push({ targets: ['unit'], reason: u.pieces_per_unit === 1 ? 'A pack holds at least 2 pieces — how many pieces are in one pack?' : 'How many pieces are in one pack?' });
  const save = ctx.save; const name = ($('#map-name', ctx.container).value || '').trim() || defaultName();
  if (bad.length) return showBad(bad, 'Some choices are missing — they are outlined in red.');
  const body = { sheet: ctx.sheet, definition: def, save_template: save, template_name: save ? name : null };
  if (u.qty_unit === 'PCS') Object.assign(body, { qty_unit: 'PCS', pieces_per_unit: 1 });
  if (u.qty_unit === 'PACK') Object.assign(body, { qty_unit: 'PACK', pieces_per_unit: u.pieces_per_unit });
  if (u.touched && !u.qty_unit && ctx.sug?.unit?.value) body.qty_unit = '';   // the person wants to be asked, not the file's unit
  const r = await ctx.apply(body);
  if (r?.rejected) showBad(r.rejected.map(x => ({ targets: targetsOf(x), reason: x.reason })), 'This layout does not fit the file — the wrong choices are outlined in red.');
  else if (r?.ok) { ctx.bad = []; ctx.templates = null; $$('#toasts .toast.warning').forEach(x => x.remove()); }
}
function showBad(bad, message) {
  ctx.bad = bad;
  if (bad.some(b => b.targets.some(t => t.startsWith('field:') && rest().includes(t.slice(6))))) ctx.more = true;
  drawForm(); drawGrid(); notify(message, 'warning');
  const first = $('.mf-row.err', ctx.container);
  if (first) { first.scrollIntoView({ behavior: 'smooth', block: 'center' }); $('input', first)?.focus({ preventScroll: true }); }
}
function rest() { const keys = headerKeys(); return keys.filter(k => !PRIMARY.includes(k) && (!fieldOf(k)?.required || DEFAULTED.includes(k))); }

// ---------------------------------------------------------------- the sheet grid
function drawGrid() {
  const s = sheet(); const grid = $('#sheet-grid', ctx.container); if (!s || !grid) return;
  const t = ctx.def.table || {}; const f = ctx.def.fields || {}; const hr = t.header_row;
  const maxR = Math.min(s.rows, 80); const maxC = Math.min(s.cols, 30);
  const byCell = new Map(); Object.entries(f).forEach(([k, ref]) => { const p = parseRef(ref); if (p) byCell.set(`${p.r}:${p.c}`, k); });
  const labelCells = new Set(Object.entries(f).map(([k, ref]) => proposal(k)?.cell === ref ? proposal(k).label_cell : null).filter(Boolean));
  const colRole = new Map();
  if (t.type === 'flat') Object.entries(t.columns || {}).forEach(([k, c]) => c && colRole.set(c, labelOf(k)));
  else {
    if (t.label_col) colRole.set(t.label_col, 'Garment'); if (t.color_col) colRole.set(t.color_col, 'Colour'); if (t.total_col) colRole.set(t.total_col, 'Total');
    if (t.first_size_col && t.last_size_col) for (let c = t.first_size_col; c <= t.last_size_col; c++) colRole.set(c, '');
  }
  let last = hr || 0;
  if (hr) { let gap = 0; for (let r = hr + 1; r <= maxR && gap < 2; r++) { if ([...colRole.keys()].some(c => at(r, c) != null && at(r, c) !== '')) { last = r; gap = 0; } else gap++; } }
  const rej = new Map(); (ctx.sug?.rejected || []).forEach(r => { const p = parseRef(r.cell); if (p) rej.set(`${p.r}:${p.c}`, r); });
  const wrong = new Map(); ctx.bad.forEach(b => b.targets.forEach(tg => { if (tg.startsWith('field:')) { const p = parseRef(f[tg.slice(6)]); if (p) wrong.set(`${p.r}:${p.c}`, b.reason); } }));
  const low = new Map(); Object.entries(ctx.sug?.fields || {}).forEach(([k, p]) => { if (f[k] !== p.cell) { const q = parseRef(p.cell); if (q) low.set(`${q.r}:${q.c}`, k); } });
  let html = '<table><thead><tr><th class="rh-h"></th>';
  for (let c = 1; c <= maxC; c++) html += `<th class="${colRole.has(c) ? 'on' : ''}">${letter(c)}</th>`;
  html += '</tr></thead><tbody>';
  for (let r = 1; r <= maxR; r++) {
    html += `<tr><td class="rh ${r === hr ? 'on' : ''}">${r}</td>`;
    for (let c = 1; c <= maxC; c++) {
      const key = `${r}:${c}`; const v = at(r, c); const a = `${letter(c)}${r}`; const cls = []; let tag = ''; let tip = `${a}: ${show(v)}`;
      const fk = byCell.get(key);
      if (fk) { cls.push('pk'); tag = labelOf(fk); tip += ` · read as ${labelOf(fk)}`; }
      if (labelCells.has(a)) cls.push('lbl');
      if (r === hr) { cls.push('hdr'); if (colRole.has(c)) { cls.push(colRole.get(c) ? 'hcol' : 'hcol hsize'); tag ||= colRole.get(c); } }
      else if (hr && r > hr && r <= last && colRole.has(c)) cls.push('body');
      if (rej.has(key) && !fk) { cls.push('rej'); tip += ` · not used for ${labelOf(rej.get(key).field)}: ${rej.get(key).reason}`; }
      if (low.has(key) && !fk) { cls.push('cand'); tip += ` · possible ${labelOf(low.get(key))}`; }
      if (wrong.has(key)) { cls.push('wrong'); tip += ` · ${wrong.get(key)}`; }
      html += `<td data-r="${r}" data-c="${c}" class="${cls.join(' ')}" title="${esc(tip)}">${tag ? `<span class="g-tag">${esc(tag)}</span>` : ''}${esc(v == null ? '' : show(v))}</td>`;
    }
    html += '</tr>';
  }
  grid.innerHTML = html + '</tbody></table>';
  grid.classList.toggle('picking', !!ctx.pick);
  grid.onclick = e => { const td = e.target.closest('td[data-r]'); if (td && ctx.pick) picked(Number(td.dataset.r), Number(td.dataset.c)); };
}
