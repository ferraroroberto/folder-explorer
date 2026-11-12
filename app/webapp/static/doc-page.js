/* The document window (/doc?root=…&scan=…&id=…): one Markdown file in its own browser or app window.
 *
 * The address names the file by root, scan id and item id, so it can be
 * bookmarked and still opens after a restart; a newer scan that renumbered it
 * says so and offers Explore. The outline is a sidebar on a wide window and a
 * popover below it. View | Edit switches to the editor; Esc goes back to View.
 * The fleet nav leads back into the app: a tab opens the app on that tab,
 * after asking when there are unsaved changes. A file dropped on the app that the
 * scan doesn't have opens here too, as ``/doc?dropped=<token>`` (doc-handoff.js).
 */

import { authHeaders, getJSON } from './api.js';
import { esc } from './fmt.js';
import { APP } from './state.js';
import { docUrl, drawDoc, fetchDoc, locFromUrl, outlineHtml, releaseDoc, scrollToHeading, stateHtml, wireDocHost } from './doc-view.js';
import { openEditor } from './doc-edit.js';
import { initDrop } from './doc-drop.js';
import { droppedToken, fetchDropped } from './doc-handoff.js';
import { wireThemeToggle } from './theme.js';
import { renderHeadStatus } from './_vendored/home-head/home-head.js';
import { initNavTabs } from './_vendored/nav/nav-tabs.js';
import { initUsageLog } from './_vendored/usage-log/usage-log.js';
import { showToast } from './_vendored/toast/toast.js';

const $ = (id) => document.getElementById(id);
// Wide windows show the outline beside the text; narrower ones open it from the header.
const WIDE = window.matchMedia('(min-width: 1100px) and (pointer: fine)');

// The address's document; null for an address that names none and for a dropped file the scan doesn't have.
let loc = locFromUrl(location.href);
let editor = null;
// Set once the reader chose how to leave (the in-app question), so the browser's own prompt doesn't ask again.
let leaving = false;

/** Leave for ``href``, asking first when the editor holds unsaved changes. */
async function go(href) {
  if (editor && !(await editor.leave())) return;
  leaving = true;
  location.href = href;
}

/** Open the app itself on ``tab``: the app reads the tab it last showed. */
function toApp(tab) {
  try { localStorage.setItem(APP + '.tab', tab); } catch (e) { /* the app opens on its default tab */ }
  go('/');
}

