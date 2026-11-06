/* Settings: text size, default basis, remote access, the in-app schedule, and the folder lists for the scan and the email index. */

import { getJSON, postJSON } from './api.js';
import { readPref, state, writePref } from './state.js';
import { fmtBytes } from './fmt.js';
import { setSwitch, switchEl } from './_vendored/switch/switch.js';
import { showToast } from './_vendored/toast/toast.js';
import { folderList } from './folder-list.js';

const $ = (id) => document.getElementById(id);
let remoteSwitch = null;
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

function renderBudget(mb) {
  const sel = $('emailBudget');
  // A budget set in config.json to a value the menu doesn't offer still shows as itself.
  if (![...sel.options].some((o) => o.value === String(mb))) sel.add(new Option(mb + ' MB', String(mb)));
  sel.value = String(mb);
  $('emailBudgetNote').textContent = mb ? 'Each email run downloads up to this much online-only mail; the rest waits for the next run.'
    : 'Online-only mail is left in the cloud.';
}

/** Save one setting from a folder list: null when saved, else the server's sentence. */
async function saveList(update, okText, after) {
  try {
    render(await postJSON('/api/config', update));
  } catch (e) {
    return e.message;
  }
  showToast(okText);
  if (after) after();
  return null;
}

const withEmail = (change) => ({ email: { ...config.email, ...change } });

function setBasis(value) {
  $('defaultBasisSeg').querySelectorAll('.segmented-item').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === value)));
}

async function save(update) {
  try {
    const body = await postJSON('/api/config', update);
    render(body);
    return body;
  } catch (e) {
    showToast('Could not save: ' + e.message, 'error');
    return null;
  }
}

export function initSettings(opts) {
  onRootsChanged = opts.onRootsChanged;
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
  $('emailBudget').addEventListener('change', (e) => save(withEmail({ download_budget_mb: Number(e.target.value) })));
  $('scheduleSelect').addEventListener('change', (e) => save({ schedule: e.target.value }));
  $('scheduleTime').addEventListener('change', (e) => { if (e.target.value) save({ schedule_time: e.target.value }); });
  $('defaultBasisSeg').addEventListener('click', (e) => {
    const b = e.target.closest('.segmented-item');
    if (!b) return;
    writePref('basis', b.dataset.value);
    setBasis(b.dataset.value);
  });
}

export async function openSettings() {
  const d = $('settingsDialog');
  try {
    render(await getJSON('/api/config'));
  } catch (e) {
    showToast('Could not load settings: ' + e.message, 'error');
    return;
  }
  if (!d.open) d.showModal();
}
