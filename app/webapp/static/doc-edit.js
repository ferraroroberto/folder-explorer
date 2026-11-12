/* The Markdown editor, shared by Explore's document sheet and the /doc window.
 *
 * A wide window with a mouse shows the source beside a live preview that
 * follows the cursor; a phone toggles Write | Preview and gets a row of
 * Markdown keys above its keyboard. The preview posts the draft to the
 * server's renderer, so it reads exactly as View will. Saving is explicit
 * (Save or Ctrl+S): it sends the version the editor opened, and a file that
 * changed on disk since then stops at the conflict screen instead of being
 * overwritten. Until it is saved or discarded, a draft stays on this device.
 */

import { api } from './api.js';
import { esc, glyph } from './fmt.js';
import { APP } from './state.js';
import { askLeave, confirmSheet } from './doc-confirm.js';
import { hideConflict, showConflict } from './doc-conflict.js';
import { drawDoc, releaseDoc, stateHtml } from './doc-view.js';
import { addEditorIcons } from './doc-icons.js';
import { caretLine, formatEdit, highlightSource } from './md-source.js';
import { showToast } from './_vendored/toast/toast.js';

// How long typing pauses before the preview renders again and before the draft is kept on the device.
const PREVIEW_MS = 300;
const DRAFT_MS = 400;
// The source sits beside its preview only where there is room and a mouse; elsewhere it is Write | Preview.
export const SPLIT = window.matchMedia('(min-width: 900px) and (pointer: fine)');

const FORMATS = [
  ['heading', 'Heading', 'heading'], ['bold', 'Bold', 'bold'], ['italic', 'Italic', 'italic'], ['code', 'Code', 'code'],
  ['link', 'Link', 'link'], ['list', 'Bulleted list', 'list'], ['task', 'Task', 'list-checks'], ['quote', 'Quote', 'text-quote'],
];
// Six keys plus link and indent: eight across a phone keep each key over the 44px touch floor.
const KEYS = ['#', '*', '_', '`', '-', '>'];

const draftKey = (loc) => APP + '.draft:' + loc.root + ':' + loc.scan_id + ':' + loc.id;

function readDraft(loc) {
  try { return JSON.parse(localStorage.getItem(draftKey(loc)) || 'null'); } catch (e) { return null; }
}

function writeDraft(loc, draft) {
  try {
    if (draft) localStorage.setItem(draftKey(loc), JSON.stringify(draft));
    else localStorage.removeItem(draftKey(loc));
  } catch (e) { /* storage is off: the draft lives only in this page until it is saved */ }
}

const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

async function json(res) {
  try { return await res.json(); } catch (e) { return null; }
}

function shell() {
  const formats = FORMATS.map(([k, label, icon]) => '<button type="button" class="icon-button" data-format="' + k + '" aria-label="' + label +
    '" title="' + label + '" data-track="doc-format-' + k + '">' + glyph(icon) + '</button>').join('');
  const keys = KEYS.map((k) => '<button type="button" class="doc-key" data-key="' + esc(k) + '" data-track="doc-key">' + esc(k) + '</button>').join('');
  return '<div class="doc-editor" data-pane="write"><div class="doc-edit-tools">' +
    '<div class="segmented doc-edit-panes" role="group" aria-label="Editor view">' +
    '<button type="button" class="segmented-item" aria-pressed="true" data-pane="write" data-track="doc-edit-write">Write</button>' +
    '<button type="button" class="segmented-item" aria-pressed="false" data-pane="preview" data-track="doc-edit-preview">Preview</button></div>' +
    '<div class="icon-button-group doc-format" role="toolbar" aria-label="Format">' + formats + '</div>' +
    '<span class="doc-edit-status" role="status" aria-live="polite"></span>' +
    '<button type="button" class="button-ghost doc-discard" data-track="doc-discard" hidden>Discard</button></div>' +
    '<div class="doc-conflict-slot" hidden></div><div class="doc-edit-split">' +
    '<div class="doc-src"><div class="doc-src-stack"><pre class="doc-src-hl" aria-hidden="true"></pre>' +
    '<textarea class="doc-src-input" aria-label="Markdown source" spellcheck="false" data-track="doc-edit-source"></textarea></div></div>' +
    '<div class="doc-edit-preview" aria-label="Preview"></div></div>' +
    '<div class="doc-keyrow" role="toolbar" aria-label="Markdown keys">' + keys +
    '<button type="button" class="doc-key" data-format="link" aria-label="Link" data-track="doc-key-link">[ ]</button>' +
    '<button type="button" class="doc-key" data-key="tab" aria-label="Indent" data-track="doc-key-indent">⇥</button></div></div>';
}