function markMode(mode) {
  document.querySelectorAll('.doc-mode .segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
  document.body.classList.toggle('is-editing', mode === 'edit');
  $('docSave').hidden = mode !== 'edit';
  $('docSave').disabled = true;
}

function startEditing() {
  if (editor || !loc) return;
  markMode('edit');
  placeOutline(false);
  releaseDoc($('docBody'));
  editor = openEditor($('docBody'), loc, {
    onDirty: (dirty) => { $('docSave').disabled = !dirty; },
    onReady: (editable) => { if (!editable) $('docSave').hidden = true; },
  });
}

/** Back to View: the draft, if any, stays on the device and the page shows the copy on disk. */
function stopEditing() {
  if (!editor) return;
  if (editor.dirty()) showToast('Your unsaved draft is kept on this device');
  editor.close();
  editor = null;
  markMode('view');
  load(false);
}

function placeOutline(hasOutline) {
  const side = $('docOutline');
  $('docOutlineBtn').hidden = !hasOutline || WIDE.matches;
  side.hidden = !hasOutline || !WIDE.matches;
  side.classList.toggle('is-popover', !WIDE.matches);
  $('docOutlineBtn').setAttribute('aria-expanded', 'false');
}

/** The documents opened last, from the server (so every window and device shares them); empty when it can't say. */
async function fetchRecent() {
  try {
    const body = await getJSON('/api/doc/recent');
    return Array.isArray(body.recent) ? body.recent : [];
  } catch (e) {
    return [];
  }
}

/** The recent list without the document on show, as links the page opens in this window. */
function recentHtml(items) {
  const others = items.filter((d) => !(loc && d.root === loc.root && d.scan_id === loc.scan_id && d.id === loc.id));
  if (!others.length) return '';
  return '<p class="doc-side-title">Recent</p><ul class="doc-outline-list doc-recent-list">' + others.map((d) =>
    '<li><a href="' + esc(docUrl(d)) + '" data-doc-id="' + esc(String(d.id)) + '" title="' + esc(d.folder.join(' › ')) +
    '" data-track="doc-recent-item">' + esc(d.name) + '</a></li>').join('') + '</ul>';
}

/** The sidebar: the outline when the document has headings, then the recent documents. */
function drawSide(outline, recent) {
  const html = (outline.length ? '<p class="doc-side-title">Outline</p>' + outlineHtml(outline) : '') + recentHtml(recent);
  $('docOutline').innerHTML = html;
  placeOutline(html !== '');
}

async function load(download) {
  if (!loc) {
    $('docBody').innerHTML = stateHtml({ reason: 'not_found', message: 'This address names no document' });
    return;
  }
  $('docBody').innerHTML = '<p class="doc-loading" role="status">' + (download ? 'Downloading to this PC…' : 'Loading…') + '</p>';
  const r = await fetchDoc(loc, download);
  if (!r.ok) {
    renderHeadStatus($('docStatus'), [], '');
    $('docBody').innerHTML = stateHtml(r);
    drawSide([], await fetchRecent());
    return;
  }
  document.title = r.doc.name + ' · Folder explorer';
  $('docTitle').textContent = r.doc.name;
  renderHeadStatus($('docStatus'), [], r.doc.folder.join(' › '));
  drawDoc($('docBody'), r.doc);
  // Asked after the document: the server counts this open first, then lists the others.
  drawSide(r.doc.outline, await fetchRecent());
  if (loc.fragment) scrollToHeading($('docBody'), loc.fragment);
}

/** A dropped file the scan has opens as its document (a new address, so Edit and the bookmark work). */
function openIndexed(next) {
  go(docUrl(next));
}

/** A dropped file the scan doesn't have, already rendered: shown read-only here, with no address of its own. */
async function showDropped(doc) {
  if (editor) {
    if (!(await editor.leave())) return;
    editor.close();
    editor = null;
  }
  loc = null;
  markMode('view');
  document.querySelector('.doc-modebar').hidden = true;
  document.title = doc.name + ' · Folder explorer';
  $('docTitle').textContent = doc.name;
  renderHeadStatus($('docStatus'), [], 'Dropped file');
  drawDoc($('docBody'), doc);
  drawSide(doc.outline, await fetchRecent());
}

/** The window a drop opened for a file the scan doesn't have: its document comes from the app page that opened it. */
async function loadDropped(token) {
  $('docBody').innerHTML = '<p class="doc-loading" role="status">Loading…</p>';
  const doc = await fetchDropped(token);
  if (doc) {
    await showDropped(doc);
    return;
  }
  renderHeadStatus($('docStatus'), [], '');
  $('docBody').innerHTML = stateHtml({ reason: 'dropped_gone', message: 'This dropped file is no longer here' });
  drawSide([], await fetchRecent());
}

function boot() {
  const usage = initUsageLog({ headers: authHeaders });
  usage.trackView('doc-window');
  let ready = false;
  // Not a tab of its own: no tab is selected, and choosing one leaves for the app.
  const nav = initNavTabs({ onChange: (tab) => { if (ready) toApp(tab); } });
  nav.setTab(null);
  ready = true;
  wireThemeToggle();
  initDrop({ open: openIndexed, show: showDropped });
  wireDocHost(document.body, $('docBody'), {
    onDocLink: (next) => go(docUrl(next) + (next.fragment ? '#' + encodeURIComponent(next.fragment) : '')),
    onDownload: () => load(true),
    onExplore: () => toApp('explore'),
    onAnchor: () => { if (!WIDE.matches) $('docOutline').hidden = true; },
    getLoc: () => loc,
  });
  $('docOutlineBtn').addEventListener('click', () => {
    const open = $('docOutline').hidden;
    $('docOutline').hidden = !open;
    $('docOutlineBtn').setAttribute('aria-expanded', String(open));
  });
  WIDE.addEventListener('change', () => { if (!editor) placeOutline(!!$('docOutline').querySelector('li')); });
  document.querySelector('.doc-mode').addEventListener('click', (e) => {
    const b = e.target.closest('[data-mode]');
    if (b && b.dataset.mode === 'edit') startEditing();
    else if (b) stopEditing();
  });
  $('docSave').addEventListener('click', () => { if (editor) editor.save(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && editor && !document.querySelector('dialog[open]')) stopEditing();
  });
  window.addEventListener('beforeunload', (e) => {
    if (!editor) return;
    editor.keepDraft();
    if (leaving || !editor.dirty()) return;
    e.preventDefault();
    e.returnValue = '';  // older Safari asks only when this is set
  });
  if (!loc) document.querySelector('.doc-modebar').hidden = true;
  const token = droppedToken(location.href);
  if (token) loadDropped(token);
  else load(false);
}

boot();
