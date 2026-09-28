# Norm 0.53.1 portable source package

This archive is the clean source baseline for installing or rebuilding Norm.

It intentionally contains **no `.venv` and no compiled `norm.exe`**. Those are generated during installation. The canonical source/executable directory is `core\`; the historical `app\` layout is no longer used.

Machine-specific persistent data and secrets are external to this package. `config\settings.ini` retains the current Norm service topology while runtime-owned paths are relocatable.

Use `package-manifest.json` as the installer's package contract.
## Dynamic plugins

Norm automatically hydrates local plugins from the configured runtime `plugins\` directory. Public functions defined in non-hidden `.py` files become namespaced native tools and are rescanned/hot-reloaded without rebuilding `norm.exe`. Prefix helper files/functions with `_` to keep them private. Manifest `init.py`/`__init__.py` and `README.md` files remain supported as optional metadata. A failed plugin refresh leaves the last-known-good hydrated version active and records the error in `.registry.json`.


## PostgreSQL integrity recovery

Startup UUID migration now repairs stale task lineage before rebuilding dependency edges. Exact surviving relationships are preferred, archived task history is used to locate the nearest surviving ancestor, and irrecoverable parents are explicitly detached instead of crashing startup. Deep-history pruning repairs surviving child lineage before deletion. Conversation pruning preserves memory-to-thread links before deleting source messages. Any remaining unthreaded memories are first remapped from surviving source-message links and then conservatively grouped into clearly labelled recovered threads; uncertain singletons remain separate.

`tools\repair_norm_state.py --runtime-root C:\Norm` can run the same repair logic against an existing installation whose current EXE cannot start.


## Current layout

Maintained docs ship under `docs\`. Local plugins and Norm SSH material live under `plugins\` and `.ssh\` inside the runtime root. `%USERPROFILE%\Documents\Norm\workspace` is durable generated work; `%USERPROFILE%\Documents\Norm\temp` is disposable scratch/recovery state with conservative automated cleanup.

The first-party `plugins\backup` capability creates a **sensitive** installer-compatible full backup containing runtime/source, docs, plugins, `.ssh`, configured secrets, workspace, selected recovery state, PostgreSQL, and environment rebuild metadata. `.venv` itself remains excluded.


## 0.51.5 compatibility pass and built-in plugins

`config\settings.ini` explicitly carries `paths.runtime_root`; portable media stores `.` and the installer rewrites the installed copy to its actual target (normally `C:\Norm`). Queued workers use the same resolved runtime configuration as the host, and optional image-analysis Python dependencies are validated only when image analysis is called.

Package-managed built-in plugin subtrees are `plugins\backup`, `plugins\verbatim_lines`, `plugins\stegosplit_key`, and `plugins\stegosplit_message`. Unrelated user plugins remain persistent across normal installer updates. The two StegoSplit plugins bundle their implementation rather than depending on an editable external checkout.

`/backup` creates portable/source installer media; `/backup full` creates the sensitive private-state format; `/backup-zip` is the legacy full-backup alias. `/condense-memories` is intentionally absent until the complete fail-closed curated-memory housekeeping policy is implemented.


## 0.53.1 operator console launcher

`norm.exe` remains detached in service mode. `Run-Norm.bat` now starts three explicit visible operator consoles (Prompt, Runtime, Replies) through a small console host. If a helper exits during startup, that console stays open and shows the exit/error instead of disappearing.

## 0.52.6 proportional plan verification

The independent verifier now distinguishes blocking execution defects from advisory plan-shape/style concerns. Explicitly bounded cohesive work may remain together, and existing mechanisms plus execution-time tests are treated as evidence rather than invitations for speculative rejection. Repair cycles must materially address the reported blocker.

## 0.52.4 canonical console/SSH ingress

The local Rich console and SSH prompt GUI are producers/consumers of the same Redis DB3 ingress stream, group, and selected-thread key, with dispatch implemented once in `core\norm_runtime\prompt_ingress.py`. `tools\norm_gui_dispatch.py` is only a compatibility import shim. Both frontends use project `default`, and a normal ingress entry is acknowledged only after the chat response contains a durable task ID.

Startup resources are owned transactionally at the process level: activity server, worker, and chat server are initialized inside one cleanup boundary and released on every startup failure before the outer retry. Normal startup no longer performs a PostgreSQL health probe outside the retry loop; generated PostgreSQL conninfo carries `connect_timeout=5`. Dynamic plugin hydration and execution are serialized by a process-global `RLock` because Python import state and stdout/stderr redirection are global.

## 0.52.3 Windows service mode

`Run-Norm.bat` starts the packaged runtime with `--service`. In that mode Ctrl+C/Ctrl-Break console events are ignored and logged; explicit control endpoints own shutdown. This isolates the minimized service process from accidental control events generated while using or closing companion console windows. Manual `norm.exe` invocation without `--service` keeps the prior KeyboardInterrupt behavior.

## 0.52.2 console/startup behavior

The activity/control API is reachable while PostgreSQL/schema initialization is still running, but advertises `initializing` rather than `ok` until chat/worker activation. GUI and Rich consoles support queue-ordered `/new [name]`, `/thread-list`, and `/thread-resume <name|id>` thread navigation. Legacy command aliases remain accepted but are omitted from current help text.
Suppressed operator cleanup is two-layer: `/flush-suppressed` removes suppressed task rows plus matching parked GUI delivery records, while preserving only any in-flight retry tombstone required to prevent a late socket failure from requeuing the prompt.


## 0.53.1 aiohttp transport

Chat and activity/control HTTP are served by pinned aiohttp 3.14.3 on dedicated asyncio loops. Existing coordinator lifecycle semantics remain compatible; blocking runtime callbacks are offloaded with asyncio.to_thread. SSE disconnects are benign transport events.
