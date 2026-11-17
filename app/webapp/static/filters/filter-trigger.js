/* The Filters & sort trigger both sheets share: the glyph, a count of the settings that differ from their default, and a name that spells them out. */

/** Show ``active`` (one phrase per non-default setting) on ``button``: a count badge when any, and an accessible name that lists them. */
export function paintFilterTrigger(button, active) {
  const badge = button.querySelector('.filter-count');
  badge.textContent = active.length ? String(active.length) : '';
  badge.hidden = !active.length;
  button.classList.toggle('has-value', active.length > 0);
  const name = active.length ? 'Filters & sort, ' + active.length + ' on: ' + active.join(', ') : 'Filters & sort';
  button.setAttribute('aria-label', name);
  button.title = name;
}
