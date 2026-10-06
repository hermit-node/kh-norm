# Norm

Canonical self-contained kit: **Norm 0.53.20 + reusable Installer 1.6.10-unified**.

## Root layout

- `Norm-Installer.py` / `installer_environment.py` — exact Installer 1.6.10 source from the original build workspace.
- `Norm-Installer.spec` — PyInstaller build recipe.
- `Build-Installer.py` / `Build-Installer.bat` — compile `dist\Norm-Installer.exe`.
- `Run-Installer.bat` — run the compiled installer when available, otherwise run the Python installer source.
- `Norm-0.53.20-portable-source.zip` — installer-ready Norm package.
- `Src/` — exact extracted contents of that ZIP.

## Build installer

Run `Build-Installer.bat`. The historical Installer 1.6.10 EXE SHA-256 is `8A23A8A93FD45A3EF72BF3F4E6735A333C3973E76825B2A91597527B7CDA80BE`. The exact source/spec are preserved; PyInstaller-generated EXE bytes can vary because build metadata is not deterministic.

## Install Norm 0.53.20

Run `Run-Installer.bat`. It explicitly uses `Norm-0.53.20-portable-source.zip`. Machine-local imprints, secrets and runtime state remain outside Git.
