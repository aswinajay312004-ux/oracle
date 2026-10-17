// Setup: write mapping, data dictionary (business meaning + relationships), order fields, templates, users, audit, settings.
import { $, $$, esc, show, fmt, can, notify, busy, confirmDialog, state } from './util.js';
import { api, id, orderFields } from './api.js';

let section = 'mapping';

export function bindAdmin() {
  $$('.subnav [data-admin]').forEach(b => b.addEventListener('click', () => { section = b.dataset.admin; loadAdmin(); }));
}

export async function loadAdmin() {
  $$('.subnav [data-admin]').forEach(b => { b.classList.toggle('active', b.dataset.admin === section); b.hidden = b.dataset.permission && !can(b.dataset.permission); });
  const body = $('#admin-body'); body.innerHTML = ''; body.onclick = body.onchange = body.oninput = body.onkeydown = null;
  await busy('Loading…', async () => ({ mapping, dictionary, fields, templates, users, audit, settings })[section](body));
}

function toggle(attrs, status, enabled) {
  return `<span class="status-toggle">${['verified', 'proposed'].map(s => `<button ${attrs} data-status="${s}" class="${status === s ? 'on ' + s : ''}" ${enabled ? '' : 'disabled'}>${s === 'verified' ? 'Verified' : 'Proposed'}</button>`).join('')}</span>`;
}

// Big tables are profiled on a block sample (scripts/discover_schema.py: sampled_pct = % of the table read),
// so the figures say how many rows they were measured on instead of implying every row was read.
const sampled = (pct, rows) => {
  const n = rows * pct / 100;
  return fmt(n >= 10000 ? Math.round(n / 1000) * 1000 : Math.round(n), 0);
};
const sampleTip = (pct, what) => `title="${esc(`Measured on a block sample of about ${Number(Number(pct).toPrecision(2))}% of the table${what ? `; ${what}` : ''}. Discovery reads a sample of very large tables so it finishes quickly.`)}"`;
const DISTINCT_FROM = { statistics: 'distinct values from Oracle statistics', sample: 'distinct values counted in the sample' };

function evidence(c) {
  const d = c.discovery || {};
  const parts = [];
  if (d.fill_pct != null) parts.push(d.sampled_pct && d.rows
    ? `<span ${sampleTip(d.sampled_pct, DISTINCT_FROM[d.distinct_from])}>filled in ${d.fill_pct}% (sample of about ${sampled(d.sampled_pct, d.rows)} of ${fmt(d.rows, 0)} rows)</span>`
    : `<span>filled in ${d.fill_pct}% of ${fmt(d.rows, 0)} rows</span>`);
  if (d.samples?.length) parts.push(`<span>e.g. ${esc(d.samples.slice(0, 3).map(show).join(' | '))}</span>`);
  if (d.resolves_in) parts.push(`<span ${d.resolves_in.sampled_pct ? sampleTip(d.resolves_in.sampled_pct) : ''}>${d.resolves_in.match_pct ?? '—'}% resolve in ${esc(d.resolves_in.table)}${d.resolves_in.sampled_pct ? ` (in a sample of ${fmt(d.resolves_in.rows, 0)} filled rows)` : ''}</span>`);
  if (d.link) parts.push(`<span ${d.link.sampled_pct ? sampleTip(d.link.sampled_pct) : ''}>${d.link.match_pct ?? '—'}% link to ${esc(d.link.parent)}${d.link.sampled_pct ? ` (in a sample of ${fmt(d.link.rows, 0)} rows)` : ''}</span>`);
  if (d.foreign_key) parts.push(`<span>FK → ${esc(d.foreign_key)}</span>`);
  return parts.join('') || '<span class="muted">—</span>';
}

