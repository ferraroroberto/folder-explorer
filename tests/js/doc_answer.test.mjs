// What a failed document-API answer means: the server's own refusal passes through; an answer with no reason is never a disk read.
import test from 'node:test';
import assert from 'node:assert/strict';
import { NO_ANSWER, refusal } from '../../app/webapp/static/doc-answer.js';

test("the server's own refusal passes through, unreadable included", () => {
  assert.deepEqual(refusal(422, { reason: 'unreadable', detail: 'Windows could not read it' }), { reason: 'unreadable', message: 'Windows could not read it' });
  assert.deepEqual(refusal(423, { reason: 'cloud_only', detail: 'Only in OneDrive' }), { reason: 'cloud_only', message: 'Only in OneDrive' });
  assert.equal(refusal(200, { editable: false, reason: 'not_utf8', detail: 'x' }).reason, 'not_utf8');
});

test('a 404 with no reason is the page and the app on different builds', () => {
  for (const body of [{ detail: 'Not Found' }, null, { reason: '' }]) assert.equal(refusal(404, body).reason, 'app_mismatch');
});

test('any other status with no reason is an app error that names the status', () => {
  const r = refusal(500, { detail: 'Internal Server Error' });
  assert.equal(r.reason, 'app_error');
  assert.match(r.message, /HTTP 500/);
  assert.equal(refusal(422, { detail: [{ msg: 'field required' }] }).reason, 'app_error');
});

test('no answer at all is its own state, never unreadable', () => {
  assert.equal(NO_ANSWER.reason, 'no_answer');
  for (const s of [404, 500, 502]) assert.notEqual(refusal(s, null).reason, 'unreadable');
});
