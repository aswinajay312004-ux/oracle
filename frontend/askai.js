'use strict';
/* Ask AI: questions about saved orders (fixed read-only queries) or about the upload on screen (local model). */
const ASK = { history: [] };
const SUGGEST = ['How many pieces in total?', 'Pieces per size', 'Orders and pieces per buyer', 'Latest saved orders', 'Which uploads are waiting for approval?'];

function openAsk(question = '') {
  const draft = location.hash.startsWith('#/new/') && NO.draft && !NO.draft.result ? NO.draft : null;
  Drawer.open({ eyebrow: 'Local AI assistant · nothing leaves this computer', title: 'Ask AI', render: () => `
      <div class="callout">${icon('shield')}<div>Answers about saved orders come from fixed, read-only queries on the Oracle order table, so the numbers are exact. The AI only works out what you are asking. ${draft ? `You can also ask about <strong>${esc(draft.filename)}</strong>.` : ''}</div></div>
      ${draft ? `<div class="seg"><button type="button" data-scope="orders" class="${ASK.scope !== 'upload' ? 'active' : ''}">Saved orders</button><button type="button" data-scope="upload" class="${ASK.scope === 'upload' ? 'active' : ''}">This upload</button></div>` : ''}
      <div class="suggest">${(ASK.scope === 'upload' && draft ? ['Which values were not found in the Excel?', 'What will be saved to Oracle?', 'Are the quantities correct?'] : SUGGEST).map(s => `<button type="button" data-q="${esc(s)}">${esc(s)}</button>`).join('')}</div>
      <div class="chat" id="chat">${ASK.history.map(renderMsg).join('') || '<p class="muted">Ask in English or Tanglish, for example “carter&#39;s order quantity evvalavu?”</p>'}</div>
      <form class="chat-input" id="ask-form"><input id="ask-q" placeholder="Ask about orders, buyers, sizes…" maxlength="500" autocomplete="off" value="${esc(question)}"><button class="btn primary" type="submit" aria-label="Send">${icon('send')}</button></form>`,
    after: body => {
      $$('[data-scope]', body).forEach(b => b.onclick = () => { ASK.scope = b.dataset.scope; Drawer.show(null); });
      $$('[data-q]', body).forEach(b => b.onclick = () => send(b.dataset.q, draft));
      $('#ask-form', body).onsubmit = e => { e.preventDefault(); const q = $('#ask-q', body).value.trim(); if (q) send(q, draft); };
      $('#ask-q', body).focus(); const chat = $('#chat', body); chat.lastElementChild?.scrollIntoView({ block: 'end' });
      if (question) { $('#ask-q', body).value = ''; send(question, draft); }
    } });
}
function renderMsg(m) {
  if (m.me) return `<div class="msg me">${esc(m.text)}</div>`;
  const rows = m.rows || []; const cols = rows.length ? Object.keys(rows[0]) : [];
  return `<div class="msg ai">${esc(m.text)}${rows.length ? `<div class="table-wrap" style="margin-top:8px;max-height:260px"><table class="data-table"><thead><tr>${cols.map(c => `<th>${esc(c.replace(/_/g, ' '))}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(fmt(r[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : ''}
    ${m.how ? `<span class="how">How: ${esc(m.how)}${m.understood_by ? ` · understood by ${esc(m.understood_by)}` : ''}${m.sql ? `<br><code>${esc(m.sql)}</code>` : ''}</span>` : ''}</div>`;
}
async function send(q, draft) {
  ASK.history.push({ me: true, text: q }); Drawer.show(null);
  try {
    const r = ASK.scope === 'upload' && draft ? await api(`/api/drafts/${encodeURIComponent(draft.id)}/assist`, { method: 'POST', body: { question: q } }).then(x => ({ text: x.answer, how: 'Explained from this upload and its preview only.' }))
      : await api('/api/ai/ask', { method: 'POST', body: { question: q } });
    ASK.history.push(r);
  } catch (e) { ASK.history.push({ text: e.message }); }
  ASK.history = ASK.history.slice(-30); Drawer.show(null);
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
  const drafts = SEARCH.drafts.filter(d => !d.committed && `${d.filename} ${d.refs.join(' ')}`.toLowerCase().includes(n)).slice(0, 4)
    .map(d => ({ href: `#/new/${d.id}`, icon: 'upload', title: d.filename, sub: 'Waiting for approval' }));
  SEARCH.items = [...orders, ...drafts, { ask: q, icon: 'sparkle', title: `Ask AI: “${q}”`, sub: 'Answer from the saved orders' }]; SEARCH.sel = -1;
  box.innerHTML = SEARCH.items.map((it, i) => `<button type="button" data-i="${i}">${icon(it.icon)}<span><strong>${esc(it.title)}</strong><small>${esc(it.sub)}</small></span></button>`).join('');
  box.hidden = false; $$('button', box).forEach(b => b.onmousedown = e => { e.preventDefault(); pickSearch(Number(b.dataset.i)); });
}
function pickSearch(i) {
  const it = SEARCH.items[i] ?? SEARCH.items[SEARCH.items.length - 1]; $('#search-results').hidden = true; $('#global-q').value = '';
  if (!it) return; if (it.ask) openAsk(it.ask); else go(it.href);
}
