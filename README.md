# kh-norm

Public installer/source distribution for **Norm 0.53.9**. The reviewable source tree is under `src/Norm`; the installer consumes the generated portable-source ZIP beside it.

This repository is intentionally split into:

- a generic, publishable Norm source package;
- Unified Installer **1.6.0**, which asks for deployment-specific host/port/path values;
- an optional local `norm-imprint.local.json` sidecar that auto-fills those non-secret values on later runs.

No deployment-specific passwords, tokens, private keys, OAuth credentials, SSH keys, or private CA material belong in this repository.

## Install

Keep these files together:

```text
Norm-Installer.py
Run-Norm-Installer.bat
Norm-0.53.9-portable-source.zip
Norm-0.53.9-portable-source.zip.sha256
norm-imprint.example.json
```

On Windows with Python 3.14 + Tkinter:

```text
Run-Norm-Installer.bat
```

The installer has four stages:

1. **Package** — automatically selects the highest valid `Norm-*-portable-source.zip` beside the installer.
2. **Environment** — hostnames, ports, paths, SSH convenience fields, and masked secret inputs.
3. **Install** — synchronized update, venv/dependencies, validation, and `norm.exe` build.
4. **Complete** — verified install summary.

The installer verifies the package companion SHA-256 before use.

## Local imprint

Copy the example once:

```powershell
Copy-Item .\norm-imprint.example.json .\norm-imprint.local.json
```

or fill the Environment page and use **Save non-secret norm-imprint.local.json**.

On the next launch, that local file is loaded automatically.

The imprint may contain non-secret operator configuration such as:

- install location and dependency mode;
- current machine/domain;
- Norm, Activity, Ollama, PostgreSQL, and Redis hosts/ports;
- PostgreSQL username/database/schema names;
- SSH convenience host/user/port settings;
- documents/workspace/temp paths;
- file-tool allowed roots;
- storage-context names/paths;
- `/network-map` never-probe rules and explicit active-probe targets.

The imprint is ignored by Git.

### Secrets are separate

Secret-like keys are rejected if they appear in an imprint.

The installer provides masked fields for values such as the PostgreSQL password. Those values are never written to the imprint or installer log. They are written only to Norm's configured external secrets file (`%APPDATA%\Norm\.env` with the public defaults).

For headless installs, secrets can be supplied through files rather than command-line values:

```powershell
python .\Norm-Installer.py --install --imprint .\norm-imprint.local.json --postgres-password-file .\postgres-password.txt
```

The password file itself must remain outside Git.

## Norm 0.53.9 network map

`/network-map` and `/network-map --json` are synchronous operator commands. They are intercepted before normal DB3 prompt enqueue and do not become Norm tasks.

The map uses passive `tailscale status --json` inventory. Active probes have a fail-closed policy:

- discovered peers are **not** automatically probed;
- only targets explicitly listed in `config/network-map.json` / the local imprint can be actively checked;
- honeypot/decoy name patterns are observed but marked `probe_policy=never`;
- resolved addresses are checked against `never_probe_cidrs` before connection;
- HTTP probes do not follow redirects to a second destination;
- there is no subnet sweep, ping sweep, or broad port scan.

For automation/SSH:

```text
C:\Norm\tools\norm-network-map.cmd --json
```

## Rebuilding the portable source ZIP

From the repository root:

```powershell
python .\tools\build_source_package.py
```

The script reads the version from `src/Norm/package-manifest.json`, rebuilds `Norm-<version>-portable-source.zip`, and regenerates its companion SHA-256.

## Building the Windows installer EXE

Run:

```text
Build-Norm-Installer-EXE.bat
```

The build uses a reusable Python 3.14 venv under:

```text
%LOCALAPPDATA%\Norm\InstallerBuilder\public-py3.14
```

It pins installer infrastructure to pip 26.2.1 and PyInstaller 6.22.3 and avoids reinstalling them when the cached builder is already correct.

## Requirements modes

**Newest available packages** derives package names from Norm's own exact lock and asks pip to resolve current non-prerelease versions accepted for the selected Python.

**Use package requirements** installs the exact `tools/requirements-lock.txt` shipped inside the selected Norm package.

Newest mode records `state/installer/resolved-requirements.txt` for audit/reproduction.

## Publishing

`Publish-To-GitHub.ps1` stages only the known public release files rather than using `git add .`. It refuses to stage local imprint/secrets/key material.

Authentication remains your normal Git/GitHub credential flow.

## Validation

The release bundle contains regression tests for:

- version-first package selection;
- package SHA verification;
- imprint merge/save behavior;
- rejection of secret-like imprint keys;
- secret/log separation;
- network-map honeypot/decoy probe policy;
- public-release private-topology/credential scan.

The Linux build environment can validate Python/source/package behavior but cannot perform the final Windows Tkinter/PyInstaller acceptance run.

## Bundled plugins

The 0.53.9 source carries forward the merged 0.53.8 plugin set, including `vision_parse`, `rotor5_cipher`, `backup`, `verbatim_lines`, `stegosplit_key`, and `stegosplit_message`.
