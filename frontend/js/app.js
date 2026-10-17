// App shell: authentication, navigation, status lights, saved orders and global actions.
import { $, $$, esc, state, can, notify, busy, closeDrawer, keepTabInside, confirmDialog, modal } from './util.js';
import { api, id, onUnauthorized, onPasswordRequired } from './api.js';
import * as ws from './workspace.js';
import { loadExplorer, bindExplorer, exploreOrder } from './explorer.js';
import { loadAdmin, bindAdmin } from './admin.js';
import { loadChat, bindChat, resetChat } from './chat.js';

const VIEWS = ['workspace', 'history', 'chat', 'explorer', 'admin'];
let view = null;

// Signed in and allowed to work: true. Signed in with a temporary password: the "Choose your own password" screen
// is shown instead and this returns false (every other request would answer 403 until it is changed).
async function session() {
  const s = await fetch('/api/session', { credentials: 'same-origin' }).then(r => r.json());
  const mustChange = !!(s.authenticated && s.password_change_required);
  state.user = s.authenticated ? s.username : null; state.permissions = s.permissions || []; state.setup = s.needs_setup;
  if (s.authenticated) state.lastUser = s.username;
  $('#auth').hidden = s.authenticated && !mustChange; $('#logout').hidden = !s.authenticated; $('#change-pw').hidden = !s.authenticated || mustChange;
  $('#auth-form').hidden = mustChange;
  $('#auth-title').textContent = s.needs_setup ? 'Set up POPPYS AI' : 'Welcome back';
  $('#auth-text').textContent = s.needs_setup ? 'Create the administrator account for this computer. You can add operators and approvers afterwards.' : 'Sign in to prepare and approve ERP entries.';
  $('#auth-submit').textContent = s.needs_setup ? 'Create administrator →' : 'Sign in →';
  $('#auth-pass').autocomplete = s.needs_setup ? 'new-password' : 'current-password';
  // a new administrator password needs 12+ characters; signing in accepts what an older account already has
  $('#auth-pass').minLength = s.needs_setup ? 12 : 1;
  $('#auth-pass').title = s.needs_setup ? 'At least 12 characters, not a common password, not your username' : '';
  $('#username').textContent = s.username || '—'; $('#role').textContent = s.role || ''; $('#avatar').textContent = (s.username || 'P')[0].toUpperCase();
  if (mustChange) passwordScreen(s.username); else $('#pw-box').hidden = true;
  return s.authenticated && !mustChange;
}

// ---------------------------------------------------------------- own password
function passwordForm(p, temporary) {
  return `<form id="${p}-form" class="pw-form stack">
    <label>${temporary ? 'Temporary password (the one you were given)' : 'Current password'}<input id="${p}-current" type="password" autocomplete="current-password" required maxlength="200"></label>
    <label>New password<input id="${p}-new" type="password" autocomplete="new-password" required minlength="12" maxlength="200" aria-describedby="${p}-rule"></label>
    <small id="${p}-rule" class="muted">At least 12 characters. Not a common password and not your username.</small>
    <label>New password again<input id="${p}-repeat" type="password" autocomplete="new-password" required minlength="12" maxlength="200"></label>
    <p id="${p}-error" class="field-error" role="alert" hidden></p>
    <button type="submit" class="primary${temporary ? ' big full' : ''}" id="${p}-submit">Save my new password</button></form>`;
}

async function changePassword(p) {
  const current = $(`#${p}-current`).value, next = $(`#${p}-new`).value, again = $(`#${p}-repeat`).value;
  const err = $(`#${p}-error`); err.hidden = true;
  const fail = (text, field) => { err.textContent = text; err.hidden = false; $(`#${p}-${field}`)?.focus(); return false; };
  if (next !== again) return fail('The two new passwords are not the same. Type the new password again.', 'repeat');
  if (next === current) return fail('Choose a new password that is different from the old one.', 'new');
  $(`#${p}-submit`).disabled = true;
  try { await api('/api/me/password', { method: 'POST', body: { current, new: next } }); return true; }
  catch (e) { return fail(e.message, /current|temporary|wrong|incorrect/i.test(e.message) ? 'current' : 'new'); }
  finally { $(`#${p}-submit`).disabled = false; }
}

