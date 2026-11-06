# `code-listing` — a fenced block or a diff in its frame

The fleet's canonical **code listing** (`~/.claude/design.md` → "Component contracts" → code-listing): the `card` surface on a hairline, a small `caption` header with the language or a title on the left and the **copy control** on the right, then the listing in mono at 13px (the one sanctioned off-scale size, for mono listings only).

**The copy control is an unpainted [`icon-button`](../icon-button/).** A glyph on nothing, named by `aria-label` and a tooltip, so it draws no fill, border or shadow at rest wherever the listing is placed. In particular, a listing inside a list item (a numbered setup step) does not read as a painted row verb: design-review `COMP-14` judges a painted button against its nearest `li`, and a labelled `button-surface` Copy failed it. After a copy, the glyph swaps to a check and the header says "Copied" for 1.5s; a refused copy says "Copy failed" instead.

## Files

| File | Role |
| --- | --- |
| `code-listing.css` | Visual contract: the frame, the 44px header, the status line, the two-glyph copy control, the mono listing. References design tokens only. |
| `code-listing.js` | `bindCodeListing(root = document)`: one delegated click listener that copies the listing's `<pre>` text and drives the confirmation. |
| `code-listing.html` | Markup skeleton to copy and adapt. |

It needs the vendored [`icon-button/`](../icon-button/) stylesheet and two glyphs from the vendored [`icons/`](../icons/) sprite: `i-copy` and `i-circle-check`.

## How to vendor

1. Copy this `code-listing/` folder **verbatim** into your app's static dir (`app/webapp/static/_vendored/code-listing/`). Do **not** edit the CSS or JS per-app.
2. Link both stylesheets and wire the behaviour once:
   ```html
   <link rel="stylesheet" href="/static/_vendored/icon-button/icon-button.css">
   <link rel="stylesheet" href="/static/_vendored/code-listing/code-listing.css">
   ```
   ```js
   import { bindCodeListing } from '/static/_vendored/code-listing/code-listing.js';
   bindCodeListing();
   ```
3. Paste the skeleton and put your text in the `<pre>`.

## Markup contract

```html
<div class="code-listing">
  <div class="code-listing-head">
    <span class="code-listing-title">PowerShell</span>
    <span class="code-listing-status" role="status" aria-live="polite"></span>
    <button type="button" class="icon-button code-listing-copy" aria-label="Copy" title="Copy">
      <svg class="icon code-listing-copy-glyph" aria-hidden="true"><use href="#i-copy"></use></svg>
      <svg class="icon code-listing-copied" aria-hidden="true"><use href="#i-circle-check"></use></svg>
    </button>
  </div>
  <pre><code>setx ARCHIVE_ROOT D:\archive</code></pre>
</div>
```

- **The frame** is `--card` on a `--line` hairline at `--radius-md`; the header sits on a `--line-muted` hairline. The listing's outer spacing is the app's, not the component's.
- **The header** is `caption` size, 600 weight, `--muted`, at least 44px tall, because the copy control's box is the 44px target floor itself (the `--icon-btn-box` is set to `--hit-min` on the header). The title ellipsizes rather than push the control off the edge.
- **The copy control** is always a `.icon-button.code-listing-copy` with an `aria-label` (and a `title`). Never a labelled `button-surface`, `button-tint` or any other painted tier. Add `data-track="<id>"` to it if the app logs usage.
- **The status line** is a polite live region and empty between copies, so it takes no room. "Copied" is neutral; "Copy failed" takes `--danger-text`, because only an error tints.
- **What is copied** is the `<pre>`'s `textContent`, so markup inside it (a highlighter's spans) never reaches the clipboard.
- **No clipboard** (an insecure page, a denied permission) takes the same path as a rejected write: the header says "Copy failed".

## Required design tokens

Wired to `~/.claude/design.md` (+ `design.dark.md`), never redefined here.

| Token | Used for |
| --- | --- |
| `--card`, `--line`, `--line-muted` | frame surface, frame hairline, header hairline |
| `--muted`, `--ink` | header text, listing text |
| `--accent-text`, `--danger-text` | the copied glyph, the failure line |
| `--radius-md`, `--font-caption`, `--hit-min` | frame radius, header size, target floor |
| `--icon-inline` | glyph size (through `icon-button`) |
