"""The tray's region screenshot: hotkey, selection overlay, loupe, JPEG save and clipboard.

``core`` is the pure geometry and naming, ``settings`` the ``snap`` block of config.json (neither
imports Tk or Win32), ``clipboard`` the DIB copy, ``overlay`` the Tk overlay and loupe, ``hotkey`` the
global hotkey thread, and ``service`` the one object the tray starts. Nothing here imports ``app/``.
"""
