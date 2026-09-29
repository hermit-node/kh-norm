# Norm

**Norm 0.53.3** is the local assistant/coordinator runtime maintained under `C:\Norm`. It plans bounded work, executes local tools and hot-loaded plugins, persists task/memory state in PostgreSQL, uses Redis for live queues/buffers, and returns normal English/Markdown.

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

The runtime lock includes `cryptography 46.0.4`/`cffi 2.0.0`, and `tools\build_norm.py` explicitly collects those packages into the one-file executable so dynamic plugins can use ChaCha20-Poly1305 from the frozen process.

`core\norm_runtime\plugin_manager.py` automatically rescans `C:\Norm\plugins` before native tool schema use/dispatch. Public functions in non-underscore Python files become namespaced native tools; helper files/functions beginning with `_` stay private. Multi-file plugins and sibling imports are supported. If a changed plugin fails to load, the last-known-good hydrated version remains active and `.registry.json` records the refresh error.

First-party plugins currently ship under `plugins\backup`, `plugins\verbatim_lines`, `plugins\stegosplit_key`, `plugins\stegosplit_message`, and `plugins\rotor5_cipher`. The StegoSplit plugins bundle their Python implementation so a `.venv` rebuild no longer depends on an external editable checkout. `stegosplit_message` is the two-image authenticated carrier; `rotor5_cipher` is an independent optional pre-encoding layer; `stegosplit_key` remains the password/map-key protected 256-bit key prototype.

The backup plugin supports two package types. `/backup` creates a portable installer/source ZIP without private state. `/backup full` creates a sensitive full-state ZIP containing source/runtime, docs, all plugins, `.ssh`, configured secrets, external workspace, selected recovery/log/state, PostgreSQL, and environment rebuild metadata. `.venv` itself is intentionally omitted. Older backup aliases remain accepted for compatibility but are not advertised in help.

## External workspace and temp policy

Use `%USERPROFILE%\Documents\Norm\workspace` for artifacts or working files that should survive normal maintenance. Use `%USERPROFILE%\Documents\Norm\temp` for disposable helper scripts, scratch/intermediate files, blocked-write staging, and recovery output. Task-scoped temp is removed only after durable terminal verification. Older scratch is age-cleaned by maintenance. SOS/recovery files are retained unless their referenced tasks are durably terminal.

`temp\recovery\SOS.md` is the current emergency recovery snapshot path. PostgreSQL remains the durable source of truth; Redis working/model buffers are best-effort interrupted-step context.

## Operator controls

The GUI recognizes `help`/`/help`, `/about`, `/status`, `/status/busy`, `/queue-full`, `/multi`, `/repeat-submission`, `/repeat-answer`, suppression/resume controls, `/backup`, `/backup full`, `/memory-condense`, `/memory-condense -full`, `/new [name]`, `/thread-list`, `/thread-resume <name>`, graceful shutdown, and emergency stop controls. `/memory-condense` incrementally refreshes the consolidated PostgreSQL background-memory snapshot; `-full` rebuilds that snapshot from the complete surviving source set. Both are maintenance requests handled by the existing worker when idle and never create a user task. The stronger destructive curated-memory `/condense-memories` design remains intentionally separate and unimplemented. Legacy aliases remain accepted but are intentionally omitted from visible help. `GET /status/busy` is the authoritative live busy probe; `/status-context` builds a fresh handoff from maintained docs plus live runtime/Redis/PostgreSQL evidence.

## Build/update

`tools\build_norm.py` is the repeatable PyInstaller path. `package-manifest.json` defines the reusable installer contract. Normal updates should use the reusable Norm installer rather than deleting `C:\Norm`: it mirrors package-owned source files, preserves local/persistent state, reuses the venv when compatible, installs only missing/changed dependencies, and optionally rebuilds `core\norm.exe`. Installer 1.4.10 binds one exact source payload by filename and SHA-256. Its builder reads this package's requirements lock and can create a derived payload using either the locked version or the newest eligible stable/release-candidate version for each package; alpha, beta, and dev builds are excluded. The package schema remains 1.

## Documentation

- `docs\README.md` — operator overview.
- `docs\CURRENT_STATUS.md` — current source/layout facts and known limitations.
- `docs\RELEASE_NOTES.md` — promoted/source-release delta ledger.
- `docs\DEVELOPMENT_NOTES.md` — detailed engineering chronology and lessons.
- `docs\FUTURE_IMPLEMENTATION_NOTES.md` — active backlog/design notebook only.

Historical notes may mention earlier `app\` and Documents-root layouts. Those are historical records, not current paths.


## 0.53.1 operator console launcher

`norm.exe` remains detached in service mode. `Run-Norm.bat` now starts three explicit visible operator consoles (Prompt, Runtime, Replies) through a small console host. If a helper exits during startup, that console stays open and shows the exit/error instead of disappearing.

## 0.52.6 proportional plan verification

The independent plan verifier now rejects only blocking execution defects. Cohesive bounded steps may contain multiple tightly coupled implementation parts when their roles and verification are explicit. Advisory organization/style concerns no longer veto a plan, and the verifier is instructed not to invent hypothetical implementation failures when the plan explicitly inspects/reuses an existing mechanism or verifies the invariant during execution. Repair cycles must make the smallest material correction instead of mechanically splitting cohesive work or returning the same rejected plan.

## 0.52.4 canonical prompt ingress

Local Rich-console prompts and SSH `P` prompts now share one Redis DB3 ingress stream/group/thread key and the same core dispatcher implementation. Both submit to project `default`. The dispatcher establishes an explicit current thread before chat submission and only acknowledges a normal prompt after `/api/chat` returns a durable `task_id`; HTTP 200 without task creation is parked as a visible protocol violation rather than silently discarded.

Startup ownership is fail-closed: the activity API, worker, and chat API are all released if any later startup stage fails, so a bounded PostgreSQL retry cannot collide with a leaked `:8766` listener. Normal startup no longer performs a PostgreSQL preflight outside the retry loop, generated PostgreSQL conninfo uses a 5-second connection timeout, and plugin hydration/execution is serialized process-wide around Python's global import/stdio state.

## 0.52.3 service-start stability

`Run-Norm.bat` launches `norm.exe --service`. In service mode, stray Windows Ctrl+C/Ctrl-Break events are logged and ignored instead of being interpreted as operator shutdown. Canonical shutdown remains `/shutdown`, `/shutdown now`, `/stop-all`, or `/stop-all now` through the control API. Directly running `norm.exe` without `--service` retains normal KeyboardInterrupt behavior for debugging.

## 0.52.2 operator/startup refinements

The activity/control API starts before PostgreSQL schema initialization and reports an explicit `initializing` phase until the worker/chat runtime is ready. This keeps `/status/busy` and shutdown/stop controls reachable during bounded schema waits without treating a half-started runtime as healthy. Conversation consoles support queue-ordered thread controls: `/new [name]`, `/thread-list`, and `/thread-resume <name-or-id>`.
`/flush-suppressed` removes suppressed task records and their parked GUI delivery records together; active socket dispatches retain only the hidden retry-block tombstone needed to prevent a late connection reset from recreating the prompt.


## 0.53.1 aiohttp transport

Chat and activity/control HTTP are served by pinned aiohttp 3.14.3 on dedicated asyncio loops. Existing coordinator lifecycle semantics remain compatible; blocking runtime callbacks are offloaded with asyncio.to_thread. SSE disconnects are benign transport events.
