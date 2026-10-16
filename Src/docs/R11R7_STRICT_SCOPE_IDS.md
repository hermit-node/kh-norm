# R11R7: strict recovery-scope IDs

Based on r11r6 Workspace Stability and r11r5 Celery Orphan Guard.
Changes only core/norm_runtime/prompt_worker.py and package metadata.

- When a parent explicitly scopes item 1, N2 should output scope_items:[1].
- Windows directory and UNC paths belong in unit instructions, never scope_items.
- Explicitly empty recovery-unit scopes remain empty, avoiding accidental
  reconstruction of numeric IDs from incidental instruction text.
- Strict positive integer validation, parent-scope subset checks and overlap
  restrictions are preserved.
- No files are renamed/moved by this source package or its tests.
- Installed runtime needs a deliberate restart to load patched Python.

Offline validation runs test_r11r7_strict_scope.py and existing suites.
Live model behavior still requires observation, without repeating prior
file operations solely because the recovery metadata was malformed.
