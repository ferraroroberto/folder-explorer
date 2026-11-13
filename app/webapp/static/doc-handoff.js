/* Hands a dropped document the scan doesn't have to the /doc window it opens in.
 *
 * Such a file has no address: the server rendered it from text the page read,
 * and kept nothing. So the app page holds the rendered document and the new
 * window asks it for the document by a one-time token in its address
 * (``/doc?dropped=<token>``), over ``postMessage`` between the two same-origin
 * windows. The text is never in the address, and the page gives it out once,
 * to the window it opened; a reload of that window finds nothing and says so.
 */

export const DROPPED_PARAM = 'dropped';

const ASK = 'folder-explorer:dropped-ask';
const GIVE = 'folder-explorer:dropped-doc';
// A window that never asks (closed, or blocked after opening) must not keep a document in memory for good.
const KEEP_MS = 60_000;
// How long the new window waits for an app page that may have been closed or navigated away.
const WAIT_MS = 3000;

// The app page's side: the rendered documents it still holds, by token, and the window each was opened for.
const held = new Map();
let listening = false;

function listen() {
  if (listening) return;
  listening = true;
  window.addEventListener('message', (e) => {
    const m = e.data;
    if (e.origin !== location.origin || !m || m.type !== ASK || !e.source) return;
    const mine = held.get(m.token);
    // A token is given once, and only to the window it was made for.
    const doc = mine && mine.win === e.source ? mine.doc : null;
    if (doc) held.delete(m.token);
    e.source.postMessage({ type: GIVE, token: m.token, doc }, location.origin);
  });
}

const newToken = () => Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('');

/** Point ``win`` (a window this page opened) at the /doc view of ``doc``, which it will ask this page for. */
export function handOff(win, doc) {
  listen();
  const token = newToken();
  held.set(token, { win, doc });
  setTimeout(() => held.delete(token), KEEP_MS);
  win.location.href = '/doc?' + DROPPED_PARAM + '=' + token;
}

/** The token a /doc address carries for a dropped file, or null. */
export const droppedToken = (href) => new URL(href, location.origin).searchParams.get(DROPPED_PARAM);

/** The new window's side: the rendered document the opener holds for ``token``, or null when it can't give it. */
export function fetchDropped(token) {
  return new Promise((resolve) => {
    const opener = window.opener;
    if (!opener) {
      resolve(null);
      return;
    }
    let timer = null;
    const done = (doc) => {
      clearTimeout(timer);
      window.removeEventListener('message', on);
      resolve(doc);
    };
    function on(e) {
      const m = e.data;
      if (e.source === opener && e.origin === location.origin && m && m.type === GIVE && m.token === token) done(m.doc || null);
    }
    window.addEventListener('message', on);
    timer = setTimeout(() => done(null), WAIT_MS);
    opener.postMessage({ type: ASK, token }, location.origin);
  });
}
