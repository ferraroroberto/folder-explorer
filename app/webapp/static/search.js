/* Search: one box over folders (every root's index) or emails (the email index), with a preview.
 *
 * The mode and the box live in the URL (#search?mode=emails&q=...), so a
 * search can be bookmarked or shared to the phone. Typing searches after a
 * short pause; Enter, a chip, a filter or a facet searches at once.
 */

import { getJSON, postJSON, api } from './api.js';
import { writePref } from './state.js';
import { withOp } from './query.js';
import {
  S, $, WIDE, box, clearRecent, dropRecent, effectiveMode, hl, mailDate, marked, plain, pushRecent, readyModes, setOpen, setRecentOpen, shortFolder,
} from './search-state.js';
import {
  FIELD_OPS, renderChips, renderFacets, renderFilters, renderRecent, renderSaved, resetFilters, toggled, withDate,
  withFacet, withoutChip,
} from './search-side.js';
import { initPreview, openPreviewSheet, previewAction, renderPreview } from './search-preview.js';
import { renderOutlookCard, scoreChip } from './archive.js';
import { renderHeadStatus } from './_vendored/home-head/home-head.js';
import { showToast } from './_vendored/toast/toast.js';
import { cloudMark, esc, fmtBytes, fmtDate, glyph, plural, winPath } from './fmt.js';

// How long typing pauses before the box searches.
const DEBOUNCE_MS = 200;

let timer = null;
let nav = null;
let booted = false;

// Per mode: the results card's glyph and title, the box's placeholder, and what one result and the total are called.
const MODE_UI = {
  folders: { glyph: 'folder', title: 'Folders', placeholder: 'Folder names, e.g. alpha budget', noun: 'folder' },
  docs: { glyph: 'file-text', title: 'Documents', placeholder: 'File names, e.g. journal', noun: 'file' },
  emails: { glyph: 'mail', title: 'Emails', placeholder: 'Words, or from: to: subject: has:attachment after:', noun: 'result' },
};

function head(exceptions, fallback) {
  renderHeadStatus($('paneSearch').querySelector('.head-status'), exceptions, fallback);
}

// ------------------------------------------------------------------ chrome

function renderChrome() {
  const mode = effectiveMode();
  const st = S.status;
  $('modeSeg').hidden = readyModes().length < 2;
  $('modeSeg').querySelectorAll('.segmented-item').forEach((b) => {
    b.hidden = !st[b.dataset.value].ready;
    b.setAttribute('aria-pressed', String(b.dataset.value === mode));
  });
  const ui = MODE_UI[mode] || MODE_UI.emails;
  $('searchOn').dataset.mode = mode || '';
  box().placeholder = ui.placeholder;
  $('resultsTitle').innerHTML = glyph(ui.glyph) + ui.title;
  if (WIDE.matches) { setOpen('filtersCard', true); setOpen('facetsCard', true); }
  renderOutlookCard(st, mode);
  $('searchOff').hidden = !!mode;
  $('searchOn').hidden = !mode;
  if (!mode) {
    $('searchOffText').textContent = 'Search isn\'t set up here. Folders: ' + st.folders.reason + '. Emails: ' + st.emails.reason + '.';
    head([{ text: 'Search is off', tone: 'attention' }], '');
  }
}

function writeHash() {
  const mode = effectiveMode();
  if (!mode || S.tab !== 'search') return;
  try { history.replaceState(null, '', '#search?mode=' + mode + '&q=' + encodeURIComponent(box().value)); } catch (e) { /* not kept in the URL */ }
}

// ----------------------------------------------------------------- results

function emailRow(h, i) {
  return '<li><button type="button" class="result-row" role="option" data-i="' + i + '" aria-selected="' + (i === S.selected) + '" title="' + esc(plain(h.subject) + ' - ' + plain(h.snippet)) + '" data-track="email-result">' +
    '<span class="rr-who">' + esc(h.from_name || h.from_addr || '(no sender)') + '</span><span class="rr-when">' + esc(mailDate(h.date)) + '</span>' +
    '<span class="rr-subj">' + marked(h.subject || '(no subject)') + '</span>' +
    '<span class="rr-snip">' + marked(h.snippet) + '</span>' +
    '<span class="rr-meta"><span class="chip">' + esc(shortFolder(h.folder)) + '</span>' +
    (h.attachments ? '<span title="' + plural(h.attachments, 'attachment') + '">' + glyph('paperclip') + '</span>' + h.attachments : '') +
    (h.flagged ? '<span class="is-flag" title="Flagged">' + glyph('flag') + '</span>' : '') + '</span></button></li>';
}

