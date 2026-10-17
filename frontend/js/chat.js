// Ask AI: plain-language questions → answer with its linked-table flowchart, result table, and the
// exact query. Click a table to preview it; add any of its columns to the answer (re-run with the
// correct joins); "+" on a table shows what else it links to.
import { $, $$, esc, fmt, show, plural, state, notify, modal, csv, timeAgo } from './util.js';
import { api, id } from './api.js';
import { renderGraph } from './graph.js';

const answers = new Map();   // id -> { result, extra: {nodes, edges}, added:Set, sort }
const STARTERS = ['How many orders for NEXT?', 'Size wise quantity for order PK/26-27/S/9001', 'Buyer wise order quantity',
  'Top 5 buyers by quantity', 'Orders this month', 'Colour wise quantity for NEXT', 'Fabric kg by colour', 'How many uploaded files?'];
let loaded = false;

// Signing out (or another person signing in) clears the previous person's questions and answers.
export function resetChat() {
  loaded = false; $('#thread').innerHTML = ''; $('#chat-input').value = '';
  $('#chat-history').innerHTML = '<p class="muted small-text">None yet.</p>';
}

export async function loadChat() {
  if (!loaded) { loaded = true; if (!$('#thread').children.length) welcome(); }
  const db = state.health?.database;
  $('#chat-source').textContent = db?.connected ? `Live Oracle · ${db.owner}` : 'Oracle offline — only uploaded-file questions work';
  $('#chat-source').className = `pill ${db?.connected ? 'ok' : 'warn'}`;
  sideLists();
}

async function sideLists() {
  try {
    const [hist, ex] = await Promise.all([api('/api/chat/history'), api('/api/chat/examples')]);
    $('#chat-history').innerHTML = hist.length ? hist.slice(0, 12).map(h => `<button data-hist="${esc(h.id)}" title="${esc(h.question)}">${h.feedback === 'correct' ? '✓ ' : ''}${esc(h.question.length > 46 ? h.question.slice(0, 45) + '…' : h.question)}<span class="muted hist-when">${esc(timeAgo(h.at))}</span></button>`).join('') : '<p class="muted small-text">None yet.</p>';
    const learned = ex.slice(0, 4).map(e => e.question);
    $('#chat-examples').innerHTML = [...new Set([...learned, ...STARTERS])].slice(0, 9).map(q => `<button data-ask="${esc(q)}">${learned.includes(q) ? '★ ' : ''}${esc(q)}</button>`).join('');
  } catch (_) { /* side lists are optional */ }
}

