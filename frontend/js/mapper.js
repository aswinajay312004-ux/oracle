// New order → Map to Oracle. Left: the uploaded Excel sheet as it is (same rows, columns and merged cells). Right: the
// Oracle tables an order is saved in, with their columns. The person selects cells on the left (drag, Shift+click,
// Ctrl+click, a column letter or a row number, or types a range) and gives them to an Oracle column on the right. Several
// Excel columns can go to several Oracle columns at once (tick the Oracle columns, then "Map in order").
// The first mapping is the one POPPYS found by itself; nothing is mapped that the person does not see here.
import { $, $$, esc, fmt, notify, busy } from './util.js';
import { api, id } from './api.js';

const TABLE_TONE = { ORDMAS: 't-ord', ORDSTYLE: 't-line', ORDSIZE: 't-size', ORDACC: 't-acc', ORDFAB: 't-fab', ORDYARN: 't-yarn' };
const KIND_HELP = {
  field: 'One value for the order', extra: 'One value', line: 'Each selected cell is one colour line',
  size: 'Each selected cell is a size name (its column)', qty: 'Each cell is the quantity of the colour in its row and the size in its column',
  row: 'One Oracle row per Excel row selected',
};
const M = { draft: null, order: 0, orders: 1, sheet: null, tables: [], targets: {}, typed: {}, automatic: true, dirty: false,
  sel: new Set(), anchor: null, drag: null, checked: [], q: '', more: new Set(), showHidden: false, tds: new Map() };
let hooks = {};
const svgI = d => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICON = { user: svgI('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'), doc: svgI('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>'), tag: svgI('<path d="M20 12 12 20 3 11V3h8z"/><circle cx="7.5" cy="7.5" r="1.5"/>'), cal: svgI('<rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M3 10h18M8 3v4M16 3v4"/>'), box: svgI('<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>'), layers: svgI('<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>'), target: svgI('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>'), sheet: svgI('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>'), link: svgI('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>') };

export const catalog = () => M.tables;
const colName = c => { let s = ''; while (c) { const r = (c - 1) % 26; s = String.fromCharCode(65 + r) + s; c = (c - 1 - r) / 26; } return s; };
const ref = (r, c) => `${colName(c)}${r}`;
const parse = a => { const m = /^([A-Z]{1,3})(\d+)$/.exec(String(a).toUpperCase()); if (!m) return null; let c = 0; for (const ch of m[1]) c = c * 26 + ch.charCodeAt(0) - 64; return [Number(m[2]), c]; };
const key = (r, c) => `${r},${c}`;
const show = v => v == null ? '' : typeof v === 'number' ? fmt(v) : String(v);
const sheetOf = () => M.draft.sheets.find(s => s.name === M.sheet) || M.draft.sheets[0];
const columnOf = target => { const [t, c] = target.split('.'); return M.tables.find(x => x.table === t)?.columns.find(x => x.name === c); };

// ------------------------------------------------------------------ load
export async function openMapper(root, draft, h, order = 0) {
  hooks = h; M.draft = draft; M.order = order;
  const m = await api(`/api/drafts/${id(draft.id)}/oracle-map?order=${order}`);
  Object.assign(M, { orders: Math.max(1, m.orders), sheet: m.sheet, tables: m.tables, targets: m.targets, typed: m.typed,
    automatic: m.automatic, dirty: false, sel: new Set(), anchor: null, checked: [], q: '', more: new Set() });
  draw(root);
}

// Typed values for blank fields come from the preview: same mapping, one value more.
export async function setTyped(draft, order, target, value) {
  const m = await api(`/api/drafts/${id(draft.id)}/oracle-map?order=${order}`);
  const typed = { ...m.typed }; if (String(value).trim()) typed[target] = String(value).trim(); else delete typed[target];
  return api(`/api/drafts/${id(draft.id)}/oracle-map`, { method: 'POST', body: { version: draft.version, order, sheet: m.sheet, targets: m.targets, typed } });
}

