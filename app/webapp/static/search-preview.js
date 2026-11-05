/* The Search tab's preview: one mail's indexed fields and "Show all", or one folder's totals, plus their actions.
 *
 * On a wide desktop it fills the right-hand pane; on a phone it opens as a sheet.
 */

import { getJSON, postJSON, api } from './api.js';
import { S, $, WIDE, copyText, effectiveMode, hl, mailDate, marked, shortFolder } from './search-state.js';
import { esc, fmtBytes, fmtNum, glyph, plural } from './fmt.js';
import { showToast } from './_vendored/toast/toast.js';

let showInExplore = () => {};

export function initPreview(opts) {
  showInExplore = opts.showInExplore;
  $('previewDialog').addEventListener('close', () => {
    const row = document.querySelector('#results .result-row[aria-selected="true"]');
    if (row) row.focus({ preventScroll: true });
  });
}

const current = () => S.results[S.selected] || null;
// Find in mailbox opens the live copy in Outlook, so it is the PC's alone, like the Archive card.
const canFind = () => !!(S.status.pc && S.status.archive && S.status.archive.ready);

// ------------------------------------------------------------------ mail

/** One mail's preview data, fetched once per mail and query. */
async function detail(hit) {
  const key = hit.id + '|' + S.q.emails;
  if (!S.detail.has(key)) S.detail.set(key, await getJSON('/api/emails/' + hit.id + '?q=' + encodeURIComponent(S.q.emails)));
  return S.detail.get(key);
}

const BODY_TITLES = {
  timeout: 'Download timed out', gone: 'The file is no longer there', parse: 'Couldn\'t read this file as a mail',
  read: 'Couldn\'t read the file', not_set_up: 'Show all isn\'t set up here', outside: 'Outside the email folders',
};

// The frame's policy: nothing loads from anywhere, styles inline only, images only from data: URLs.
const FRAME_CSP = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">';

function bodyHtml(d) {
  const st = S.body.get(d.id) || { state: 'idle' };
  const online = !!(d.location && d.location.file && d.location.file.online);
  if (!d.readable) return '<p class="pv-note">Show all isn\'t available: ' + esc(d.reader_problem || 'not set up here') + '.</p>';
  if (st.state === 'idle') {
    return '<button type="button" class="button-tint" data-act="showall" data-track="show-all">' +
      (online ? glyph('download') + 'Show all · downloads from OneDrive' : 'Show all') + '</button>';
  }
  if (st.state === 'loading') {
    return '<button type="button" class="button-tint is-busy" disabled data-track="show-all-busy">' + (online ? 'Downloading from OneDrive…' : 'Reading the mail…') + '</button>';
  }
  if (st.state === 'error') {
    return '<div class="pv-error" role="alert">' + glyph('triangle-alert') + '<div><strong>' + esc(BODY_TITLES[st.error] || 'Couldn\'t read the mail') +
      '</strong><p>' + esc(st.message) + '</p>' +
      (st.error === 'timeout' || st.error === 'read' ? '<button type="button" class="button-ghost" data-act="showall" data-track="show-all-retry">Try again</button>' : '') +
      '</div></div>';
  }
  if (st.data.html) {
    return '<div class="pv-section"><span class="pv-label">Full message · HTML, remote images blocked</span>' +
      '<iframe class="pv-frame" sandbox="" referrerpolicy="no-referrer" title="Message body" srcdoc="' + esc(FRAME_CSP + st.data.html) + '"></iframe></div>';
  }
  return '<div class="pv-section"><span class="pv-label">Full message</span><pre class="pv-body">' + hl(st.data.text, d.words) + '</pre></div>';
}

