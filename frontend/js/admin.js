// Setup: write mapping, data dictionary (business meaning + relationships), templates, users, audit, settings.
import { $, $$, esc, show, fmt, can, notify, busy, confirmDialog } from './util.js';
import { api, id } from './api.js';

let section = 'mapping';

export function bindAdmin() {
  $$('.subnav [data-admin]').forEach(b => b.addEventListener('click', () => { section = b.dataset.admin; loadAdmin(); }));
}

export async function loadAdmin() {
  $$('.subnav [data-admin]').forEach(b => { b.classList.toggle('active', b.dataset.admin === section); b.hidden = b.dataset.permission && !can(b.dataset.permission); });
  const body = $('#admin-body'); body.innerHTML = '';
  await busy('Loading…', async () => ({ mapping, dictionary, templates, users, audit, settings })[section](body));
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
  body.innerHTML = `<div class="banner ${m.live ? 'info' : 'warning'}"><span class="b-ic">i</span><div>${m.live ? 'Column types below are read live from Oracle.' : 'Oracle is not connected — showing the saved dictionary snapshot.'}
    ${m.discovery.available ? ` Discovery evidence from ${esc(new Date(m.discovery.generated_at).toLocaleString())}.` : ' No discovery evidence yet: run <code>python scripts/discover_schema.py</code> on this computer.'}
    POPPYS AI writes <b>only verified columns of verified tables</b>. Keys and parent links are fixed.</div></div>
    ${m.tables.map(t => `<section class="card" style="margin-bottom:14px;overflow:hidden"><div class="card-head"><div><h2>${esc(t.business)}</h2><span class="muted small-text"><code>${esc(t.table)}</code> · ${esc(t.screen || '')}${t.parent ? ` · child of ${esc(t.parent)}` : ''}</span></div>
      <span>${t.visible ? '<span class="pill ok">visible</span>' : '<span class="pill err">not visible</span>'} ${t.insertable === null ? '' : t.insertable ? '<span class="pill ok">insert granted</span>' : '<span class="pill warn">no insert grant</span>'}
      ${toggle(`data-map-table="${esc(t.table)}"`, t.status, edit)}</span></div>
      <div class="tbl-wrap flush"><table><thead><tr><th>Oracle column</th><th>Business meaning</th><th>Filled from</th><th>Type</th><th>Evidence</th><th>Status</th></tr></thead><tbody>
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
  body.innerHTML = `<section class="card"><div class="card-head"><div><h2>Saved layout templates</h2><div class="sub">Created when someone maps a new buyer layout once</div></div></div>
    ${list.length ? `<div class="tbl-wrap"><table><thead><tr><th>Name</th><th>Created</th><th>Used</th><th>Recognises</th><th>Active</th></tr></thead><tbody>
    ${list.map(t => `<tr><td><strong>${esc(t.name)}</strong></td><td>${esc(t.created_by)} · ${esc(new Date(t.created_at).toLocaleDateString())}</td><td>${t.uses} files</td>
      <td class="evidence">${esc(t.labels.slice(0, 6).map(l => l.split(':').slice(2).join(':')).join(', '))}…</td>
      <td>${can('templates') ? `<button class="${t.active ? 'secondary' : 'ghost'} small" data-tpl="${esc(t.id)}" data-active="${!t.active}">${t.active ? 'Active' : 'Disabled'}</button>` : (t.active ? 'Active' : 'Disabled')}</td></tr>`).join('')}</tbody></table></div>`
    : '<p class="empty">No templates yet. Built-in layouts (merch BOM, buyer contract, simple tables) are recognised automatically.</p>'}</section>`;
  body.onclick = async e => { const b = e.target.closest('[data-tpl]'); if (!b) return; await busy('Saving…', async () => { await api(`/api/templates/${id(b.dataset.tpl)}/active`, { method: 'POST', body: { active: b.dataset.active === 'true' } }); await templates(body); }); };
}

