Norm Installer Kit 1.4.4
========================

What changed
------------
The installer is no longer hard-bound to one source ZIP filename/SHA. At launch it scans
the folder containing the installer EXE and automatically chooses the newest valid Norm
portable-source package. Version comes from package-manifest.json, not from the filename.
If two packages declare the same Norm version, the most recently modified ZIP wins.

This specifically fixes the awkward workflow where a customized Norm package had to be
renamed/rebound to whatever payload filename had been compiled into that installer.

Launch behavior
---------------
- Double-click Norm-Installer-1.4.4.exe.
- It scans only the EXE's own folder for *.zip packages.
- It ignores ZIPs that are not Norm portable-source packages.
- It selects the highest manifest version; modification time breaks same-version ties.
- If a .sha256 companion exists for the selected package, it is still verified.
- If the selected newest package is invalid or its companion SHA is stale, the installer
  reports that problem visibly instead of silently falling back to an older package.
- If no Norm source package is present, a visible error dialog explains what is missing
  instead of a windowed EXE appearing to crash.

The installer GUI still only asks for:
1. Install/update folder (normally C:\Norm)
2. Base Python used to create/recreate the Norm .venv

Builder behavior
----------------
Build-Norm-Installer-EXE.bat / Make-Norm-Installer.py also select the newest local Norm
portable-source ZIP rather than a hardcoded filename. The dependency table and
"All newest (resolve)" behavior from 1.4.0 are unchanged.

When a build is made, the selected/resolved requirements are written into a derived copy
of that source ZIP, and a matching .sha256 companion is generated. The installer itself
remains generic, so a later newer/customized Norm package can simply be placed beside the
EXE without recompiling the installer.

Startup smoke test
------------------
The builder now smoke-tests the generated EXE as a real mini bundle: it copies the EXE,
payload ZIP, and SHA into one folder, launches the EXE with no explicit --source, and
requires its newest-local-package discovery/validation path to succeed before publishing.
This exercises the path that previously could look like an immediate launch crash.

Dependency selection
--------------------
"All newest (resolve)" still asks pip for the newest mutually compatible stable/RC set.
Alpha, beta, and dev releases remain excluded. Manual online overrides are still allowed
and the exact final lock is resolver-validated before packaging.

Norm source
-----------
The included base source remains Norm 0.52.6. This installer-only revision does not change
the Norm runtime version.
