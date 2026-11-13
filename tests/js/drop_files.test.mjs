// What a dropped Markdown or HTML file sends: its name, size and modified time (then its text), never a path.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DROP_MAX_BYTES, dragHasFiles, isDocumentName, matchBody, pickDrop, renderBody } from '../../app/webapp/static/drop-files.js';

// A browser File carries more than the page needs; a Chromium build may add a path-like property too.
const file = (name, size = 10, extra = {}) => ({ name, size, lastModified: 1700000000123.7, type: '', webkitRelativePath: 'folder/' + name, path: 'C:\\invented\\' + name, ...extra });

test('only .md, .markdown, .html and .htm names are documents, whatever their case', () => {
  for (const name of ['a.md', 'A.MD', 'notes.markdown', 'x.y.Md', 'page.html', 'PAGE.HTM']) assert.equal(isDocumentName(name), true, name);
  for (const name of ['a.txt', 'a.md.bak', 'md', 'a.mdx', 'a.xhtml', 'a.html.bak', '', undefined]) assert.equal(isDocumentName(name), false, String(name));
});

test('the match request holds the name, size and modified time and nothing else', () => {
  const body = matchBody(file('guide.md', 42));
  assert.deepEqual(Object.keys(body).sort(), ['modified', 'name', 'size']);
  assert.deepEqual(body, { name: 'guide.md', size: 42, modified: 1700000000123 });
  assert.ok(!JSON.stringify(body).includes('invented'), 'no path text reaches the server');
});

test('the render request holds the name and the text and nothing else', () => {
  const body = renderBody(file('guide.md'), '# Hi');
  assert.deepEqual(Object.keys(body).sort(), ['name', 'text']);
  assert.ok(!JSON.stringify(body).includes('invented'));
});

test('the first document of a drop is the one that opens, Markdown or HTML', () => {
  assert.equal(pickDrop([file('photo.png'), file('page.html'), file('a.md')]).file.name, 'page.html');
  const picked = pickDrop([file('photo.png'), file('a.md'), file('b.md')]);
  assert.equal(picked.file.name, 'a.md');
});

test('a drop with no document, no file, or one past the cap names why it cannot open', () => {
  assert.match(pickDrop([file('photo.png')]).message, /Only Markdown and HTML/);
  assert.match(pickDrop([]).message, /Nothing/);
  assert.match(pickDrop(null).message, /Nothing/);
  assert.match(pickDrop([file('big.md', DROP_MAX_BYTES + 1)]).message, /2 MB/);
  assert.ok(pickDrop([file('edge.md', DROP_MAX_BYTES)]).file);
});

test('only a drag of files shows the drop target', () => {
  assert.equal(dragHasFiles({ types: ['Files'] }), true);
  assert.equal(dragHasFiles({ types: ['text/plain', 'Files'] }), true);
  assert.equal(dragHasFiles({ types: ['text/plain', 'text/uri-list'] }), false);
  assert.equal(dragHasFiles(null), false);
});
