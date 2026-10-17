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

// Where a value came from: an Excel/PDF cell, a calculation or a default.
export function source(src) {
  if (!src) return '';
  if (src.derivation && !src.cell) return `<span class="chip calc" title="${esc(src.derivation)}">${esc(src.derivation.length > 28 ? src.derivation.slice(0, 26) + '…' : src.derivation)}</span>`;
  if (src.origin) return `<span class="chip calc">${esc(src.origin)}</span>`;
  if (!src.cell) return '';
  const where = String(src.sheet || '').startsWith('PDF') ? `${src.sheet} · ${src.cell}` : `${src.sheet ? src.sheet + '!' : ''}${src.cell}`;
  return `<span class="chip cell" title="${esc((src.formula ? 'Formula ' + src.formula + ' · ' : '') + 'read from ' + where)}">${esc(where.length > 22 ? '…' + where.slice(-20) : where)}</span>`;
}

export function confirmDialog(title, html, ok = 'Confirm') {
  const d = $('#confirm'); $('#confirm-title').textContent = title; $('#confirm-body').innerHTML = html; $('#confirm-ok').textContent = ok;
  return new Promise(resolve => { d.onclose = () => resolve(d.returnValue === 'ok'); d.returnValue = ''; d.showModal(); });
}

export function drawer(title, html) {
  $('#drawer-title').textContent = title; $('#drawer-body').innerHTML = html;
  $('#drawer').hidden = false; $('#scrim').hidden = false; $('#drawer-close').focus();
  return $('#drawer-body');
}
export function closeDrawer() { $('#drawer').hidden = true; $('#scrim').hidden = true; }

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
  const q = v => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
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
