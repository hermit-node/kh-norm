# Norm 0.52.0 portable source package

This archive is the clean source baseline for installing or rebuilding Norm.

It intentionally contains **no `.venv` and no compiled `norm.exe`**. Those are generated during installation. The canonical source/executable directory is `core\`; the historical `app\` layout is no longer used.

Machine-specific persistent data and secrets are external to this package. `config\settings.ini` retains the current Norm service topology while runtime-owned paths are relocatable.

Use `package-manifest.json` as the installer's package contract.
## Dynamic plugins

Norm automatically hydrates local plugins from the configured `documents_root\plugins` directory. Public functions defined in non-hidden `.py` files become namespaced native tools and are rescanned/hot-reloaded without rebuilding `norm.exe`. Prefix helper files/functions with `_` to keep them private. Manifest `init.py`/`__init__.py` and `README.md` files remain supported as optional metadata. A failed plugin refresh leaves the last-known-good hydrated version active and records the error in `.registry.json`.

