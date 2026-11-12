/* Explore's document sheet: a Markdown file read in a full-height sheet, with its outline, links and a way out to its own window.
 *
 * A relative link to another scanned Markdown file opens in the same sheet,
 * and Back returns to the one before. The sheet never sends a path: every
 * document is named by root, scan id and item id.
 */

import { docUrl, drawDoc, fetchDoc, outlineHtml, releaseDoc, scrollToHeading, stateHtml, wireDocHost } from './doc-view.js';

const $ = (id) => document.getElementById(id);
const back = [];
let current = null;
let wired = false;
// Each load's number: a slower answer to an earlier load never draws over a later one.
let seq = 0;

function toggleOutline(open) {
  const on = open === undefined ? $('docOutline').hidden : open;
  $('docOutline').hidden = !on;
  $('docOutlineBtn').setAttribute('aria-expanded', String(on));
}

function wire() {
  if (wired) return;
  wired = true;
  wireDocHost($('docDialog'), $('docSheetBody'), {
    onDocLink: (loc) => { back.push(current); show(loc); },
    onDownload: () => show(current, true),
    onExplore: () => $('docDialog').close(),
    onAnchor: () => toggleOutline(false),
    getLoc: () => current,
  });
  $('docBack').addEventListener('click', () => { if (back.length) show(back.pop()); });
  $('docOutlineBtn').addEventListener('click', () => toggleOutline());
  $('docDialog').addEventListener('close', () => {
    seq += 1;
    releaseDoc($('docSheetBody'));
    back.length = 0;
    current = null;
    toggleOutline(false);
  });
}

async function show(loc, download) {
  const mine = ++seq;
  current = { root: loc.root, scan_id: loc.scan_id, id: loc.id, name: loc.name };
  $('docBack').hidden = !back.length;
  $('docWindow').href = docUrl(current);
  $('docDialogTitle').textContent = loc.name || 'Document';
  $('docWhere').textContent = '';
  $('docOutlineBtn').hidden = true;
  toggleOutline(false);
  $('docSheetBody').innerHTML = '<p class="doc-loading" role="status">' + (download ? 'Downloading to this PC…' : 'Loading…') + '</p>';
  if (!$('docDialog').open) $('docDialog').showModal();
  const r = await fetchDoc(current, download);
  if (mine !== seq) return;
  if (!r.ok) {
    $('docSheetBody').innerHTML = stateHtml(r);
    return;
  }
  current.name = r.doc.name;
  $('docDialogTitle').textContent = r.doc.name;
  $('docWhere').textContent = r.doc.folder.join(' › ');
  $('docOutline').innerHTML = outlineHtml(r.doc.outline);
  $('docOutlineBtn').hidden = !r.doc.outline.length;
  drawDoc($('docSheetBody'), r.doc);
  $('docSheetBody').scrollTop = 0;
  if (loc.fragment) scrollToHeading($('docSheetBody'), loc.fragment);
}

/** Open the sheet on a document: ``loc`` is ``{root, scan_id, id}`` and, for the title while it loads, ``name``. */
export function openDoc(loc) {
  wire();
  back.length = 0;
  show(loc);
}
