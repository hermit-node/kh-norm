Norm Unified Installer 1.5.0
================================

Purpose
-------
One installer window. One package-selection rule. One install engine.

Normal workflow
---------------
1. Put this installer beside one or more Norm-*-portable-source.zip packages and their .sha256 files.
2. Run Run-Norm-Installer.bat, or build/run Norm-Installer.exe.
3. Page 1 automatically selects the highest valid Norm package VERSION in the folder.
   File modification time is only a same-version tie-breaker.
4. Choose requirements mode:
   - Newest available packages (default): derive distribution names from the package's own
     tools/requirements-lock.txt, ask pip to upgrade/resolve those names using normal stable
     resolution, run pip check, and save state/installer/resolved-requirements.txt.
   - Use package requirements: install the exact tools/requirements-lock.txt shipped in the package.
5. Click Next.
6. Page 2 shows the installation timeline and merged subprocess output in the same window.
7. On verified success, Page 3 summarizes the installation and enables Finish.

Design decisions
----------------
- There is no external requirements.txt authority.
- There is no second/legacy installer in this operator package.
- Advanced package selection remains available for deliberate rollback/debugging.
- The mature managed-sync/venv/build/backup-restore engine from the prior installer is retained.
- Existing protected/persistent Norm paths continue to be preserved by the install engine.
- A checksum mismatch is fail-closed.
- A non-empty target that does not look like Norm is fail-closed.
- Newest-available mode records the resolved environment for later reproduction/audit.

Files
-----
Norm-Installer.py
Run-Norm-Installer.bat
Build-Norm-Installer-EXE.bat
Norm-0.53.8-portable-source.zip
Norm-0.53.8-portable-source.zip.sha256
test_unified_installer.py

Windows EXE build
-----------------
Run Build-Norm-Installer-EXE.bat on the Windows Norm machine. It builds a one-file,
windowed Norm-Installer.exe with Python 3.14, pip 26.2.1, and PyInstaller 6.22.3.

Validation performed here
-------------------------
- Python syntax compilation: PASS
- Version-first package discovery: PASS
- Manifest inspection: PASS
- Companion SHA-256 verification/rejection: PASS
- Package-lock dependency mode present: PASS
- Newest-available dependency mode present: PASS
- Resolved-environment evidence generation present: PASS
- GUI has no external requirements.txt gate: PASS

Environment boundary
--------------------
This Linux sandbox cannot execute the final Windows Tk/PyInstaller EXE or perform a real
C:\Norm update. Run the included regression test and then the installer on Windows for
the final platform-specific acceptance pass.
