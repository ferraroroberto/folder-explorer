/* The editor's own glyphs (Lucide), added to the page once, the first time an editor opens.
 *
 * They live here rather than in index.html and doc.html, so both pages get one
 * copy and the pages carry only the glyphs they show before the editor opens.
 * A glyph the page already holds is skipped.
 */

const NS = 'http://www.w3.org/2000/svg';

const ICONS = {
  heading: ['M6 12h12', 'M6 20V4', 'M18 20V4'],
  bold: ['M6 12h9a4 4 0 0 1 0 8H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h7a4 4 0 0 1 0 8'],
  italic: ['M19 4h-9', 'M14 20H5', 'M15 4 9 20'],
  code: ['m16 18 6-6-6-6', 'm8 6-6 6 6 6'],
  link: ['M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71', 'M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'],
  list: ['M3 5h.01', 'M3 12h.01', 'M3 19h.01', 'M8 5h13', 'M8 12h13', 'M8 19h13'],
  'list-checks': ['m3 17 2 2 4-4', 'm3 7 2 2 4-4', 'M13 6h8', 'M13 12h8', 'M13 18h8'],
  'text-quote': ['M17 5H3', 'M21 12H8', 'M21 19H8', 'M3 12v7'],
  'triangle-alert': ['m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3', 'M12 9v4', 'M12 17h.01'],
};

/** Add the editor's glyphs to the page's sprite, once. */
export function addEditorIcons() {
  const missing = Object.keys(ICONS).filter((name) => !document.getElementById('i-' + name));
  if (!missing.length) return;
  const sprite = document.createElementNS(NS, 'svg');
  sprite.setAttribute('aria-hidden', 'true');
  sprite.setAttribute('focusable', 'false');
  sprite.setAttribute('style', 'position:absolute;width:0;height:0;overflow:hidden');
  missing.forEach((name) => {
    const symbol = document.createElementNS(NS, 'symbol');
    symbol.id = 'i-' + name;
    symbol.setAttribute('viewBox', '0 0 24 24');
    symbol.setAttribute('fill', 'none');
    ICONS[name].forEach((d) => {
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', d);
      symbol.appendChild(path);
    });
    sprite.appendChild(symbol);
  });
  document.body.prepend(sprite);
}