// Saves the mapping when it changed (else the order read automatically stays as it is). Returns the draft.
export async function saveMapping() {
  if (!M.dirty) return M.draft;
  const targets = Object.fromEntries(Object.entries(M.targets).filter(([, v]) => v.length));
  M.draft = await api(`/api/drafts/${id(M.draft.id)}/oracle-map`, { method: 'POST', body: { version: M.draft.version, order: M.order, sheet: M.sheet, targets, typed: M.typed } });
  M.dirty = false; M.automatic = false;
  return M.draft;
}

// ------------------------------------------------------------------ the screen
function draw(root) {
  const d = M.draft; const sheets = d.sheets.filter(s => !s.hidden || s.name === M.sheet);
  const g = sheetOf(); const o = d.orders?.[M.order] || {};
  const pieces = o.workbook_pieces ?? (o.lines || []).reduce((n, l) => n + (l.sizes || []).reduce((q, z) => q + (Number(z.quantity) || 0), 0), 0);
  const ext = (d.filename.match(/\.([a-z0-9]+)$/i)?.[1] || '').toUpperCase();
  const fact = (icon, label, value) => `<div class="mf"><span class="mf-i" aria-hidden="true">${ICON[icon]}</span><span><small>${esc(label)}</small><b>${esc(value == null || value === '' ? '—' : value)}</b></span></div>`;
  root.innerHTML = `<div class="map-top">
    <div class="map-bar card">
      <span class="map-xl" aria-hidden="true">${/^(XLSX|XLSM|XLS|XLSB|ODS|CSV)$/.test(ext) ? '<b>X</b>' : ICON.doc}</span>
      <div class="map-file"><b>${esc(d.filename)}</b>
        <span class="map-meta">${sheets.length > 1 ? `<label class="small-text">Sheet <select id="map-sheet">${sheets.map(s => `<option ${s.name === M.sheet ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>` : `<span>Sheet: ${esc(M.sheet)}</span>`}
          <span>${fmt(g.rows || 0, 0)} rows</span><span>${fmt(g.cols || 0, 0)} columns</span>
          ${M.orders > 1 ? `<label class="small-text">Order <select id="map-order">${d.orders.map((x, i) => `<option value="${i}" ${i === M.order ? 'selected' : ''}>${i + 1}. ${esc(x.style || x.order_ref || 'order')}</option>`).join('')}</select></label>` : ''}</span></div>
      <span class="spacer"></span>
      <span class="pill ${M.automatic ? 'brand' : 'info'}" id="map-state">${M.automatic ? 'Mapped automatically' : 'Your mapping'}</span></div>
    <section class="card map-facts" aria-labelledby="mf-title"><div class="map-card-head"><span class="mh-i" aria-hidden="true">${ICON.target}</span><div><h2 id="mf-title">Detected order details</h2><p class="muted small-text">As POPPYS read them from the file</p></div></div>
      <div class="mf-grid">${fact('user', 'Buyer', o.buyer)}${fact('doc', 'Order ID', o.order_ref)}${fact('tag', 'Style', o.style)}${fact('cal', 'Season', o.season)}${fact('box', 'Total pieces', pieces ? fmt(pieces, 0) : '')}${fact('layers', 'Total packs', o.packs)}</div></section>
    </div>
    <div class="map-panes">
      <section class="card map-excel" aria-label="The Excel file">
        <div class="map-card-head"><span class="mh-i" aria-hidden="true">${ICON.sheet}</span><div><h2>Excel</h2><p class="muted small-text">Sheet ${esc(M.sheet)}</p></div></div>
        <div class="sel-bar"><span id="sel-info" aria-live="polite">Nothing selected</span>
          <form id="sel-form" class="sel-form"><label class="visually-hidden" for="sel-range">Cells</label><input id="sel-range" placeholder="Type cells, e.g. E12:P20" maxlength="200"><button class="secondary small">Select</button></form>
          <button class="ghost small" data-m="clear-sel">Clear</button>
          <label class="check small-text"><input type="checkbox" id="map-hidden" ${M.showHidden ? 'checked' : ''}> Hidden rows</label></div>
        <div class="xg-wrap map-grid" id="map-grid"></div>
        <ul class="map-legend">${M.tables.map(t => `<li><span class="tone ${TABLE_TONE[t.table]}"></span>${esc(t.business)} <code>${esc(t.table)}</code></li>`).join('')}</ul>
        <p class="map-help">Select cells in the Excel on the left — drag, <kbd>Shift</kbd>+click, <kbd>Ctrl</kbd>+click, or click a column letter / row number — then press <b>Use selection</b> on an Oracle column on the right. A blank cell stays blank.</p>
      </section>
      <section class="card map-oracle" aria-label="Oracle tables">
        <div class="map-card-head"><span class="mh-i" aria-hidden="true">${ICON.link}</span><div><h2>Oracle columns</h2><p class="muted small-text">Give each one its Excel cells</p></div></div>
        <div class="oc-search"><input id="oc-q" type="search" placeholder="Find an Oracle column (e.g. remarks, season, DIA)" value="${esc(M.q)}" aria-label="Find an Oracle column"></div>
        <div id="oc-multi" class="oc-multi" hidden></div>
        <div id="oc-tables"></div>
      </section>
    </div>
    <div class="map-foot"><button class="ghost" data-m="new">← New upload</button><button class="primary" data-m="continue">Continue to preview →</button></div>`;
  drawGrid(); drawOracle(); paint(); bind(root);
}

function drawGrid() {
  const g = sheetOf(); const box = $('#map-grid');
  // rows / columns whose cells are all hidden in Excel are left out unless "Hidden rows" is ticked
  const hidR = new Set(), hidC = new Set();
  if (!M.showHidden) {
    const seen = (by, pick) => { const m = new Map(); g.cells.forEach(x => { const k = pick(x); m.set(k, (m.get(k) ?? true) && !!x.hidden); }); m.forEach((all, k) => all && by.add(k)); };
    seen(hidR, x => x.row); seen(hidC, x => x.col);
  }
  const cells = new Map(g.cells.map(x => [key(x.row, x.col), x]));
  const used = new Set(g.cells.map(x => x.row));
  const maxR = Math.min(g.rows, 3000), maxC = Math.min(g.cols, 80);
  const cols = []; for (let c = 1; c <= maxC; c++) if (!hidC.has(c)) cols.push(c);
  const span = new Map(), covered = new Set(), keep = new Set();
  for (const rng of g.merged || []) {
    const [a, b] = String(rng).split(':').map(parse);
    if (!a || !b) continue;
    span.set(key(a[0], a[1]), [b[0] - a[0] + 1, b[1] - a[1] + 1]);
    for (let r = a[0]; r <= b[0]; r++) { keep.add(r); for (let c = a[1]; c <= b[1]; c++) if (r !== a[0] || c !== a[1]) covered.add(key(r, c)); }
  }
  const rows = []; let gap = null;
  const row = r => `<tr><th scope="row" class="rh" data-row="${r}" title="Select row ${r}">${r}</th>${cols.map(c => {
    const k = key(r, c); if (covered.has(k)) return '';
    const x = cells.get(k); const sp = span.get(k);
    const attrs = sp ? `${sp[0] > 1 ? ` rowspan="${sp[0]}"` : ''}${sp[1] > 1 ? ` colspan="${sp[1]}"` : ''}` : '';
    const v = show(x?.value);
    return `<td${attrs} data-k="${k}" class="${typeof x?.value === 'number' ? 'num' : ''}${x?.hidden ? ' hid' : ''}" title="${esc(`${ref(r, c)}${v ? ': ' + v : ''}`)}">${esc(v)}</td>`;
  }).join('')}</tr>`;
  const flush = () => { if (!gap) return; if (gap[1] - gap[0] >= 2) rows.push(`<tr class="xg-gap"><th scope="row">${gap[0]}–${gap[1]}</th><td colspan="${cols.length}">empty rows</td></tr>`); else for (let r = gap[0]; r <= gap[1]; r++) rows.push(row(r)); gap = null; };
  for (let r = 1; r <= maxR; r++) {
    if (hidR.has(r)) { flush(); continue; }
    if (!used.has(r) && !keep.has(r)) { gap = gap ? [gap[0], r] : [r, r]; continue; }
    flush(); rows.push(row(r));
  }
  flush();
  box.innerHTML = `<table class="xg map-xg"><thead><tr><th class="corner"></th>${cols.map(c => `<th scope="col" data-col="${c}" title="Select column ${colName(c)}">${colName(c)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>
    ${g.rows > maxR || g.cols > maxC ? `<p class="small-text muted">Showing ${fmt(maxR, 0)} rows and ${maxC} columns of ${fmt(g.rows, 0)} × ${g.cols}.</p>` : ''}`;
  M.tds = new Map([...box.querySelectorAll('td[data-k]')].map(td => [td.dataset.k, td]));
}

// cells that hold a value (merged areas count once, at their top-left cell)
const filled = k => { const td = M.tds.get(k); return !!td && td.textContent.trim() !== ''; };

function owners() {
  const own = new Map();
  for (const [target, refs] of Object.entries(M.targets)) {
    const tone = TABLE_TONE[target.split('.')[0]];
    for (const a of refs) { const p = parse(a); if (p) { const k = key(...p); if (!own.has(k)) own.set(k, { tone, targets: [] }); own.get(k).targets.push(target); } }
  }
  return own;
}

function paint() {
  const own = owners();
  for (const [k, td] of M.tds) {
    const o = own.get(k);
    td.className = td.className.replace(/\s?(t-\w+|sel|mapped)/g, '');
    if (o) { td.classList.add('mapped', o.tone); td.dataset.to = o.targets.join(', '); } else delete td.dataset.to;
    if (M.sel.has(k)) td.classList.add('sel');
  }
  const cells = [...M.sel].filter(filled).map(k => k.split(',').map(Number));
  const rows = [...new Set(cells.map(x => x[0]))].sort((a, b) => a - b), cols = [...new Set(cells.map(x => x[1]))].sort((a, b) => a - b);
  $('#sel-info').innerHTML = cells.length ? `<b>${fmt(cells.length, 0)}</b> cell${cells.length > 1 ? 's' : ''} with values · ${cols.length > 1 ? `columns ${colName(cols[0])}–${colName(cols.at(-1))} (${cols.length})` : `column ${colName(cols[0])}`} · ${rows.length > 1 ? `rows ${rows[0]}–${rows.at(-1)}` : `row ${rows[0]}`}`
    : M.sel.size ? 'The selected cells are empty' : 'Nothing selected';
  $$('[data-use]').forEach(b => { b.disabled = !cells.length; });
  drawMulti(cols.length);
}

// ------------------------------------------------------------------ the Oracle side
function typeText(c) {
  if (c.master && c.kind !== 'extra' && c.kind !== 'row') return `a name, looked up in ${c.master}`;
  if (c.type === 'DATE' || String(c.type).startsWith('TIMESTAMP')) return 'date';
  if (c.type === 'NUMBER') return 'number';
  return c.length ? `text · up to ${c.length}` : 'text';
}

function summary(target) {
  const refs = M.targets[target] || [];
  if (M.typed[target]) return `<span class="oc-typed">typed: <b>${esc(M.typed[target])}</b></span>`;
  if (!refs.length) return '<span class="muted">blank</span>';
  const first = M.tds.get(key(...(parse(refs[0]) || [0, 0])))?.textContent.trim() || '';
  const where = refs.length <= 3 ? refs.join(', ') : `${refs[0]} … ${refs.at(-1)} · ${fmt(refs.length, 0)} cells`;
  return `<span class="db-cell">${esc(where)}</span> ${first ? `<b>${esc(first.length > 28 ? first.slice(0, 27) + '…' : first)}</b>` : ''}${refs.length > 1 ? ' …' : ''}`;
}

function drawOracle() {
  const q = M.q.trim().toLowerCase();
  const match = c => !q || c.name.toLowerCase().includes(q) || String(c.label).toLowerCase().includes(q);
  $('#oc-tables').innerHTML = M.tables.map(t => {
    const target = c => `${t.table}.${c.name}`;
    const mapped = c => (M.targets[target(c)] || []).length || M.typed[target(c)];
    const auto = t.columns.filter(c => c.kind === 'auto');
    const main = t.columns.filter(c => c.kind !== 'auto' && (mapped(c) || (!c.system && (c.kind !== 'extra' && c.kind !== 'row' || c.standard))));
    const rest = t.columns.filter(c => c.kind !== 'auto' && !main.includes(c) && (!c.system || q));
    const first = { field: 0, line: 0, size: 0, qty: 0 };
    main.sort((a, b) => (a.kind in first ? 0 : 1) - (b.kind in first ? 0 : 1));   // the columns POPPYS fills first, then the others
    const shown = q ? [...main, ...rest].filter(match) : M.more.has(t.table) ? [...main, ...rest] : main;
    const n = t.columns.filter(mapped).length;
    if (q && !shown.length) return '';
    return `<section class="ot ${TABLE_TONE[t.table]}" data-table="${esc(t.table)}">
      <header class="ot-head"><span class="tone ${TABLE_TONE[t.table]}"></span><code>${esc(t.table)}</code><b>${esc(t.business)}</b>
        <span class="muted small-text">${esc(t.rows)}</span><span class="spacer"></span>${n ? `<span class="pill">${n} mapped</span>` : ''}</header>
      <ul class="oc-list">${shown.map(c => `<li class="oc ${mapped(c) ? 'on' : ''}" data-target="${esc(target(c))}">
          <label class="oc-tick" title="Tick to map several Excel columns at once"><input type="checkbox" data-tick="${esc(target(c))}" ${M.checked.includes(target(c)) ? 'checked' : ''} aria-label="Tick ${esc(c.name)}"></label>
          <button class="oc-name" data-show="${esc(target(c))}" title="${esc(`${c.name}: ${c.label}. ${KIND_HELP[c.kind] || ''}`)}"><span><code>${esc(c.name)}</code> ${esc(c.label)}</span><small>${esc(typeText(c))}</small></button>
          <div class="oc-val">${summary(target(c))}</div>
          <span class="oc-status ${M.typed[target(c)] ? 'typed' : (M.targets[target(c)] || []).length ? 'on' : ''}">${M.typed[target(c)] ? 'Typed' : (M.targets[target(c)] || []).length ? 'Mapped' : 'Blank'}</span>
          <div class="oc-act"><button class="secondary tiny" data-use="${esc(target(c))}" disabled>Use selection</button>${mapped(c) ? `<button class="ghost tiny" data-clear="${esc(target(c))}" aria-label="Clear ${esc(c.name)}" title="Clear">✕</button>` : ''}</div></li>`).join('')}</ul>
      ${!q && rest.length ? `<button class="link small-text ot-more" data-more="${esc(t.table)}">${M.more.has(t.table) ? 'Show fewer columns' : `Show ${rest.length} more columns`}</button>` : ''}
      ${!q && auto.length ? `<details class="ot-auto"><summary>${auto.length} set by POPPYS</summary><ul>${auto.map(c => `<li><code>${esc(c.name)}</code> ${esc(c.note || '')}</li>`).join('')}</ul></details>` : ''}
    </section>`;
  }).join('') || '<p class="empty">No Oracle column matches.</p>';
}

function drawMulti(excelCols) {
  const box = $('#oc-multi'); const n = M.checked.length;
  box.hidden = n < 2;
  if (n < 2) return;
  const ok = excelCols === n;
  box.innerHTML = `<b>${n} Oracle columns ticked</b> · ${ok ? `the ${n} selected Excel columns go to them in order` : `select ${n} Excel columns (now ${excelCols})`}
    <button class="primary small" data-m="map-order" ${ok ? '' : 'disabled'}>Map in order</button><button class="ghost small" data-m="untick">Untick all</button>`;
}

// ------------------------------------------------------------------ actions
function selected() { return [...M.sel].filter(filled).map(k => k.split(',').map(Number)).sort((a, b) => a[1] - b[1] || a[0] - b[0]); }

function assign(target, cells) {
  if (!cells.length) return notify('Select cells with values first.', 'warning');
  const col = columnOf(target);
  M.targets[target] = cells.map(([r, c]) => ref(r, c)); delete M.typed[target];
  M.dirty = true; M.automatic = false;
  const n = cells.length;
  notify(`${target}: ${n} cell${n > 1 ? 's' : ''} ${col?.kind === 'qty' ? 'as quantities' : col?.kind === 'line' ? `→ ${n} colour line${n > 1 ? 's' : ''}` : col?.kind === 'row' ? `→ ${new Set(cells.map(x => x[0])).size} rows` : 'mapped'}.`, 'success');
}

function refresh() { drawOracle(); paint(); $('#map-state').textContent = M.automatic ? 'Mapped automatically' : 'Your mapping'; $('#map-state').className = `pill ${M.automatic ? 'brand' : 'info'}`; }

function rect(a, b) {
  const out = new Set();
  for (let r = Math.min(a[0], b[0]); r <= Math.max(a[0], b[0]); r++) for (let c = Math.min(a[1], b[1]); c <= Math.max(a[1], b[1]); c++) if (M.tds.has(key(r, c))) out.add(key(r, c));
  return out;
}
const used = () => [...M.tds.keys()].filter(filled).map(k => k.split(',').map(Number));

function selectRange(text) {
  const out = new Set();
  for (const part of String(text).toUpperCase().split(/[,;\s]+/).filter(Boolean)) {
    const [a, b] = part.split(':').map(parse);
    if (!a) return notify(`“${part}” is not a cell such as E12 or a range such as E12:P20.`, 'error');
    rect(a, b || a).forEach(k => out.add(k));
  }
  M.sel = out; M.anchor = null; paint();
}

function bind(root) {
  const grid = $('#map-grid');
  grid.addEventListener('mousedown', e => {
    const td = e.target.closest('td[data-k]'); if (!td || e.button !== 0) return;
    e.preventDefault();
    const p = td.dataset.k.split(',').map(Number);
    const add = e.ctrlKey || e.metaKey;
    if (e.shiftKey && M.anchor) { M.sel = new Set([...(add ? M.sel : []), ...rect(M.anchor, p)]); paint(); return; }
    M.anchor = p; M.drag = { base: add ? new Set(M.sel) : new Set() };
    M.sel = new Set([...M.drag.base, key(...p)]); paint();
  });
  grid.addEventListener('mouseover', e => {
    if (!M.drag) return; const td = e.target.closest('td[data-k]'); if (!td) return;
    M.sel = new Set([...M.drag.base, ...rect(M.anchor, td.dataset.k.split(',').map(Number))]); paint();
  });
  document.addEventListener('mouseup', () => { M.drag = null; });
  grid.addEventListener('click', e => {
    const h = e.target.closest('th[data-col], th[data-row]'); if (!h) return;
    const add = e.ctrlKey || e.metaKey;
    const pick = h.dataset.col ? (([, c]) => c === Number(h.dataset.col)) : (([r]) => r === Number(h.dataset.row));
    let cells = used().filter(pick).map(x => key(...x));
    if (e.shiftKey && h.dataset.col && M.anchorCol) {
      const [lo, hi] = [Math.min(M.anchorCol, Number(h.dataset.col)), Math.max(M.anchorCol, Number(h.dataset.col))];
      cells = used().filter(([, c]) => c >= lo && c <= hi).map(x => key(...x));
    }
    if (h.dataset.col) M.anchorCol = Number(h.dataset.col);
    M.sel = new Set([...(add ? M.sel : []), ...cells]); M.anchor = null; paint();
  });
  $('#sel-form').addEventListener('submit', e => { e.preventDefault(); selectRange($('#sel-range').value); });
  $('#map-hidden').addEventListener('change', e => { M.showHidden = e.target.checked; drawGrid(); paint(); });
  $('#oc-q').addEventListener('input', e => { M.q = e.target.value; drawOracle(); paint(); });
  $('#map-sheet')?.addEventListener('change', e => { M.sheet = e.target.value; M.sel = new Set(); M.dirty = true; drawGrid(); paint(); });
  $('#map-order')?.addEventListener('change', async e => {
    await busy('Saving this mapping…', async () => { M.draft = await saveMapping(); hooks.onDraft?.(M.draft); await openMapper(root, M.draft, hooks, Number(e.target.value)); });
  });
  const oracle = $('#oc-tables');
  oracle.addEventListener('change', e => {
    const t = e.target.dataset.tick; if (!t) return;
    M.checked = e.target.checked ? [...M.checked, t] : M.checked.filter(x => x !== t);
    paint();
  });
  oracle.addEventListener('click', e => {
    const b = e.target.closest('[data-use],[data-clear],[data-show],[data-more]'); if (!b) return;
    if (b.dataset.use) { assign(b.dataset.use, selected()); M.sel = new Set(); return refresh(); }
    if (b.dataset.clear) { delete M.targets[b.dataset.clear]; delete M.typed[b.dataset.clear]; M.dirty = true; M.automatic = false; return refresh(); }
    if (b.dataset.more) { M.more.has(b.dataset.more) ? M.more.delete(b.dataset.more) : M.more.add(b.dataset.more); drawOracle(); return paint(); }
    if (b.dataset.show) {                       // the cells this column takes, selected and in view
      const refs = M.targets[b.dataset.show] || [];
      M.sel = new Set(refs.map(parse).filter(Boolean).map(p => key(...p))); paint();
      M.tds.get([...M.sel][0])?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    }
  });
  root.querySelector('.map-foot').addEventListener('click', e => {
    const b = e.target.closest('[data-m]'); if (b) action(b.dataset.m);
  });
  $('#oc-multi').addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) action(b.dataset.m); });
  root.querySelector('.sel-bar').addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) action(b.dataset.m); });
}

function action(what) {
  if (what === 'clear-sel') { M.sel = new Set(); return paint(); }
  if (what === 'untick') { M.checked = []; drawOracle(); return paint(); }
  if (what === 'new') return hooks.onNew?.();
  if (what === 'continue') return hooks.onContinue?.();
  if (what === 'map-order') {
    const cells = selected(); const cols = [...new Set(cells.map(x => x[1]))].sort((a, b) => a - b);
    const order = $$('#oc-tables [data-tick]').map(x => x.dataset.tick).filter(t => M.checked.includes(t));
    if (cols.length !== order.length) return;
    order.forEach((t, i) => { M.targets[t] = cells.filter(x => x[1] === cols[i]).map(([r, c]) => ref(r, c)); delete M.typed[t]; });
    M.dirty = true; M.automatic = false; M.checked = []; M.sel = new Set();
    notify(`${order.length} Excel columns mapped: ${order.map((t, i) => `${colName(cols[i])} → ${t}`).join(', ')}.`, 'success');
    refresh();
  }
}
