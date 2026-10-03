# Norm Verbatim Lines

First-party exact-text writer for multiline or quote-heavy edits. Public native tools append or insert UTF-8 text without shell interpolation. The private `src/_cli.py` preserves the stdin workflow used by `run_command` and operator scripts.

- `append_text(path, content)`
- `insert_text(path, content, line)`
- `run(payload)` legacy manual-broker compatibility

This capability is intentionally low-level. Callers remain responsible for target authorization, verification, and read-back.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
