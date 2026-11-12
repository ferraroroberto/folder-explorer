/* Explore: the root's summary, the lazily loaded folder tree, and the selected folder's contents. */

import { getJSON, postJSON } from './api.js';
import { state, query, val } from './state.js';
import { bar, cloudMark, esc, extLabel, fmtBytes, fmtDate, fmtNum, plural, fmtPct, glyph, pct } from './fmt.js';
import { initExploreActions, isDocument, kebab, wireRowMenus } from './explore-actions.js';
import { switchEl } from './_vendored/switch/switch.js';

const $ = (id) => document.getElementById(id);
const WIDE = window.matchMedia('(min-width: 1100px) and (pointer: fine)');
// The index's ``hidden`` values: 1 the item itself, 2 inside a hidden folder.
const HIDDEN_TEXT = { 1: 'Hidden', 2: 'In a hidden folder' };

const nodes = new Map();
const kids = new Map();
const expanded = new Set();
let contents = null;
let onSelect = () => {};

export function initExplore(opts) {
  onSelect = opts.onSelect;
  initExploreActions(() => contents);
  WIDE.addEventListener('change', () => { if (WIDE.matches && $('contentsDialog').open) $('contentsDialog').close(); });
}

const hiddenMark = (h) => (h ? '<svg class="icon hidden-mark" role="img" aria-label="' + HIDDEN_TEXT[h] + '"><title>' +
  HIDDEN_TEXT[h] + '</title><use href="#i-eye-off"></use></svg>' : '');

/** Save the Show hidden items switch; a hidden folder that is open goes back to the root when they hide. */
export async function setShowHidden(on) {
  await postJSON('/api/config', { explore: { show_hidden: on } });
  if (!on && contents && contents.folder.hidden) state.folder = 0;
}

/** Forget every loaded folder: the root, basis, type or scan changed. */
export function resetExplore() {
  nodes.clear();
  kids.clear();
  expanded.clear();
  contents = null;
}

export const folderNode = (id) => nodes.get(id) || null;

async function loadChildren(id) {
  const body = await getJSON('/api/tree?' + query({ folder: id }));
  nodes.set(body.folder.id, body.folder);
  body.children.forEach((c) => nodes.set(c.id, c));
  kids.set(id, { ids: body.children.map((c) => c.id), more: body.more });
  return body;
}

/** Make sure every folder on ``path`` is loaded and expanded, so the selection shows in the tree. */
async function revealPath(path) {
  for (const step of path.slice(0, -1)) {
    if (!kids.has(step.id)) await loadChildren(step.id);
    expanded.add(step.id);
  }
}

export async function loadExplore() {
  if (!kids.has(0)) {
    await loadChildren(0);
    expanded.add(0);
    const first = kids.get(0).ids[0];
    if (first !== undefined && nodes.get(first).has_children) {
      await loadChildren(first);
      expanded.add(first);
    }
  }
  const [summary, body] = await Promise.all([
    getJSON('/api/summary?' + query({ folder: 0 })),
    getJSON('/api/folder?' + query({ mode: state.mode })),
  ]);
  contents = body;
  state.path = body.path;
  state.folderName = body.folder.name;
  await revealPath(body.path);
  renderSummary(summary.totals);
  renderTree();
  renderContents();
}

function renderSummary(t) {
  const disk = state.basis === 'disk';
  const cells = [
    [fmtBytes(disk ? t.alloc : t.size), disk ? 'On disk' : 'Total size'],
    [fmtNum(t.files), 'Files in ' + plural(t.folders, 'folder')],
    [fmtBytes(t.online_size), fmtNum(t.online_files) + ' online-only'],
    [fmtBytes(disk ? t.size : t.alloc), disk ? 'Total size' : 'On this PC'],
  ];
  $('summary').innerHTML = cells.map((c) => '<div class="stat"><div class="stat-value">' + c[0] +
    '</div><div class="stat-label">' + esc(c[1]) + '</div></div>').join('');
}

