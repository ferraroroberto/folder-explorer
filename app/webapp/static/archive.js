/* The Search tab's Archive card, the Archive sheet, the Outlook macro sheet, and the macro's hand-off.
 *
 * Every Outlook step runs on the server in a child of its own; the page only
 * holds a prepared mail's token. A page on this PC holds a poll open for a
 * mail the Outlook macro hands over while it is visible, opens the sheet the
 * moment one arrives, and fills its folders in when the same token comes
 * again ranked.
 */

import { api, getJSON, postJSON } from './api.js';
import { mailDate } from './search-state.js';
import { bar, esc, glyph, plural, winPath } from './fmt.js';
import { showToast } from './_vendored/toast/toast.js';
import { bindCodeListing } from './_vendored/code-listing/code-listing.js';

const $ = (id) => document.getElementById(id);
// How long the server may hold a visible page's hand-off poll open; it answers the moment the macro hands a mail over.
const PENDING_WAIT_S = 20;
// A poll answered sooner than this, with no mail, came from a server that doesn't hold polls open:
// the page then asks again after PENDING_MS.
const HELD_MS = 1000;
// How long a page waits before asking again when it can't hold a poll open (hidden, an error, a quick answer).
const PENDING_MS = 3000;
// How long typing in "Another email folder" pauses before it searches.
const DEBOUNCE_MS = 200;
// Below this blended score a suggestion is a weak guess, and its % chip says so.
const WEAK_SCORE = 0.35;
const DATE_CHOICES = [['auto', 'Auto'], ['on', 'On'], ['off', 'Off']];

const A = {
  status: null, // /api/search/status .archive
  doc: null, // the prepared mail, as /api/archive/current or the hand-off gives it
  fromMacro: false,
  choice: 0,
  extra: null, // a folder picked from "Another email folder"
  category: '',
  copy: false, // the user asked for another copy of a mail that is already archived
  datePrefix: 'auto', // auto (the folder's own form), on or off
  previewOpen: false, // the mail preview stays open across repaints
  busy: false,
  opening: false,
  error: null,
  lastFiled: '',
  found: [], // the last "Another email folder" answer
  reading: false, // the server is asking Windows for the front File Explorer folder
  explorerError: null, // why the Explorer folder could not be offered: {title, message}
};

// What a refused Explorer folder is called, by the server's error code; each is a different fix for the user.
const EXPLORER_TITLES = {
  explorer_unavailable: 'Cannot read Explorer windows',
  no_explorer_folder: 'No Explorer folder open',
  outside_email_folders: 'Outside the email folders',
};

let pollTimer = null;
let polling = false; // a hand-off poll is in flight
let searchTimer = null;

const lastTwo = (path) => (path || '').split(/[\\/]/).filter(Boolean).slice(-2).join('\\');

/** ``path`` below the email folder that holds it, with Windows separators, as the sheet lists a folder. */
function underRoot(path) {
  const win = winPath(path);
  const lower = win.toLowerCase();
  for (const r of (A.doc && A.doc.roots) || []) {
    const root = winPath(r).replace(/\\+$/, '').toLowerCase();
    if (lower.startsWith(root + '\\')) return win.slice(root.length + 1);
  }
  return win;
}

const options = () => (A.extra ? [A.extra] : []).concat((A.doc && A.doc.suggestions) || []);

// ---------------------------------------------------------------- card

/** The card leads the side column on the PC in email mode; it says why when archiving can't run here. */
export function renderOutlookCard(status, mode) {
  A.status = status ? status.archive : null;
  const card = $('outlookCard');
  card.hidden = !(status && status.pc && mode === 'emails');
  if (card.hidden) return;
  const ready = !!(A.status && A.status.ready);
  const btn = $('openArchive');
  btn.disabled = !ready || A.opening;
  btn.classList.toggle('is-busy', A.opening);
  $('outlookMeta').innerHTML = !ready ? '<span class="tone-attention-text">' + esc((A.status && A.status.reason) || 'Not set up here') + '</span>'
    : esc(A.opening ? 'Reading the mail from Outlook…' : A.lastFiled ? 'Last filed to ' + A.lastFiled : 'The mail open or selected in Outlook (classic)');
}

function repaintCard() {
  if (!$('outlookCard').hidden) renderOutlookCard({ pc: true, archive: A.status }, 'emails');
}

