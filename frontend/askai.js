'use strict';
/* AI Assistant: questions about any order in plain language. Answers come from Oracle through fixed, read-only
   queries; each answer shows which tables and queries it used. */
const ASK = { history: [], context: {}, busy: false };
const SUGGEST = ['Show me the latest orders', 'Pieces per size', 'Orders and pieces per buyer', 'Which uploads are waiting for approval?'];
const ORDER_SUGGEST = ['What are the size details for this order?', 'Which tables contain this order?', 'Does this order require printing?'];

/* Page-specific layout (kept here so the page works on its own; the shared stylesheet may override it). */
if (!document.getElementById('ai-style')) {
  const st = document.createElement('style'); st.id = 'ai-style';
  st.textContent = `.ai-card{display:flex;flex-direction:column;gap:12px;padding:16px 18px;min-height:calc(100vh - 230px)}
  .ai-head:empty{display:none} .ai-chat{flex:1;overflow:auto;min-height:280px;max-height:calc(100vh - 340px);padding:4px 2px}
  .ai-chat .msg.ai{max-width:100%;width:100%;background:#f4f7f5;border:1px solid var(--line-soft,#edf2ef)} .ai-chat .msg.ai p{margin:0 0 6px;font-weight:560}
  .ai-chat .msg.err{background:#fdf6e6;border-color:#efd8a6} .ai-sec{margin-top:10px} .ai-sec h4{margin:0 0 6px;font-size:13.5px}
  .ai-facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px 14px;margin:0}
  .ai-facts div{padding:6px 9px;background:#fff;border:1px solid var(--line-soft,#edf2ef);border-radius:8px}
  .ai-facts dt{font-size:12px;color:var(--muted,#627570)} .ai-facts dt code{font-size:10.5px;color:var(--faint,#94a39c)} .ai-facts dd{margin:0;font-weight:650}
  .ai-chat .data-table{background:#fff}`;
  document.head.appendChild(st);
}

function openAsk(question = '') { go('#/ai' + (question ? '?q=' + encodeURIComponent(question) : '')); }

route('ai', async view => {
  setTitle('AI Assistant', 'Ask about any order in your own words. Answers come from Oracle with read-only queries, and show which tables they used.');
  const pending = window.ASK_PENDING; window.ASK_PENDING = null;
  page(view, `<section class="card ai-card"><div class="ai-head" id="ai-ctx"></div>
      <div class="chat ai-chat" id="chat" aria-live="polite"></div>
      <div class="suggest" id="ai-suggest"></div>
      <form class="chat-input" id="ask-form"><input id="ask-q" placeholder="Ask about an order, a buyer, sizes… e.g. “Show me order PK/26-27/E/1373”" maxlength="500" autocomplete="off" aria-label="Your question">
        <button class="btn primary" type="submit" aria-label="Send">${icon('send')} Ask</button></form></section>`);
  drawAsk(view);
  $('#ask-form', view).onsubmit = e => { e.preventDefault(); const q = $('#ask-q', view).value.trim(); if (q) { $('#ask-q', view).value = ''; sendAsk(q, view); } };
  $('#ask-q', view).focus();
  if (pending) sendAsk(pending, view);
});

function drawAsk(view) {
  const chat = $('#chat', view); if (!chat) return;
  chat.innerHTML = ASK.history.length ? ASK.history.map(renderMsg).join('')
    : `<div class="empty">${icon('sparkle')}<p>Ask in English or Tanglish, for example “carter&#39;s order quantity evvalavu?” or “Show me order PK/26-27/E/1373”.</p></div>`;
  if (ASK.busy) chat.insertAdjacentHTML('beforeend', '<div class="msg ai"><span class="spinner small"></span> Looking it up in Oracle…</div>');
  paintIcons(chat);
  const ctx = $('#ai-ctx', view);
  ctx.innerHTML = ASK.context.order ? `<span class="filter-chip">Talking about order ${esc(ASK.context.order)}<button type="button" id="ctx-clear" aria-label="Clear the order">${icon('x')}</button></span>` : '';
  paintIcons(ctx);
  $('#ctx-clear', ctx)?.addEventListener('click', () => { ASK.context = {}; drawAsk(view); });
  const last = [...ASK.history].reverse().find(m => !m.me);
  const chips = [...(last?.suggestions || []), ...(ASK.context.order ? ORDER_SUGGEST : []), ...SUGGEST].filter((x, i, a) => a.indexOf(x) === i).slice(0, 7);
  $('#ai-suggest', view).innerHTML = chips.map(s => `<button type="button" data-q="${esc(s)}">${esc(s)}</button>`).join('');
  $$('[data-q]', view).forEach(b => b.onclick = () => sendAsk(b.dataset.q, view));
  chat.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'smooth' });
}

