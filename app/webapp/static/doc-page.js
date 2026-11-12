/* The document window (/doc?root=…&scan=…&id=…): one Markdown file in its own browser or app window.
 *
 * The address names the file by root, scan id and item id, so it can be
 * bookmarked and still opens after a restart; a newer scan that renumbered it
 * says so and offers Explore. The outline is a sidebar on a wide window and a
 * popover below it. The fleet nav leads back into the app: a tab opens the
 * app on that tab.
 */

import { authHeaders } from './api.js';
import { APP } from './state.js';
import { docUrl, drawDoc, fetchDoc, locFromUrl, outlineHtml, scrollToHeading, stateHtml, wireDocHost } from './doc-view.js';
import { wireThemeToggle } from './theme.js';
import { renderHeadStatus } from './_vendored/home-head/home-head.js';
import { initNavTabs } from './_vendored/nav/nav-tabs.js';
import { initUsageLog } from './_vendored/usage-log/usage-log.js';

const $ = (id) => document.getElementById(id);
// Wide windows show the outline beside the text; narrower ones open it from the header.
const WIDE = window.matchMedia('(min-width: 1100px) and (pointer: fine)');

const loc = locFromUrl(location.href);

/** Open the app itself on ``tab``: the app reads the tab it last showed. */
function toApp(tab) {
  try { localStorage.setItem(APP + '.tab', tab); } catch (e) { /* the app opens on its default tab */ }
  location.href = '/';
}

function placeOutline(hasOutline) {
  const side = $('docOutline');
  $('docOutlineBtn').hidden = !hasOutline || WIDE.matches;
  side.hidden = !hasOutline || !WIDE.matches;
  side.classList.toggle('is-popover', !WIDE.matches);
  $('docOutlineBtn').setAttribute('aria-expanded', 'false');
}

async function load(download) {
  if (!loc) {
    $('docBody').innerHTML = stateHtml({ reason: 'not_found', message: 'This address names no document' });
    return;
  }
  $('docBody').innerHTML = '<p class="doc-loading" role="status">' + (download ? 'Downloading to this PC…' : 'Loading…') + '</p>';
  const r = await fetchDoc(loc, download);
  if (!r.ok) {
    renderHeadStatus($('docStatus'), [], '');
    $('docBody').innerHTML = stateHtml(r);
    placeOutline(false);
    return;
  }
  document.title = r.doc.name + ' · Folder explorer';
  $('docTitle').textContent = r.doc.name;
  renderHeadStatus($('docStatus'), [], r.doc.folder.join(' › '));
  $('docOutline').innerHTML = '<p class="doc-side-title">Outline</p>' + outlineHtml(r.doc.outline);
  placeOutline(r.doc.outline.length > 0);
  drawDoc($('docBody'), r.doc);
  if (loc.fragment) scrollToHeading($('docBody'), loc.fragment);
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
  wireDocHost(document.body, $('docBody'), {
    onDocLink: (next) => { location.href = docUrl(next) + (next.fragment ? '#' + encodeURIComponent(next.fragment) : ''); },
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
  WIDE.addEventListener('change', () => placeOutline(!!$('docOutline').querySelector('li')));
  load(false);
}

boot();