/** One open editor: its file, the copy on disk, the version a save is checked against, and its elements. */
class Editor {
  constructor(mount, loc, hooks) {
    this.mount = mount;
    this.loc = loc;
    this.hooks = hooks;
    this.where = { root: loc.root, scan_id: loc.scan_id, id: loc.id };
    this.disk = null;
    this.version = '';
    this.ready = false;
    this.saving = false;
    this.closed = false;
    this.seq = 0;
    this.timers = { draft: 0, preview: 0, frame: 0 };
    this.el = {};
    this.placeKeys = this.placeKeys.bind(this);
  }

  dirty() { return this.ready && this.el.ta.value !== this.disk.text; }

  status(text) { if (this.el.status) this.el.status.textContent = text; }

  keepDraft() {
    if (this.ready) writeDraft(this.loc, this.dirty() ? { text: this.el.ta.value, version: this.version, at: Date.now() } : null);
  }

  changed() {
    const dirty = this.dirty();
    this.el.discard.hidden = !dirty;
    this.hooks.onDirty(dirty);
    clearTimeout(this.timers.draft);
    this.timers.draft = setTimeout(() => this.keepDraft(), DRAFT_MS);
  }

  paint() {
    cancelAnimationFrame(this.timers.frame);
    this.timers.frame = requestAnimationFrame(() => { this.el.hl.innerHTML = highlightSource(this.el.ta.value); });
  }

  /** Scroll the preview to the block the cursor is in, a third of the way down. */
  follow() {
    const { ta, preview } = this.el;
    const line = caretLine(ta.value, ta.selectionStart);
    let target = null;
    for (const block of preview.querySelectorAll('[data-line]')) {
      if (Number(block.dataset.line) > line) break;
      target = block;
    }
    preview.scrollTop = target ? Math.max(0, target.offsetTop - preview.clientHeight / 3) : 0;
  }

  async renderPreview() {
    const mine = ++this.seq;
    let res;
    try { res = await post('/api/doc/render', { ...this.where, text: this.el.ta.value }); } catch (e) { return; }
    const body = await json(res);
    if (mine !== this.seq || this.closed) return;
    if (!res.ok) {
      this.el.preview.innerHTML = stateHtml({ reason: (body && body.reason) || 'unreadable', message: (body && body.detail) || 'No preview' });
      return;
    }
    drawDoc(this.el.preview, { ...body, root: this.loc.root, scan_id: this.loc.scan_id }, true);
    this.follow();
  }

  schedulePreview() {
    clearTimeout(this.timers.preview);
    this.timers.preview = setTimeout(() => this.renderPreview(), PREVIEW_MS);
  }

  setText(text, version) {
    this.el.ta.value = text;
    this.version = version;
    hideConflict(this.el.slot);
    this.paint();
    this.schedulePreview();
    this.changed();
  }

  /** Apply one edit, through the browser's own undo where the insertText command still exists. */
  replace(edit) {
    const { ta } = this.el;
    ta.focus();
    ta.setSelectionRange(edit.from, edit.to);
    if (!document.execCommand('insertText', false, edit.insert)) {
      ta.setRangeText(edit.insert, edit.from, edit.to, 'end');
      ta.dispatchEvent(new Event('input'));
    }
    ta.setSelectionRange(edit.selStart, edit.selEnd);
  }

