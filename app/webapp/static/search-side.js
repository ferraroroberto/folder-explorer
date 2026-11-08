/* The Search tab's side parts: query chips, saved and recent searches, filters or display options, and facets.
 *
 * Every chip, filter and facet writes its operator into the box, then the box
 * runs the search, so the two never disagree.
 */

import { LABEL, getOp, serialize, tokenize, withOp } from './query.js';
import { S, $, effectiveMode, recent, shortFolder } from './search-state.js';
import { esc, fmtNum, glyph } from './fmt.js';

const box = () => $('q');

// --------------------------------------------------------------- chips

export function renderChips() {
  const toks = effectiveMode() === 'emails' ? tokenize(box().value) : [];
  const ops = toks.filter((t) => t.key);
  $('queryChips').innerHTML = ops.map((t, i) =>
    '<button type="button" class="qchip' + (t.neg ? ' is-not' : '') + '" data-chip="' + i + '" aria-label="Remove ' +
    esc((t.neg ? 'not ' : '') + LABEL[t.key] + ' ' + t.val) + '" data-track="chip-remove">' +
    '<span class="q-key">' + esc(LABEL[t.key]) + '</span><span class="q-val">' + esc(t.val) + '</span>' + glyph('x') + '</button>').join('');
}

/** The box without its ``n``-th operator chip. */
export function withoutChip(n) {
  const toks = tokenize(box().value);
  const at = toks.map((t, i) => (t.key ? i : -1)).filter((i) => i >= 0)[n];
  if (at !== undefined) toks.splice(at, 1);
  return serialize(toks);
}

// ------------------------------------------------------- saved / recent

export function renderSaved() {
  const mode = effectiveMode();
  const mine = S.saved.filter((s) => s.mode === mode);
  $('savedRow').innerHTML = mine.map((s) =>
    '<span class="saved-item"><button type="button" class="saved-chip" data-saved="' + s.id + '" title="' + esc(s.query) +
    '" data-track="saved-search">' + glyph('bookmark') + esc(s.name) + '</button>' +
    '<button type="button" class="icon-button saved-remove" data-unsave="' + s.id + '" aria-label="Forget ' + esc(s.name) +
    '" data-track="saved-remove">' + glyph('x') + '</button></span>').join('') +
    (mode ? '<button type="button" class="button-ghost" id="saveSearch" data-track="save-search">Save search</button>' : '');
}

export function renderRecent() {
  const mode = effectiveMode();
  const list = mode ? recent(mode) : [];
  const show = !box().value.trim() && list.length > 0;
  $('recentList').hidden = !show;
  $('recentList').innerHTML = show ? '<li><p class="recent-head">Recent</p></li>' + list.map((r) =>
    '<li><button type="button" class="recent-item" data-recent="' + esc(r) + '" data-track="recent-search">' + glyph('clock') +
    '<span>' + esc(r) + '</span></button></li>').join('') : '';
}

// ------------------------------------------------------------- filters

const field = (id, label, ph, track) => '<label class="ff"><span class="ff-label">' + label + '</span><input class="input-native" id="' + id +
  '" type="text" placeholder="' + esc(ph) + '" autocomplete="off" spellcheck="false" data-track="' + track + '"></label>';

const switchHtml = (id, label, track) => '<button type="button" class="toggle" role="switch" aria-checked="false" id="' + id +
  '" aria-label="' + esc(label) + '" data-track="' + track + '"><span class="knob"></span><span class="toggle-label">OFF</span></button>';

function setSwitchState(id, on) {
  const b = $(id);
  if (!b) return;
  b.classList.toggle('on', on);
  b.setAttribute('aria-checked', String(on));
  b.querySelector('.toggle-label').textContent = on ? 'ON' : 'OFF';
}

