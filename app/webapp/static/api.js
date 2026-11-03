/* Requests to the app's own API, with the vendored sign-in sheet on a 401.
 *
 * On this PC every request passes; from another device the server answers
 * 401 until the page holds the token, which the sheet trades the password for.
 */

import { requestSignIn } from './_vendored/sign-in/sign-in.js';

const TOKEN_KEY = 'folder-explorer.token';

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
}

export function authHeaders() {
  const t = readToken();
  return t ? { Authorization: 'Bearer ' + t } : {};
}

export async function api(path, init) {
  const opts = init || {};
  const send = () => fetch(path, { ...opts, headers: { ...(opts.headers || {}), ...authHeaders() } });
  let res = await send();
  if (res.status === 401) {
    await requestSignIn({
      endpoint: '/api/login',
      hint: 'Enter the password to use Folder explorer on this device.',
      onToken: (token) => { try { localStorage.setItem(TOKEN_KEY, token); } catch (e) { /* kept for this page only */ } },
    });
    res = await send();
  }
  return res;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || 'HTTP ' + status);
    this.status = status;
  }
}

async function parse(res) {
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (!res.ok) throw new ApiError(res.status, body && body.detail);
  return body;
}

export async function getJSON(path) {
  return parse(await api(path));
}

export async function postJSON(path, body) {
  return parse(await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }));
}
