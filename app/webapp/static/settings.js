/* Settings: text size, the Documents search switch, blocking remote content in HTML, default basis, how File Explorer opens, the Markdown association, what a tray click opens, every screenshot setting, remote access, the in-app schedule, and the folder lists for the scan and the email index. Each row of the pane shows its current value; a row with help text or a list opens its own sheet (settingDialog) holding the control, its note and its list. */

import { getJSON, postJSON } from './api.js';
import { DEFAULT_BASIS, readPref, state, writePref, setFeatures } from './state.js';
import { fmtBytes } from './fmt.js';
import { setSwitch, switchEl } from './_vendored/switch/switch.js';
import { showToast } from './_vendored/toast/toast.js';
import { folderList } from './folder-list.js';
import { loadFailureText, serverSentence, settingsBody } from './config-defaults.js';

const $ = (id) => document.getElementById(id);
let remoteSwitch = null;
let reuseSwitch = null;
let markdownSwitch = null;
let remoteHtmlSwitch = null;
let assocSwitch = null;
let categorySwitch = null;
let moveSwitch = null;
let traySwitch = null;
let snapHoldSwitch = null;
let snapClipboardSwitch = null;
let snapLoupeSwitch = null;
let autoSwitch = null;
let onRootsChanged = () => {};
let onSearchChanged = () => {};
// The four folder lists, built once in initSettings.
const lists = {};
// The feature blocks' switches by block key, built once in initSettings.
const FEATURE_KEYS = ['email', 'archive', 'documents', 'screenshot'];
const featureSwitches = {};
const cap = (key) => key[0].toUpperCase() + key.slice(1);
let config = null;
// The last settings body shown, so a refused save puts the server's values back in the fields.
let shown = null;

const REMOTE_TEXT = {
  on: 'On: your devices reach it on the tailnet over HTTPS, after signing in.',
  off: 'Off: this PC only, over plain HTTP, with no sign-in.',
  'no-cert': 'On, but there is no certificate yet, so only this PC can reach it. Run scripts/gen_tailscale_cert.py, then restart.',
};

function render(body) {
  shown = body;
  const cfg = body.config;
  const remote = body.remote;
  $('setupCard').hidden = body.config_file.exists;
  renderFeatures(body.features);
  setFeatures(body.features);
  renderValues(body);
  setSwitch(markdownSwitch, cfg.search.markdown);
  $('markdownSearchNote').textContent = cfg.search.markdown
    ? 'On: Search offers a Documents mode that lists Markdown and HTML files by name.'
    : 'Off: Search looks only in folders and emails, and the Documents mode is hidden.';
  setSwitch(remoteHtmlSwitch, cfg.docs.block_remote_html);
  $('blockRemoteHtmlNote').textContent = cfg.docs.block_remote_html
    ? 'On: an HTML page shows only its own content and the files beside it; nothing loads from the web.'
    : 'Off: an HTML page loads its web images, styles and fonts as a browser would. Either way it runs walled off from the app.';
  setSwitch(reuseSwitch, cfg.desktop.reuse_explorer_window);
  $('reuseExplorerNote').textContent = cfg.desktop.reuse_explorer_window
    ? 'On: Open shows the folder in the File Explorer window you used last, which is faster but replaces what that window showed.'
    : 'Off: each Open starts a new File Explorer window. Turn on for a faster open that reuses the window you used last.';
  renderAssoc(body.md_assoc.state);
  setSwitch(categorySwitch, cfg.outlook.add_category);
  $('archiveCategoryNote').textContent = cfg.outlook.add_category
    ? 'On: the Archive window and sheet offer the category "' + cfg.outlook.category + '" and stamp it on the mail they file.'
    : 'Off: the Archive window and sheet show no category row, and filing stamps none on the mail.';
  setSwitch(moveSwitch, cfg.outlook.move_to_archive);
  $('archiveMoveNote').textContent = cfg.outlook.move_to_archive
    ? 'On: once the mail is saved, the Archive window and sheet also move it to the Outlook folder "' + cfg.outlook.archive_folder + '". A move Outlook refuses is reported and the saved file stays.'
    : 'Off: filing saves the mail to disk and leaves it where it is in Outlook.';
  const quick = cfg.tray.left_click === 'quick_search';
  setSwitch(traySwitch, quick);
  $('trayClickNote').textContent = quick
    ? 'On: a left click on the tray icon opens the small Quick search window for folders and email. Open in the tray menu still opens this app.'
    : 'Off: a left click on the tray icon opens this app. Quick search stays in the tray menu.';
  setSwitch(snapHoldSwitch, cfg.snap.hold_to_capture);
  $('snapHoldNote').textContent = cfg.snap.hold_to_capture
    ? 'On: the screenshot selects by pressing, dragging and releasing the left button.'
    : 'Off: the screenshot selects with a click to start, a move, and a click to finish; no button is held. Applies from the next capture.';
  renderSnap(cfg.snap, body.snap_folder);
  setSwitch(remoteSwitch, cfg.remote_access);
  remoteSwitch.setAttribute('aria-label', 'Remote access');
  let note = REMOTE_TEXT[remote.running] || '';
  if (remote.restart_needed) note += ' Changed: restart the app to apply it (tray icon, Restart web app).';
  if (cfg.remote_access && !remote.password_set) note += ' No password is set yet: run python -m src.password set and scripts/gen_token.py on this PC.';
  $('remoteNote').textContent = note;

  $('scheduleSelect').value = cfg.schedule;
  $('scheduleTime').value = cfg.schedule_time;
  $('scheduleTimeRow').hidden = cfg.schedule !== 'nightly';
  $('scheduleNote').textContent = cfg.schedule === 'off'
    ? 'Off: scans run from Rescan, the tray menu or the command line.'
    : (cfg.schedule === 'nightly' ? 'Every night at ' + cfg.schedule_time : 'Every hour') + ', while the app is running.';

  config = cfg;
  const auto = cfg.roots === null;
  setSwitch(autoSwitch, auto);
  autoSwitch.setAttribute('aria-label', 'All drives and OneDrive');
  $('rootsEditor').hidden = auto;
  if (!auto) lists.roots.render(cfg.roots);
  $('rootsNote').textContent = auto ? 'Scanning ' + state.roots.map((r) => r.name).join(', ') + '.' : '';
  lists.exclude.render(cfg.exclude);
  lists.emailRoots.render(cfg.email.roots);
  lists.emailExclude.render(cfg.email.exclude);
  renderBudget(cfg.email.download_budget_mb);
  $('indexSize').textContent = fmtBytes(body.index_bytes);
  $('outlookNote').textContent = body.archive.ready
    ? 'Ready: the Archive button in Outlook files the open mail through this app.'
    : body.archive.reason + '. The macro text is still here to copy.';
  setBasis(readPref('basis', DEFAULT_BASIS));
}

