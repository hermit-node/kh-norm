# Norm Verbatim Lines

First-party exact-text writer for multiline or quote-heavy edits. Public native tools append or insert UTF-8 text without shell interpolation. Core `write_file` and `replace_text` retain their authorization/hash/backup/atomic-replace semantics but delegate exact temporary-file content creation to the plugin's private `_write_text` primitive, so there is no parallel core text-writer implementation and no unguarded model-facing overwrite tool. The private `src/_cli.py` preserves the stdin workflow used by `run_command` and operator scripts.

- `append_text(path, content)`
- `insert_text(path, content, line)`
- private `_write_text(path, content)` used only behind guarded core writes
- `run(payload)` legacy manual-broker compatibility (`append|insert`)

This capability is intentionally low-level. Callers remain responsible for target authorization, verification, and read-back.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
## Upgrade compatibility

The maintained private CLI is `src/_cli.py`. This package also includes a root `_cli.py` shim because older frozen Norm executables and older `settings.ini` files may still resolve `plugins\verbatim_lines\_cli.py` during an in-place upgrade. The shim delegates directly to the maintained schema-2 CLI; it is not a second implementation and is intentionally outside the `src/` identity hash. New runtime path loading accepts both locations.