function sectionHtml(s) {
  if (s.kind === 'facts') return `<div class="ai-sec"><h4>${esc(s.title)}</h4><dl class="ai-facts">${(s.facts || []).map(f => `<div><dt>${esc(f.label)}${f.column ? ` <code>${esc(f.column)}</code>` : ''}</dt><dd>${esc(fmt(f.value))}</dd></div>`).join('')}</dl></div>`;
  const rows = s.rows || []; const cols = s.columns?.length ? s.columns : rows.length ? Object.keys(rows[0]) : [];
  if (!rows.length) return `<div class="ai-sec"><h4>${esc(s.title)}</h4><p class="muted">No rows.</p></div>`;
  return `<div class="ai-sec"><h4>${esc(s.title)} <span class="muted">(${rows.length})</span></h4><div class="table-wrap" style="max-height:320px"><table class="data-table"><thead><tr>${cols.map(c => `<th>${esc(String(c).replace(/_/g, ' '))}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr>${cols.map(c => `<td class="${typeof r[c] === 'number' ? 'num' : ''}">${esc(r[c] == null ? '' : typeof r[c] === 'number' ? fmt(r[c]) : String(r[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;
}

function renderMsg(m) {
  if (m.me) return `<div class="msg me">${esc(m.text)}</div>`;
  const sql = Array.isArray(m.sql) ? m.sql : m.sql ? [m.sql] : [];
  const how = m.understood_by || m.how || sql.length ? `<details class="quiet"><summary>How this was answered</summary><span class="how">
      ${m.understood_by ? `Question understood by ${esc(m.understood_by)}.` : ''} ${m.how ? esc(m.how) : ''}${m.found?.length ? ` Found by ${esc(m.found.join(', '))}.` : ''}
      ${sql.map(q => `<br><code>${esc(q)}</code>`).join('')}</span></details>` : '';
  return `<div class="msg ai${m.error ? ' err' : ''}"><p>${esc(m.text)}</p>${(m.sections || []).map(sectionHtml).join('')}${how}</div>`;
}

async function sendAsk(q, view) {
  if (ASK.busy) return;
  ASK.history.push({ me: true, text: q }); ASK.busy = true; drawAsk(view);
  try {
    const r = await api('/api/ai/ask', { method: 'POST', body: { question: q, context: ASK.context.order ? { order: ASK.context.order } : {} } });
    if (r.order) ASK.context = { order: r.order };
    ASK.history.push(r);
  } catch (e) { ASK.history.push({ text: e.message, error: true }); }
  ASK.busy = false; ASK.history = ASK.history.slice(-40);
  if ($('#chat') && location.hash.startsWith('#/ai')) drawAsk($('#view'));
}

/* global search: orders and uploads; Enter with no match asks the AI */
const SEARCH = { at: 0, orders: [], drafts: [], sel: -1, items: [] };
async function searchData() {
  if (Date.now() - SEARCH.at > 30000) { [SEARCH.orders, SEARCH.drafts] = await Promise.all([api('/api/orders').catch(() => []), api('/api/drafts').catch(() => [])]); SEARCH.at = Date.now(); }
}
async function runSearch(q) {
  const box = $('#search-results'); if (!q.trim()) { box.hidden = true; return; }
  await searchData(); const n = q.toLowerCase();
  const orders = SEARCH.orders.filter(o => `${o.order_ref} ${o.buyer} ${o.style} ${o.filename}`.toLowerCase().includes(n)).slice(0, 6)
    .map(o => ({ href: `#/orders/${o.ordmasid}`, icon: 'file', title: o.order_ref, sub: `${o.buyer || '—'} · ${o.style || ''} · ${fmt(o.quantity)} pcs` }));
  const drafts = SEARCH.drafts.filter(d => !d.committed && `${d.filename} ${(d.refs || []).join(' ')}`.toLowerCase().includes(n)).slice(0, 4)
    .map(d => ({ href: `#/new/${d.id}`, icon: 'upload', title: d.filename, sub: 'Waiting for approval' }));
  SEARCH.items = [...orders, ...drafts, { ask: q, icon: 'sparkle', title: `Ask AI: “${q}”`, sub: 'Answer from Oracle' }]; SEARCH.sel = -1;
  box.innerHTML = SEARCH.items.map((it, i) => `<button type="button" data-i="${i}">${icon(it.icon)}<span><strong>${esc(it.title)}</strong><small>${esc(it.sub)}</small></span></button>`).join('');
  box.hidden = false; $$('button', box).forEach(b => b.onmousedown = e => { e.preventDefault(); pickSearch(Number(b.dataset.i)); });
}
function pickSearch(i) {
  const it = SEARCH.items[i] ?? SEARCH.items[SEARCH.items.length - 1]; $('#search-results').hidden = true; $('#global-q').value = '';
  if (!it) return; if (it.ask) openAsk(it.ask); else go(it.href);
}
