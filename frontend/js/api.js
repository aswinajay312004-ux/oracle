// Same-origin JSON API client with plain-language errors.
const listeners = new Set();
export const onUnauthorized = fn => listeners.add(fn);
const passwordListeners = new Set();
export const onPasswordRequired = fn => passwordListeners.add(fn);   // 403 "Choose your own password first."

// When the server gives no sentence of its own (a page from a proxy or antivirus, an empty answer): one plain
// sentence per status. A sentence the server sends (including its plain 422 messages) is always shown as it is.
const PLAIN = {
  400: 'The server could not use this request. Reload the page and try again.',
  401: 'You are signed out. Sign in again to continue.',
  403: 'Your role cannot do this. Ask an administrator.',
  404: 'This was not found. It may have been removed: reload the page.',
  408: 'The server took too long to answer. Try again.',
  409: 'Someone changed this at the same time. Reload the page and try again.',
  413: 'The file is too large (the limit is 12 MB). Save only the order sheets (delete unused sheets, pictures and blank rows), then upload it again.',
  415: 'This kind of file cannot be read. Choose the buyer Excel file (.xlsx, .xls, .xlsm, .xlsb, .ods), a CSV file or a JSON file.',
  429: 'Too many tries in a short time. Wait a minute, then try again.',
};
export function plainStatus(status) {
  if (PLAIN[status]) return PLAIN[status];
  if ([502, 503, 504].includes(status)) return 'The POPPYS AI server is not answering properly. Check that start.ps1 is still running, wait a minute, then try again.';
  if (status >= 500) return 'The POPPYS AI server had a problem and could not finish. Try again. If it happens again, tell your administrator.';
  return 'This could not be done. Try again. If it happens again, tell your administrator.';
}

function message(detail, status) {
  if (Array.isArray(detail)) return detail.map(x => `${(x.loc || []).slice(1).join(' › ')}: ${x.msg}`).join('\n');
  if (detail && typeof detail === 'object') return detail.message ? String(detail.message) : plainStatus(status);
  return String(detail || plainStatus(status));
}

export const NOT_RESPONDING = 'The POPPYS AI server is not responding. Check that start.ps1 is still running, then try again.';

export async function api(path, { method = 'GET', body, form } = {}) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, opts); }
  // no answer at all (server stopped, connection dropped): status 0, so callers can tell "maybe done" from "refused"
  catch (_) { throw Object.assign(new Error(`${NOT_RESPONDING} Your draft is kept.`), { status: 0, network: true }); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const text = message(data.detail, res.status);
    if (res.status === 401) listeners.forEach(fn => fn());
    if (res.status === 403 && /choose your own password/i.test(text)) passwordListeners.forEach(fn => fn());
    // keep the status and body: some answers carry details for the screen (e.g. 409 {detail, rejected})
    throw Object.assign(new Error(text), { status: res.status, data });
  }
  return data;
}
export const id = s => encodeURIComponent(s);

// Effective order fields (Setup → Order fields): labels, aliases and which ones are required.
let fieldsCache = null;
export function orderFields(refresh = false) {
  if (refresh || !fieldsCache) fieldsCache = api('/api/fields').catch(e => { fieldsCache = null; throw e; });
  return fieldsCache;
}
