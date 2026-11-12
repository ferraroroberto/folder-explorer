/* The editor's conflict screen: the file changed on disk since it was opened, so Save stopped.
 *
 * Nothing is lost here: the editor keeps the reader's text. The screen offers
 * Compare (a line diff of the copy on disk against theirs), Copy my text, Load
 * theirs, and Overwrite theirs…, which asks first and leaves the replaced copy
 * as a backup in the app's data folder.
 */

import { esc, glyph } from './fmt.js';
import { confirmSheet } from './doc-confirm.js';
import { lineDiff } from './md-source.js';
import { copyText } from './search-state.js';
import { showToast } from './_vendored/toast/toast.js';

const MARK = { '=': ' ', '-': '−', '+': '+' };

function diffHtml(theirs, mine) {
  const rows = lineDiff(theirs, mine);
  if (!rows.some((r) => r.op !== '=')) return '<p class="doc-diff-same">The two copies have the same text.</p>';
  return '<p class="doc-diff-key"><span class="doc-diff-del">− On disk</span><span class="doc-diff-add">+ Yours</span></p>' +
    '<pre class="doc-diff" tabindex="0" aria-label="Differences">' + rows.map((r) => '<span class="doc-diff-' +
      ({ '=': 'same', '-': 'del', '+': 'add' }[r.op]) + '">' + MARK[r.op] + ' ' + esc(r.text) + '</span>').join('\n') + '</pre>';
}

/**
 * Show the conflict in ``slot``. ``c`` carries ``theirs`` (the text on disk, or
 * null when it can no longer be edited), ``mine()`` (the editor's text now),
 * ``onLoadTheirs()`` and ``onOverwrite()``.
 */
export function showConflict(slot, c) {
  slot.innerHTML = '<div class="doc-conflict" role="alert"><p class="doc-conflict-title">' + glyph('triangle-alert') +
    '<span>Changed on disk since you opened it</span></p><p class="doc-conflict-text">Your text is still here and has not been saved.</p>' +
    '<div class="doc-conflict-actions">' +
    '<button type="button" class="button-surface" data-conflict="compare" aria-expanded="false" data-track="doc-conflict-compare">Compare</button>' +
    '<button type="button" class="button-surface" data-conflict="copy" data-track="doc-conflict-copy">Copy my text</button>' +
    '<button type="button" class="button-surface" data-conflict="theirs" data-track="doc-conflict-theirs"' +
    (c.theirs === null ? ' disabled title="The copy on disk can no longer be edited here"' : '') + '>Load theirs</button>' +
    '<button type="button" class="button-tint danger" data-conflict="overwrite" data-track="doc-conflict-overwrite">Overwrite theirs…</button>' +
    '</div><div class="doc-conflict-diff" hidden></div></div>';
  slot.hidden = false;
  const diff = slot.querySelector('.doc-conflict-diff');
  slot.onclick = async (e) => {
    const b = e.target.closest('[data-conflict]');
    if (!b || b.disabled) return;
    const act = b.dataset.conflict;
    if (act === 'compare') {
      const open = diff.hidden;
      if (open) diff.innerHTML = diffHtml(c.theirs === null ? '' : c.theirs, c.mine());
      diff.hidden = !open;
      b.setAttribute('aria-expanded', String(open));
    } else if (act === 'copy') {
      const ok = await copyText(c.mine());
      showToast(ok ? 'Your text is copied' : 'Could not copy', ok ? undefined : 'error');
    } else if (act === 'theirs') {
      c.onLoadTheirs();
    } else if (act === 'overwrite') {
      const ok = await confirmSheet({
        title: 'Overwrite the copy on disk?',
        text: 'Your text replaces it. The copy on disk now is kept as a backup in the app\'s data folder.',
        view: 'doc-overwrite',
        actions: [{ value: 'overwrite', label: 'Overwrite', kind: 'danger' }],
      });
      if (ok === 'overwrite') c.onOverwrite();
    }
  };
}

/** Put the conflict screen away. */
export function hideConflict(slot) {
  slot.hidden = true;
  slot.innerHTML = '';
  slot.onclick = null;
}
