/* A dropped file that no scan covers, opened for editing through the browser's own file handle.
 *
 * Chrome and Edge on a PC give a dropped file a handle (``getAsFileSystemHandle``,
 * in a secure context). The page reads and saves the file through it, so the
 * server never receives or builds a path: it only renders the text the page
 * sends (``POST /api/doc/dropped/render``). This is the one module that writes
 * a user's file from the page (a test holds it). Save asks the browser for
 * write permission on the reader's tap, never on the drop, and checks the
 * file's version first, so a copy that changed on disk since it opened stops at
 * the editor's conflict screen. Overwriting it keeps no backup: the page has no
 * folder to put one in.
 */

import { postRaw } from '../api.js';
import { APP } from '../state.js';
import { NO_ANSWER } from '../doc-answer.js';
import { renderBody, toHex } from '../drop-files.js';
import { EDIT_MAX_BYTES, decode, encode, versionOf } from './local-text.js';

const WRITE = { mode: 'readwrite' };

/** Whether this browser can hand a dropped file's handle to the page at all (not Safari or Firefox, nor a page served insecurely). */
export const handlesSupported = () => typeof DataTransferItem !== 'undefined' && typeof DataTransferItem.prototype.getAsFileSystemHandle === 'function';

/**
 * The file handle of a dropped ``item``, or null when the drop gave none (a virtual file, a mail attachment) or it is not the file named ``name``.
 * Called inside the drop event: the item is dead once the event has been handled.
 */
export async function handleFrom(item, name) {
  if (!item || typeof item.getAsFileSystemHandle !== 'function') return null;
  try {
    const handle = await item.getAsFileSystemHandle();
    return handle && handle.kind === 'file' && handle.name === name ? handle : null;
  } catch (e) {
    return null;
  }
}

/** The document a drop with a handle shows: the rendered one, marked as the page's own to save. */
export const withLocal = (doc, handle) => (handle ? { ...doc, local: true } : doc);

/** The server's read-only render of dropped text: ``{ok: true, body}`` or ``{ok: false, reason, message}``. */
export async function renderDropped(name, text) {
  let reply;
  try {
    reply = await postRaw('/api/doc/dropped/render', renderBody({ name }, text));
  } catch (e) {
    return { ok: false, ...NO_ANSWER };
  }
  const { res, body } = reply;
  if (res.ok) return { ok: true, body };
  return { ok: false, reason: (body && body.reason) || 'app_error', message: (body && typeof body.detail === 'string' && body.detail) || 'The app answered with an error (HTTP ' + res.status + ')' };
}

async function hexOf(bytes) {
  return toHex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
}

/** The file now: its bytes and the version they carry. */
async function readNow(handle) {
  const file = await handle.getFile();
  const bytes = new Uint8Array(await file.arrayBuffer());
  return { bytes, version: versionOf(file.lastModified, bytes.length, await hexOf(bytes)) };
}

const refused = (reason, detail, status = 422) => ({ res: { ok: false, status }, body: { reason, detail } });

/** Write access for the handle, asked of the reader on the tap that saves; false when they decline. */
async function mayWrite(handle) {
  try {
    if (typeof handle.queryPermission !== 'function') return true;
    if ((await handle.queryPermission(WRITE)) === 'granted') return true;
    return (await handle.requestPermission(WRITE)) === 'granted';
  } catch (e) {
    return false;
  }
}

/**
 * The editor's side of a local file: ``source()``, ``render(text)`` and ``save(text, version, overwrite)`` answer
 * ``{res, body}`` like the server's routes do, so one editor serves both. ``draftKey`` names the unsaved draft by file name and version.
 */
export function localSource(handle, name) {
  let shape = { bom: false, newline: '\n', trailing: true };
  return {
    draftKey: (version) => APP + '.draft:local:' + name + ':' + version,
    previewDoc: (body) => ({ ...body, dropped: false }),

    async source() {
      let now;
      try { now = await readNow(handle); } catch (e) { return refused('unreadable', 'The browser could not read the file', 500); }
      const kind = /\.html?$/i.test(name) ? 'html' : 'markdown';
      const decoded = now.bytes.length <= EDIT_MAX_BYTES ? decode(now.bytes) : null;
      if (!decoded) {
        const big = now.bytes.length > EDIT_MAX_BYTES;
        return { res: { ok: true, status: 200 }, body: { editable: false, version: now.version, reason: big ? 'edit_too_large' : 'not_utf8',
          detail: big ? 'Documents up to 1 MB can be edited here' : 'This file is not saved as UTF-8 text, so it opens read-only here' } };
      }
      shape = decoded.shape;
      return { res: { ok: true, status: 200 }, body: { editable: true, text: decoded.text, version: now.version, kind } };
    },

    async render(text) {
      const r = await renderDropped(name, text);
      return r.ok ? { res: { ok: true, status: 200 }, body: r.body } : refused(r.reason, r.message, 500);
    },

    async save(text, version, overwrite) {
      if (!(await mayWrite(handle))) return refused('permission_denied', 'The browser did not let the page save this file');
      let now;
      try { now = await readNow(handle); } catch (e) { return refused('unreadable', 'The browser could not read the file', 500); }
      const theirs = decode(now.bytes);
      if (now.version !== version) {
        return { res: { ok: false, status: 409 }, body: { reason: 'conflict', text: theirs && now.bytes.length <= EDIT_MAX_BYTES ? theirs.text : null, version: now.version } };
      }
      if (theirs) shape = theirs.shape;
      else if (!overwrite) return refused('not_utf8', 'This file is not saved as UTF-8 text, so it opens read-only here');
      if (theirs && text === theirs.text) return { res: { ok: true, status: 200 }, body: { version: now.version } };
      try {
        const out = await handle.createWritable();
        await out.write(encode(text, shape));
        await out.close();
        return { res: { ok: true, status: 200 }, body: { version: (await readNow(handle)).version } };
      } catch (e) {
        return refused('write_failed', 'The browser could not write the file', 500);
      }
    },
  };
}

/** The current text of a local file, rendered, for View after an edit: ``{ok: true, body}`` or ``{ok: false, reason, message}``. */
export async function renderLocal(handle, name) {
  let bytes;
  try {
    bytes = new Uint8Array(await (await handle.getFile()).arrayBuffer());
  } catch (e) {
    return { ok: false, reason: 'unreadable', message: 'The browser could not read the file' };
  }
  const decoded = decode(bytes);
  if (!decoded) return { ok: false, reason: 'not_utf8', message: 'This file is not saved as UTF-8 text, so it opens read-only here' };
  return renderDropped(name, decoded.text);
}
