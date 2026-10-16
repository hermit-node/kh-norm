# Norm 0.55.10: integrated Qt terminal workspace

Uses the existing PySide6 Qt-Focus workspace and three real Windows terminal
HWNDs; preserves native focus/Alt+Tab handling, layering, drag and resize.
No ConPTY rewrite. Run-Norm.bat now starts tools/start_workspace.py.
Windows session is reused using a named mutex; Norm service, Celery and
operator roles have serialized startup and process-identity checks.
Repeated launches attach instead of creating fresh sets. Duplicate Celery
IDs fail closed: clean orphan workers first.

Font family, point size and weight for UI, Runtime, Replies and Prompt are
configured persistently in config/settings.ini [workspace], edited using
the workspace Fonts button. Terminal fonts apply to new Windows Terminal
role-specific profiles only; running terminals are not forcibly restarted.
Only the Norm-owned Windows Terminal fragment is updated, never global
Windows Terminal settings.json.

Window/pane geometry, z-order, active role and maximization persist in
state/workspace-layout.json, migrating old Qt-Focus layout on first use.

Quit Norm, window X, and Alt+F4 initiate tools/safe_shutdown_norm.py. Norm
first drains/checkpoints work, stops its service and Celery workers,
closes process-local PostgreSQL pools (norm and stocks) and Redis clients,
signals operator console hosts, and shuts down auxiliary browser/GUI tools.
Workspace does not disappear until owned process exit has been verified;
shutdown failures leave the window visible with a log reference.
The PostgreSQL SERVER and Redis SERVER remain running, and no other
application's connections or processes are terminated.

Standalone cleanup if Norm is not running:
    cd C:\Norm
    Safe-Shutdown-Norm.bat
This stops all orphaned Norm-owned workers; original helper can force stop
remaining workers after its grace timeout. Do not use during critical work.

Opt out of integrated Qt UI using workspace.enabled=false in settings.ini.
This reverts to standalone operator windows with duplicate-start protection.

Release state: source-only, uninstalled. Regression: source compilation,
simulated Celery/console identity detection, persistent font configuration,
independent Qt offscreen close/geometry test, and previous browser PDF tests.
Full interactive HWND/Windows Terminal and complete real shutdown not yet
smoke tested. Runtime EXE and installer rebuild are separate release work.
