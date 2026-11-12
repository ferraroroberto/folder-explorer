/* The Markdown editor's text helpers, with no DOM: the source highlighting, the format bar's edits and the conflict's line diff.
 *
 * The highlighting sits under a transparent text field, so it only colours
 * characters: it never adds, drops or reflows one, and never changes a
 * glyph's width (no bold or italic), or the caret would drift from the text.
 */

import { esc } from './fmt.js';

// Past this many line pairs the diff would stall a phone; the conflict then shows both copies whole.
const DIFF_MAX_CELLS = 4000000;

const span = (cls, html) => '<span class="' + cls + '">' + html + '</span>';

function inline(line) {
  return esc(line)
    .replace(/`[^`]+`/g, (m) => span('md-code', m))
    .replace(/(\*\*|__)(?=\S)(.+?)\1/g, (m) => span('md-strong', m))
    .replace(/!?\[[^\]\n]*\]\([^)\n]*\)/g, (m) => span('md-link', m));
}

/** ``text`` as coloured HTML, line for line; a trailing newline keeps the last empty line's height. */
export function highlightSource(text) {
  let fence = false;
  return text.split('\n').map((line) => {
    if (/^ {0,3}(```|~~~)/.test(line)) {
      fence = !fence;
      return span('md-fence', esc(line));
    }
    if (fence) return span('md-code', esc(line));
    if (/^ {0,3}#{1,6}(\s|$)/.test(line)) return span('md-heading', esc(line));
    if (/^ {0,3}>/.test(line)) return span('md-quote', inline(line));
    const m = /^(\s*)([-*+]|\d+[.)])( \[[ xX]\])?(?=\s|$)/.exec(line);
    if (m) return esc(m[1]) + span('md-mark', esc(m[2] + (m[3] || ''))) + inline(line.slice(m[0].length));
    return inline(line);
  }).join('\n') + '\n';
}

/** The zero-based line the offset ``pos`` sits on. */
export function caretLine(text, pos) {
  let n = 0;
  for (let i = text.indexOf('\n'); i !== -1 && i < pos; i = text.indexOf('\n', i + 1)) n += 1;
  return n;
}

const WRAPS = { bold: '**', italic: '_', code: '`' };
const PREFIXES = { heading: '## ', list: '- ', task: '- [ ] ', quote: '> ' };

/**
 * One format-bar edit on ``text`` with the selection ``start``–``end``:
 * ``{from, to, insert, selStart, selEnd}``, the range to replace, its new text
 * and the selection after it. A wrap (bold, italic, code) toggles around the
 * selection; a prefix (heading, list, task, quote) toggles on every selected line.
 */
export function formatEdit(text, start, end, kind) {
  const picked = text.slice(start, end);
  if (WRAPS[kind]) {
    const w = WRAPS[kind];
    if (picked.startsWith(w) && picked.endsWith(w) && picked.length >= 2 * w.length) {
      const inner = picked.slice(w.length, picked.length - w.length);
      return { from: start, to: end, insert: inner, selStart: start, selEnd: start + inner.length };
    }
    return { from: start, to: end, insert: w + picked + w, selStart: start + w.length, selEnd: end + w.length };
  }
  if (kind === 'link') {
    const label = picked || 'text';
    const insert = '[' + label + '](url)';
    const at = start + label.length + 3;
    return { from: start, to: end, insert, selStart: at, selEnd: at + 3 };
  }
  const p = PREFIXES[kind];
  const from = text.lastIndexOf('\n', start - 1) + 1;
  const nl = text.indexOf('\n', Math.max(end - (end > start && text[end - 1] === '\n' ? 1 : 0), from));
  const to = nl === -1 ? text.length : nl;
  const lines = text.slice(from, to).split('\n');
  const all = lines.every((l) => l.startsWith(p));
  const insert = lines.map((l) => (all ? l.slice(p.length) : l.startsWith(p) ? l : p + l)).join('\n');
  return { from, to, insert, selStart: from, selEnd: from + insert.length };
}

/** A line diff of ``theirs`` against ``mine``: ``[{op, text}]`` with ``op`` '=' (both), '-' (on disk only) or '+' (yours only). */
export function lineDiff(theirs, mine) {
  const a = theirs.split('\n');
  const b = mine.split('\n');
  if (a.length * b.length > DIFF_MAX_CELLS) return [...a.map((text) => ({ op: '-', text })), ...b.map((text) => ({ op: '+', text }))];
  // Longest common subsequence, filled from the end so the walk below reads forward; on a tie the copy on disk's line goes first.
  const w = b.length + 1;
  const lcs = new Uint32Array((a.length + 1) * w);
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i * w + j] = a[i] === b[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { out.push({ op: '=', text: a[i] }); i += 1; j += 1; }
    else if (j < b.length && (i === a.length || lcs[i * w + j + 1] > lcs[(i + 1) * w + j])) { out.push({ op: '+', text: b[j] }); j += 1; }
    else { out.push({ op: '-', text: a[i] }); i += 1; }
  }
  return out;
}