function emailHtml(d) {
  const online = !!(d.location && d.location.file && d.location.file.online);
  const size = d.size || (d.location && d.location.file ? d.location.file.size : null);
  const atts = d.attachments;
  const flags = (d.flagged ? '<span class="chip tone-attention">Flagged</span>' : '') + (online ? '<span class="chip">Online-only</span>' : '');
  return '<div class="pv">' +
    '<h2 class="pv-subject">' + marked(d.subject || '(no subject)') + '</h2>' +
    (flags ? '<div class="pv-flags">' + flags + '</div>' : '') +
    '<dl class="pv-fields"><dt>From</dt><dd>' + esc(d.from_name) + (d.from_addr && d.from_addr !== d.from_name ? ' <span class="muted">' + esc(d.from_addr) + '</span>' : '') + '</dd>' +
    (d.to ? '<dt>To</dt><dd>' + esc(d.to) + '</dd>' : '') +
    (d.cc ? '<dt>Cc</dt><dd>' + esc(d.cc) + '</dd>' : '') +
    '<dt>Date</dt><dd>' + esc(mailDate(d.date, true)) + '</dd>' +
    '<dt>Folder</dt><dd><button type="button" class="reference-pill" data-act="folder" data-track="folder-pill">' + glyph('folder') + '<span>' + esc(shortFolder(d.folder)) + '</span></button></dd>' +
    (size ? '<dt>Size</dt><dd>' + fmtBytes(size) + '</dd>' : '') + '</dl>' +
    (atts && atts.length ? '<div class="pv-section"><span class="pv-label">' + plural(atts.length, 'attachment') + '</span><div class="pv-atts">' +
      atts.map((a) => '<span class="file-chip">' + glyph('paperclip') + hl(a, d.words) + '</span>').join('') + '</div></div>' : '') +
    '<div class="pv-section"><span class="pv-label">Indexed preview</span><p class="pv-snippet">' + marked(d.preview || '(no preview indexed)') + '</p></div>' +
    bodyHtml(d) +
    '<div class="pv-actions">' +
    (d.can_open ? '<button type="button" class="button-primary" data-act="outlook" data-track="open-outlook">' + glyph('external-link') + 'Open in Outlook</button>' : '') +
    '<div class="pv-utils">' +
    '<button type="button" class="button-surface" data-act="folder" data-track="show-folder">' + glyph('folder-open') + 'Show folder</button>' +
    '<button type="button" class="button-surface" data-act="copy" data-track="copy-path">' + glyph('copy') + 'Copy path</button>' +
    (canFind() ? '<button type="button" class="button-surface" data-act="mailbox" data-track="find-mailbox">' + glyph('search') + 'Find in mailbox</button>' : '') + '</div>' +
    (!S.status.pc ? '<p class="pv-note">Open in Outlook works on the PC.</p>' : (!d.can_open ? '<p class="pv-note">Outlook (classic) isn\'t installed on this PC.</p>' : '')) +
    '</div>' +
    '<p class="pv-path mono">' + esc(d.path) + '</p></div>';
}

// ---------------------------------------------------------------- folder

function folderHtml(f) {
  const pc = S.status.pc;
  return '<div class="pv">' +
    '<h2 class="pv-subject">' + esc(f.name) + '</h2>' +
    '<p class="pv-path mono">' + esc(f.path) + '</p>' +
    '<div class="pv-stats">' +
    '<div class="stat"><div class="stat-value">' + fmtBytes(f.size) + '</div><div class="stat-label">Size</div></div>' +
    '<div class="stat"><div class="stat-value">' + fmtBytes(f.disk) + '</div><div class="stat-label">On disk</div></div>' +
    '<div class="stat"><div class="stat-value">' + fmtNum(f.files) + '</div><div class="stat-label">Files</div></div>' +
    '<div class="stat"><div class="stat-value">' + (f.people ? fmtNum(f.people) : esc(f.root_name)) + '</div><div class="stat-label">' + (f.people ? 'People' : 'Root') + '</div></div></div>' +
    (f.people ? '<p class="pv-note">A folder with one subfolder per person, shown as one result.</p>' : '') +
    '<div class="pv-actions">' +
    (pc ? '<button type="button" class="button-primary" data-act="explorer" data-track="open-explorer">' + glyph('external-link') + 'Open in File Explorer</button>' : '') +
    '<div class="pv-utils">' +
    '<button type="button" class="button-surface" data-act="folder" data-track="show-in-explore">' + glyph('folder-open') + 'Show in Explore</button>' +
    '<button type="button" class="button-surface" data-act="copy" data-track="copy-path">' + glyph('copy') + 'Copy path</button></div>' +
    (!pc ? '<p class="pv-note">Opening the folder works on the PC.</p>' : '') +
    '</div></div>';
}