async function users(body) {
  const list = await api('/api/users');
  const roles = ['operator', 'approver', 'admin', 'viewer'];
  body.innerHTML = `<section class="card"><div class="card-head"><div><h2>People</h2><div class="sub">Operators prepare entries · Approvers save to Oracle · Admins manage setup</div></div></div>
    <div class="tbl-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
    ${list.map(u => `<tr><td><strong>${esc(u.username)}</strong></td><td><select data-role="${esc(u.username)}">${roles.map(r => `<option ${r === u.role ? 'selected' : ''}>${r}</option>`).join('')}</select></td>
      <td>${u.active ? '<span class="pill ok">active</span>' : '<span class="pill">disabled</span>'}</td>
      <td><button class="ghost small" data-active-user="${esc(u.username)}" data-active="${!u.active}">${u.active ? 'Disable' : 'Enable'}</button> <button class="ghost small" data-reset="${esc(u.username)}">Reset password</button></td></tr>`).join('')}
    </tbody></table></div>
    <div class="card-body"><h3 style="margin-bottom:8px">Add a person</h3>
    <form id="add-user" class="form-row"><label>Username<input name="username" required minlength="2" maxlength="50" pattern="[A-Za-z0-9._@\-]+"></label>
      <label>Temporary password<input name="password" type="password" required minlength="10" autocomplete="new-password"></label>
      <label>Role<select name="role">${roles.map(r => `<option>${r}</option>`).join('')}</select></label><button class="primary">Add</button></form></div></section>`;
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
  user_created: 'Added a person', user_changed: 'Changed a person', settings_changed: 'Changed settings', setup: 'Created the workspace', template_active: 'Changed a template',
  knowledge_edit: 'Edited the data dictionary', knowledge_refresh: 'Refreshed the data dictionary', chat_feedback: 'Rated an AI answer', example_deleted: 'Removed a confirmed answer' };