function folderRow(f, i, terms) {
  return '<li><button type="button" class="result-row is-folder" role="option" data-i="' + i + '" aria-selected="' + (i === S.selected) + '" title="' + esc(winPath(f.path)) + '" data-track="folder-result">' +
    '<span class="rr-who">' + glyph(f.people ? 'users' : 'folder') + '<span>' + hl(f.name, terms) + '</span></span>' +
    '<span class="rr-when">' + fmtBytes(f.size) + '</span>' +
    '<span class="rr-snip">' + hl(f.display, terms) + '</span>' +
    '<span class="rr-meta"><span class="rr-score">' + scoreChip(f.score) + '</span>' + plural(f.files, 'file') + (f.people ? ' · <span class="chip tone-accent">' + plural(f.people, 'person', 'people') + '</span>' : '') +
    (f.kind === 'synced' ? ' · ' + glyph('cloud') + 'OneDrive' : '') + '</span></button></li>';
}

function docRow(h, i, terms) {
  const where = [h.root_name].concat(h.folder).join(' › ');
  return '<li><button type="button" class="result-row is-doc" role="option" data-i="' + i + '" aria-selected="' + (i === S.selected) + '" title="' + esc(winPath(h.path)) + '" data-track="doc-result">' +
    '<span class="rr-who">' + glyph('file-text') + '<span>' + hl(h.name, terms) + '</span>' + cloudMark(h.online) + '</span>' +
    '<span class="rr-when">' + esc(fmtDate(h.mtime)) + '</span>' +
    '<span class="rr-snip">' + esc(where) + '</span>' +
    '<span class="rr-meta"><span>' + fmtBytes(h.size) + '</span>' + (h.online ? '<span class="chip">Online-only</span>' : '') + '</span></button></li>';
}

function emptyRow(icon, text, clear) {
  return '<li><div class="empty-state">' + glyph(icon, 'empty-state-icon') + '<p class="empty-state-message">' + esc(text) + '</p>' +
    (clear ? '<button type="button" class="empty-state-action" id="clearQuery" data-track="search-clear">Clear search</button>' : '') + '</div></li>';
}

function keysLine(mode) {
  const k = (keys, label) => '<span>' + keys.map((x) => '<kbd>' + x + '</kbd>').join('') + ' ' + label + '</span>';
  if (mode === 'docs') {
    return k(['/'], 'search') + k(['j', 'k'], 'move') + k(['Enter'], 'view') + (S.status.pc ? k(['e'], 'open in default app') : '') + k(['f'], 'show in Explore') + k(['c'], 'copy path') + k(['Esc'], 'back');
  }
  return mode === 'folders'
    ? k(['/'], 'search') + k(['j', 'k'], 'move') + (S.status.pc ? k(['e'], 'open in File Explorer') : '') + k(['f'], 'show in Explore') + k(['c'], 'copy path') + k(['Esc'], 'back')
    : k(['/'], 'search') + k(['j', 'k'], 'move') + k(['a'], 'show all') + (S.status.outlook.ready ? k(['o'], 'Outlook') : '') + (S.status.pc ? k(['e'], 'open folder') : '') + k(['f'], 'show folder') + k(['c'], 'copy path') + k(['Esc'], 'back');
}