function emailFiltersHtml() {
  const dates = [['any', 'Any', 'date-any'], ['30d', '30 days', 'date-30d'], ['year', 'This year', 'date-year'], ['range', 'Range', 'date-range']];
  return '<div class="ff"><span class="ff-label">Date</span><span class="segmented" role="group" aria-label="Date" id="fDateSeg">' +
    dates.map(([v, l, t]) => '<button type="button" class="segmented-item" aria-pressed="false" data-value="' + v + '" data-track="' + t + '">' + l + '</button>').join('') +
    '</span><div class="ff-pair" id="fRange" hidden><label>After<input class="input-native" type="date" id="fAfter" data-track="date-after"></label>' +
    '<label>Before<input class="input-native" type="date" id="fBefore" data-track="date-before"></label></div></div>' +
    '<div class="ff-grid">' +
    field('fFrom', 'From', 'name or address', 'filter-from') +
    field('fTo', 'To', 'name or address', 'filter-to') +
    field('fCc', 'Cc', 'name or address', 'filter-cc') +
    field('fSubject', 'Subject or topic', 'words in the subject', 'filter-subject') +
    field('fFile', 'Attachment name', 'e.g. pdf, contract', 'filter-file') +
    field('fIn', 'In folder', 'part of the folder path', 'filter-folder') +
    '<label class="ff"><span class="ff-label">Size</span><select class="select-native" id="fLarger" data-track="filter-size">' +
    '<option value="">Any size</option><option value="1M">Larger than 1 MB</option><option value="5M">Larger than 5 MB</option>' +
    '<option value="10M">Larger than 10 MB</option></select></label></div>' +
    '<div class="ff-line"><span>Has an attachment</span>' + switchHtml('fHas', 'Has an attachment', 'filter-has') + '</div>' +
    '<div class="ff-line"><span>Flagged only</span>' + switchHtml('fFlag', 'Flagged only', 'filter-flagged') + '</div>';
}

function folderOptionsHtml() {
  return '<div class="ff-line"><span>Hide leading levels</span><span class="stepper">' +
    '<button type="button" class="icon-button" id="skipDown" aria-label="Show one more level" data-track="skip-down">' + glyph('minus') + '</button>' +
    '<span class="num" id="skipVal"></span>' +
    '<button type="button" class="icon-button" id="skipUp" aria-label="Hide one more level" data-track="skip-up">' + glyph('plus') + '</button></span></div>' +
    '<div class="ff-line"><span>One result per folder of people</span>' + switchHtml('pruneSwitch', 'One result per folder of people', 'prune') + '</div>' +
    '<p class="row-note">A folder whose subfolders are email addresses shows once, with a count.</p>' +
    '<div class="ff-line"><span>Show OneDrive folders once</span>' + switchHtml('onedriveSwitch', 'Show OneDrive folders once', 'onedrive-prevails') + '</div>' +
    '<p class="row-note">A folder in a synced OneDrive library shows as the OneDrive row, not also under its drive.</p>';
}

let builtFor = null;

const isoDay = (v) => (/^\d{4}-\d{2}-\d{2}$/.test(v) ? v : '');

/** The date segment the box's date operators amount to. */
function dateMode(toks) {
  const nt = getOp(toks, 'newer_than');
  const after = getOp(toks, 'after');
  const before = getOp(toks, 'before');
  if (nt === '30d' && !after && !before) return '30d';
  if (after === String(new Date().getFullYear()) && !before && !nt) return 'year';
  return after || before || nt ? 'range' : 'any';
}

