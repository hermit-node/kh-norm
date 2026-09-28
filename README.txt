Norm Installer Kit 1.4.1
========================

What changed
------------
The installer builder now treats "All newest" as a dependency-resolution request, not as
"pin every package to its individually newest PyPI release."

Build-Norm-Installer-EXE.bat launches Make-Norm-Installer.py. The builder:
- uses Norm-0.52.5-portable-source.zip as the one fixed base source package;
- reads every exact package pin from tools\requirements-lock.txt;
- shows the locked version and newest Python-compatible eligible PyPI release;
- considers stable releases and release candidates (rc) eligible;
- excludes alpha, beta, and dev builds;
- "All newest (resolve)" creates an isolated resolver venv and asks pip for the newest
  mutually compatible stable set across all direct Norm requirements;
- after the stable solve, it tries eligible newer RC candidates one at a time, globally
  re-resolving the rest of the stack around each RC and keeping only compatible RCs;
- writes the resolved direct-package versions back into the table before any build starts;
- keeps "Use online (manual)" for intentionally forcing an individual row; manual choices
  are still checked by pip before packaging and may legitimately fail if incompatible;
- exposes pip separately as installer infrastructure and uses the newest eligible pip for
  the resolver when "All newest" is selected;
- creates a derived source ZIP whose requirements-lock.txt exactly matches the selected /
  resolved versions;
- calculates that derived ZIP's SHA-256;
- generates an installer source with the exact payload filename and SHA-256 baked into it;
- resolver-checks the exact final dependency set again immediately before packaging;
- builds and smoke-tests Norm-Installer-1.4.1.exe;
- publishes exactly three normal install files to the output folder:
    Norm-Installer-1.4.1.exe
    Norm-0.52.5-portable-source.zip
    Norm-0.52.5-portable-source.zip.sha256

The generated installer
-----------------------
The normal installer GUI does not ask which source ZIP to use. The EXE is bound to the exact
Norm-0.52.5-portable-source.zip produced with that build, including its SHA-256.

The normal installer GUI only asks for:
1. Install/update folder (normally C:\Norm)
2. Base Python used to create/recreate the Norm .venv

Normal in-place updates preserve a compatible .venv, .ssh, user plugins, logs/state, and
local secrets. Package-owned source is synchronized and norm.exe is rebuilt normally.

Dependency selection
--------------------
"All newest (resolve)" is the safe global update button. pip, not the UI's per-row maximum,
determines the compatible version set. For example, if the newest mpmath violates SymPy's
constraint, the resolver keeps the newest mpmath version SymPy actually permits rather than
selecting an impossible pair and failing afterward.

The online column remains informational: it is the newest individually eligible stable/RC
release for that package. The selected/build column shows the globally resolved version.
Those two columns may therefore differ, and that is expected when another package constrains
the version.

"Use online (manual)" deliberately forces selected rows to the individual online version.
This is useful for experiments, but the final resolver check can reject the combination.

Norm 0.52.5
-----------
0.52.5 includes the proportional plan-verifier change: blocking execution defects can reject
a plan, while advisory plan-shape preferences do not automatically veto execution. Existing
inspected/reused mechanisms and execution tests can establish invariants instead of the
verifier inventing already-handled edge cases.