  conflict(body) {
    this.status('');
    showConflict(this.el.slot, {
      theirs: typeof body.text === 'string' ? body.text : null,
      mine: () => this.el.ta.value,
      onLoadTheirs: () => {
        this.disk = { text: body.text, version: body.version };
        this.setText(body.text, body.version);
        this.status('Showing the copy on disk');
      },
      onOverwrite: () => { this.version = body.version; this.save(true); },
    });
  }

  async save(overwrite) {
    if (!this.ready || this.saving) return false;
    if (!overwrite && !this.dirty()) { this.status('No changes to save'); return true; }
    this.saving = true;
    this.status('Saving…');
    const text = this.el.ta.value;
    let res = null;
    try {
      res = await post('/api/doc/save', { ...this.where, text, version: this.version, overwrite: !!overwrite });
    } catch (e) { res = null; }
    const body = res ? await json(res) : null;
    this.saving = false;
    if (res && res.ok) {
      this.disk = { text, version: body.version };
      this.version = body.version;
      hideConflict(this.el.slot);
      writeDraft(this.loc, null);
      this.changed();
      this.status('Saved');
      showToast('Saved');
      return true;
    }
    if (body && body.reason === 'conflict') { this.conflict(body); return false; }
    this.status('');
    showToast(res ? (body && body.detail) || 'Could not save' : 'The app did not answer. Your draft is kept on this device.', 'error');
    return false;
  }

  dropChanges() {
    writeDraft(this.loc, null);
    if (this.ready) this.setText(this.disk.text, this.disk.version);
    this.status('');
  }

  async discard() {
    const ok = await confirmSheet({
      title: 'Discard your changes?', text: 'The editor goes back to the copy on disk.', view: 'doc-discard',
      actions: [{ value: 'discard', label: 'Discard', kind: 'danger' }],
    });
    if (ok === 'discard') this.dropChanges();
  }

  /** Keep the key row just above the phone's keyboard: the visual viewport ends where the keyboard starts. */
  placeKeys() {
    const vv = window.visualViewport;
    if (vv) this.el.root.style.setProperty('--kb', Math.max(0, window.innerHeight - vv.height - vv.offsetTop) + 'px');
  }

