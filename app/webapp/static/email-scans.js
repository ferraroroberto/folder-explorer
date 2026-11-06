/* Scans: the email index card: what the last run did, failed files, "Index emails now" and one folder. */

import { getJSON, postJSON, ApiError } from './api.js';
import { esc, fmtAgo, fmtNum, winPath } from './fmt.js';
import { showToast } from './_vendored/toast/toast.js';

const $ = (id) => document.getElementById(id);
// How often the card re-reads the run history while a run is going.
const POLL_MS = 3000;

let body = null;
let timer = null;

const MB = 1024 * 1024;

function stat(value, label, tone, act) {
  const tag = act ? 'button type="button" data-email-act="' + act + '" data-track="email-' + act + '"' : 'div';
  return '<' + tag + ' class="stat"><div class="stat-value' + (tone ? ' ' + tone : '') + '">' + value + '</div>' +
    '<div class="stat-label">' + esc(label) + '</div></' + tag.split(' ')[0] + '>';
}

// The last two folders of a path: an email folder as a select or a run names it.
const shortPath = (p) => p.split(/[\\/]/).filter(Boolean).slice(-2).join('\\');

function runRow(r) {
  const live = r.status === 'running';
  const warn = r.status === 'failed' || r.status === 'interrupted';
  const scope = r.scope === 'all' ? 'Every email folder' : shortPath(r.scope);
  return '<li class="run-row"><span class="mark' + (live ? ' is-live' : warn ? ' is-warn' : '') + '" aria-hidden="true"></span><span>' +
    '<strong>' + esc(live ? 'Indexing now' : r.message || r.status) + '</strong>' +
    esc(scope + ' · ' + fmtAgo(r.started_at) + (r.trigger === 'cli' ? ' · nightly or command line' : '')) + '</span></li>';
}

function render() {
  const card = $('emailCard');
  card.hidden = !body;
  if (!body) return;
  const last = body.runs.find((r) => r.status === 'ok');
  const c = last ? last.counts : {};
  $('emailMeta').textContent = body.ready ? fmtNum(body.indexed) + ' emails' : 'Not built yet';
  $('emailStats').innerHTML =
    stat(fmtNum(body.indexed), 'Indexed') +
    stat(fmtNum((c.new || 0) + (c.changed || 0) + (c.reread || 0)), 'New or changed, last run') +
    stat(fmtNum(c.downloaded || 0) + ' · ' + Math.round((c.downloaded_bytes || 0) / MB) + ' MB', 'Downloaded, last run') +
    stat(fmtNum(c.waiting || 0), 'Waiting for budget', c.waiting ? 'tone-attention-text' : '') +
    stat(fmtNum(body.failed), 'Failed reads', body.failed ? 'tone-danger-text' : '', body.failed ? 'failures' : '');
  $('emailScope').innerHTML = body.roots.map((r) => '<option value="' + esc(r) + '" title="' + esc(r) + '">' + esc(shortPath(r)) + '</option>').join('');
  const off = body.stage_problem;
  $('indexEmails').disabled = !!off || body.running;
  $('indexOne').disabled = !!off || body.running || !body.roots.length;
  $('indexEmails').textContent = body.running ? 'Indexing…' : 'Index emails now';
  $('emailNote').textContent = !body.folders.length ? 'No email folders yet: add one in Settings, Email folders.'
    : !body.ready ? 'Not set up here: ' + body.problem + '.' : off ? 'Indexing is off here: ' + off + '.'
    : 'Folders, exclusions and the download budget are in Settings.';
  $('emailRuns').innerHTML = body.runs.length ? body.runs.map(runRow).join('') : '<li class="rows-note">No runs yet.</li>';
}

export async function loadEmailScans() {
  try {
    body = await getJSON('/api/email/status');
  } catch (e) {
    body = null;
  }
  render();
  if (timer) clearTimeout(timer);
  timer = body && body.running ? setTimeout(() => loadEmailScans(), POLL_MS) : null;
}

async function start(root) {
  try {
    await postJSON('/api/email/index', root ? { root } : {});
    showToast(root ? 'Indexing that folder' : 'Indexing the email folders');
  } catch (e) {
    showToast(e instanceof ApiError && e.status === 409 ? 'The email index is already updating' : e.message, 'error');
  }
  setTimeout(() => loadEmailScans(), 500);
}

async function showFailures() {
  const d = $('failuresDialog');
  try {
    const f = await getJSON('/api/email/failures');
    $('failuresBody').innerHTML = f.failures.length ? '<ul class="rows">' + f.failures.map((x) =>
      '<li class="fail-row"><span class="mono">' + esc(winPath(x.path)) + '</span><span class="why">' + esc(x.reason) + '</span></li>').join('') + '</ul>' +
      (f.total > f.failures.length ? '<p class="rows-note">The newest ' + f.failures.length + ' of ' + fmtNum(f.total) + '.</p>' : '') +
      '<p class="rows-note">A file that failed is read again only once it changes.</p>'
      : '<p class="rows-note">No failed reads.</p>';
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  d.showModal();
}

export function initEmailScans() {
  $('indexEmails').addEventListener('click', () => start(null));
  $('indexOne').addEventListener('click', () => start($('emailScope').value));
  $('emailInfo').addEventListener('click', () => $('emailInfoDialog').showModal());
  $('emailStats').addEventListener('click', (e) => { if (e.target.closest('[data-email-act="failures"]')) showFailures(); });
}

