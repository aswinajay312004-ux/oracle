'use strict';
/* Start-up: sign-in, global controls, routing. */
$('#auth-form').addEventListener('submit', async e => {
  e.preventDefault(); $('#auth-error').hidden = true; $('#auth-submit').disabled = true;
  try {
    await api(App.state.setup ? '/api/setup' : '/api/login', { method: 'POST', body: { username: $('#auth-user').value, password: $('#auth-pass').value } });
    $('#auth-pass').value = '';
    if (await showAuth()) { refreshHealth(); refreshBell(); normalizeAiHash(); navigate(); }
  } catch (err) { $('#auth-error').textContent = err.message; $('#auth-error').hidden = false; }
  finally { $('#auth-submit').disabled = false; }
});
$('#auth-eye').addEventListener('click', () => { const p = $('#auth-pass'); p.type = p.type === 'password' ? 'text' : 'password'; $('#auth-eye').setAttribute('aria-label', p.type === 'password' ? 'Show password' : 'Hide password'); });
$('#drawer-close').addEventListener('click', () => Drawer.close());
$('#drawer-backdrop').addEventListener('click', () => Drawer.close());
$('#drawer-tabs').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (b) Drawer.show(b.dataset.tab); });
$('#modal-close').addEventListener('click', () => Modal.close());
$('#modal-backdrop').addEventListener('click', () => Modal.close());

/* '#/ai?q=question' opens the AI Assistant with that question. The router only knows '#/ai', so the question is
   handed over in window.ASK_PENDING and the address becomes '#/ai' before routing. */
function normalizeAiHash() {
  const m = location.hash.match(/^#\/ai\?q=(.*)$/);
  if (!m) return;
  try { window.ASK_PENDING = decodeURIComponent(m[1]); } catch (_) { window.ASK_PENDING = m[1]; }
  history.replaceState(null, '', '#/ai');
}

// account menu: Setup and sign out
$('#account').addEventListener('click', e => {
  e.stopPropagation(); $$('.menu').forEach(m => m.remove());
  const menu = document.createElement('div'); menu.className = 'menu'; menu.style.position = 'fixed';
  const r = $('#account').getBoundingClientRect(); menu.style.top = `${r.bottom + 6}px`; menu.style.left = `${Math.max(8, r.right - 214)}px`;
  menu.innerHTML = `<button type="button" data-a="setup">${icon('settings')}Setup</button><button type="button" data-a="out">${icon('back')}Sign out</button>`;
  document.body.appendChild(menu);
  $('[data-a=setup]', menu).onclick = () => { menu.remove(); go('#/settings'); };
  $('[data-a=out]', menu).onclick = async () => { menu.remove(); await api('/api/logout', { method: 'POST' }).catch(() => {}); $('#view').innerHTML = ''; showAuth(); };
});
// notifications: uploads that need a look or a fix
async function refreshBell() {
  try { const o = await api('/api/overview'); App.state.bell = o.pending; const n = o.pending.length; $('#bell-count').hidden = !n; $('#bell-count').textContent = n > 9 ? '9+' : n; } catch (_) { /* optional */ }
}
$('#bell').addEventListener('click', e => {
  e.stopPropagation(); $$('.menu').forEach(m => m.remove()); const items = App.state.bell || [];
  const menu = document.createElement('div'); menu.className = 'menu'; menu.style.position = 'fixed'; menu.style.minWidth = '300px';
  const r = $('#bell').getBoundingClientRect(); menu.style.top = `${r.bottom + 6}px`; menu.style.left = `${Math.max(8, r.right - 300)}px`;
  menu.innerHTML = items.length ? items.slice(0, 8).map((d, i) => `<button type="button" data-i="${i}">${icon(d.status === 'Needs a fix' ? 'alert' : 'clock')}<span><strong>${esc(d.filename)}</strong><small class="muted" style="display:block">${esc(d.status)}</small></span></button>`).join('')
    : `<div class="empty" style="padding:16px">No notifications. Everything uploaded is saved.</div>`;
  document.body.appendChild(menu); $$('[data-i]', menu).forEach(b => b.onclick = () => { menu.remove(); go(`#/new/${items[Number(b.dataset.i)].id}`); });
});
// global search: orders and uploads; Enter with nothing selected asks the AI Assistant
const gq = $('#global-q');
let searchTimer;
gq.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => runSearch(gq.value), 150); });
gq.addEventListener('keydown', e => {
  const box = $('#search-results');
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (box.hidden || !SEARCH.items.length) return; SEARCH.sel = (SEARCH.sel + (e.key === 'ArrowDown' ? 1 : -1) + SEARCH.items.length) % SEARCH.items.length; $$('button', box).forEach((b, i) => b.classList.toggle('sel', i === SEARCH.sel)); }
  if (e.key === 'Enter') { e.preventDefault(); const q = gq.value.trim(); if (!q) return; if (SEARCH.sel >= 0) pickSearch(SEARCH.sel); else { box.hidden = true; gq.value = ''; go('#/ai?q=' + encodeURIComponent(q)); } }
  if (e.key === 'Escape') { box.hidden = true; gq.blur(); }
});
gq.addEventListener('blur', () => setTimeout(() => { $('#search-results').hidden = true; }, 150));
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); gq.focus(); }
  if (e.key !== 'Escape') return; if (!$('#modal').hidden) Modal.close(); else if (!$('#drawer').hidden) Drawer.close();
});
window.addEventListener('hashchange', () => { normalizeAiHash(); navigate(); refreshBell(); });
paintIcons();
(async () => {
  try { if (await showAuth()) { refreshHealth(); refreshBell(); normalizeAiHash(); await navigate(); } }
  catch (_) { toast('The POPPYS application is not reachable. Start it with start.bat.', 'attention'); }
  setInterval(() => { if ($('#auth').hidden) { refreshHealth(); refreshBell(); } }, 60000);
})();
