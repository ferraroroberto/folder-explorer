# Region screenshot

The tray's screenshot: press a global hotkey anywhere, drag a rectangle over the frozen screen, release, and the selection is saved as a timestamped JPEG and copied to the clipboard. No editor, no dialog. Esc or a right-click cancels and saves nothing. It runs inside the tray process (`pythonw -m app.tray`), so it is there whenever the tray is, and a second tray never starts (the tray's mutex).

## Selecting precisely

- **Loupe:** a 25 x 25 pixel square around the cursor, magnified 8x with nearest-neighbour so every pixel is a block, a crosshair on the centre pixel, and a label `x,y` (screen coordinates), or `x,y (w×h)` while dragging. It is drawn from the frame already captured, reads physical pixels, and flips to the other side of the cursor near the right and bottom edge of the monitor under it.
- **F6** shows or hides the loupe and saves the choice as `snap.show_loupe`.
- **Arrow keys** move the cursor one pixel, **Shift+arrow** ten, before or during a drag, so an edge can be placed exactly.

## Tray menu

**Screenshot** in the tray menu shows the hotkey in its title and holds **Copy to clipboard on save**, **Show loupe (F6)**, **JPEG quality** (70, 80, 90, 95, 100), **Filename prefix...** (a dialog; a character Windows refuses in a filename, an empty value or more than 40 characters is refused), **Open folder** and **Change folder...**. Each is written to the `snap` block of `config.json` (only that key changes) and applies from the next capture. A hotkey another application already owns is shown first in the submenu ("Not running: ...") and logged, and the rest of the tray is unaffected.

## Config (`snap` in `config.json`)

| Key | Default | Meaning |
|---|---|---|
| `folder` | `null` (`~/Pictures/snaps`) | Where captures go; `~` and `%VARS%` expand |
| `hotkey` | `ctrl+shift+a` | One or more of `ctrl`, `shift`, `alt`, `win`, then a letter, digit, `f1`-`f24`, `printscreen` or `space`; read when the tray starts |
| `jpeg_quality` | `90` | 1-100 |
| `copy_to_clipboard` | `true` | Also put the capture on the clipboard |
| `file_prefix` | `snap` | Start of every file name |
| `show_loupe` | `true` | Whether the loupe is shown |

A value that cannot be used is logged and takes its default. The web app's settings route refuses the block (it is not a Settings control), so a phone can never change where the PC saves files.

## Files and rules

Captures are named `<file_prefix>_YYYY-MM-DD_HH-MM-SS-mmm.jpg`; a name already taken gets `_2`, `_3`, ... so nothing is overwritten. The screenshot reads screen pixels with `mss` and never a file; its only write is that new JPEG, through `src/snap/overlay.py` (a test holds it), and `make_dirs` refuses a folder under the code.

## How it stays fast

The Tk thread does its start-up work once: `RegisterHotKey` runs on its own message-pump thread and wakes the Tk thread, and the overlay is a borderless top-most window built in advance, so a keypress only grabs the virtual screen and paints it. `tray.log` (runtime-data folder) gets one line per capture for the overlay (grab time and hotkey-to-overlay milliseconds), one for the first loupe paint (taken after the overlay time, so the loupe never adds to the logged figure) and one for the save (path, size, release-to-file). The settings are read after the overlay time is taken, for the same reason.

## DPI and monitors

Screen, capture and crop must all be physical pixels on a scaled display. The Tk thread opts into per-monitor DPI awareness for itself only (`SetThreadDpiAwarenessContext`), and the process default is first fixed as unaware: the screen grabber switches the whole process to DPI aware on first use, which would resize the Quick search window. The capture spans every monitor; a monitor left of or above the primary has a negative origin, handled in `src/snap/core.py` and tested there.

## Tests

`tests/snap/` covers naming, crop maths with monitor offsets, loupe placement (edge flipping, monitor seams, scaled displays), arrow nudging, prefix validation, hotkey parsing and the settings' round trip through `config.json`. The overlay and the hotkey need a real desktop and are checked by hand.