  showPane(button) {
    this.el.root.dataset.pane = button.dataset.pane;
    this.el.root.querySelectorAll('.doc-edit-panes .segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    if (button.dataset.pane === 'preview') this.renderPreview();
  }

  /** A link in the preview is the draft's: an in-page anchor scrolls the preview, a link to another document waits for View. */
  previewLink(e, a) {
    e.preventDefault();
    e.stopPropagation();
    const href = a.getAttribute('href');
    const h = href.startsWith('#') && this.el.preview.querySelector('[id="' + CSS.escape(decodeURIComponent(href.slice(1))) + '"]');
    if (h) this.el.preview.scrollTop = h.offsetTop;
    else this.status('Links open from View');
  }

  onClick(e) {
    const pane = e.target.closest('.doc-edit-panes .segmented-item');
    if (pane) { this.showPane(pane); return; }
    const { ta } = this.el;
    const f = e.target.closest('[data-format]');
    if (f) { this.replace(formatEdit(ta.value, ta.selectionStart, ta.selectionEnd, f.dataset.format)); return; }
    const k = e.target.closest('[data-key]');
    if (k) {
      const s = ta.selectionStart;
      const insert = k.dataset.key === 'tab' ? '  ' : k.dataset.key;
      this.replace({ from: s, to: ta.selectionEnd, insert, selStart: s + insert.length, selEnd: s + insert.length });
      return;
    }
    if (e.target.closest('.doc-discard')) { this.discard(); return; }
    const a = e.target.closest('.doc-edit-preview a[href]');
    if (a && !a.target) this.previewLink(e, a);
  }

  wire() {
    const { ta, root } = this.el;
    ta.addEventListener('input', () => { this.paint(); this.schedulePreview(); this.changed(); });
    ['click', 'keyup'].forEach((t) => ta.addEventListener(t, () => this.follow()));
    ta.addEventListener('focus', () => { root.classList.add('is-typing'); this.placeKeys(); });
    ta.addEventListener('blur', () => root.classList.remove('is-typing'));
    root.addEventListener('click', (e) => this.onClick(e), true);
    // A tap on a key or format button must leave the focus (and the phone's keyboard) in the text.
    root.addEventListener('pointerdown', (e) => { if (e.target.closest('[data-key], [data-format]')) e.preventDefault(); });
    root.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); this.save(false); }
    });
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', this.placeKeys);
      window.visualViewport.addEventListener('scroll', this.placeKeys);
    }
  }

  async fetchSource() {
    const query = new URLSearchParams({ root: this.loc.root, scan: String(this.loc.scan_id), id: String(this.loc.id) });
    try {
      const res = await api('/api/doc/source?' + query);
      return { res, body: await json(res) };
    } catch (e) {
      return { res: null, body: null };
    }
  }

  async load() {
    this.mount.innerHTML = '<p class="doc-loading" role="status">Loading the source…</p>';
    const { res, body } = await this.fetchSource();
    if (this.closed) return;
    if (!res || !res.ok || !body || !body.editable) {
      this.mount.innerHTML = stateHtml({ reason: (body && body.reason) || 'unreadable', message: (body && body.detail) || 'The app did not answer' });
      this.hooks.onReady(false);
      return;
    }
    addEditorIcons();
    this.mount.innerHTML = shell();
    const q = (sel) => this.mount.querySelector(sel);
    this.el = { root: q('.doc-editor'), ta: q('.doc-src-input'), hl: q('.doc-src-hl'), preview: q('.doc-edit-preview'),
      status: q('.doc-edit-status'), slot: q('.doc-conflict-slot'), discard: q('.doc-discard') };
    this.disk = { text: body.text, version: body.version };
    this.ready = true;
    const draft = readDraft(this.loc);
    const restored = draft && typeof draft.text === 'string' && draft.text !== body.text;
    // A draft made against an older copy keeps that copy's version, so its Save stops at the conflict screen.
    this.setText(restored ? draft.text : body.text, restored ? draft.version : body.version);
    if (restored) this.status('Restored your unsaved draft');
    this.wire();
    this.el.ta.setSelectionRange(0, 0);
    if (SPLIT.matches) this.el.ta.focus();
    this.hooks.onReady(true);
  }

  /** Whether the host may go: asks first when there are unsaved changes (Save, Keep draft, Discard, or stay). */
  async leave() {
    if (!this.dirty()) return true;
    const choice = await askLeave();
    if (choice === 'save') return this.save(false);
    if (choice === 'discard') this.dropChanges();
    if (choice === 'keep') this.keepDraft();
    return choice !== null;
  }

  close() {
    if (this.closed) return;
    this.keepDraft();
    this.closed = true;
    clearTimeout(this.timers.draft);
    clearTimeout(this.timers.preview);
    cancelAnimationFrame(this.timers.frame);
    if (window.visualViewport) {
      window.visualViewport.removeEventListener('resize', this.placeKeys);
      window.visualViewport.removeEventListener('scroll', this.placeKeys);
    }
    if (this.el.preview) releaseDoc(this.el.preview);
    this.mount.innerHTML = '';
  }
}

/**
 * Open the editor for ``loc`` (``{root, scan_id, id}``) in ``mount``.
 * ``hooks.onDirty(dirty)`` follows unsaved changes; ``hooks.onReady(editable)``
 * says whether the file opened for editing. The host calls ``save()``,
 * ``dirty()``, ``leave()`` (resolves true when it may go), ``keepDraft()``
 * (before the page unloads) and ``close()``.
 */
export function openEditor(mount, loc, hooks) {
  const editor = new Editor(mount, loc, hooks);
  editor.load();
  return editor;
}
