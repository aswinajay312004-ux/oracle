// Small DOM and formatting helpers shared by every screen.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const clone = v => structuredClone(v);
export const num = v => (v === '' || v == null || !Number.isFinite(Number(v))) ? 0 : Number(v);
export const fmt = (v, d = 3) => v == null || v === '' ? '—' : Number.isFinite(Number(v)) && typeof v !== 'boolean' && String(v).trim() !== '' ? Number(v).toLocaleString('en-IN', { maximumFractionDigits: d }) : esc(String(v));   // text from a file is escaped: callers put the result into markup
export const show = v => v == null || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
export const plural = (n, word, many = word + 's') => `${fmt(n, 0)} ${Number(n) === 1 ? word : many}`;

export const state = {
  user: null, permissions: [], draft: null, dirty: false, saved: false, health: null,
  tab: {}, masters: {}, busy: false,
};
export const can = p => state.permissions.includes(p);

// A short message in the corner (#toasts is a polite live region, so screen readers read it out). An error stays
// until the person dismisses it: someone who looked away for a moment must still be able to read what went wrong.
export function notify(message, type = '') {
  if (!message) return;
  const box = $('#toasts'); const t = document.createElement('div');
  t.className = `toast ${type}`; t.innerHTML = `<span>${esc(message)}</span><button type="button" aria-label="Dismiss this message">✕</button>`;
  t.querySelector('button').addEventListener('click', () => t.remove());
  box.prepend(t); while (box.children.length > 4) box.lastChild.remove();
  if (type !== 'error') setTimeout(() => t.remove(), 6000);
}

// Error toasts stay until dismissed, but not past the next step that worked: an old "not saved" next to a saved
// order would mislead.
export function dismissErrors() { $$('#toasts .toast.error').forEach(t => t.remove()); }

// A message that stays where it belongs (on the drop zone, in the approval bar…) until it is dismissed.
// app.js handles [data-dismiss] for every one of them.
export function lastingBox(kind, title, text, { id = '', extraHtml = '' } = {}) {
  return `<div class="banner ${kind} lasting" ${id ? `id="${esc(id)}"` : ''} role="alert" data-dismissible><span class="b-ic" aria-hidden="true">!</span>
    <div class="flex-fill">${title ? `<b>${esc(title)}</b> ` : ''}<span data-box-text>${esc(text)}</span>${extraHtml}</div>
    <button type="button" class="icon-btn b-close" data-dismiss aria-label="Dismiss this message">✕</button></div>`;
}

// Re-rendering a screen with innerHTML drops keyboard focus on <body>. These keep it: remember which control had
// focus (by id, or by the data-* attributes that say what it is), draw, then focus the same control again.
const FOCUS_KEYS = ['path', 'act', 'act2', 'oi', 'tab', 'basis', 'unit', 'key', 'li', 'fi', 'ai', 'yi', 'size', 'sizecol', 'old', 'addsize', 'newsize', 'groupDesc', 'row', 'xtab'];
const attrName = k => 'data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
export function focusKey(root, fallback = []) {
  const el = document.activeElement;
  if (!el || el === document.body || !root?.contains(el)) return null;
  const parts = FOCUS_KEYS.filter(k => el.dataset[k] != null).map(k => `[${attrName(k)}="${CSS.escape(el.dataset[k])}"]`);
  return [el.id ? `#${CSS.escape(el.id)}` : null, parts.length ? el.tagName.toLowerCase() + parts.join('') : null, ...fallback].filter(Boolean);
}
export const focusable = el => !!el && !el.disabled && !el.closest('[hidden], [inert]') && el.getClientRects().length > 0;
export function restoreFocus(keys) {
  for (const s of keys || []) { const el = $(s); if (focusable(el)) { el.focus({ preventScroll: true }); return true; } }
  return false;
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

// The side drawer is a modal dialog: the page behind it is inert while it is open, Tab and Shift+Tab stay inside
// it, and closing it puts focus back on the control that opened it.
let opener = null;
const setInert = on => { const shell = $('.shell'); if (shell) shell.inert = on; };
export function drawer(title, html) {
  const d = $('#drawer');
  if (d.hidden) opener = document.activeElement;
  $('#drawer-title').textContent = title; $('#drawer-body').innerHTML = html;
  d.hidden = false; $('#scrim').hidden = false; setInert(true); $('#drawer-close').focus();
  return $('#drawer-body');
}
export function closeDrawer() {
  const d = $('#drawer'); const wasOpen = !d.hidden;
  d.hidden = true; $('#scrim').hidden = true; setInert(false);
  const back = opener; opener = null;
  if (wasOpen && focusable(back)) back.focus();
}
const TABBABLE = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex="-1"])';
export function keepTabInside(e, root) {
  if (e.key !== 'Tab') return;
  const items = $$(TABBABLE, root).filter(focusable);
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0], last = items.at(-1), at = document.activeElement;
  if (e.shiftKey && (at === first || !root.contains(at))) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && (at === last || !root.contains(at))) { e.preventDefault(); first.focus(); }
}

export function modal(titleHtml, bodyHtml, footHtml = '', { narrow = false } = {}) {
  $('#modal-title-wrap').innerHTML = titleHtml; $('#modal-body').innerHTML = bodyHtml; $('#modal-foot').innerHTML = footHtml;
  $('#modal-foot').hidden = !footHtml;
  const d = $('#modal'); d.classList.toggle('narrow', narrow); if (!d.open) d.showModal();
  return { body: $('#modal-body'), foot: $('#modal-foot'), head: $('#modal-title-wrap'), close: () => d.close() };
}

// Business form view of one record: label, value (decoded masters), Oracle column, source.
export function formView(fields) {
  return `<div class="form-view">${fields.map(f => `<div class="fv ${f.written ? 'written' : ''}"><div class="k">${esc(f.label)}${f.code ? `<code>${esc(f.code)}</code>` : ''}</div>
    <div class="v">${f.html ?? esc(show(f.value))}${f.src || ''}</div></div>`).join('')}</div>`;
}

export function csv(columns, rows) {
  // Text that starts with = + - @ (or a tab / carriage return) would run as a formula when the file is opened in
  // Excel (CSV injection): a leading ' makes Excel show it as text. Numbers stay numbers.
  const q = v => {
    let s = v == null ? '' : String(v);
    if (typeof v !== 'number' && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
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
