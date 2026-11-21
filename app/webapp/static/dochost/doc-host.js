/* The view/edit controller Explore's document sheet and the /doc window share.
 *
 * One state machine owns the mode (View or Edit), the open editor, the dropped
 * file's handle (``local``, set only for a file no scan covers) and the
 * race guard for a slower load. A host (doc-sheet.js, doc-page.js) passes its
 * root element, body, Save button and the few things only it knows: what to
 * hide while editing, how to redraw the indexed document, and what to draw
 * around a local file once its body is on screen.
 */

import { drawDoc, releaseDoc, stateHtml } from '../doc-view.js';
import { openEditor, saveButtonHooks } from '../doc-edit.js';
import { renderLocal, withLocal } from '../localdoc/local-doc.js';
import { showToast } from '../_vendored/toast/toast.js';

const LOADING = '<p class="doc-loading" role="status">Loading…</p>';

function markMode(root, saveBtn, mode) {
  root.querySelectorAll('.doc-mode .segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
  root.classList.toggle('is-editing', mode === 'edit');
  saveBtn.hidden = mode !== 'edit';
  saveBtn.disabled = true;
}

/** The View | Edit switch and the Save button, wired to the controller's ``host``. */
function wireControls(root, saveBtn, host) {
  root.querySelector('.doc-mode').addEventListener('click', (e) => {
    const b = e.target.closest('[data-mode]');
    if (b && b.dataset.mode === 'edit') host.startEditing();
    else if (b) host.stopEditing();
  });
  saveBtn.addEventListener('click', () => { if (host.editor) host.editor.save(); });
}

/**
 * ``root`` holds the ``.doc-mode`` switch and takes the ``is-editing`` class; ``body`` is the document's container.
 * Hooks: ``getLoc()`` the indexed document on show (or null), ``onEditStart()`` hides what Edit replaces,
 * ``redraw()`` shows the indexed document again, ``onLocalDrawn(body)`` follows a local file's draw.
 */
export function createDocHost({ root, body, saveBtn, getLoc, onEditStart, redraw, onLocalDrawn }) {
  let editor = null;
  // A dropped file the browser gave a handle for: ``{handle, name}``; null otherwise.
  let local = null;
  // Each load's number: a slower answer to an earlier load never draws over a later one.
  let seq = 0;

  const setMode = (mode) => markMode(root, saveBtn, mode);

  function startEditing() {
    const target = getLoc() || local;
    if (editor || !target) return;
    seq += 1;
    setMode('edit');
    onEditStart();
    releaseDoc(body);
    editor = openEditor(body, target, saveButtonHooks(saveBtn));
  }

  /** View of a local file: its copy on disk now, read through the handle and rendered by the server. */
  async function viewLocal() {
    const started = local;
    const mine = ++seq;
    body.innerHTML = LOADING;
    const r = await renderLocal(started.handle, started.name);
    if (mine !== seq || local !== started) return;
    if (!r.ok) {
      body.innerHTML = stateHtml(r);
      return;
    }
    drawDoc(body, withLocal(r.body, started.handle));
    await onLocalDrawn(r.body);
  }

  /** Back to View: the draft, if any, stays on the device and the host shows the copy on disk. */
  function stopEditing() {
    if (!editor) return;
    if (editor.dirty()) showToast('Your unsaved draft is kept on this device');
    editor.close();
    editor = null;
    setMode('view');
    if (local) viewLocal();
    else redraw();
  }

  /** Leave the editor for another document: asks first when it holds unsaved changes; false when the reader chose to stay. */
  async function leaveEditor() {
    if (!editor) return true;
    if (!(await editor.leave())) return false;
    editor.close();
    editor = null;
    setMode('view');
    return true;
  }

  const host = {
    get editor() { return editor; },
    get local() { return local; },
    setLocal(next) { local = next; },
    startEditing,
    stopEditing,
    viewLocal,
    leaveEditor,
    /** A new load starts: the number to hand ``stale`` once its answer comes. */
    begin: () => ++seq,
    stale: (ticket) => ticket !== seq,
    /** The host is going away: stop what is in flight, drop the editor and the handle, show View. */
    reset() {
      seq += 1;
      if (editor) { editor.close(); editor = null; }
      local = null;
      setMode('view');
    },
    /** A dropped file the scan doesn't have, already rendered: leave the editor, keep its handle (null: read-only) and draw it; false when the reader chose to stay. */
    async showDropped(doc, handle) {
      if (!(await leaveEditor())) return false;
      seq += 1;
      local = handle ? { handle, name: doc.name } : null;
      setMode('view');
      drawDoc(body, withLocal(doc, handle));
      return true;
    },
    wire: () => wireControls(root, saveBtn, host),
  };
  return host;
}