/** The Available features rows: each block's switch, what it covers, and why it can't run on this machine. */
function renderFeatures(list) {
  for (const key of FEATURE_KEYS) {
    const info = list.find((b) => b.key === key);
    if (!info) continue;
    setSwitch(featureSwitches[key], info.enabled);
    $('featHint' + cap(key)).textContent = info.covers + (info.restart ? ' A change applies when ' + info.restart + ' next starts.' : '');
    $('featWhy' + cap(key)).textContent = info.available ? '' : 'Not available here: ' + info.reason + '.';
  }
}

const onOff = (on) => (on ? 'On' : 'Off');
const folderCount = (list) => (list.length ? list.length + (list.length === 1 ? ' folder' : ' folders') : 'None');
const SCHEDULE_VALUE = { off: 'Off', nightly: 'Every night', hourly: 'Every hour' };
const ASSOC_VALUE = { on: 'On', off: 'Off', partial: 'Partly set', unknown: 'Unknown' };
const BUDGET_VALUE = { '': 'No limit', 0: 'Local only' };

/** The value each sheet row shows beside its chevron, so the pane reads without opening a sheet. */
function renderValues(body) {
  const cfg = body.config;
  const set = (id, text) => { $(id).textContent = text; $(id).title = text; };
  set('valSchedule', cfg.schedule === 'nightly' ? 'Nightly, ' + cfg.schedule_time : (SCHEDULE_VALUE[cfg.schedule] || cfg.schedule));
  set('valRoots', cfg.roots === null ? 'All drives' : folderCount(cfg.roots));
  set('valExclude', folderCount(cfg.exclude));
  set('valEmailRoots', folderCount(cfg.email.roots));
  set('valEmailExclude', folderCount(cfg.email.exclude));
  const mb = cfg.email.download_budget_mb;
  set('valBudget', BUDGET_VALUE[budgetValue(mb)] || (mb >= 1024 ? mb / 1024 + ' GB' : mb + ' MB'));
  const archiving = [cfg.outlook.add_category && 'Category', cfg.outlook.move_to_archive && 'Move'].filter(Boolean);
  set('valOutlook', archiving.length ? archiving.join(' + ') : 'Off');
  set('valMacro', body.archive.ready ? 'Ready' : 'Not ready');
  set('valMarkdownSearch', onOff(cfg.search.markdown));
  set('valRemoteHtml', cfg.docs.block_remote_html ? 'Blocked' : 'Allowed');
  set('valExplorerWindow', cfg.desktop.reuse_explorer_window ? 'Reused' : 'New window');
  set('valTrayClick', cfg.tray.left_click === 'quick_search' ? 'Quick search' : 'Open app');
  set('valScreenshot', cfg.snap.hotkey);
  set('valRemote', onOff(cfg.remote_access));
}

