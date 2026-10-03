# Norm File Read

Python-backed bounded reads using the same configured file-access policy as Norm's native file layer.

- `read_text(path, start_byte=0, max_bytes=0)` — UTF-8 chunk with continuation cursor.
- `read_lines(path, start_line=1, end_line=0, max_bytes=0, start_byte=0)` — bounded inclusive line range; oversized lines resume with `next_start_line` + `next_byte`.
- `read_bytes(path, start_byte=0, max_bytes=0)` — literal bytes returned as Base64.

Chunk defaults/maxima come from `[file_access]` in `config/settings.ini`. Secret files remain unreadable through this plugin.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
