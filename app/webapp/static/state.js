/* The page's shared state, the query string every browse call carries, and which feature blocks are on.
 *
 * Available features: a block that is off sets its key on <html data-features-off>, and app.css hides every [data-feature]
 * part of it, so a part is hidden by its markup and needs no code of its own. A block the server doesn't report (an older
 * server, a failed load) is on: every block is on by default.
 */

import { getJSON } from './api.js';

export const APP = 'folder-explorer';

export function readPref(key, fallback) {
  try { return localStorage.getItem(APP + '.' + key) || fallback; } catch (e) { return fallback; }
}

export function writePref(key, value) {
  try { localStorage.setItem(APP + '.' + key, value); } catch (e) { /* not kept across visits */ }
}

/** What deleting frees is the space on disk, not a file's length (a sparse or compressed file is far smaller than its length). */
export const DEFAULT_BASIS = 'disk';

export const state = {
  roots: [],
  root: null,
  basis: readPref('basis', DEFAULT_BASIS) === 'size' ? 'size' : 'disk',
  type: null, // null = every type, '' = files with no extension
  folder: 0,
  folderName: '',
  path: [],
  scanId: null,
  mode: 'direct',
  pct: 1,
  scanning: false,
  scanUnknown: false,
};

export const rootInfo = () => state.roots.find((r) => r.key === state.root) || null;

/** Query string for a browse call: root, folder, basis and (unless ``allTypes``) type, plus extras. */
export function query(extra, allTypes) {
  const p = new URLSearchParams({ root: state.root || '', folder: String(state.folder), basis: state.basis });
  if (state.type !== null && !allTypes) p.set('type', state.type === '' ? '-' : state.type);
  Object.entries(extra || {}).forEach(([k, v]) => p.set(k, String(v)));
  return p.toString();
}

/** The basis's byte figure of a node or file record. */
export const val = (x) => (state.basis === 'disk' ? x.disk : x.size);

// ----------------------------------------------------------- feature blocks
let blocks = {};
const listeners = new Set();

/** Whether block ``key`` is switched on. */
export const isOn = (key) => !(blocks[key] && blocks[key].enabled === false);

/** The block's state as the server gave it: ``{key, title, covers, enabled, available, reason, restart}``, or null. */
export const block = (key) => blocks[key] || null;

/** Call ``fn`` after every change to the blocks' state; returns the call that stops it. */
export function onFeaturesChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Take a list of block states from the server: mark the page, then, only when a block went on or off, tell the parts that run work of their own. */
export function setFeatures(list) {
  const before = document.documentElement.dataset.featuresOff || '';
  blocks = {};
  for (const item of Array.isArray(list) ? list : []) {
    if (item && typeof item.key === 'string') blocks[item.key] = item;
  }
  const off = Object.keys(blocks).filter((k) => !isOn(k)).join(' ');
  document.documentElement.dataset.featuresOff = off;
  if (off !== before) listeners.forEach((fn) => fn());
}

/** Read the blocks from the server at start; a failed read leaves every block on. */
export async function loadFeatures() {
  try {
    setFeatures((await getJSON('/api/features')).features);
  } catch (e) {
    setFeatures([]);
  }
}