function renderResults() {
  const mode = effectiveMode();
  const q = box().value.trim();
  const tools = $('resultsTools');
  const more = S.results.length < S.total && mode !== 'folders';
  $('resultsMeta').textContent = plural(S.total, MODE_UI[mode].noun);
  if (mode === 'emails') {
    tools.innerHTML = '<span class="segmented" role="group" aria-label="Sort" id="sortSeg">' +
      '<button type="button" class="segmented-item" aria-pressed="' + (S.sort === 'relevance') + '" data-value="relevance" data-track="sort-relevance">Best match</button>' +
      '<button type="button" class="segmented-item" aria-pressed="' + (S.sort === 'newest') + '" data-value="newest" data-track="sort-newest">Newest</button></span>';
  } else tools.innerHTML = '';
  const notes = S.notes.map((n) => '<li class="search-note">' + glyph('info') + '<span>' + esc(n) + '</span></li>').join('');
  let rows;
  if (mode === 'folders' && !q) rows = emptyRow('folder', 'Type part of a folder name. Every word must match.', false);
  else if (mode === 'docs' && !S.results.length) rows = emptyRow('file-text', q ? 'No document name matches every word.' : 'No Markdown or HTML files in the scanned roots.', !!q);
  else if (!S.results.length) rows = emptyRow(mode === 'folders' ? 'folder' : 'mail', mode === 'folders' ? 'No folder matches every word.' : 'No emails match. Remove a filter or use fewer words.', !!q);
  else {
    const terms = q.split(/[\s;]+/).filter(Boolean);
    const draw = { folders: folderRow, docs: docRow, emails: emailRow }[mode];
    rows = S.results.map((r, i) => draw(r, i, terms)).join('');
  }
  $('results').innerHTML = notes + rows +
    (more ? '<li class="results-more"><button type="button" class="button-ghost" id="moreResults" data-track="more-results">Show more</button></li>' : '');
  $('keysLine').innerHTML = keysLine(mode);
  head(S.notes.length ? [{ text: 'Some filters left out', tone: 'attention' }] : [],
    mode === 'folders' ? plural(S.total, 'folder') : mode === 'docs' ? plural(S.total, 'document') : plural(S.total, 'email'));
}

// ------------------------------------------------------------------- search

async function fetchResults(offset) {
  const mode = effectiveMode();
  const q = box().value;
  if (mode === 'folders') return getJSON('/api/search/folders?q=' + encodeURIComponent(q));
  if (mode === 'docs') return getJSON('/api/search/docs?q=' + encodeURIComponent(q) + '&offset=' + offset);
  return getJSON('/api/search/emails?q=' + encodeURIComponent(q) + '&sort=' + S.sort + '&offset=' + offset);
}

export async function runSearch() {
  const mode = effectiveMode();
  renderChrome();
  if (!mode) return;
  S.q[mode] = box().value;
  writeHash();
  renderChips();
  renderSaved();
  renderRecent();
  renderFilters();
  const seq = ++S.seq;
  let body;
  try {
    body = await fetchResults(0);
  } catch (e) {
    if (seq === S.seq) { S.results = []; S.total = 0; S.notes = [e.message]; S.facets = null; renderResults(); renderFacets(); }
    return;
  }
  if (seq !== S.seq) return;
  S.results = body.hits || body.results || [];
  S.total = body.total || 0;
  S.facets = body.facets || null;
  S.notes = (body.notes || []).concat(body.needs_rescan && body.needs_rescan.length ? ['Rescan to search folders in ' + body.needs_rescan.join(', ')] : [])
    .concat(body.truncated && mode === 'folders' ? ['Showing the best ' + S.results.length + ' folders; add a word to narrow it'] : []);
  S.words = body.words || [];
  S.selected = S.results.length ? 0 : -1;
  renderResults();
  renderFacets();
  renderPreview();
}

async function loadMore() {
  const seq = S.seq;
  const body = await fetchResults(S.results.length);
  if (seq !== S.seq) return;
  S.results = S.results.concat(body.hits || []);
  renderResults();
}

function onTyping() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => runSearch(), DEBOUNCE_MS);
}

/** Put ``text`` in the box and search now. */
function setBox(text) {
  box().value = text;
  if (timer) clearTimeout(timer);
  runSearch();
}

function select(i, open) {
  if (!S.results.length) return;
  S.selected = Math.max(0, Math.min(S.results.length - 1, i));
  document.querySelectorAll('#results .result-row').forEach((r) => r.setAttribute('aria-selected', String(Number(r.dataset.i) === S.selected)));
  const row = document.querySelector('#results .result-row[data-i="' + S.selected + '"]');
  if (row) { row.focus({ preventScroll: true }); row.scrollIntoView({ block: 'nearest' }); }
  renderPreview();
  if (open) openPreviewSheet();
}

