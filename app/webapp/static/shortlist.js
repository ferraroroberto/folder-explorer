/* The cleanup shortlist: Largest's bookmark marks a file, and the sheet lists every marked file with its size
 * now, Show in Explore and Remove.
 *
 * The list lives on the server (state.sqlite3), so the phone and the PC share it, and an entry is kept by its
 * names below its root, so it outlives a rescan; a file the latest scan no longer has stays listed as gone.
 */

import { deleteJSON, getJSON, postJSON } from './api.js';
import { state } from './state.js';
import { cloudMark, esc, fmtBytes, glyph, plural } from './fmt.js';
import { showToast } from './_vendored/toast/toast.js';

const $ = (id) => document.getElementById(id);

let list = { items: [], size: 0 };
// "root:scan:file id" of every entry the current scans still hold, to its entry id.
let marked = new Map();
let showInExplore = () => {};

const markKey = (root, scanId, fileId) => root + ':' + scanId + ':' + fileId;
const entryFor = (fileId) => marked.get(markKey(state.root, state.scanId, fileId));

function fail(e) {
  showToast(e.message || 'Could not update the shortlist', 'error');
}

function take(body) {
  list = body;
  marked = new Map(body.items.filter((i) => i.file).map((i) => [markKey(i.root, i.scan_id, i.file.id), i.id]));
  document.querySelectorAll('.row-mark').forEach(paintMark);
  renderOpeners();
  if ($('shortlistDialog').open) renderSheet();
}

export async function loadShortlist() {
  take(await getJSON('/api/shortlist'));
}

// One name in both states: aria-pressed carries on or off, so a screen reader hears a toggle, not an Add or Remove action.
const markLabel = (name) => 'Shortlist ' + name;

function paintMark(btn) {
  const on = entryFor(Number(btn.dataset.id)) !== undefined;
  btn.setAttribute('aria-pressed', String(on));
  btn.setAttribute('aria-label', markLabel(btn.dataset.name));
}

/** The bookmark toggle a Largest row ends with. */
export function markButton(f) {
  const on = entryFor(f.id) !== undefined;
  return '<button type="button" class="icon-button row-mark" data-id="' + f.id + '" data-name="' + esc(f.name) + '" aria-pressed="' + on +
    '" aria-label="' + esc(markLabel(f.name)) + '" title="Shortlist" data-track="largest-review">' +
    glyph('bookmark') + '</button>';
}

async function toggle(btn) {
  const fileId = Number(btn.dataset.id);
  const entry = entryFor(fileId);
  btn.disabled = true;
  try {
    take(entry !== undefined ? await deleteJSON('/api/shortlist/' + entry)
      : await postJSON('/api/shortlist', { root: state.root, scan_id: state.scanId, id: fileId }));
  } catch (e) {
    fail(e);
  } finally {
    btn.disabled = false;
  }
}

/** The shortlist's line as words plus the figures that end it, so a row can keep the figures whole at phone width. */
function summaryParts() {
  const here = list.items.filter((i) => i.file).length;
  if (!list.items.length) return { text: 'Mark files in Largest to review them here.', tail: [] };
  return { text: plural(list.items.length, 'file'), tail: [fmtBytes(list.size)].concat(here < list.items.length ? [(list.items.length - here) + ' gone'] : []) };
}

const summary = () => { const m = summaryParts(); return [m.text].concat(m.tail).join(' · '); };

function renderOpeners() {
  const m = summaryParts();
  const html = '<span class="action-row-meta-text">' + esc(m.text) + '</span>' + m.tail.map((t) => '<span class="action-row-meta-tail">' + esc(t) + '</span>').join('');
  document.querySelectorAll('[data-shortlist-meta]').forEach((el) => { el.innerHTML = html; });
}

function entryRow(i) {
  const where = [i.root_name].concat(i.folder).join(' › ');
  const size = i.file ? i.file.size : i.size;
  const meta = i.file ? where : 'Not in the latest scan · was in ' + where;
  return '<li class="row-item' + (i.file ? '' : ' is-gone') + '"><div class="list-row">' + glyph(i.file ? 'file-text' : 'triangle-alert') +
    '<span class="lr-name"><span>' + esc(i.name) + '</span>' + cloudMark(i.file && i.file.online) + '</span>' +
    '<span class="lr-meta" title="' + esc(where) + '">' + esc(meta) + '</span>' +
    '<span class="lr-size">' + fmtBytes(size) + '</span></div>' +
    '<button type="button" class="icon-button row-mark" data-show="' + i.id + '" aria-label="Show ' + esc(i.name) + ' in Explore" title="Show in Explore"' +
    (i.folder_id === null ? ' disabled' : '') + ' data-track="shortlist-show">' + glyph('folder-open') + '</button>' +
    '<button type="button" class="icon-button row-mark" data-remove="' + i.id + '" aria-label="Remove ' + esc(i.name) + ' from the shortlist" title="Remove" data-track="shortlist-remove">' +
    glyph('x') + '</button></li>';
}

function renderSheet() {
  $('shortlistMeta').textContent = summary();
  $('shortlistList').innerHTML = list.items.length ? list.items.map(entryRow).join('')
    : '<li class="rows-note">Nothing on the shortlist yet. Tap the bookmark on a file in Largest to add it.</li>';
}

async function openSheet() {
  renderSheet();
  $('shortlistDialog').showModal();
  try { await loadShortlist(); } catch (e) { fail(e); }
}

/** Wire the row bookmarks, the openers and the sheet; ``opts.showInExplore(root, folderId)`` jumps to Explore. */
export function initShortlist(opts) {
  showInExplore = opts.showInExplore;
  $('topList').addEventListener('click', (e) => {
    const btn = e.target.closest('.row-mark');
    if (btn && !btn.disabled) toggle(btn);
  });
  document.querySelectorAll('[data-shortlist-open]').forEach((b) => b.addEventListener('click', openSheet));
  $('shortlistList').addEventListener('click', async (e) => {
    const show = e.target.closest('[data-show]');
    const remove = e.target.closest('[data-remove]');
    if (show && !show.disabled) {
      const item = list.items.find((i) => i.id === Number(show.dataset.show));
      if (item) showInExplore(item.root, item.folder_id);
    } else if (remove && !remove.disabled) {
      remove.disabled = true;
      try { take(await deleteJSON('/api/shortlist/' + remove.dataset.remove)); } catch (err) { remove.disabled = false; fail(err); }
    }
  });
  loadShortlist().catch(fail);
}
