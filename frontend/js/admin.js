// Setup: database mapping verification, layout templates, users & roles, audit, settings.
import { $, $$, esc, show, fmt, can, notify, busy, confirmDialog } from './util.js';
import { api, id } from './api.js';

let section = 'mapping';

export function bindAdmin() {
  $$('.subtab').forEach(b => b.addEventListener('click', () => { section = b.dataset.admin; loadAdmin(); }));
}

export async function loadAdmin() {
  $$('.subtab').forEach(b => { b.classList.toggle('active', b.dataset.admin === section); b.hidden = b.dataset.permission && !can(b.dataset.permission); });
  const body = $('#admin-body'); body.innerHTML = '';
  await busy('Loading…', async () => ({ mapping, templates, users, audit, settings })[section](body));
}

function toggle(attrs, status, enabled) {
  return `<span class="status-toggle">${['verified', 'proposed'].map(s => `<button ${attrs} data-status="${s}" class="${status === s ? 'on ' + s : ''}" ${enabled ? '' : 'disabled'}>${s === 'verified' ? 'Verified' : 'Proposed'}</button>`).join('')}</span>`;
}

function evidence(c) {
  const d = c.discovery || {};
  const parts = [];
  if (d.fill_pct != null) parts.push(`<span>filled in ${d.fill_pct}% of ${fmt(d.rows, 0)} rows</span>`);
  if (d.samples?.length) parts.push(`<span>e.g. ${esc(d.samples.slice(0, 3).map(show).join(' | '))}</span>`);
  if (d.resolves_in) parts.push(`<span>${d.resolves_in.match_pct ?? '—'}% resolve in ${esc(d.resolves_in.table)}</span>`);
  if (d.link) parts.push(`<span>${d.link.match_pct ?? '—'}% link to ${esc(d.link.parent)}</span>`);
  if (d.foreign_key) parts.push(`<span>FK → ${esc(d.foreign_key)}</span>`);
  return parts.join('') || '<span class="muted">—</span>';
}

async function mapping(body) {
  const m = await api('/api/mapping');
  const edit = can('mapping');
  body.innerHTML = `<div class="notice ${m.live ? '' : 'warning'}">${m.live ? 'Column types below are read live from Oracle.' : 'Oracle is not connected — showing the saved dictionary snapshot.'}
    ${m.discovery.available ? ` Discovery evidence from ${esc(new Date(m.discovery.generated_at).toLocaleString())}.` : ' No discovery evidence yet: run <code>python scripts/discover_schema.py</code> on this computer.'}
    POPPYS AI writes <b>only verified columns of verified tables</b>. Keys and parent links are fixed.</div>
    ${m.tables.map(t => `<section class="card map-table"><div class="card-head"><div><h2>${esc(t.business)}</h2><span class="muted"><code>${esc(t.table)}</code> · ${esc(t.screen || '')}${t.parent ? ` · child of ${esc(t.parent)}` : ''}</span></div>
      <span>${t.visible ? '<span class="pill ok">visible</span>' : '<span class="pill err">not visible</span>'} ${t.insertable === null ? '' : t.insertable ? '<span class="pill ok">insert granted</span>' : '<span class="pill warn">no insert grant</span>'}
      ${toggle(`data-map-table="${esc(t.table)}"`, t.status, edit)}</span></div>
      <div class="table-wrap" style="border:0;border-radius:0"><table><thead><tr><th>Oracle column</th><th>Business meaning</th><th>Filled from</th><th>Type</th><th>Evidence</th><th>Status</th></tr></thead><tbody>
      ${t.columns.map(c => `<tr><td><code>${esc(c.column)}</code>${c.exists ? '' : ' <span class="pill err">missing</span>'}</td><td>${esc(c.label)}${c.evidence ? `<div class="evidence">${esc(c.evidence)}</div>` : ''}</td>
        <td><code>${esc(c.source)}</code>${c.master ? `<div class="evidence">master ${esc(c.master)} ${c.master_available ? '' : '<span class="pill warn">master not available</span>'}</div>` : ''}</td>
        <td><code>${esc(c.type || '')}</code>${c.nullable === 'N' ? ' <span class="pill">required</span>' : ''}</td><td class="evidence">${evidence(c)}</td>
        <td>${c.fixed ? '<span class="pill ok">fixed</span>' : toggle(`data-map-table="${esc(t.table)}" data-map-col="${esc(c.column)}"`, c.status, edit)}</td></tr>`).join('')}
      </tbody></table></div></section>`).join('')}`;
  body.onclick = async e => {
    const b = e.target.closest('[data-status]'); if (!b || b.classList.contains('on')) return;
    const table = b.dataset.mapTable, column = b.dataset.mapCol, status = b.dataset.status;
    if (status === 'verified' && !await confirmDialog(`Verify ${table}${column ? '.' + column : ''}?`, `<p>From now on POPPYS AI may write ${column ? 'this column' : 'this table'} on approved imports. Confirm that the evidence shows this is the correct business meaning in Axpert.</p>`, 'Verify')) return;
    await busy('Saving…', async () => { await api('/api/mapping/status', { method: 'POST', body: { table, column: column || null, status } }); notify(`${table}${column ? '.' + column : ''} is now ${status}. Prepare previews again to use it.`, 'success'); await mapping(body); });
  };
}

