/* The email search box's operators: split it into words and operators, write it back, and read one.
 *
 * The server parses the same box with src/email/query.py; both split text the
 * same way, so a chip, a filter field and the box always say the same thing.
 */

export const OPERATORS = ['from', 'to', 'cc', 'with', 'subject', 'filename', 'has', 'larger', 'after', 'before', 'newer_than', 'in', 'is', 'id'];

/** The order operators are written back in, and each one's chip label. */
const ORDER = OPERATORS;
export const LABEL = {
  from: 'From', to: 'To', cc: 'Cc', with: 'With', subject: 'Subject', filename: 'Attachment', has: 'Has',
  larger: 'Larger than', after: 'After', before: 'Before', newer_than: 'Newer than', in: 'In', is: 'Is', id: 'Message id',
};

const TOKEN_RE = /(-?)([a-z_]+):"([^"]*)"|(-?)([a-z_]+):(\S+)|(-?)"([^"]*)"|(\S+)/gi;

/** ``[{neg, key, val}]`` for operators and ``[{neg, word, phrase, or}]`` for words, in typed order. */
export function tokenize(text) {
  const out = [];
  let pendingOr = false;
  let m;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(text || ''))) {
    if (m[2] || m[5]) {
      const [neg, key, val] = m[2] ? [m[1], m[2].toLowerCase(), m[3]] : [m[4], m[5].toLowerCase(), m[6]];
      if (OPERATORS.includes(key)) { out.push({ neg: !!neg, key, val }); pendingOr = false; continue; }
      out.push({ neg: !!neg, word: key + ':' + val, or: pendingOr });
    } else if (m[8] !== undefined) {
      out.push({ neg: !!m[7], word: m[8], phrase: true, or: pendingOr });
    } else {
      const raw = m[9];
      if (raw.toUpperCase() === 'OR') { pendingOr = out.length > 0 && !out[out.length - 1].key; continue; }
      const neg = raw.startsWith('-') && raw.length > 1;
      out.push({ neg, word: neg ? raw.slice(1) : raw, or: pendingOr });
    }
    pendingOr = false;
  }
  return out;
}

const quote = (v) => (/\s/.test(v) || v === '' ? '"' + v + '"' : v);

/** The box text for ``tokens``: words first as typed, then operators in a fixed order. */
export function serialize(tokens) {
  const words = [];
  tokens.filter((t) => t.word !== undefined).forEach((t) => {
    if (t.or && words.length) words.push('OR');
    words.push((t.neg ? '-' : '') + (t.phrase ? '"' + t.word + '"' : quote(t.word)));
  });
  const ops = tokens.filter((t) => t.key).sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key))
    .map((t) => (t.neg ? '-' : '') + t.key + ':' + quote(t.val));
  return words.concat(ops).join(' ');
}

/** The value of the first positive ``key:`` operator, or ''. */
export function getOp(tokens, key) {
  const t = tokens.find((x) => x.key === key && !x.neg);
  return t ? t.val : '';
}

/** ``text`` with every ``key:`` operator replaced by one carrying ``val`` (none when ``val`` is empty). */
export function withOp(text, key, val) {
  const toks = tokenize(text).filter((t) => t.key !== key);
  if (val) toks.push({ neg: false, key, val });
  return serialize(toks);
}

/** The positive free words and phrases: the ones a highlight marks. */
export const wordsOf = (tokens) => tokens.filter((t) => t.word !== undefined && !t.neg).map((t) => t.word);