function passwordScreen(username) {
  const box = $('#pw-box');
  if (!box.hidden && box.dataset.user === username) return;   // already open: keep what is typed
  box.dataset.user = username; box.hidden = false;
  box.innerHTML = `<span class="eyebrow">Your account · ${esc(username)}</span><h1 id="pw-title" tabindex="-1">Choose your own password</h1>
    <p class="muted">An administrator gave you a temporary password. Choose your own password now: after this, only you know it.</p>
    ${passwordForm('pw', true)}<button type="button" class="ghost full" id="pw-signout">Sign out</button>`;
  $('#pw-form').addEventListener('submit', async e => {
    e.preventDefault();
    if (!await changePassword('pw')) return;
    box.hidden = true; notify('Your password is saved. Use it from now on.', 'success');
    if (await session()) start();
  });
  $('#pw-signout').addEventListener('click', signOut);
  $('#pw-current').focus();
}

function openChangePassword() {
  const m = modal('<h2 id="modal-title">Change my password</h2>', `<p class="muted mb-12">You stay signed in here. Anywhere else you are signed in is signed out.</p>${passwordForm('cp', false)}`, '', { narrow: true });
  $('#cp-form').addEventListener('submit', async e => {
    e.preventDefault();
    if (!await changePassword('cp')) return;
    m.close(); notify('Your password is changed. You stay signed in here; anywhere else you were signed in is signed out.', 'success');
  });
  $('#cp-current').focus();
}

// ---------------------------------------------------------------- signing out / another person
// Nothing of the previous person may stay on screen: their draft, answers, receipts or an open drawer.
function forgetUser() {
  clearInterval(start.timer);
  closeDrawer(); if ($('#modal').open) $('#modal').close(); if ($('#confirm').open) $('#confirm').close();
  ws.clearWorkspace(); resetChat(); $('#toasts').innerHTML = '';
  $('#history').innerHTML = ''; $('#history-detail').innerHTML = ''; $('#history-detail').hidden = true; $('#admin-body').innerHTML = '';
  view = null; try { window.history.replaceState(null, '', '#workspace'); } catch (_) { /* file previews */ }
}

async function signOut() {
  if ((state.dirty || state.mappingDirty) && !await confirmDialog('Sign out and lose your corrections?', '<p>Corrections not yet checked are lost when you sign out. Click <b>Check &amp; continue</b> first to keep them.</p>', 'Sign out')) return;
  await busy('Signing out…', async () => {
    await api('/api/logout', { method: 'POST' });
    forgetUser(); state.lastUser = null;
    await session();
  });
}

