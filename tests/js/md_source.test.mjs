// The Markdown editor's text helpers: the highlighting keeps every character, the format edits toggle, the diff reads - then +.
import test from 'node:test';
import assert from 'node:assert/strict';
import { caretLine, formatEdit, highlightSource, lineDiff } from '../../app/webapp/static/md-source.js';

const unhtml = (html) => html.replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
  .replace(/&#39;/g, "'").replace(/&amp;/g, '&');

test('the highlighted copy has exactly the source text, plus the trailing newline', () => {
  const text = '# Head\n\nSome **bold** & `code <b>` [a](b.md)\n- [ ] task\n> quote\n```js\nif (a < b) {}\n```\n__x__ "q" \'s\'';
  assert.equal(unhtml(highlightSource(text)), text + '\n');
});

test('a wrap toggles around the selection', () => {
  const on = formatEdit('say hi now', 4, 6, 'bold');
  assert.deepEqual(on, { from: 4, to: 6, insert: '**hi**', selStart: 6, selEnd: 8 });
  const text = 'say **hi** now';
  assert.equal(formatEdit(text, 4, 10, 'bold').insert, 'hi');
});

test('a prefix toggles on every selected line', () => {
  const text = 'one\ntwo\nthree';
  const on = formatEdit(text, 1, 5, 'list');
  assert.equal(text.slice(0, on.from) + on.insert + text.slice(on.to), '- one\n- two\nthree');
  const listed = '- one\n- two\nthree';
  const off = formatEdit(listed, 0, 9, 'list');
  assert.equal(listed.slice(0, off.from) + off.insert + listed.slice(off.to), text);
});

test('a link wraps the selection and selects the address', () => {
  const e = formatEdit('see docs', 4, 8, 'link');
  assert.equal(e.insert, '[docs](url)');
  assert.equal('see [docs](url)'.slice(e.selStart, e.selEnd), 'url');
});

test('the caret line counts the newlines before it', () => {
  assert.equal(caretLine('a\nb\nc', 0), 0);
  assert.equal(caretLine('a\nb\nc', 2), 1);
  assert.equal(caretLine('a\nb\nc', 5), 2);
});

test('the diff keeps shared lines and lists the copy on disk first', () => {
  assert.deepEqual(lineDiff('# T\nTheirs\nend', '# T\nMine\nend'), [
    { op: '=', text: '# T' }, { op: '-', text: 'Theirs' }, { op: '+', text: 'Mine' }, { op: '=', text: 'end' },
  ]);
  assert.deepEqual(lineDiff('a', 'a'), [{ op: '=', text: 'a' }]);
});
