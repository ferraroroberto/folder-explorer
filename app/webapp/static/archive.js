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
import { setSwitch, switchEl } from './_vendored/switch/switch.js';
import { bindCodeListing } from './_vendored/code-listing/code-listing.js';

const $ = (id) => document.getElementById(id);
// How long the server may hold a visible page's hand-off poll open; it answers the moment the macro hands a mail over.
const PENDING_WAIT_S = 20;
// A poll answered sooner than this, with no mail, came from a server that doesn't hold polls open:
// the page then asks again after PENDING_MS.
const HELD_MS = 1000;
// How long a page waits before asking again when it can't hold a poll open (hidden, an error, a quick answer).
const PENDING_MS = 3000;
// How long typing in "Find a folder" pauses before it searches.
const DEBOUNCE_MS = 200;
// Below this blended score a suggestion is a weak guess, and its % chip says so.
const WEAK_SCORE = 0.35;
const DATE_CHOICES = [['auto', 'Auto'], ['on', 'On'], ['off', 'Off']];

const A = {
  status: null, // /api/search/status .archive
  doc: null, // the prepared mail, as /api/archive/current or the hand-off gives it
  fromMacro: false,
  choice: 0,
  extra: null, // a folder picked from "Find a folder"
  category: '',
  copy: false, // the user asked for another copy of a mail that is already archived
  datePrefix: 'auto', // auto (the folder's own form), on or off
  previewOpen: false, // the mail preview stays open across repaints
  busy: false,
  opening: false,
  error: null,
  lastFiled: '',
  query: '', // what "Find a folder" holds
  found: null, // its last answer: {folders, total}, or null before a search
  trail: [], // the folders stepped into from it: {name, folders, total}, the last one listed
  sel: -1, // the listed folder the actions apply to
  stepping: false, // the server is listing a folder's subfolders
  foundNote: '', // why the list could not be filled, said in its place
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
let searchSeq = 0; // the newest search or step in; an older answer arriving late is dropped

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
    '<input type="search" id="archSearch" placeholder="Find a folder" aria-label="Find a folder" autocomplete="off" spellcheck="false" value="' + esc(A.query) +
    '" data-track="archive-search"></label>' +
    '<ul class="arch-found" id="archFound">' + foundHtml() + '</ul>' +
    '<button type="button" class="button-surface" id="archExplorer" title="Use the folder open in the front File Explorer window"' + (A.reading ? ' disabled' : '') +
    ' data-track="archive-explorer">' + glyph('folder-open') + (A.reading ? 'Reading File Explorer…' : 'Current Explorer folder') + '</button>' +
    (A.explorerError ? errorBlock(A.explorerError.title, A.explorerError.message, false) : '') + '</div>' +
    (d.add_category ? '<div class="row"><span>Outlook category</span><select class="select-native" id="archCat" aria-label="Outlook category" data-track="archive-category">' +
      (d.category ? '<option value="' + esc(d.category) + '"' + (A.category === d.category ? ' selected' : '') + '>' + esc(d.category) + '</option>' : '') +
      '<option value=""' + (A.category ? '' : ' selected') + '>No category</option></select></div>' : '') +
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
  A.query = '';
  A.found = null;
  A.trail = [];
  A.sel = -1;
  A.stepping = false;
  A.foundNote = '';
  searchSeq++;
  A.category = doc.ok && doc.add_category ? doc.category || '' : '';
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
  const token = A.doc.token;
  try {
    const payload = { token, folder: o.folder_path, category: A.category, date_prefix: A.datePrefix };
    if (A.doc.already_archived && A.copy) payload.duplicate = true;
    r = await postJSON('/api/archive/file', payload);
  } catch (e) {
    r = { ok: false, error: { message: e.message } };
  }
  A.busy = false;
  if (r.files && r.files.length) {
    // The answer means the .msg is on disk: the sheet goes now, and the tag and the indexing report on their own.
    $('archiveDialog').close();
    A.lastFiled = lastTwo(r.folder_path);
    repaintCard();
    showToast('Archived to ' + A.lastFiled);
    if (r.follow_up) followUp(token);
    return;
  }
  A.error = r.error || { message: 'Not archived' };
  renderArchive();
}

