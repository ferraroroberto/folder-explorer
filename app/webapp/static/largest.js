/* Largest: the top 10 files under the selected folder and its top percentile. */

import { getJSON } from './api.js';
import { state, query, val } from './state.js';
import { bar, cloudMark, esc, fmtBytes, fmtNum, fmtPct, glyph, pct, plural } from './fmt.js';

const $ = (id) => document.getElementById(id);

export async function loadLargest() {
  const [top, p] = await Promise.all([
    getJSON('/api/top?' + query({ n: 10 })),
    getJSON('/api/percentile?' + query({ p: state.pct })),
  ]);
  renderTop(top);
  renderPercentile(p);
}

function renderTop(top) {
  const whole = val(top.folder);
  $('topMeta').textContent = 'in ' + top.folder.name;
  $('topList').innerHTML = top.files.map((f, i) => {
    const p = pct(val(f), whole);
    const where = f.path.length ? f.path.join(' › ') : top.folder.name;
    return '<li><div class="list-row" title="Size ' + fmtNum(f.size) + ' bytes · On disk ' + fmtNum(f.disk) + ' bytes"><span class="rank">' + (i + 1) + '</span>' +
      '<span class="lr-name"><span>' + esc(f.name) + '</span>' + cloudMark(f.online) + '</span>' +
      '<span class="lr-meta">' + esc(where + ' · ' + fmtPct(p)) + '</span>' +
      '<span class="lr-size">' + fmtBytes(val(f)) + '</span>' + bar(p) + '</div></li>';
  }).join('') || '<li class="rows-note">' + (state.type !== null ? 'No files match the type filter.' : 'No files in this folder.') + '</li>';
}

function renderPercentile(r) {
  if (!r.files) {
    $('pctBody').innerHTML = '<p class="rows-note">' + (state.type !== null ? 'No files match the type filter.' : 'No files in this folder.') + '</p>';
    return;
  }
  const where = r.where.map((w) => {
    const share = pct(w.bytes, r.bytes);
    return '<li><div class="list-row">' + glyph(w.name ? 'folder' : 'file-text') + '<span class="lr-name"><span>' + esc(w.name || 'In this folder') + '</span></span>' +
      '<span class="lr-meta">' + plural(w.files, 'file') + ' · ' + fmtPct(share) + ' of this group</span>' +
      '<span class="lr-size">' + fmtBytes(w.bytes) + '</span>' + bar(share) + '</div></li>';
  }).join('');
  $('pctBody').innerHTML = '<div class="pct-stat"><span class="pct-value">' + fmtPct(r.share) + '</span><span class="pct-text">of ' + fmtBytes(r.whole) +
    ' sits in the largest ' + r.p + '% of files: ' + fmtNum(r.count) + ' of ' + fmtNum(r.files) + ', each ' + fmtBytes(r.threshold) + ' or more.</span></div>' +
    '<span class="share-bar pct-bar" aria-hidden="true"><span style="width:' + Math.min(100, r.share).toFixed(2) + '%"></span></span>' +
    '<p class="sub-title">Where they are</p><ul class="rows">' + where + '</ul>';
}
