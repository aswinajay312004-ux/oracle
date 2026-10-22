'use strict';
/* Compare with Excel: Oracle columns on the left, the original workbook on the right. Click a column to jump to the
   Excel cell its value came from; click a cell to see which Oracle column(s) it fills. "Change mapping" lets the
   person pick a different cell for a column, leave it blank, type a value, or give the next OC number. */
const CMP = { table: null, col: null, cell: null, sheet: null, edit: false, q: '', all: false, pane: 'oracle', split: 42 };

function openCompare(table, col, cellref) {
  const d = NO.draft; if (!d?.plan) { toast('The preview is not ready yet.', 'attention'); return; }
  Object.assign(CMP, { table: table || mainTable(d), col: col || null, cell: null, edit: false, q: '', sheet: null });
  if (cellref) { const r = splitRef(cellref); if (r) { CMP.sheet = r.sheet; CMP.cell = r.cell; } }
  if (col) { const src = sourceOf(d, CMP.table, col); if (src) { CMP.sheet = src.sheet; CMP.cell = src.cell; } }
  CMP.sheet ||= d.sheets.find(s => !s.hidden)?.name || d.sheets[0]?.name;
  Modal.open('Compare with Excel', '<div id="cmp-root"></div>', { full: true, onClose: () => { if (location.hash.startsWith('#/new/')) previewPage($('#view')); } });
  drawCompare();
}
function sourceOf(d, t, col) {
  const v = viewOf(d, t); const b = v?.boxes?.[col]; if (!b) return null;
  if (b.source?.cell) return { sheet: b.source.sheet, cell: b.source.cell };
  const first = (v.row_sources || []).find(r => r[col]); return first ? splitRef(first[col]) : null;
}
function cellsOf(d, t, col) {   // every Excel cell feeding a column (all rows)
  const v = viewOf(d, t); const out = new Set(); const b = v?.boxes?.[col];
  if (b?.source?.cell) out.add(`${b.source.sheet}!${b.source.cell}`);
  for (const r of v?.row_sources || []) if (r[col]) out.add(r[col]);
  return out;
}
const tint = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };

