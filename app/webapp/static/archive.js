/* The Search tab's Archive card, the Archive sheet, the Outlook macro sheet, and the macro's hand-off.
 *
 * Every Outlook step runs on the server in a child of its own; the page only
 * holds a prepared mail's token. A page on this PC polls for a mail the
 * Outlook macro handed over while it is visible, and opens the sheet with it.
 */

import { api, getJSON, postJSON } from './api.js';
import { copyText, mailDate } from './search-state.js';
import { esc, glyph, plural } from './fmt.js';
import { showToast } from './_vendored/toast/toast.js';

const $ = (id) => document.getElementById(id);
// How often a visible page on this PC asks whether the macro handed over a mail.
const PENDING_MS = 3000;
// How long typing in "Another email folder" pauses before it searches.
const DEBOUNCE_MS = 200;

const A = {
  status: null, // /api/search/status .archive
  doc: null, // the prepared mail, as /api/archive/current or the hand-off gives it
  fromMacro: false,
  choice: 0,
  extra: null, // a folder picked from "Another email folder"
  category: '',
  busy: false,
  opening: false,
  error: null,
  lastFiled: '',
};

let pollTimer = null;
let searchTimer = null;

const lastTwo = (path) => (path || '').split(/[\\/]/).filter(Boolean).slice(-2).join('\\');