function welcome() {
  $('#thread').innerHTML = `<section class="card welcome" id="welcome"><div class="orb">✦</div><h2>Ask anything about your orders</h2>
    <p class="muted welcome-lead">I find the right tables, link them correctly and show you exactly where each number came from. Try one of these:</p>
    <div class="suggestions">${STARTERS.slice(0, 6).map(q => `<button class="suggestion" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</div></section>`;
}

export async function askQuestion(question) {
  question = String(question || '').trim(); if (!question) return;
  $('#welcome')?.remove();
  const thread = $('#thread');
  thread.insertAdjacentHTML('beforeend', `<div class="q-bubble">${esc(question)}</div><div class="card answer" data-pending><div class="typing"><i></i><i></i><i></i></div></div>`);
  const pending = thread.lastElementChild; pending.scrollIntoView({ behavior: 'smooth', block: 'end' });
  try {
    const r = await api('/api/chat/ask', { method: 'POST', body: { question } });
    mount(pending, r);
  } catch (e) {
    pending.outerHTML = `<div class="card answer"><div class="answer-summary">${esc(e.message)}</div></div>`;
  }
  sideLists();
}

function mount(el, r, prev) {
  const entry = { result: r, extra: prev?.extra || { nodes: [], edges: [] }, added: prev?.added || new Set(), sort: null, lastAdded: prev?.lastAdded || null };
  answers.set(r.id, entry);
  const node = document.createElement('div'); node.className = 'card answer'; node.id = `ans-${r.id}`;
  el.replaceWith(node); draw(r.id);
}

function confidencePill(c) {
  const lvl = c?.level || 'medium';
  return `<span class="pill ${lvl === 'high' ? 'ok' : lvl === 'medium' ? 'warn' : 'err'}" title="${esc((c?.reasons || []).join(' '))}">${lvl === 'high' ? 'High confidence' : lvl === 'medium' ? 'Medium confidence — check' : 'Low confidence — check'}</span>`;
}

function draw(aid) {
  const entry = answers.get(aid); const r = entry.result; const el = $(`#ans-${aid}`);
  if (r.kind !== 'answer') {
    el.innerHTML = `<div class="answer-top"><span class="pill ${r.kind === 'refused' ? 'err' : 'warn'}">${r.kind === 'refused' ? 'Cannot answer safely' : 'Please rephrase'}</span></div>
      <div class="answer-summary">${esc(r.message)}</div>
      ${(r.suggestions || []).length ? `<div class="answer-body"><div class="muted small-text mb-6">For example:</div><div class="suggestions align-start">${r.suggestions.map(q => `<button class="suggestion" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</div></div>` : ''}`;
    return;
  }
  const cols = r.columns.map((c, i) => ({ ...c, index: i })).filter(c => !c.hidden);
  let rows = r.rows;
  if (entry.sort) { const { i, dir } = entry.sort; rows = [...rows].sort((a, b) => { const x = a[i], y = b[i]; if (x == null) return 1; if (y == null) return -1; return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * dir; }); }
  const removable = cols.filter(c => c.kind !== 'measure').length > 1;
  el.innerHTML = `<div class="answer-top"><span class="understood">Understood as: <b>${esc(r.understood)}</b></span><span class="spacer"></span>${confidencePill(r.confidence)}
      <span class="pill" title="How the question was turned into a query">${esc(r.source === 'rules' ? 'matched by rules' : r.source)}</span></div>
    <div class="flow-wrap" data-graph></div>
    <div class="legend"><span><i class="sw-brand"></i>Used in this answer</span><span><i class="sw-dashed"></i>Linked, not used</span>
      <span><i class="sw-proposed"></i>Unconfirmed link</span><span class="muted">Click a table to preview it and add its columns · ＋ shows linked tables</span></div>
    <div class="answer-summary">${esc(r.summary)}</div>
    <div class="answer-body">
      <div class="answer-tools"><span>${plural(r.row_count, 'row')}${r.truncated ? ` (first ${r.limit} shown)` : ''} · ${r.elapsed_ms} ms</span><span class="spacer"></span>
        <button class="ghost small" data-csv="${esc(aid)}">Download CSV</button></div>
      ${rows.length ? `<div class="tbl-wrap"><table><thead><tr>${cols.map(c => `<th class="sortable ${c.type === 'NUMBER' ? 'num' : ''} ${entry.added.has(c.ref) ? 'added' : ''}" data-sort="${c.index}" title="${esc((c.table || '') + '.' + (c.column || ''))} — click to sort">${esc(c.label)}${c.unit && !String(c.label).includes(`(${c.unit})`) ? ` <span class="muted">(${esc(c.unit)})</span>` : ''}
          ${removable && c.kind !== 'measure' ? `<button class="th-x" data-remove-col="${esc(c.ref)}" data-aid="${esc(aid)}" title="Remove this column" aria-label="Remove ${esc(c.label)}">×</button>` : ''}<span class="code">${esc(c.table)}.${esc(c.column || '*')}</span></th>`).join('')}</tr></thead>
        <tbody>${rows.map(row => `<tr>${cols.map(c => { const v = row[c.index]; return `<td class="${typeof v === 'number' ? 'num' : ''} ${v == null ? 'null' : ''}">${v == null ? '—' : esc(typeof v === 'number' ? fmt(v) : show(v))}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>`
        : '<p class="empty">No matching rows.</p>'}
    </div>
    <details class="how"><summary>How this was answered</summary><div class="how-body">
      ${(r.confidence?.reasons || []).length ? `<div>${r.confidence.reasons.map(x => `<div>• ${esc(x)}</div>`).join('')}</div>` : ''}
      ${(r.notes || []).length ? `<div>${r.notes.map(x => `<div>• ${esc(x)}</div>`).join('')}</div>` : ''}
      ${(r.linked_values || []).length ? `<div><b>Values matched in the database:</b> ${r.linked_values.map(v => `<span class="chip ${v.found ? 'reuse' : 'new'}">${esc(v.phrase)} → ${esc(v.column)}${v.found ? '' : ' (not found)'}</span>`).join(' ')}</div>` : ''}
      <div><b>Tables:</b> ${r.tables.map(t => `<code>${esc(t)}</code>`).join(', ')}</div>
      ${r.joins.length ? `<div><b>Links used:</b><ul class="list-tight">${r.joins.map(j => `<li><code>${esc(j.on)}</code> <span class="pill ${j.status === 'verified' || j.status === 'declared' ? 'ok' : 'warn'}">${esc(j.status)}</span> <span class="muted">${esc(j.label)}</span></li>`).join('')}</ul></div>` : ''}
      <div><b>Read-only query</b> <span class="muted">(values passed separately as bind variables: ${esc(JSON.stringify(r.binds))})</span><div class="sql">${esc(r.sql)}</div></div>
    </div></details>
    <div class="answer-foot"><span class="muted small-text">Is this right?</span>
      <button class="secondary small" data-feedback="${esc(aid)}" data-correct="1" ${r.feedback ? 'disabled' : ''}>✓ Correct — remember it</button>
      <button class="ghost small" data-feedback="${esc(aid)}" data-correct="0" ${r.feedback ? 'disabled' : ''}>✗ Not right</button>
      <span class="spacer"></span><span class="muted small-text">${r.live ? 'Live Oracle data' : 'Database structure from the saved snapshot'}</span></div>`;
  drawGraph(aid);
  if (entry.lastAdded) {   // bring the column the user just added into view
    const th = [...el.querySelectorAll('th.added')].pop();
    const wrap = th?.closest('.tbl-wrap');
    if (th && wrap) wrap.scrollLeft = Math.max(0, th.offsetLeft - wrap.clientWidth / 2 + th.offsetWidth / 2);
    entry.lastAdded = null;
  }
}

function graphModel(entry) {
  const r = entry.result; const used = new Set(r.graph.nodes.map(n => n.table));
  const nodes = r.graph.nodes.map(n => ({ id: n.table, title: n.business, code: n.table, kind: n.kind, base: n.base,
    line3: n.base ? 'answer starts here' : n.columns.length ? `used: ${n.columns.slice(0, 3).join(', ')}` : 'link only',
    status: { text: n.rows != null ? `${n.approx ? '≈ ' : ''}${fmt(n.rows, 0)} rows` : '', cls: 'muted' } }));
  entry.extra.nodes.filter(n => !used.has(n.id)).forEach(n => nodes.push(n));
  const edges = r.graph.edges.map(e => ({ from: e.source, to: e.target, label: e.on.split(' AND ').pop().replace(/^[A-Z0-9_]+\./, '').split(' = ')[0], title: `${e.label} · ${e.on} (${e.status})`, cls: e.status === 'verified' || e.status === 'declared' ? 'link' : 'proposed' }));
  entry.extra.edges.forEach(e => { if (!edges.some(x => x.from === e.from && x.to === e.to)) edges.push(e); });
  return { nodes, edges };
}

function drawGraph(aid) {
  const entry = answers.get(aid); const el = $(`#ans-${aid} [data-graph]`); if (!el) return;
  renderGraph(el, graphModel(entry), { nodeW: 196, nodeH: 72, gapX: 70, gapY: 16, label: 'Tables used in this answer',
    onSelect: t => preview(aid, t), onExpand: t => expand(aid, t) });
}

async function expand(aid, table) {
  const entry = answers.get(aid);
  try {
    const n = await api(`/api/knowledge/neighbours/${id(table)}`);
    const have = new Set(graphModel(entry).nodes.map(x => x.id));
    let added = 0;
    n.neighbours.slice(0, 10).forEach(x => {
      if (!have.has(x.table)) { entry.extra.nodes.push({ id: x.table, title: x.business, code: x.table, kind: x.kind, dashed: true, line3: x.label, status: { text: x.rows != null ? `${x.approx ? '≈ ' : ''}${fmt(x.rows, 0)} rows` : x.status, cls: 'muted' } }); have.add(x.table); added++; }
      const from = x.direction === 'up' ? x.table : table, to = x.direction === 'up' ? table : x.table;
      entry.extra.edges.push({ from, to, title: `${x.label} · ${x.on} (${x.status})`, label: '', cls: x.status === 'verified' || x.status === 'declared' ? 'related' : 'proposed' });
    });
    drawGraph(aid);
    if (!added) notify(`${n.business} has no other linked tables.`, '');
  } catch (e) { notify(e.message, 'error'); }
}

async function preview(aid, table) {
  const entry = answers.get(aid); const r = entry.result;
  let p;
  try { p = await api('/api/chat/preview', { method: 'POST', body: { table, intent: r.intent, limit: 20 } }); }
  catch (e) { return notify(e.message, 'error'); }
  const inAnswer = new Set(r.columns.map(c => c.ref));
  const all = p.columns.filter(c => !c.hidden);
  const filled = all.filter(c => p.rows.some(row => row[p.columns.indexOf(c)] != null && row[p.columns.indexOf(c)] !== ''));
  let showEmpty = false;
  const addBtn = c => inAnswer.has(c.ref) ? '<span class="chip reuse">in answer</span>' : `<button class="tiny secondary" data-add-col="${esc(c.ref)}" title="Add ${esc(c.label)} to the answer">＋ Add</button>`;
  let tab = 'data';
  const body = () => { const cols = tab === 'data' && !showEmpty && filled.length ? filled : all; return (tab === 'data'
    ? (p.rows.length ? `${all.length > filled.length ? `<label class="check mb-8"><input type="checkbox" data-empty ${showEmpty ? 'checked' : ''}> Show empty columns too (${all.length - filled.length})</label>` : ''}` + `<div class="tbl-wrap max-h-52vh"><table><thead><tr>${cols.map(c => `<th>${esc(c.label)}<span class="code">${esc(c.column)}</span><div class="mt-4">${addBtn(c)}</div></th>`).join('')}</tr></thead>
        <tbody>${p.rows.map(row => `<tr>${cols.map(c => { const v = row[p.columns.indexOf(c)]; return `<td class="${typeof v === 'number' ? 'num' : ''} ${v == null ? 'null' : ''}">${v == null ? '—' : esc(show(v))}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>`
      : '<p class="empty">No rows in this table match the answer.</p>')
    : `<div class="tbl-wrap max-h-52vh"><table><thead><tr><th>Business name</th><th>Column</th><th>Type</th><th>Meaning</th><th></th></tr></thead><tbody>
        ${cols.map(c => `<tr><td><b>${esc(c.label)}</b></td><td class="mono">${esc(c.column)}</td><td class="muted">${esc(c.type || '')}</td>
          <td>${esc(c.role || '')}${c.references ? ` <span class="chip label">→ ${esc(c.references)}</span>` : ''}</td><td>${addBtn(c)}</td></tr>`).join('')}</tbody></table></div>`); };
  const render = () => {
    const m = modal(`<div class="row wrap"><span class="pill ${p.kind === 'master' ? 'master' : p.kind === 'header' ? 'header' : 'brand'}">${esc(p.kind)}</span><h2 id="modal-title">${esc(p.business)}</h2><code class="muted">${esc(p.table)}</code>
        ${p.rows_total != null ? `<span class="pill" title="${p.rows_approx ? 'Approximate (Oracle statistics) — this table is very large' : 'Exact count, read just now'}">${p.rows_approx ? '≈ ' : ''}${fmt(p.rows_total, 0)} rows in Oracle</span>` : ''}</div>
        <p class="muted small-text mt-4">${esc(p.description || '')}${p.scoped ? ` · Showing rows related to this answer (${esc(p.scope.join(', '))}).` : ' · Showing the latest rows (this table is not linked to the answer’s filters).'}</p>
        <nav class="tabs modal-tabs"><button class="${tab === 'data' ? 'active' : ''}" data-ptab="data">Data preview</button><button class="${tab === 'cols' ? 'active' : ''}" data-ptab="cols">Columns (${all.length})</button></nav>`,
      body(),
      `<span class="muted small-text">Linked tables:</span>${p.neighbours.slice(0, 10).map(n => `<button class="tiny ghost" data-open-table="${esc(n.table)}" title="${esc(n.label)} · ${esc(n.on)}">${n.direction === 'up' ? '↑' : '↓'} ${esc(n.business)}</button>`).join('')}`);
    $$('[data-ptab]', m.head).forEach(b => b.onclick = () => { tab = b.dataset.ptab; render(); });
    m.body.onclick = async e => { const b = e.target.closest('[data-add-col]'); if (!b) return; m.close(); await addColumn(aid, b.dataset.addCol); };
    m.body.onchange = e => { if (e.target.matches('[data-empty]')) { showEmpty = e.target.checked; render(); } };
    m.foot.onclick = e => { const b = e.target.closest('[data-open-table]'); if (b) preview(aid, b.dataset.openTable); };
  };
  render();
}

async function rerun(aid, intent, note) {
  const entry = answers.get(aid); const el = $(`#ans-${aid}`);
  el.classList.add('rerunning');   // dimmed while the answer is re-run
  try {
    const r = await api('/api/chat/run', { method: 'POST', body: { intent, question: entry.result.question } });
    if (r.kind !== 'answer') { el.classList.remove('rerunning'); return notify(r.message, 'error'); }
    r.linked_values = entry.result.linked_values;
    answers.delete(aid);
    const holder = document.createElement('div'); el.replaceWith(holder);
    mount(holder, r, entry);
    if (note) notify(note, 'success');
  } catch (e) { el.classList.remove('rerunning'); notify(e.message, 'error'); }
}

async function addColumn(aid, ref) {
  const entry = answers.get(aid); const r = entry.result; const it = structuredClone(r.intent);
  if ((it.measures || []).length) it.group_by = [...(it.group_by || []), ref];
  else it.columns = [...(it.columns?.length ? it.columns : r.columns.filter(c => c.kind !== 'key').map(c => c.ref)), ref];
  entry.added.add(ref); entry.lastAdded = ref;
  await rerun(aid, it, 'Column added — the answer was re-run with the linked tables.');
}

async function removeColumn(aid, ref) {
  const entry = answers.get(aid); const r = entry.result; const it = structuredClone(r.intent);
  if ((it.measures || []).length) it.group_by = (it.group_by || []).filter(g => (typeof g === 'string' ? g : g.column) !== ref && !r.columns.some(c => c.ref === ref && (typeof g === 'string' ? g : g.column) === `${c.table}.${c.column}`));
  else it.columns = (it.columns?.length ? it.columns : r.columns.filter(c => c.kind !== 'key').map(c => c.ref)).filter(x => x !== ref);
  entry.added.delete(ref);
  await rerun(aid, it);
}

export function bindChat() {
  const input = $('#chat-input');
  $('#chat-form').addEventListener('submit', e => { e.preventDefault(); const q = input.value; input.value = ''; input.style.height = ''; askQuestion(q); });
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chat-form').requestSubmit(); } });
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 140) + 'px'; });
  $('#view-chat').addEventListener('click', async e => {
    const ask = e.target.closest('[data-ask]'); if (ask) return askQuestion(ask.dataset.ask);
    const h = e.target.closest('[data-hist]');
    if (h) {
      try { const r = await api(`/api/chat/history/${id(h.dataset.hist)}`); $('#welcome')?.remove();
        $('#thread').insertAdjacentHTML('beforeend', `<div class="q-bubble">${esc(r.question)}</div><div></div>`); mount($('#thread').lastElementChild, r); $(`#ans-${r.id}`)?.scrollIntoView({ behavior: 'smooth' }); }
      catch (err) { notify(err.message, 'error'); }
      return;
    }
    const rm = e.target.closest('[data-remove-col]'); if (rm) { e.stopPropagation(); return removeColumn(rm.dataset.aid, rm.dataset.removeCol); }
    const th = e.target.closest('th[data-sort]');
    if (th) { const aid = th.closest('.answer').id.slice(4); const entry = answers.get(aid); const i = Number(th.dataset.sort); entry.sort = { i, dir: entry.sort?.i === i ? -entry.sort.dir : -1 }; return draw(aid); }
    const c = e.target.closest('[data-csv]');
    if (c) { const r = answers.get(c.dataset.csv).result; return csv(r.columns.map((x, i) => ({ ...x, index: i })).filter(x => !x.hidden), r.rows); }
    const f = e.target.closest('[data-feedback]');
    if (f) {
      const aid = f.dataset.feedback; const correct = f.dataset.correct === '1';
      try { const res = await api(`/api/chat/${id(aid)}/feedback`, { method: 'POST', body: { correct } });
        answers.get(aid).result.feedback = correct ? 'correct' : 'wrong'; draw(aid);
        notify(correct ? (res.example ? 'Thanks — saved as a confirmed example. Similar questions will be answered the same way.' : 'Thanks for confirming.') : 'Thanks — marked as not right. Try rephrasing, or add/remove columns.', 'success');
        sideLists();
      } catch (err) { notify(err.message, 'error'); }
    }
  });
}
