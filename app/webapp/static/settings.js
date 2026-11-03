/* Settings: text size, default basis, remote access, the in-app schedule and the scan roots. */

import { getJSON, postJSON } from './api.js';
import { readPref, state, writePref } from './state.js';
import { fmtBytes } from './fmt.js';
import { setSwitch, switchEl } from './_vendored/switch/switch.js';
import { showToast } from './_vendored/toast/toast.js';

const $ = (id) => document.getElementById(id);
let remoteSwitch = null;
let autoSwitch = null;
let onRootsChanged = () => {};

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

  const auto = cfg.roots === null;
  setSwitch(autoSwitch, auto);
  autoSwitch.setAttribute('aria-label', 'Every local drive and OneDrive');
  $('rootsInput').hidden = auto;
  if (!auto) $('rootsInput').value = cfg.roots.join('\n');
  $('rootsNote').textContent = (auto ? 'Scanning ' : 'One folder per line, saved when you leave the box. Scanning ') +
    state.roots.map((r) => r.name).join(', ') + '.';
  $('indexSize').textContent = fmtBytes(body.index_bytes) + ', kept on this PC';
  setBasis(readPref('basis', 'size'));
}

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
        setSwitch(autoSwitch, false);
        $('rootsInput').hidden = false;
        $('rootsInput').value = state.roots.map((r) => r.path).join('\n');
        $('rootsInput').focus();
      }
    },
  });
  $('rootsAutoSlot').appendChild(autoSwitch);
  $('rootsInput').addEventListener('change', async () => {
    const roots = $('rootsInput').value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!roots.length) { showToast('Add at least one folder, or switch back to every drive.', 'error'); return; }
    if (await save({ roots })) { showToast('Roots saved'); onRootsChanged(); }
  });
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
