/* Folder explorer: boot, tabs, toolbar, header lines, scan polling, the jump from Search into Explore and the shared click handling. */

import { getJSON, postJSON, authHeaders, ApiError } from './api.js';
import { readPref, rootInfo, state, writePref, APP } from './state.js';
import { esc, extLabel, fmtAgo, glyph } from './fmt.js';
import { initExplore, loadExplore, resetExplore, selectFolder, setMode, setShowHidden, toggleFolder } from './explore.js';
import { loadLargest } from './largest.js';
import { loadTypes, typeOptions } from './types.js';
import { loadRoots, renderRoots, scansStatus } from './scans.js';
import { initEmailScans, loadEmailScans } from './email-scans.js';
import { initSettings, openSettings } from './settings.js';
import { initSearch, leaveSearch, loadSearch } from './search.js';
import { initArchive, startArchivePoll } from './archive.js';
import { initDrop } from './doc-drop.js';
import { openDropped, openIndexed } from './doc-sheet.js';
import { wireThemeToggle } from './theme.js';
import { initNavTabs } from './_vendored/nav/nav-tabs.js';
import { renderHeadStatus } from './_vendored/home-head/home-head.js';
import { buildReadoutText } from './_vendored/page-foot/page-foot.js';
import { bindTextSize } from './_vendored/text-size/text-size.js';
import { showToast } from './_vendored/toast/toast.js';
import { emptyStateEl } from './_vendored/empty-state/empty-state.js';
import { initUsageLog } from './_vendored/usage-log/usage-log.js';

const $ = (id) => document.getElementById(id);
const PANES = { search: 'paneSearch', explore: 'paneExplore', largest: 'paneLargest', types: 'paneTypes', scans: 'paneScans' };
const LOADERS = { explore: loadExplore, largest: loadLargest, types: loadTypes };
// How often the page re-reads scan progress while a scan runs.
const POLL_MS = 2000;

const both = (...fns) => (value) => fns.forEach((fn) => fn(value));

const dirty = new Set(['explore', 'largest', 'types']);
let pollTimer = null;
// Views stay idle until the roots are known; the nav reports its first tab before that.
let booted = false;

function fail(e) {
  if (e instanceof ApiError && e.status === 404) return;
  showToast(e.message || 'Something went wrong', 'error');
}

// ------------------------------------------------------------ header lines

function updateHeads() {
  const r = rootInfo();
  const heads = {};
  if (!r || !r.current) {
    heads.explore = heads.largest = heads.types = { exceptions: [], fallback: r ? 'Not scanned yet' : '' };
  } else {
    const stale = r.failed ? [{ text: 'Last scan failed', tone: 'danger' }] : [];
    const live = r.running ? [{ text: 'Rescanning', tone: 'neutral' }] : [];
    // The toolbar's Root select already names the root, so the line keeps to when it was scanned and fits a phone.
    heads.explore = { exceptions: stale.concat(live), fallback: 'Scanned ' + fmtAgo(r.current.finished_at) };
    heads.largest = { exceptions: stale, fallback: 'Biggest files in ' + (state.folderName || r.name) };
    heads.types = { exceptions: stale, fallback: 'Space by type in ' + (state.folderName || r.name) };
  }
  heads.scans = scansStatus();
  Object.entries(heads).forEach(([tab, h]) => renderHeadStatus($(PANES[tab]).querySelector('.head-status'), h.exceptions, h.fallback));
}

// ----------------------------------------------------------------- toolbar