async function templates(body) {
  const list = await api('/api/templates');
  body.innerHTML = `<section class="card"><div class="card-head"><h2>Saved layout templates</h2><span class="muted">Created when someone maps a new buyer layout once</span></div>
    ${list.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Created</th><th>Used</th><th>Recognises</th><th>Active</th></tr></thead><tbody>
    ${list.map(t => `<tr><td><strong>${esc(t.name)}</strong></td><td>${esc(t.created_by)} · ${esc(new Date(t.created_at).toLocaleDateString())}</td><td>${t.uses} files</td>
      <td class="evidence">${esc(t.labels.slice(0, 6).map(l => l.split(':').slice(2).join(':')).join(', '))}…</td>
      <td>${can('templates') ? `<button class="${t.active ? 'secondary' : 'ghost'} small" data-tpl="${esc(t.id)}" data-active="${!t.active}">${t.active ? 'Active' : 'Disabled'}</button>` : (t.active ? 'Active' : 'Disabled')}</td></tr>`).join('')}</tbody></table></div>`
    : '<p class="empty">No templates yet. Built-in layouts (merch BOM, buyer contract, simple tables) are recognised automatically.</p>'}</section>`;
  body.onclick = async e => { const b = e.target.closest('[data-tpl]'); if (!b) return; await busy('Saving…', async () => { await api(`/api/templates/${id(b.dataset.tpl)}/active`, { method: 'POST', body: { active: b.dataset.active === 'true' } }); await templates(body); }); };
}

async function users(body) {
  const list = await api('/api/users');
  const roles = ['operator', 'approver', 'admin', 'viewer'];
  body.innerHTML = `<section class="card"><div class="card-head"><h2>People</h2><span class="muted">Operators prepare entries · Approvers save to Oracle · Admins manage setup</span></div>
    <div class="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
    ${list.map(u => `<tr><td><strong>${esc(u.username)}</strong></td><td><select data-role="${esc(u.username)}">${roles.map(r => `<option ${r === u.role ? 'selected' : ''}>${r}</option>`).join('')}</select></td>
      <td>${u.active ? '<span class="pill ok">active</span>' : '<span class="pill">disabled</span>'}</td>
      <td><button class="ghost small" data-active-user="${esc(u.username)}" data-active="${!u.active}">${u.active ? 'Disable' : 'Enable'}</button> <button class="ghost small" data-reset="${esc(u.username)}">Reset password</button></td></tr>`).join('')}
    </tbody></table></div>
    <h3 style="margin:18px 0 8px">Add a person</h3>
    <form id="add-user" class="form-row"><label>Username<input name="username" required minlength="2" maxlength="50" pattern="[A-Za-z0-9._@\-]+"></label>
      <label>Temporary password<input name="password" type="password" required minlength="10" autocomplete="new-password"></label>
      <label>Role<select name="role">${roles.map(r => `<option>${r}</option>`).join('')}</select></label><button class="primary">Add</button></form></section>`;
  $('#add-user').onsubmit = e => { e.preventDefault(); const f = new FormData(e.target); busy('Adding…', async () => { await api('/api/users', { method: 'POST', body: Object.fromEntries(f) }); notify('Person added.', 'success'); await users(body); }); };
  body.onchange = e => { const s = e.target.closest('[data-role]'); if (s) busy('Saving…', async () => { await api(`/api/users/${id(s.dataset.role)}`, { method: 'PATCH', body: { role: s.value } }); notify('Role updated.', 'success'); await users(body); }); };
  body.onclick = e => {
    const a = e.target.closest('[data-active-user]'); const r = e.target.closest('[data-reset]');
    if (a) busy('Saving…', async () => { await api(`/api/users/${id(a.dataset.activeUser)}`, { method: 'PATCH', body: { active: a.dataset.active === 'true' } }); await users(body); });
    if (r) { const pw = prompt(`New password for ${r.dataset.reset} (10+ characters)`); if (pw) busy('Saving…', async () => { await api(`/api/users/${id(r.dataset.reset)}`, { method: 'PATCH', body: { password: pw } }); notify('Password reset. Their sessions were signed out.', 'success'); }); }
  };
}

const ACTIONS = { upload: 'Uploaded a file', edit: 'Corrected values', preview: 'Prepared the preview', approved_saved: 'Approved & saved to Oracle', approve_failed: 'Save failed (rolled back)',
  mapping_applied: 'Applied a layout', template_saved: 'Saved a layout template', mapping_status: 'Changed column mapping', login: 'Signed in', login_failed: 'Failed sign-in',
  user_created: 'Added a person', user_changed: 'Changed a person', settings_changed: 'Changed settings', setup: 'Created the workspace', template_active: 'Changed a template' };

async function audit(body) {
  const list = await api('/api/audit');
  body.innerHTML = `<section class="card"><div class="card-head"><h2>Audit history</h2><span class="muted">Latest 300 events · stored locally; Oracle saves are also logged in PP_IMPORT_LOG</span></div>
    <div class="table-wrap" style="max-height:70vh"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead><tbody>
    ${list.map(a => `<tr><td style="white-space:nowrap">${esc(new Date(a.at).toLocaleString())}</td><td>${esc(a.actor)}</td><td>${esc(ACTIONS[a.action] || a.action)}</td>
      <td class="evidence">${a.detail.changes ? a.detail.changes.slice(0, 6).map(c => `<div><code>${esc(c.field)}</code>: ${esc(show(c.before))} → <b>${esc(show(c.after))}</b></div>`).join('') + (a.detail.changes.length > 6 ? `<div>…${a.detail.changes.length - 6} more</div>` : '')
        : esc(Object.entries(a.detail).filter(([, v]) => v != null).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v).slice(0, 120) : v}`).join(' · '))}</td></tr>`).join('')}
    </tbody></table></div></section>`;
}

async function settings(body) {
  const s = await api('/api/settings');
  body.innerHTML = `<form class="card form-card" id="settings-form"><h2>Workspace settings</h2>
    <label>Default exporter (pre-filled on every order)<input name="company_exporter" value="${esc(s.company_exporter)}" maxlength="100"></label>
    <label>Default sample type<input name="default_sample_type" value="${esc(s.default_sample_type)}" maxlength="30"></label>
    <label class="check"><input type="checkbox" name="allow_self_approval" ${s.allow_self_approval ? 'checked' : ''}> Allow the person who uploaded a file to approve it (turn off for a four-eyes rule)</label>
    <button class="primary">Save settings</button></form>`;
  $('#settings-form').onsubmit = e => { e.preventDefault(); const f = new FormData(e.target);
    busy('Saving…', async () => { await api('/api/settings', { method: 'PUT', body: { company_exporter: f.get('company_exporter'), default_sample_type: f.get('default_sample_type'), allow_self_approval: f.get('allow_self_approval') === 'on' } }); notify('Settings saved.', 'success'); }); };
}
