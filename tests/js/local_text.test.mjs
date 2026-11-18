// What a local document's save keeps of the file it replaces: byte-order mark, line end and final newline (the server writer's shape).
import test from 'node:test';
import assert from 'node:assert/strict';
import { EDIT_MAX_BYTES, decode, encode, versionOf } from '../../app/webapp/static/localdoc/local-text.js';

const bytes = (text, bom = false) => Uint8Array.from([...(bom ? [0xef, 0xbb, 0xbf] : []), ...new TextEncoder().encode(text)]);

test('a file reads as text with \\n line ends and a shape that puts its own back', () => {
  const a = decode(bytes('one\r\ntwo\r\n'));
  assert.deepEqual(a, { text: 'one\ntwo\n', shape: { bom: false, newline: '\r\n', trailing: true } });
  assert.deepEqual(encode(a.text, a.shape), bytes('one\r\ntwo\r\n'));
});

test('a byte-order mark and a missing final newline survive a round trip', () => {
  const a = decode(bytes('é\ntwo', true));
  assert.deepEqual(a.shape, { bom: true, newline: '\n', trailing: false });
  assert.deepEqual(encode('é\nthree\n', a.shape), bytes('é\nthree', true));
  assert.deepEqual(encode(a.text, a.shape), bytes('é\ntwo', true));
});

test('a file with more bare line feeds than CRLF keeps \\n', () => {
  assert.equal(decode(bytes('a\nb\nc\r\n')).shape.newline, '\n');
});

test('text with its own CRLF is saved with the file line end', () => {
  const shape = { bom: false, newline: '\n', trailing: true };
  assert.deepEqual(encode('a\r\nb', shape), bytes('a\nb\n'));
});

test('a file that is not UTF-8 does not decode', () => {
  assert.equal(decode(Uint8Array.from([0xff, 0xfe, 0x41])), null);
});

test('the version moves with the time, the size or one byte', () => {
  const hex = 'ab'.repeat(32);
  const base = versionOf(1700000000123.9, 10, hex);
  assert.equal(base, '1700000000123.10.' + 'ab'.repeat(12));
  assert.notEqual(base, versionOf(1700000000124, 10, hex));
  assert.notEqual(base, versionOf(1700000000123, 11, hex));
  assert.notEqual(base, versionOf(1700000000123, 10, 'cd'.repeat(32)));
});

test('the editor cap is the server writer cap', () => {
  assert.equal(EDIT_MAX_BYTES, 1024 * 1024);
});
