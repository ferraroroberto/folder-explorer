/* Explore: the root's summary, the lazily loaded folder tree, and the selected folder's contents. */

import { getJSON } from './api.js';
import { state, query, val } from './state.js';
import { bar, cloudMark, esc, extLabel, fmtBytes, fmtDate, fmtNum, plural, fmtPct, glyph, pct } from './fmt.js';

const $ = (id) => document.getElementById(id);
const WIDE = window.matchMedia('(min-width: 1100px) and (pointer: fine)');

const nodes = new Map();
const kids = new Map();
const expanded = new Set();
let contents = null;
let onSelect = () => {};

export function initExplore(opts) {
  onSelect = opts.onSelect;
  WIDE.addEventListener('change', () => { if (WIDE.matches && $('contentsDialog').open) $('contentsDialog').close(); });
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
  return '<div class="tree-row' + (sel ? ' is-selected' : '') + '" style="--depth:' + depth + '">' +
    (n.has_children
      ? '<button type="button" class="icon-button tree-twisty" data-act="toggle" data-id="' + n.id + '" aria-expanded="' + open +
        '" aria-label="' + (open ? 'Collapse ' : 'Expand ') + esc(n.name) + '" data-track="tree-expand">' + glyph('chevron-right') + '</button>'
      : '<span class="tree-spacer" aria-hidden="true"></span>') +
    '<button type="button" class="tree-main" data-act="select" data-id="' + n.id + '"' + (sel ? ' aria-current="true"' : '') + ' data-track="tree-select">' +
    '<span class="tree-name">' + glyph('folder') + '<span class="tree-label">' + esc(n.name) + '</span></span>' +
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
    out.push('<li>' + treeRow(n, depth, share, open, state.folder === id));
    if (open) {
      const k = kids.get(id);
      out.push('<ul class="tree">');
      k.ids.forEach((c) => walk(c, depth + 1, val(n)));
      if (k.more) out.push('<li class="tree-more" style="--depth:' + (depth + 1) + '">and ' + fmtNum(k.more) + ' smaller folders</li>');
      out.push('</ul>');
    }
    out.push('</li>');
  })(0, 0, 0);
  $('tree').innerHTML = out.join('');
  const root = nodes.get(0);
  $('treeMeta').textContent = root ? plural(root.files, 'file') : '';
}

const segBtn = (v, label, track) => '<button type="button" class="segmented-item" aria-pressed="' + (v === state.mode) +
  '" data-value="' + v + '" data-track="' + track + '">' + label + '</button>';

function folderRow(it, whole) {
  const p = pct(val(it), whole);
  return '<li><button type="button" class="list-row" data-act="select" data-id="' + it.id + '" data-track="contents-folder">' + glyph('folder') +
    '<span class="lr-name"><span>' + esc(it.name) + '</span></span>' +
    '<span class="lr-meta">Folder · ' + plural(it.files, 'file') + ' · ' + fmtPct(p) + '</span>' +
    '<span class="lr-type">Folder</span><span class="lr-date"></span><span class="lr-share">' + fmtPct(p) + '</span>' +
    '<span class="lr-size">' + fmtBytes(val(it)) + '</span>' + bar(p) + '</button></li>';
}

function fileRow(f, whole, nested) {
  const p = pct(val(f), whole);
  const where = nested ? (f.path && f.path.length ? 'in ' + f.path.join(' › ') : 'in this folder') : '';
  const meta = nested ? where : extLabel(f.ext) + ' · ' + fmtDate(f.mtime) + ' · ' + fmtPct(p);
  return '<li><div class="list-row' + (nested ? ' has-path' : '') + '" title="Size ' + fmtNum(f.size) + ' bytes · On disk ' + fmtNum(f.disk) + ' bytes">' + glyph('file-text') +
    '<span class="lr-name"><span>' + esc(f.name) + '</span>' + cloudMark(f.online) + '</span>' +
    '<span class="lr-meta' + (nested ? ' is-path' : '') + '">' + esc(meta) + '</span>' +
    '<span class="lr-type">' + (f.ext ? esc(extLabel(f.ext)) : '—') + '</span><span class="lr-date">' + fmtDate(f.mtime) + '</span><span class="lr-share">' + fmtPct(p) + '</span>' +
    '<span class="lr-size">' + fmtBytes(val(f)) + '</span>' + bar(p) + '</div></li>';
}

function contentsHtml() {
  if (!contents) return '';
  const whole = val(contents.folder);
  const nested = contents.mode === 'nested';
  const rows = contents.items.map((it) => (it.kind === 'folder' ? folderRow(it, whole) : fileRow(it, whole, nested)));
  let note = '';
  if (!contents.items.length) note = state.type !== null ? 'Nothing here matches the type filter.' : 'This folder is empty.';
  else if (contents.total_items > contents.items.length) {
    note = (nested ? 'The ' + contents.items.length + ' largest of ' : 'The ' + contents.items.length + ' biggest of ') + fmtNum(contents.total_items) + (nested ? ' files.' : ' items.');
  }
  return '<p class="contents-path">' + esc(state.path.map((x) => x.name).join(' › ')) + ' · ' + fmtBytes(whole) + ' · ' + plural(contents.folder.files, 'file') + '</p>' +
    '<div class="contents-controls"><span class="segmented" role="group" aria-label="Show" data-seg="mode">' +
    segBtn('direct', 'In this folder', 'mode-direct') + segBtn('nested', 'All nested files', 'mode-nested') + '</span></div>' +
    '<div class="col-head list-cols" aria-hidden="true"><span></span><span>Name</span><span>Type</span><span>Modified</span><span>Share</span><span>Size</span></div>' +
    '<ul class="rows">' + rows.join('') + '</ul>' + (note ? '<p class="rows-note">' + note + '</p>' : '');
}

export function renderContents() {
  if (!contents) return;
  const html = contentsHtml();
  $('contentsDock').innerHTML = '<div class="card-head"><h2 class="card-title">' + glyph('folder') + esc(contents.folder.name) +
    '</h2><span class="card-head-meta">' + (contents.mode === 'direct' ? 'Biggest first' : 'Every nested file') + '</span></div>' + html;
  if ($('contentsDialog').open) {
    $('contentsDialogTitle').textContent = contents.folder.name;
    $('contentsSheetBody').innerHTML = html;
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
  }
}
