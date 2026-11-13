/* Open a Markdown or HTML file dropped on the window: in its own /doc window from the app, in place in the /doc window.
 *
 * A drop gives the page the file's bytes and name, never its path, so the
 * file is looked up by what the page knows: name, size, modified time and a
 * SHA-256 of the bytes (``POST /api/doc/dropped/match``). The one file on this
 * PC with those bytes, scanned or not, opens like any other document, with
 * Edit; otherwise the page reads the text and has it rendered read-only
 * (``POST /api/doc/dropped/render``) and a toast says why. A drop of anything
 * else says so in a toast. The host decides how each opens: ``host.open(loc)`` for an
 * indexed document, ``host.show(doc)`` for a rendered one. A host with
 * ``ownWindow`` opens each in a new /doc window, like Explore's "Open in new
 * window"; if the browser blocks that window, the same two calls show it in
 * place and a toast says why.
 */

import { api } from './api.js';
import { NO_ANSWER, refusal } from './doc-answer.js';
import { dragHasFiles, matchBody, pickDrop, renderBody, toHex, whyNote } from './drop-files.js';
import { docUrl } from './doc-view.js';
import { handOff } from './doc-handoff.js';
import { glyph } from './fmt.js';
import { showToast } from './_vendored/toast/toast.js';

const JSON_HEAD = { 'Content-Type': 'application/json' };

async function post(path, body) {
  let res;
  try {
    res = await api(path, { method: 'POST', headers: JSON_HEAD, body: JSON.stringify(body) });
  } catch (e) {
    return { ok: false, ...NO_ANSWER };
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  return res.ok ? { ok: true, body: data } : { ok: false, ...refusal(res.status, data) };
}

/** The SHA-256 of the dropped bytes as hex, or '' where the browser has no Web Crypto (a page not served securely) or can't read the file. */
async function digest(file) {
  try {
    if (!globalThis.crypto || !crypto.subtle) return '';
    return toHex(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())));
  } catch (e) {
    return '';
  }
}

/** What a dropped ``file`` is: ``{kind: 'indexed', loc}``, ``{kind: 'dropped', doc, note}`` or ``{kind: 'refused', message}``. */
export async function resolveDrop(file) {
  const found = await post('/api/doc/dropped/match', matchBody(file, await digest(file)));
  if (!found.ok) return { kind: 'refused', message: found.message };
  if (found.body.match) return { kind: 'indexed', loc: { ...found.body.match, name: file.name } };
  let text;
  try {
    text = await file.text();
  } catch (e) {
    return { kind: 'refused', message: 'The browser could not read the dropped file' };
  }
  const shown = await post('/api/doc/dropped/render', renderBody(file, text));
  return shown.ok ? { kind: 'dropped', doc: shown.body, note: whyNote(found.body.why) } : { kind: 'refused', message: shown.message };
}

function overlay() {
  const el = document.createElement('div');
  el.className = 'drop-overlay';
  el.setAttribute('aria-hidden', 'true');
  el.innerHTML = '<div class="drop-card">' + glyph('file-text') + '<p class="drop-title">Drop a Markdown or HTML file to open it</p>' +
    '<p class="drop-text">It opens for editing when the scan has it, and read-only when it doesn\'t.</p></div>';
  // The top layer keeps it above an open sheet; a browser without popovers shows it as a fixed layer.
  if (typeof el.showPopover === 'function') el.setAttribute('popover', 'manual');
  document.body.appendChild(el);
  return el;
}

const BLOCKED = 'The browser blocked the new window, so the file opened here. Allow pop-ups for this app to open it in its own window.';
const BLOCKED_MS = 6000;

/** The blocked-window note, above the sheet it just opened: a modal sheet is in the top layer, which no z-index beats, so the toast joins it as a popover. */
function toastAboveSheet(message) {
  const el = showToast(message, undefined, { ms: BLOCKED_MS });
  if (typeof el.showPopover !== 'function') return;
  el.setAttribute('popover', 'manual');
  el.showPopover();
  // Back to a plain toast, so the next one raised from the page is not left in the top layer.
  setTimeout(() => el.removeAttribute('popover'), BLOCKED_MS);
}

/** Show a resolved drop in the window ``popup`` that was opened for it; false when it can't (the caller closes the window). */
function showInWindow(popup, r) {
  if (r.kind === 'indexed') popup.location.href = docUrl(r.loc);
  else if (r.kind === 'dropped') handOff(popup, r.doc);
  else return false;
  if (r.note) showToast(r.note);
  return true;
}

/** Wire the whole window as a drop target for Markdown and HTML files; ``host`` is ``{open(loc), show(doc), ownWindow?}``. */
export function initDrop(host) {
  const el = overlay();
  const popover = el.hasAttribute('popover');
  let depth = 0;
  let busy = false;
  const toggle = (on) => {
    if (popover) {
      if (on && !el.matches(':popover-open')) el.showPopover();
      else if (!on && el.matches(':popover-open')) el.hidePopover();
    } else {
      el.classList.toggle('is-open', on);
    }
  };
  const reset = () => { depth = 0; toggle(false); };

  window.addEventListener('dragenter', (e) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    depth += 1;
    toggle(true);
  });
  window.addEventListener('dragover', (e) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();  // without this the browser would open the file in the tab
    e.dataTransfer.dropEffect = 'copy';
  });
  window.addEventListener('dragleave', (e) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) toggle(false);
  });
  window.addEventListener('drop', async (e) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    reset();
    const picked = pickDrop(e.dataTransfer.files);
    if (!picked.file) { showToast(picked.message, 'error'); return; }
    if (busy) return;
    busy = true;
    // Opened now, while the drop still counts as the reader's own act, and pointed at the file once it is resolved.
    const popup = host.ownWindow ? window.open('', '_blank') : null;
    try {
      const r = await resolveDrop(picked.file);
      if (popup && showInWindow(popup, r)) return;
      if (popup) popup.close();
      if (r.kind === 'refused') showToast(r.message, 'error');
      else {
        // The sheet is open once the host answers, so the note goes on top of it.
        await (r.kind === 'indexed' ? host.open(r.loc) : host.show(r.doc));
        const notes = [r.note, host.ownWindow && BLOCKED].filter(Boolean);
        if (notes.length) toastAboveSheet(notes.join(' '));
      }
    } finally {
      busy = false;
    }
  });
}
