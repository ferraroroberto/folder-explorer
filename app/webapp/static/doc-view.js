/* The document viewer's body (Markdown, or an HTML page in a sandboxed frame), shared by Explore's document sheet and the /doc window.
 *
 * A document is named by root, scan id and item id, never a path. The server
 * renders it; this module draws the HTML, fetches each image by its own id
 * (the request carries the sign-in token an <img src> could not), copies code
 * listings, keeps in-page anchors inside the document, and says why a document
 * can't show with the one action that helps.
 */

import { api } from './api.js';
import { NO_ANSWER, refusal } from './doc-answer.js';
import { esc, glyph } from './fmt.js';
import { copyText } from './search-state.js';

// How long a listing's copy control shows its check before going back to the copy glyph.
const COPIED_MS = 1500;

// What each refusal means for the reader, and the one action offered with it.
const STATES = {
  cloud_only: { text: 'This file is only in OneDrive. Downloading it brings this one file to the PC.', act: 'download', label: 'Download and open' },
  too_large: { text: 'Documents up to 2 MB open here. Use Open in default app on the PC for this one.' },
  scan_changed: { text: 'A newer scan renumbered the files, so this address no longer names it. Find it again in Explore.', act: 'explore', label: 'Open Explore' },
  not_found: { text: 'This scan has no such file. Find it again in Explore.', act: 'explore', label: 'Open Explore' },
  gone: { text: 'It was moved or deleted since the last scan.' },
  not_document: { text: 'Only Markdown and HTML files and the images beside them open here.' },
  unreadable: { text: 'Windows could not read it. Try again in a moment.' },
  app_mismatch: { text: 'This usually means the app was just updated or is restarting. Reload the page in a moment.', act: 'reload', label: 'Reload' },
  app_error: { text: 'Something went wrong in the app. Try again in a moment.' },
  no_answer: { text: 'Check the connection to the PC, then try again.' },
  dropped_gone: { text: 'A dropped file is kept only by the page it was dropped on. Drop it again on the app to open it.' },
  not_utf8: { text: 'This file is not saved as UTF-8 text, so it opens read-only here.' },
  edit_too_large: { text: 'Documents up to 1 MB can be edited here.' },
};

// Shown above a dropped file the scan doesn't have: it renders from the text the page read, so it can't be saved back.
const DROPPED_NOTE = '<p class="doc-drop-note" role="note">' + glyph('info') +
  '<span>Dropped file, shown read-only. Editing, relative links and the files beside it need it in a scanned folder.</span></p>';

const blobs = new WeakMap();
// Each container's loaded images by id, so the editor's preview reuses them on every render instead of fetching again.
const loaded = new WeakMap();

const params = (loc) => new URLSearchParams({ root: loc.root, scan: String(loc.scan_id), id: String(loc.id) });

/** The /doc address of a document, for a new window or a bookmark. */
export const docUrl = (loc) => '/doc?' + params(loc);

/** The document's place from a /doc address: ``{root, scan_id, id}``, or null when it names none. */
export function locFromUrl(href) {
  const u = new URL(href, location.origin);
  const scan = Number(u.searchParams.get('scan'));
  const id = Number(u.searchParams.get('id'));
  const root = u.searchParams.get('root');
  if (!root || !Number.isInteger(scan) || !Number.isInteger(id)) return null;
  return { root, scan_id: scan, id, fragment: decodeURIComponent(u.hash.slice(1)) };
}

async function answer(res) {
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (res.ok) return { ok: true, doc: body };
  return { ok: false, ...refusal(res.status, body) };
}

/** Fetch a document rendered; ``download`` is the one tap that lets a cloud-only file come down to the PC. */
export async function fetchDoc(loc, download) {
  const post = (path) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root: loc.root, scan_id: loc.scan_id, id: loc.id }) });
  try {
    return await answer(await (download ? post('/api/doc/download') : api('/api/doc?' + params(loc))));
  } catch (e) {
    return { ok: false, ...NO_ANSWER };
  }
}

/** Why a document can't show, with its one action. */
export function stateHtml(r) {
  const s = STATES[r.reason] || { text: '' };
  return '<div class="doc-state" role="status"><p class="doc-state-title">' + glyph(r.reason === 'cloud_only' ? 'cloud' : 'info') +
    '<span>' + esc(r.message) + '</span></p>' + (s.text ? '<p class="doc-state-text">' + esc(s.text) + '</p>' : '') +
    (s.act ? '<button type="button" class="button-tint" data-doc-act="' + s.act + '" data-track="doc-' + s.act + '">' + esc(s.label) + '</button>' : '') +
    '</div>';
}

/** The heading outline, as in-page links. */
export function outlineHtml(outline) {
  if (!outline.length) return '<p class="doc-outline-empty">No headings</p>';
  const top = Math.min(...outline.map((h) => h.level));
  return '<ul class="doc-outline-list">' + outline.map((h) => '<li style="--level:' + (h.level - top) + '"><a href="#' + esc(h.id) +
    '" data-track="doc-outline-item">' + esc(h.text || 'Untitled') + '</a></li>').join('') + '</ul>';
}

