# Norm Backup

Creates installer-compatible backup ZIPs without copying `.venv`.

- `create_backup(full=False)` — portable/source backup: package-managed Norm source, docs, and built-in plugins. No secrets, `.ssh`, PostgreSQL, workspace, logs/state, or user plugins.
- `create_backup(full=True)` / `create_full_backup()` — sensitive full-state backup: additionally captures all plugins, `.ssh`, configured secrets, external workspace, selected recovery material, logs/state, and PostgreSQL `norm_runtime`.

Both formats are consumed by the reusable installer. Full backups contain private keys and credentials and must be protected as secrets.
