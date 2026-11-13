/* What a Markdown or HTML file dropped on the window sends, with no DOM so a test can hold it.
 *
 * A browser hands the page a dropped file's bytes and name, never its path.
 * The server is told the name, size, modified time and a SHA-256 of the bytes
 * (to find the file on this PC), and the text only when none matched. Nothing
 * here builds a path.
 */

// The viewer's size cap (src/docs/reader.py DOC_MAX_BYTES); the server checks it again.
export const DROP_MAX_BYTES = 2 * 1024 * 1024;

// The viewer's documents (src/docs/reader.py DOC_EXTS); the server checks the name again.
const DOCUMENT = /\.(md|markdown|html?)$/i;

export const isDocumentName = (name) => DOCUMENT.test(String(name || ''));

/** Whether a drag carries files (so the overlay shows); a drag of text or a link doesn't. */
export const dragHasFiles = (dataTransfer) => !!dataTransfer && Array.from(dataTransfer.types || []).includes('Files');

/** The file to open from a drop: ``{file}``, or ``{message}`` saying why none can open. The first document wins. */
export function pickDrop(files) {
  const list = Array.from(files || []);
  if (!list.length) return { message: 'Nothing was dropped' };
  const file = list.find((f) => isDocumentName(f.name));
  if (!file) return { message: 'Only Markdown and HTML files (.md, .markdown, .html, .htm) open here' };
  if (file.size > DROP_MAX_BYTES) return { message: 'Documents up to 2 MB open here' };
  return { file };
}

/** Bytes as lower-case hex, the form the server takes a SHA-256 in. */
export const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');

/** The match request for a dropped file: its name, size and modified time (ms), and the SHA-256 of its bytes when the browser could make one. */
export const matchBody = (file, sha256) => ({ name: file.name, size: file.size, modified: Math.floor(file.lastModified), ...(sha256 ? { sha256 } : {}) });

// Why the server found no copy on this PC, by its ``why`` code; the file then opens read-only.
const WHY_NOTES = {
  several: 'Several identical copies exist on this PC, so it opens read-only. Open the one you want from its folder.',
  not_found: 'No copy of it was found in the scanned folders, so it opens read-only.',
  not_confirmed: 'It could not be matched to a file on this PC (it may be only in OneDrive), so it opens read-only.',
};

/** The toast for a drop that opened read-only, or '' for a reason this page does not know. */
export const whyNote = (why) => WHY_NOTES[why] || '';

/** The read-only render request: the name and the text the page read. */
export const renderBody = (file, text) => ({ name: file.name, text });
