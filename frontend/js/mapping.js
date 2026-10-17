// "Show us once" layout mapping for unfamiliar buyer files. Saved as a reusable template.
import { $, $$, esc, show, notify, busy } from './util.js';
import { api, id } from './api.js';

const FIELDS = [['buyer', 'Buyer'], ['style', 'Style #'], ['description', 'Description'], ['po_number', 'PO / contract'], ['order_ref', 'Order reference'], ['ship_date', 'Ship date'], ['season', 'Season'], ['merch', 'Merchandiser']];
const FLAT = [['style', 'Style column'], ['size', 'Size column *'], ['quantity', 'Quantity column *'], ['color', 'Colour column'], ['buyer', 'Buyer column'], ['po_number', 'PO column'], ['ship_date', 'Ship date column']];
const MATRIX = [['label_col', 'Garment / style column'], ['color_col', 'Colour column'], ['first_size_col', 'First size column *'], ['last_size_col', 'Last size column *'], ['total_col', 'Total column']];
const letter = n => { let s = ''; n = Number(n); while (n > 0) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; };
const colnum = s => { s = String(s || '').trim().toUpperCase(); if (!/^[A-Z]{1,3}$/.test(s)) return null; return [...s].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0); };

let ctx = null;

export function renderMapping(container, draft, apply) {
  const sug = ctx?.draftId === draft.id ? ctx.def : draft.suggestion?.definition;
  const sheetName = (ctx?.draftId === draft.id && ctx.sheet) || draft.suggestion?.sheet || draft.sheets.find(s => !s.hidden)?.name;
  ctx = { draftId: draft.id, sheet: sheetName, def: sug || { fields: {}, table: { type: 'matrix' } }, pick: null, apply, draft };
  container.innerHTML = `<div class="card-head"><div><span class="eyebrow">NEW LAYOUT</span><h2>Show us once where the order is</h2>
    <p class="muted">${draft.suggestion ? esc(draft.suggestion.explanation) : 'Click a field, then click its value in the sheet. Save it as a template and the next file from this buyer is read automatically.'}</p></div>
    <button class="secondary small" id="map-suggest">Suggest with local AI</button></div>
    <div class="mapgrid"><div class="mapform" id="mapform"></div><div><label class="field">Sheet / page<select id="map-sheet">${draft.sheets.map(s => `<option ${s.name === sheetName ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>
    <p class="muted" id="pick-hint" style="margin:8px 0">Highlighted cells are the current choices.</p><div class="sheet-grid" id="sheet-grid"></div></div></div>`;
  drawForm(); drawGrid();
  $('#map-sheet').onchange = e => { ctx.sheet = e.target.value; drawGrid(); };
  $('#map-suggest').onclick = () => busy('Analysing the layout locally…', async () => {
    const r = await api(`/api/drafts/${id(draft.id)}/suggest`, { method: 'POST', body: { sheet: ctx.sheet } });
    ctx.def = r.definition; ctx.sheet = r.sheet; $('#map-sheet').value = r.sheet; drawForm(); drawGrid();
    notify(r.explanation, 'success');
  });
}

function sheet() { return ctx.draft.sheets.find(s => s.name === ctx.sheet); }

function drawForm() {
  const t = ctx.def.table || {}; const f = ctx.def.fields || {};
  const type = t.type || 'matrix';
  $('#mapform').innerHTML = `<h3>Order details (cells)</h3>
    ${FIELDS.map(([k, l]) => `<div class="two"><label class="field">${l}<input data-mf="${k}" value="${esc(f[k] || '')}" placeholder="e.g. C4"></label><button class="ghost small" data-pick="field:${k}" style="align-self:end">Pick</button></div>`).join('')}
    <h3>Size quantities</h3>
    <div class="segmented"><button class="${type === 'matrix' ? 'on' : ''}" data-ttype="matrix">Sizes across columns</button><button class="${type === 'flat' ? 'on' : ''}" data-ttype="flat">One row per size</button></div>
    <div class="two"><label class="field">Header row *<input type="number" min="1" data-mt="header_row" value="${esc(t.header_row || '')}"></label><button class="ghost small" data-pick="row" style="align-self:end">Pick row</button></div>
    ${(type === 'flat' ? FLAT : MATRIX).map(([k, l]) => { const v = type === 'flat' ? t.columns?.[k] : t[k]; return `<div class="two"><label class="field">${l}<input data-mc="${k}" value="${esc(v ? letter(v) : '')}" placeholder="column letter"></label><button class="ghost small" data-pick="col:${k}" style="align-self:end">Pick</button></div>`; }).join('')}
    <label class="check"><input type="checkbox" id="map-save" checked> Save as a template for future files</label>
    <label class="field">Template name<input id="map-name" maxlength="80" placeholder="${esc((ctx.draft.orders[0]?.buyer || 'Buyer') + ' order layout')}"></label>
    <button class="primary" id="map-apply">Read the file with this layout →</button>`;
  $$('#mapform [data-mf]').forEach(i => i.oninput = () => { (ctx.def.fields ||= {})[i.dataset.mf] = i.value.trim().toUpperCase(); if (!i.value.trim()) delete ctx.def.fields[i.dataset.mf]; drawGrid(); });
  $$('#mapform [data-mt]').forEach(i => i.oninput = () => { ctx.def.table.header_row = Number(i.value) || null; drawGrid(); });
  $$('#mapform [data-mc]').forEach(i => i.oninput = () => setCol(i.dataset.mc, colnum(i.value)));
  $$('#mapform [data-ttype]').forEach(b => b.onclick = () => { ctx.def.table = { type: b.dataset.ttype, header_row: ctx.def.table?.header_row, columns: {} }; drawForm(); drawGrid(); });
  $$('#mapform [data-pick]').forEach(b => b.onclick = () => { ctx.pick = b.dataset.pick; $('#pick-hint').textContent = 'Now click the cell in the sheet…'; });
  $('#map-apply').onclick = () => {
    const def = structuredClone(ctx.def);
    if (def.table.type === 'flat') def.table.columns = Object.fromEntries(Object.entries(def.table.columns || {}).filter(([, v]) => v));
    ctx.apply({ sheet: ctx.sheet, definition: def, save_template: $('#map-save').checked, template_name: $('#map-name').value || null });
  };
}

function setCol(k, n) {
  const t = ctx.def.table;
  if (t.type === 'flat') { t.columns ||= {}; if (n) t.columns[k] = n; else delete t.columns[k]; } else t[k] = n;
  drawGrid();
}

function drawGrid() {
  const s = sheet(); if (!s) return;
  const t = ctx.def.table || {}; const f = ctx.def.fields || {};
  const maxR = Math.min(s.rows, 80); const maxC = Math.min(s.cols, 30);
  const cells = new Map(s.cells.map(c => [`${c.row}:${c.col}`, c]));
  const picked = new Set(Object.values(f).map(a => String(a).toUpperCase()));
  const cols = t.type === 'flat' ? Object.values(t.columns || {}) : [t.label_col, t.color_col, t.total_col].filter(Boolean);
  const inSizes = c => t.type !== 'flat' && t.first_size_col && t.last_size_col && c >= t.first_size_col && c <= t.last_size_col;
  let html = '<table><thead><tr><th></th>'; for (let c = 1; c <= maxC; c++) html += `<th>${letter(c)}</th>`; html += '</tr></thead><tbody>';
  for (let r = 1; r <= maxR; r++) {
    html += `<tr><td class="rh">${r}</td>`;
    for (let c = 1; c <= maxC; c++) {
      const cell = cells.get(`${r}:${c}`); const addr = `${letter(c)}${r}`;
      const hl = picked.has(addr) || (r === t.header_row && (cols.includes(c) || inSizes(c)));
      html += `<td data-r="${r}" data-c="${c}" class="${hl ? 'hl' : r === t.header_row ? 'hdr' : ''}" title="${esc(addr + ': ' + show(cell?.value))}">${esc(cell ? show(cell.value) : '')}</td>`;
    }
    html += '</tr>';
  }
  $('#sheet-grid').innerHTML = html + '</tbody></table>';
  $('#sheet-grid').onclick = e => {
    const td = e.target.closest('td[data-r]'); if (!td || !ctx.pick) return;
    const r = Number(td.dataset.r), c = Number(td.dataset.c); const [kind, key] = ctx.pick.split(':');
    if (kind === 'field') (ctx.def.fields ||= {})[key] = `${letter(c)}${r}`;
    if (kind === 'row') ctx.def.table.header_row = r;
    if (kind === 'col') { setCol(key, c); if (!ctx.def.table.header_row) ctx.def.table.header_row = r; }
    ctx.pick = null; $('#pick-hint').textContent = 'Highlighted cells are the current choices.'; drawForm(); drawGrid();
  };
}
