# Norm Verbatim Lines

First-party exact-text writer for multiline or quote-heavy edits. Public native tools append or insert UTF-8 text without shell interpolation. The private `_cli.py` preserves the stdin workflow used by `run_command` and operator scripts.

- `append_text(path, content)`
- `insert_text(path, content, line)`
- `run(payload)` legacy manual-broker compatibility

This capability is intentionally low-level. Callers remain responsible for target authorization, verification, and read-back.
