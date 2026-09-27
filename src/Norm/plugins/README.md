# Norm plugins

Each non-hidden direct subfolder is a hot-swappable plugin. Public functions defined in non-underscore `.py` files are exposed to Norm as namespaced native tools. Prefix implementation-only files or functions with `_` to keep them private.

`init.py` / `__init__.py` may contain literal metadata (`NAME`, `VERSION`, `CAPABILITIES`, `DESCRIPTION`, `ENTRYPOINT`) and `README.md` may describe the capability, but neither is required for native hydration.

Plugin folders are persistent local state during normal installer upgrades. Package-shipped plugin files may be updated by a newer base, while unrelated local plugin folders are preserved. A full-backup restore intentionally restores the captured plugin tree.


Built-in package-managed plugin folders in 0.52.0:
- `backup` — source/full installer-compatible backups.
- `verbatim_lines` — exact UTF-8 append/insert plus private stdin CLI.
- `stegosplit_key` — password/map-key protected 256-bit key pair prototype.
- `stegosplit_message` — two-PNG UTF-8 message carrier.

These built-ins are synchronized by the installer; other plugin folders are local persistent state.
