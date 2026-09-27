Norm Installer Kit 1.3.6
========================

Normal use
----------
1. Double-click Run-Norm-Installer.bat, or run Norm-Installer.py with Python.
2. Select either a Norm portable-source ZIP or a private Norm full-backup ZIP.
3. Select the install target (default C:\Norm).
4. Select the Python executable Norm should use.
5. Click Install Norm.

Normal source updates are in-place managed syncs. Changed package files are replaced,
package-owned files removed by the new base are deleted, and persistent local state is
preserved. In particular, a compatible .venv is reused instead of being rebuilt.

Persistent normal-update paths include:
  .venv\
  .ssh\
  plugins\
  logs\
  state\

The package-managed docs live in C:\Norm\docs. The external writable tree is split into:
  %USERPROFILE%\Documents\Norm\workspace   durable artifacts/work
  %USERPROFILE%\Documents\Norm\temp        disposable scratch/recovery

Full backups
------------
The shipped backup plugin supports `/backup` portable-source media and `/backup full` sensitive full-state media. This same installer accepts both package types. A full-backup restore additionally restores the captured .ssh, plugins, secrets,
workspace, selected recovery material, runtime state/logs, and PostgreSQL snapshot.

Full-backup ZIPs contain credentials and private keys. Protect them accordingly.
The .venv directory is deliberately NOT archived; the installer reuses an existing compatible
venv or recreates it from the included Python/Torch/requirements metadata.

Future Norm versions
--------------------
The installer is not tied to Norm 0.52.3. Future package_schema 1 source/full-backup packages can be
selected without recompiling the installer.

Build the standalone GUI EXE
----------------------------
Double-click Build-Norm-Installer-EXE.bat. It creates a temporary build venv, installs
PyInstaller, writes Norm-Installer.exe beside this file, and removes the temporary build
environment after a successful build. Installer 1.3.6 avoids FOR /F command capture when
reading the pip requirement, so Python executables on paths/drives such as G:\... work reliably.


Runtime-root binding
--------------------
Portable source packages keep [paths].runtime_root relative (`.`) so they remain relocatable.
After every install/update/restore, Installer 1.3.6 writes the target's actual absolute path
(e.g. C:\Norm) into the installed config\settings.ini. This prevents runtime placeholders
from drifting or being interpreted relative to the wrong folder.


Checksums and intentional source changes
----------------------------------------
If a selected ZIP has a sibling file named <zip-name>.sha256, Installer 1.3.6 verifies it
before reading or installing the package. A ZIP copied without a companion checksum is still
accepted.

pip is installer infrastructure and is NOT stored in Norm's requirements-lock.txt. Installer 1.3.6 runs `pip install --upgrade "pip>=26.1"`, so any newer available pip is accepted instead of pinning one exact build. The standalone EXE build reads the same PIP_SPEC from Norm-Installer.py.

If you intentionally change Norm source or requirements, build a NEW portable-source ZIP
(preferably with a bumped Norm version), then drag that ZIP onto Update-Source-SHA.bat.
Despite its old filename, this helper does NOT update Norm or alter the ZIP; it only regenerates
the companion .sha256 file for the rebuilt ZIP. Do not regenerate a checksum merely to silence
an unexpected mismatch; investigate unexpected changes first.


Installer 1.3.6 notes
---------------------
- Normal updates preserve `.ssh`, `.venv`, user plugins, logs, and state.
- The installer UI explicitly shows the reuse/preservation behavior and gives more space to useful progress/log output.
- Embedded Norm 0.52.3 retains canonical thread controls: `/new [name]`, `/thread-list`, and `/thread-resume <name|id>`.
- Visible command help no longer lists legacy aliases, although the parsers continue to accept them for compatibility.
- `/flush-suppressed` removes suppressed PostgreSQL tasks and the corresponding parked GUI delivery records; an in-flight prompt keeps only its hidden retry-block tombstone until dispatch ends.

Installer 1.3.6 build/startup hardening
----------------------------------------
- The kit contains one source payload only: Norm 0.52.3 plus its SHA-256 companion.
- Standalone installer builds use PyInstaller 6.22.3.
- The build BAT launches the freshly generated EXE with a hidden self-test and requires a sentinel file before promoting it. A loader/startup failure therefore fails the build.
- Norm 0.52.3 adds explicit Windows service mode so stray Ctrl+C/Ctrl-Break events from console/process-group interactions cannot silently terminate the background runtime.