async function status() {
  try {
    const h = await api('/api/health'); state.health = h;
    const db = h.database; const o = $('#oracle-status'); const a = $('#ai-status');
    o.className = `status ${db.connected ? 'ok' : 'off'}`;
    o.querySelector('span').textContent = db.connected ? 'Oracle connected' : 'Oracle offline';
    o.querySelector('small').textContent = db.connected ? `${db.owner} · ${db.tables} tables` : 'start Docker + oracle26ai';
    o.title = db.connected ? (db.dsn ? `Connected to ${db.dsn}` : 'Connected to the local Oracle database') : db.error || '';
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

// ---------------------------------------------------------------- navigation and browser history
// Every main view and every workspace step has its own history entry (#workspace/review …), so the browser's
// Back and Forward move between screens inside the app. Leaving the app with unchecked corrections still asks.
let popping = false;
function hashFor() {
  const st = view === 'workspace' ? ws.currentStage() : '';
  return `#${view}${['review', 'approve', 'saved'].includes(st) ? '/' + st : ''}`;
}
function record(push = true) {
  if (!view) return;
  const h = hashFor(); if (location.hash === h) return;
  try { window.history[push && !popping ? 'pushState' : 'replaceState'](null, '', h); } catch (_) { /* file previews */ }
}

function go(next, { push = true } = {}) {
  $$('.nav [data-view]').forEach(t => { const on = t.dataset.view === next; t.classList.toggle('active', on); if (on) t.setAttribute('aria-current', 'page'); else t.removeAttribute('aria-current'); });
  $$('.view').forEach(v => { v.hidden = v.id !== `view-${next}`; });
  closeDrawer();
  view = next;
  if (next === 'history') history();
  if (next === 'explorer') loadExplorer();
  if (next === 'admin') loadAdmin();
  if (next === 'chat') { loadChat(); setTimeout(() => $('#chat-input').focus(), 50); }
  if (next === 'workspace' && ws.currentStage() === 'upload') ws.recent();
  record(push);
}

async function onPop() {
  if (!state.user || !$('#auth').hidden) return;
  const [v, step] = (location.hash || '').slice(1).split('/');
  const target = VIEWS.includes(v) ? v : 'workspace';
  let stayed = false;
  popping = true;
  try {
    if (target !== view) go(target, { push: false });
    if (target === 'workspace') stayed = !(await ws.toStep(step || 'upload'));
  } finally { popping = false; }
  // the person chose to stay (unchecked corrections): give them their entry back; otherwise make the URL match
  if (stayed) { try { window.history.pushState(null, '', hashFor()); } catch (_) { /* file previews */ } } else record(false);
}

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

function bind() {
  onUnauthorized(() => session());
  onPasswordRequired(() => session());
  $('#auth-form').addEventListener('submit', async e => {
    e.preventDefault(); $('#auth-error').hidden = true; $('#auth-submit').disabled = true;
    const before = state.lastUser;
    try {
      await api(state.setup ? '/api/setup' : '/api/login', { method: 'POST', body: { username: $('#auth-user').value.trim(), password: $('#auth-pass').value } });
      $('#auth-pass').value = ''; $('#toasts').innerHTML = '';   // "Sign in to continue" and the like are answered now
      const ready = await session();
      // the same person after a time-out keeps their unsaved corrections; a different person sees none of them
      if (before && before !== state.user) forgetUser();
      if (ready) start();
    } catch (err) { $('#auth-error').textContent = err.message; $('#auth-error').hidden = false; }
    finally { $('#auth-submit').disabled = false; }
  });
  $('#logout').addEventListener('click', signOut);
  $('#change-pw').addEventListener('click', openChangePassword);
  $$('.nav [data-view]').forEach(t => t.addEventListener('click', () => go(t.dataset.view)));
  const input = $('#file-input'); const drop = $('#dropzone');
  input.addEventListener('change', e => ws.upload(e.target.files[0]));
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  ['dragenter', 'dragover'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { if (e.dataTransfer.files.length > 1) return notify('Drop one order file at a time.', 'warning'); ws.upload(e.dataTransfer.files[0]); });
  $('#go-review').addEventListener('click', ws.goReview);
  $('#drawer-close').addEventListener('click', closeDrawer); $('#scrim').addEventListener('click', closeDrawer);
  $('#drawer').addEventListener('keydown', e => keepTabInside(e, $('#drawer')));
  $('#modal-close').addEventListener('click', () => $('#modal').close());
  $('#modal').addEventListener('click', e => { if (e.target === $('#modal')) $('#modal').close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#drawer').hidden) closeDrawer(); });
  document.addEventListener('click', e => {
    // a lasting message (upload error, failed save, review notice): dismissed, and focus goes somewhere useful
    const x = e.target.closest('[data-dismiss]');
    if (x) {
      const box = x.closest('[data-dismissible]'); if (!box) return;
      box.hidden = true; if (box.id === 'review-notice') state.notice = null;
      (box.closest('.action-bar')?.querySelector('.primary:not([disabled])') || (box.id === 'upload-error' ? $('#dropzone') : $('#ws-title')))?.focus();
      return;
    }
    const v = e.target.closest('[data-goto-view]'); if (v && VIEWS.includes(v.dataset.gotoView)) go(v.dataset.gotoView);
  });
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-open-draft],[data-receipt],[data-explore-order],[data-act2]'); if (!b || state.busy) return;
    if (b.dataset.openDraft) { go('workspace'); return ws.openDraft(b.dataset.openDraft); }
    if (b.dataset.receipt) return receipt(b.dataset.receipt);
    if (b.dataset.exploreOrder) { closeDrawer(); go('explorer'); return exploreOrder(b.dataset.exploreOrder); }
    if (b.dataset.act2 === 'approve') return ws.approve();
    if (b.dataset.act2 === 'recheck') return ws.prepare();
    if (b.dataset.act2 === 'back-review') return ws.backToReview();
    if (b.dataset.act2 === 'another') return ws.resetWorkspace();
  });
  window.addEventListener('beforeunload', e => { if (state.dirty || state.mappingDirty) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('popstate', onPop);
  ws.onStage(name => { if (view === 'workspace' && name !== 'read') record(true); });
  ws.bindWorkspace(); bindExplorer(); bindAdmin(); bindChat();
}

async function start() {
  $$('[data-permission]').forEach(el => { el.hidden = !can(el.dataset.permission); });
  await Promise.allSettled([status(), lists()]);
  const [initial] = (location.hash || '').slice(1).split('/');
  go(VIEWS.includes(initial) ? initial : 'workspace', { push: false });
  clearInterval(start.timer); start.timer = setInterval(status, 30000);
}

bind();
(async () => {
  try { if (await session()) await start(); }
  catch (e) { notify(`Unable to reach the POPPYS AI server: ${e.message}`, 'error'); }
})();
