// App shell: authentication, navigation, status lights, saved orders and global actions.
import { $, $$, esc, plural, fmt, state, can, notify, busy, closeDrawer } from './util.js';
import { api, id, onUnauthorized } from './api.js';
import * as ws from './workspace.js';
import { loadExplorer, bindExplorer, exploreOrder } from './explorer.js';
import { loadAdmin, bindAdmin } from './admin.js';
import { loadChat, bindChat } from './chat.js';
import { loadDashboard } from './dashboard.js';

async function session() {
  const s = await fetch('/api/session', { credentials: 'same-origin' }).then(r => r.json());
  state.user = s.authenticated ? s.username : null; state.permissions = s.permissions || []; state.setup = s.needs_setup;
  $('#auth').hidden = s.authenticated; $('#logout').hidden = !s.authenticated;
  $('#auth-title').textContent = s.needs_setup ? 'Set up POPPYS AI' : 'Welcome back';
  $('#auth-text').textContent = s.needs_setup ? 'Create the administrator account for this computer. You can add operators and approvers afterwards.' : 'Sign in to prepare and approve ERP entries.';
  $('#auth-submit').textContent = s.needs_setup ? 'Create administrator →' : 'Sign in →';
  $('#auth-pass').autocomplete = s.needs_setup ? 'new-password' : 'current-password';
  $('#auth-pass').minLength = s.needs_setup ? 12 : 1;   // new passwords: 12+ (the server also refuses common ones)
  $('#username').textContent = s.username || '—'; $('#role').textContent = s.role || ''; $('#avatar').textContent = (s.username || 'P')[0].toUpperCase();
  return s.authenticated;
}

async function status() {
  try {
    const h = await api('/api/health'); state.health = h;
    const db = h.database; const o = $('#oracle-status'); const a = $('#ai-status');
    o.className = `status ${db.connected ? 'ok' : 'off'}`;
    o.querySelector('span').textContent = db.connected ? 'Oracle connected' : 'Oracle offline';
    o.querySelector('small').textContent = db.connected ? `${db.owner} · ${db.tables} tables` : 'start Docker + oracle26ai';
    o.title = db.connected ? `Connected to ${db.dsn || ''}` : db.error || '';
    a.className = `status ${h.ollama.available ? 'ok' : 'idle'}`;
    a.querySelector('span').textContent = h.ollama.available ? 'Local AI ready' : 'Local AI off';
    a.querySelector('small').textContent = h.ollama.available ? h.ollama.model : 'optional · rules still work';
  } catch (_) { $('#oracle-status').className = 'status off'; }
}

async function lists() {
  try {
    const m = await api('/api/masters');
    document.getElementById('master-lists')?.remove();
    const div = document.createElement('div'); div.id = 'master-lists'; div.hidden = true;
    div.innerHTML = Object.entries(m).map(([k, v]) => `<datalist id="list-${esc(k)}">${v.filter(x => !x.cancelled).slice(0, 3000).map(x => `<option value="${esc(x.label)}"></option>`).join('')}</datalist>`).join('');
    document.body.appendChild(div);
  } catch (_) { /* typing stays free; the check validates masters */ }
  try {
    const mp = await api('/api/mapping');
    state.mappingStatus = Object.fromEntries(mp.tables.map(t => [t.table, t.status]));
  } catch (_) { state.mappingStatus = {}; }
}

function go(next) {
  $$('.nav [data-view]').forEach(t => t.classList.toggle('active', t.dataset.view === next));
  $$('.view').forEach(v => { v.hidden = v.id !== `view-${next}`; });
  closeDrawer();
  if (next === 'dashboard') loadDashboard();
  if (next === 'history') history();
  if (next === 'explorer') loadExplorer();
  if (next === 'admin') loadAdmin();
  if (next === 'chat') { loadChat(); setTimeout(() => $('#chat-input').focus(), 50); }
  if (next === 'workspace' && !state.draft) ws.recent();
  if (location.hash !== '#' + next) history_replace(next);
}
function history_replace(v) { try { window.history.replaceState(null, '', '#' + v); } catch (_) { /* file previews */ } }

