# Norm

**Norm 0.52.0** is the local assistant/coordinator runtime maintained under `C:\Norm`. It plans bounded work, executes local tools and hot-loaded plugins, persists task/memory state in PostgreSQL, uses Redis for live queues/buffers, and returns normal English/Markdown.

## Canonical layout

```text
C:\Norm\
├─ core\                 runtime source + norm.exe
├─ config\               settings/runtime configuration
├─ docs\                 maintained Norm documentation
├─ plugins\              hot-swappable local capabilities
├─ .ssh\                 Norm-specific SSH keys/known_hosts (local, private)
├─ .venv\                reusable Python environment (generated, not backed up)
├─ tools\                build/operator/maintenance helpers
├─ logs\                 runtime logs
├─ state\                local runtime state
└─ (first-party exact writer is plugins\verbatim_lines\)

%USERPROFILE%\Documents\Norm\
├─ workspace\            durable generated artifacts and working files
└─ temp\                 disposable scratch/recovery material
   ├─ tasks\              task-scoped temporary work
   ├─ scratch\            one-off convenience scripts
   └─ recovery\           SOS/status-context recovery material
```

The installer treats `.venv`, `.ssh`, `plugins`, `logs`, and `state` as persistent local state during normal base updates. Package-managed files are synchronized in place: changed files are replaced and package-owned files removed from a newer base are deleted. A compatible `.venv` is reused.

## Runtime services

`config\settings.ini` is the canonical operator configuration. `[paths]` records the installed `runtime_root` (normally `C:\Norm`), and `[network]` defines service topology. Current defaults are Ollama `11434`, Norm HTTP/chat `12543`, activity/control `8766`, Redis `6379`, and PostgreSQL `25434`. Credentials are loaded from the configured external secrets file and are never included in the portable source package.

## Tool and plugin model

`core\norm_runtime\plugin_manager.py` automatically rescans `C:\Norm\plugins` before native tool schema use/dispatch. Public functions in non-underscore Python files become namespaced native tools; helper files/functions beginning with `_` stay private. Multi-file plugins and sibling imports are supported. If a changed plugin fails to load, the last-known-good hydrated version remains active and `.registry.json` records the refresh error.

First-party plugins currently ship under `plugins\backup`, `plugins\verbatim_lines`, `plugins\stegosplit_key`, and `plugins\stegosplit_message`. The two StegoSplit plugins bundle their Python implementation so a `.venv` rebuild no longer depends on an external editable checkout. The message codec is a two-image text carrier rather than encryption; the key-pair plugin is the password/map-key protected 256-bit key prototype.

The backup plugin supports two package types. `/backup` creates a portable installer/source ZIP without private state. `/backup full` creates a sensitive full-state ZIP containing source/runtime, docs, all plugins, `.ssh`, configured secrets, external workspace, selected recovery/log/state, PostgreSQL, and environment rebuild metadata. `.venv` itself is intentionally omitted. `/backup-zip` remains a legacy alias for `/backup full`.

## External workspace and temp policy

Use `%USERPROFILE%\Documents\Norm\workspace` for artifacts or working files that should survive normal maintenance. Use `%USERPROFILE%\Documents\Norm\temp` for disposable helper scripts, scratch/intermediate files, blocked-write staging, and recovery output. Task-scoped temp is removed only after durable terminal verification. Older scratch is age-cleaned by maintenance. SOS/recovery files are retained unless their referenced tasks are durably terminal.

`temp\recovery\SOS.md` is the current emergency recovery snapshot path. PostgreSQL remains the durable source of truth; Redis working/model buffers are best-effort interrupted-step context.

## Operator controls

The GUI recognizes `help`/`/help`, `/status`, `/status/busy`, `/queue-full`, `/new`, `/multi`, `/repeat-submission`, `/repeat-answer`, suppression/resume controls, `/backup`, `/backup full` (`/backup-zip` legacy alias), graceful shutdown, and emergency stop aliases. `GET /status/busy` is the authoritative live busy probe; `/status-context` builds a fresh handoff from maintained docs plus live runtime/Redis/PostgreSQL evidence.

## Build/update

`tools\build_norm.py` is the repeatable PyInstaller path. `package-manifest.json` defines the reusable installer contract. Normal updates should use the reusable Norm installer rather than deleting `C:\Norm`: it mirrors package-owned source files, preserves local/persistent state, reuses the venv when compatible, installs only missing/changed dependencies, and optionally rebuilds `core\norm.exe`.

## Documentation

- `docs\README.md` — operator overview.
- `docs\CURRENT_STATUS.md` — current source/layout facts and known limitations.
- `docs\RELEASE_NOTES.md` — promoted/source-release delta ledger.
- `docs\DEVELOPMENT_NOTES.md` — detailed engineering chronology and lessons.
- `docs\FUTURE_IMPLEMENTATION_NOTES.md` — active backlog/design notebook only.

Historical notes may mention earlier `app\` and Documents-root layouts. Those are historical records, not current paths.
