/* Formatting and small markup helpers shared by every view. */

export const KB = 1024;

export function fmtBytes(b) {
  if (!b) return '0 B';
  if (b < KB) return b + ' B';
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let v = b / KB;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)) + ' ' + units[i];
}

const NF = new Intl.NumberFormat('en-US');
export const fmtNum = (n) => NF.format(n || 0);

/** ``3 files`` / ``1 file``. */
export const plural = (n, one, many) => fmtNum(n) + ' ' + (n === 1 ? one : many || one + 's');

export const pct = (part, whole) => (whole ? (part / whole) * 100 : 0);

export function fmtPct(p) {
  if (!p) return '0%';
  if (p < 0.1) return '<0.1%';
  return p < 10 ? p.toFixed(1) + '%' : Math.round(p) + '%';
}

export function fmtDate(unix) {
  if (!unix) return '';
  const d = new Date(unix * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

export function fmtAgo(unix) {
  if (!unix) return 'never';
  const s = Math.max(0, Date.now() / 1000 - unix);
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  const days = Math.round(s / 86400);
  return days === 1 ? 'yesterday' : days + ' days ago';
}

export function fmtDuration(seconds) {
  if (seconds < 60) return seconds.toFixed(1) + ' s';
  const m = Math.floor(seconds / 60);
  return m + ' min ' + Math.round(seconds - m * 60) + ' s';
}

/** A stored path as Windows shows it: every separator a backslash. Display only; the stored value stays. */
export const winPath = (p) => String(p || '').replace(/\//g, '\\');

export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const glyph = (name, cls) => '<svg class="icon' + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';

export const bar = (p) => '<span class="share-bar" aria-hidden="true"><span style="width:' + Math.min(100, Math.max(0, p)).toFixed(2) + '%"></span></span>';

export const cloudMark = (online) => (online ? '<svg class="icon" role="img" aria-label="Online-only"><title>Online-only</title><use href="#i-cloud"></use></svg>' : '');

/** The display label of an extension; '' is a file with none. */
export const extLabel = (ext) => (ext ? '.' + ext : '(no extension)');