export function renderFilters() {
  const mode = effectiveMode();
  if (builtFor !== mode) {
    builtFor = mode;
    $('filtersBody').innerHTML = mode === 'folders' ? folderOptionsHtml() : mode === 'emails' ? emailFiltersHtml() : '';
  }
  $('filtersTitle').textContent = mode === 'folders' ? 'Display' : 'Filters';
  if (mode === 'folders') {
    const fs = S.status.folder_search;
    $('skipVal').textContent = String(fs.skip_levels);
    setSwitchState('pruneSwitch', fs.prune_people);
    setSwitchState('onedriveSwitch', fs.onedrive_prevails);
    $('filtersMeta').textContent = fs.skip_levels ? 'Hiding ' + fs.skip_levels + (fs.skip_levels === 1 ? ' level' : ' levels') : 'Full paths';
    return;
  }
  if (mode !== 'emails') return;
  const toks = tokenize(box().value);
  const set = (id, v) => { const el = $(id); if (el && document.activeElement !== el) el.value = v; };
  [['fFrom', 'from'], ['fTo', 'to'], ['fCc', 'cc'], ['fSubject', 'subject'], ['fFile', 'filename'], ['fIn', 'in'], ['fLarger', 'larger']]
    .forEach(([id, key]) => set(id, getOp(toks, key)));
  set('fAfter', isoDay(getOp(toks, 'after')));
  set('fBefore', isoDay(getOp(toks, 'before')));
  setSwitchState('fHas', getOp(toks, 'has') === 'attachment');
  setSwitchState('fFlag', getOp(toks, 'is') === 'flagged');
  const dm = dateMode(toks);
  $('fDateSeg').querySelectorAll('.segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === dm)));
  $('fRange').hidden = dm !== 'range';
  const n = toks.filter((t) => t.key).length;
  $('filtersMeta').textContent = n ? n + ' on' : 'None';
}

/** The box after the date segment ``value`` was picked. */
export function withDate(value) {
  const toks = tokenize(box().value).filter((t) => !['after', 'before', 'newer_than'].includes(t.key));
  if (value === '30d') toks.push({ neg: false, key: 'newer_than', val: '30d' });
  if (value === 'year') toks.push({ neg: false, key: 'after', val: String(new Date().getFullYear()) });
  if (value === 'range') toks.push({ neg: false, key: 'after', val: (new Date().getFullYear() - 1) + '-01-01' });
  return serialize(toks);
}

/** Filter inputs and the operator each one writes. */
export const FIELD_OPS = { fFrom: 'from', fTo: 'to', fCc: 'cc', fSubject: 'subject', fFile: 'filename', fIn: 'in', fLarger: 'larger', fAfter: 'after', fBefore: 'before' };

export const toggled = (key, on, val) => withOp(box().value, key, on ? '' : val);

// --------------------------------------------------------------- facets

function facetChip(op, val, label, count, pressed, track) {
  return '<button type="button" class="facet" data-op="' + op + '" data-val="' + esc(val) + '" aria-pressed="' + pressed +
    '" data-track="' + track + '">' + esc(label) + ' <span class="count">' + fmtNum(count) + '</span></button>';
}

export function renderFacets() {
  const f = S.facets;
  const card = $('facetsCard');
  card.hidden = effectiveMode() !== 'emails';
  if (card.hidden) return;
  if (!f) { $('facetsBody').innerHTML = '<p class="row-note">Nothing to refine yet.</p>'; $('facetsMeta').textContent = ''; return; }
  const toks = tokenize(box().value);
  const group = (title, items) => (items.length ? '<div class="facet-group"><span class="facet-title">' + title + '</span><div class="facet-chips">' + items.join('') + '</div></div>' : '');
  const from = getOp(toks, 'from');
  const inF = getOp(toks, 'in');
  const senders = f.senders.map((s) => facetChip('from', s.value, s.name || s.value, s.count, from === s.value, 'facet-sender'));
  const folders = f.folders.map((x) => facetChip('in', x.value, shortFolder(x.value), x.count, inF === x.value, 'facet-folder'));
  const years = f.years.map((y) => facetChip('year', y.value, y.value, y.count,
    getOp(toks, 'after') === y.value + '-01-01' && getOp(toks, 'before') === y.value + '-12-31', 'facet-year'));
  const more = [];
  if (f.attachments) more.push(facetChip('has', 'attachment', 'Attachments', f.attachments, getOp(toks, 'has') === 'attachment', 'facet-attachments'));
  if (f.flagged) more.push(facetChip('is', 'flagged', 'Flagged', f.flagged, getOp(toks, 'is') === 'flagged', 'facet-flagged'));
  $('facetsBody').innerHTML = (group('Sender', senders) + group('Folder', folders) + group('Year', years) + group('Has', more)) ||
    '<p class="row-note">Nothing to refine yet.</p>';
  $('facetsMeta').textContent = f.senders.length ? f.senders.length + (f.senders.length === 1 ? ' sender' : ' senders') : '';
}

/** The box after a facet chip was tapped: on adds its operator, off takes it away. */
export function withFacet(op, val, on) {
  if (op !== 'year') return withOp(box().value, op, on ? '' : val);
  const toks = tokenize(box().value).filter((t) => !['after', 'before', 'newer_than'].includes(t.key));
  if (!on) toks.push({ neg: false, key: 'after', val: val + '-01-01' }, { neg: false, key: 'before', val: val + '-12-31' });
  return serialize(toks);
}

/** Forget which mode the filters were built for: the next render rebuilds them. */
export function resetFilters() {
  builtFor = null;
}
