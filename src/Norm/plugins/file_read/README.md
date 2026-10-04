# Norm File Read

Python-backed bounded reads using the same configured file-access policy as Norm's native file layer.

- `read_text(path, start_byte=0, max_bytes=0)` — UTF-8 chunk with continuation cursor.
- `read_lines(path, start_line=1, end_line=0, max_bytes=0, start_byte=0)` — bounded inclusive line range; oversized lines resume with `next_start_line` + `next_byte`.
- `read_bytes(path, start_byte=0, max_bytes=0)` — literal bytes returned as Base64.
- `archive_manifest(path)` — non-extracting tree, sizes, packed sizes, CRC/metadata, encryption flags, and a normalized tree+size fingerprint.
- `archive_member(path, member, ...)` — bounded selective member read without extracting to disk.
- `archive_member_sha256(path, member)` — streaming SHA-256 for one member.
- `archive_compare_directory(path, directory)` — compare path tree + uncompressed sizes first, then SHA-256 only when the structures are a likely exact match.

Archive handling prefers a local `7z.exe` / `7zz` / `7za` backend (including common ZIP/7z/RAR/TAR/compressed formats). Standard ZIP/TAR archives fall back to Python stdlib when 7-Zip is unavailable. Norm never implicitly extracts an archive to disk.

Chunk defaults/maxima come from `[file_access]` in `config/settings.ini`. Secret files remain unreadable through this plugin.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
