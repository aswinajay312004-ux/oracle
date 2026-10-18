// Small DOM and formatting helpers shared by every screen.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const clone = v => structuredClone(v);
export const num = v => (v === '' || v == null || !Number.isFinite(Number(v))) ? 0 : Number(v);
export const fmt = (v, d = 3) => v == null || v === '' ? '—' : Number.isFinite(Number(v)) && typeof v !== 'boolean' && String(v).trim() !== '' ? Number(v).toLocaleString('en-IN', { maximumFractionDigits: d }) : String(v);
export const show = v => v == null || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
export const plural = (n, word, many = word + 's') => `${fmt(n, 0)} ${Number(n) === 1 ? word : many}`;

export const state = {
  user: null, permissions: [], draft: null, dirty: false, saved: false, health: null,
  tab: {}, masters: {}, busy: false,
};
export const can = p => state.permissions.includes(p);

export function notify(message, type = '') {
  if (!message) return;
  const box = $('#toasts'); const t = document.createElement('div');
  t.className = `toast ${type}`; t.innerHTML = `<span>${esc(message)}</span><button aria-label="Dismiss">✕</button>`;
  t.querySelector('button').onclick = () => t.remove();
  box.prepend(t); while (box.children.length > 4) box.lastChild.remove();
  setTimeout(() => t.remove(), type === 'error' ? 12000 : 6000);
}

export async function busy(message, action) {
  if (state.busy) return;
  state.busy = true; $('#busy').hidden = false; $('#busy-text').textContent = message;
  try { return await action(); }
  catch (e) { notify(e.message, 'error'); }
  finally { state.busy = false; $('#busy').hidden = true; }
}

// Where a value came from: an Excel cell, a calculation or a default.
export function source(src) {
  if (!src) return '';
  if (src.derivation && !src.cell) return `<span class="chip calc" title="${esc(src.derivation)}">${esc(src.derivation.length > 28 ? src.derivation.slice(0, 26) + '…' : src.derivation)}</span>`;
  if (src.origin) return `<span class="chip calc">${esc(src.origin)}</span>`;
  if (!src.cell) return '';
  const where = `${src.sheet ? src.sheet + '!' : ''}${src.cell}`;
  return `<span class="chip cell" title="${esc((src.formula ? 'Formula ' + src.formula + ' · ' : '') + 'read from ' + where)}">${esc(where.length > 22 ? '…' + where.slice(-20) : where)}</span>`;
}

export function confirmDialog(title, html, ok = 'Confirm') {
  const d = $('#confirm'); $('#confirm-title').textContent = title; $('#confirm-body').innerHTML = html; $('#confirm-ok').textContent = ok;
  return new Promise(resolve => { d.onclose = () => resolve(d.returnValue === 'ok'); d.returnValue = ''; d.showModal(); });
}

// A new password, typed twice and hidden (never the browser's prompt(), which shows it in plain text). null = cancelled.
export async function passwordDialog(title, intro) {
  const html = `<p>${esc(intro)}</p><label class="field" for="pw-a">New password (12 or more characters)<input id="pw-a" type="password" required minlength="12" maxlength="200" autocomplete="new-password"></label>
    <label class="field" for="pw-b">Type it again<input id="pw-b" type="password" required minlength="12" maxlength="200" autocomplete="new-password"></label>`;
  const d = $('#confirm'); const pending = confirmDialog(title, html, 'Set password');
  const a = $('#pw-a', d); const b = $('#pw-b', d);
  const same = () => b.setCustomValidity(a.value === b.value ? '' : 'The two passwords are different.');
  a.addEventListener('input', same); b.addEventListener('input', same); a.focus();
  return (await pending) ? a.value : null;
}

// The side panel keeps the keyboard inside while it is open (Tab cycles within it) and gives focus back on close.
let drawerReturn = null;
export function drawer(title, html) {
  $('#drawer-title').textContent = title; $('#drawer-body').innerHTML = html;
  if ($('#drawer').hidden) drawerReturn = document.activeElement;
  $('#drawer').hidden = false; $('#scrim').hidden = false; $('#drawer-close').focus();
  return $('#drawer-body');
}
export function closeDrawer() {
  const open = !$('#drawer').hidden;
  $('#drawer').hidden = true; $('#scrim').hidden = true;
  if (open && drawerReturn?.isConnected) drawerReturn.focus();
  drawerReturn = null;
}
if (typeof document !== 'undefined') document.addEventListener('keydown', e => {   // (the module is also imported by Node tests)
  const d = $('#drawer'); if (e.key !== 'Tab' || !d || d.hidden) return;
  const items = [...d.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter(x => !x.disabled && x.offsetParent);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

export function modal(titleHtml, bodyHtml, footHtml = '') {
  $('#modal-title-wrap').innerHTML = titleHtml; $('#modal-body').innerHTML = bodyHtml; $('#modal-foot').innerHTML = footHtml;
  $('#modal-foot').hidden = !footHtml;
  const d = $('#modal'); if (!d.open) d.showModal();
  return { body: $('#modal-body'), foot: $('#modal-foot'), head: $('#modal-title-wrap'), close: () => d.close() };
}

// Business form view of one record: label, value (decoded masters), Oracle column, source.
export function formView(fields) {
  return `<div class="form-view">${fields.map(f => `<div class="fv ${f.written ? 'written' : ''}"><div class="k">${esc(f.label)}${f.code ? `<code>${esc(f.code)}</code>` : ''}</div>
    <div class="v">${f.html ?? esc(show(f.value))}${f.src || ''}</div></div>`).join('')}</div>`;
}

export function csv(columns, rows) {
  // Text that starts like a formula (= + - @, tab, CR) is prefixed with ' so a spreadsheet shows it instead of running it.
  const q = v => { let s = v == null ? '' : String(v); if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = `'${s}`; return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = [columns.map(c => q(c.label)).join(','), ...rows.map(r => columns.map(c => q(r[c.index])).join(','))].join('\r\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv' }));
  a.download = 'poppys-answer.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export function timeAgo(iso) {
  const d = new Date(iso); const s = (Date.now() - d) / 1000;
  if (!Number.isFinite(s)) return '';
  if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return d.toLocaleDateString();
}
