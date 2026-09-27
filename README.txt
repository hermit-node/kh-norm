Norm Installer 1.0.0
====================

Normal use
----------
1. Double-click Run-Norm-Installer.bat, or run Norm-Installer.py with Python.
2. Select a Norm portable-source ZIP (0.52.0 is included).
3. Select the install target (default C:\Norm).
4. Select the Python executable Norm should use.
5. Click Install Norm.

The installer validates package-manifest.json before touching the target, creates
.venv, installs the package's configured PyTorch build and pinned requirements,
runs pip/compile checks, compiles core\norm.exe, creates the persistent plugin
workspace, and reports missing external secrets without embedding them.

Future Norm versions
--------------------
The installer is not tied to 0.52.0. Put a future Norm portable-source ZIP beside
Norm-Installer.py / Norm-Installer.exe or browse to it. Packages using
package_schema 1 and the same manifest contract can be installed by the same
installer. If several valid Norm ZIPs are beside the installer, the newest
version is preselected.

Build the standalone GUI EXE
----------------------------
Double-click Build-Norm-Installer-EXE.bat. It creates a temporary build venv,
installs PyInstaller 6.22.2 there, writes Norm-Installer.exe beside this file,
and removes the temporary build environment after a successful build.

The EXE intentionally does NOT embed a Norm release. Keeping installer and source
package separate is what lets the same installer EXE be reused for later Norm
base copies without recompiling it.