async function renderToolbar() {
  $('rootSelect').innerHTML = state.roots.map((r) => '<option value="' + esc(r.key) + '"' + (r.key === state.root ? ' selected' : '') +
    (r.current ? '' : ' disabled') + '>' + esc(r.name) + ' (' + esc(r.kind) + ')</option>').join('');
  $('basisSeg').querySelectorAll('.segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === state.basis)));
  updateScope();
  const r = rootInfo();
  if (!r || !r.current) { $('typeSelect').innerHTML = '<option value="*">All types</option>'; return; }
  const exts = await typeOptions();
  if (state.type !== null && !exts.includes(state.type)) exts.unshift(state.type);
  $('typeSelect').innerHTML = '<option value="*">All types</option>' + exts.map((x) => '<option value="' + esc(x) + '"' +
    (x === state.type ? ' selected' : '') + '>' + esc(extLabel(x)) + '</option>').join('');
}

function updateScope() {
  const crumbs = state.path.length ? state.path.map((x) => x.name).join(' › ') : (rootInfo() ? rootInfo().name : '');
  $('scopePill').innerHTML = glyph('folder') + '<span>' + esc(crumbs) + '</span>';
  $('scopePill').setAttribute('aria-label', 'Scope: ' + crumbs + '. Show in Explore');
}

function placeToolbar(tab) {
  const bar = $('toolbar');
  const slot = $(PANES[tab]).querySelector('.toolbar-slot');
  if (slot) slot.appendChild(bar);
  else $(PANES.explore).insertBefore(bar, $(PANES.explore).querySelector('.pane-body'));
}

// ------------------------------------------------------------------ views

function showEmpty(empty) {
  ['explore', 'largest', 'types'].forEach((tab) => {
    const pane = $(PANES[tab]);
    pane.querySelector('.pane-body').hidden = !!empty;
    const slot = pane.querySelector('.pane-empty');
    slot.hidden = !empty;
    slot.innerHTML = '';
    if (empty) slot.appendChild(empty.cloneNode(true));
  });
  $('toolbar').hidden = false;
}

function emptyFor(r) {
  const msg = !r ? 'No scan roots are set: add one in Settings.'
    : r.running ? 'Scanning ' + r.name + ' for the first time…'
      : !r.exists ? r.name + ' was not found on this PC.'
        : 'No scan of ' + r.name + ' yet.';
  const el = emptyStateEl(r && r.running ? 'refresh-cw' : 'folder', msg, r && r.exists && !r.running && !state.scanning ? { actionLabel: 'Scan now' } : undefined);
  const btn = el.querySelector('button');
  if (btn) { btn.dataset.act = 'rescan'; btn.dataset.root = r.key; btn.dataset.track = 'empty-scan-now'; }
  return el;
}

async function renderActive() {
  if (!booted) return;
  const tab = state.tab;
  if (tab === 'search') { await loadSearch(); return; }
  if (tab === 'scans') { renderRoots(); loadEmailScans(); updateHeads(); return; }
  if (!dirty.has(tab)) return;
  const r = rootInfo();
  if (!r || !r.current) { showEmpty(emptyFor(r)); updateHeads(); return; }
  showEmpty(null);
  dirty.delete(tab);
  try {
    await LOADERS[tab]();
  } catch (e) {
    dirty.add(tab);
    fail(e);
  }
  updateScope();
  updateHeads();
}

/** Start the root over at its top folder: after a root, basis, type or index change. */
async function resetRoot() {
  resetExplore();
  state.folder = 0;
  state.path = [];
  state.folderName = '';
  const r = rootInfo();
  state.scanId = r && r.current ? r.current.id : null;
  ['explore', 'largest', 'types'].forEach((t) => dirty.add(t));
  try { await renderToolbar(); } catch (e) { fail(e); }
  await renderActive();
}

/** Open Explore at one folder of one root: Search's "Show folder" and "Show in Explore". */
async function showInExplore(nav, rootKey, folderId) {
  const r = state.roots.find((x) => x.key === rootKey);
  if (!r || !r.current) { showToast('That root has no finished scan yet'); return; }
  state.root = rootKey;
  writePref('root', rootKey);
  resetExplore();
  state.type = null;
  state.scanId = r.current.id;
  state.folder = folderId;
  state.path = [];
  state.folderName = '';
  ['explore', 'largest', 'types'].forEach((t) => dirty.add(t));
  try { await renderToolbar(); } catch (e) { fail(e); }
  document.querySelectorAll('dialog[open]').forEach((d) => d.close());
  if (state.tab === 'explore') renderActive();
  else nav.setTab('explore');
}

async function refreshRoots() {
  const wasScanning = state.scanning;
  await loadRoots();
  if (!rootInfo()) {
    const first = state.roots.find((r) => r.current) || state.roots[0];
    state.root = first ? first.key : null;
  }
  const r = rootInfo();
  const newScan = r && r.current ? r.current.id : null;
  if (newScan !== state.scanId) {
    if (state.scanId !== null) showToast('Scan of ' + r.name + ' finished: showing the new index');
    await resetRoot();
  }
  if (state.tab === 'scans') renderRoots();
  updateHeads();
  schedulePoll();
  if (wasScanning && !state.scanning) showToast('Scan finished');
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = state.scanning ? setTimeout(() => refreshRoots().catch(fail), POLL_MS) : null;
}

async function rescan(keys) {
  try {
    await postJSON('/api/scan', keys ? { roots: keys } : {});
    showToast(keys ? 'Scan started' : 'Scanning every root');
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) showToast('A scan is already running');
    else fail(e);
  }
  await refreshRoots();
}

// ------------------------------------------------------------------- wiring

function onFolderSelected() {
  dirty.add('largest');
  dirty.add('types');
  updateScope();
  updateHeads();
}

