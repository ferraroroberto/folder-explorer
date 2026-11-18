/* The text of a local document and the bytes it goes back as, with no DOM so a test can hold it.
 *
 * Mirrors what ``src/docs/writer.py`` keeps for a document it saves: a UTF-8
 * byte-order mark, the file's line end and whether it ends in a newline. The
 * editor's text always has ``\n`` line ends, so a save puts the file's own
 * back; a file that is not UTF-8, or is over the cap, is not editable.
 */

// The server editor's cap (src/docs/writer.py EDIT_MAX_BYTES).
export const EDIT_MAX_BYTES = 1024 * 1024;

const BOM = [0xef, 0xbb, 0xbf];

const hasBom = (bytes) => BOM.every((b, i) => bytes[i] === b);

/** The file's text with ``\n`` line ends and its shape ``{bom, newline, trailing}``; null when it is not UTF-8. */
export function decode(bytes) {
  const bom = hasBom(bytes);
  let text;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bom ? bytes.subarray(BOM.length) : bytes);
  } catch (e) {
    return null;
  }
  const crlf = (text.match(/\r\n/g) || []).length;
  const lf = (text.match(/\n/g) || []).length;
  const unified = text.replace(/\r\n/g, '\n');
  return { text: unified, shape: { bom, newline: crlf > lf - crlf ? '\r\n' : '\n', trailing: unified.endsWith('\n') } };
}

/** ``text`` as the file's bytes: its line end, its final newline (or none) and its byte-order mark. */
export function encode(text, shape) {
  let t = text.replace(/\r\n/g, '\n');
  if (shape.trailing && t && !t.endsWith('\n')) t += '\n';
  else if (!shape.trailing && t.endsWith('\n')) t = t.slice(0, -1);
  const body = new TextEncoder().encode(shape.newline === '\n' ? t : t.replace(/\n/g, shape.newline));
  if (!shape.bom) return body;
  const out = new Uint8Array(BOM.length + body.length);
  out.set(BOM);
  out.set(body, BOM.length);
  return out;
}

/** The version a save is checked against: the modified time (ms), the size and a hash of the bytes, so a same-size change in one tick still differs. */
export const versionOf = (lastModified, size, hex) => Math.floor(lastModified) + '.' + size + '.' + hex.slice(0, 24);
