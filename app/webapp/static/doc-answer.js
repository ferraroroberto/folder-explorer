/* What an answer from the document API means, with no DOM, so the viewer and the editor name each condition the same way.
 *
 * A refusal the server made carries its own ``reason`` (``unreadable`` is
 * the disk read that failed, ``cloud_only``, ``too_large``…) and is passed
 * through. An answer with no reason is not about the file: a 404 means the
 * page and the running app are on different builds (the page is newer, as
 * during a restart), any other status is the app's own error, and no answer
 * at all is its own state. None of them is ever reported as a disk read.
 */

/** The refusal for a failed answer: ``{reason, message}`` from its HTTP ``status`` and parsed ``body`` (or null). */
export function refusal(status, body) {
  if (body && typeof body.reason === 'string' && body.reason) {
    return { reason: body.reason, message: typeof body.detail === 'string' ? body.detail : '' };
  }
  if (status === 404) return { reason: 'app_mismatch', message: 'The page and the app are on different versions' };
  return { reason: 'app_error', message: 'The app answered with an error (HTTP ' + status + ')' };
}

/** The refusal when the request never got an answer. */
export const NO_ANSWER = Object.freeze({ reason: 'no_answer', message: 'The app did not answer' });
