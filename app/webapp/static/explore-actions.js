/* Explore's item actions: each row's menu, double-click to open on this PC, and Copy path on every device.
 * Off the PC the menu says opening works there, instead of showing Copy path alone.
 * A Markdown file opens in the app's own viewer instead, on every device: a tap
 * on a touch screen or a double-click, and View, Edit here or Open in new window in its menu.
 *
 * Opening asks the server, which looks the item up by its id in the scan the
 * page shows and refuses any caller but this PC; the page never sends a path.
 */

import { postJSON } from './api.js';
import { esc, glyph } from './fmt.js';
import { copyText } from './search-state.js';
import { rootInfo, state } from './state.js';
import { docUrl } from './doc-view.js';
import { openDoc } from './doc-sheet.js';
import { createRowMenu } from './_vendored/row-menu/row-menu.js';
import { showToast } from './_vendored/toast/toast.js';

// The second press of a double-click can land on a row that the first press
// re-rendered, so the first press names the item; this bounds how stale it may be.
const DOUBLE_MS = 800;
const DONE = { explore: 'Opened in File Explorer', open: 'Opening', edit: 'Opening to edit', reveal: 'Shown in File Explorer' };

// The one line a menu opens with when this device can't open anything, so Copy path isn't alone without a reason.
const NOT_HERE = 'Opening files and folders works on the PC';
const DOC_EXTS = new Set(['md', 'markdown']);
// A touch screen has no double-click, so one tap opens a Markdown row there.
const TOUCH = window.matchMedia('(pointer: coarse)');

let source = () => null;
const menus = new Map();
let press = null;

const canOpen = () => !!(source() && source().can_open);

/** ``getContents`` returns the contents answer the rows were drawn from. */
export function initExploreActions(getContents) {
  source = getContents;
  document.addEventListener('click', (e) => {
    if (e.detail !== 1) return;
    const el = e.target.closest('[data-open]');
    press = el ? { kind: el.dataset.open, id: Number(el.dataset.id), doc: !!el.dataset.doc, at: e.timeStamp } : null;
    if (press && press.doc && TOUCH.matches && !e.target.closest('[data-kebab]')) viewDoc(press.id);
  }, true);
  document.addEventListener('dblclick', (e) => {
    const p = press;
    press = null;
    if (!p || e.timeStamp - p.at > DOUBLE_MS) return;
    if (p.doc) viewDoc(p.id);
    else if (canOpen()) openItem(p.kind, p.id, p.kind === 'folder' ? 'explore' : 'open');
  });
}

/** True for a file the viewer reads: Markdown, by its extension. */
export const isDocument = (it) => it.kind !== 'folder' && DOC_EXTS.has(String(it.ext || '').toLowerCase());

const docLoc = (id) => {
  const it = source().items.find((x) => x.kind === 'file' && x.id === id);
  return { root: state.root, scan_id: source().scan_id, id, name: it ? it.name : '' };
};

function viewDoc(id, edit) {
  if (source()) openDoc(docLoc(id), edit);
}

export async function openItem(kind, id, verb) {
  const c = source();
  if (!c) return;
  try {
    await postJSON('/api/open', { root: state.root, scan_id: c.scan_id, kind, id, verb });
    showToast(DONE[verb]);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

/** The item's full path on disk: the root's path, the folders down to it, then its name. */
function itemPath(it) {
  const c = source();
  const r = rootInfo();
  const below = c.path.slice(1).map((x) => x.name);
  const nested = it.kind === 'file' && c.mode === 'nested' ? it.path || [] : [];
  return [r.path.replace(/[\\/]+$/, ''), ...below, ...nested, it.name].join('\\');
}

function menuItems(it) {
  const off = () => !canOpen();
  const run = (verb) => () => openItem(it.kind, it.id, verb);
  const copy = {
    label: 'Copy path', glyph: 'copy',
    onTap: async () => showToast((await copyText(itemPath(it))) ? 'Path copied' : 'Copy isn\'t allowed here'),
  };
  if (it.kind === 'folder') {
    return [{ label: 'Open in File Explorer', glyph: 'folder-open', hidden: off, onTap: run('explore') }, copy];
  }
  if (isDocument(it)) {
    return [
      { label: 'View', glyph: 'file-text', onTap: () => viewDoc(it.id) },
      { label: 'Edit here', glyph: 'pencil', onTap: () => viewDoc(it.id, true) },
      { label: 'Open in new window', glyph: 'external-link', onTap: () => window.open(docUrl(docLoc(it.id)), '_blank', 'noopener') },
      { label: 'Open in default app', glyph: 'external-link', hidden: off, onTap: run('open') },
      { label: 'Show in File Explorer', glyph: 'folder-open', hidden: off, onTap: run('reveal') },
      copy,
    ];
  }
  return [
    { label: 'Open', glyph: 'external-link', hidden: off, onTap: run('open') },
    { label: 'Edit', glyph: 'pencil', hidden: off, onTap: run('edit') },
    { label: 'Show in File Explorer', glyph: 'folder-open', hidden: off, onTap: run('reveal') },
    copy,
  ];
}

/** The row's trailing kebab; ``i`` is the item's place in the contents answer. */
export const kebab = (i, name) => '<button type="button" class="icon-button row-kebab" data-kebab="' + i +
  '" aria-label="Actions for ' + esc(name) + '" data-track="contents-row-menu">' + glyph('ellipsis-vertical') + '</button>';

/** Give every kebab in ``container`` its menu, once the rows are drawn. */
export function wireRowMenus(container) {
  const c = source();
  if (!container || !c) return;
  let menu = menus.get(container.id);
  if (!menu) {
    menu = createRowMenu();
    menus.set(container.id, menu);
  }
  container.querySelectorAll('[data-kebab]').forEach((btn) => {
    const it = c.items[Number(btn.dataset.kebab)];
    if (it) menu.attach(it.kind + ':' + it.id, btn, menuItems(it), { header: () => (canOpen() || isDocument(it) ? '' : NOT_HERE) });
  });
  menu.endRender();
}