// ---------------------------------------------------------------- render

function emptyHtml() {
  return '<div class="empty-state">' + glyph(effectiveMode() === 'folders' ? 'folder' : 'mail', 'empty-state-icon') +
    '<p class="empty-state-message">Pick a result to preview it.</p></div>';
}

async function previewHtml() {
  const item = current();
  if (!item) return emptyHtml();
  if (effectiveMode() === 'folders') return folderHtml(item);
  return emailHtml(await detail(item));
}

let renderSeq = 0;

export async function renderPreview() {
  const seq = ++renderSeq;
  let html;
  try {
    html = await previewHtml();
  } catch (e) {
    html = '<div class="pv-error" role="alert">' + glyph('triangle-alert') + '<div><strong>Couldn\'t load this mail</strong><p>' + esc(e.message) + '</p></div></div>';
  }
  if (seq !== renderSeq) return;
  if (WIDE.matches) $('previewDock').innerHTML = html;
  if ($('previewDialog').open) $('previewSheetBody').innerHTML = html;
}

export async function openPreviewSheet() {
  if (!current() || WIDE.matches) return;
  $('previewDialogTitle').textContent = effectiveMode() === 'folders' ? 'Folder' : 'Email';
  if (!$('previewDialog').open) $('previewDialog').showModal();
  await renderPreview();
}

// --------------------------------------------------------------- actions

async function showAll(d) {
  const st = S.body.get(d.id);
  if (!d.readable || (st && st.state === 'loading')) return;
  S.body.set(d.id, { state: 'loading' });
  renderPreview();
  const res = await api('/api/emails/' + d.id + '/body');
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  S.body.set(d.id, res.ok ? { state: 'ok', data } : { state: 'error', error: data && data.error, message: (data && data.detail) || 'HTTP ' + res.status });
  renderPreview();
}

async function findInMailbox(d) {
  showToast('Looking for the live copy in Outlook…');
  try {
    const r = await postJSON('/api/emails/' + d.id + '/find', {});
    if (r.ok) showToast('Opened in Outlook' + (r.folder ? ' from ' + r.folder : ''));
    else showToast(r.error.message, 'error');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function post(path, body, okText) {
  try {
    await postJSON(path, body);
    showToast(okText);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

export async function previewAction(act) {
  const item = current();
  if (!item) return;
  if (effectiveMode() === 'folders') {
    if (act === 'copy') showToast((await copyText(item.path)) ? 'Path copied' : 'Copy isn\'t allowed here');
    else if (act === 'explorer' && S.status.pc) post('/api/folders/open', { root: item.root, id: item.id }, 'Opened in File Explorer');
    else if (act === 'folder') showInExplore(item.root, item.id);
    return;
  }
  const d = await detail(item);
  if (act === 'showall') showAll(d);
  else if (act === 'copy') showToast((await copyText(d.path)) ? 'Path copied' : 'Copy isn\'t allowed here');
  else if (act === 'outlook' && d.can_open) post('/api/emails/' + d.id + '/open', {}, 'Opening in Outlook');
  else if (act === 'mailbox' && canFind()) findInMailbox(d);
  else if (act === 'folder') {
    if (d.location) showInExplore(d.location.root, d.location.folder);
    else showToast('This mail\'s folder isn\'t in a scanned root yet');
  }
}
