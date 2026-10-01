# Norm 0.53.8 portable source package

This archive is the clean source baseline for installing or rebuilding Norm.

It intentionally contains **no `.venv` and no compiled `norm.exe`**. Those are generated during installation. The canonical source/executable directory is `core\`; the historical `app\` layout is no longer used.

Machine-specific persistent data and secrets are external to this package. `config\settings.ini` retains the current Norm service topology while runtime-owned paths are relocatable.

Use `package-manifest.json` as the installer's package contract.
## 0.53.8 Ingrained Details and unresolved bits

Norm now interprets a mixed user turn into one actionable primary request plus optional **Ingrained Details** without altering the verbatim conversation message. Confident details are routed directly to existing durable homes (fact, preference, decision, constraint, task/backlog, assumption, or current-task context); only genuinely unplaced details enter PostgreSQL `unresolved_bits`. `unresolved_bit_trials` records later bounded attempts to fit those bits to real tasks. Promotion/application removes the temporary unresolved state; broad repeated irrelevance can garbage-collect a singly-mentioned bit after the configured trial/domain thresholds. Background-memory condensation sees unresolved state and aggregate trial evidence.

DB3 prompt IDs are also carried through the chat API into durable task provenance, allowing `/queue` and `/queue-full` to report the actual matched task/current step rather than treating the first characters of the original mixed message as the runtime activity description.

## 0.53.7 large-source streaming and semantic task storage

Native `read_file` no longer rejects a source merely because the whole file is larger than a small byte cap. Sources may be arbitrarily large within the filesystem; reads stream through a 24 MiB processing buffer and return bounded model-facing chunks with byte/line continuation cursors. A 3 GiB per-pass processing allowance checkpoints and parks the same task for manual resume, while Norm-owned task working storage is capped at 54 GiB. Internal Markdown extraction/summary notes rotate at 5 MiB per physical file, and human-facing replies are capped at 384 KiB without truncating the durable task summary.

Task-local image-analysis derivatives now live under task temp storage and are treated as reproducible cache. On verified terminal cleanup Norm keeps compact source/asset lineage and internal Markdown notes under the durable workspace retention area, but deletes reproducible brightness/contrast/analysis variants rather than preserving duplicate bytes. Source files are referenced by path/size/mtime/hash when practical and are not copied merely for task storage accounting.

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

Package-managed built-in plugin subtrees are `plugins\backup`, `plugins\verbatim_lines`, `plugins\stegosplit_key`, `plugins\stegosplit_message`, and `plugins\rotor5_cipher`. Unrelated user plugins remain persistent across normal installer updates. The two StegoSplit plugins bundle their implementation rather than depending on an editable external checkout.

`/backup` creates portable/source installer media; `/backup full` creates the sensitive private-state format; `/backup-zip` is the legacy full-backup alias. `/memory-condense` performs a safe incremental background-memory snapshot refresh and `/memory-condense -full` rebuilds that snapshot from the full surviving source set without creating a user task. `/condense-memories` remains intentionally absent until the complete fail-closed curated-memory housekeeping policy is implemented.


## 0.53.1 operator console launcher

`norm.exe --service` is launched headlessly with Windows `CREATE_NO_WINDOW` plus a hidden startup window, so the service process no longer leaves an inert console on the desktop. `Run-Norm.bat` still opens the three operator surfaces, but **Norm Runtime** prefers a separate Windows Terminal (`wt.exe`) window for the more compact/refined terminal host and falls back to the classic console when Windows Terminal is unavailable. Norm Prompt and Norm Replies keep their existing explicit console behavior. The Runtime stream already exits when `norm.exe` exits, so shutting Norm down also closes that terminal naturally.

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


## 0.53.6 recovery cleanup and prompt-origin enforcement

- Restores the existing `/inject-context` architecture end-to-end: current active task resolution, PostgreSQL persistence, and next-model-boundary delivery.

Weekly cleanup and manual `/memory-condense` now run a recovery-state cleanup before rebuilding background memory. Verified terminal task trees shed obsolete `task_recovery_notes`; stale nonterminal trees with no live Redis membership are summarized from their task/recovery state into compact `task_history`, replay-validated, and only then pruned. Orphan archive rows are deleted only when a validated compact history record already covers their task ID; uncovered orphans are preserved and reported.

The worker now enforces the existing `request_type`/`prompt_origin` provenance instead of treating every queued instruction as user-authored text. Norm-generated steps/recovery/verifier work are explicitly labelled internal in the model envelope, runtime child tasks inherit the true original user prompt, and generated child instructions no longer overwrite `original_user_prompt`.

## 0.53.5 tuning

This source adds ingress-level `/suppress-task` fallback for prompts that are still dispatching before task creation, reuses PyInstaller analysis state under `state\build-cache\pyinstaller`, and advances the cryptography pin to 50.0.2.
