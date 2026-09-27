# Norm current status

Updated 2026-09-27 for the clean **0.52.0** portable source line.

## Source/layout

- Version: **0.52.0**.
- Runtime root: `C:\Norm` (relocatable by installer); the installed absolute path is written to `config\settings.ini` `[paths].runtime_root`.
- Runtime source/executable directory: `core\`; compiled executable target is `core\norm.exe`.
- Maintained docs: `C:\Norm\docs`.
- Dynamic plugins: `C:\Norm\plugins`.
- Norm-specific SSH material: `C:\Norm\.ssh`.
- External documents root: `%USERPROFILE%\Documents\Norm`.
- Durable generated workspace: `%USERPROFILE%\Documents\Norm\workspace`.
- Disposable/recovery root: `%USERPROFILE%\Documents\Norm\temp`.
- `.venv` is generated/reusable and is intentionally omitted from source and full backup ZIPs.

## Built-in capabilities and plugin hydration

First-party package-managed plugin subtrees are `plugins\backup`, `plugins\verbatim_lines`, `plugins\stegosplit_key`, and `plugins\stegosplit_message`. User-added plugin folders remain persistent and are not deleted by normal base updates. The StegoSplit key and MessageCodec sources are bundled privately inside their plugin folders, eliminating the old editable-install dependency.


Plugins are first-class native tools. Norm rescans the plugin tree before schema use and dispatch, supports multi-file/sibling imports, hot-reloads changed code without an executable rebuild, and keeps the last-known-good loaded capability when a new edit fails to import. The legacy `tools\norm_plugins.py` remains a diagnostic/manual broker.

## Task/memory integrity

0.52.0 includes defensive task-lineage repair around UUID/dependency migration and referentially safe pruning. Surviving children are reparented to a defensible surviving ancestor or detached rather than left with dangling task UUIDs. Memory-thread recovery preserves intact links first, then source-message mapping, then conservative grouping of genuinely orphaned memories. `tools\repair_norm_state.py` provides an offline repair path when an older executable cannot start.

## Temporary workspace cleanup

Disposable work belongs under `Documents\Norm\temp`. `temp\tasks\<task_id>` is eligible for removal only after the task is durably terminal and its terminal summary verifies. General scratch is age-cleaned. `temp\recovery\SOS.md` and related recovery files are retained until referenced tasks are durably terminal; uncertain recovery material is preserved rather than guessed disposable. Weekly maintenance records cleanup results in its maintenance note.

## Backups

The first-party `plugins\backup` capability creates both a portable source backup (`/backup`) and a **sensitive full-backup ZIP** (`/backup full`) that the reusable installer can consume. It includes package/source files, maintained docs, all plugins, `.ssh`, configured secrets, workspace contents, selected recovery state, PostgreSQL `norm_runtime`, and environment rebuild metadata. It excludes `.venv`, PyInstaller staging/build output, Python caches, and routine disposable scratch.

Portable base/source ZIPs remain secrets-free and safe to treat separately from private backups. `/backup-zip` remains a compatibility alias for `/backup full`.

## Installer behavior

The reusable installer performs an in-place managed sync. Changed package files are replaced and package-owned files no longer present are removed. Persistent local directories (`.venv`, `.ssh`, plugins, logs/state) survive normal base updates. Existing compatible venvs are reused; pip only changes dependencies as needed. Full-backup packages additionally restore their private state payload.

## Current validation boundary

Source-level syntax/config/package validation is part of release packaging. Actual service health still depends on the target machine's Ollama, Redis, PostgreSQL, Tailscale/network authority, secrets, SSH material, and local CUDA/Python environment. A source package validation must not be described as proof that those external services are healthy.

## 0.51.5 compatibility corrections in this source

The 0.52.0 compatibility pass retained the intentional 0.51.5 DB3 durable ingress, quiet-driven busy probing, secret redaction, protocol-v2/UUID lineage, PostgreSQL-before-Redis terminal cleanup, bounded recovery, Tailscale authority, model-buffer/SOS recovery, and task-tree suppression/resume behavior. The prior audit's remaining GUI suppression gap was closed by checking suppressed prompt IDs before primary HTTP dispatch; duplicate suppression helpers/checks were removed.

Queued workers now obtain `runtime.json` through the same resolved `runtime_bootstrap.load_config()` path as the host, so `{runtime_root}` and other placeholders cannot remain literal. Image analyzer Python/script validation is lazy: missing image-specific dependencies fail an image-analysis call, not ordinary text work.

Emergency stop now passes the real runtime root into the SOS writer and supplies its emergency output directory separately. This prevents `state\emergency-stop` from being mistaken for a second Norm runtime root.