// Rounds of the server's longest wait (20 s each) that outlast its own limit on what follows a save.
const FOLLOW_UP_ROUNDS = 4;
const NOT_CONFIRMED = "The mail was filed, but its category and indexing couldn't be confirmed";

/** What followed a filing: nothing to say when it went well, an error toast when it failed or can't be confirmed. */
async function followUp(token) {
  for (let round = 0; round < FOLLOW_UP_ROUNDS; round++) {
    let r;
    try {
      r = await getJSON('/api/archive/followup?token=' + encodeURIComponent(token) + '&wait=20');
    } catch (e) {
      showToast(NOT_CONFIRMED + ': ' + e.message, 'error');
      return;
    }
    if (r.done) {
      if (!r.result.ok) showToast((r.result.error && r.result.error.message) || NOT_CONFIRMED + '.', 'error');
      return;
    }
  }
  showToast(NOT_CONFIRMED + ' in time.', 'error');
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

/** The folders listed now: the last one stepped into, else the search's own. */
const listed = () => (A.trail.length ? A.trail[A.trail.length - 1] : A.found) || { folders: [], total: 0 };

/** The list under the search box: the breadcrumb while stepped in, the folders, and the actions for the selected one. */
function foundHtml() {
  // Nothing to say before the first answer: a hint of "no match" while the search is still out would be wrong.
  if (!A.trail.length && (!A.query.trim() || (!A.found && !A.foundNote))) return '';
  const { folders, total } = listed();
  const out = [];
  if (A.trail.length) {
    out.push('<li class="arch-crumb"><button type="button" class="icon-button" id="archBack" aria-label="Back" title="Back" data-track="archive-back">' + glyph('chevron-left') +
      '</button><span class="arch-crumb-path">' + esc(A.trail.map((l) => l.name).join(' › ')) + '</span></li>');
  }
  const note = A.foundNote || (folders.length ? '' : A.trail.length ? 'No subfolders.' : 'No folder matches every word.');
  if (note) out.push('<li><p class="row-note' + (A.foundNote ? ' tone-danger-text' : '') + '">' + esc(note) + '</p></li>');
  folders.forEach((f, i) => {
    out.push('<li class="arch-found-row"><button type="button" class="arch-pick' + (f.fileable ? '' : ' is-outside') + '" data-found="' + i + '" aria-pressed="' + (i === A.sel) +
      '" data-track="archive-found"><span class="ao-name">' + esc(f.name) + '</span>' + (f.fileable ? '' : '<span class="chip">Outside the email folders</span>') +
      '<span class="ao-path">' + esc(underRoot(f.path)) + '</span></button>' +
      '<button type="button" class="icon-button" data-step="' + i + '" aria-label="Subfolders of ' + esc(f.name) + '" title="Subfolders"' + (A.stepping ? ' disabled' : '') +
      ' data-track="archive-step-in">' + glyph('chevron-right') + '</button></li>');
  });
  if (total > folders.length) out.push('<li><p class="row-note">' + plural(total, 'folder') + ', first ' + folders.length + ' shown</p></li>');
  const here = folders[A.sel];
  if (here) {
    out.push('<li class="arch-found-actions"><button type="button" class="button-tint" id="archHere" data-track="archive-here"' +
      (here.fileable && !A.busy ? '' : ' disabled') + '>Archive here</button>' +
      '<button type="button" class="button-surface" id="archFoundOpen" data-track="archive-found-open">' + glyph('folder-open') + 'Open in File Explorer</button></li>');
    if (!here.fileable) out.push('<li><p class="row-note">Outside the email folders: pick a folder inside them to archive.</p></li>');
  }
  return out.join('');
}

async function findFolders(q) {
  const seq = ++searchSeq;
  A.query = q;
  A.trail = [];
  A.sel = -1;
  A.foundNote = '';
  A.stepping = false;
  if (!q.trim()) {
    A.found = null;
    paintFound();
    return;
  }
  try {
    const found = await getJSON('/api/archive/folders?q=' + encodeURIComponent(q));
    if (seq !== searchSeq) return;
    A.found = found;
  } catch (e) {
    if (seq !== searchSeq) return;
    A.found = null;
    A.foundNote = e.message;
  }
  paintFound();
}

/** Repaint only the folder list, so typing in the box and its focus are left alone. */
function paintFound() {
  const list = $('archFound');
  if (list) list.innerHTML = foundHtml();
}

/** Step into a listed folder's subfolders: the server looks it up by its place in the scan. */
async function stepIn(i) {
  const f = listed().folders[i];
  if (!f || A.stepping) return;
  const seq = ++searchSeq;
  A.stepping = true;
  paintFound();
  const place = f.location;
  try {
    const r = await getJSON('/api/archive/subfolders?root=' + encodeURIComponent(place.root) + '&scan_id=' + place.scan_id + '&id=' + place.id);
    if (seq !== searchSeq) return;
    A.trail.push({ name: f.name, folders: r.folders, total: r.total });
    A.sel = -1;
    A.foundNote = '';
  } catch (e) {
    if (seq !== searchSeq) return;
    A.foundNote = e.message;
  }
  A.stepping = false;
  paintFound();
}

function stepBack() {
  if (!A.trail.length) return;
  searchSeq++;
  A.stepping = false;
  A.trail.pop();
  A.sel = -1;
  A.foundNote = '';
  paintFound();
}

/** Select a listed folder; one a mail can go in also becomes the chosen folder, which the button below names. */
function pickFound(i) {
  const f = listed().folders[i];
  if (!f) return;
  A.sel = i;
  if (f.fileable) {
    A.extra = { folder_path: f.path, why: [{ label: 'Chosen', tone: '' }], note: 'picked from the folder index', saved_as: null, location: f.location };
    A.choice = 0;
  }
  renderArchive();
}

/** File the mail in the selected folder at once, with the sheet's category, copy and date-prefix choices. */
function archiveHere() {
  const f = listed().folders[A.sel];
  if (!f || !f.fileable || A.busy) return;
  pickFound(A.sel);
  doArchive();
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

let preferSwitch = null;

function renderPreferWindow(on) {
  if (!preferSwitch) {
    preferSwitch = switchEl(on, { label: 'Prefer the window over the web app', onToggle: savePreferWindow });
    $('preferWindowSlot').appendChild(preferSwitch);
  }
  setSwitch(preferSwitch, on);
  $('preferWindowNote').textContent = on
    ? 'On: the Archive button in Outlook opens the small window, even when the app is open in a browser, and starts faster.'
    : 'Off: the Archive button hands the mail to the app when it is open in a browser on this PC, and opens the window when it is not.';
}

async function savePreferWindow(next) {
  try {
    const body = await postJSON('/api/config', { macro: { prefer_window: next } });
    renderPreferWindow(body.config.macro.prefer_window);
  } catch (e) {
    showToast('Could not save: ' + e.message, 'error');
    return;
  }
  showToast(next ? 'Outlook Archive opens the window' : 'Outlook Archive uses the app when it is open');
}

// The variable can change while the page stays open, so the sheet asks again each time it opens.
async function openMacro() {
  try {
    const doc = await getJSON('/api/archive/macro');
    $('macroText').textContent = doc.text;
    renderSetup(doc.setup);
    renderPreferWindow(doc.prefer_window);
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
  // The box and the list are drawn from A.query and the folders found, so only the focus needs keeping.
  const focused = document.activeElement === $('archSearch');
  A.doc = doc;
  renderArchive();
  if (focused) $('archSearch').focus();
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
    else if (el.dataset.found) pickFound(Number(el.dataset.found));
    else if (el.dataset.step) stepIn(Number(el.dataset.step));
    else if (el.id === 'archBack') stepBack();
    else if (el.id === 'archHere') archiveHere();
    else if (el.id === 'archFoundOpen') openFolder(listed().folders[A.sel]);
    else if (el.id === 'archExplorer') useExplorerFolder();
    else if (el.id === 'archRetry') { $('archiveDialog').close(); openArchive(); }
    else if (el.id === 'archMacro') openMacro();
  });
  body.addEventListener('input', (ev) => {
    if (ev.target.id !== 'archSearch') return;
    A.query = ev.target.value;
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
