# Native Norm Workspace 0.55.10r2

This is the original C++/Win32 native pane container with targeted integration.
Default Run-Norm.bat opens NormWorkspace.exe first; the native UI starts the
current service through tools/workspace/native/start_native_roles.py ->
tools/workspace/start_roles.py -> Run-Norm.bat --service-only. The backend
branch does not open another workspace.

Native Fonts: Win32 common dialog, UI immediate, terminal profiles on NEW
terminal sessions only. Initial preferences copied from current Norm settings;
later changes persisted under %LOCALAPPDATA%/Norm/workspace.

Quit Norm: exact-root safe_shutdown_norm.py; wait for verified completion;
normal BAT/service exit also closes workspace; Release merely detaches
terminals. Does NOT purge soft-delete trash.

Native binary produced by MSVC /std:c++17 /O2 /MT; source and Build.ps1
included. Offscreen tests do not establish real-user GUI responsiveness.
