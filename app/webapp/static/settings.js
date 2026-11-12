/* Settings: text size, default basis, how File Explorer opens, the Markdown association, what a tray click opens, remote access, the in-app schedule, and the folder lists for the scan and the email index. */

import { getJSON, postJSON } from './api.js';
import { readPref, state, writePref } from './state.js';
import { fmtBytes } from './fmt.js';
import { setSwitch, switchEl } from './_vendored/switch/switch.js';
import { showToast } from './_vendored/toast/toast.js';
import { folderList } from './folder-list.js';
import { loadFailureText, serverSentence, settingsBody } from './config-defaults.js';

const $ = (id) => document.getElementById(id);
let remoteSwitch = null;
let reuseSwitch = null;
let assocSwitch = null;
let categorySwitch = null;
let moveSwitch = null;
let traySwitch = null;
let autoSwitch = null;
let onRootsChanged = () => {};
// The four folder lists, built once in initSettings.
const lists = {};
let config = null;

const REMOTE_TEXT = {
  on: 'On: your devices reach it on the tailnet over HTTPS, after signing in.',
  off: 'Off: this PC only, over plain HTTP, with no sign-in.',
  'no-cert': 'On, but there is no certificate yet, so only this PC can reach it. Run scripts/gen_tailscale_cert.py, then restart.',
};

function render(body) {
  const cfg = body.config;
  const remote = body.remote;
  $('setupCard').hidden = body.config_file.exists;
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
  setSwitch(remoteSwitch, cfg.remote_access);
  remoteSwitch.setAttribute('aria-label', 'Remote access');
  let note = REMOTE_TEXT[remote.running] || '';
  if (remote.restart_needed) note += ' Changed: restart the app to apply it (tray icon, Restart web app).';
  if (cfg.remote_access && !remote.password_set) note += ' No password is set yet: run scripts/set_password.py on this PC.';
  $('remoteNote').textContent = note;

  $('scheduleSelect').value = cfg.schedule;
  $('scheduleTime').value = cfg.schedule_time;
  $('scheduleTime').hidden = cfg.schedule !== 'nightly';
  $('scheduleNote').textContent = cfg.schedule === 'off'
    ? 'Off: scans run from Rescan, the tray menu or the command line.'
    : (cfg.schedule === 'nightly' ? 'Every night at ' + cfg.schedule_time : 'Every hour') + ', while the app is running.';

  config = cfg;
  const auto = cfg.roots === null;
  setSwitch(autoSwitch, auto);
  autoSwitch.setAttribute('aria-label', 'Every local drive and OneDrive');
  $('rootsEditor').hidden = auto;
  if (!auto) lists.roots.render(cfg.roots);
  $('rootsNote').textContent = auto ? 'Scanning ' + state.roots.map((r) => r.name).join(', ') + '.' : '';
  lists.exclude.render(cfg.exclude);
  lists.emailRoots.render(cfg.email.roots);
  lists.emailExclude.render(cfg.email.exclude);
  renderBudget(cfg.email.download_budget_mb);
  $('indexSize').textContent = fmtBytes(body.index_bytes) + ', kept on this PC';
  $('outlookNote').textContent = body.archive.ready
    ? 'Ready: the Archive button in Outlook files the open mail through this app.'
    : body.archive.reason + '. The macro text is still here to copy.';
  setBasis(readPref('basis', 'size'));
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

export function initSettings(opts) {
  onRootsChanged = opts.onRootsChanged;
  reuseSwitch = switchEl(false, {
    label: 'Reuse an open Explorer window',
    onToggle: (next) => save({ desktop: { reuse_explorer_window: next } }),
  });
  $('reuseExplorerSlot').appendChild(reuseSwitch);
  // Built here, not in index.html: the row exists only for a server that reports this PC's association.
  const assocRow = document.createElement('div');
  assocRow.className = 'row row-stack';
  assocRow.id = 'mdAssocRow';
  assocRow.hidden = true;
  assocRow.innerHTML = '<div class="row-line"><span>Associate Markdown files with this app</span>' +
    '<span class="switch-slot" id="mdAssocSlot" data-track="md-assoc-switch"></span></div><p class="row-note" id="mdAssocNote"></p>';
  $('reuseExplorerSlot').closest('.row').after(assocRow);
  assocSwitch = switchEl(false, { label: 'Associate Markdown files with this app', onToggle: saveAssoc });
  $('mdAssocSlot').appendChild(assocSwitch);
  categorySwitch = switchEl(false, {
    label: 'Add an Outlook category when archiving',
    onToggle: (next) => save({ outlook: { add_category: next } }),
  });
  $('archiveCategorySlot').appendChild(categorySwitch);
  moveSwitch = switchEl(false, {
    label: 'Move to the Outlook archive folder when archiving',
    onToggle: (next) => save({ outlook: { move_to_archive: next } }),
  });
  $('archiveMoveSlot').appendChild(moveSwitch);
  traySwitch = switchEl(true, {
    label: 'Tray click opens Quick search',
    onToggle: async (next) => {
      if (await save({ tray: { left_click: next ? 'quick_search' : 'open_app' } })) {
        showToast(next ? 'Tray click opens Quick search' : 'Tray click opens the app');
      }
    },
  });
  $('trayClickSlot').appendChild(traySwitch);
  $('createConfig').addEventListener('click', createConfig);
  remoteSwitch = switchEl(false, { label: 'Remote access', onToggle: (next) => save({ remote_access: next }) });
  $('remoteSwitchSlot').appendChild(remoteSwitch);
  autoSwitch = switchEl(true, {
    label: 'Every local drive and OneDrive',
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
