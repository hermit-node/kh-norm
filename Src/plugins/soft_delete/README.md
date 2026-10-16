# Norm Soft Delete

Reversible, batch-oriented trash management.

Physical files live in one flat `Documents\Norm\trashbin`. Redis is the hot metadata index and `trash-index.json` is rewritten once per meaningful batch, restore, purge, or reconciliation operation.

Public tools:
- `soft_delete(path, reason) ? file or entire folder`
- `delete_list() ? includes files, directories and held orphan folders`
- `restore_delete(deletion_id)` (`all` is supported)
- `delete_files()`
- `reconcile_trash()`

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.

Per-capability write additions can be configured centrally with `[file_access_overrides] soft_delete.write_add`.


The /soft-delete "PATH" operator command calls the protected deletion queue
directly (not via N2). /delete-list, /restore-delete ID and /delete-files
operate on the same index. Orphan folders with no known original path appear
as held records and are not silently restored or purged.