// --------------------------------------------------------------- sheet

function errorBlock(title, message, retry) {
  return '<div class="pv-error" role="alert">' + glyph('triangle-alert') + '<div><strong>' + esc(title) + '</strong><p>' + esc(message) + '</p>' +
    (retry ? '<button type="button" class="button-ghost" id="archRetry" data-track="archive-retry">Try again</button>' : '') + '</div></div>';
}

/** The % match as a chip: neutral for a real match, attention for a weak guess. Folder search shows the same chip. */
export function scoreChip(score) {
  const weak = score < WEAK_SCORE;
  return '<span class="chip' + (weak ? ' tone-attention' : '') + '" title="' + (weak ? 'A weak match' : 'Match') + '">' +
    Math.round(score * 100) + '%</span>';
}

/** The % chip with a bar showing the same share. */
function scoreHtml(score) {
  return '<span class="ao-score">' + scoreChip(score) + bar(Math.round(score * 100)) + '</span>';
}

function similarHtml(o) {
  const bits = [];
  if (o.similar) bits.push(plural(o.similar, 'similar email'));
  if (o.example) bits.push('e.g. “' + o.example + '”');
  return bits.length ? '<span class="ao-similar">' + esc(bits.join(' · ')) + '</span>' : '';
}

function openButton(o, i) {
  const what = o.location ? 'Open this folder in File Explorer' : 'Not in the last disk scan yet, so it can\'t be opened from here';
  return '<button type="button" class="icon-button ao-open" data-open-choice="' + i + '" aria-label="' + esc(what) + '" title="' + esc(what) + '"' +
    (o.location ? '' : ' disabled') + ' data-track="archive-open-folder">' + glyph('folder-open') + '</button>';
}

function optionHtml(o, i) {
  const on = i === A.choice;
  // A picked folder leads the list unranked; the suggestions keep their own rank after it.
  const rank = A.extra ? i : i + 1;
  return '<li class="arch-row"><button type="button" class="arch-opt" role="radio" aria-checked="' + on + '" data-choice="' + i + '" data-track="archive-choice">' +
    glyph(on ? 'circle-dot' : 'circle') +
    '<span class="ao-line">' + (typeof o.score === 'number' ? '<span class="rank">' + rank + '</span>' + scoreHtml(o.score) : '') +
    '<span class="ao-path">' + esc(underRoot(o.folder_path)) + '</span></span>' +
    '<span class="ao-why">' + (o.why || []).map((w) => '<span class="chip' + (w.tone ? ' tone-' + w.tone : '') + '">' + esc(w.label) + '</span>').join('') +
    esc(o.note || '') + '</span>' + similarHtml(o) + '</button>' + openButton(o, i) + '</li>';
}

function mailHtml(mail) {
  const when = mail.date_sent ? mailDate(mail.date_sent, true) : '';
  const bits = [mail.sender || '(no sender)', when, mail.attachment_count ? plural(mail.attachment_count, 'attachment') : '', mail.where].filter(Boolean);
  return '<div class="arch-mail"><div class="arch-mail-head"><strong>' + esc(mail.subject || '(no subject)') + '</strong><span>' + esc(bits.join(' · ')) + '</span></div>' +
    '<details class="arch-preview"' + (A.previewOpen ? ' open' : '') + '><summary data-track="archive-preview"><span>Preview</span>' +
    glyph('chevron-right', 'summary-chevron') + '</summary>' +
    '<p>' + esc(mail.preview || '(no readable text)') + '</p></details></div>';
}

function datePrefixHtml() {
  return '<div class="row"><span>Date prefix</span><span class="segmented" role="group" aria-label="Date prefix" id="archDate">' +
    DATE_CHOICES.map(([v, label]) => '<button type="button" class="segmented-item" aria-pressed="' + (A.datePrefix === v) + '" data-date="' + v +
      '" data-track="archive-date-' + v + '">' + label + '</button>').join('') + '</span></div>';
}

/** The name the chosen folder would give the mail under the date-prefix choice. */
function savedAs(choice) {
  const names = choice && choice.saved_as;
  return names && names[A.datePrefix] ? names[A.datePrefix] : 'Numbered after the folder\'s last mail';
}

