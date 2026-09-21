# Rebuilding the Norm Python environment

Norm backup ZIPs intentionally exclude `C:\Norm\.venv` because the environment is reproducible and is dominated by the CUDA PyTorch package.

The preferred recovery path is `restore_norm_backup.bat`. Its PowerShell installer reads `backup-manifest.json` and the restored `config\settings.ini`, then:

1. Looks for a compatible Python 3.14 installation.
2. If Python is missing, tries `winget` first.
3. If needed, downloads the configured Python installer from `python.org`.
4. Creates `C:\Norm\.venv`.
5. Installs the configured CUDA PyTorch build from the configured PyTorch wheel index.
6. Installs the pinned packages from `tools\requirements-lock.txt`.
7. Validates the vision/runtime imports before continuing.

Current environment settings live in `config\settings.ini` under `[environment]`. Do not archive or copy an old `.venv` as the normal restore strategy.
