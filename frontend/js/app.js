// App shell: sign-in, navigation, the Oracle status light and actions any screen can start (open an order, a draft).
import { $, $$, esc, state, notify, busy, closeDrawer, setRail } from './util.js';
import { api, onUnauthorized } from './api.js';
import * as ws from './workspace.js';
import { loadExplorer, bindExplorer, showTables } from './explorer.js';
import { orderPanel } from './dborders.js';
import { loadAdmin, bindAdmin } from './admin.js';
import { loadChat, bindChat } from './chat.js';
import { loadDashboard } from './dashboard.js';

async function session() {
  const s = await fetch('/api/session', { credentials: 'same-origin' }).then(r => r.json());
  state.user = s.authenticated ? s.username : null; state.permissions = s.permissions || []; state.setup = s.needs_setup;
  $('#auth').hidden = s.authenticated; $('#logout').hidden = !s.authenticated;
  $('#auth-title').textContent = s.needs_setup ? 'Set up POPPYS AI' : 'Welcome back';
  $('#auth-text').textContent = s.needs_setup ? 'Choose the one login everyone in the team will use.' : 'One login for everyone in the team.';
  $('#auth-submit').textContent = s.needs_setup ? 'Create the login →' : 'Sign in →';
  $('#auth-pass').autocomplete = s.needs_setup ? 'new-password' : 'current-password';
  $('#auth-pass').minLength = s.needs_setup ? 12 : 1;   // new passwords: 12+ (the server also refuses common ones)
  $('#username').textContent = s.username || '—'; $('#avatar').textContent = (s.username || 'P')[0].toUpperCase();
  return s.authenticated;
}

async function status() {
  try {
    const h = await api('/api/health'); state.health = h;
    const db = h.database; const o = $('#oracle-status');
    o.className = `status ${db.connected ? 'ok' : 'off'}`;
    o.querySelector('span').textContent = db.connected ? 'Oracle connected' : 'Oracle offline';
    o.querySelector('small').textContent = db.connected ? 'saving enabled' : 'saving paused';
    // The fix for an administrator stays in the tooltip, not on everyone's screen.
    o.title = db.connected ? `Connected to ${db.owner} (${db.dsn || ''})` : `${db.error || 'Oracle is not reachable.'} Start the Oracle database (Docker Desktop → oracle26ai) and refresh.`;
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

const VIEWS = ['dashboard', 'workspace', 'explorer', 'chat', 'admin'];

function go(next, push = true) {
  $$('.nav [data-view]').forEach(t => t.classList.toggle('active', t.dataset.view === next));
  $$('.view').forEach(v => { v.hidden = v.id !== `view-${next}`; });
  closeDrawer();
  setRail(null);
  if (next === 'dashboard') loadDashboard();
  if (next === 'explorer') loadExplorer();
  if (next === 'admin') loadAdmin();
  if (next === 'chat') { loadChat(); setTimeout(() => $('#chat-input').focus(), 50); }
  if (next === 'workspace' && !state.draft) ws.recent();
  if (location.hash.split('/')[0] !== '#' + next) (push ? history_push : history_replace)(next);
}
// Each screen is a browser history entry, so Back and Forward move between screens instead of leaving the app.
function history_replace(v) { try { window.history.replaceState(null, '', '#' + v); } catch (_) { /* file previews */ } }
function history_push(v) { try { window.history.pushState(null, '', '#' + v); } catch (_) { /* file previews */ } }
window.addEventListener('popstate', () => {
  if (!state.user) return;
  const [view, sub] = location.hash.slice(1).split('/');
  if (!VIEWS.includes(view)) return;
  go(view, false);
  if (view === 'workspace') ws.onHistory(sub);
});

function showPassword(visible) {
  $('#auth-pass').type = visible ? 'text' : 'password';
  const b = $('#auth-pass-show'); b.textContent = visible ? 'Hide' : 'Show';
  b.setAttribute('aria-pressed', String(visible)); b.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
}

function bind() {
  onUnauthorized(() => session());
  // Icons-only menu: each item keeps its name as a tooltip; the choice is remembered on this computer.
  $$('.nav [data-view]').forEach(b => { b.title = b.textContent.trim(); });
  $('#rail-toggle').addEventListener('click', () => setRail(!$('.shell').classList.contains('rail'), { remember: true }));
  setRail(null);
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
  $('#drawer-close').addEventListener('click', closeDrawer); $('#scrim').addEventListener('click', closeDrawer);
  $('#modal-close').addEventListener('click', () => $('#modal').close());
  $('#modal').addEventListener('click', e => { if (e.target === $('#modal')) $('#modal').close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#drawer').hidden) closeDrawer(); });
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-open-draft],[data-goto],[data-order-panel]'); if (!b || state.busy) return;
    if (b.dataset.goto) return go(b.dataset.goto);
    if (b.dataset.openDraft) { go('workspace'); return ws.openDraft(b.dataset.openDraft); }
    if (b.dataset.orderPanel) return orderPanel(b.dataset.orderPanel);
  });
  document.addEventListener('poppys:tables', () => { go('explorer'); showTables(); });
  window.addEventListener('beforeunload', e => { if (state.dirty) { e.preventDefault(); e.returnValue = ''; } });
  keyboardReach();
  ws.bindWorkspace(); bindExplorer(); bindAdmin(); bindChat();
}

// Rows and headings that act on a click (open a record, sort a column, pick a sheet cell, expand a group) are reachable and
// usable with the keyboard too: focusable, announced as buttons, and Enter / Space do what a click does.
const ACTIONABLE = 'tr.clickable, th[data-sort], tr[data-open], tr.group-row, #sheet-grid td[data-r]';
function keyboardReach() {
  const mark = root => root.querySelectorAll?.(ACTIONABLE).forEach(el => {
    if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
    if (!el.hasAttribute('role') && el.tagName !== 'TH') el.setAttribute('role', 'button');
  });
  mark(document);
  new MutationObserver(list => list.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && (n.matches(ACTIONABLE) ? mark(n.parentNode) : mark(n))))).observe(document.body, { childList: true, subtree: true });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const el = e.target.closest?.(ACTIONABLE);
    if (!el || e.target !== el) return;
    e.preventDefault(); el.click();
  });
}

async function start() {
  await Promise.allSettled([status(), lists()]);
  const initial = (location.hash || '').slice(1);
  const first = initial.split('/')[0];
  go(VIEWS.includes(first) ? first : 'dashboard', false);
  clearInterval(start.timer); start.timer = setInterval(async () => { await status(); if (!$('#view-dashboard').hidden) loadDashboard(); }, 30000);
}

bind();
(async () => {
  try { if (await session()) await start(); }
  catch (e) { notify(`Unable to reach the POPPYS AI server: ${e.message}`, 'error'); }
})();