function drawCompare() {
  const d = NO.draft, root = $('#cmp-root'); if (!root) return;
  const tables = Object.keys(d.plan.tables); const t = CMP.table; const v = viewOf(d, t) || { boxes: {} };
  const cols = colsOf(d, t).filter(c => { const b = v.boxes[c.name] || {}; const q = CMP.q.toLowerCase();
    if (q && !`${c.name} ${c.label} ${b.value ?? ''} ${(b.values || []).join(' ')} ${b.excel_label ?? ''}`.toLowerCase().includes(q)) return false;
    return CMP.all || CMP.edit || b.count || ['empty_label', 'error', 'auto'].includes(b.state); });
  const sheet = d.sheets.find(s => s.name === CMP.sheet) || d.sheets[0];
  const cov = d.coverage?.cells?.[sheet.name] || {};
  const marks = {}, style = {};
  for (const [addr, [state, group]] of Object.entries(cov)) {
    if (state === 'mapped') { style[addr] = `background:${tint(groupColor(group), .2)};box-shadow:inset 3px 0 0 ${groupColor(group)}`; }
    else if (state === 'label') style[addr] = `background:${tint(groupColor(group), .08)};font-weight:600`;
  }
  const lit = CMP.col ? cellsOf(d, t, CMP.col) : new Set();
  for (const ref of lit) { const r = splitRef(ref); if (r?.sheet === sheet.name) marks[r.cell] = 'hit'; }
  if (CMP.cell && !marks[CMP.cell]) marks[CMP.cell] = 'hit';
  const info = CMP.cell ? cov[CMP.cell] : null;
  const cellVal = CMP.cell ? sheet.cells.find(c => c.cell === CMP.cell) : null;
  const selBox = CMP.col ? v.boxes[CMP.col] : null; const selCol = CMP.col ? colsOf(d, t).find(c => c.name === CMP.col) : null;
  const usedGroups = [...new Set(Object.values(cov).filter(x => x[0] !== 'kept').map(x => x[1]))];
  root.innerHTML = `<div class="cmp-top"><div class="seg">${tables.map(x => `<button type="button" data-ct="${esc(x)}" class="${x === t ? 'active' : ''}">${esc(x)}</button>`).join('')}</div>
      <label class="switch"><input type="checkbox" id="cmp-edit" ${CMP.edit ? 'checked' : ''}> <span>Change mapping</span></label>
      <div class="legend">${usedGroups.map(g => `<span><i style="background:${groupColor(g)}"></i>${esc(groupName(g))}</span>`).join('')}<span><i style="background:#fff;border:1px solid var(--line-strong)"></i>Preserved — not mapped</span></div>
      <div class="seg cmp-switch"><button type="button" data-pane="oracle" class="${CMP.pane === 'oracle' ? 'active' : ''}">Oracle</button><button type="button" data-pane="excel" class="${CMP.pane === 'excel' ? 'active' : ''}">Excel</button></div></div>
    ${CMP.edit ? `<div class="mp-hint">${icon('info')}<span>${CMP.col ? `Click the Excel cell that holds <b>${esc(CMP.col)}</b> (${esc(selCol?.label || '')}). Or use the buttons on the column.` : 'Pick an Oracle column on the left, then click its Excel cell on the right.'}</span></div>` : ''}
    <div class="cmp pane-${CMP.pane}" id="cmp" style="grid-template-columns:minmax(280px,${CMP.split}%) 10px minmax(0,1fr)">
      <div class="cmp-pane"><div class="search">${icon('search')}<input id="cmp-q" placeholder="Find a column" value="${esc(CMP.q)}" aria-label="Find a column"></div>
        ${CMP.edit ? '' : `<label class="muted" style="font-size:12.5px"><input type="checkbox" id="cmp-all" ${CMP.all ? 'checked' : ''}> Show blank columns too</label>`}
        <div class="cmp-list" id="cmp-list">${cols.map(c => cmpItem(c, v.boxes[c.name] || {}, t)).join('') || '<div class="empty">No column matches.</div>'}</div></div>
      <div class="cmp-divider" id="cmp-div" role="separator" aria-orientation="vertical" tabindex="0" title="Drag to resize"></div>
      <div class="cmp-pane"><div class="xl-tabs">${d.sheets.map(s => `<button type="button" data-sheet="${esc(s.name)}" class="${s.name === sheet.name ? 'active' : ''}">${esc(s.name)}${s.hidden ? ' (hidden)' : ''}</button>`).join('')}</div>
        <div class="cmp-detail">${detailText(d, t, sheet, info, cellVal, selCol, selBox)}</div>
        <div class="xl-wrap" id="xl">${xlGrid(sheet, { marks, style, selected: CMP.cell })}</div></div></div>`;
  paintIcons(root); wireCompare(root, d, sheet);
  const target = CMP.cell || [...lit].map(splitRef).find(r => r?.sheet === sheet.name)?.cell;
  if (target) requestAnimationFrame(() => scrollToCell($('#xl', root), target));
  const sel = $('.cmp-item.sel', root); if (sel) sel.scrollIntoView({ block: 'nearest' });
}
function cmpItem(c, b, t) {
  const src = b.source || {}; const st = b.state || 'blank';
  const value = b.level === 'row' ? `${(b.values || []).slice(0, 3).join(', ')}${b.distinct > 3 ? '…' : ''} <small class="muted">(${b.count} rows)</small>` : esc(fmtCell(b.value ?? '—'));
  return `<div class="cmp-item s-${st}${CMP.col === c.name ? ' sel' : ''}" data-col="${esc(c.name)}" tabindex="0" role="button">
    <span class="gdot" style="background:${groupColor(b.group)}" title="${esc(groupName(b.group))}"></span>
    <div class="grow"><div class="row between" style="gap:6px"><strong>${esc(c.label)}</strong><code>${esc(c.name)}</code></div>
      <div class="v">${b.count ? value : `<span class="muted">${esc(b.note || STATE_TEXT[st] || '')}</span>`}</div>
      <small class="muted">${src.cell ? `${esc(src.cell)}${b.excel_label ? ' · ' + esc(b.excel_label) : ''}` : esc(STATE_TEXT[st] || '')}${st === 'ai' ? ' · <b style="color:var(--violet)">AI — please check</b>' : ''}</small>
      ${CMP.edit && CMP.col === c.name ? `<div class="mp-actions">${c.name === 'IONO' ? '<button class="btn small" data-act="auto_oc">Next OC number</button>' : ''}
        <button class="btn small" data-act="text">Type a value</button><button class="btn small" data-act="blank">Leave blank</button><button class="btn small" data-act="na">Not applicable</button>
        ${b.binding && !b.binding.auto ? '<button class="btn small" data-act="auto">Back to automatic</button>' : ''}</div>` : ''}</div></div>`;
}
function detailText(d, t, sheet, info, cell, col, box) {
  if (CMP.cell && cell) {
    const refs = info?.[2] || [];
    return `<span class="chip">${esc(sheet.name)}!${esc(CMP.cell)}</span> <strong>${esc(fmtCell(cell.value))}</strong>${cell.formula ? ` <span class="muted">(formula ${esc(cell.formula)})</span>` : ''} →
      ${refs.length ? refs.map(r => `<button class="chip" data-ref="${esc(r)}">${esc(r)}</button>`).join(' ') + (info[0] === 'label' ? ' <span class="muted">(this is the label of that value)</span>' : '')
        : '<span class="status-chip grey">Preserved — not mapped to Oracle</span>'}`;
  }
  if (col && box) return `<strong>${esc(col.label)}</strong> <code>${esc(col.name)}</code> ← ${box.source?.cell ? `<span class="chip">${esc(box.source.sheet)}!${esc(box.source.cell)}</span> ${esc(box.excel_label || '')}` : esc(box.note || STATE_TEXT[box.state] || '')}`;
  return '<span class="muted">Click a column on the left to see its Excel cell, or click any Excel cell.</span>';
}
function wireCompare(root, d, sheet) {
  $$('[data-ct]', root).forEach(b => b.onclick = () => { CMP.table = b.dataset.ct; CMP.col = null; drawCompare(); });
  $$('[data-pane]', root).forEach(b => b.onclick = () => { CMP.pane = b.dataset.pane; drawCompare(); });
  $('#cmp-edit', root).onchange = e => { CMP.edit = e.target.checked; drawCompare(); };
  $('#cmp-all', root)?.addEventListener('change', e => { CMP.all = e.target.checked; drawCompare(); });
  const q = $('#cmp-q', root); q.oninput = () => { CMP.q = q.value; const pos = q.selectionStart; drawCompare(); const nq = $('#cmp-q'); nq.focus(); nq.setSelectionRange(pos, pos); };
  $$('.cmp-item', root).forEach(el => {
    const pick = () => { CMP.col = el.dataset.col; const src = sourceOf(d, CMP.table, CMP.col); CMP.cell = null;
      if (src) { CMP.sheet = src.sheet; CMP.cell = src.cell; } if (window.innerWidth < 900 && src && !CMP.edit) CMP.pane = 'excel'; drawCompare(); };
    el.onclick = e => { if (!e.target.closest('[data-act]')) pick(); }; el.onkeydown = e => { if (e.key === 'Enter') pick(); };
    el.ondragover = e => { if (CMP.edit) { e.preventDefault(); el.classList.add('drop'); } }; el.ondragleave = () => el.classList.remove('drop');
    el.ondrop = e => { e.preventDefault(); el.classList.remove('drop'); const addr = e.dataTransfer.getData('text/cell'); if (addr) { CMP.col = el.dataset.col; bindCell(d, sheet, addr); } };
  });
  $$('[data-act]', root).forEach(b => b.onclick = async () => {
    const act = b.dataset.act; let binding = { kind: act };
    if (act === 'text') { const value = prompt(`Type the value for ${CMP.col}`); if (value == null) return; binding = { kind: 'text', value }; }
    await rebind(d, binding);
  });
  $$('[data-sheet]', root).forEach(b => b.onclick = () => { CMP.sheet = b.dataset.sheet; CMP.cell = null; drawCompare(); });
  $$('[data-ref]', root).forEach(b => b.onclick = () => { const [tt, col] = b.dataset.ref.split('.'); CMP.table = tt; CMP.col = col; drawCompare(); });
  const grid = $('#xl', root);
  grid.addEventListener('click', e => { const td = e.target.closest('td.c'); if (!td || td.classList.contains('e') && !CMP.edit) return;
    if (CMP.edit && CMP.col) { bindCell(d, sheet, td.dataset.cell); return; }
    CMP.cell = td.dataset.cell; const refs = d.coverage?.cells?.[sheet.name]?.[CMP.cell]?.[2] || [];
    if (refs.length) { const [tt, col] = refs[0].split('.'); CMP.table = tt; CMP.col = col; } else CMP.col = null;
    if (window.innerWidth < 900 && refs.length) CMP.pane = 'oracle';
    drawCompare(); });
  if (CMP.edit) $$('td.c:not(.e)', grid).forEach(td => { td.draggable = true; td.ondragstart = e => e.dataTransfer.setData('text/cell', td.dataset.cell); });
  const div = $('#cmp-div', root), box = $('#cmp', root);
  div.onpointerdown = e => { div.setPointerCapture(e.pointerId); const r = box.getBoundingClientRect();
    div.onpointermove = ev => { CMP.split = Math.max(22, Math.min(70, (ev.clientX - r.left) / r.width * 100)); box.style.gridTemplateColumns = `minmax(280px,${CMP.split}%) 10px minmax(0,1fr)`; };
    div.onpointerup = () => { div.onpointermove = null; }; };
  div.onkeydown = e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { CMP.split = Math.max(22, Math.min(70, CMP.split + (e.key === 'ArrowLeft' ? -3 : 3))); box.style.gridTemplateColumns = `minmax(280px,${CMP.split}%) 10px minmax(0,1fr)`; } };
}
async function bindCell(d, sheet, addr) {
  const info = d.plan.tables[CMP.table];
  const sec = info?.section; const cell = sheet.cells.find(c => c.cell === addr);
  if (!cell) { toast(`${addr} is empty. Choose a cell that holds the value.`, 'attention'); return; }
  let binding = { kind: 'cell', sheet: sheet.name, cell: addr };
  if (info?.rows_kind === 'section' && sec && sheet.name === sec.sheet && cell.row >= sec.header_row && cell.row <= sec.end_row) binding = { kind: 'header', col: cell.col };
  CMP.cell = addr; await rebind(d, binding);
}
async function rebind(d, binding) {
  await busy('Updating the mapping…', async () => {
    NO.draft = await api(`/api/drafts/${d.id}/bind`, { method: 'POST', body: { version: d.version, table: CMP.table, column: CMP.col, binding } });
    toast(`${CMP.col} updated. ${binding.kind === 'cell' ? 'POPPYS will remember this Excel label next time.' : ''}`); drawCompare();
  });
}