function renderArchive() {
  const d = A.doc;
  const source = A.fromMacro ? '<p class="arch-source">' + glyph('archive') + 'Opened from the Archive button in Outlook</p>' : '';
  $('archiveActions').hidden = !d.ok;
  if (!d.ok) {
    $('archiveBody').innerHTML = source + errorBlock('Couldn\'t open the mail', d.error.message, !A.fromMacro);
    $('archiveActions').innerHTML = '';
    return;
  }
  const opts = options();
  const choice = opts[A.choice] || null;
  const filed = d.already_archived;
  $('archiveBody').innerHTML = source + mailHtml(d.mail) +
    (filed ? '<p class="arch-filed tone-attention-text">Already archived as ' + esc(underRoot(filed)) + '</p>' +
      '<div class="row"><span>Archive a copy anyway</span><button type="button" class="toggle' + (A.copy ? ' on' : '') + '" role="switch" aria-checked="' + A.copy +
      '" id="archCopy" aria-label="Archive a copy anyway" data-track="archive-copy"><span class="knob"></span><span class="toggle-label">' + (A.copy ? 'ON' : 'OFF') + '</span></button></div>' : '') +
    '<div class="arch-head"><h3>Suggested folders</h3><button type="button" class="icon-button" id="rankInfo" aria-label="How folders are suggested" title="How folders are suggested" data-track="rank-info">' + glyph('info') + '</button></div>' +
    '<p class="row-note" id="rankNote" hidden>Each folder scores by its best match: the same subject first, then the sender and the topic. A big folder doesn\'t win by volume.</p>' +
    (opts.length ? '<ul class="arch-options" role="radiogroup" aria-label="Archive folder">' + opts.map(optionHtml).join('') + '</ul>'
      : d.ranking ? '<p class="row-note" id="archRanking">Finding folders…</p>'
        : '<p class="row-note">No folder matches this mail yet. Find one below.</p>') +
    '<div class="arch-other"><label class="search-field">' + glyph('search') +
    '<input type="search" id="archSearch" placeholder="Another email folder" aria-label="Find another email folder" autocomplete="off" spellcheck="false" data-track="archive-search"></label>' +
    '<ul class="arch-found" id="archFound"></ul>' +
    '<button type="button" class="button-surface" id="archExplorer" title="Use the folder open in the front File Explorer window"' + (A.reading ? ' disabled' : '') +
    ' data-track="archive-explorer">' + glyph('folder-open') + (A.reading ? 'Reading File Explorer…' : 'Current Explorer folder') + '</button>' +
    (A.explorerError ? errorBlock(A.explorerError.title, A.explorerError.message, false) : '') + '</div>' +
    '<div class="row"><span>Outlook category</span><select class="select-native" id="archCat" aria-label="Outlook category" data-track="archive-category">' +
    (d.category ? '<option value="' + esc(d.category) + '"' + (A.category === d.category ? ' selected' : '') + '>' + esc(d.category) + '</option>' : '') +
    '<option value=""' + (A.category ? '' : ' selected') + '>No category</option></select></div>' +
    datePrefixHtml() +
    '<div class="row"><span>Saved as</span><span class="row-value mono">' + esc(savedAs(choice)) + '</span></div>' +
    '<div class="arch-foot">' + glyph('mail') + '<span>Outlook macro: archive straight from Outlook\'s ribbon.</span>' +
    '<button type="button" class="icon-button" id="archMacro" aria-label="About the Outlook macro" title="About the Outlook macro" data-track="macro-info">' + glyph('info') + '</button></div>' +
    (A.error ? errorBlock('Not archived', A.error.message, false) : '');
  $('archiveActions').innerHTML = '<button type="button" class="button-primary detail-save-btn" id="archiveGo" data-track="archive-go"' +
    (choice && (!filed || A.copy) && !A.busy ? '' : ' disabled') + '>' +
    esc(A.busy ? 'Archiving…' : choice ? (filed ? 'Archive a copy to ' : 'Archive to ') + lastTwo(choice.folder_path) : 'Pick a folder') + '</button>';
}

function showSheet(doc, fromMacro) {
  A.doc = doc;
  A.fromMacro = fromMacro;
  A.choice = 0;
  A.extra = null;
  A.error = null;
  A.copy = false;
  A.datePrefix = 'auto';
  A.previewOpen = false;
  A.busy = false;
  A.reading = false;
  A.explorerError = null;
  A.category = doc.ok ? doc.category || '' : '';
  renderArchive();
  if (!$('archiveDialog').open) $('archiveDialog').showModal();
}

