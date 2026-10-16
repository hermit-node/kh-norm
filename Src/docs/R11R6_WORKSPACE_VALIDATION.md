# Norm 0.55.10r11r6 - Workspace stability fixes

Built from r11r5 without changing Celery, N1/N2, Redis, PostgreSQL,
startup/installer configuration, or the installed C:\Norm tree.

- WH_MOUSE_LL callback now defers native window lookup, focus and raise
  operations to a Qt single-shot dispatch.
- Stop terminal-discovery polling once all three HWNDs are captured,
  after release, and during explicit shutdown. Resume discovery for
  missing terminals. Enumerate windows once per batch of missing roles.
- Watch normal-service-exit marker via QFileSystemWatcher rather than
  persistent polling. Dedicated shutdown timer runs only during Quit Norm.
- Avoid redundant console SetWindowPos calls and routine SWP_FRAMECHANGED;
  pass SWP_NOZORDER and reset the geometry cache on attach/release.

Offline tests: tools/test_qt_focus_lifecycle_r4.py plus
tools/test_workspace_stability_r11r6.py. Neither starts Norm nor
operator terminals. Interactive Windows terminal-size and focus checks
are still needed after installation. This ZIP is portable source,
NOT a rebuilt Norm-Installer.exe binary.
