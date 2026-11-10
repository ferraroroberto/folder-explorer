/* Scans: every root with its last result or live progress, and the Rescan actions. */

import { getJSON } from './api.js';
import { state } from './state.js';
import { bar, esc, fmtAgo, fmtBytes, fmtDuration, fmtNum, glyph, pct, plural } from './fmt.js';

const $ = (id) => document.getElementById(id);

export async function loadRoots() {
  const body = await getJSON('/api/roots');
  state.roots = body.roots;
  state.scanning = body.scanning;
  state.scanUnknown = body.state === 'unknown';
  return body;
}

function meta(r) {
  if (r.running) return { text: 'Scanning… ' + plural(r.running.files, 'file') + ' in ' + plural(r.running.folders, 'folder') + ' read', live: true };
  const parts = [];
  if (!r.exists) parts.push('Folder not found');
  if (r.state === 'unknown') parts.push('Cannot tell whether a scan is running');
  if (r.failed) parts.push(r.state === 'interrupted' ? 'Last scan stopped early' : 'Last scan failed');
  if (r.current) {
    const c = r.current;
    parts.push('Scanned ' + fmtAgo(c.finished_at) + ' · took ' + fmtDuration(c.finished_at - c.started_at) + ' · ' +
      plural(c.files, 'file') + ' · ' + fmtBytes(c.size));
  } else if (r.exists) {
    parts.push('Not scanned yet');
  }
  return { text: parts.join(' · '), warn: !r.exists || !!r.failed || r.state === 'unknown' };
}

export function renderRoots() {
  $('rootsList').innerHTML = state.roots.map((r) => {
    const m = meta(r);
    const known = r.current ? r.current.files : 0;
    const progress = r.running && known ? bar(pct(r.running.files, known)) : '';
    return '<li class="action-row root-row">' +
      '<button type="button" class="action-row-main" data-act="open-root" data-root="' + esc(r.key) + '" data-track="root-open"' + (r.current ? '' : ' disabled') + '>' +
      '<span class="action-row-title">' + glyph(r.kind === 'synced' ? 'cloud' : 'folder', 'root-glyph') + esc(r.name) + '<span class="chip">' + esc(r.kind) + '</span></span>' +
      '<span class="action-row-meta' + (m.live ? ' is-live' : '') + (m.warn ? ' is-warn' : '') + '">' + esc(m.text) + '</span>' +
      '</button>' +
      '<button type="button" class="icon-button action-row-verb" data-act="rescan" data-root="' + esc(r.key) + '" aria-label="Refresh the scan of ' + esc(r.name) +
      '" title="Refresh the scan"' + (state.scanning || state.scanUnknown || !r.exists ? ' disabled' : '') + ' data-track="rescan-root">' + glyph('refresh-cw') + '</button>' +
      progress + '</li>';
  }).join('');
  $('rescanAll').disabled = state.scanning || state.scanUnknown;
  const running = state.roots.filter((r) => r.running).map((r) => r.name);
  $('scansNote').textContent = running.length ? 'Scanning ' + running.join(', ') + '. You can keep browsing the last index.' : '';
}

/** The Scans tab's header line: what is happening, else when the last scan finished. */
export function scansStatus() {
  const live = state.roots.find((r) => r.running);
  if (live) return { exceptions: [{ text: 'Scanning ' + live.name, tone: 'neutral' }], fallback: '' };
  const failed = state.roots.filter((r) => r.failed);
  const last = Math.max(0, ...state.roots.map((r) => (r.current ? r.current.finished_at : 0)));
  return {
    exceptions: failed.length ? [{ text: failed.length + (failed.length === 1 ? ' scan failed' : ' scans failed'), tone: 'danger' }] : [],
    fallback: fmtNum(state.roots.length) + ' roots · last scan ' + fmtAgo(last),
  };
}
