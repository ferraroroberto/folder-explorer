/* Explore's document sheet: a Markdown file read, or edited, in a full-height sheet, with its outline, links and a way out to its own window.
 *
 * A relative link to another scanned Markdown file opens in the same sheet,
 * and Back returns to the one before. View | Edit switches to the editor;
 * Esc goes back to View, and closing the sheet with unsaved changes asks
 * first. The sheet never sends a path: every document is named by root,
 * scan id and item id.
 */

import { docUrl, drawDoc, fetchDoc, outlineHtml, releaseDoc, scrollToHeading, stateHtml, wireDocHost } from './doc-view.js';
import { openEditor } from './doc-edit.js';
import { showToast } from './_vendored/toast/toast.js';

const $ = (id) => document.getElementById(id);
const back = [];
let current = null;
let wired = false;
let editor = null;
// Each load's number: a slower answer to an earlier load never draws over a later one.
let seq = 0;

function toggleOutline(open) {
  const on = open === undefined ? $('docOutline').hidden : open;
  $('docOutline').hidden = !on;
  $('docOutlineBtn').setAttribute('aria-expanded', String(on));
}

function markMode(mode) {
  document.querySelectorAll('#docDialog .doc-mode .segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
  $('docDialog').classList.toggle('is-editing', mode === 'edit');
  $('docSave').hidden = mode !== 'edit';
  $('docSave').disabled = true;
}

function startEditing() {
  if (editor || !current) return;
  seq += 1;
  markMode('edit');
  $('docBack').hidden = true;
  $('docOutlineBtn').hidden = true;
  toggleOutline(false);
  releaseDoc($('docSheetBody'));
  editor = openEditor($('docSheetBody'), current, {
    onDirty: (dirty) => { $('docSave').disabled = !dirty; },
    onReady: (editable) => { if (!editable) $('docSave').hidden = true; },
  });
}

/** Back to View: the draft, if any, stays on the device and the sheet shows the copy on disk. */
function stopEditing() {
  if (!editor) return;
  if (editor.dirty()) showToast('Your unsaved draft is kept on this device');
  editor.close();
  editor = null;
  markMode('view');
  show(current);
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
  $('docSave').addEventListener('click', () => { if (editor) editor.save(); });
  dialog.querySelector('.doc-mode').addEventListener('click', (e) => {
    const b = e.target.closest('[data-mode]');
    if (b && b.dataset.mode === 'edit') startEditing();
    else if (b) stopEditing();
  });
  // Closing while editing (×, a tap outside) asks first when there are unsaved changes; this runs before the app's own closer.
  dialog.addEventListener('click', (e) => {
    if (!editor || !(e.target === dialog || e.target.closest('[data-close]'))) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    editor.leave().then((go) => { if (go) dialog.close(); });
  }, true);
  dialog.addEventListener('cancel', (e) => {
    if (!editor) return;
    e.preventDefault();
    stopEditing();
  });
  dialog.addEventListener('close', () => {
    seq += 1;
    if (editor) { editor.close(); editor = null; }
    markMode('view');
    releaseDoc($('docSheetBody'));
    back.length = 0;
    current = null;
    toggleOutline(false);
  });
}

async function show(loc, download) {
  const mine = ++seq;
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
  if (mine !== seq) return;
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

/** Open the sheet on a document: ``loc`` is ``{root, scan_id, id}`` and, for the title while it loads, ``name``; ``edit`` opens the editor. */
export function openDoc(loc, edit) {
  wire();
  back.length = 0;
  if (edit) {
    show(loc).then(startEditing);
  } else {
    show(loc);
  }
}
