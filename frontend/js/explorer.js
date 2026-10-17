// Read-only GARMENT_DEMO explorer: tables, records, and "where does this order live".
import { $, $$, esc, show, fmt, plural, busy, drawer, record } from './util.js';
import { api, id } from './api.js';

let tables = []; let current = null;

export async function loadExplorer() {
  await busy('Reading the table list from Oracle…', async () => {
    try { tables = await api('/api/explorer/tables'); }
    catch (e) { $('#tables').innerHTML = `<p class="empty">${esc(e.message)}</p>`; $('#table-view').innerHTML = '<p class="empty">Connect Oracle to browse tables.</p>'; return; }
    drawTables();
  });
}

function drawTables() {
  const q = $('#table-filter').value.trim().toUpperCase();
  const rank = { order: 0, master: 1, other: 2 };
  const list = tables.filter(t => !q || t.table.includes(q) || (t.business || '').toUpperCase().includes(q)).sort((a, b) => rank[a.kind] - rank[b.kind] || a.table.localeCompare(b.table));
  $('#tables').innerHTML = list.map(t => `<button class="tbl ${current?.table === t.table ? 'active' : ''}" data-table="${esc(t.table)}"><strong>${esc(t.table)}</strong><span class="n">${fmt(t.rows, 0)} rows</span><small>${esc(t.business || (t.kind === 'other' ? 'Not used by order entry' : ''))} · ${t.columns} columns</small></button>`).join('') || '<p class="empty">No tables.</p>';
}

export async function openTable(table, params = {}) {
  current = { table, column: params.column || '', value: params.value || '', offset: params.offset || 0 };
  drawTables();
  await busy(`Reading ${table}…`, async () => {
    const qs = new URLSearchParams({ limit: 50, offset: current.offset, ...(current.column && current.value ? { column: current.column, value: current.value } : {}) });
    const data = await api(`/api/explorer/tables/${id(table)}?${qs}`);
    current.data = data;
    $('#table-view').innerHTML = `<div class="card-head"><div><h2><code>${esc(table)}</code></h2><span class="muted">${plural(data.total, 'record')}${current.value ? ' matching the filter' : ''} · newest first · read-only</span></div></div>
      <form class="tools" id="filter-form"><label>Column<select id="f-col"><option value="">— any —</option>${data.columns.map(c => `<option ${c.name === current.column ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
        <label>Contains<input id="f-val" value="${esc(current.value)}" maxlength="100"></label><button class="secondary">Filter</button>${current.value ? '<button type="button" class="ghost" id="f-clear">Clear</button>' : ''}</form>
      <div class="grid-scroll"><table><thead><tr>${data.columns.map(c => `<th title="${esc(c.type)}">${esc(c.name)}</th>`).join('')}</tr></thead><tbody>
        ${data.rows.map((r, i) => `<tr data-row="${i}" style="cursor:pointer">${data.columns.map(c => `<td>${esc(show(r[c.name]))}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${data.columns.length}" class="empty">No records.</td></tr>`}
      </tbody></table></div>
      <div class="pager"><span>${data.total ? `${data.offset + 1}–${Math.min(data.offset + data.limit, data.total)} of ${fmt(data.total, 0)}` : ''}</span>
        <button class="ghost small" id="prev" ${data.offset ? '' : 'disabled'}>← Newer</button><button class="ghost small" id="next" ${data.offset + data.limit < data.total ? '' : 'disabled'}>Older →</button></div>`;
    $('#filter-form').onsubmit = e => { e.preventDefault(); openTable(table, { column: $('#f-col').value, value: $('#f-val').value }); };
    $('#f-clear')?.addEventListener('click', () => openTable(table));
    $('#prev').onclick = () => openTable(table, { ...current, offset: Math.max(0, current.offset - 50) });
    $('#next').onclick = () => openTable(table, { ...current, offset: current.offset + 50 });
    $$('#table-view tr[data-row]').forEach(tr => tr.onclick = () => {
      const row = data.rows[Number(tr.dataset.row)];
      drawer(`${table} record`, `<label class="check toggle-empty"><input type="checkbox" id="rec-empty"> Show empty columns</label><div id="rec">${record(row, [], { hideEmpty: true })}</div>
        ${row.ORDMASID ? `<button class="secondary" data-explore-order="${esc(row.ORDMASID)}" style="margin-top:12px">Show everything linked to ORDMASID ${esc(row.ORDMASID)}</button>` : ''}`);
      $('#rec-empty').onchange = e => { $('#rec').innerHTML = record(row, [], { hideEmpty: !e.target.checked }); };
    });
  });
}

export async function exploreOrder(ordmasid) {
  await busy('Collecting every row linked to this order…', async () => {
    const list = await api(`/api/explorer/order/${id(ordmasid)}`);
    drawer(`Order record ${ordmasid}`, list.length ? `<p class="muted">Rows in every visible table whose ORDMASID = ${esc(ordmasid)}.</p>` + list.map(t => `<details class="group" open><summary><span><strong><code>${esc(t.table)}</code></strong></span><span class="pill">${plural(t.rows.length, 'row')}</span><span></span></summary><div class="inner">${t.rows.map(r => `<div class="record">${record(r)}</div>`).join('')}</div></details>`).join('') : '<p class="empty">No rows reference this ORDMASID.</p>');
  });
}

export function bindExplorer() {
  $('#table-filter').addEventListener('input', drawTables);
  $('#tables').addEventListener('click', e => { const b = e.target.closest('[data-table]'); if (b) openTable(b.dataset.table); });
  $('#order-lookup').addEventListener('submit', e => { e.preventDefault(); const v = $('#order-id').value.trim(); if (/^\d+$/.test(v)) exploreOrder(v); });
}
