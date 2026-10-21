'use strict';
/* Two-panel mapping editor. LEFT: Oracle tables/columns with the proposed values. RIGHT: the original workbook.
   Drag a cell onto a field (or arm a field and click a cell). Values always come from the stored workbook on the
   server, so the source reference shown is the real one. Typing a value is the Simple edit. */
const ORDER_FIELDS = [
  ['order_ref', 'DOCID', 'Order reference', true], ['buyer', 'BUYER', 'Buyer'], ['style', 'STYLENO', 'Style number'], ['description', 'STYLEDESC', 'Garment description'],
  ['po_number', 'BONO', 'Buyer PO / contract'], ['order_date', 'ODATE', 'Order date'], ['merch', 'MERCH', 'Merchandiser'], ['sample_type', 'SAMPLETYPE', 'Sample type'],
];
const Mapper = {
  M: null,
  async open(draft, onDone) {
    this.M = { draft, onDone, sheet: (draft.sheets.find(s => !s.hidden) || draft.sheets[0])?.name, armed: null, selected: null, undo: [], redo: [], advanced: false,
      order: 0, suggestions: null, added: { ORDMAS: [], ORDSTYLE: [] }, columns: null };
    Modal.open('Edit mapping', '<div id="mapper-root" class="stack" style="flex:1;min-height:0"></div>', { full: true, onClose: () => { const done = this.M?.onDone; const d = this.M?.draft; this.M = null; if (done) done(d); } });
    $('#modal-body').style.display = 'flex';
    this.render();
    api('/api/schema/order-columns').then(c => { if (this.M) { this.M.columns = c; if (this.M.advanced) this.render(); } }).catch(() => {});
  },
  order() { return this.M.draft.orders[this.M.order] || null; },
  sources() {
    // every cell the current order draws a value from, for highlighting
    const out = []; const o = this.order(); if (!o) return out;
    const add = (src, target) => { if (src?.cell) out.push({ ...src, target }); };
    Object.entries(o.provenance || {}).forEach(([k, s]) => add(s, `field:${k}`));
    Object.entries(o.extras || {}).forEach(([k, e]) => add(e.source, `column:ORDMAS.${k}`));
    (o.lines || []).forEach((l, li) => { add(l.provenance?.color, `line:${li}:color`); Object.entries(l.extras || {}).forEach(([k, e]) => add(e.source, `column:ORDSTYLE.${k}@${li}`));
      (l.sizes || []).forEach((s, si) => { add(s.source, `size:${li}:${si}:quantity`); add(s.size_source, `size:${li}:${si}:size`); }); });
    return out;
  },
  render() {
    const M = this.M; if (!M) return; const root = $('#mapper-root'); const o = this.order();
    root.innerHTML = `<div class="row between">
        <div class="row"><div class="seg"><button type="button" data-mode="simple" class="${M.advanced ? '' : 'active'}">Simple edit</button><button type="button" data-mode="advanced" class="${M.advanced ? 'active' : ''}">Advanced mapping</button></div>
        ${M.draft.orders.length > 1 ? `<select id="m-order">${M.draft.orders.map((x, i) => `<option value="${i}" ${i === M.order ? 'selected' : ''}>Order ${i + 1} · ${esc(x.order_ref || x.style || '')}</option>`).join('')}</select>` : ''}
        <button class="btn small" id="m-suggest" type="button">${icon('sparkle')} Suggest mapping</button></div>
        <div class="row"><button class="icon-btn" id="m-undo" title="Undo" aria-label="Undo" ${M.undo.length ? '' : 'disabled'}>${icon('undo')}</button><button class="icon-btn" id="m-redo" title="Redo" aria-label="Redo" ${M.redo.length ? '' : 'disabled'}>${icon('redo')}</button>
        <button class="btn small" id="m-template" type="button">${icon('save')} Save as buyer template</button><button class="btn primary" id="m-done" type="button">${icon('check')} Save mapping &amp; preview</button></div></div>
      ${M.armed ? `<div class="callout info">${icon('info')}<div>Click the Excel cell that holds <strong>${esc(M.armedLabel)}</strong>. <button class="btn small" id="m-disarm">Cancel</button></div></div>` : ''}
      <div id="m-template-form" hidden class="row"><input id="m-template-name" placeholder="Template name, e.g. ${esc((o?.buyer || 'Buyer') + ' order format')}" style="min-width:280px"><button class="btn small primary" id="m-template-save">Save template</button><span class="muted">Reused automatically when the same layout is uploaded again.</span></div>
      ${M.suggestions ? this.suggestionsHtml() : ''}
      <div class="mapper">
        <section class="card"><div class="card-head"><div><h2>Oracle mapping</h2><span class="sub">Drop an Excel cell on a field, or type the value</span></div></div><div class="pane-body">${o ? this.fieldsHtml(o) : this.noOrderHtml()}</div></section>
        <section class="card"><div class="card-head"><div><h2>Original Excel</h2><span class="sub">Read-only · drag a cell to the left</span></div></div>
          <div class="sheet-tabs">${M.draft.sheets.map(s => `<button type="button" data-sheet="${esc(s.name)}" class="${s.name === M.sheet ? 'active' : ''}">${esc(s.name)}${s.hidden ? ' (hidden)' : ''}</button>`).join('')}</div>
          <div class="pane-body" id="m-grid">${this.gridHtml()}</div><div class="cell-info" id="m-cell-info">${this.cellInfo()}</div></section>
      </div>`;
    paintIcons(root); this.wire(root);
  },
  noOrderHtml() { return `<div class="empty">${icon('info')}<p>No order was recognised automatically.<br>Drag the order reference cell here to start, or use Suggest mapping.</p><div class="map-row" data-target="field:order_ref" style="margin-top:10px"><div class="target"><span class="col-name">DOCID</span><span class="col-label">Order reference</span></div><span class="faint">drop a cell</span><span></span></div></div>`; },
  fieldRow(target, col, label, value, src, opts = {}) {
    const missing = opts.required && !String(value ?? '').trim();
    return `<div class="map-row ${missing ? 'missing-required' : ''} ${this.M.armed === target + (opts.line != null ? '@' + opts.line : '') ? 'armed' : ''}" data-target="${esc(target)}" ${opts.line != null ? `data-line="${opts.line}"` : ''} data-label="${esc(label)}">
      <div class="target"><span class="col-name">${esc(col)}</span><span class="col-label">${esc(label)}${opts.required ? ' · needed' : ''}${opts.note ? ' · ' + esc(opts.note) : ''}</span></div>
      <input value="${esc(value ?? '')}" data-edit="${esc(target)}" ${opts.line != null ? `data-line="${opts.line}"` : ''} ${opts.readonly ? 'disabled' : ''} placeholder="${missing ? 'Needed' : 'Optional · blank'}" aria-label="${esc(label)}">
      <span class="src">${sourceChip(src)}<button class="icon-btn" type="button" data-arm="${esc(target)}" ${opts.line != null ? `data-line="${opts.line}"` : ''} title="Pick an Excel cell" aria-label="Pick cell for ${esc(label)}">${icon('fit')}</button>${src?.cell ? `<button class="icon-btn" type="button" data-clear="${esc(target)}" ${opts.line != null ? `data-line="${opts.line}"` : ''} title="Remove mapping" aria-label="Remove mapping">${icon('x')}</button>` : ''}</span></div>`;
  },
  fieldsHtml(o) {
    const M = this.M; const prov = o.provenance || {};
    const extras = (table, holder, line) => { const keys = [...new Set([...Object.keys(holder.extras || {}), ...M.added[table]])];
      return keys.map(k => this.fieldRow(`column:${table}.${k}`, k, COLUMN_LABELS[k] || 'Extra column', holder.extras?.[k]?.value, holder.extras?.[k]?.source, { line })).join(''); };
    const addCol = (table, line) => !M.advanced ? '' : !M.columns ? `<p class="faint" style="padding:8px 14px">Connect Oracle to add more columns.</p>` :
      `<div class="row" style="padding:8px 14px"><input list="cols-${table}" data-addcol="${table}" ${line != null ? `data-line="${line}"` : ''} placeholder="Add Oracle column (search ${table})…" style="flex:1"><datalist id="cols-${table}">${M.columns[table].columns.filter(c => c.mappable).map(c => `<option value="${esc(c.name)}">${esc(c.type)}</option>`).join('')}</datalist></div>`;
    return `<div class="map-group"><header><span><span class="col-name">ORDMAS</span> <span class="muted">· Order header</span></span><span class="badge">1 record</span></header>
        ${ORDER_FIELDS.map(([f, col, label, req]) => this.fieldRow(`field:${f}`, col, label, o[f], prov[f], { required: req })).join('')}
        ${M.advanced ? this.fieldRow('field:season', '—', 'Season', o.season, prov.season, { note: 'reference only, not saved' }) : ''}
        ${extras('ORDMAS', o)}${addCol('ORDMAS')}</div>
      <div class="map-group"><header><span><span class="col-name">ORDSTYLE</span> <span class="muted">· Order lines</span></span><span class="badge">${plural((o.lines || []).length, 'line')}</span></header>
        ${this.fieldRow('field:ship_date', 'SHIPDT', 'Ship date (all lines)', o.ship_date, prov.ship_date)}
        ${(o.lines || []).map((l, li) => `<div class="map-row" style="background:var(--surface-2)"><strong>Line ${li + 1}</strong><span class="muted">${esc(l.label || '')}</span><button class="btn small ghost danger" type="button" data-remove-line="${li}">Remove line</button></div>
          ${this.fieldRow(`line:${li}:color`, 'COMBO', 'Colour / combo', l.color, l.provenance?.color)}${M.advanced ? this.fieldRow(`line:${li}:label`, '—', 'Garment / component', l.label, l.provenance?.label, { note: 'reference only' }) : ''}
          ${extras('ORDSTYLE', l, li)}${addCol('ORDSTYLE', li)}`).join('')}
        <div style="padding:8px 14px"><button class="btn small" type="button" id="m-add-line">${icon('plus')} Add line</button></div></div>
      <div class="map-group"><header><span><span class="col-name">ORDSIZE</span> <span class="muted">· Size quantities</span></span></header>
        ${(o.lines || []).map((l, li) => `<div style="padding:6px 14px 2px" class="muted">Line ${li + 1}${l.color ? ' · ' + esc(l.color) : ''}${l.declared_total != null ? ` · Excel total ${esc(l.declared_total)}` : ''}</div>
          ${(l.sizes || []).map((s, si) => `<div class="map-row" data-target="size:${li}:${si}:quantity" data-label="Quantity of size ${esc(s.size)}"><div class="row" style="gap:6px;flex-wrap:nowrap"><input value="${esc(s.size)}" data-edit="size:${li}:${si}:size" aria-label="Size" style="width:70px" placeholder="Size">${sourceChip(s.size_source)}</div>
            <input value="${esc(s.quantity ?? '')}" data-edit="size:${li}:${si}:quantity" inputmode="numeric" aria-label="Quantity" placeholder="blank = no record">
            <span class="src">${sourceChip(s.source)}<button class="icon-btn" type="button" data-arm="size:${li}:${si}:quantity" title="Pick quantity cell">${icon('fit')}</button><button class="icon-btn" type="button" data-remove-size="${li}:${si}" title="Remove size">${icon('x')}</button></span></div>`).join('')}
          <div style="padding:4px 14px 10px"><button class="btn small ghost" type="button" data-add-size="${li}">${icon('plus')} Add size</button></div>`).join('') || '<p class="faint" style="padding:10px 14px">No lines. Add a line to enter sizes.</p>'}</div>`;
  },
  gridHtml() {
    const sheet = this.M.draft.sheets.find(s => s.name === this.M.sheet); if (!sheet) return '<div class="empty">No sheet.</div>';
    if (!sheet.cells.length) return '<div class="empty">This sheet is empty.</div>';
    const maxR = Math.min(Math.max(...sheet.cells.map(c => c.row)), 400), maxC = Math.min(Math.max(...sheet.cells.map(c => c.col)), 50);
    const at = new Map(sheet.cells.map(c => [`${c.row}:${c.col}`, c]));
    const mapped = new Set(this.sources().filter(s => s.sheet === sheet.name).map(s => s.cell));
    const colName = n => { let s = ''; for (; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + (n - 1) % 26) + s; return s; };
    let html = `<table class="xl"><thead><tr><th class="rh"></th>${Array.from({ length: maxC }, (_, i) => `<th>${colName(i + 1)}</th>`).join('')}</tr></thead><tbody>`;
    for (let r = 1; r <= maxR; r++) {
      html += `<tr><td class="rh">${r}</td>`;
      for (let c = 1; c <= maxC; c++) {
        const cell = at.get(`${r}:${c}`);
        if (!cell) { html += '<td></td>'; continue; }
        const v = cell.value == null ? '' : String(cell.value); const isNum = typeof cell.value === 'number';
        html += `<td class="has ${mapped.has(cell.cell) ? 'mapped' : ''} ${cell.formula ? 'formula' : ''} ${isNum ? 'num' : ''} ${this.M.selected === cell.cell ? 'selected' : ''}" draggable="true" data-cell="${cell.cell}" title="${esc(cell.cell)}: ${esc(v)}${cell.formula ? ' · ' + esc(cell.formula) : ''}">${esc(v)}</td>`;
      }
      html += '</tr>';
    }
    const more = Math.max(...sheet.cells.map(c => c.row)) > maxR;
    return html + `</tbody></table>${more ? '<p class="faint" style="padding:8px">Showing the first 400 rows.</p>' : ''}`;
  },
  cellInfo() {
    const sheet = this.M.draft.sheets.find(s => s.name === this.M.sheet); const cell = sheet?.cells.find(c => c.cell === this.M.selected);
    if (!cell) return '<span class="faint">Select a cell to see its exact value, formula and format.</span>';
    return `<span class="chip">${esc(sheet.name)}!${esc(cell.cell)}</span> <strong>${esc(fmt(cell.value))}</strong>${cell.formula ? ` · formula <code>${esc(cell.formula)}</code>` : ''}${cell.format ? ` · format <code>${esc(cell.format)}</code>` : ''}${cell.hidden ? ' · <span class="badge amber">hidden in Excel</span>' : ''} <span class="faint">· drag it to a field, or click a field's target button</span>`;
  },
  suggestionsHtml() {
    const s = this.M.suggestions;
    if (!s.length) return `<div class="callout">${icon('info')}<div>No confident suggestions for this sheet. Drag the cells you need.</div></div>`;
    return `<div class="card card-pad stack" style="gap:8px"><div class="row between"><strong>${icon('sparkle')} Suggested mapping</strong><div class="row"><button class="btn small primary" id="m-apply-all">Apply all</button><button class="btn small ghost" id="m-close-sugg">Close</button></div></div>
      ${s.map((x, i) => `<div class="suggestion"><span class="col-name">${esc(x.label)}</span><span class="arrow">←</span>${sourceChip(x)}<strong>${esc(fmt(x.value))}</strong><span class="badge ${x.confidence === 'high' ? 'green' : 'amber'}">${esc(x.reason)}</span><span style="flex:1"></span><button class="btn small" data-apply="${i}">Apply</button></div>`).join('')}</div>`;
  },
  wire(root) {
    const M = this.M;
    $$('[data-mode]', root).forEach(b => b.onclick = () => { M.advanced = b.dataset.mode === 'advanced'; this.render(); });
    $('#m-order', root)?.addEventListener('change', e => { M.order = Number(e.target.value); this.render(); });
    $$('[data-sheet]', root).forEach(b => b.onclick = () => { M.sheet = b.dataset.sheet; this.render(); });
    $('#m-disarm', root)?.addEventListener('click', () => { M.armed = null; this.render(); });
    $('#m-done', root).onclick = () => Modal.close();
    $('#m-template', root).onclick = () => { const f = $('#m-template-form', root); f.hidden = !f.hidden; if (!f.hidden) $('#m-template-name', root).focus(); };
    $('#m-template-save', root).onclick = () => busy('Saving template…', async () => { const name = $('#m-template-name', root).value.trim(); if (name.length < 2) throw new Error('Enter a template name.');
      const r = await api('/api/templates', { method: 'POST', body: { draft_id: M.draft.id, name } }); toast(`Template “${r.name}” saved as version ${r.version}.`); $('#m-template-form', root).hidden = true; });
    $('#m-suggest', root).onclick = () => busy('Looking for matching cells…', async () => { M.suggestions = (await api(`/api/drafts/${M.draft.id}/detect`, { method: 'POST', body: { sheet: M.sheet } })).suggestions; this.render(); });
    $('#m-close-sugg', root)?.addEventListener('click', () => { M.suggestions = null; this.render(); });
    $('#m-apply-all', root)?.addEventListener('click', async () => { for (const s of M.suggestions) await this.bind(s.target, s.sheet, s.cell, null, false); M.suggestions = null; this.render(); });
    $$('[data-apply]', root).forEach(b => b.onclick = async () => { const s = M.suggestions[Number(b.dataset.apply)]; M.suggestions.splice(Number(b.dataset.apply), 1); await this.bind(s.target, s.sheet, s.cell); });
    $('#m-undo', root).onclick = () => this.restore(M.undo, M.redo);
    $('#m-redo', root).onclick = () => this.restore(M.redo, M.undo);
    const grid = $('#m-grid', root);
    grid.addEventListener('dragstart', e => { const td = e.target.closest('td[data-cell]'); if (!td) return; e.dataTransfer.setData('text/plain', JSON.stringify({ sheet: M.sheet, cell: td.dataset.cell })); e.dataTransfer.effectAllowed = 'copy'; });
    grid.addEventListener('click', e => { const td = e.target.closest('td[data-cell]'); if (!td) return;
      if (M.armed) { const [target, line] = M.armed.split('@'); M.armed = null; this.bind(target, M.sheet, td.dataset.cell, line === undefined ? null : Number(line)); return; }
      M.selected = td.dataset.cell; $$('td.selected', grid).forEach(x => x.classList.remove('selected')); td.classList.add('selected'); $('#m-cell-info', root).innerHTML = this.cellInfo(); });
    $$('.map-row[data-target]', root).forEach(row => {
      row.addEventListener('dragover', e => { e.preventDefault(); row.classList.add('drop-ok'); });
      row.addEventListener('dragleave', () => row.classList.remove('drop-ok'));
      row.addEventListener('drop', e => { e.preventDefault(); row.classList.remove('drop-ok'); try { const d = JSON.parse(e.dataTransfer.getData('text/plain')); this.bind(row.dataset.target, d.sheet, d.cell, row.dataset.line == null ? null : Number(row.dataset.line)); } catch (_) {} });
      row.addEventListener('mouseenter', () => { const src = this.sources().find(s => s.target === row.dataset.target + (row.dataset.line != null && row.dataset.target.startsWith('column:ORDSTYLE') ? '@' + row.dataset.line : ''));
        $$('td.focus', grid).forEach(x => x.classList.remove('focus')); if (src && src.sheet === M.sheet) { const td = $(`td[data-cell="${src.cell}"]`, grid); if (td) { td.classList.add('focus'); td.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } } });
    });
    $$('[data-arm]', root).forEach(b => b.onclick = () => { const key = b.dataset.arm + (b.dataset.line != null ? '@' + b.dataset.line : '');
      if (M.selected) { this.bind(b.dataset.arm, M.sheet, M.selected, b.dataset.line == null ? null : Number(b.dataset.line)); return; }
      M.armed = key; M.armedLabel = b.closest('[data-label]')?.dataset.label || 'this field'; this.render(); });
    $$('[data-clear]', root).forEach(b => b.onclick = () => this.bind(b.dataset.clear, null, null, b.dataset.line == null ? null : Number(b.dataset.line)));
    $$('[data-edit]', root).forEach(inp => inp.addEventListener('change', () => this.edit(inp.dataset.edit, inp.value, inp.dataset.line == null ? null : Number(inp.dataset.line))));
    $$('[data-addcol]', root).forEach(inp => inp.addEventListener('change', () => { const col = inp.value.trim().toUpperCase(); const table = inp.dataset.addcol;
      if (!M.columns[table].columns.some(c => c.name === col && c.mappable)) return toast(`${col} cannot be mapped here.`, 'attention'); if (!M.added[table].includes(col)) M.added[table].push(col); this.render(); }));
    $('#m-add-line', root)?.addEventListener('click', () => this.structural(o => o.lines.push({ label: '', color: '', declared_total: null, sizes: [{ size: '', quantity: null }] })));
    $$('[data-remove-line]', root).forEach(b => b.onclick = () => this.structural(o => o.lines.splice(Number(b.dataset.removeLine), 1)));
    $$('[data-add-size]', root).forEach(b => b.onclick = () => this.structural(o => o.lines[Number(b.dataset.addSize)].sizes.push({ size: '', quantity: null })));
    $$('[data-remove-size]', root).forEach(b => b.onclick = () => { const [li, si] = b.dataset.removeSize.split(':').map(Number); this.structural(o => o.lines[li].sizes.splice(si, 1)); });
  },
  async bind(target, sheet, cell, line = null, rerender = true) {
    const M = this.M; const before = M.draft.version;
    await busy('Mapping…', async () => {
      M.draft = await api(`/api/drafts/${M.draft.id}/bind`, { method: 'POST', body: { version: M.draft.version, order: Math.min(M.order, M.draft.orders.length), target, sheet, cell, line } });
      M.undo.push(before); M.redo = []; if (rerender) this.render();
    });
  },
  async put(orders) {
    const M = this.M; const before = M.draft.version;
    await busy('Saving…', async () => { M.draft = await api(`/api/drafts/${M.draft.id}`, { method: 'PUT', body: { version: M.draft.version, orders } }); M.undo.push(before); M.redo = []; this.render(); });
  },
  structural(change) { const orders = structuredClone(this.M.draft.orders); change(orders[this.M.order]); this.put(orders); },
  edit(target, value, line) {
    const orders = structuredClone(this.M.draft.orders); const o = orders[this.M.order]; let m;
    if ((m = /^field:(\w+)$/.exec(target))) o[m[1]] = value;
    else if ((m = /^column:(ORDMAS|ORDSTYLE)\.(\w+)$/.exec(target))) { const holder = m[1] === 'ORDMAS' ? o : o.lines[line]; holder.extras ||= {}; holder.extras[m[2]] = { value }; if (value === '') delete holder.extras[m[2]]; }
    else if ((m = /^line:(\d+):(\w+)$/.exec(target))) o.lines[Number(m[1])][m[2]] = value;
    else if ((m = /^size:(\d+):(\d+):(\w+)$/.exec(target))) { const s = o.lines[Number(m[1])].sizes[Number(m[2])]; s[m[3]] = m[3] === 'quantity' ? (value.trim() === '' ? null : value.trim()) : value; }
    this.put(orders);
  },
  async restore(from, to) {
    const M = this.M; const target = from.pop(); if (target == null) return;
    await busy('Restoring…', async () => { const current = M.draft.version; M.draft = await api(`/api/drafts/${M.draft.id}/restore`, { method: 'POST', body: { version: current, to_version: target } }); to.push(current); this.render(); });
  },
};
