/* A confirm sheet over the document: one question, its choices, and × to back out.
 *
 * Built on demand so Explore's sheet and the /doc window share one copy;
 * it opens above an open sheet (the top layer stacks dialogs). Resolves with
 * the chosen action's value, or null when closed without one.
 */

import { esc, glyph } from './fmt.js';

/**
 * Ask ``title`` with ``text`` and ``actions`` (``[{value, label, kind}]``,
 * ``kind`` one of primary, surface or danger, primary last); ``view`` names
 * the sheet for the usage log.
 */
export function confirmSheet({ title, text, actions, view }) {
  const d = document.createElement('dialog');
  d.className = 'detail-dialog doc-confirm';
  // dynamic-id-exempt: every caller passes one fixed literal (doc-leave, doc-overwrite, doc-discard), so each sheet's id is constant
  d.dataset.trackView = view;
  d.setAttribute('aria-labelledby', view + '-title');
  const cls = { primary: 'button-primary', surface: 'button-surface', danger: 'button-tint danger' };
  d.innerHTML = '<div class="detail-card"><div class="detail-header"><h2 tabindex="-1" id="' + view + '-title">' + esc(title) + '</h2>' +
    '<button type="button" class="icon-button detail-close" aria-label="Close" data-track="' + view + '-close" data-confirm="">' +
    glyph('x') + '</button></div><div class="prose"><p>' + esc(text) + '</p></div><div class="detail-actions doc-confirm-actions">' +
    actions.map((a) => '<button type="button" class="' + cls[a.kind] + '" data-confirm="' + esc(a.value) + '" data-track="' + view + '-' +
      esc(a.value) + '">' + esc(a.label) + '</button>').join('') + '</div></div>';
  document.body.appendChild(d);
  return new Promise((resolve) => {
    let chosen = null;
    d.addEventListener('click', (e) => {
      const b = e.target.closest('[data-confirm]');
      if (b) { chosen = b.dataset.confirm || null; d.close(); } else if (e.target === d) d.close();
    });
    d.addEventListener('close', () => { d.remove(); resolve(chosen); });
    d.showModal();
  });
}

/** The question before leaving a document with unsaved changes: 'save', 'keep', 'discard', or null to stay. */
export const askLeave = () => confirmSheet({
  title: 'Leave with unsaved changes?',
  text: 'Your draft stays on this device until you save or discard it.',
  view: 'doc-leave',
  actions: [{ value: 'discard', label: 'Discard', kind: 'danger' }, { value: 'keep', label: 'Keep draft', kind: 'surface' },
    { value: 'save', label: 'Save', kind: 'primary' }],
});