/** The screenshot rows: each shows what the tray's Screenshot menu holds, read from the same config.json block. */
function renderSnap(snap, folder) {
  setSwitch(snapClipboardSwitch, snap.copy_to_clipboard);
  $('snapClipboardNote').textContent = snap.copy_to_clipboard
    ? 'On: a saved screenshot is also put on the clipboard, ready to paste.'
    : 'Off: a screenshot is only saved as a file.';
  setSwitch(snapLoupeSwitch, snap.show_loupe);
  $('snapLoupeNote').textContent = snap.show_loupe
    ? 'On: a magnifier follows the cursor while you select. F6 shows or hides it.'
    : 'Off: no magnifier while you select. F6 shows it again.';
  const quality = $('snapQuality');
  // A quality set in config.json to a value the menu doesn't offer still shows as itself.
  if (![...quality.options].some((o) => o.value === String(snap.jpeg_quality))) quality.add(new Option(String(snap.jpeg_quality), String(snap.jpeg_quality)));
  quality.value = String(snap.jpeg_quality);
  $('snapQualityNote').textContent = 'Higher keeps more detail and makes a bigger file. Applies from the next capture.';
  $('snapPrefix').value = snap.file_prefix;
  $('snapPrefixNote').textContent = 'Every file name starts with it, as ' + snap.file_prefix + '_date_time.jpg. Applies from the next capture.';
  $('snapHotkey').value = snap.hotkey;
  $('snapHotkeyNote').textContent = 'Ctrl, Shift, Alt or Win, then a letter, a digit, F1 to F24, PrintScreen or Space. Applies the next time the tray starts; if another app already owns the combo, the Screenshot menu in the tray says it is not running.';
  $('snapFolderPath').textContent = folder.path;
  $('snapOpenFolder').hidden = !folder.can_open;
  $('snapFolderNote').textContent = 'Where screenshots are saved. It is changed from the tray menu or config.json on this PC, never from this page.';
}

async function openSnapFolder() {
  try {
    await postJSON('/api/snap/open-folder', {});
  } catch (e) {
    showToast('Could not open the folder: ' + serverSentence(e), 'error');
  }
}

/** Save one screenshot setting; a refusal shows the validator's sentence and puts the saved value back. */
async function saveSnap(change) {
  const body = await save({ snap: change });
  if (!body && shown) renderSnap(shown.config.snap, shown.snap_folder);
  return body;
}

const ASSOC_TEXT = {
  on: 'On: Markdown files list folder-explorer under Open with, and File Explorer\'s menu has Open in Folder Explorer. Windows may ask once which app opens them by default.',
  off: 'Off: Markdown files open in the app Windows uses for them. Turning it on changes only your user account; nothing needs an administrator.',
  partial: 'Partly set, or set for another copy of this app. Turn it on to set it for this one, or off to remove it.',
  unknown: 'The association could not be read on this PC, so its state is unknown.',
};

/** The Markdown association row: shown on this PC only, with the state the registry gave back. */
function renderAssoc(assocState) {
  $('mdAssocRow').hidden = assocState === 'pc_only';
  $('valMdAssoc').textContent = ASSOC_VALUE[assocState] || ASSOC_VALUE.unknown;
  setSwitch(assocSwitch, assocState === 'on');
  assocSwitch.disabled = assocState === 'unknown';
  $('mdAssocNote').textContent = ASSOC_TEXT[assocState] || ASSOC_TEXT.unknown;
}

async function saveAssoc(on) {
  try {
    const body = await postJSON('/api/md-assoc', { on });
    renderAssoc(typeof body.state === 'string' ? body.state : 'unknown');
    showToast(on ? 'Markdown files associated with this app' : 'Markdown association removed');
  } catch (e) {
    showToast('Could not change it: ' + serverSentence(e), 'error');
  }
}

// The menu value for a budget: '' is no limit (null in config.json), else the MB as text.
const budgetValue = (mb) => (mb === null ? '' : String(mb));