function treeRow(n, depth, share, open, sel) {
  return '<div class="tree-row' + (sel ? ' is-selected' : '') + (n.hidden ? ' is-hidden' : '') + '" style="--depth:' + depth + '">' +
    (n.has_children
      ? '<button type="button" class="icon-button tree-twisty" data-act="toggle" data-id="' + n.id + '" aria-expanded="' + open +
        '" aria-label="' + (open ? 'Collapse ' : 'Expand ') + esc(n.name) + '" data-track="tree-expand">' + glyph('chevron-right') + '</button>'
      : '<span class="tree-spacer" aria-hidden="true"></span>') +
    '<button type="button" class="tree-main" data-act="select" data-open="folder" data-id="' + n.id + '" title="' + esc(n.name) + '"' + (sel ? ' aria-current="true"' : '') + ' data-track="tree-select">' +
    '<span class="tree-name">' + glyph('folder') + '<span class="tree-label">' + esc(n.name) + '</span>' + hiddenMark(n.hidden) + '</span>' +
    '<span class="tree-size">' + fmtBytes(val(n)) + '</span>' +
    '<span class="tree-meta"><span class="tree-files">' + fmtNum(n.files) + '<span class="tree-unit">' + (n.files === 1 ? ' file' : ' files') + '</span></span><span class="sep"> · </span><span class="tree-share">' + fmtPct(share) + '</span></span>' +
    bar(share) + '</button></div>';
}

export function renderTree() {
  const out = [];
  (function walk(id, depth, parentBytes) {
    const n = nodes.get(id);
    if (!n) return;
    const share = depth === 0 ? 100 : pct(val(n), parentBytes);
    const open = expanded.has(id) && kids.has(id);
    // One flat list item per folder, its depth drawn by indent, so each row holds only its own controls.
    out.push('<li>' + treeRow(n, depth, share, open, state.folder === id) + '</li>');
    if (open) {
      const k = kids.get(id);
      k.ids.forEach((c) => walk(c, depth + 1, val(n)));
      if (k.more) out.push('<li class="tree-more" style="--depth:' + (depth + 1) + '">and ' + fmtNum(k.more) + ' smaller folders</li>');
    }
  })(0, 0, 0);
  $('tree').innerHTML = out.join('');
  const root = nodes.get(0);
  $('treeMeta').textContent = root ? plural(root.files, 'file') : '';
}

const segBtn = (v, label, track) => '<button type="button" class="segmented-item" aria-pressed="' + (v === state.mode) +
  '" data-value="' + v + '" data-track="' + track + '">' + label + '</button>';

const rowItem = (it, i, row) => '<li class="row-item' + (it.hidden ? ' is-hidden' : '') + '">' + row + kebab(i, it.name) + '</li>';

function folderRow(it, whole, i) {
  const p = pct(val(it), whole);
  return rowItem(it, i, '<button type="button" class="list-row" data-act="select" data-open="folder" data-id="' + it.id + '" data-track="contents-folder">' + glyph('folder') +
    '<span class="lr-name"><span>' + esc(it.name) + '</span>' + hiddenMark(it.hidden) + '</span>' +
    '<span class="lr-meta">Folder · ' + plural(it.files, 'file') + ' · ' + fmtPct(p) + '</span>' +
    '<span class="lr-type">Folder</span><span class="lr-date"></span><span class="lr-share">' + fmtPct(p) + '</span>' +
    '<span class="lr-size">' + fmtBytes(val(it)) + '</span>' + bar(p) + '</button>');
}

function fileRow(f, whole, nested, i) {
  const p = pct(val(f), whole);
  const where = nested ? (f.path && f.path.length ? 'in ' + f.path.join(' › ') : 'in this folder') : '';
  const meta = nested ? where : extLabel(f.ext) + ' · ' + fmtDate(f.mtime) + ' · ' + fmtPct(p);
  const doc = isDocument(f);
  return rowItem(f, i, '<div class="list-row' + (nested ? ' has-path' : '') + '" data-open="file" data-id="' + f.id + '"' + (doc ? ' data-doc="1"' : '') +
    ' title="Size ' + fmtNum(f.size) + ' bytes · On disk ' + fmtNum(f.disk) + ' bytes">' + glyph('file-text') +
    '<span class="lr-name"><span>' + esc(f.name) + '</span>' + (doc ? '<span class="chip doc-badge">MD</span>' : '') + cloudMark(f.online) + hiddenMark(f.hidden) + '</span>' +
    '<span class="lr-meta' + (nested ? ' is-path' : '') + '">' + esc(meta) + '</span>' +
    '<span class="lr-type">' + (f.ext ? esc(extLabel(f.ext)) : '—') + '</span><span class="lr-date">' + fmtDate(f.mtime) + '</span><span class="lr-share">' + fmtPct(p) + '</span>' +
    '<span class="lr-size">' + fmtBytes(val(f)) + '</span>' + bar(p) + '</div>');
}