function imageOff(img, text, act) {
  const el = document.createElement('span');
  el.className = 'doc-img-off';
  el.innerHTML = glyph(act ? 'cloud' : 'info') + '<span>' + esc(img.alt || 'Image') + ' · ' + esc(text) + '</span>' +
    (act ? '<button type="button" class="button-ghost" data-doc-act="image-download" data-id="' + esc(img.dataset.docImg) +
      '" data-track="doc-image-download">Download</button>' : '');
  el.dataset.alt = img.alt;
  img.replaceWith(el);
}

async function showImage(container, img, loc, download) {
  const id = Number(img.dataset.docImg);
  const known = !download && loaded.get(container) && loaded.get(container).get(id);
  if (known) {
    img.src = known;
    return;
  }
  const res = download
    ? await api('/api/doc/image/download', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: loc.root, scan_id: loc.scan_id, id }) })
    : await api('/api/doc/image?' + params({ ...loc, id }));
  if (!res.ok) {
    const r = await answer(res);
    imageOff(img, r.message, r.reason === 'cloud_only');
    return;
  }
  const url = URL.createObjectURL(await res.blob());
  if (!blobs.has(container)) releaseDoc(container);
  blobs.get(container).push(url);
  loaded.get(container).set(id, url);
  img.src = url;
}

function loadImages(container, loc) {
  container.querySelectorAll('img[data-doc-img]').forEach((img) => {
    if (img.dataset.online) imageOff(img, 'Only in OneDrive', true);
    else showImage(container, img, loc, false).catch(() => imageOff(img, 'Could not load', false));
  });
}

/** Free the images of the document ``container`` showed last. */
export function releaseDoc(container) {
  (blobs.get(container) || []).forEach((u) => URL.revokeObjectURL(u));
  blobs.set(container, []);
  loaded.set(container, new Map());
}

// An HTML page runs its own scripts in an opaque origin: with no allow-same-origin it can't reach this page, its storage or
// cookies, and the server refuses its API calls (Origin: null). Popups let its links open a normal tab; there are no forms or modals.
export const FRAME_SANDBOX = 'allow-scripts allow-popups allow-popups-to-escape-sandbox';

function drawFrame(container, doc) {
  container.innerHTML = doc.dropped ? DROPPED_NOTE : '';
  const frame = document.createElement('iframe');
  frame.className = 'doc-frame';
  frame.setAttribute('sandbox', FRAME_SANDBOX);
  frame.setAttribute('referrerpolicy', 'no-referrer');
  frame.title = doc.name || 'HTML page';
  frame.srcdoc = doc.html;
  container.appendChild(frame);
}

/** Draw a rendered document into ``container``; ``keep`` holds on to the images it already loaded (the editor's preview). */
export function drawDoc(container, doc, keep) {
  if (!keep || !blobs.has(container)) releaseDoc(container);
  if (doc.kind === 'html') {
    drawFrame(container, doc);
    return;
  }
  container.innerHTML = (doc.dropped ? DROPPED_NOTE : '') + '<article class="doc-prose">' + doc.html + '</article>';
  loadImages(container, { root: doc.root, scan_id: doc.scan_id });
}

/** Scroll to the heading a fragment names, inside ``container``; false when there is none. */
export function scrollToHeading(container, id) {
  if (!id) return false;
  const el = container.querySelector('[id="' + CSS.escape(id) + '"]') || container.querySelector('[id="' + CSS.escape('doc-' + id) + '"]');
  if (el) el.scrollIntoView({ block: 'start' });
  return !!el;
}

async function copyListing(btn) {
  const listing = btn.closest('.code-listing');
  const ok = await copyText(listing.querySelector('pre').innerText);
  const status = listing.querySelector('.code-listing-status');
  btn.dataset.copied = String(ok);
  status.textContent = ok ? 'Copied' : 'Copy failed';
  if (!ok) status.dataset.state = 'error';
  setTimeout(() => { btn.dataset.copied = 'false'; status.textContent = ''; delete status.dataset.state; }, COPIED_MS);
}

/** Handle taps inside a document host: ``hooks`` are onDocLink(loc), onDownload(), onExplore(), and getLoc() for images. */
export function wireDocHost(host, scroller, hooks) {
  releaseDoc(scroller);
  host.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (a && a.dataset.docId && hooks.onDocLink && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      const loc = locFromUrl(a.getAttribute('href'));
      if (loc) { e.preventDefault(); hooks.onDocLink(loc); }
      return;
    }
    if (a && a.getAttribute('href').startsWith('#')) {
      e.preventDefault();
      scrollToHeading(scroller, decodeURIComponent(a.getAttribute('href').slice(1)));
      if (hooks.onAnchor) hooks.onAnchor();
      return;
    }
    const copy = e.target.closest('.code-listing-copy');
    if (copy) { copyListing(copy); return; }
    const act = e.target.closest('[data-doc-act]');
    if (!act) return;
    if (act.dataset.docAct === 'download') hooks.onDownload();
    else if (act.dataset.docAct === 'explore') hooks.onExplore();
    else if (act.dataset.docAct === 'reload') location.reload();
    else if (act.dataset.docAct === 'image-download') {
      const off = act.closest('.doc-img-off');
      const img = document.createElement('img');
      img.dataset.docImg = act.dataset.id;
      img.alt = off.dataset.alt || '';
      act.disabled = true;
      act.textContent = 'Downloading';
      const again = () => { act.disabled = false; act.textContent = 'Download'; };
      showImage(scroller, img, hooks.getLoc(), true).then(() => (img.src ? off.replaceWith(img) : again())).catch(again);
    }
  });
}
