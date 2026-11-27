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

/** A response's JSON, or null when it carries none. */
async function readBody(res) {
  try { return await res.json(); } catch (e) { return null; }
}

async function parse(res) {
  const body = await readBody(res);
  if (!res.ok) throw new ApiError(res.status, body && body.detail);
  return body;
}

/** POST ``body`` as JSON and hand back the response unread, for an answer that isn't JSON. */
export function postRes(path, body) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
}

/** A response with its body read, refusals included: ``{res, body}``. */
export async function answered(res) {
  return { res, body: await readBody(res) };
}

// For a caller that reads the status itself. Both reject only when the app never answered.
export const getRaw = async (path) => answered(await api(path));
export const postRaw = async (path, body) => answered(await postRes(path, body));

export async function getJSON(path) {
  return parse(await api(path));
}

export async function postJSON(path, body) {
  return parse(await postRes(path, body));
}

export async function deleteJSON(path) {
  return parse(await api(path, { method: 'DELETE' }));
}