// ------------------------------------------------------------------ wiring

async function saveSearch() {
  const q = box().value.trim();
  if (!q) { showToast('Type a search first'); return; }
  try {
    const saved = await postJSON('/api/searches', { mode: effectiveMode(), name: q.length > 24 ? q.slice(0, 23) + '…' : q, query: q });
    S.saved = S.saved.filter((s) => s.id !== saved.id).concat(saved);
    renderSaved();
    showToast('Saved. Relative dates stay relative.');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function forget(id) {
  const res = await api('/api/searches/' + id, { method: 'DELETE' });
  if (res.ok) { S.saved = S.saved.filter((s) => s.id !== id); renderSaved(); }
}

async function saveFolderOptions(update) {
  const fs = { ...S.status.folder_search, ...update };
  try {
    await postJSON('/api/config', { folder_search: fs });
    S.status.folder_search = fs;
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  runSearch();
}

function setMode(mode) {
  pushRecent(effectiveMode(), box().value);
  S.mode = mode;
  writePref('search.mode', mode);
  box().value = S.q[mode] || '';
  resetFilters();
  runSearch();
}

const CLICKS = [
  ['#modeSeg .segmented-item', (el) => setMode(el.dataset.value)],
  ['.qchip', (el) => setBox(withoutChip(Number(el.dataset.chip)))],
  ['.saved-chip', (el) => { const s = S.saved.find((x) => x.id === Number(el.dataset.saved)); if (s) { setBox(s.query); pushRecent(s.mode, s.query); } }],
  ['.saved-remove', (el) => forget(Number(el.dataset.unsave))],
  ['#saveSearch', () => saveSearch()],
  ['.recent-item', (el) => setBox(el.dataset.recent)],
  ['.recent-remove', (el) => { dropRecent(effectiveMode(), el.dataset.unrecent); renderRecent(); ($('recentList').querySelector('.recent-head') || box()).focus(); }],
  ['.recent-clear', () => { clearRecent(effectiveMode()); renderRecent(); box().focus(); }],
  ['.recent-head', (el) => {
    const open = el.getAttribute('aria-expanded') !== 'true';
    setRecentOpen(open);
    el.setAttribute('aria-expanded', String(open));
    $('recentItems').hidden = !open;
  }],
  ['#clearQuery', () => { setBox(''); box().focus(); }],
  ['.facet', (el) => setBox(withFacet(el.dataset.op, el.dataset.val, el.getAttribute('aria-pressed') === 'true'))],
  ['#sortSeg .segmented-item', (el) => { S.sort = el.dataset.value; runSearch(); }],
  ['.result-row', (el) => select(Number(el.dataset.i), true)],
  ['#moreResults', () => loadMore().catch((e) => showToast(e.message, 'error'))],
  ['#fDateSeg .segmented-item', (el) => setBox(withDate(el.dataset.value))],
  ['#fHas', (el) => setBox(toggled('has', el.classList.contains('on'), 'attachment'))],
  ['#fFlag', (el) => setBox(toggled('is', el.classList.contains('on'), 'flagged'))],
  ['#skipDown', () => saveFolderOptions({ skip_levels: Math.max(0, S.status.folder_search.skip_levels - 1) })],
  ['#skipUp', () => saveFolderOptions({ skip_levels: Math.min(8, S.status.folder_search.skip_levels + 1) })],
  ['#pruneSwitch', () => saveFolderOptions({ prune_people: !S.status.folder_search.prune_people })],
  ['#onedriveSwitch', () => saveFolderOptions({ onedrive_prevails: !S.status.folder_search.onedrive_prevails })],
  ['#keysInfo', () => $('keysDialog').showModal()],
  ['.side-toggle', (el) => setOpen(el.closest('.side-card').id, el.getAttribute('aria-expanded') !== 'true')],
];

function wire() {
  $('paneSearch').addEventListener('click', (ev) => {
    for (const [sel, fn] of CLICKS) {
      const el = ev.target.closest(sel);
      if (el) { fn(el); return; }
    }
  });
  const previewClick = (ev) => {
    const el = ev.target.closest('[data-act]');
    if (el && !el.disabled) previewAction(el.dataset.act);
  };
  $('previewDock').addEventListener('click', previewClick);
  $('previewSheetBody').addEventListener('click', previewClick);
  $('paneSearch').addEventListener('input', (ev) => {
    if (ev.target.id === 'q') { onTyping(); return; }
    const key = FIELD_OPS[ev.target.id];
    if (key && ev.target.tagName === 'INPUT' && ev.target.type === 'text') {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => setBox(withOp(box().value, key, ev.target.value.trim())), DEBOUNCE_MS);
    }
  });
  $('paneSearch').addEventListener('change', (ev) => {
    const key = FIELD_OPS[ev.target.id];
    if (key && (ev.target.tagName === 'SELECT' || ev.target.type === 'date')) setBox(withOp(box().value, key, ev.target.value));
  });
  box().addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown' && S.results.length) { ev.preventDefault(); select(Math.max(0, S.selected), false); }
    else if (ev.key === 'Enter') { pushRecent(effectiveMode(), box().value); setBox(box().value); }
  });
  box().addEventListener('blur', () => pushRecent(effectiveMode(), box().value));
  document.addEventListener('keydown', onKey);
  WIDE.addEventListener('change', () => { renderChrome(); renderPreview(); });
}

