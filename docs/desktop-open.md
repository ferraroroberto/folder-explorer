# Opening a folder fast (this PC)

How Explore's folder-open action stays fast by reusing the already-running
File Explorer process, and how to read its timing breadcrumb in
`webapp.log`.

## Reusing the running Explorer

A folder opens through the shell's explore action, which the Explorer
process already running answers without starting a new `explorer.exe`; the
app then brings that window to the front (`src/window_front.py`), restoring
it when minimised, even though the browser still has the focus.

Settings' **Reuse an open Explorer window** (`desktop.reuse_explorer_window`
in `config.json`, off by default) shows the folder in the Explorer window
used last instead of opening a fresh one: faster, but that window loses
whatever it showed before. A folder the app can't confirm exists within
250 ms (a slow or offline network share) opens anyway rather than waiting
on the check.

## Reading the log

Each open logs its timings to `webapp.log` in the runtime-data folder, as
milliseconds elapsed since the request:

```
open: request ready at <ms> ms, existence check <ms> ms (there)
File Explorer asked for the folder in a new window at <ms> ms
window shown at <ms> ms, in front at <ms> ms
```

A large gap before "asked" points at the app or the existence check; a gap
between "asked" and "shown" is Windows and Explorer, not the app. A
`kept it behind` or `no File Explorer window showed the folder` warning
names which step failed.

`src/desktop.py` is the one module allowed to launch these actions (see
`CLAUDE.md`'s scan hard rules); its tests fake the launch seam and never
start a real Explorer process.
