// What Settings reads when the server's answer is old, partial or not a config at all.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULTS, configWithDefaults, loadFailureText, serverSentence, settingsBody } from '../../app/webapp/static/config-defaults.js';

test('an empty or non-object config is every default', () => {
  for (const raw of [{}, null, undefined, [], 'text', 7]) assert.deepEqual(configWithDefaults(raw), DEFAULTS);
});

test('a missing block, or one that is not an object, takes its default', () => {
  const cfg = configWithDefaults({ remote_access: false, tray: null, desktop: 'on', outlook: [] });
  assert.equal(cfg.remote_access, false);
  assert.deepEqual(cfg.tray, DEFAULTS.tray);
  assert.deepEqual(cfg.desktop, DEFAULTS.desktop);
  assert.deepEqual(cfg.outlook, DEFAULTS.outlook);
});

test('a block with only some keys keeps those and defaults the rest', () => {
  const cfg = configWithDefaults({ email: { roots: ['C:/Invented'] }, outlook: { category: 'Filed' } });
  assert.deepEqual(cfg.email, { ...DEFAULTS.email, roots: ['C:/Invented'] });
  assert.equal(cfg.outlook.category, 'Filed');
  assert.equal(cfg.outlook.add_category, false);
});

test('a null roots stays null, and a list stays a list', () => {
  assert.equal(configWithDefaults({ roots: null }).roots, null);
  assert.deepEqual(configWithDefaults({ roots: ['D:/Invented'] }).roots, ['D:/Invented']);
});

test('defaults are not shared between calls', () => {
  configWithDefaults({}).email.roots.push('x');
  assert.deepEqual(configWithDefaults({}).email.roots, []);
});

test('a body that is not a config is null', () => {
  for (const raw of [null, {}, [], { config: null }, { config: [] }, 'x']) assert.equal(settingsBody(raw), null);
});

test('an older body without remote, archive, index size or config_file still reads, with no setup card', () => {
  const body = settingsBody({ config: {} });
  assert.equal(body.config_file.exists, true);
  assert.equal(body.remote.running, 'off');
  assert.equal(body.archive.ready, false);
  assert.equal(body.index_bytes, 0);
  assert.equal(body.created, false);
});

test('the screenshot folder is empty and not openable unless the server reports it', () => {
  assert.deepEqual(settingsBody({ config: {} }).snap_folder, { path: '', can_open: false });
  assert.deepEqual(settingsBody({ config: {}, snap_folder: { path: 'C:/Invented', can_open: true } }).snap_folder, { path: 'C:/Invented', can_open: true });
  assert.deepEqual(settingsBody({ config: {}, snap_folder: { path: 3, can_open: 'yes' } }).snap_folder, { path: '', can_open: false });
});

test('only an explicit missing file asks for the setup card', () => {
  assert.equal(settingsBody({ config: {}, config_file: { exists: false } }).config_file.exists, false);
  assert.equal(settingsBody({ config: {}, config_file: { exists: true } }).config_file.exists, true);
  assert.equal(settingsBody({ config: {}, config_file: {} }).config_file.exists, true);
  assert.equal(settingsBody({ config: {}, created: true }).created, true);
});

test('a load failure names what failed and what to do, never the script error', () => {
  const text = loadFailureText('abc1234');
  assert.match(text, /couldn't be read from the server \(build abc1234\)\. Reload/);
  assert.doesNotMatch(loadFailureText(''), /build/);
  assert.doesNotMatch(text, /undefined|Cannot read/);
});

test('a refused request keeps the server sentence; a thrown script or network error becomes a plain one', () => {
  const refused = Object.assign(new Error('Folder not found: X'), { status: 400 });
  assert.equal(serverSentence(refused), 'Folder not found: X');
  for (const err of [new TypeError("Cannot read properties of undefined (reading 'left_click')"), new TypeError('Failed to fetch'), null, undefined]) {
    assert.doesNotMatch(serverSentence(err), /Cannot read|Failed to fetch|undefined/);
  }
});
