/* The sun/moon theme toggle shared by the app and the document window: flips html[data-theme] and remembers it. */

// The key the pre-paint boot script in each page's <head> reads.
const THEME_KEY = 'folder-explorer.theme';

/** Wire every .theme-toggle on the page; each shows the action it takes (sun = switch to light). */
export function wireThemeToggle() {
  const paint = () => {
    const dark = document.documentElement.dataset.theme === 'dark';
    const label = dark ? 'Switch to light theme' : 'Switch to dark theme';
    document.querySelectorAll('.theme-toggle').forEach((b) => {
      b.querySelector('use').setAttribute('href', dark ? '#i-sun' : '#i-moon');
      b.setAttribute('aria-label', label);
      b.title = label;
    });
  };
  document.querySelectorAll('.theme-toggle').forEach((b) => b.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* this visit only */ }
    paint();
  }));
  paint();
}
