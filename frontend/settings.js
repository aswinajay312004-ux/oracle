'use strict';
/* Settings: connection status, local AI, backups and the single admin account. Secrets are never shown. */
route('settings', async view => {
  setTitle('Settings', 'Connection, local AI, backups and admin account');
  const s = await api('/api/settings');
  view.innerHTML = `<div class="grid g2">
    <section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('database')}</span><h2>Oracle database</h2></div><span class="badge ${s.oracle.connected ? 'green' : 'amber'}">${s.oracle.connected ? 'Connected' : 'Not connected'}</span></div>
      <div class="table-wrap"><table class="kv"><tbody><tr><td>Address</td><td class="mono">${esc(s.oracle.dsn)}</td></tr><tr><td>Application account</td><td class="mono">${esc(s.oracle.user)}</td></tr>
        <tr><td>Order schema</td><td class="mono">${esc(s.oracle.owner)}</td></tr>${s.oracle.version ? `<tr><td>Oracle version</td><td>${esc(s.oracle.version)}</td></tr>` : ''}
        <tr><td>Table creation</td><td>${s.oracle.schema_changes ? '<span class="badge green">Enabled · needs your confirmation each time</span>' : '<span class="badge">Off</span>'}</td></tr></tbody></table></div>
      <p class="faint card-pad" style="padding-top:10px">The password is stored only in the local configuration file and is never shown here.</p></section>
    <section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('cpu')}</span><h2>Local AI</h2></div><span class="badge ${s.ai.available ? 'green' : ''}">${s.ai.available ? 'Running' : 'Not running'}</span></div>
      <div class="table-wrap"><table class="kv"><tbody><tr><td>Model</td><td class="mono">${esc(s.ai.model)}</td></tr><tr><td>Runs at</td><td>${esc(s.ai.endpoint)}</td></tr>
        ${s.ai.models.length ? `<tr><td>Installed models</td><td>${s.ai.models.map(m => `<span class="badge">${esc(m)}</span>`).join(' ')}</td></tr>` : ''}</tbody></table></div>
      <p class="muted card-pad" style="padding-top:10px">The AI only suggests mappings. It never writes to Oracle. Everything keeps working without it.</p></section>
    <section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('backup')}</span><h2>Backup</h2></div><button class="btn small primary" id="s-backup">Create backup now</button></div>
      <div class="card-pad"><p class="muted">Saves drafts, mapping templates, links and the activity log of this workspace. Uploaded files stay in the data folder. Oracle itself is backed up by your database administrator.</p>
      ${s.backups.length ? `<ul class="timeline" style="padding:8px 0 0">${s.backups.map(b => `<li><span class="dot"></span><span class="mono">${esc(b)}</span><time></time></li>`).join('')}</ul>` : '<p class="faint" style="margin-top:8px">No backups yet.</p>'}</div></section>
    <section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('key')}</span><h2>Admin account</h2></div><span class="badge">${esc(s.admin)}</span></div>
      <form class="card-pad stack" id="s-pass" style="gap:10px"><p class="muted">POPPYS AI has one administrator account.</p>
        <label class="field">Current password<input type="password" id="s-cur" autocomplete="current-password" required></label>
        <label class="field">New password<input type="password" id="s-new" autocomplete="new-password" minlength="10" required></label>
        <div><button class="btn" type="submit">Change password</button></div></form></section>
    <section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('shield')}</span><h2>Offline and private</h2></div></div>
      <div class="status-list"><div>${icon('check')}No internet connection is used.</div><div>${icon('check')}Uploaded Excel files stay on this computer, unchanged.</div><div>${icon('check')}Every Oracle change needs your approval and is recorded.</div>
        <div>${icon('check')}${plural(s.templates, 'mapping template')} · ${plural(s.relationships, 'admin-defined link')}</div></div></section>
  </div>`;
  $('#s-backup', view).onclick = () => busy('Creating and verifying the backup…', async () => { const r = await api('/api/settings/backup', { method: 'POST' }); toast(`Backup ${r.name} created and verified.`); navigate(); });
  $('#s-pass', view).onsubmit = e => { e.preventDefault(); busy('Changing password…', async () => { await api('/api/settings/password', { method: 'POST', body: { current: $('#s-cur', view).value, new: $('#s-new', view).value } }); e.target.reset(); toast('Password changed.'); }); };
});