async function audit(body) {
  const list = await api('/api/audit');
  body.innerHTML = `<section class="card"><div class="card-head"><div><h2>Audit history</h2><div class="sub">Latest 300 events · stored locally; Oracle saves are also logged in PP_IMPORT_LOG</div></div></div>
    <div class="tbl-wrap" style="max-height:70vh"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead><tbody>
    ${list.map(a => `<tr><td style="white-space:nowrap">${esc(new Date(a.at).toLocaleString())}</td><td>${esc(a.actor)}</td><td>${esc(ACTIONS[a.action] || a.action)}</td>
      <td class="evidence">${a.detail.changes ? a.detail.changes.slice(0, 6).map(c => `<div><code>${esc(c.field)}</code>: ${esc(show(c.before))} → <b>${esc(show(c.after))}</b></div>`).join('') + (a.detail.changes.length > 6 ? `<div>…${a.detail.changes.length - 6} more</div>` : '')
        : esc(Object.entries(a.detail).filter(([, v]) => v != null).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v).slice(0, 120) : v}`).join(' · '))}</td></tr>`).join('')}
    </tbody></table></div></section>`;
}

async function settings(body) {
  const s = await api('/api/settings');
  body.innerHTML = `<form class="card pad form-card" id="settings-form"><h2>Workspace settings</h2>
    <label>Default exporter (pre-filled on every order)<input name="company_exporter" value="${esc(s.company_exporter)}" maxlength="100"></label>
    <label>Default sample type<input name="default_sample_type" value="${esc(s.default_sample_type)}" maxlength="30"></label>
    <label class="check"><input type="checkbox" name="allow_self_approval" ${s.allow_self_approval ? 'checked' : ''}> Allow the person who uploaded a file to approve it (turn off for a four-eyes rule)</label>
    <button class="primary">Save settings</button></form>`;
  $('#settings-form').onsubmit = e => { e.preventDefault(); const f = new FormData(e.target);
    busy('Saving…', async () => { await api('/api/settings', { method: 'PUT', body: { company_exporter: f.get('company_exporter'), default_sample_type: f.get('default_sample_type'), allow_self_approval: f.get('allow_self_approval') === 'on' } }); notify('Settings saved.', 'success'); }); };
}


// ---------------- data dictionary: business meaning of tables/columns + relationship verification
let dictTable = null;

async function dictionary(body) {
  const [k, joins, examples] = await Promise.all([api('/api/knowledge'), api('/api/knowledge/joins'), api('/api/chat/examples')]);
  const s = k.summary; const edit = can('mapping');
  const st = s.statuses || {};
  body.innerHTML = `<div class="banner ${s.live ? 'info' : 'warning'}"><span class="b-ic">i</span><div>${s.live ? `Structure read from Oracle on ${esc(new Date(s.generated_at).toLocaleString())}.` : 'Oracle structure not read yet — using the saved snapshot.'}
      The Ask AI screen only uses these tables, business names and links. Better names and synonyms here = better answers.</div>
      ${edit ? '<button class="secondary small" id="kb-refresh" style="margin-left:auto">Refresh from Oracle</button>' : ''}</div>
    <div class="stat-row">${[['Tables', s.tables], ['Links confirmed', (st.verified || 0) + (st.declared || 0)], ['Links to review', (st.proposed || 0) + (st.inferred || 0)], ['Values indexed', fmt(k.values.reduce((n, v) => n + v.values, 0), 0)], ['Confirmed answers', examples.length]].map(([a, b]) => `<div class="card kpi"><small>${a}</small><strong>${b}</strong></div>`).join('')}</div>
    <section class="card" style="margin-bottom:14px"><div class="card-head"><div><h2>Relationships</h2><div class="sub">How tables link. Confirmed links are used freely; unconfirmed ones lower an answer’s confidence until verified.</div></div>
      <select id="rel-filter" style="width:220px"><option value="review">Needs review</option><option value="all">All links</option></select></div>
      <div class="tbl-wrap flush" style="max-height:420px" id="rels"></div></section>
    <section class="card" style="margin-bottom:14px"><div class="card-head"><div><h2>Tables &amp; business names</h2><div class="sub">Click a table to edit its business name, synonyms and column meanings.</div></div><input id="dict-q" placeholder="Search" style="width:220px"></div>
      <div style="display:grid;grid-template-columns:300px minmax(0,1fr)"><div id="dict-list" style="border-right:1px solid var(--line);max-height:560px;overflow:auto;padding:6px"></div><div id="dict-edit" style="padding:14px 16px"><p class="empty">Choose a table.</p></div></div></section>
    <section class="card"><div class="card-head"><div><h2>Confirmed answers</h2><div class="sub">Questions marked “correct” in Ask AI. Similar questions reuse the same plan with new values.</div></div></div>
      ${examples.length ? `<div class="tbl-wrap flush"><table><thead><tr><th>Question</th><th>Pattern</th><th>By</th><th>Used</th><th></th></tr></thead><tbody>${examples.map(e => `<tr><td>${esc(e.question)}</td><td class="mono">${esc(e.masked)}</td><td>${esc(e.actor)}</td><td>${e.uses}</td><td>${edit ? `<button class="ghost small" data-del-example="${e.id}">Remove</button>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">None yet.</p>'}</section>`;
  const drawRels = () => {
    const mode = $('#rel-filter').value;
    const list = joins.filter(j => mode === 'all' || ['proposed', 'inferred'].includes(j.status));
    $('#rels').innerHTML = list.length ? `<table><thead><tr><th>From (many)</th><th>To (one)</th><th>Join on</th><th>Meaning</th><th>Source</th><th>Status</th></tr></thead><tbody>
      ${list.map(j => `<tr><td class="mono">${esc(j.child)}</td><td class="mono">${esc(j.parent)}</td><td class="mono small-text">${esc(j.pairs.map(([a, b]) => `${a} = ${b}`).join(' AND '))}</td><td>${esc(j.label)}</td><td class="muted">${esc(j.source)}</td>
        <td><span class="status-toggle">${['verified', 'proposed', 'rejected'].map(x => `<button data-join="${esc(j.id)}" data-status="${x}" class="${(j.status === x || (x === 'proposed' && j.status === 'inferred')) ? 'on ' + x : ''}" ${edit ? '' : 'disabled'}>${x === 'verified' ? 'Confirm' : x === 'proposed' ? 'Unsure' : 'Wrong'}</button>`).join('')}</span></td></tr>`).join('')}</tbody></table>` : '<p class="empty">Every link is confirmed.</p>';
  };
  const drawList = () => {
    const q = $('#dict-q').value.trim().toUpperCase();
    $('#dict-list').innerHTML = k.tables.filter(t => !q || t.table.includes(q) || t.business.toUpperCase().includes(q)).sort((a, b) => (b.status === 'verified') - (a.status === 'verified') || a.table.localeCompare(b.table))
      .map(t => `<button class="tl-item ${dictTable === t.table ? 'active' : ''}" data-dict="${esc(t.table)}"><b>${esc(t.business)}</b><span class="n">${t.status === 'verified' ? '✓' : ''}</span><code>${esc(t.table)}</code></button>`).join('');
  };
  drawRels(); drawList();
  if (dictTable) openDict(dictTable, edit);
  $('#rel-filter').onchange = drawRels; $('#dict-q').oninput = drawList;
  $('#kb-refresh')?.addEventListener('click', () => busy('Reading the Oracle data dictionary and indexing values…', async () => {
    const r = await api('/api/knowledge/refresh', { method: 'POST' }); notify(`Read ${r.tables} tables · indexed ${fmt(r.values.values, 0)} values in ${r.values.columns} columns.`, 'success'); await dictionary(body); }));
  body.onclick = async e => {
    const j = e.target.closest('[data-join]');
    if (j && !j.classList.contains('on')) { await busy('Saving…', async () => { await api('/api/knowledge/objects', { method: 'PUT', body: { object: 'join:' + j.dataset.join, status: j.dataset.status } }); const x = joins.find(y => y.id === j.dataset.join); x.status = j.dataset.status; drawRels(); notify('Link updated.', 'success'); }); return; }
    const d = e.target.closest('[data-dict]'); if (d) { dictTable = d.dataset.dict; drawList(); return openDict(dictTable, edit); }
    const x = e.target.closest('[data-del-example]'); if (x) { await busy('Removing…', async () => { await api(`/api/chat/examples/${x.dataset.delExample}`, { method: 'DELETE' }); await dictionary(body); }); }
  };
}

async function openDict(table, edit) {
  const t = await api(`/api/knowledge/tables/${id(table)}`);
  const el = $('#dict-edit');
  el.innerHTML = `<form id="dict-form" class="form-card" style="max-width:none"><div class="row wrap"><h3>${esc(t.business)}</h3><code class="muted">${esc(t.name)}</code><span class="pill">${esc(t.kind)}</span>${t.rows != null ? `<span class="pill" title="From Oracle statistics; the Database screen shows exact counts">≈ ${fmt(t.rows, 0)} rows</span>` : ''}</div>
      <div class="grid2"><label>Business name<input name="business" value="${esc(t.business)}" maxlength="80" ${edit ? '' : 'disabled'}></label>
      <label>Synonyms (comma separated)<input name="synonyms" value="${esc(t.synonyms.join(', '))}" ${edit ? '' : 'disabled'}></label></div>
      <label>Description<input name="description" value="${esc(t.description)}" maxlength="500" ${edit ? '' : 'disabled'}></label>
      ${edit ? '<div><button class="primary small">Save table</button></div>' : ''}</form>
    <div class="tbl-wrap" style="margin-top:12px;max-height:420px"><table class="grid-input"><thead><tr><th>Column</th><th>Business name</th><th>Synonyms</th><th>Role</th><th></th></tr></thead><tbody>
      ${t.columns.map(c => `<tr data-col="${esc(c.name)}"><td class="mono">${esc(c.name)}<div class="muted small-text">${esc(c.type)}${c.references ? ` → ${esc(c.references)}` : ''}</div></td>
        <td><input data-k="business" value="${esc(c.business)}" maxlength="80" ${edit ? '' : 'disabled'}></td><td><input data-k="synonyms" value="${esc((c.synonyms || []).join(', '))}" ${edit ? '' : 'disabled'}></td>
        <td><select data-k="role" ${edit ? '' : 'disabled'}>${['label', 'measure', 'date', 'reference', 'attribute', 'key', 'system'].map(r => `<option ${r === c.role ? 'selected' : ''}>${r}</option>`).join('')}</select></td>
        <td>${edit ? '<button class="tiny secondary" data-save-col>Save</button>' : ''}</td></tr>`).join('')}</tbody></table></div>`;
  const list = v => v.split(',').map(x => x.trim()).filter(Boolean);
  $('#dict-form').onsubmit = e => { e.preventDefault(); const f = new FormData(e.target);
    busy('Saving…', async () => { await api('/api/knowledge/objects', { method: 'PUT', body: { object: t.name, business: f.get('business'), description: f.get('description'), synonyms: list(f.get('synonyms')) } }); notify('Saved. Ask AI uses the new names right away.', 'success'); }); };
  el.onclick = e => { const b = e.target.closest('[data-save-col]'); if (!b) return; const tr = b.closest('tr');
    busy('Saving…', async () => { await api('/api/knowledge/objects', { method: 'PUT', body: { object: `${t.name}.${tr.dataset.col}`, business: $('[data-k=business]', tr).value, synonyms: list($('[data-k=synonyms]', tr).value), role: $('[data-k=role]', tr).value } }); notify(`${tr.dataset.col} saved.`, 'success'); }); };
}
