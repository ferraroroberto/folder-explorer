/* The Search preview's "Show all" load for one mail: idle, loading, ok or error.
 *
 * One declared state and one step function, so the preview never decides on its own whether a load may start
 * or what a mail's body is doing. Pure (no DOM, no imports); docs/body-state.md holds the table.
 */

export const BodyState = Object.freeze({
  IDLE: 'idle', // not asked for yet: the mail shows its indexed fields and a "Show all" button
  LOADING: 'loading', // the body is being read (and downloaded when the file is online-only)
  OK: 'ok', // the body arrived and `data` carries it
  ERROR: 'error', // the read failed; `error` is the server's code and `message` its text
});

// Every legal move. A loaded body never reloads; a failed one may be asked for again.
const NEXT = Object.freeze({
  [BodyState.IDLE]: [BodyState.LOADING],
  [BodyState.LOADING]: [BodyState.OK, BodyState.ERROR],
  [BodyState.OK]: [],
  [BodyState.ERROR]: [BodyState.LOADING],
});

/** A mail's body entry; one never asked for is idle. */
export function bodyOf(bodies, id) {
  return bodies.get(id) || { state: BodyState.IDLE };
}

/** Whether the mail's body may be asked for now (idle, or failed and retried). */
export function canLoad(bodies, id) {
  return NEXT[bodyOf(bodies, id).state].includes(BodyState.LOADING);
}

/** Move one mail's body to `next` with its details; an illegal move throws and leaves the entry as it was. */
export function bodyStep(bodies, id, next, detail = {}) {
  const from = bodyOf(bodies, id).state;
  if (!NEXT[from] || !NEXT[from].includes(next)) throw new Error('body ' + id + ': ' + from + ' cannot become ' + next);
  bodies.set(id, { ...detail, state: next });
}
