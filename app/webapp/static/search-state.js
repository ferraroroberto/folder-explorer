/* The Search tab's shared state and the markup helpers its three modules use. */

import { esc } from './fmt.js';
import { readPref, writePref } from './state.js';

// Recent searches kept per mode, on this device only.
const RECENT_MAX = 10;

// The three things Search looks in, in the order a chosen-but-off mode falls back through.
export const MODES = ['emails', 'folders', 'docs'];

export const S = {
  status: null, // /api/search/status
  mode: MODES.includes(readPref('search.mode', 'emails')) ? readPref('search.mode', 'emails') : 'emails',
  q: { emails: '', folders: '', docs: '' },
  sort: 'relevance',
  results: [],
  total: 0,
  facets: null,
  notes: [],
  words: [],
  selected: -1,
  saved: [],
  detail: new Map(), // email row id -> /api/emails/{id}
  body: new Map(), // email row id -> {state, data, error}; moved only by body-state.js
  frame: new Map(), // email row id -> {look: 'dark' | 'light' | null (follow the app), remote: bool}
  seq: 0, // bumps per search, so a slow answer never overwrites a newer one
  tab: null, // 'search' while the tab shows; only then does the box write the URL
};

export const WIDE = window.matchMedia('(min-width: 1100px) and (pointer: fine)');
export const $ = (id) => document.getElementById(id);
/** The search box. */
export const box = () => $('q');

// The server wraps each match in these two private-use characters (src/email/search.py).
const MARK_START = String.fromCharCode(0xe000);
const MARK_END = String.fromCharCode(0xe001);

/** Open or close a side card: its toggle's aria-expanded and its body's hidden move together. */
export function setOpen(card, open) {
  const btn = $(card).querySelector('.side-toggle');
  btn.setAttribute('aria-expanded', String(open));
  $(btn.getAttribute('aria-controls')).hidden = !open;
}

/** Text without the server's match markers, for a title attribute. */
export const plain = (text) => (text || '').split(MARK_START).join('').split(MARK_END).join('');

/** The modes that can run here, in :data:`MODES` order. */
export const readyModes = () => (S.status ? MODES.filter((m) => S.status[m].ready) : MODES);

// Where a chosen-but-off mode lands: Folders first (what Documents off means), then Emails.
const FALLBACK = ['folders', 'emails', 'docs'];

/** Which mode actually runs: the chosen one, or the first of :data:`FALLBACK` that is on when the chosen one is off here. */
export function effectiveMode() {
  const on = readyModes();
  if (!S.status || on.includes(S.mode)) return S.mode;
  return FALLBACK.find((m) => on.includes(m)) || null;
}


/** Server text with its match markers, escaped, with the markers turned into <mark>. */
export function marked(text) {
  return esc(text || '').split(MARK_START).join('<mark>').split(MARK_END).join('</mark>');
}

/** ``text`` escaped, with every case-insensitive occurrence of ``words`` marked. */
export function hl(text, words) {
  let html = esc(text || '');
  (words || []).filter((w) => w && w.length > 1).forEach((w) => {
    const re = new RegExp('(' + esc(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
    html = html.replace(/(<[^>]*>)|([^<]+)/g, (all, tag, txt) => (tag || txt.replace(re, '<mark>$1</mark>')));
  });
  return html;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** ``14 Oct 2025`` from an ISO date; ``long`` adds the time. */
export function mailDate(iso, long) {
  if (!iso) return '';
  const y = iso.slice(0, 4);
  const m = Number(iso.slice(5, 7));
  const d = Number(iso.slice(8, 10));
  if (!m || !d) return iso;
  const day = d + ' ' + MONTHS[m - 1] + ' ' + y;
  return long && iso.length >= 16 ? day + ', ' + iso.slice(11, 16) : day;
}

/** The last two folders of a path, as a result row labels its folder. */
export const shortFolder = (path) => (path || '').split(/[\\/]/).filter(Boolean).slice(-2).join('\\');

export function recent(mode) {
  try { return JSON.parse(readPref('search.recent.' + mode, '[]')).slice(0, RECENT_MAX); } catch (e) { return []; }
}

export function pushRecent(mode, text) {
  const q = (text || '').trim();
  if (!q || !mode) return;
  writePref('search.recent.' + mode, JSON.stringify([q].concat(recent(mode).filter((r) => r !== q)).slice(0, RECENT_MAX)));
}

/** Forget one recent search for ``mode``. */
export function dropRecent(mode, text) {
  writePref('search.recent.' + mode, JSON.stringify(recent(mode).filter((r) => r !== text)));
}

/** Forget every recent search for ``mode``. */
export function clearRecent(mode) {
  writePref('search.recent.' + mode, '[]');
}

/** Whether the Recent list is unfolded on this device; it stays folded until the user opens it. */
export const recentOpen = () => readPref('search.recent.open', '0') === '1';

export const setRecentOpen = (open) => writePref('search.recent.open', open ? '1' : '0');

/** Copy ``text`` to the clipboard, falling back to a hidden textarea where the API is not allowed. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    ta.remove();
    return ok;
  }
}
