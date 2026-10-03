# Norm current status

Updated 2026-10-02 for the **0.53.11 maintenance source**; Installer **1.6.5-unified** remains unchanged. Live installation and installer payload are not promoted by this checkpoint.

## Implemented and verified in maintenance source

- Shared advisory validation generations, a rolling 24-hour window, bounded context, optimistic Redis concurrency protection, compact PostgreSQL snapshots/history, retryable completed-generation history, and live/durable count wording.
- Evidence-backed no-op recovery units; unchanged recovery handoff; deterministic read-after-write verification including exact-writer plugin mutation results.
- Nine per-plugin READMEs and explicit identity/checksum files; runtime and diagnostic verification; failed candidate reload keeps the loaded version.
- Regression and isolated Redis/PostgreSQL test evidence: [MAINTENANCE_VERIFICATION.md](MAINTENANCE_VERIFICATION.md). Operator semantics and plugin index: [README.md](README.md).

## Not promoted or claimed

The live executable, installer embedded source payload, and public GitHub worktree have not been updated by this maintenance checkpoint. Frozen-runtime/installer acceptance and live deployment remain separate work. No version bump is required by these source changes.

## Source/layout

- Version: **0.53.11**.
- Runtime root: `C:\Norm` (relocatable by installer); the installed absolute path is written to `config\settings.ini` `[paths].runtime_root`.
- Runtime source/executable directory: `core\`; compiled executable target is `core\norm.exe`.
- Maintained docs: `C:\Norm\docs`.
- Dynamic plugins: `C:\Norm\plugins`.
- Norm-specific SSH material: `C:\Norm\.ssh`.
- External documents root: `%USERPROFILE%\Documents\Norm`.
- Durable generated workspace: `%USERPROFILE%\Documents\Norm\workspace`.
- Disposable/recovery root: `%USERPROFILE%\Documents\Norm\temp`.
- `.venv` is generated/reusable and is intentionally omitted from source and full backup ZIPs.

## 0.53.9 mixed-turn context routing and unresolved bits

- The full user turn is still stored verbatim, but planning now operates on a separately preserved **primary task request**. Meaningful side information is typed as an **Ingrained Detail** instead of being silently discarded or misrepresented as another execution step.
- Confident details go directly to an existing durable final home: fact/terminology/correction, preference, decision, reusable constraint, future task/backlog item, assumption, or task-local context. Corrections can supersede an explicitly matched active memory.
- Only genuinely unplaced details enter PostgreSQL `unresolved_bits`; their temporary test-fit history lives in `unresolved_bit_trials`. There is no resolved graveyard. Promotion/application verifies the final write and then deletes the unresolved row, cascading its trial history.
- Later real tasks test at most `ingrained_details.test_limit` candidate bits. Lexically plausible bits are considered promptly; otherwise a deterministic exploratory sample occurs roughly every `explore_every_tasks` tasks. Each consideration records a stable task-domain label and outcome.
- Default irrelevant-bit garbage collection requires at least 15 trials across at least 3 task domains, zero useful trials, and only one user mention. Repeatedly mentioned bits are protected. Age by itself is not evidence for deletion.
- Background-memory condensation includes unresolved-bit state/trial aggregates so uncertain context can be compressed alongside the rest of surviving PostgreSQL memory without being prematurely promoted.
- GUI DB3 ingress now carries its `prompt_id` through the chat API into durable task-plan provenance. `/queue` and `/queue-full` join that ID to the newest matching running task/child and expose the actual current task/step; raw prompt previews are only used before a durable task exists or while an item is merely queued.

## 0.53.7 large-source/task-storage contract

Source files no longer have a Norm-imposed size ceiling. `read_file` streams arbitrarily large UTF-8 files through a 24 MiB processing buffer and returns at most 384 KiB per tool result, with `next_byte` / `next_start_line` continuation metadata. Full-file SHA-256 is automatic for sources within one processing buffer and optional for larger sources so reading the first chunk of a very large file does not first require hashing the entire object.

Large-source work has three independent limits: 3 GiB processed per task pass, 54 GiB of Norm-owned task working storage, and 5 MiB per internal Markdown note file. Hitting the 3 GiB pass ceiling creates a continuation ZIP and parks the same task through the existing suppression/resume machinery; manual resume keeps the task identity and lifetime progress but resets the pass counter. The 54 GiB storage ceiling also checkpoints before refusing further task-local growth. Source files referenced outside task temp do not count as copied task storage.

Terminal cleanup is lineage-aware. Task-local image-analysis variants and other reproducible cache are disposable once the terminal result verifies. Norm retains a compact manifest containing source identity, transformation recipes, and asset lineage plus internal Markdown extraction/summary notes under `workspace\.norm-task-retention\<task_id>`. Durable user-requested artifacts written outside task temp are not swept merely because a task ended. Human-facing replies are limited to 384 KiB while the full durable terminal summary remains available; oversized replies are additionally written under `workspace\large-responses`.

## 0.53.6 recovery-state cleanup and work-item provenance

`/inject-context` is wired to the live worker again. Without an explicit `--task`, the control path targets the worker's current active task first, then the oldest queued task only as a between-step fallback. Context is persisted in `task_context_injections` and delivered at the next model boundary without creating a new task.

- Weekly maintenance and both manual `/memory-condense` modes now run recovery-state cleanup before memory condensation.
- Recovery notes for an entirely terminal task tree are deleted once every surviving task in the tree has a verified terminal summary.
- A nonterminal recovery tree is considered dangling only when it has no live Redis task membership, is not suppressed, and every nonterminal member is older than `memory.recovery_stale_hours` (default 24 hours). Dangling trees are summarized from task state plus recovery notes into `task_history`, replay-validated, and only then hard-pruned; failed validation preserves the raw task tree.
- Orphan `task_evidence_archive` / legacy `task_step_archive` rows are deleted only when a validated `task_history` record already covers the task ID. Uncovered orphan archives remain preserved and are counted in maintenance results.
- Existing `request_type` / `prompt_origin` provenance is now enforced in worker model envelopes. Norm-generated work is explicitly marked internal/non-user-authored, while the true original user prompt remains separate. Runtime child/recovery tasks no longer overwrite `original_user_prompt` with their generated instruction.
- Cleanup bounds are configurable with `memory.recovery_stale_hours` (24) and `memory.recovery_cleanup_max_trees` (20 dangling trees per pass). Terminal-note cleanup is not capped by that dangling-tree limit.

## 0.53.5 suppression and rebuild tuning

- `/suppress-task` now falls back to canonical Redis DB3 ingress when a prompt is dispatching/planning but no durable PostgreSQL task row exists yet. It tombstones the active prompt ID (or oldest queued prompt), cancels an active pre-task model call when needed, and lets the ingress layer park the submission instead of retrying it.
- The PyInstaller runtime build cache now lives under `state\build-cache\pyinstaller` and survives normal release staging cleanup. `tools\build_norm.py` no longer forces `--clean`; pass `--clean` explicitly only when a cold rebuild is wanted.
- The cryptography pin was advanced to 50.0.2 so a working newer cryptography installation is not downgraded back to the former 46.0.4 pin during this release.

## Built-in capabilities and plugin hydration

First-party package-managed plugin subtrees are `plugins\\backup`, `plugins\\verbatim_lines`, `plugins\\stegosplit_key`, `plugins\\stegosplit_message`, `plugins\\rotor5_cipher`, `plugins\\vision_parse`, `plugins\\file_read`, `plugins\\soft_delete`, and `plugins\\postgres_pool`. User-added plugin folders remain persistent and are not deleted by normal base updates. The StegoSplit key and message sources are bundled privately inside their plugin folders, eliminating the old editable-install dependency. Rotor5 is now a separate first-party plugin rather than being embedded in the StegoSplit message codec.


`cryptography 50.0.2` and `psycopg-pool 3.3.3` are locked runtime/build dependencies and the PyInstaller build explicitly collects cryptography/cffi so ChaCha20-Poly1305 remains available inside the frozen `norm.exe` plugin host.

Plugins are first-class native tools. Norm rescans the plugin tree before schema use and dispatch, supports multi-file/sibling imports, hot-reloads changed code without an executable rebuild, and keeps the last-known-good loaded capability when a new edit fails to import. The legacy `tools\norm_plugins.py` remains a diagnostic/manual broker.

## Operator metadata and manual memory condensation

- `/about` is available in both operator prompt surfaces and reports current project/version, runtime/executable paths, source/frozen mode, Python version, package schema/type, and plugin-root/count information.
- `/memory-condense` schedules an incremental consolidated background-memory refresh inside the existing worker when idle. `/memory-condense -full` rebuilds the consolidated snapshot from the full surviving PostgreSQL source set. Neither command creates a user task or new task UUID.
- This command is intentionally not the deferred destructive `/condense-memories` design: it does not hard-prune curated memories, delete history, or treat age as a deletion reason. Existing checkpointed condensation machinery is reused and maintenance outcomes are written to PostgreSQL.

## Task/memory integrity

0.52.2 includes defensive task-lineage repair around UUID/dependency migration and referentially safe pruning. Surviving children are reparented to a defensible surviving ancestor or detached rather than left with dangling task UUIDs. Memory-thread recovery preserves intact links first, then source-message mapping, then conservative grouping of genuinely orphaned memories. `tools\repair_norm_state.py` provides an offline repair path when an older executable cannot start.

## Temporary workspace cleanup

Disposable work belongs under `Documents\Norm\temp`. `temp\tasks\<task_id>` is eligible for removal only after the task is durably terminal and its terminal summary verifies. General scratch is age-cleaned. `temp\recovery\SOS.md` and related recovery files are retained until referenced tasks are durably terminal; uncertain recovery material is preserved rather than guessed disposable. Weekly maintenance records cleanup results in its maintenance note.

## Backups

The first-party `plugins\backup` capability creates both a portable source backup (`/backup`) and a **sensitive full-backup ZIP** (`/backup full`) that the reusable installer can consume. It includes package/source files, maintained docs, all plugins, `.ssh`, configured secrets, workspace contents, selected recovery state, PostgreSQL `norm_runtime`, and environment rebuild metadata. It excludes `.venv`, PyInstaller staging/build output, Python caches, and routine disposable scratch.

Portable base/source ZIPs remain secrets-free and safe to treat separately from private backups. `/backup-zip` remains a compatibility alias for `/backup full`.

## Installer behavior

The reusable installer performs an in-place managed sync. Changed package files are replaced and package-owned files no longer present are removed. Persistent local directories (`.venv`, `.ssh`, plugins, logs/state) survive normal base updates. Existing compatible venvs are reused. Recommended Installer 1.6.5 binds one exact source payload by filename and SHA-256 and can build a derived payload from locked or newest eligible stable/RC dependency versions. Alpha, beta, and dev releases are excluded. Full-backup packages additionally restore their private state payload.

Installer 1.6.5 additionally snapshots existing configuration before synchronization, migrates legacy PostgreSQL identity to normal settings, pre-fills environment values from the installation before imprint/defaults, and retains secrets in masked fields. Successful installs persist non-secret choices to the private local imprint; the public example stays generic. The Environment connection check uses only configured endpoints. These are recovered installer-source features, not newly tested installer UI claims.

## Current validation boundary

Source-level syntax/config/package validation is part of release packaging. Actual service health still depends on the target machine's Ollama, Redis, PostgreSQL, Tailscale/network authority, secrets, SSH material, and local CUDA/Python environment. A source package validation must not be described as proof that those external services are healthy.

## 0.51.5 compatibility corrections in this source

The 0.52.x compatibility pass retained the intentional 0.51.5 DB3 durable ingress, quiet-driven busy probing, secret redaction, protocol-v2/UUID lineage, PostgreSQL-before-Redis terminal cleanup, bounded recovery, Tailscale authority, model-buffer/SOS recovery, and task-tree suppression/resume behavior. The prior audit's remaining GUI suppression gap was closed by checking suppressed prompt IDs before primary HTTP dispatch; duplicate suppression helpers/checks were removed.

Queued workers now obtain `runtime.json` through the same resolved `runtime_bootstrap.load_config()` path as the host, so `{runtime_root}` and other placeholders cannot remain literal. Image analyzer Python/script validation is lazy: missing image-specific dependencies fail an image-analysis call, not ordinary text work.

Emergency stop now passes the real runtime root into the SOS writer and supplies its emergency output directory separately. This prevents `state\emergency-stop` from being mistaken for a second Norm runtime root.


## 0.53.1 operator console launcher

`norm.exe --service` is launched headlessly with Windows `CREATE_NO_WINDOW` plus a hidden startup window, so the service process no longer leaves an inert console on the desktop. `Run-Norm.bat` still opens the three operator surfaces, but **Norm Runtime** prefers a separate Windows Terminal (`wt.exe`) window for the more compact/refined terminal host and falls back to the classic console when Windows Terminal is unavailable. Norm Prompt and Norm Replies keep their existing explicit console behavior. The Runtime stream already exits when `norm.exe` exits, so shutting Norm down also closes that terminal naturally.

## 0.52.6 proportional plan verification

Plan verification now reserves rejection for blocking coverage/safety/boundedness/verification defects. The runtime build-tool lock uses PyInstaller 6.22.3. Explicit, tightly coupled implementation parts may share a bounded step; advisory decomposition/style concerns are accepted. Existing mechanisms and execution-time invariant tests are treated as evidence instead of inviting speculative edge-case objections.

## 0.52.4 canonical prompt ingress

The local Rich console and SSH prompt GUI now use the same Redis DB3 ingress namespace (`norm:gui:ingress`, `norm-gui-dispatchers`, `norm:gui:thread-id`) and the same core dispatcher. Both use project `default`. A normal queued prompt is not acknowledged merely because `/api/chat` returned HTTP 200: the dispatcher requires a durable `task_id`, otherwise the prompt is parked visibly in uncertain state without automatic replay.

Startup resources now share one cleanup boundary: if PostgreSQL migration, prompt-queue initialization, conversation-store setup, worker startup, or chat binding fails, any already-started activity/chat server and worker are stopped before the exception is allowed to retry/escape. PostgreSQL normal-start connection/schema work sits inside the three-attempt retry policy and uses `connect_timeout=5`; the prior duplicate preflight outside that loop is gone. Plugin refresh/hydration/execution uses one process-global reentrant lock because those operations temporarily mutate global Python import/stdio state.

## 0.52.3 service signal isolation

The Windows launcher now starts `norm.exe --service`. Service mode ignores and logs Ctrl+C/Ctrl-Break console events so a control event originating from a helper/console cannot silently terminate an otherwise healthy runtime. Explicit control endpoints remain authoritative for operator shutdown. A manually-invoked `norm.exe` without `--service` retains normal KeyboardInterrupt handling.

## 0.52.2 startup/control and thread navigation

- Activity/control port `8766` is started before schema migration and reports `status=initializing` with a startup phase until chat/worker activation. `/status/busy` stays busy during this interval so GUI dispatch does not mistake initialization for idle time.
- Chat/API readiness still requires durable initialization; port `12543` is not exposed as healthy until the runtime is ready.
- `/new [name]`, `/thread-list`, and `/thread-resume <name|id>` are queue-ordered console controls. Named threads are created durably in PostgreSQL; the switch itself is queued so older queued prompts remain attached to the thread they preceded.
- `/flush-suppressed` reconciles the durable task ledger with GUI delivery state: suppressed/parked uncertain records are discarded as well as suppressed task rows, while an in-flight prompt keeps a temporary retry-block tombstone until its socket dispatch finishes.
- Visible help lists only current canonical commands. Legacy aliases remain accepted silently for compatibility.


## 0.53.1 aiohttp transport

Chat and activity/control HTTP are served by pinned aiohttp 3.14.3 on dedicated asyncio loops; on Windows those socket-only server threads explicitly use SelectorEventLoop to avoid Proactor accept-loop listener loss. Existing coordinator lifecycle semantics remain compatible; blocking runtime callbacks are offloaded with asyncio.to_thread. SSE disconnects are benign transport events.

### 2026-09-30 same-version startup cleanup correction
- `core\norm_runtime\prompt_worker.py` imports `pathlib.Path` at module scope. Startup temp cleanup and verified terminal task-temp cleanup therefore execute the existing 0.53.7 retention policy instead of failing with `NameError` and preserving all temp material.

- PDF semantic parsing: first-party `plugins\vision_parse` uses PyMuPDF rendering + local Ollama vision, up to four pages per call with continuation; rendered-page evidence overrides garbled text-layer extraction.
