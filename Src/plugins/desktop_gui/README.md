# Norm Desktop GUI (prototype)

desktop_windows, desktop_controls, desktop_screenshot are read-only
Native Norm plugin tools that communicate with the authenticated loopback
desktop helper running in the interactive Windows console session.
desktop_click, desktop_type, desktop_hotkey are defined but refused by
the preview helper until input is explicitly approved and enabled.

This does NOT install a second MCP server, and does NOT touch N1/N2
tool authorization. N2 proposes the tool; N1 remains the gatekeeper.
Screenshot files are created only when requested and are saved under
workspace/images/desktop for Norm's existing vision tools.

The helper must run in the logged-in session, not Windows service session 0.
Bundled server binds only configured 127.0.0.1:18766 by default and requires a per-install token.
The plugin reads its token from state/desktop_gui_token by default, or the
[desktop_gui] token_file option in config/settings.ini. Do not put the token
in source releases, logs, installer archives, or chat. Be aware that GUI
content may include sensitive information.

Dependencies for the helper: pywinauto 0.6.9, mss 10.1.0, PyWin32, comtypes.
Helper must be separately packaged/deployed and placed under Norm's tools/
by tools/start_desktop_gui.py using a login-session scheduled task.
Do not claim the optional input controls are active in this preview.
