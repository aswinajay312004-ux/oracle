// Same-origin JSON API client with plain-language errors.
const listeners = new Set();
export const onUnauthorized = fn => listeners.add(fn);

function message(detail, status) {
  if (Array.isArray(detail)) return detail.map(x => `${(x.loc || []).slice(1).join(' › ')}: ${x.msg}`).join('\n');
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return String(detail || `The request could not be completed (${status}).`);
}

export async function api(path, { method = 'GET', body, form } = {}) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, opts); }
  catch (_) { throw new Error('The POPPYS AI server is not responding. Check that start.ps1 is still running, then try again. Your draft is kept.'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) listeners.forEach(fn => fn());
    throw new Error(message(data.detail, res.status));
  }
  return data;
}
export const id = s => encodeURIComponent(s);
