/* Explore's document sheet: a Markdown or HTML file read, or edited, in a full-height sheet, with its outline, links and a way out to its own window.
 *
 * A relative link to another scanned document opens in the same sheet,
 * and Back returns to the one before. View | Edit switches to the editor;
 * Esc goes back to View, and closing the sheet with unsaved changes asks
 * first. The sheet never sends a path: every document is named by root,
 * scan id and item id, or, for a dropped file the scan doesn't have, held by
 * the browser's handle for it (local-doc.js), which has Edit and saves through
 * that handle; without a handle it is read-only.
 */

import { docUrl, drawDoc, fetchDoc, outlineHtml, releaseDoc, scrollToHeading, stateHtml, wireDocHost } from './doc-view.js';
import { createDocHost } from './dochost/doc-host.js';

const $ = (id) => document.getElementById(id);
const back = [];
let current = null;
let wired = false;
// The view/edit lifecycle (doc-host.js); its ``local`` is a dropped file's handle, and ``current`` is then null.
const host = createDocHost({
  root: $('docDialog'),
  body: $('docSheetBody'),
  saveBtn: $('docSave'),
  getLoc: () => current,
  onEditStart: () => {
    $('docBack').hidden = true;
    $('docOutlineBtn').hidden = true;
    toggleOutline(false);
  },
  redraw: () => show(current),
  onLocalDrawn: (body) => {
    $('docOutline').innerHTML = outlineHtml(body.outline);
    $('docOutlineBtn').hidden = !body.outline.length;
    $('docSheetBody').scrollTop = 0;
  },
});

function toggleOutline(open) {
  const on = open === undefined ? $('docOutline').hidden : open;
  $('docOutline').hidden = !on;
  $('docOutlineBtn').setAttribute('aria-expanded', String(on));
}

function wire() {
  if (wired) return;
  wired = true;
  const dialog = $('docDialog');
  wireDocHost(dialog, $('docSheetBody'), {
    onDocLink: (loc) => { back.push(current); show(loc); },
    onDownload: () => show(current, true),
    onExplore: () => dialog.close(),
    onAnchor: () => toggleOutline(false),
    getLoc: () => current,
  });
  $('docBack').addEventListener('click', () => { if (back.length) show(back.pop()); });
  $('docOutlineBtn').addEventListener('click', () => toggleOutline());
  host.wire();
  // Closing while editing (×, a tap outside) asks first when there are unsaved changes; this runs before the app's own closer.
  dialog.addEventListener('click', (e) => {
    if (!host.editor || !(e.target === dialog || e.target.closest('[data-close]'))) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    host.editor.leave().then((go) => { if (go) dialog.close(); });
  }, true);
  dialog.addEventListener('cancel', (e) => {
    if (!host.editor) return;
    e.preventDefault();
    host.stopEditing();
  });
  dialog.addEventListener('close', () => {
    host.reset();
    dialog.classList.remove('is-dropped', 'is-local');
    releaseDoc($('docSheetBody'));
    back.length = 0;
    current = null;
    toggleOutline(false);
  });
}

async function show(loc, download) {
  const mine = host.begin();
  $('docDialog').classList.remove('is-dropped', 'is-local');
  host.setLocal(null);
  current = { root: loc.root, scan_id: loc.scan_id, id: loc.id, name: loc.name };
  $('docBack').hidden = !back.length;
  $('docWindow').href = docUrl(current);
  $('docDialogTitle').textContent = loc.name || 'Document';
  $('docWhere').textContent = '';
  $('docOutlineBtn').hidden = true;
  toggleOutline(false);
  $('docSheetBody').innerHTML = '<p class="doc-loading" role="status">' + (download ? 'Downloading to this PC…' : 'Loading…') + '</p>';
  if (!$('docDialog').open) $('docDialog').showModal();
  const r = await fetchDoc(current, download);
  if (host.stale(mine)) return;
  if (!r.ok) {
    $('docSheetBody').innerHTML = stateHtml(r);
    return;
  }
  current.name = r.doc.name;
  $('docDialogTitle').textContent = r.doc.name;
  $('docWhere').textContent = r.doc.folder.join(' › ');
  $('docOutline').innerHTML = outlineHtml(r.doc.outline);
  $('docOutlineBtn').hidden = !r.doc.outline.length;
  drawDoc($('docSheetBody'), r.doc);
  $('docSheetBody').scrollTop = 0;
  if (loc.fragment) scrollToHeading($('docSheetBody'), loc.fragment);
}

/** A dropped file that the scan has: opened like any indexed document, Edit included. */
export async function openIndexed(loc) {
  wire();
  if (await host.leaveEditor()) openDoc(loc);
}

/** A dropped file the scan doesn't have, already rendered by the server: with no id to link from or open in a window, and Edit only when the browser gave its ``handle``. */
export async function openDropped(doc, handle) {
  wire();
  if (!(await host.showDropped(doc, handle))) return;
  back.length = 0;
  current = null;
  $('docDialog').classList.add('is-dropped');
  $('docDialog').classList.toggle('is-local', !!host.local);
  $('docBack').hidden = true;
  $('docDialogTitle').textContent = doc.name;
  $('docWhere').textContent = '';
  $('docOutline').innerHTML = outlineHtml(doc.outline);
  $('docOutlineBtn').hidden = !doc.outline.length;
  toggleOutline(false);
  if (!$('docDialog').open) $('docDialog').showModal();
  $('docSheetBody').scrollTop = 0;
}

/** Open the sheet on a document: ``loc`` is ``{root, scan_id, id}`` and, for the title while it loads, ``name``; ``edit`` opens the editor. */
export function openDoc(loc, edit) {
  wire();
  back.length = 0;
  if (edit) {
    show(loc).then(host.startEditing);
  } else {
    show(loc);
  }
}
