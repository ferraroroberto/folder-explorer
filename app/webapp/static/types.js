/* Types: bytes, file count and share per extension under the selected folder; a row sets the filter. */

import { getJSON } from './api.js';
import { state, query, val } from './state.js';
import { bar, esc, extLabel, fmtBytes, fmtNum, fmtPct, glyph, plural } from './fmt.js';

const $ = (id) => document.getElementById(id);

export async function loadTypes() {
  // The breakdown always covers every type; the filter only marks a row.
  const body = await getJSON('/api/types?' + query({}, true));
  $('typesMeta').textContent = fmtNum(body.count) + ' types in ' + body.folder.name;
  $('typesBar').innerHTML = body.types.slice(0, 8).map((t) => '<span style="width:' + t.share.toFixed(2) + '%"></span>').join('');
  $('typesList').innerHTML = body.types.map((t) => {
    const chip = t.ext ? '<span class="code-chip">' + esc(extLabel(t.ext)) + '</span>' : '<span class="lr-plain">' + esc(extLabel('')) + '</span>';
    return '<li><button type="button" class="list-row" data-act="type" data-ext="' + esc(t.ext) + '" aria-pressed="' + (state.type === t.ext) + '" data-track="types-row">' +
      '<span class="rank">' + glyph('file-text') + '</span><span class="lr-name">' + chip + '</span>' +
      '<span class="lr-meta">' + plural(t.files, 'file') + ' · ' + fmtPct(t.share) + '</span>' +
      '<span class="lr-size">' + fmtBytes(val(t)) + '</span>' + bar(t.share) + '</button></li>';
  }).join('') + (body.count > body.types.length ? '<li class="rows-note">And ' + fmtNum(body.count - body.types.length) + ' smaller types.</li>' : '');
  $('typesFilterLine').hidden = state.type === null;
  $('typesFilterText').innerHTML = state.type !== null ? 'Showing only <span class="code-chip">' + esc(extLabel(state.type)) + '</span> in every tab' : '';
}

/** Options for the toolbar's type filter: the root's heaviest types. */
export async function typeOptions() {
  const body = await getJSON('/api/types?' + query({ folder: 0 }, true));
  return body.types.map((t) => t.ext);
}
