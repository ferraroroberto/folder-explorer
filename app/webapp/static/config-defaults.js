/* What Settings reads from /api/config, with a default for every block, and the plain sentences for a failed load or save.
 *
 * A server older than this page, or a body that is not a config, must not stop Settings or put a script error in a toast.
 * DEFAULTS mirrors src/config.py DEFAULTS; a test holds the two equal.
 */

export const DEFAULTS = {
  remote_access: true,
  port: 8453,
  server: 'hypercorn',
  roots: null,
  schedule: 'off',
  schedule_time: '03:00',
  exclude: [],
  email: { index_path: null, roots: [], exclude: [], download_budget_mb: null },
  folder_search: { skip_levels: 0, prune_people: true, onedrive_prevails: true },
  explore: { show_hidden: false },
  search: { markdown: true },
  docs: { block_remote_html: false },
  desktop: { reuse_explorer_window: false },
  macro: { prefer_window: true },
  tray: { left_click: 'quick_search' },
  quick_search: { open_folder: true },
  archive: { date_prefix: false, max_path_length: 255, max_suggestions: 3, min_score: 0.05 },
  outlook: {
    archive_folder: 'Archive', category: 'Archived by task-os', dismiss_category: 'Dismissed by task-os',
    self_address: null, add_category: false, move_to_archive: false,
  },
  snap: { folder: null, hotkey: 'ctrl+shift+a', jpeg_quality: 90, copy_to_clipboard: true, file_prefix: 'snap', show_loupe: true },
};

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/** The config with every key present: a block that is missing or not an object takes its default, a key inside it too. */
export function configWithDefaults(raw) {
  const cfg = isObject(raw) ? raw : {};
  const out = {};
  for (const [key, fallback] of Object.entries(DEFAULTS)) {
    const value = cfg[key];
    if (isObject(fallback)) out[key] = { ...structuredClone(fallback), ...(isObject(value) ? value : {}) };
    else out[key] = value === undefined ? structuredClone(fallback) : value;
  }
  return out;
}

/** The whole /api/config body as Settings reads it; null when it is not a config body at all. */
export function settingsBody(raw) {
  if (!isObject(raw) || !isObject(raw.config)) return null;
  return {
    config: configWithDefaults(raw.config),
    remote: { configured: true, running: 'off', would_be: 'off', restart_needed: false, password_set: false, ...(isObject(raw.remote) ? raw.remote : {}) },
    archive: { ready: false, reason: 'This server does not report it', ...(isObject(raw.archive) ? raw.archive : {}) },
    index_bytes: typeof raw.index_bytes === 'number' ? raw.index_bytes : 0,
    // Only a server that says the file is missing gets the setup card; an older one says nothing.
    config_file: { exists: !(isObject(raw.config_file) && raw.config_file.exists === false) },
    // Only this PC is told the Markdown association's state; any other device, or an older server, hides the row.
    md_assoc: { state: isObject(raw.md_assoc) && typeof raw.md_assoc.state === 'string' ? raw.md_assoc.state : 'pc_only' },
    created: raw.created === true,
  };
}

/** The sentence for a Settings load that failed, whatever threw: never the script's own message. */
export function loadFailureText(build) {
  return "Settings couldn't be read from the server" + (build ? ' (build ' + build + ')' : '') + '. Reload the page, or restart folder-explorer.';
}

/** The server's own sentence for a refused request (it carries a numeric status), else a plain one. */
export function serverSentence(err) {
  if (err && typeof err.status === 'number' && typeof err.message === 'string' && err.message) return err.message;
  return "The server didn't answer. Reload the page and try again.";
}
