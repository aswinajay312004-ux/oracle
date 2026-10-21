'use strict';
/* New Order: Upload → (automatic JSON + mapping + preview) → Review → Approve → Saved. */
const NO = { draft: null, showEmpty: false, showJson: false };
const MASTER_OF = { 'ORDMAS.BUYER': 'BUYMAS', 'ORDMAS.STYLENO': 'STYLENUM', 'ORDMAS.STYLEDESC': 'STYLEMAS', 'ORDMAS.UOM': 'UNITMAS', 'ORDMAS.MERCH': 'MERCHMAS',
  'ORDSTYLE.STNO': 'STYLENUM', 'ORDSTYLE.STYLENAME': 'STYLEMAS', 'ORDSTYLE.COMBO': 'COLORMAS', 'ORDSTYLE.PUOM': 'UNITMAS', 'ORDSIZE.GSIZE': 'SIZE_MAS' };
const TABLE_ORDER = ['ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'BUYMAS', 'STYLENUM', 'STYLEMAS', 'COLORMAS', 'SIZE_MAS', 'UNITMAS', 'MERCHMAS'];
const ORIGIN = { excel: ['From Excel', 'green'], master: ['Linked record', 'blue'], generated: ['New ID', 'blue'], derived: ['Calculated', 'violet'], manual: ['Entered', 'violet'] };

const STEPS = [['Upload Excel', 'Upload your file'], ['Analyze & Map', 'AI reads and maps data'], ['Preview & Review', 'Check extracted data'], ['Approve & Save', 'Save to Oracle']];
function stepper(active) {
  return `<section class="card steps4">${STEPS.map(([t, sub], i) => `${i ? `<span class="line ${i < active ? 'done' : ''}"></span>` : ''}<div class="s ${i + 1 < active ? 'done' : i + 1 === active ? 'active' : ''}"><b>${i + 1}</b><div><strong>${t}</strong><small>${sub}</small></div></div>`).join('')}</section>`;
}
function guide() {
  Modal.open('How a new order works', `<ol class="stack" style="padding-left:18px;gap:10px;margin:0">
    <li><strong>Upload</strong> the buyer's Excel (.xlsx). The original file is kept unchanged.</li>
    <li><strong>The Data Expert Agent</strong> reads every sheet, converts it to JSON, finds the sections, and fills the Oracle columns from the Excel labels. The local AI checks the cells no rule could place. Its suggestions are marked <span class="ai-chip">AI</span>.</li>
    <li><strong>Preview</strong> shows the exact Oracle rows and the Excel cell behind each value. Blank optional fields are fine.</li>
    <li>If something is wrong, <strong>Edit Mapping</strong>: click a field, then click the Excel cell. Or type the value.</li>
    <li><strong>Approve & Save</strong>: all rows are written in one transaction, then read back from Oracle to verify.</li></ol>
    <div class="modal-foot"><button class="btn primary" type="button" onclick="Modal.close()">Got it</button></div>`);
}

route('new', async (view, [id]) => {
  setTitle('New Order', 'Upload your Excel file, review the extracted data and mapping, then save to Oracle.',
    `<button class="btn" type="button" id="guide-btn">${icon('play')} Need help? Watch Guide</button>`);
  if (!id) { NO.draft = null; return renderNew(view); }
  if (!NO.draft || NO.draft.id !== id) NO.draft = await api(`/api/drafts/${encodeURIComponent(id)}`);
  // Always show the Oracle preview when a draft is opened; it is rebuilt after every change.
  if (!NO.draft.plan && !NO.draft.result) await preparePreview(true);
  renderNew(view);
});

function uploadCard(d) {
  return `<section class="card"><label class="drop2" id="drop" tabindex="0"><span class="xls-ico">${icon('xls')}</span><strong>Drag &amp; drop your Excel file here</strong><span class="muted">or click to browse files</span>
      <small class="muted">${icon('file')} Supports .xlsx and .xlsm (max 12 MB)</small><input type="file" id="file" accept=".xlsx,.xlsm" class="visually-hidden"></label>
    ${d ? `<div class="file-card"><span class="file-ico">XLS</span><div><strong>${esc(d.filename)}</strong><small class="muted" style="display:block">${plural((d.sheets || []).length, 'sheet')} · Uploaded ${when(d.created_at)}</small>
      <span class="okline">${icon('checkcircle')} File uploaded successfully · original kept unchanged</span> <a class="muted" style="font-size:12.5px;margin-left:6px" href="/api/drafts/${esc(d.id)}/file">Open in Excel</a></div>
      <a class="icon-btn x" href="#/new" title="Upload another file" aria-label="Upload another file">${icon('x')}</a></div>` : ''}</section>`;
}

function agentCard(d) {
  const steps = d?.agent?.steps || []; const plan = d?.plan;
  const rows = steps.length ? steps.map(s => `<li><span class="dot2 ${s.status === 'ok' ? '' : s.status}">${s.status === 'ok' ? icon('check') : s.status === 'warn' ? icon('alert') : s.status === 'pending' ? '' : icon('info')}</span>
      <span><strong>${esc(s.title)}</strong><small title="${esc(s.detail)}">${esc(s.detail.length > 120 ? s.detail.slice(0, s.detail.lastIndexOf(',', 110)) + ' …' : s.detail)}</small></span><time>${new Date(s.at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time></li>`).join('')
    : ['Workbook read', 'Sheets and sections found', 'Converted to structured JSON', 'Oracle columns matched', 'Local AI review'].map(t => `<li><span class="dot2 skip"></span><span><strong class="muted">${t}</strong><small>Waiting for a file</small></span><time></time></li>`).join('');
  const checks = plan ? `<li><span class="dot2 ${plan.errors.length ? 'warn' : ''}">${plan.errors.length ? icon('alert') : icon('check')}</span><span><strong>Checks finished</strong><small>${plan.errors.length ? `${plural(plan.errors.length, 'item')} to check before approving` : 'Ready for your review and approval'}</small></span><time></time></li>` : '';
  return `<section class="card"><div class="card-title"><span class="tile-ico">${icon('file')}</span><div><h2>Automatic Reading &amp; Mapping</h2><p>The Data Expert Agent reads your file and matches it with the Oracle table</p></div></div>
    <ul class="timeline2">${rows}${checks}</ul></section>`;
}

const SECTION_LABEL = { order: 'Order quantities', sample: 'Sample quantities', summary: 'Summary block', fabric: 'Fabric information', trims: 'Trims information', packing: 'Packing information', printing: 'Printing', embroidery: 'Embroidery', costing: 'Costing information', other: 'Other tables' };
function sectionRows(d) {
  const secs = d?.agent?.sections || []; const groups = new Map();
  for (const s of secs) { const k = ['order', 'sample', 'summary'].includes(s.kind) ? s.name : SECTION_LABEL[s.kind] || s.kind; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(s); }
  return [...groups].map(([name, list]) => { const s = list[0]; const n = list.length;
    const [mark, ic, text] = s.status === 'saved' ? ['ok', 'check', `Mapped · ${s.detail}`] : s.status === 'reference' ? ['info', 'info', n > 1 ? `In Excel · ${n} sections, kept for reference` : 'In Excel · kept for reference']
      : s.status === 'empty' ? ['none', 'info', 'Empty in Excel'] : s.status === 'repeat' ? ['none', 'info', 'Repeat · not saved twice'] : ['none', 'info', 'Not provided'];
    return `<div class="side-row" title="${esc(list.map(x => x.name + (x.cell ? ' (' + x.cell + ')' : '') + ': ' + x.detail).join('\n'))}"><span class="mark ${mark}">${icon(ic)}</span><span class="grow">${esc(name)}</span><span class="muted-r">${esc(text)}</span></div>`; }).join('');
}

function qualityRows(plan) {
  const has = re => plan.errors.some(e => re.test(e));
  const warn = re => plan.warnings.some(e => re.test(e));
  const rows = [['Duplicate check', has(/already saved|already exists|appears twice/) ? ['bad', 'Duplicate found'] : warn(/no OC number|saved before/) ? ['warn', 'No OC number to check'] : ['ok', 'No duplicates found']],
    ['Mandatory fields', has(/requires a value|enter an order reference|Needed/i) ? ['bad', 'Missing'] : ['ok', 'All valid']],
    ['Data format', has(/not a date|two different dates|needs a number|longer than|whole number|not a valid/) ? ['bad', 'Check values'] : ['ok', 'Valid']],
    ['Totals match Excel', has(/Excel total/) ? ['bad', 'Different'] : warn(/add up to|Excel total pieces/) ? ['warn', 'Check total'] : ['ok', 'Matched']]];
  return rows.map(([t, [m, v]]) => `<div class="side-row"><span class="mark ${m}">${icon(m === 'ok' ? 'check' : 'alert')}</span><span class="grow">${t}</span><span class="muted-r">${v}</span></div>`).join('');
}

function summaryCard(d) {
  const plan = d?.plan; const ready = plan && !plan.errors.length;
  if (!plan) return `<aside class="no-side"><section class="card"><div class="card-title"><span class="tile-ico">${icon('checkcircle')}</span><div><h2>Approval Summary</h2><p>Review results and save to Oracle</p></div></div>
    <div class="empty">${d ? esc(NO.previewError || 'Preparing the preview…') : 'Upload a file to see the results here.'}</div></section></aside>`;
  const rows = plan.operations; const total = plan.mode === 'table' ? rows.length : rows.filter(o => o.action === 'insert').length;
  const blanks = plan.mode === 'table' ? (plan.tables[plan.table].columns.filter(c => c.level === 'order' && rows[0] && rows[0].values[c.name] == null).length) : 0;
  const invalid = plan.errors.length ? rows.filter(op => rowStatus(plan, op) === 'check').length : 0;
  return `<aside class="no-side"><section class="card"><div class="card-title"><span class="tile-ico">${icon('checkcircle')}</span><div><h2>Approval Summary</h2><p>Review results and save to Oracle</p></div></div>
    <div class="stack" style="padding:0 14px 14px;gap:12px">
      <div class="side-block"><header>Validation Results <button class="btn small" type="button" id="val-details">View Details ${icon('arrow')}</button></header>
        <div class="side-row"><span class="mark ok">${icon('check')}</span><span class="grow">Valid records</span><span class="val">${fmt(total - invalid)}</span></div>
        <div class="side-row"><span class="mark bad">${icon('alert')}</span><span class="grow">Validation issues</span><span class="val red">${fmt(plan.errors.length)}</span></div>
        <div class="side-row"><span class="mark warn">${icon('alert')}</span><span class="grow">Missing optional values</span><span class="val amber">${fmt(blanks)}</span></div>
        <div class="side-row"><span class="mark info">${icon('info')}</span><span class="grow">Total records</span><span class="val">${fmt(total)}</span></div></div>
      <div class="side-block"><header>Optional Sections Status</header>${sectionRows(d) || '<div class="side-row muted">No sections found.</div>'}</div>
      <div class="side-block"><header>Data Quality Checks</header>${qualityRows(plan)}</div>
      <button class="btn primary approve-big" id="approve" type="button" ${ready ? '' : 'disabled'}>${icon('database')} Approve &amp; Save to Oracle</button>
      <small class="muted" style="text-align:center">${ready ? `This will create ${plural(total, plan.mode === 'table' ? 'row' : 'record')} in ${esc(plan.mode === 'table' ? 'GARMENT_DEMO.' + plan.table : 'GARMENT_DEMO')}` : 'Resolve the items above first'}</small>
      <div class="grid g2" style="gap:10px"><a class="btn" href="#/overview">${icon('back')} Back</a><button class="btn" id="edit-map" type="button">${icon('settings')} Edit Mapping</button></div></div></section></aside>`;
}

const WEIGHT = { excel: 1, manual: 1, master: 1, derived: 1, generated: 1 };
function mappingTiles(d) {
  const plan = d.plan; if (!plan) return '';
  const first = plan.operations[0] || { values: {}, columns: {} };
  let fields = 0, of = 0, conf = 0, n = 0, ai = 0;
  if (plan.mode === 'table') {
    const cols = plan.tables[plan.table].columns.filter(c => c.level !== 'system'); of = cols.length; fields = cols.filter(c => first.values[c.name] != null).length;
    for (const c of cols) { const info = first.columns[c.name]; if (first.values[c.name] == null || !info) continue; const via = info.source?.via || '';
      const w = /^AI suggestion/.test(via) ? 0.6 : /^Excel label/.test(via) ? 0.95 : WEIGHT[info.origin] ?? 0.9; if (/^AI/.test(via)) ai++; conf += w; n++; }
  } else { for (const op of plan.operations) { of += Object.keys(op.values).length; fields += Object.keys(op.values).length; } conf = 1; n = 1; }
  const pct = n ? Math.round(conf / n * 100) : 0; const unused = (d.agent?.unused || []).length;
  return `<section class="card"><div class="card-title"><span class="tile-ico">${icon('chart')}</span><div><h2>Mapping Summary</h2><p>Overview of how your Excel data is mapped to the Oracle table</p></div><button class="btn right" id="edit-map3" type="button">View Mapping Details ${icon('arrow')}</button></div>
    <div class="sum-tiles">
      <div><span class="tile-ico blue">${icon('file')}</span><span><strong>${(d.sheets || []).filter(s => !s.hidden).length}</strong><small>Sheets detected</small><small>${esc((d.sheets || []).map(s => s.name).join(', ').slice(0, 40))}</small></span></div>
      <div><span class="tile-ico">${icon('database')}</span><span><strong>${plan.mode === 'table' ? 1 : Object.keys(plan.tables).length}</strong><small>Mapped table${plan.mode === 'table' ? '' : 's'}</small><small>${esc(plan.mode === 'table' ? plan.table : Object.keys(plan.tables).join(', '))}</small></span></div>
      <div><span class="tile-ico">${icon('list')}</span><span><strong>${fields}</strong><small>Mapped fields</small><small>Out of ${of} columns${ai ? ` · ${ai} by AI` : ''}</small></span></div>
      <div><span class="tile-ico ${unused ? 'red' : ''}">${icon(unused ? 'alert' : 'check')}</span><span><strong>${unused}</strong><small>Unmapped Excel values</small>${unused ? `<em class="amber" title="${esc((d.agent.unused || []).map(a => `${a.label}: ${a.value} (${a.cell})`).join('\n'))}">Review if needed</em>` : '<em class="green">All used</em>'}</span></div>
      <div><span class="tile-ico">${icon('checkcircle')}</span><span><strong>${pct}%</strong><small>Mapping confidence</small><em class="${pct >= 90 ? 'green' : 'amber'}" title="How sure POPPYS is where each value came from: structure and exact Excel labels count fully, AI suggestions count less until you check them.">${pct >= 90 ? 'High confidence' : pct >= 70 ? 'Medium — check AI values' : 'Low — please review'}</em></span></div></div></section>`;
}

function rowStatus(plan, op) {
  if (!plan.errors.length) return 'valid';
  const line = op.values.ORDSTYLEROW; const size = op.values.SIZES;
  return plan.errors.some(e => (line && new RegExp(`line ${line}\\b`).test(e) && (!size || e.includes(size) || !/size/.test(e))) || e.startsWith('Order')) ? 'check' : 'valid';
}

function previewCard(d) {
  const plan = d.plan; if (!plan) return '';
  if (plan.mode !== 'table') return `<section class="card"><div class="card-title"><span class="tile-ico">${icon('database')}</span><div><h2>Oracle Preview <span class="muted" style="font-weight:500">(Extracted Data)</span></h2><p>The exact records that will be written</p></div></div><div class="card-pad stack">${oracleTables(plan)}</div></section>`;
  const ops = plan.operations; const per = 10; const pages = Math.max(1, Math.ceil(ops.length / per)); NO.pageNo = Math.min(NO.pageNo || 0, pages - 1);
  const shownOps = ops.slice(NO.pageNo * per, NO.pageNo * per + per);
  const cols = [['IONO', 'Oracle Value'], ['BUYERNAME', 'Mapped'], ['STYLENO', 'Mapped'], ['COMBO', 'Mapped'], ['SIZES', 'Mapped'], ['ORDERQTY', 'Original → Oracle'], ['SHIPDT', 'Original → Oracle']];
  const cell = (op, c) => { const v = op.values[c]; const info = op.columns[c] || {}; if (v == null) return '<span class="blank">—</span>';
    const raw = info.raw != null && String(info.raw) !== String(v) ? `<span class="arrowv">${esc(info.raw)} → ${esc(v)}</span>` : ''; const aiTag = /^AI/.test(info.source?.via || '') ? ' <span class="ai-chip">AI</span>' : '';
    return `${esc(val(c, v))}${aiTag}${raw}`; };
  return `<section class="card"><div class="card-title"><span class="tile-ico">${icon('database')}</span><div><h2>Oracle Preview <span class="muted" style="font-weight:500">(Extracted Data)</span></h2><p>Review the extracted data and mapped values before saving to Oracle</p></div>
      <div class="right row"><span class="chip-box">Table: <strong>${esc(plan.table)}</strong> (${fmt(ops.length)} rows)</span><span class="muted">${NO.pageNo * per + 1}–${Math.min(ops.length, NO.pageNo * per + per)} of ${fmt(ops.length)}</span>
      <button class="icon-btn" id="pg-prev" ${NO.pageNo ? '' : 'disabled'} aria-label="Previous page">${icon('back')}</button><button class="icon-btn" id="pg-next" ${NO.pageNo < pages - 1 ? '' : 'disabled'} aria-label="Next page">${icon('arrow')}</button></div></div>
    <div class="table-wrap"><table class="data-table preview-grid"><thead><tr><th>#</th>${cols.map(([c, k]) => `<th>${esc(c)}<small>(${k}) · ${esc(COLUMN_LABELS[c] || '')}</small></th>`).join('')}<th>Status</th><th></th></tr></thead><tbody>
      ${shownOps.map((op, i) => { const st = rowStatus(plan, op); return `<tr><td class="faint">${NO.pageNo * per + i + 1}</td>${cols.map(([c]) => `<td>${cell(op, c)}</td>`).join('')}
        <td><span class="valid ${st === 'check' ? 'check' : ''}">${icon(st === 'check' ? 'alert' : 'check')} ${st === 'check' ? 'Check' : 'Valid'}</span></td>
        <td><span class="dots-btn" title="${esc(Object.entries(op.columns).filter(([, x]) => x?.source?.cell).map(([k, x]) => `${k} ← ${x.source.sheet}!${x.source.cell}`).join('\n'))}">${icon('dots')}</span></td></tr>`; }).join('')}</tbody></table></div>
    <details class="quiet card-pad" ${NO.showAll ? 'open' : ''} id="all-cols"><summary>All ${plan.tables[plan.table].columns.length} Oracle columns, the Excel source of every value, and the structured JSON</summary><div class="stack" style="margin-top:12px">${singleTable(plan)}
      <div><button class="btn small" id="toggle-json" type="button">${NO.showJson ? 'Hide' : 'Show'} structured JSON</button>${NO.showJson ? `<div class="json-view" style="margin-top:8px">${esc(JSON.stringify(d.orders.map(cleanOrder), null, 2))}</div>` : ''}</div></div></details></section>`;
}

function renderNew(view) {
  const d = NO.draft;
  if (d?.result) return renderResult(view, d.result);
  const plan = d?.plan; const active = !d ? 1 : !plan ? 2 : plan.errors.length ? 3 : 4;
  page(view, `<div class="stack">${stepper(active)}
    <div class="no-grid"><div class="stack"><div class="no-top">${uploadCard(d)}${agentCard(d)}</div>
      ${d ? `<div class="stack">${NO.previewError ? `<div class="callout info">${icon('info')}<div><strong>The Oracle preview is not available.</strong> ${esc(NO.previewError)} <button class="btn small" id="retry">Try again</button></div></div>` : ''}
        ${d.template ? `<div class="callout">${icon('save')}<div>Saved buyer template <strong>${esc(d.template.name)} v${d.template.version}</strong> was reused${d.template.missing?.length ? `, but ${plural(d.template.missing.length, 'cell')} did not match this layout. Check them in Edit Mapping.` : '.'}</div></div>` : ''}
        ${quantityQuestion(d.orders || [])}${plan?.errors?.length ? attention(plan.errors, d.orders) : ''}
        ${plan?.warnings?.length ? `<div class="callout attention">${icon('info')}<div><strong>Please check</strong><ul>${plan.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div></div>` : ''}</div>
        ${mappingTiles(d)}${previewCard(d)}` : `<section class="card"><div class="card-title"><span class="tile-ico">${icon('clock')}</span><div><h2>Continue a previous upload</h2><p>Files uploaded but not saved yet</p></div></div><div id="recent"><div class="empty"><span class="spinner"></span></div></div></section>`}
    </div>${summaryCard(d)}</div></div>`);
  wireNew(view);
}

function wireNew(view) {
  const d = NO.draft;
  $('#guide-btn', view)?.addEventListener('click', guide);
  const drop = $('#drop', view), input = $('#file', view);
  input.addEventListener('change', () => upload(input.files[0]));
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  ['dragenter', 'dragover'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { if (e.dataTransfer.files.length !== 1) return toast('Please drop one Excel file at a time.', 'attention'); upload(e.dataTransfer.files[0]); });
  if (!d) {
    api('/api/drafts').then(list => { const drafts = list.filter(x => !x.committed).slice(0, 8);
      $('#recent', view).innerHTML = drafts.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>File Name</th><th>Order (Detected)</th><th>Uploaded</th><th></th></tr></thead><tbody>${drafts.map(x => `<tr><td><strong>${esc(x.filename)}</strong><span class="sub">${esc(x.format)}</span></td><td>${esc(x.refs.filter(Boolean).join(', ') || '—')}</td><td>${when(x.created_at)}</td><td class="num"><a class="btn small soft" href="#/new/${esc(x.id)}">Review</a></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No unfinished uploads.</div>'; })
      .catch(() => { $('#recent', view).innerHTML = '<div class="empty">No unfinished uploads.</div>'; });
    return;
  }
  const openMapper = () => Mapper.open(NO.draft, async draft => { NO.draft = draft; await busy('Updating the preview…', async () => { await preparePreview(true); renderNew($('#view')); }); });
  $$('#edit-map,#edit-map3', view).forEach(b => b.addEventListener('click', openMapper));
  $('#approve', view)?.addEventListener('click', approve);
  $('#val-details', view)?.addEventListener('click', () => { NO.showAll = true; renderNew(view); $('#all-cols', view)?.scrollIntoView({ behavior: 'smooth' }); });
  $('#retry', view)?.addEventListener('click', () => busy('Preparing the preview…', async () => { await preparePreview(); renderNew(view); }));
  $('#pg-prev', view)?.addEventListener('click', () => { NO.pageNo--; renderNew(view); });
  $('#pg-next', view)?.addEventListener('click', () => { NO.pageNo++; renderNew(view); });
  $('#toggle-json', view)?.addEventListener('click', () => { NO.showJson = !NO.showJson; NO.showAll = true; renderNew(view); });
  $('#all-cols', view)?.addEventListener('toggle', e => { NO.showAll = e.target.open; });
  $('#show-empty', view)?.addEventListener('change', e => { NO.showEmpty = e.target.checked; NO.showAll = true; renderNew(view); });
  view.querySelectorAll('[data-basis]').forEach(b => b.addEventListener('click', () => {
    const orders = structuredClone(NO.draft.orders); const o = orders[Number(b.dataset.order)]; o.quantity_basis = b.dataset.basis;
    if (b.dataset.basis === 'sample' && o.sample_lines?.length) o.lines = structuredClone(o.sample_lines);
    if (b.dataset.basis === 'order' && o.order_lines?.length) o.lines = structuredClone(o.order_lines);
    saveOrders(orders);
  }));
  view.querySelectorAll('[data-ppu]').forEach(inp => inp.addEventListener('change', () => { const orders = structuredClone(NO.draft.orders); orders[Number(inp.dataset.ppu)].pieces_per_unit = inp.value === '' ? null : Number(inp.value); saveOrders(orders); }));
  $('#quick-fix', view)?.addEventListener('submit', e => { e.preventDefault(); const orders = structuredClone(NO.draft.orders);
    $$('[data-fix]', e.target).forEach(i => { if (i.value.trim()) orders[Number(i.dataset.order)][i.dataset.fix] = i.value.trim(); }); saveOrders(orders); });
  runAgentAI(view);
}

/* The agent's local-AI step runs after the screen is shown; the timeline shows it working. */
async function runAgentAI(view) {
  const d = NO.draft; if (!d?.agent || d.agent.ai?.status !== 'pending' || NO.aiRunning === d.id) return;
  NO.aiRunning = d.id;
  try {
    const next = await api(`/api/drafts/${d.id}/agent/ai`, { method: 'POST', body: { version: d.version } });
    if (NO.draft?.id !== d.id) return;
    NO.draft = next; await preparePreview(true);
    const acc = (next.agent.ai.proposals || []).filter(p => p.accepted).length;
    if (acc) toast(`The AI filled ${plural(acc, 'more column')}. They are marked AI — please check them.`);
    if (location.hash === `#/new/${d.id}`) renderNew($('#view'));
  } catch (e) { /* draft changed meanwhile or AI unavailable: the rule-based result stays */ }
  finally { NO.aiRunning = null; }
}

async function upload(file) {
  if (!file) return;
  if (!/\.(xlsx|xlsm)$/i.test(file.name)) return toast(/\.xls$/i.test(file.name) ? 'This is an old .xls file. Open it in Excel and "Save as" .xlsx, then upload it.' : 'Please choose an Excel .xlsx or .xlsm file.', 'attention');
  await busy('Reading the workbook and mapping it to Oracle…', async () => {
    const form = new FormData(); form.append('file', file);
    NO.draft = await api('/api/uploads', { method: 'POST', body: form }); NO.pageNo = 0; NO.showAll = false;
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
    await preparePreview(true); renderNew($('#view'));
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
    catch (e) { NO.draft = await api(`/api/drafts/${d.id}`).catch(() => d); if (!NO.draft.result) { renderNew($('#view')); throw new Error(e.message + '\nNo partial order was saved.'); } }
    renderResult($('#view'), NO.draft.result);
  });
}

function renderResult(view, result) {
  const verified = result.verified !== false;
  view.innerHTML = head() + `${stepper(5)}<section class="card result-card"><span class="success-mark">${icon('check')}</span>
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