async function history() {
  $('#history-detail').hidden = true;
  await busy('Reading saved imports from Oracle…', async () => {
    let list;
    try { list = await api('/api/history'); } catch (e) { $('#history').innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
    $('#history').innerHTML = list.length ? `<div class="tbl-wrap flush"><table><thead><tr><th>File</th><th>Order</th><th>Approved by</th><th>Saved</th><th></th></tr></thead><tbody>
      ${list.map(r => `<tr class="clickable" data-receipt="${esc(r.job_id)}"><td><b>${esc(r.filename || r.job_id)}</b><div class="muted small-text">${esc(r.format || '')}</div></td>
        <td>${esc((r.orders || []).join(', ') || '—')}<div class="muted small-text">${esc((r.styles || []).join(', '))}</div></td><td>${esc(r.approver)}</td>
        <td class="nowrap">${esc(new Date(r.committed_at).toLocaleString())}</td><td><button class="secondary small" data-receipt="${esc(r.job_id)}">Show where it was saved</button></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="empty">No orders have been saved yet.</p>';
  });
}

async function receipt(jobId) {
  await busy('Reading the saved records…', async () => {
    const result = await api(`/api/history/${id(jobId)}`);
    const target = $('#history-detail'); target.hidden = false;
    await ws.showSaved(result, target, true);
    target.scrollIntoView({ behavior: 'smooth' });
  });
}

function showPassword(visible) {
  $('#auth-pass').type = visible ? 'text' : 'password';
  const b = $('#auth-pass-show'); b.textContent = visible ? 'Hide' : 'Show';
  b.setAttribute('aria-pressed', String(visible)); b.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
}

function bind() {
  onUnauthorized(() => session());
  $('#auth-pass-show').addEventListener('click', () => { showPassword($('#auth-pass').type === 'password'); $('#auth-pass').focus(); });
  $('#auth-form').addEventListener('submit', async e => {
    e.preventDefault(); $('#auth-error').hidden = true; $('#auth-submit').disabled = true;
    try {
      await api(state.setup ? '/api/setup' : '/api/login', { method: 'POST', body: { username: $('#auth-user').value.trim(), password: $('#auth-pass').value } });
      $('#auth-pass').value = ''; showPassword(false);
      if (await session()) start();
    } catch (err) { $('#auth-error').textContent = err.message; $('#auth-error').hidden = false; }
    finally { $('#auth-submit').disabled = false; }
  });
  $('#logout').addEventListener('click', () => busy('Signing out…', async () => { await api('/api/logout', { method: 'POST' }); state.draft = null; await session(); }));
  $$('.nav [data-view]').forEach(t => t.addEventListener('click', () => go(t.dataset.view)));
  const input = $('#file-input'); const drop = $('#dropzone');
  input.addEventListener('change', e => ws.upload(e.target.files[0]));
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  ['dragenter', 'dragover'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { if (e.dataTransfer.files.length > 1) return notify('Drop one order file at a time.', 'warning'); ws.upload(e.dataTransfer.files[0]); });
  $('#go-review').addEventListener('click', ws.goReview);
  $('#drawer-close').addEventListener('click', closeDrawer); $('#scrim').addEventListener('click', closeDrawer);
  $('#modal-close').addEventListener('click', () => $('#modal').close());
  $('#modal').addEventListener('click', e => { if (e.target === $('#modal')) $('#modal').close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#drawer').hidden) closeDrawer(); });
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-open-draft],[data-receipt],[data-receipt-go],[data-goto],[data-explore-order],[data-act2]'); if (!b || state.busy) return;
    if (b.dataset.goto) return go(b.dataset.goto);
    if (b.dataset.receiptGo) { go('history'); return receipt(b.dataset.receiptGo); }
    if (b.dataset.openDraft) { go('workspace'); return ws.openDraft(b.dataset.openDraft); }
    if (b.dataset.receipt) return receipt(b.dataset.receipt);
    if (b.dataset.exploreOrder) { closeDrawer(); go('explorer'); return exploreOrder(b.dataset.exploreOrder); }
    if (b.dataset.act2 === 'approve') return ws.approve();
    if (b.dataset.act2 === 'back-review') return ws.backToReview();
    if (b.dataset.act2 === 'another') return ws.resetWorkspace();
  });
  window.addEventListener('beforeunload', e => { if (state.dirty) { e.preventDefault(); e.returnValue = ''; } });
  ws.bindWorkspace(); bindExplorer(); bindAdmin(); bindChat();
}

async function start() {
  $$('[data-permission]').forEach(el => { el.hidden = !can(el.dataset.permission); });
  await Promise.allSettled([status(), lists()]);
  const initial = (location.hash || '').slice(1);
  go(['dashboard', 'workspace', 'history', 'chat', 'explorer', 'admin'].includes(initial) ? initial : 'dashboard');
  clearInterval(start.timer); start.timer = setInterval(async () => { await status(); if (!$('#view-dashboard').hidden) loadDashboard(); }, 30000);
}

bind();
(async () => {
  try { if (await session()) await start(); }
  catch (e) { notify(`Unable to reach the POPPYS AI server: ${e.message}`, 'error'); }
})();
void plural; void fmt;
