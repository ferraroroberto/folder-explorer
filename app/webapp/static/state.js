/* The page's shared state and the query string every browse call carries. */

export const APP = 'folder-explorer';

export function readPref(key, fallback) {
  try { return localStorage.getItem(APP + '.' + key) || fallback; } catch (e) { return fallback; }
}

export function writePref(key, value) {
  try { localStorage.setItem(APP + '.' + key, value); } catch (e) { /* not kept across visits */ }
}

export const state = {
  roots: [],
  root: null,
  basis: readPref('basis', 'size') === 'disk' ? 'disk' : 'size',
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