function renderBudget(mb) {
  const sel = $('emailBudget');
  // A budget set in config.json to a value the menu doesn't offer still shows as itself.
  if (![...sel.options].some((o) => o.value === budgetValue(mb))) sel.add(new Option(mb + ' MB', String(mb)));
  sel.value = budgetValue(mb);
  $('emailBudgetNote').textContent = mb === null ? 'One run downloads all the online-only mail it needs to index.'
    : mb ? 'Each email run downloads up to this much online-only mail; the rest waits for the next run. Choose No limit to index everything in one run.'
    : 'Online-only mail is left in the cloud. Choose No limit to index everything in one run.';
}

/** Save one setting from a folder list: null when saved, else the server's sentence. */
async function saveList(update, okText, after) {
  try {
    render(await postConfig(update));
  } catch (e) {
    return serverSentence(e);
  }
  showToast(okText);
  if (after) after();
  return null;
}

const withEmail = (change) => ({ email: { ...config.email, ...change } });

function setBasis(value) {
  $('defaultBasisSeg').querySelectorAll('.segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === value)));
}

/** POST a change (or the first-run create) and read the answer as a settings body, or throw. */
async function postConfig(update, path = '/api/config') {
  const body = settingsBody(await postJSON(path, update));
  if (!body) throw new Error('not a settings body');
  return body;
}

async function save(update) {
  try {
    const body = await postConfig(update);
    render(body);
    return body;
  } catch (e) {
    showToast('Could not save: ' + serverSentence(e), 'error');
    return null;
  }
}

/** The first-run card: create config.json from config.example.json; the server never overwrites a file that exists. */
async function createConfig() {
  try {
    const body = await postConfig({}, '/api/config/create');
    render(body);
    showToast(body.created ? 'config.json created' : 'config.json already exists, left as it is');
  } catch (e) {
    showToast('Could not create config.json: ' + serverSentence(e), 'error');
  }
}

/** The four folder lists: what the scan covers and skips, and the email folders and what their index leaves out. */
function buildFolderLists() {
  lists.roots = folderList($('rootsEditor'), {
    name: 'roots', label: 'Add a folder to scan', empty: 'No folders yet.',
    onSave: (roots) => saveList({ roots }, 'Folders to scan saved', onRootsChanged),
  });
  lists.exclude = folderList($('excludeEditor'), {
    name: 'exclude', label: 'Add a folder to skip', empty: 'Nothing is skipped.',
    onSave: (exclude) => saveList({ exclude }, 'Saved: the next scan leaves them out'),
  });
  lists.emailRoots = folderList($('emailRootsEditor'), {
    name: 'email-roots', label: 'Add an email folder', empty: 'No email folders yet.',
    onSave: (roots) => saveList(withEmail({ roots }), 'Email folders saved'),
  });
  lists.emailExclude = folderList($('emailExcludeEditor'), {
    name: 'email-exclude', label: 'Add a folder to leave out of the email index', empty: 'Nothing is left out.',
    onSave: (exclude) => saveList(withEmail({ exclude }), 'Saved: the next email run leaves them out'),
  });
}

/** The screenshot sheet's three switches, its menus and fields, and Open folder. */
function buildSnapControls() {
  snapHoldSwitch = switchEl(false, {
    label: 'Hold to capture',
    onToggle: (next) => saveSnap({ hold_to_capture: next }),
  });
  $('snapHoldSlot').appendChild(snapHoldSwitch);
  snapClipboardSwitch = switchEl(true, {
    label: 'Copy to clipboard',
    onToggle: (next) => saveSnap({ copy_to_clipboard: next }),
  });
  $('snapClipboardSlot').appendChild(snapClipboardSwitch);
  snapLoupeSwitch = switchEl(true, {
    label: 'Show loupe',
    onToggle: (next) => saveSnap({ show_loupe: next }),
  });
  $('snapLoupeSlot').appendChild(snapLoupeSwitch);
  $('snapQuality').addEventListener('change', (e) => saveSnap({ jpeg_quality: Number(e.target.value) }));
  $('snapPrefix').addEventListener('change', (e) => saveSnap({ file_prefix: e.target.value }));
  $('snapHotkey').addEventListener('change', (e) => saveSnap({ hotkey: e.target.value }));
  $('snapOpenFolder').addEventListener('click', openSnapFolder);
}

/** One switch per feature block: a saved change re-reads Search, whose modes follow the Email and Documents blocks. */
function buildFeatureSwitches() {
  for (const key of FEATURE_KEYS) {
    featureSwitches[key] = switchEl(true, {
      label: cap(key) + ' feature',
      onToggle: async (next) => { if (await save({ features: { [key]: next } })) onSearchChanged(); },
    });
    $('featSlot' + cap(key)).appendChild(featureSwitches[key]);
  }
}

export function initSettings(opts) {
  onRootsChanged = opts.onRootsChanged;
  onSearchChanged = opts.onSearchChanged;
  buildFeatureSwitches();
  markdownSwitch = switchEl(true, {
    label: 'Documents search',
    onToggle: async (next) => { if (await save({ search: { markdown: next } })) onSearchChanged(); },
  });
  $('markdownSearchSlot').appendChild(markdownSwitch);
  remoteHtmlSwitch = switchEl(false, {
    label: 'Block remote content',
    onToggle: (next) => save({ docs: { block_remote_html: next } }),
  });
  $('blockRemoteHtmlSlot').appendChild(remoteHtmlSwitch);
  reuseSwitch = switchEl(false, {
    label: 'Reuse an open window',
    onToggle: (next) => save({ desktop: { reuse_explorer_window: next } }),
  });
  $('reuseExplorerSlot').appendChild(reuseSwitch);
  assocSwitch = switchEl(false, { label: 'Open with this app', onToggle: saveAssoc });
  $('mdAssocSlot').appendChild(assocSwitch);
  categorySwitch = switchEl(false, {
    label: 'Outlook category',
    onToggle: (next) => save({ outlook: { add_category: next } }),
  });
  $('archiveCategorySlot').appendChild(categorySwitch);
  moveSwitch = switchEl(false, {
    label: 'Also archive in Outlook',
    onToggle: (next) => save({ outlook: { move_to_archive: next } }),
  });
  $('archiveMoveSlot').appendChild(moveSwitch);
  traySwitch = switchEl(true, {
    label: 'Opens Quick search',
    onToggle: async (next) => {
      if (await save({ tray: { left_click: next ? 'quick_search' : 'open_app' } })) {
        showToast(next ? 'Tray click opens Quick search' : 'Tray click opens the app');
      }
    },
  });
  $('trayClickSlot').appendChild(traySwitch);
  buildSnapControls();
  $('createConfig').addEventListener('click', createConfig);
  $('settingsDialog').querySelectorAll('[data-open]').forEach((row) => row.addEventListener('click', () => openSetting(row.dataset.open)));
  remoteSwitch = switchEl(false, { label: 'Remote access', onToggle: (next) => save({ remote_access: next }) });
  $('remoteSwitchSlot').appendChild(remoteSwitch);
  autoSwitch = switchEl(true, {
    label: 'All drives and OneDrive',
    onToggle: async (next) => {
      if (next) {
        if (await save({ roots: null })) onRootsChanged();
      } else {
        // Start the explicit list from what is scanned now; nothing is saved until it changes.
        setSwitch(autoSwitch, false);
        $('rootsEditor').hidden = false;
        $('rootsNote').textContent = '';
        lists.roots.render(state.roots.map((r) => r.path));
        $('rootsEditor').querySelector('input').focus();
      }
    },
  });
  $('rootsAutoSlot').appendChild(autoSwitch);
  buildFolderLists();
  $('emailBudget').addEventListener('change', (e) => save(withEmail({ download_budget_mb: e.target.value === '' ? null : Number(e.target.value) })));
  $('scheduleSelect').addEventListener('change', (e) => save({ schedule: e.target.value }));
  $('scheduleTime').addEventListener('change', (e) => { if (e.target.value) save({ schedule_time: e.target.value }); });
  $('defaultBasisSeg').addEventListener('click', (e) => {
    const b = e.target.closest('.segmented-item');
    if (!b) return;
    writePref('basis', b.dataset.value);
    setBasis(b.dataset.value);
  });
}

/** Open the sheet for one setting: its control, its note and any folder list, over the Settings sheet. */
function openSetting(name) {
  const dialog = $('settingDialog');
  dialog.querySelectorAll('[data-setting]').forEach((section) => {
    const shown = section.dataset.setting === name;
    section.hidden = !shown;
    if (shown) $('settingTitle').textContent = section.dataset.title;
  });
  dialog.showModal();
}

/** The server's build for the load-failure sentence, '' when it can't say. */
async function serverBuild() {
  try {
    return (await getJSON('/api/version')).git_sha || '';
  } catch (e) {
    return '';
  }
}

export async function openSettings() {
  const d = $('settingsDialog');
  try {
    const body = settingsBody(await getJSON('/api/config'));
    if (!body) throw new Error('not a settings body');
    render(body);
  } catch (e) {
    console.warn('Settings could not be loaded', e);
    showToast(loadFailureText(await serverBuild()), 'error');
    return;
  }
  if (!d.open) d.showModal();
}
