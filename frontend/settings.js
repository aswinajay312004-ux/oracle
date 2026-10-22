'use strict';
/* Setup: connection status, local AI, what POPPYS has learned, saved mappings, backups and the single admin account.
   Secrets are never shown. */
route('settings', async view => {
  setTitle('Setup', 'Connection, local AI, learned Excel labels, saved mappings, backups and admin account');
  const [s, learned, templates] = await Promise.all([api('/api/settings'), api('/api/learned').catch(() => []), api('/api/templates').catch(() => [])]);
  const maps = templates.filter(t => t.kind === 'entry');
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
    <section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('sparkle')}</span><h2>What POPPYS has learned</h2></div><span class="badge">${learned.length}</span></div>
      <div class="card-pad" style="padding-bottom:8px"><p class="muted">When you map an Excel cell yourself, the label beside it is remembered, so the next Excel with the same label is filled by itself.</p></div>
      ${learned.length ? `<div class="table-wrap" style="max-height:300px"><table class="data-table"><thead><tr><th>Oracle column</th><th>Excel label</th><th>Learned</th><th></th></tr></thead><tbody>
        ${learned.map((l, i) => `<tr><td><strong>${esc(l.table_name)}.${esc(l.column_name)}</strong></td><td>“${esc(l.label)}”</td><td>${when(l.created_at)}${l.created_by ? `<span class="sub">${esc(l.created_by)}</span>` : ''}</td>
          <td><button class="btn small" data-forget="${i}">Forget</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="faint card-pad" style="padding-top:0">Nothing learned yet.</p>'}</section>
    <section class="card"><div class="card-head"><div class="table-name"><span class="table-ico">${icon('flow')}</span><h2>Saved mappings</h2></div><span class="badge">${maps.length}</span></div>
      <div class="card-pad" style="padding-bottom:8px"><p class="muted">A saved mapping is reused for Excel files with the same layout. Only mappings that are on are used.</p></div>
      ${maps.length ? `<div class="table-wrap" style="max-height:300px"><table class="data-table"><thead><tr><th>Name</th><th>Version</th><th>Saved</th><th>Used</th></tr></thead><tbody>
        ${maps.map(t => `<tr><td><strong>${esc(t.name)}</strong>${t.buyer ? `<span class="sub">${esc(t.buyer)}</span>` : ''}</td><td>v${esc(t.version)}</td><td>${when(t.created_at)}</td>
          <td><label class="row" style="gap:6px"><input type="checkbox" data-tpl="${esc(t.id)}" ${t.active ? 'checked' : ''} aria-label="Use mapping ${esc(t.name)} v${esc(t.version)}">${t.active ? 'On' : 'Off'}</label></td></tr>`).join('')}</tbody></table></div>`
        : '<p class="faint card-pad" style="padding-top:0">No saved mappings yet. Save one from the mapping screen of an upload.</p>'}</section>
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
  $$('[data-forget]', view).forEach(b => b.onclick = () => { const l = learned[Number(b.dataset.forget)];
    busy('Forgetting…', async () => { await api('/api/learned/forget', { method: 'POST', body: { table: l.table_name, column: l.column_name, label: l.label } }); toast(`“${l.label}” is no longer matched to ${l.column_name}.`); navigate(); }); });
  $$('[data-tpl]', view).forEach(c => c.onchange = () => busy('Saving…', async () => { await api(`/api/templates/${c.dataset.tpl}/active`, { method: 'POST', body: { active: c.checked } }); toast(c.checked ? 'Mapping turned on.' : 'Mapping turned off.'); navigate(); }));
  $('#s-backup', view).onclick = () => busy('Creating and verifying the backup…', async () => { const r = await api('/api/settings/backup', { method: 'POST' }); toast(`Backup ${r.name} created and verified.`); navigate(); });
  $('#s-pass', view).onsubmit = e => { e.preventDefault(); busy('Changing password…', async () => { await api('/api/settings/password', { method: 'POST', body: { current: $('#s-cur', view).value, new: $('#s-new', view).value } }); e.target.reset(); toast('Password changed.'); }); };
});