// One key means the same thing on both cards (e opens the folder, c copies its path, f shows it), and each runs the action the card's button runs.
const RESULT_KEYS = {
  emails: { a: 'showall', o: 'outlook', e: 'openfolder', c: 'copy', f: 'folder' },
  folders: { e: 'explorer', c: 'copy', f: 'folder' },
  docs: { e: 'default', c: 'copy', f: 'folder' },
};

function onKey(ev) {
  if (!nav || nav.getTab() !== 'search' || document.querySelector('dialog[open]:not(#previewDialog)')) return;
  const t = ev.target;
  const typing = t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT';
  if (ev.key === '/' && !typing) { ev.preventDefault(); box().focus(); box().select(); return; }
  if (typing || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const mode = effectiveMode();
  const k = ev.key;
  if (k === 'j' || k === 'ArrowDown') { ev.preventDefault(); select(S.selected + 1, false); }
  else if (k === 'k' || k === 'ArrowUp') { ev.preventDefault(); select(S.selected - 1, false); }
  else if (k === 'Enter' && t.classList.contains('result-row')) { ev.preventDefault(); openPreviewSheet(); }
  else if (RESULT_KEYS[mode][k]) previewAction(RESULT_KEYS[mode][k]);
  else if (k === 'Escape' && !$('previewDialog').open) box().focus();
}

function readHash() {
  const m = /^#search\?mode=(emails|folders|docs)&q=(.*)$/.exec(location.hash);
  if (!m) return;
  S.mode = m[1];
  try { S.q[m[1]] = decodeURIComponent(m[2]); } catch (e) { S.q[m[1]] = ''; }
}

export function initSearch(opts) {
  nav = opts.nav;
  initPreview({ showInExplore: opts.showInExplore });
  readHash();
  box().value = S.q[S.mode];
  wire();
}

/** Load what Search needs, the first time its tab shows and on every later visit. */
export async function loadSearch() {
  S.tab = 'search';
  try {
    const [status, saved] = await Promise.all([getJSON('/api/search/status'), getJSON('/api/searches')]);
    const before = effectiveMode();
    S.status = status;
    S.saved = saved.searches;
    // A mode that went off (Documents switched off in Settings) hands the box to the one that runs instead.
    const after = effectiveMode();
    if (before && after && before !== after) { S.q[before] = box().value; box().value = S.q[after] || ''; }
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  if (!booted) {
    booted = true;
    const mode = effectiveMode();
    if (mode && box().value === '' && S.q[mode]) box().value = S.q[mode];
  }
  resetFilters();
  await runSearch();
}

/** The tab is no longer showing: stop writing the URL. */
export function leaveSearch() {
  S.tab = null;
  try { if (location.hash.startsWith('#search')) history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* not kept in the URL */ }
}

