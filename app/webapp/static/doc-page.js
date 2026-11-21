/* The document window (/doc?root=…&scan=…&id=…): one document (Markdown or HTML) in its own browser or app window.
 *
 * The address names the file by root, scan id and item id, so it can be
 * bookmarked and still opens after a restart; a newer scan that renumbered it
 * says so and offers Explore. The outline is a sidebar on a wide window and a
 * popover below it. View | Edit switches to the editor; Esc goes back to View.
 * The fleet nav leads back into the app: a tab opens the app on that tab,
 * after asking when there are unsaved changes. A file dropped on the app that the
 * scan doesn't have opens here too, as ``/doc?dropped=<token>`` (doc-handoff.js);
 * with the browser's handle for it, it has Edit and saves through that handle
 * (local-doc.js), otherwise it is read-only.
 */

import { authHeaders, getJSON } from './api.js';
import { esc } from './fmt.js';
import { APP } from './state.js';
import { docUrl, drawDoc, fetchDoc, locFromUrl, outlineHtml, scrollToHeading, stateHtml, wireDocHost } from './doc-view.js';
import { createDocHost } from './dochost/doc-host.js';
import { initDrop } from './doc-drop.js';
import { droppedToken, fetchDropped } from './doc-handoff.js';
import { wireThemeToggle } from './theme.js';
import { renderHeadStatus } from './_vendored/home-head/home-head.js';
import { initNavTabs } from './_vendored/nav/nav-tabs.js';
import { initUsageLog } from './_vendored/usage-log/usage-log.js';

const $ = (id) => document.getElementById(id);
// Wide windows show the outline beside the text; narrower ones open it from the header.
const WIDE = window.matchMedia('(min-width: 1100px) and (pointer: fine)');

// The address's document; null for an address that names none and for a dropped file the scan doesn't have.
let loc = locFromUrl(location.href);
// The view/edit lifecycle (doc-host.js); its ``local`` is a dropped file's handle, edited and saved through it.
const host = createDocHost({
  root: document.body,
  body: $('docBody'),
  saveBtn: $('docSave'),
  getLoc: () => loc,
  onEditStart: () => placeOutline(false),
  redraw: () => load(false),
  onLocalDrawn: async (body) => drawSide(body.outline, await fetchRecent()),
});
// Set once the reader chose how to leave (the in-app question), so the browser's own prompt doesn't ask again.
let leaving = false;

/** Leave for ``href``, asking first when the editor holds unsaved changes. */
async function go(href) {
  if (host.editor && !(await host.editor.leave())) return;
  leaving = true;
  location.href = href;
}

/** Open the app itself on ``tab``: the app reads the tab it last showed. */
function toApp(tab) {
  try { localStorage.setItem(APP + '.tab', tab); } catch (e) { /* the app opens on its default tab */ }
  go('/');
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
  // A file dropped on the window while a request is in flight replaces `loc`; that file stays on show.
  const started = loc;
  const r = await fetchDoc(started, download);
  if (loc !== started) return;
  if (!r.ok) {
    renderHeadStatus($('docStatus'), [], '');
    $('docBody').innerHTML = stateHtml(r);
    const recent = await fetchRecent();
    if (loc === started) drawSide([], recent);
    return;
  }
  document.title = r.doc.name + ' · Folder explorer';
  $('docTitle').textContent = r.doc.name;
  renderHeadStatus($('docStatus'), [], r.doc.folder.join(' › '));
  drawDoc($('docBody'), r.doc);
  // Asked after the document: the server counts this open first, then lists the others.
  const recent = await fetchRecent();
  if (loc !== started) return;
  drawSide(r.doc.outline, recent);
  if (started.fragment) scrollToHeading($('docBody'), started.fragment);
}

/** A dropped file the scan has opens as its document (a new address, so Edit and the bookmark work). */
function openIndexed(next) {
  go(docUrl(next));
}

/** A dropped file the scan doesn't have, already rendered, with no address of its own: read-only here, or with Edit when the browser gave its ``handle``. */
async function showDropped(doc, handle) {
  if (!(await host.showDropped(doc, handle))) return;
  loc = null;
  document.querySelector('.doc-modebar').hidden = !host.local;
  document.title = doc.name + ' · Folder explorer';
  $('docTitle').textContent = doc.name;
  renderHeadStatus($('docStatus'), [], 'Dropped file');
  drawSide(doc.outline, await fetchRecent());
}

/** The window a drop opened for a file the scan doesn't have: its document comes from the app page that opened it. */
async function loadDropped(token) {
  $('docBody').innerHTML = '<p class="doc-loading" role="status">Loading…</p>';
  const given = await fetchDropped(token);
  if (given) {
    await showDropped(given.doc, given.handle);
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
  WIDE.addEventListener('change', () => { if (!host.editor) placeOutline(!!$('docOutline').querySelector('li')); });
  host.wire();
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && host.editor && !document.querySelector('dialog[open]')) host.stopEditing();
  });
  window.addEventListener('beforeunload', (e) => {
    const editor = host.editor;
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