/** ``path`` below the email folder that holds it, as the sheet lists a folder. */
function underRoot(path) {
  const lower = path.toLowerCase();
  for (const r of (A.doc && A.doc.roots) || []) {
    const root = r.replace(/[\\/]+$/, '');
    const next = lower.charAt(root.length);
    if (lower.startsWith(root.toLowerCase()) && (next === '\\' || next === '/')) return path.slice(root.length + 1);
  }
  return path;
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

function optionHtml(o, i) {
  const on = i === A.choice;
  return '<li><button type="button" class="arch-opt" role="radio" aria-checked="' + on + '" data-choice="' + i + '" data-track="archive-choice">' +
    glyph(on ? 'circle-dot' : 'circle') + '<span class="ao-path">' + esc(underRoot(o.folder_path)) + '</span>' +
    '<span class="ao-why">' + (o.why || []).map((w) => '<span class="chip' + (w.tone ? ' tone-' + w.tone : '') + '">' + esc(w.label) + '</span>').join('') +
    esc(o.note || '') + '</span></button></li>';
}

function mailHtml(mail) {
  const when = mail.date_sent ? mailDate(mail.date_sent, true) : '';
  const bits = [mail.sender || '(no sender)', when, mail.attachment_count ? plural(mail.attachment_count, 'attachment') : '', mail.where].filter(Boolean);
  return '<div class="arch-mail"><strong>' + esc(mail.subject || '(no subject)') + '</strong><span>' + esc(bits.join(' · ')) + '</span></div>';
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
    (filed ? '<p class="arch-filed tone-attention-text">Already archived as ' + esc(lastTwo(filed)) + '</p>' : '') +
    '<div class="arch-head"><h3>Suggested folders</h3><button type="button" class="icon-button" id="rankInfo" aria-label="How folders are suggested" title="How folders are suggested" data-track="rank-info">' + glyph('info') + '</button></div>' +
    '<p class="row-note" id="rankNote" hidden>Each folder scores by its best match: the same subject first, then the sender and the topic. A big folder doesn\'t win by volume.</p>' +
    (opts.length ? '<ul class="arch-options" role="radiogroup" aria-label="Archive folder">' + opts.map(optionHtml).join('') + '</ul>'
      : '<p class="row-note">No folder matches this mail yet. Find one below.</p>') +
    '<div class="arch-other"><label class="search-field">' + glyph('search') +
    '<input type="search" id="archSearch" placeholder="Another email folder" aria-label="Find another email folder" autocomplete="off" spellcheck="false" data-track="archive-search"></label>' +
    '<ul class="arch-found" id="archFound"></ul></div>' +
    '<div class="row"><span>Outlook category</span><select class="select-native" id="archCat" aria-label="Outlook category" data-track="archive-category">' +
    (d.category ? '<option value="' + esc(d.category) + '"' + (A.category === d.category ? ' selected' : '') + '>' + esc(d.category) + '</option>' : '') +
    '<option value=""' + (A.category ? '' : ' selected') + '>No category</option></select></div>' +
    '<div class="row"><span>Saved as</span><span class="row-value mono">' + esc(choice && choice.saved_as ? choice.saved_as : 'Numbered after the folder\'s last mail') + '</span></div>' +
    '<div class="arch-foot">' + glyph('mail') + '<span>Outlook macro: archive straight from Outlook\'s ribbon.</span>' +
    '<button type="button" class="icon-button" id="archMacro" aria-label="About the Outlook macro" title="About the Outlook macro" data-track="macro-info">' + glyph('info') + '</button></div>' +
    (A.error ? errorBlock('Not archived', A.error.message, false) : '');
  $('archiveActions').innerHTML = '<button type="button" class="button-primary detail-save-btn" id="archiveGo" data-track="archive-go"' +
    (choice && !filed && !A.busy ? '' : ' disabled') + '>' +
    esc(A.busy ? 'Archiving…' : choice ? 'Archive to ' + lastTwo(choice.folder_path) : 'Pick a folder') + '</button>';
}

function showSheet(doc, fromMacro) {
  A.doc = doc;
  A.fromMacro = fromMacro;
  A.choice = 0;
  A.extra = null;
  A.error = null;
  A.busy = false;
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
    r = await postJSON('/api/archive/file', { token: A.doc.token, folder: o.folder_path, category: A.category });
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
  list.innerHTML = found.length ? found.map((f) => '<li><button type="button" data-folder="' + esc(f.path) + '" data-track="archive-found">' + esc(underRoot(f.path)) + '</button></li>').join('')
    : '<li><p class="row-note">No email folder matches every word.</p></li>';
}

function pickFound(path) {
  A.extra = { folder_path: path, why: [{ label: 'Chosen', tone: '' }], note: 'picked from the folder index', saved_as: '' };
  A.choice = 0;
  renderArchive();
}

// --------------------------------------------------------------- macro

async function openMacro() {
  const pre = $('macroText');
  if (!pre.textContent) {
    try {
      pre.textContent = (await getJSON('/api/archive/macro')).text;
    } catch (e) {
      showToast(e.message, 'error');
      return;
    }
  }
  if (!$('macroDialog').open) $('macroDialog').showModal();
}

// ------------------------------------------------------------- hand-off

async function pollPending() {
  pollTimer = null;
  if (document.visibilityState === 'visible') {
    let res = null;
    try { res = await api('/api/archive/pending'); } catch (e) { res = null; }
    // Not this PC: the macro only ever hands a mail to the PC's own pages.
    if (res && res.status === 403) return;
    if (res && res.ok) {
      const body = await res.json();
      if (body.mail) showSheet(body.mail, true);
    }
  }
  pollTimer = setTimeout(pollPending, PENDING_MS);
}

/** Start asking for the macro's hand-off; called once the page has signed in. */
export function startArchivePoll() {
  if (!pollTimer) pollPending();
}

// --------------------------------------------------------------- wiring

export function initArchive() {
  $('openArchive').addEventListener('click', () => openArchive());
  $('showMacro').addEventListener('click', () => { $('settingsDialog').close(); openMacro(); });
  $('copyMacro').addEventListener('click', async () => showToast((await copyText($('macroText').textContent)) ? 'Macro copied' : 'Copy isn\'t allowed here'));
  const body = $('archiveBody');
  $('archiveActions').addEventListener('click', (ev) => {
    const el = ev.target.closest('#archiveGo');
    if (el && !el.disabled) doArchive();
  });
  body.addEventListener('click', (ev) => {
    const el = ev.target.closest('button');
    if (!el || el.disabled) return;
    if (el.classList.contains('arch-opt')) { A.choice = Number(el.dataset.choice); renderArchive(); }
    else if (el.id === 'rankInfo') $('rankNote').hidden = !$('rankNote').hidden;
    else if (el.dataset.folder) pickFound(el.dataset.folder);
    else if (el.id === 'archRetry') { $('archiveDialog').close(); openArchive(); }
    else if (el.id === 'archMacro') openMacro();
  });
  body.addEventListener('input', (ev) => {
    if (ev.target.id !== 'archSearch') return;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => findFolders(ev.target.value), DEBOUNCE_MS);
  });
  body.addEventListener('change', (ev) => { if (ev.target.id === 'archCat') A.category = ev.target.value; });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && pollTimer) { clearTimeout(pollTimer); pollPending(); }
  });
}