/** The Show hidden items switch, or nothing when the index can't tell hidden items apart. */
function hiddenSwitch() {
  if (!contents.hidden.known) return '';
  const sw = switchEl(contents.hidden.shown, { label: 'Show hidden items' });
  sw.dataset.act = 'hidden';
  sw.dataset.track = 'show-hidden';
  return '<span class="hidden-toggle"><span aria-hidden="true">Hidden items</span>' + sw.outerHTML + '</span>';
}

/** Whether the sizes count hidden items (always), and how many here the list leaves out. */
function hiddenNote() {
  const h = contents.hidden;
  if (!h.known) return 'This scan can\'t tell hidden items apart: rescan to hide them.';
  const n = contents.hidden_items || 0;
  return (!h.shown && n ? plural(n, 'hidden item') + ' not listed. ' : '') + 'Sizes include hidden items.';
}

function contentsHtml() {
  if (!contents) return '';
  const whole = val(contents.folder);
  const nested = contents.mode === 'nested';
  const rows = contents.items.map((it, i) => (it.kind === 'folder' ? folderRow(it, whole, i) : fileRow(it, whole, nested, i)));
  let note = '';
  if (!contents.items.length) note = state.type !== null ? 'Nothing here matches the type filter.' : 'This folder is empty.';
  else if (contents.total_items > contents.items.length) {
    note = (nested ? 'The ' + contents.items.length + ' largest of ' : 'The ' + contents.items.length + ' biggest of ') + fmtNum(contents.total_items) + (nested ? ' files.' : ' items.');
  }
  return '<p class="contents-path">' + esc(state.path.map((x) => x.name).join(' › ')) + ' · ' + fmtBytes(whole) + ' · ' + plural(contents.folder.files, 'file') + '</p>' +
    '<div class="contents-controls"><span class="segmented" role="group" aria-label="Show" data-seg="mode">' +
    segBtn('direct', 'In this folder', 'mode-direct') + segBtn('nested', 'All nested files', 'mode-nested') + '</span>' + hiddenSwitch() + '</div>' +
    '<p class="contents-note">' + esc(hiddenNote()) + '</p>' +
    '<div class="col-head list-cols" aria-hidden="true"><span></span><span>Name</span><span>Type</span><span>Modified</span><span>Share</span><span>Size</span></div>' +
    '<ul class="rows">' + rows.join('') + '</ul>' + (note ? '<p class="rows-note">' + note + '</p>' : '');
}

export function renderContents() {
  if (!contents) return;
  const html = contentsHtml();
  $('contentsDock').innerHTML = '<div class="card-head"><h2 class="card-title">' + glyph('folder') + esc(contents.folder.name) +
    '</h2><span class="card-head-meta">' + (contents.mode === 'direct' ? 'Biggest first' : 'Every nested file') + '</span></div>' + html;
  wireRowMenus($('contentsDock'));
  if ($('contentsDialog').open) {
    $('contentsDialogTitle').textContent = contents.folder.name;
    $('contentsSheetBody').innerHTML = html;
    wireRowMenus($('contentsSheetBody'));
  }
}

export async function setMode(mode) {
  state.mode = mode;
  contents = await getJSON('/api/folder?' + query({ mode }));
  renderContents();
}

export async function toggleFolder(id) {
  if (expanded.has(id)) {
    expanded.delete(id);
  } else {
    if (!kids.has(id)) await loadChildren(id);
    expanded.add(id);
  }
  renderTree();
}

/** Select a folder: load its contents, reveal it in the tree, open the sheet on a phone. */
export async function selectFolder(id, fromSheet) {
  state.folder = id;
  contents = await getJSON('/api/folder?' + query({ mode: state.mode }));
  state.path = contents.path;
  state.folderName = contents.folder.name;
  nodes.set(id, { ...(nodes.get(id) || {}), ...contents.folder });
  await revealPath(contents.path);
  renderTree();
  renderContents();
  onSelect();
  if (!WIDE.matches && !fromSheet && state.tab === 'explore') {
    const d = $('contentsDialog');
    $('contentsDialogTitle').textContent = contents.folder.name;
    $('contentsSheetBody').innerHTML = contentsHtml();
    if (!d.open) d.showModal();
    wireRowMenus($('contentsSheetBody'));
  }
}
