// "Where the data goes": table flowchart on top, and the selected table's rows in table format below.
// Works for the approval preview (planned rows) and for saved orders (rows read live from Oracle).
import { $, esc, show, plural, formView, source } from './util.js';
import { renderGraph } from './graph.js';

const ORDER = { header: 0, detail: 1, master: 2 };

function nice(n) {
  const name = n.kind === 'master' ? n.business.replace(/ master$/, '') : n.business.includes(' — ') ? n.business.split(' — ').pop() : n.business;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function graphModel(graph) {
  const nodes = graph.nodes.map(n => {
    const counts = [n.inserted ? `${n.inserted} new` : '', n.reused ? `${n.reused} existing` : ''].filter(Boolean).join(' · ');
    let status;
    if (n.reference_only) status = { text: `${n.reference_rows} rows · not saved yet`, cls: 'muted' };
    else if (n.planned) status = { text: n.kind === 'master' ? (n.inserted ? 'will be created' : 'existing record') : 'ready to save', cls: 'muted' };
    else status = { text: n.verified ? '✓ matches Oracle' : '⚠ check differences', cls: n.verified ? '' : 'bad' };
    return { id: n.table, title: nice(n), code: n.table,
             kind: n.kind, line3: counts || (n.reference_only ? 'checked, mapping not verified' : ''), status, dashed: !!n.reference_only };
  });
  const edges = graph.edges.map(e => {
    const master = graph.nodes.find(n => n.table === e.source)?.kind === 'master';
    return { from: e.source, to: e.target, label: master ? '' : e.on.split('=')[0].trim(), title: `${e.label} (${e.target}.${e.on})`, cls: master ? 'master' : 'link' };
  });
  return { nodes, edges };
}

export function renderFlowView(el, graph, { planned = false, columns = {}, onReferenceOnly } = {}) {
  const first = [...graph.nodes].sort((a, b) => (ORDER[a.kind] ?? 3) - (ORDER[b.kind] ?? 3))[0];
  el.innerHTML = `<div class="flow-wrap" data-flow></div>
    <div class="legend"><span><i style="background:var(--header)"></i>Order header</span><span><i style="background:var(--brand)"></i>Order detail tables</span>
      <span><i style="background:var(--master)"></i>Master tables</span><span><i style="border:1.5px dashed #94a3b8;background:#fff"></i>Checked but not saved</span>
      <span class="muted">Click a table to see its rows below</span></div>
    <div class="table-view" data-tv></div>`;
  const tv = el.querySelector('[data-tv]');
  const show = table => renderTable(tv, graph.nodes.find(n => n.table === table), { planned, columns, onReferenceOnly });
  renderGraph(el.querySelector('[data-flow]'), graphModel(graph), {
    masterBand: true, selected: first?.table, onSelect: show, label: 'Affected Oracle tables', gapX: 120,
    bandTitle: 'MASTER DATA USED (point at a table to see where it is linked)', treeTitle: 'ORDER RECORDS · linked by ORDMASID and line number (SUBNO)',
  });
  if (first) show(first.table);
}

function labelOf(node, col, columns) {
  return columns?.[node.table]?.[col]?.label || node.labels?.[col] || col;
}

function valueHtml(node, record, col) {
  const v = record.live?.[col];
  const dec = record.decoded?.[col];
  if (typeof v === 'string' && v.startsWith('$')) {
    if (dec != null) return `${esc(dec)} <span class="chip new">new master</span>`;
    return col === node.key ? '<span class="chip new">new ID on save</span>' : '<span class="chip calc">linked to new record</span>';
  }
  if (dec != null) return `${esc(dec)} <span class="chip label" title="ID ${esc(v)}">ID ${esc(v)}</span>`;
  if (v == null || v === '') return '<span class="muted">—</span>';
  return esc(show(v));
}

function srcOf(node, record, col, columns) {
  const meta = columns?.[node.table]?.[col]; const src = record.source || {};
  const f = meta?.field?.split('.')[1];
  const s = (f && src[f]) || null;
  if (s) return source(s);
  if (meta?.field?.startsWith('calc.')) return '<span class="chip calc">calculated</span>';
  return '';
}

function renderTable(el, node, { planned, columns, onReferenceOnly }) {
  if (!node) { el.innerHTML = ''; return; }
  const head = `<div class="table-view-head"><span class="pill ${node.kind === 'master' ? 'master' : node.kind === 'header' ? 'header' : 'brand'}">${esc(node.kind === 'master' ? 'Master' : node.kind === 'header' ? 'Order header' : 'Order detail')}</span>
    <h3>${esc(nice(node))}</h3><code class="muted">${esc(node.table)}</code><span class="spacer"></span>
    ${node.reference_only ? '' : `<span class="pill">${plural(node.records.length, 'row')}</span>`}
    ${planned ? '' : node.verified ? '<span class="pill ok">✓ read back from Oracle just now</span>' : '<span class="pill err">differences found</span>'}</div>`;
  if (node.reference_only) {
    el.innerHTML = head + `<div class="banner info"><span class="b-ic">i</span><div><b>${node.reference_rows} rows were checked but will not be saved.</b> ${esc(node.reason || '')}
      An administrator can verify this table in Setup → Write mapping. ${onReferenceOnly ? '<button class="link" data-ref-open>See these rows in the review screen</button>' : ''}</div></div>`;
    el.querySelector('[data-ref-open]')?.addEventListener('click', () => onReferenceOnly(node.table));
    return;
  }
  const recs = node.records;
  const written = new Set(recs.flatMap(r => r.written_columns || []));
  const allCols = [...new Set(recs.flatMap(r => Object.keys(r.live || {})))];
  let showAll = false;
  const draw = () => {
    const cols = showAll || !written.size ? allCols.filter(c => showAll || recs.some(r => r.live?.[c] != null && r.live?.[c] !== '')) : allCols.filter(c => written.has(c));
    if (recs.length === 1 && node.kind !== 'master') {
      const r = recs[0];
      body.innerHTML = formView(cols.map(c => ({ label: labelOf(node, c, columns), code: `${node.table}.${c}`, html: valueHtml(node, r, c),
        src: srcOf(node, r, c, columns), written: !planned && written.has(c) })));
    } else {
      body.innerHTML = `<div class="tbl-wrap" style="max-height:440px"><table><thead><tr><th>#</th>${cols.map(c => `<th>${esc(labelOf(node, c, columns))}<span class="code">${esc(c)}</span></th>`).join('')}
        <th>${planned ? 'Action' : 'Check'}</th></tr></thead><tbody>
        ${recs.map((r, i) => `<tr><td class="muted" title="${esc(r.where || '')}">${i + 1}</td>${cols.map(c => `<td class="${written.has(c) && !planned ? 'written' : ''} ${String(r.decoded?.[c] ?? r.live?.[c] ?? '').length <= 24 ? 'nowrap' : ''}">${valueHtml(node, r, c)}</td>`).join('')}
          <td>${planned ? (r.kind === 'reused' ? '<span class="chip reuse">use existing</span>' : '<span class="chip new">insert</span>') :
            r.found ? (r.matches ? '<span class="pill ok">✓ matches</span>' : `<span class="pill err">differs: ${esc(r.mismatched.join(', '))}</span>`) : '<span class="pill err">not found</span>'}</td></tr>`).join('')}
        </tbody></table></div>`;
    }
  };
  el.innerHTML = head + `${planned ? '' : `<label class="check" style="margin-bottom:10px"><input type="checkbox" data-all> Show every column Oracle returned (not only the ones POPPYS AI wrote)</label>`}<div data-body></div>`;
  const body = el.querySelector('[data-body]');
  el.querySelector('[data-all]')?.addEventListener('change', e => { showAll = e.target.checked; draw(); });
  draw();
}

export function flowCard(title, sub, pillHtml = '') {
  return `<section class="card flow-card"><div class="card-head"><div><h2>${title}</h2><div class="sub" data-flow-sub>${sub}</div></div>${pillHtml}</div><div data-flowview></div></section>`;
}

export function mountFlow(container, graph, opts) {
  renderFlowView($('[data-flowview]', container), graph, opts);
}