async function mapping(body) {
  const m = await api('/api/mapping');
  const edit = can('mapping');
  body.innerHTML = `<div class="banner ${m.live ? 'info' : 'warning'}"><span class="b-ic">i</span><div>${m.live ? 'Column types below are read live from Oracle.' : 'Oracle is not connected — showing the saved dictionary snapshot.'}
    ${m.discovery.available ? ` Discovery evidence from ${esc(new Date(m.discovery.generated_at).toLocaleString())}.` : ' No discovery evidence yet: run <code>python scripts/discover_schema.py</code> on this computer.'}
    POPPYS AI writes <b>only verified columns of verified tables</b>. Keys and parent links are fixed.</div></div>
    ${m.tables.map(t => `<section class="card map-card"><div class="card-head"><div><h2>${esc(t.business)}</h2><span class="muted small-text"><code>${esc(t.table)}</code> · ${esc(t.screen || '')}${t.parent ? ` · child of ${esc(t.parent)}` : ''}</span></div>
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

// ---------------- layout templates: one row per buyer layout, its versions folded underneath
const UNIT_TEXT = u => !u?.qty_unit ? '' : u.qty_unit === 'PCS' ? 'Pieces' : `Packs of ${u.pieces_per_unit || '?'}`;
const BASIS_TEXT = { order_demo: 'buyer order quantities', sample: 'sample quantities' };
let openFamilies = new Set();

function unitCell(t) {
  const u = UNIT_TEXT(t.unit); const b = BASIS_TEXT[t.quantity_basis];
  if (!u && !b) return '<span class="muted">Asked on review</span>';
  return `${u ? `<b>${esc(u)}</b>` : '<span class="muted">unit asked on review</span>'}${b ? `<div class="muted small-text">${esc(b)}</div>` : ''}`;
}
function recognises(t) {
  const labels = t.anchors ? Object.values(t.anchors).map(a => a?.label).filter(Boolean) : (t.labels || []).map(l => l.split(':').slice(2).join(':'));
  const list = [...new Set(labels)];
  return list.length ? `${esc(list.slice(0, 5).map(l => `“${l}”`).join(', '))}${list.length > 5 ? ` +${list.length - 5}` : ''}` : '—';
}
function activeSwitch(t, edit) {
  return `<button type="button" class="switch" role="switch" aria-checked="${t.active}" data-tpl="${esc(t.id)}" data-active="${!t.active}" ${edit ? '' : 'disabled'}
    aria-label="${esc(`${t.name} version ${t.version}: ${t.active ? 'in use' : 'not used'}`)}"><span class="knob" aria-hidden="true"></span><span>${t.active ? 'In use' : 'Off'}</span></button>`;
}

async function templates(body) {
  const list = await api('/api/templates'); const edit = can('templates');
  const families = new Map();
  list.forEach(t => { const k = t.family || t.id; if (!families.has(k)) families.set(k, []); families.get(k).push(t); });
  const rows = [...families.entries()].map(([fam, versions]) => {
    versions.sort((a, b) => (b.version || 1) - (a.version || 1));
    const cur = versions.find(v => v.active) || versions[0]; const older = versions.filter(v => v !== cur); const open = openFamilies.has(fam);
    const uses = versions.reduce((n, v) => n + (v.uses || 0), 0);
    return `<tr class="tpl-row"><td><strong>${esc(cur.name)}</strong>${cur.anchors ? '' : ' <span class="pill" title="Saved before label anchors existed: matched on the whole sheet">older format</span>'}
        <div class="muted small-text">Recognises ${recognises(cur)}</div></td>
      <td>${cur.buyer ? esc(cur.buyer) : '<span class="muted">—</span>'}</td>
      <td class="nowrap"><span class="pill brand">v${esc(cur.version || 1)}</span>${older.length ? ` <button type="button" class="link small-text" data-fam="${esc(fam)}" aria-expanded="${open}">${open ? 'Hide' : 'History'} (${older.length} earlier)</button>` : ''}</td>
      <td class="num">${fmt(cur.uses || 0, 0)}${older.length ? `<div class="muted small-text">${fmt(uses, 0)} across versions</div>` : ''}</td>
      <td>${unitCell(cur)}</td>
      <td class="nowrap">${esc(cur.created_by || '')}<div class="muted small-text">${cur.created_at ? esc(new Date(cur.created_at).toLocaleDateString()) : ''}</div></td>
      <td>${activeSwitch(cur, edit)}</td></tr>
      ${open ? older.map(v => `<tr class="tpl-old"><td><span class="tpl-indent">└</span> ${esc(v.name)}<div class="muted small-text">Recognises ${recognises(v)}</div></td><td>${esc(v.buyer || '—')}</td>
        <td><span class="pill">v${esc(v.version || 1)}</span></td><td class="num">${fmt(v.uses || 0, 0)}</td><td>${unitCell(v)}</td>
        <td class="nowrap">${esc(v.created_by || '')}<div class="muted small-text">${v.created_at ? esc(new Date(v.created_at).toLocaleDateString()) : ''}</div></td><td>${activeSwitch(v, edit)}</td></tr>`).join('') : ''}`;
  }).join('');
  body.innerHTML = `<section class="card"><div class="card-head"><div><h2>Layout templates</h2><div class="sub">One per buyer layout, saved when someone checks a new layout once. Saving the same layout again adds a version; only the version in use reads new files.</div></div></div>
    ${list.length ? `<div class="tbl-wrap flush"><table class="tpl-table"><thead><tr><th>Layout</th><th>Buyer</th><th>Version</th><th class="num">Files read</th><th>Quantity unit</th><th>Saved by</th><th>In use</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : '<p class="empty">No templates yet. Built-in layouts (merch BOM, buyer contract, simple tables) are recognised automatically.</p>'}</section>`;
  body.onclick = async e => {
    const f = e.target.closest('[data-fam]');
    if (f) { const k = f.dataset.fam; openFamilies.has(k) ? openFamilies.delete(k) : openFamilies.add(k); return templates(body); }
    const b = e.target.closest('[data-tpl]'); if (!b || b.disabled) return;
    const on = b.dataset.active === 'true';
    await busy('Saving…', async () => {
      await api(`/api/templates/${id(b.dataset.tpl)}/active`, { method: 'POST', body: { active: on } });
      notify(on ? 'This version now reads new files with this layout; the other versions are off.' : 'Template turned off. Files with this layout will ask for a layout check.', 'success');
      await templates(body);
    });
  };
}

// ---------------- order fields: buyer words (aliases) per field and which fields every order needs
const KIND = { text: 'Text', code: 'Code', date: 'Date', number: 'Number', size: 'Size', quantity: 'Quantity', colour: 'Colour', unit: 'Unit' };
const LOCK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';
const anorm = s => String(s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
let fieldEdits = {};

async function fields(body) {
  const list = await api('/api/fields'); const edit = can('mapping');
  state.fields = list; fieldEdits = {};
  const work = k => fieldEdits[k] ||= { required: list.find(f => f.key === k).required, aliases: [...list.find(f => f.key === k).aliases], error: '' };
  const dirty = k => { const f = list.find(x => x.key === k); const w = fieldEdits[k]; return !!w && (w.required !== f.required || w.aliases.join('\u0001') !== f.aliases.join('\u0001')); };
  const where = f => f.where === 'table' ? 'Size table' : f.column ? 'Header or table column' : 'Order header';
  const row = f => {
    const w = fieldEdits[f.key] || { required: f.required, aliases: f.aliases, error: '' };
    const changed = w.aliases.join('\u0001') !== (f.default_aliases || []).join('\u0001');
    return `<td><b>${esc(f.label)}</b><div class="muted small-text"><span class="pill">${esc(KIND[f.kind] || f.kind)}</span> ${esc(where(f))}</div></td>
      <td>${f.locked ? `<span class="lock-note" title="Axpert needs this field on every order">${LOCK} Always · needed by Axpert</span>`
        : `<label class="check req-toggle"><input type="checkbox" data-req="${esc(f.key)}" ${w.required ? 'checked' : ''} ${edit ? '' : 'disabled'}> Required</label>`}</td>
      <td><div class="alias-box ${w.error ? 'err' : ''}" data-alias-box="${esc(f.key)}">${w.aliases.map((a, i) => `<span class="alias-chip">${esc(a)}${edit ? `<button type="button" data-rm="${esc(f.key)}" data-i="${i}" aria-label="Remove “${esc(a)}” from ${esc(f.label)}">×</button>` : ''}</span>`).join('') || '<span class="muted small-text">No words yet</span>'}
          ${edit ? `<input class="alias-add" data-add="${esc(f.key)}" maxlength="60" placeholder="Add a word, press Enter" aria-label="Add a word buyers use for ${esc(f.label)}" ${w.error ? `aria-invalid="true" aria-describedby="fe-${esc(f.key)}"` : ''}>` : ''}</div>
        ${w.error ? `<div class="f-msg field-err" id="fe-${esc(f.key)}"><span>${esc(w.error)}</span></div>` : ''}
        <div class="muted small-text alias-meta">${f.changed_by ? `Changed by ${esc(f.changed_by)} · ${esc(new Date(f.changed_at).toLocaleString())}` : 'Default words'}${edit && changed ? ` · <button type="button" class="link small-text" data-defaults="${esc(f.key)}">Restore the default words</button>` : ''}</div></td>
      <td>${edit ? `<button type="button" class="primary small" data-save-field="${esc(f.key)}" ${dirty(f.key) ? '' : 'hidden'}>Save</button>` : ''}</td>`;
  };
  const draw = k => { const tr = $(`tr[data-frow="${CSS.escape(k)}"]`, body); tr.classList.toggle('row-err', !!fieldEdits[k]?.error); tr.innerHTML = row(list.find(f => f.key === k)); };
  body.innerHTML = `${edit ? '' : '<div class="banner info"><span class="b-ic">i</span><div>Only an administrator can change order fields.</div></div>'}
    <section class="card"><div class="card-head"><div><h2>Order fields</h2><div class="sub">The words buyers use for each field in their files, and which fields every order must have. Reading files, the layout check and the review screen all follow this list.</div></div></div>
    <div class="tbl-wrap flush"><table class="fields-table"><thead><tr><th class="w-220">Field</th><th class="w-200">Required</th><th>Words buyers use for it (aliases)</th><th class="w-80"></th></tr></thead>
    <tbody>${list.map(f => `<tr data-frow="${esc(f.key)}">${row(f)}</tr>`).join('')}</tbody></table></div></section>`;

  const add = (k, input) => {
    const text = input.value.split(/\s+/).join(' ').trim(); if (!text) return;
    const w = work(k); const n = anorm(text);
    if (!n) { w.error = `“${text}” needs letters or digits.`; return draw(k); }
    const other = list.find(f => f.key !== k && (fieldEdits[f.key]?.aliases || f.aliases).some(a => anorm(a) === n));
    if (other) { w.error = `“${text}” is already an alias of ${other.label}. An alias can belong to one field only.`; draw(k); const i = $(`[data-add="${CSS.escape(k)}"]`, body); if (i) { i.value = text; i.focus(); } return; }
    if (!w.aliases.some(a => anorm(a) === n)) w.aliases.push(text);
    w.error = ''; draw(k); $(`[data-add="${CSS.escape(k)}"]`, body)?.focus();
  };
  body.onkeydown = e => { const i = e.target.closest('[data-add]'); if (i && (e.key === 'Enter' || e.key === ',')) { e.preventDefault(); add(i.dataset.add, i); } };
  body.onchange = e => { const r = e.target.closest('[data-req]'); if (r) { work(r.dataset.req).required = r.checked; draw(r.dataset.req); $(`[data-req="${CSS.escape(r.dataset.req)}"]`, body)?.focus(); } };
  body.oninput = e => { const i = e.target.closest('[data-add]'); if (i) { const b = $(`[data-save-field="${CSS.escape(i.dataset.add)}"]`, body); if (b) b.hidden = !(dirty(i.dataset.add) || i.value.trim()); } };
  body.onclick = async e => {
    const rm = e.target.closest('[data-rm]');
    if (rm) { const k = rm.dataset.rm; const w = work(k); w.aliases.splice(Number(rm.dataset.i), 1); w.error = ''; draw(k); return $(`[data-add="${CSS.escape(k)}"]`, body)?.focus(); }
    const def = e.target.closest('[data-defaults]');
    if (def) { const k = def.dataset.defaults; const w = work(k); w.aliases = [...list.find(f => f.key === k).default_aliases]; w.error = ''; draw(k); return; }
    const sv = e.target.closest('[data-save-field]'); if (!sv || sv.hidden) return;
    const k = sv.dataset.saveField; const f = list.find(x => x.key === k);
    const pending = $(`[data-add="${CSS.escape(k)}"]`, body); if (pending?.value.trim()) { add(k, pending); if (fieldEdits[k].error) return; }
    const w = work(k);
    await busy('Saving…', async () => {
      try {
        const saved = await api(`/api/fields/${id(k)}`, { method: 'PUT', body: { aliases: w.aliases, ...(f.locked ? {} : { required: w.required }) } });
        Object.assign(f, saved); delete fieldEdits[k]; draw(k); state.fields = list; orderFields(true).catch(() => {});
        notify(`${f.label} saved. New uploads and the review screen use it now.`, 'success');
      } catch (err) {
        if (err.status !== 409) throw err;
        w.error = err.message; draw(k); $(`[data-add="${CSS.escape(k)}"]`, body)?.focus();
      }
    });
  };
}

async function users(body) {
  const list = await api('/api/users');
  const roles = ['operator', 'approver', 'admin', 'viewer'];
  body.innerHTML = `<section class="card"><div class="card-head"><div><h2>People</h2><div class="sub">Operators prepare entries · Approvers save to Oracle · Admins manage setup</div></div>
      <button class="secondary small" data-signout-all title="Ends every signed-in session except yours, for example after a shared password became known">Sign out everyone else</button></div>
    <div class="tbl-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
    ${list.map(u => `<tr><td><strong>${esc(u.username)}</strong></td><td><select data-role="${esc(u.username)}">${roles.map(r => `<option ${r === u.role ? 'selected' : ''}>${r}</option>`).join('')}</select></td>
      <td>${u.active ? '<span class="pill ok">active</span>' : '<span class="pill">disabled</span>'}${u.locked_until ? ` <span class="pill err" title="Too many wrong passwords">locked until ${esc(new Date(u.locked_until).toLocaleTimeString())}</span>` : ''}</td>
      <td><button class="ghost small" data-active-user="${esc(u.username)}" data-active="${!u.active}">${u.active ? 'Disable' : 'Enable'}</button> <button class="ghost small" data-reset="${esc(u.username)}">Reset password</button>${u.locked_until ? ` <button class="secondary small" data-unlock="${esc(u.username)}">Unlock</button>` : ''}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="card-body"><h3 class="mb-8">Add a person</h3>
    <form id="add-user" class="form-row"><label>Username<input name="username" required minlength="2" maxlength="50" pattern="[A-Za-z0-9._@\-]+"></label>
      <label>Temporary password<input name="password" type="password" required minlength="12" autocomplete="new-password" title="At least 12 characters, not a common password, not the username"></label>
      <label>Role<select name="role">${roles.map(r => `<option>${r}</option>`).join('')}</select></label><button class="primary">Add</button></form></div></section>`;
  $('#add-user').onsubmit = e => { e.preventDefault(); const f = new FormData(e.target); busy('Adding…', async () => { await api('/api/users', { method: 'POST', body: Object.fromEntries(f) }); notify('Person added.', 'success'); await users(body); }); };
  body.onchange = e => { const s = e.target.closest('[data-role]'); if (s) busy('Saving…', async () => { await api(`/api/users/${id(s.dataset.role)}`, { method: 'PATCH', body: { role: s.value } }); notify('Role updated.', 'success'); await users(body); }); };
  body.onclick = e => {
    const a = e.target.closest('[data-active-user]'); const r = e.target.closest('[data-reset]');
    if (a) busy('Saving…', async () => { await api(`/api/users/${id(a.dataset.activeUser)}`, { method: 'PATCH', body: { active: a.dataset.active === 'true' } }); await users(body); });
    const un = e.target.closest('[data-unlock]'); const all = e.target.closest('[data-signout-all]');
    if (un) busy('Unlocking…', async () => { await api(`/api/users/${id(un.dataset.unlock)}/unlock`, { method: 'POST' }); notify(`${un.dataset.unlock} can sign in again.`, 'success'); await users(body); });
    if (all) confirmDialog('Sign out everyone else?', '<p>Every other signed-in session ends now. People sign in again with their own password.</p>', 'Sign them out')
      .then(ok => ok && busy('Signing out…', async () => { const res = await api('/api/sessions/sign-out-everyone', { method: 'POST' }); notify(`${res.sessions_ended} session${res.sessions_ended === 1 ? '' : 's'} ended.`, 'success'); }));
    if (r) { const pw = prompt(`New password for ${r.dataset.reset} (12+ characters, not a common password)`); if (pw) busy('Saving…', async () => { await api(`/api/users/${id(r.dataset.reset)}`, { method: 'PATCH', body: { password: pw } }); notify('Password reset. Their sessions were signed out.', 'success'); }); }
  };
}

const ACTIONS = { upload: 'Uploaded a file', edit: 'Corrected values', preview: 'Prepared the preview', approved_saved: 'Approved & saved to Oracle', approve_failed: 'Save failed (rolled back)',
  mapping_applied: 'Applied a layout', template_saved: 'Saved a layout template', mapping_status: 'Changed column mapping', login: 'Signed in', login_failed: 'Failed sign-in',
  user_created: 'Added a person', user_changed: 'Changed a person', settings_changed: 'Changed settings', setup: 'Created the workspace', template_active: 'Changed a template',
  field_changed: 'Changed an order field', template_unit_learned: 'A layout template learned its quantity unit',
  login_locked: 'Account locked after wrong passwords', user_unlocked: 'Unlocked an account', sessions_revoked: 'Signed everyone else out',
  audit_verified: 'Checked the audit history', knowledge_edit: 'Edited the data dictionary', knowledge_refresh: 'Refreshed the data dictionary', chat_feedback: 'Rated an AI answer', example_deleted: 'Removed a confirmed answer' };

async function audit(body) {
  const list = await api('/api/audit');
  body.innerHTML = `<section class="card"><div class="card-head"><div><h2>Audit history</h2><div class="sub">Latest 300 events · stored locally, cannot be edited or deleted; Oracle saves are also logged in PP_IMPORT_LOG</div></div>
      ${can('users') ? '<button class="secondary small" data-verify-audit title="Recomputes the chain of fingerprints that links every entry to the one before">Verify history is untouched</button>' : ''}</div>
    <div class="tbl-wrap max-h-70vh"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead><tbody>
    ${list.map(a => `<tr><td class="nowrap">${esc(new Date(a.at).toLocaleString())}</td><td>${esc(a.actor)}</td><td>${esc(ACTIONS[a.action] || a.action)}</td>
      <td class="evidence">${a.detail.changes ? a.detail.changes.slice(0, 6).map(c => `<div><code>${esc(c.field)}</code>: ${esc(show(c.before))} → <b>${esc(show(c.after))}</b></div>`).join('') + (a.detail.changes.length > 6 ? `<div>…${a.detail.changes.length - 6} more</div>` : '')
        : esc(Object.entries(a.detail).filter(([, v]) => v != null).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v).slice(0, 120) : v}`).join(' · '))}</td></tr>`).join('')}
    </tbody></table></div></section>`;
  body.onclick = e => {
    if (!e.target.closest('[data-verify-audit]')) return;
    busy('Checking every entry…', async () => {
      const r = await api('/api/audit/verify');
      if (r.ok) notify(`The audit history is untouched: ${r.rows} entries checked.`, 'success');
      else notify(`The audit history was changed near entry ${r.row ?? '?'}. Keep the database file and tell your administrator.`, 'error');
      await audit(body);
    });
  };
}

async function settings(body) {
  const [s, h] = await Promise.all([api('/api/settings'), api('/api/health').catch(() => null)]);
  const checks = h?.readers?.checks || [];
  body.innerHTML = `<form class="card pad form-card" id="settings-form"><h2>Workspace settings</h2>
    <label>Default exporter (pre-filled on every order)<input name="company_exporter" value="${esc(s.company_exporter)}" maxlength="100"></label>
    <label>Default sample type<input name="default_sample_type" value="${esc(s.default_sample_type)}" maxlength="30"></label>
    <label class="check"><input type="checkbox" name="allow_self_approval" ${s.allow_self_approval ? 'checked' : ''}> Allow the person who uploaded a file to approve it (turn off for a four-eyes rule)</label>
    <button class="primary">Save settings</button></form>
    ${checks.length ? `<section class="card mt-14"><div class="card-head"><div><h2>System checks</h2><div class="sub">Versions that matter for reading buyer files safely on this computer</div></div>
      ${h.readers.ok ? '<span class="pill ok">all good</span>' : '<span class="pill warn">needs attention</span>'}</div>
      <div class="tbl-wrap flush"><table><thead><tr><th>Check</th><th>Version</th><th>Status</th></tr></thead><tbody>
      ${checks.map(c => `<tr><td>${esc(c.name)}</td><td class="mono">${esc(c.version || '—')}</td><td>${c.ok ? '<span class="pill ok">OK</span>' : `<span class="pill warn">check</span> <span class="small-text">${esc(c.message || '')}</span>`}</td></tr>`).join('')}
      </tbody></table></div></section>` : ''}`;
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
      ${edit ? '<button class="secondary small ml-auto" id="kb-refresh">Refresh from Oracle</button>' : ''}</div>
    <div class="stat-row">${[['Tables', s.tables], ['Links confirmed', (st.verified || 0) + (st.declared || 0)], ['Links to review', (st.proposed || 0) + (st.inferred || 0)], ['Values indexed', fmt(k.values.reduce((n, v) => n + v.values, 0), 0)], ['Confirmed answers', examples.length]].map(([a, b]) => `<div class="card kpi"><small>${a}</small><strong>${b}</strong></div>`).join('')}</div>
    <section class="card mb-14"><div class="card-head"><div><h2>Relationships</h2><div class="sub">How tables link. Confirmed links are used freely; unconfirmed ones lower an answer’s confidence until verified.</div></div>
      <select id="rel-filter" class="w-220"><option value="review">Needs review</option><option value="all">All links</option></select></div>
      <div class="tbl-wrap flush max-h-420" id="rels"></div></section>
    <section class="card mb-14"><div class="card-head"><div><h2>Tables &amp; business names</h2><div class="sub">Click a table to edit its business name, synonyms and column meanings.</div></div><input id="dict-q" class="w-220" placeholder="Search"></div>
      <div class="dict-grid"><div id="dict-list" class="dict-list"></div><div id="dict-edit" class="dict-edit"><p class="empty">Choose a table.</p></div></div></section>
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
  el.innerHTML = `<form id="dict-form" class="form-card form-wide"><div class="row wrap"><h3>${esc(t.business)}</h3><code class="muted">${esc(t.name)}</code><span class="pill">${esc(t.kind)}</span>${t.rows != null ? `<span class="pill" title="From Oracle statistics; the Database screen shows exact counts">≈ ${fmt(t.rows, 0)} rows</span>` : ''}</div>
      <div class="grid2"><label>Business name<input name="business" value="${esc(t.business)}" maxlength="80" ${edit ? '' : 'disabled'}></label>
      <label>Synonyms (comma separated)<input name="synonyms" value="${esc(t.synonyms.join(', '))}" ${edit ? '' : 'disabled'}></label></div>
      <label>Description<input name="description" value="${esc(t.description)}" maxlength="500" ${edit ? '' : 'disabled'}></label>
      ${edit ? '<div><button class="primary small">Save table</button></div>' : ''}</form>
    <div class="tbl-wrap mt-12 max-h-420"><table class="grid-input"><thead><tr><th>Column</th><th>Business name</th><th>Synonyms</th><th>Role</th><th></th></tr></thead><tbody>
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