function wireClicks(nav) {
  document.addEventListener('click', (e) => {
    const closer = e.target.closest('[data-close]');
    if (closer) { closer.closest('dialog').close(); return; }
    const seg = e.target.closest('[data-seg="mode"] .segmented-item');
    if (seg) { setMode(seg.dataset.value).catch(fail); return; }
    const t = e.target.closest('[data-act]');
    if (!t || t.disabled) return;
    const fromSheet = !!t.closest('dialog');
    switch (t.dataset.act) {
      case 'toggle': toggleFolder(Number(t.dataset.id)).catch(fail); break;
      case 'select': selectFolder(Number(t.dataset.id), fromSheet).catch(fail); break;
      case 'type':
        state.type = state.type === t.dataset.ext ? null : t.dataset.ext;
        resetKeepFolder();
        break;
      case 'hidden':
        setShowHidden(t.getAttribute('aria-checked') !== 'true').then(resetKeepFolder).catch(fail);
        break;
      case 'rescan': rescan([t.dataset.root]); break;
      case 'open-root':
        state.root = t.dataset.root;
        writePref('root', state.root);
        resetRoot().then(() => nav.setTab('explore'));
        break;
      default: break;
    }
  });
  document.querySelectorAll('dialog').forEach((d) => d.addEventListener('click', (e) => { if (e.target === d) d.close(); }));
}

/** The type filter or basis changed: reload every view for the same folder. */
function resetKeepFolder() {
  const keep = state.folder;
  resetExplore();
  state.folder = keep;
  ['explore', 'largest', 'types'].forEach((t) => dirty.add(t));
  renderToolbar().catch(fail);
  renderActive();
}

function wireToolbar(nav) {
  $('basisSeg').addEventListener('click', (e) => {
    const b = e.target.closest('.segmented-item');
    if (!b || b.dataset.value === state.basis) return;
    state.basis = b.dataset.value;
    resetKeepFolder();
  });
  $('typeSelect').addEventListener('change', (e) => {
    state.type = e.target.value === '*' ? null : e.target.value;
    resetKeepFolder();
  });
  $('rootSelect').addEventListener('change', (e) => {
    state.root = e.target.value;
    state.type = null;
    writePref('root', state.root);
    resetRoot();
  });
  $('scopePill').addEventListener('click', () => nav.setTab('explore'));
  $('clearFilter').addEventListener('click', () => { state.type = null; resetKeepFolder(); });
  $('pctSeg').addEventListener('click', (e) => {
    const b = e.target.closest('.segmented-item');
    if (!b) return;
    state.pct = Number(b.dataset.value);
    $('pctSeg').querySelectorAll('.segmented-item').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    loadLargest().catch(fail);
  });
  $('basisInfo').addEventListener('click', () => $('infoDialog').showModal());
  $('scanInfo').addEventListener('click', () => $('scanInfoDialog').showModal());
  $('rescanAll').addEventListener('click', () => rescan(null));
}

function wireTheme() {
  wireThemeToggle();
  document.querySelectorAll('.open-settings').forEach((b) => b.addEventListener('click', () => openSettings()));
}

async function showBuild() {
  try {
    const v = await getJSON('/api/version');
    $('buildReadout').textContent = buildReadoutText(v.git_sha, v.built_at);
  } catch (e) {
    $('buildReadout').textContent = 'Build: unknown';
  }
}

async function boot() {
  const usage = initUsageLog({ headers: authHeaders });
  const onTab = (tab) => {
    if (state.tab === 'search' && tab !== 'search') leaveSearch();
    state.tab = tab;
    placeToolbar(tab);
    renderActive();
  };
  // The usage log records each tab as a view: its trackView goes in by reference.
  const nav = initNavTabs({ storageKey: APP + '.tab', defaultTab: 'search', onChange: both(usage.trackView, onTab) });
  // A #search?mode=...&q=... link opens Search whatever tab was last open.
  if (location.hash.startsWith('#search')) nav.setTab('search');
  state.tab = document.querySelector('.tab.active') ? document.querySelector('.tab.active').dataset.tab : 'search';
  placeToolbar(state.tab);
  initSearch({ nav, showInExplore: (root, folder) => showInExplore(nav, root, folder) });
  initExplore({ onSelect: onFolderSelected });
  initEmailScans();
  initArchive();
  initDrop({ open: openIndexed, show: openDropped, ownWindow: true });
  initSettings({ onRootsChanged: () => { state.root = null; refreshRoots().catch(fail); } });
  bindTextSize($('textSizeControl'), APP);
  wireTheme();
  wireClicks(nav);
  wireToolbar(nav);
  showBuild();
  state.root = readPref('root', null);
  booted = true;
  try {
    await refreshRoots();
    if (state.scanId === null) await resetRoot();
  } catch (e) {
    fail(e);
  }
  startArchivePoll();
  window.addEventListener('pageshow', (e) => { if (e.persisted) refreshRoots().catch(fail); });
}

boot();
