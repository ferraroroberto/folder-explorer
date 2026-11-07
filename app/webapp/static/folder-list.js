/* One folder list Settings edits: the saved paths as rows, Remove in each row's menu, and an add row.
 *
 * The scan roots, the skip list, the email folders and the email excludes all
 * use it, so each is added to and removed from the same way. The list holds
 * no state of its own: ``onSave(next)`` saves the whole new list and resolves
 * to an error sentence, or null once the server took it and the caller has
 * re-rendered the lists from its answer.
 */

import { createRowMenu } from './_vendored/row-menu/row-menu.js';
import { esc } from './fmt.js';

// The row's title: a path's last folder, which a long path would otherwise cut off.
const folderName = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;

const glyph = (name) => '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';

/** Ask before a remove: the one confirm sheet every list shares. */
function confirmRemove(path) {
  const d = document.getElementById('folderRemoveDialog');
  d.querySelector('[data-remove-path]').textContent = path;
  return new Promise((resolve) => {
    const ok = d.querySelector('[data-remove-ok]');
    const done = (yes) => { ok.removeEventListener('click', onOk); d.removeEventListener('close', onClose); resolve(yes); };
    const onOk = () => { d.close(); done(true); };
    const onClose = () => done(false);
    ok.addEventListener('click', onOk);
    d.addEventListener('close', onClose, { once: true });
    d.showModal();
  });
}

/**
 * Build a list into ``host``.
 * ``name``: the data-track stem (``roots``, ``exclude``, ``email-roots``, ``email-exclude``);
 * ``label``: the add field's accessible name; ``empty``: the line when the list is empty.
 */
export function folderList(host, { name, label, empty, onSave }) {
  host.classList.add('folder-list');
  host.dataset.list = name;
  host.innerHTML =
    '<ul class="action-rows folder-rows"></ul>' +
    '<form class="folder-add" novalidate>' +
    '<input class="input-native" type="text" spellcheck="false" autocomplete="off" placeholder="Add a folder path" aria-label="' +
    esc(label) + '" data-track="' + name + '-add-input">' +
    '<button type="submit" class="button-surface" data-track="' + name + '-add">' + glyph('plus') + 'Add</button>' +
    '</form><p class="row-note folder-error" role="alert"></p>';
  const rows = host.querySelector('.folder-rows');
  const form = host.querySelector('.folder-add');
  const input = form.querySelector('input');
  const error = host.querySelector('.folder-error');
  const menu = createRowMenu();
  let paths = [];

  async function commit(next) {
    error.textContent = '';
    const problem = await onSave(next);
    if (problem) error.textContent = problem;
    return !problem;
  }

  function render(list) {
    paths = list.slice();
    rows.innerHTML = paths.length ? paths.map((p, i) =>
      '<li class="action-row"><span class="action-row-main"><span class="action-row-title" title="' + esc(p) + '">' +
      esc(folderName(p)) + '</span><span class="action-row-meta mono">' + esc(p) + '</span></span><button type="button" class="icon-button action-row-kebab" data-i="' + i + '" aria-label="Actions for ' +
      esc(p) + '" data-track="' + name + '-row-menu">' + glyph('ellipsis-vertical') + '</button></li>').join('')
      : '<li class="rows-note folder-empty">' + esc(empty) + '</li>';
    rows.querySelectorAll('[data-i]').forEach((btn) => {
      const path = paths[Number(btn.dataset.i)];
      menu.attach(path, btn, [{
        label: 'Remove', glyph: 'minus', danger: true,
        onTap: async () => { if (await confirmRemove(path)) commit(paths.filter((x) => x !== path)); },
      }]);
    });
    menu.endRender();
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const path = input.value.trim();
    if (!path) { error.textContent = 'Type a folder path first.'; return; }
    if (await commit([...paths, path])) input.value = '';
  });

  return { render, clearError: () => { error.textContent = ''; } };
}
