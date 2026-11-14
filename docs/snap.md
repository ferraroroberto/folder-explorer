# Region screenshot

The tray's screenshot: press a global hotkey anywhere, click one corner of an area on the frozen screen, move, click again, and the selection is saved as a timestamped JPEG and copied to the clipboard. No editor, no dialog. Esc or a right-click cancels and saves nothing. It runs inside the tray process (`pythonw -m app.tray`), so it is there whenever the tray is, and a second tray never starts (the tray's mutex).

## Selecting an area

By default the first click anchors a corner and releasing it changes nothing; moving stretches the rectangle (the loupe follows and shows its size) and the second click saves. A second click that would crop under the minimum size keeps the overlay and the anchor. Turn on **Hold left click to capture** (Settings, or `snap.hold_to_capture`) for press, drag, release, where a release under the minimum size cancels. Esc or a right-click cancels in both modes, and the arrow keys nudge in both. The mode is read at each capture, so a change applies from the next one with no restart. The rules live in `src/snap/selection.py`, with no Tk, so both modes are unit-tested.

## Selecting precisely

- **Loupe:** a 25 x 25 pixel square around the cursor, magnified 8x with nearest-neighbour so every pixel is a block, a crosshair on the centre pixel, and a label `x,y` (screen coordinates), or `x,y (w×h)` once a corner is anchored. It is drawn from the frame already captured, reads physical pixels, and flips to the other side of the cursor near the right and bottom edge of the monitor under it.
- **F6** shows or hides the loupe and saves the choice as `snap.show_loupe`.
- **Arrow keys** move the cursor one pixel, **Shift+arrow** ten, before or after the first click, so an edge can be placed exactly.

## Tray menu

**Screenshot** in the tray menu shows the hotkey in its title and holds **Copy to clipboard on save**, **Show loupe (F6)**, **JPEG quality** (70, 80, 90, 95, 100), **Filename prefix...** (a dialog; a character Windows refuses in a filename, an empty value or more than 40 characters is refused), **Open folder** and **Change folder...**. Each is also a row of Settings (below). Each is written to the `snap` block of `config.json` (only that key changes) and applies from the next capture. While the tray runs, it looks at `config.json` every two seconds and redraws the menu when the file changes, so a setting changed in Settings shows its check here too. A hotkey another application already owns is shown first in the submenu ("Not running: ...") and logged, and the rest of the tray is unaffected.

## Settings

The **Tray and screenshot** section of Settings holds every setting the tray menu does, plus the hotkey: **Hold left click to capture**, **Copy to clipboard on save**, **Show loupe**, **JPEG quality** (70, 80, 90, 95, 100), **Filename prefix**, **Hotkey** and **Save folder**. A switch or a menu saves at once; a text field saves when you leave it or press Enter. Each goes through `POST /api/config` into `snap_settings.validate()`, so Settings and the tray refuse the same values with the same sentence, and a refused value leaves `config.json` untouched and puts the saved one back in the field. Only the changed key is written.

- **Hotkey:** read once when the tray starts, so a new combo applies the next time the tray starts; the row says so. A combo another application owns is reported where it always was, as "Not running: ..." at the top of the tray's Screenshot menu and in `tray.log`; Settings does not probe it, because the tray holds the registration.
- **Save folder:** shown, never edited, so a phone can never change where the PC writes files. **Open folder** (`POST /api/snap/open-folder`, this PC only) shows the configured folder in File Explorer through `src/desktop.py`, making it first when no capture has yet; the page names no path. Change it from the tray's **Change folder...** or in `config.json`.
- **Both ways:** Settings reads `config.json` each time it opens, and the tray redraws its menu when the file changes.

## Config (`snap` in `config.json`)

| Key | Default | Meaning |
|---|---|---|
| `folder` | `null` (`~/Pictures/snaps`) | Where captures go; `~` and `%VARS%` expand |
| `hotkey` | `ctrl+shift+a` | One or more of `ctrl`, `shift`, `alt`, `win`, then a letter, digit, `f1`-`f24`, `printscreen` or `space`; read when the tray starts |
| `jpeg_quality` | `90` | 1-100 |
| `copy_to_clipboard` | `true` | Also put the capture on the clipboard |
| `file_prefix` | `snap` | Start of every file name |
| `show_loupe` | `true` | Whether the loupe is shown |
| `hold_to_capture` | `false` | `false`: click, move, click; `true`: press, drag, release |

A value that cannot be used is logged and takes its default. The web app's settings route accepts every key in this block but `folder` and refuses that one, so a phone can never change where the PC saves files.

## Files and rules

Captures are named `<file_prefix>_YYYY-MM-DD_HH-MM-SS-mmm.jpg`; a name already taken gets `_2`, `_3`, ... so nothing is overwritten. The screenshot reads screen pixels with `mss` and never a file; its only write is that new JPEG, through `src/snap/overlay.py` (a test holds it), and `make_dirs` refuses a folder under the code.

## How it stays fast

The Tk thread does its start-up work once: `RegisterHotKey` runs on its own message-pump thread and wakes the Tk thread, and the overlay is a borderless top-most window built in advance, so a keypress only grabs the virtual screen and paints it. `tray.log` (runtime-data folder) gets one line per capture for the overlay (grab time and hotkey-to-overlay milliseconds), one for the first loupe paint (taken after the overlay time, so the loupe never adds to the logged figure) and one for the save (path, size, release-to-file). The settings are read after the overlay time is taken, for the same reason.

## DPI and monitors

Screen, capture and crop must all be physical pixels on a scaled display. The Tk thread opts into per-monitor DPI awareness for itself only (`SetThreadDpiAwarenessContext`), and the process default is first fixed as unaware: the screen grabber switches the whole process to DPI aware on first use, which would resize the Quick search window. The capture spans every monitor; a monitor left of or above the primary has a negative origin, handled in `src/snap/core.py` and tested there. The overlay's Tk geometry writes every offset after a `+` (`+0+-1080`), because Tk reads `-1080` as the bottom edge 1080 pixels up from the screen's bottom, which would leave the lower monitors uncovered and the picture shifted; an overlay that still lands off the virtual screen's origin is logged.

## A black grab

A monitor whose part of the grab is pure black is a capture Windows refused, not a dark screen. One such monitor is logged as a warning naming it and the overlay still opens; when every monitor comes back black, the warning says so and no overlay is shown, so the next press tries again.

## Tests

`tests/snap/` covers naming, crop maths with monitor offsets, loupe placement (edge flipping, monitor seams, scaled displays), arrow nudging, the selection state machine of both modes, prefix validation, hotkey parsing, the settings' round trip through `config.json`, the Settings API (every key but the folder, the validator's refusals, Open folder for this PC only) and the tray's menu refresh. The overlay and the hotkey need a real desktop and are checked by hand.