async function openArchive() {
  if (A.opening || !(A.status && A.status.ready)) return;
  A.opening = true;
  repaintCard();
  try {
    showSheet(await postJSON('/api/archive/current', {}), false);
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    A.opening = false;
    repaintCard();
  }
}

async function doArchive() {
  const o = options()[A.choice];
  if (!o || A.busy) return;
  A.busy = true;
  A.error = null;
  renderArchive();
  let r;
  try {
    const payload = { token: A.doc.token, folder: o.folder_path, category: A.category, date_prefix: A.datePrefix };
    if (A.doc.already_archived && A.copy) payload.duplicate = true;
    r = await postJSON('/api/archive/file', payload);
  } catch (e) {
    r = { ok: false, error: { message: e.message } };
  }
  A.busy = false;
  if (r.files && r.files.length) {
    $('archiveDialog').close();
    A.lastFiled = lastTwo(r.folder_path);
    repaintCard();
    if (r.ok) showToast('Archived to ' + A.lastFiled + (r.indexing ? '. Indexing the folder now.' : ''));
    else showToast('Filed to ' + A.lastFiled + ', but not tagged: ' + r.error.message, 'error');
    return;
  }
  A.error = r.error || { message: 'Not archived' };
  renderArchive();
}

/** Show an offered folder in File Explorer, named by its place in the scan, never by its path. */
async function openFolder(o) {
  if (!o || !o.location) return;
  try {
    await postJSON('/api/open', { root: o.location.root, scan_id: o.location.scan_id, kind: 'folder', id: o.location.id, verb: 'explore' });
    showToast('Opened in File Explorer');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function findFolders(q) {
  const list = $('archFound');
  if (!list) return;
  if (!q.trim()) { list.innerHTML = ''; return; }
  let found = [];
  try {
    found = (await getJSON('/api/archive/folders?q=' + encodeURIComponent(q))).folders;
  } catch (e) {
    list.innerHTML = '<li><p class="row-note">' + esc(e.message) + '</p></li>';
    return;
  }
  if ($('archSearch') && $('archSearch').value !== q) return;
  A.found = found;
  list.innerHTML = found.length ? found.map((f, i) => '<li><button type="button" data-found="' + i + '" data-track="archive-found">' + esc(underRoot(f.path)) + '</button></li>').join('')
    : '<li><p class="row-note">No email folder matches every word.</p></li>';
}

function pickFound(f) {
  A.extra = { folder_path: f.path, why: [{ label: 'Chosen', tone: '' }], note: 'picked from the folder index', saved_as: null, location: f.location };
  A.choice = 0;
  renderArchive();
}

/** Offer the folder open in the front File Explorer window; the full path is shown with it and the user still presses Archive. */
async function useExplorerFolder() {
  if (A.reading) return;
  A.reading = true;
  A.explorerError = null;
  renderArchive();
  let r;
  try {
    r = await postJSON('/api/archive/explorer', {});
  } catch (e) {
    r = { ok: false, error: { code: '', message: e.message } };
  }
  A.reading = false;
  if (r.ok) {
    A.extra = { folder_path: r.folder_path, why: [{ label: 'Explorer window', tone: '' }], note: 'open in File Explorer: ' + winPath(r.folder_path),
      saved_as: null, location: r.location };
    A.choice = 0;
  } else {
    A.explorerError = { title: EXPLORER_TITLES[r.error.code] || 'No Explorer folder', message: r.error.message };
  }
  renderArchive();
}

// --------------------------------------------------------------- macro

// What the user environment says about FOLDER_EXPLORER_HOME, in words; the state also sets the colour.
function homeStateText(s) {
  if (s.state === 'ok') return 'Set — it points at this folder.';
  if (s.state === 'different') return 'Set, but to another folder: ' + s.current;
  if (s.state === 'unset') return 'Not set yet.';
  return 'Couldn\'t read the user environment, so whether it is set is not confirmed.';
}

function renderSetup(setup) {
  $('setupBox').hidden = !setup;
  $('setupPcOnly').hidden = !!setup;
  if (!setup) return;
  $('setxText').textContent = setup.command;
  const el = $('homeState');
  el.textContent = homeStateText(setup);
  el.classList.toggle('is-ok', setup.state === 'ok');
  el.classList.toggle('is-warn', setup.state !== 'ok');
}

// The variable can change while the page stays open, so the sheet asks again each time it opens.
async function openMacro() {
  try {
    const doc = await getJSON('/api/archive/macro');
    $('macroText').textContent = doc.text;
    renderSetup(doc.setup);
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  if (!$('macroDialog').open) $('macroDialog').showModal();
}

// ------------------------------------------------------------- hand-off

/** A hand-off from the macro: a new mail opens the sheet; the same one again brings its ranked folders. */
function takeHandoff(doc) {
  if (!(A.doc && doc.token && A.doc.token === doc.token)) {
    showSheet(doc, true);
    return;
  }
  // The user closed the sheet before the folders came: leave it closed.
  if (!$('archiveDialog').open) return;
  // Keep what the user already typed in "Another email folder" across the repaint.
  const search = $('archSearch');
  const typed = search ? search.value : '';
  const focused = search && document.activeElement === search;
  A.doc = doc;
  renderArchive();
  const again = $('archSearch');
  if (again && typed) again.value = typed;
  if (again && focused) again.focus();
}

async function pollPending() {
  pollTimer = null;
  if (polling) return;
  let next = PENDING_MS;
  if (document.visibilityState === 'visible') {
    polling = true;
    const asked = Date.now();
    let res = null;
    try { res = await api('/api/archive/pending?wait=' + PENDING_WAIT_S); } catch (e) { res = null; }
    polling = false;
    // Not this PC: the macro only ever hands a mail to the PC's own pages.
    if (res && res.status === 403) return;
    if (res && res.ok) {
      const body = await res.json();
      if (body.mail) { takeHandoff(body.mail); next = 0; }
      // A server that held the poll open is waiting for hand-offs: ask again at once.
      else if (Date.now() - asked >= HELD_MS) next = 0;
    }
  }
  if (!pollTimer) pollTimer = setTimeout(pollPending, next);
}

/** Start asking for the macro's hand-off; called once the page has signed in. */
export function startArchivePoll() {
  if (!pollTimer && !polling) pollPending();
}

// --------------------------------------------------------------- wiring

export function initArchive() {
  $('openArchive').addEventListener('click', () => openArchive());
  $('showMacro').addEventListener('click', () => { $('settingsDialog').close(); openMacro(); });
  bindCodeListing();
  const body = $('archiveBody');
  $('archiveActions').addEventListener('click', (ev) => {
    const el = ev.target.closest('button');
    if (!el || el.disabled) return;
    if (el.id === 'archiveGo') doArchive();
  });
  body.addEventListener('click', (ev) => {
    const el = ev.target.closest('button');
    if (!el || el.disabled) return;
    if (el.classList.contains('arch-opt')) { A.choice = Number(el.dataset.choice); renderArchive(); }
    else if (el.id === 'archCopy') { A.copy = !A.copy; renderArchive(); }
    else if (el.id === 'rankInfo') $('rankNote').hidden = !$('rankNote').hidden;
    else if (el.dataset.openChoice) openFolder(options()[Number(el.dataset.openChoice)]);
    else if (el.dataset.date) { A.datePrefix = el.dataset.date; renderArchive(); }
    else if (el.dataset.found) pickFound(A.found[Number(el.dataset.found)]);
    else if (el.id === 'archExplorer') useExplorerFolder();
    else if (el.id === 'archRetry') { $('archiveDialog').close(); openArchive(); }
    else if (el.id === 'archMacro') openMacro();
  });
  body.addEventListener('input', (ev) => {
    if (ev.target.id !== 'archSearch') return;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => findFolders(ev.target.value), DEBOUNCE_MS);
  });
  // toggle does not bubble, so the sheet listens in the capture phase.
  body.addEventListener('toggle', (ev) => { if (ev.target.classList.contains('arch-preview')) A.previewOpen = ev.target.open; }, true);
  body.addEventListener('change', (ev) => { if (ev.target.id === 'archCat') A.category = ev.target.value; });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && pollTimer && !polling) { clearTimeout(pollTimer); pollPending(); }
  });
}
