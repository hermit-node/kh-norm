# Norm Backup

Creates installer-compatible backup ZIPs without copying `.venv`.

- `create_backup(full=False)` — portable/source backup: package-managed Norm source, docs, and built-in plugins. No secrets, `.ssh`, PostgreSQL, workspace, logs/state, or user plugins.
- `create_backup(full=True)` / `create_full_backup()` — sensitive full-state backup: additionally captures all plugins, `.ssh`, configured secrets, external workspace, selected recovery material, logs/state, and PostgreSQL `norm_runtime`.

Both formats are consumed by the reusable installer. Full backups contain private keys and credentials and must be protected as secrets.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
