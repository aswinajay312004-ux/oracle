'use strict';
/* Start-up: sign-in, global controls, routing. */
$('#auth-form').addEventListener('submit', async e => {
  e.preventDefault(); $('#auth-error').hidden = true; $('#auth-submit').disabled = true;
  try {
    await api(App.state.setup ? '/api/setup' : '/api/login', { method: 'POST', body: { username: $('#auth-user').value, password: $('#auth-pass').value } });
    $('#auth-pass').value = '';
    if (await showAuth()) { refreshHealth(); navigate(); }
  } catch (err) { $('#auth-error').textContent = err.message; $('#auth-error').hidden = false; }
  finally { $('#auth-submit').disabled = false; }
});
$('#logout').addEventListener('click', async () => { await api('/api/logout', { method: 'POST' }).catch(() => {}); $('#view').innerHTML = ''; showAuth(); });
$('#drawer-close').addEventListener('click', () => Drawer.close());
$('#drawer-backdrop').addEventListener('click', () => Drawer.close());
$('#drawer-tabs').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (b) Drawer.show(b.dataset.tab); });
$('#modal-close').addEventListener('click', () => Modal.close());
$('#modal-backdrop').addEventListener('click', () => Modal.close());
document.addEventListener('keydown', e => { if (e.key !== 'Escape') return; if (!$('#modal').hidden) Modal.close(); else if (!$('#drawer').hidden) Drawer.close(); });
window.addEventListener('hashchange', navigate);
paintIcons();
(async () => {
  try { if (await showAuth()) { refreshHealth(); await navigate(); } }
  catch (_) { toast('The POPPYS application is not reachable. Start it with start.ps1.', 'attention'); }
  setInterval(() => { if ($('#auth').hidden) refreshHealth(); }, 60000);
})();
