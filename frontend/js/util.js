// Small DOM and formatting helpers shared by every screen.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const clone = v => structuredClone(v);
export const num = v => (v === '' || v == null || !Number.isFinite(Number(v))) ? 0 : Number(v);
export const fmt = (v, d = 3) => v == null || v === '' ? '—' : Number.isFinite(Number(v)) ? Number(v).toLocaleString('en-IN', { maximumFractionDigits: d }) : String(v);
export const show = v => v == null || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
export const plural = (n, word, many = word + 's') => `${fmt(n, 0)} ${n === 1 ? word : many}`;

export const state = {
  user: null, permissions: [], draft: null, dirty: false, saved: false, health: null,
  tab: {}, masters: {}, busy: false,
};
export const can = p => state.permissions.includes(p);

export function notify(message, type = '') {
  const n = $('#notice'); n.textContent = message || ''; n.className = `notice ${type}`; n.hidden = !message;
  if (message && type !== 'error') clearTimeout(notify.t), notify.t = setTimeout(() => { if (n.textContent === message) n.hidden = true; }, 9000);
}

export async function busy(message, action) {
  if (state.busy) return;
  state.busy = true; $('#busy').hidden = false; $('#busy-text').textContent = message;
  try { return await action(); }
  catch (e) { notify(e.message, 'error'); }
  finally { state.busy = false; $('#busy').hidden = true; }
}

export function source(src) {
  if (!src) return '';
  if (src.derivation && !src.cell) return `<span class="chip default" title="${esc(src.derivation)}">${esc(src.derivation.length > 34 ? src.derivation.slice(0, 32) + '…' : src.derivation)}</span>`;
  if (src.origin) return `<span class="chip default">${esc(src.origin)}</span>`;
  if (!src.cell) return '';
  const where = String(src.sheet || '').startsWith('PDF') ? `${src.sheet} · ${src.cell}` : `${src.sheet ? src.sheet + '!' : ''}${src.cell}`;
  return `<span class="chip" title="${esc(src.formula ? 'Formula ' + src.formula : 'Value read from the file')}">${esc(where)}</span>`;
}

export function confirmDialog(title, html, ok = 'Confirm') {
  const d = $('#confirm'); $('#confirm-title').textContent = title; $('#confirm-body').innerHTML = html; $('#confirm-ok').textContent = ok;
  return new Promise(resolve => { d.onclose = () => resolve(d.returnValue === 'ok'); d.returnValue = ''; d.showModal(); });
}

export function drawer(title, html) {
  $('#drawer-title').textContent = title; $('#drawer-body').innerHTML = html;
  $('#drawer').hidden = false; $('#scrim').hidden = false; $('#drawer-close').focus();
}
export function closeDrawer() { $('#drawer').hidden = true; $('#scrim').hidden = true; }

// Key/value record renderer: highlights the columns POPPYS AI wrote.
export function record(row, written = [], { hideEmpty = true, decoded = {} } = {}) {
  const w = new Set(written);
  const entries = Object.entries(row || {}).filter(([k, v]) => !hideEmpty || w.has(k) || (v !== null && v !== ''));
  return `<div class="kv">${entries.map(([k, v]) => `<div class="k ${w.has(k) ? 'w' : ''}">${esc(k)}</div><div class="${w.has(k) ? 'w' : ''} ${v == null || v === '' ? 'empty-v' : ''}">${esc(show(v))}${decoded[k] != null ? ` <span class="pill brand">${esc(decoded[k])}</span>` : ''}</div>`).join('')}</div>`;
}
