/* The Search tab's preview: one mail's indexed fields and "Show all", one folder's totals or one document's facts, plus their actions.
 *
 * On a wide desktop it fills the right-hand pane; on a phone it opens as a sheet.
 */

import { getJSON, postJSON, api } from './api.js';
import { BodyState, bodyOf, bodyStep, canLoad } from './body-state.js';
import { S, $, WIDE, copyText, effectiveMode, hl, mailDate, marked, shortFolder } from './search-state.js';
import { docUrl } from './doc-view.js';
import { openDoc } from './doc-sheet.js';
import { esc, fmtBytes, fmtDate, fmtNum, glyph, plural, winPath } from './fmt.js';
import { showToast } from './_vendored/toast/toast.js';
import { switchEl } from './_vendored/switch/switch.js';

let showInExplore = () => {};

export function initPreview(opts) {
  showInExplore = opts.showInExplore;
  // A frame that follows the app's theme follows it live; one the reader flipped keeps its look.
  new MutationObserver(() => {
    const item = current();
    if (item && effectiveMode() === 'emails' && !S.frame.get(item.id)?.look) refreshFrame(item.id);
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  const refocus = () => {
    const row = S.tab === 'search' && document.querySelector('#results .result-row[aria-selected="true"]');
    if (row) row.focus({ preventScroll: true });
  };
  $('previewDialog').addEventListener('close', refocus);
  $('docDialog').addEventListener('close', refocus);
}

const current = () => S.results[S.selected] || null;
// Find in mailbox opens the live copy in Outlook, so it is the PC's alone, like the Archive card.
const canFind = () => !!(S.status.pc && S.status.archive && S.status.archive.ready);
// Open folder hands the mail's folder to File Explorer through /api/open, the folder view's own route: the PC only, and only for a folder the scan holds.
const canOpenFolder = (d) => !!(S.status.pc && d.location);

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

// The frame's policy: nothing loads from anywhere, styles inline only, images only from data: URLs,
// plus https: ones while the reader has "Load remote images" on for that mail. Never scripts.
const frameCsp = (remote) => '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:' +
  (remote ? ' https:' : '') + '">';

// The dark reader look: the mail's own colours inverted in luminance with the hue turned back, and images
// inverted again so photos and logos keep their colours. The ground #ebeff5 comes out as the page's --bg (#0d1117) once the hue is turned back.
const DARK_LOOK = '<style>html{background:#ebeff5!important;filter:invert(1) hue-rotate(180deg)!important}' +
  'img{filter:invert(1) hue-rotate(180deg)!important}</style>';

const appLook = () => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
const frameView = (id) => S.frame.get(id) || { look: null, remote: false };

/** The mail's HTML as the frame gets it: the policy first, then the look, then the mail. */
function frameDoc(html, view) {
  const body = view.remote ? html.replace(/ data-remote-src="/g, ' src="') : html;
  return frameCsp(view.remote) + (view.look === 'dark' ? DARK_LOOK : '') + body;
}

/** The full-message block of an HTML mail: label, look button, the remote-images switch and the frame. */
function frameBlock(d, html) {
  const view = { ...frameView(d.id), look: frameView(d.id).look || appLook() };
  const dark = view.look === 'dark';
  const sw = switchEl(view.remote, { label: 'Load remote images' });
  sw.dataset.act = 'images';
  sw.dataset.track = 'frame-images';
  return '<div class="pv-section" data-frame="' + d.id + '">' +
    '<div class="pv-frame-head"><span class="pv-label">Full message · HTML, remote images ' + (view.remote ? 'shown' : 'blocked') + '</span>' +
    '<button type="button" class="icon-button" data-act="look" data-track="frame-look" aria-label="' + (dark ? 'Show this message in the light look' : 'Show this message in the dark look') +
    '" title="' + (dark ? 'Light look for this message' : 'Dark look for this message') + '">' + glyph(dark ? 'sun' : 'moon') + '</button></div>' +
    '<div class="pv-frame-opt"><span aria-hidden="true">Load remote images</span>' + sw.outerHTML + '</div>' +
    '<iframe class="pv-frame ' + view.look + '" sandbox="" referrerpolicy="no-referrer" title="Message body" srcdoc="' + esc(frameDoc(html, view)) + '"></iframe></div>';
}

function bodyHtml(d) {
  const st = bodyOf(S.body, d.id);
  const online = !!(d.location && d.location.file && d.location.file.online);
  if (!d.readable) return '<p class="pv-note">Show all isn\'t available: ' + esc(d.reader_problem || 'not set up here') + '.</p>';
  if (st.state === BodyState.IDLE) {
    return '<button type="button" class="button-tint" data-act="showall" data-track="show-all">' +
      (online ? glyph('download') + 'Show all · downloads from OneDrive' : 'Show all') + '</button>';
  }
  if (st.state === BodyState.LOADING) {
    return '<button type="button" class="button-tint is-busy" disabled data-track="show-all-busy">' + (online ? 'Downloading from OneDrive…' : 'Reading the mail…') + '</button>';
  }
  if (st.state === BodyState.ERROR) {
    return '<div class="pv-error" role="alert">' + glyph('triangle-alert') + '<div><strong>' + esc(BODY_TITLES[st.error] || 'Couldn\'t read the mail') +
      '</strong><p>' + esc(st.message) + '</p>' +
      (st.error === 'timeout' || st.error === 'read' ? '<button type="button" class="button-ghost" data-act="showall" data-track="show-all-retry">Try again</button>' : '') +
      '</div></div>';
  }
  if (st.data.html) return frameBlock(d, st.data.html);
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
    (canOpenFolder(d) ? '<button type="button" class="button-surface" data-act="openfolder" data-track="open-folder">' + glyph('folder-open') + 'Open folder</button>' : '') +
    '<button type="button" class="button-surface" data-act="copy" data-track="copy-path">' + glyph('copy') + 'Copy path</button>' +
    (canFind() ? '<button type="button" class="button-surface" data-act="mailbox" data-track="find-mailbox">' + glyph('search') + 'Find in mailbox</button>' : '') + '</div>' +
    (!S.status.pc ? '<p class="pv-note">Open in Outlook works on the PC.</p>' : (!d.can_open ? '<p class="pv-note">Outlook (classic) isn\'t installed on this PC.</p>' : '')) +
    '</div>' +
    '<p class="pv-path mono">' + esc(winPath(d.path)) + '</p></div>';
}

// ---------------------------------------------------------------- folder

function folderHtml(f) {
  const pc = S.status.pc;
  return '<div class="pv">' +
    '<h2 class="pv-subject">' + esc(f.name) + '</h2>' +
    '<p class="pv-path mono">' + esc(winPath(f.path)) + '</p>' +
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

// ------------------------------------------------------------- documents

const docLoc = (h) => ({ root: h.root, scan_id: h.scan_id, id: h.id, name: h.name });

function docHtml(h) {
  const pc = S.status.pc;
  return '<div class="pv">' +
    '<h2 class="pv-subject">' + esc(h.name) + '</h2>' +
    (h.online ? '<div class="pv-flags"><span class="chip">Online-only</span></div>' : '') +
    '<dl class="pv-fields"><dt>Folder</dt><dd><button type="button" class="reference-pill" data-act="folder" data-track="folder-pill">' + glyph('folder') +
    '<span>' + esc([h.root_name].concat(h.folder).join(' › ')) + '</span></button></dd>' +
    '<dt>Size</dt><dd>' + fmtBytes(h.size) + '</dd><dt>Modified</dt><dd>' + esc(fmtDate(h.mtime)) + '</dd></dl>' +
    '<div class="pv-actions">' +
    '<button type="button" class="button-primary" data-act="view" data-track="doc-view">' + glyph('file-text') + 'View</button>' +
    '<div class="pv-utils">' +
    '<button type="button" class="button-surface" data-act="edit" data-track="doc-edit">' + glyph('pencil') + 'Edit here</button>' +
    '<button type="button" class="button-surface" data-act="window" data-track="doc-window">' + glyph('external-link') + 'Open in new window</button>' +
    '<button type="button" class="button-surface" data-act="folder" data-track="show-in-explore">' + glyph('folder-open') + 'Show in Explore</button>' +
    '<button type="button" class="button-surface" data-act="copy" data-track="copy-path">' + glyph('copy') + 'Copy path</button>' +
    (pc ? '<button type="button" class="button-surface" data-act="default" data-track="open-default-app">' + glyph('external-link') + 'Open in default app</button>' : '') +
    '</div>' +
    (!pc ? '<p class="pv-note">Opening in a Windows app works on the PC.</p>' : '') +
    '</div></div>';
}

// ---------------------------------------------------------------- render

const EMPTY_GLYPH = { folders: 'folder', docs: 'file-text', emails: 'mail' };

function emptyHtml() {
  return '<div class="empty-state">' + glyph(EMPTY_GLYPH[effectiveMode()] || 'mail', 'empty-state-icon') +
    '<p class="empty-state-message">Pick a result to preview it.</p></div>';
}

async function previewHtml() {
  const item = current();
  if (!item) return emptyHtml();
  if (effectiveMode() === 'folders') return folderHtml(item);
  if (effectiveMode() === 'docs') return docHtml(item);
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
  if (current() && effectiveMode() === 'docs') { openDoc(docLoc(current())); return; }
  if (!current() || WIDE.matches) return;
  $('previewDialogTitle').textContent = effectiveMode() === 'folders' ? 'Folder' : 'Email';
  if (!$('previewDialog').open) $('previewDialog').showModal();
  await renderPreview();
}

// --------------------------------------------------------------- actions

async function showAll(d) {
  if (!d.readable || !canLoad(S.body, d.id)) return;
  bodyStep(S.body, d.id, BodyState.LOADING);
  renderPreview();
  const res = await api('/api/emails/' + d.id + '/body');
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (res.ok) bodyStep(S.body, d.id, BodyState.OK, { data });
  else bodyStep(S.body, d.id, BodyState.ERROR, { error: data && data.error, message: (data && data.detail) || 'HTTP ' + res.status });
  renderPreview();
}

/** Redraw one mail's full-message block where it shows, in place, and keep the keyboard on the control that was used. */
function refreshFrame(id, focusAct) {
  const st = S.body.get(id);
  if (!st || st.state !== BodyState.OK || !st.data.html) return;
  for (const host of [$('previewDock'), $('previewSheetBody')]) {
    const old = host.querySelector('[data-frame="' + id + '"]');
    if (!old) continue;
    old.outerHTML = frameBlock({ id }, st.data.html);
    if (focusAct) host.querySelector('[data-frame="' + id + '"] [data-act="' + focusAct + '"]')?.focus({ preventScroll: true });
  }
}

/** Flip this message to the other look, or turn its remote images on or off; neither is remembered past the session. */
function setFrame(d, act) {
  const view = frameView(d.id);
  if (act === 'look') S.frame.set(d.id, { ...view, look: (view.look || appLook()) === 'dark' ? 'light' : 'dark' });
  else S.frame.set(d.id, { ...view, remote: !view.remote });
  refreshFrame(d.id, act);
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
    else if (act === 'explorer' && S.status.pc) post('/api/folders/open', { root: item.root, scan_id: item.scan_id, id: item.id }, 'Opened in File Explorer');
    else if (act === 'folder') showInExplore(item.root, item.id);
    return;
  }
  if (effectiveMode() === 'docs') {
    if (act === 'view' || act === 'edit') openDoc(docLoc(item), act === 'edit');
    else if (act === 'window') window.open(docUrl(docLoc(item)), '_blank', 'noopener');
    else if (act === 'copy') showToast((await copyText(winPath(item.path))) ? 'Path copied' : 'Copy isn\'t allowed here');
    else if (act === 'default' && S.status.pc) post('/api/open', { root: item.root, scan_id: item.scan_id, kind: 'file', id: item.id, verb: 'open' }, 'Opening');
    else if (act === 'folder') showInExplore(item.root, item.folder_id);
    return;
  }
  const d = await detail(item);
  if (act === 'showall') showAll(d);
  else if (act === 'look' || act === 'images') setFrame(d, act);
  else if (act === 'copy') showToast((await copyText(d.path)) ? 'Path copied' : 'Copy isn\'t allowed here');
  else if (act === 'outlook' && d.can_open) post('/api/emails/' + d.id + '/open', {}, 'Opening in Outlook');
  else if (act === 'mailbox' && canFind()) findInMailbox(d);
  else if (act === 'openfolder' && canOpenFolder(d)) {
    const { root, scan_id, folder } = d.location;
    post('/api/open', { root, scan_id, kind: 'folder', id: folder, verb: 'explore' }, 'Opened in File Explorer');
  } else if (act === 'folder') {
    if (d.location) showInExplore(d.location.root, d.location.folder);
    else showToast('This mail\'s folder isn\'t in a scanned root yet');
  }
}
