// The Search preview's body load: every legal move, and the moves it refuses.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BodyState, bodyOf, bodyStep, canLoad } from '../../app/webapp/static/body-state.js';

test('a mail never asked for is idle and may be loaded', () => {
  const bodies = new Map();
  assert.equal(bodyOf(bodies, 7).state, BodyState.IDLE);
  assert.equal(canLoad(bodies, 7), true);
});

test('a load runs idle, loading, ok and keeps its data', () => {
  const bodies = new Map();
  bodyStep(bodies, 1, BodyState.LOADING);
  assert.equal(canLoad(bodies, 1), false);
  bodyStep(bodies, 1, BodyState.OK, { data: { text: 'hello' } });
  assert.deepEqual(bodyOf(bodies, 1), { data: { text: 'hello' }, state: BodyState.OK });
});

test('a failed load keeps its reason and may be asked for again', () => {
  const bodies = new Map();
  bodyStep(bodies, 2, BodyState.LOADING);
  bodyStep(bodies, 2, BodyState.ERROR, { error: 'timeout', message: 'too slow' });
  assert.equal(bodyOf(bodies, 2).error, 'timeout');
  assert.equal(canLoad(bodies, 2), true);
  bodyStep(bodies, 2, BodyState.LOADING);
  assert.equal(bodyOf(bodies, 2).state, BodyState.LOADING);
});

test('an illegal move is refused and leaves the entry as it was', () => {
  // state-machine-proof: body-state
  const bodies = new Map();
  assert.throws(() => bodyStep(bodies, 3, BodyState.OK, { data: {} }), /idle cannot become ok/);
  assert.throws(() => bodyStep(bodies, 3, BodyState.ERROR), /idle cannot become error/);
  assert.equal(bodies.has(3), false);
  bodyStep(bodies, 3, BodyState.LOADING);
  assert.throws(() => bodyStep(bodies, 3, BodyState.LOADING), /loading cannot become loading/);
  bodyStep(bodies, 3, BodyState.OK, { data: { text: 'x' } });
  assert.throws(() => bodyStep(bodies, 3, BodyState.LOADING), /ok cannot become loading/);
  assert.throws(() => bodyStep(bodies, 3, 'paused'), /cannot become paused/);
  assert.equal(bodyOf(bodies, 3).state, BodyState.OK);
});
