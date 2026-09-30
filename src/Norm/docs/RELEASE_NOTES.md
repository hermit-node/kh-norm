## 0.53.7 — 2026-09-30 — large-source streaming and semantic task storage

- Removed the whole-source `read_file` size gate. Large UTF-8 sources are streamed with byte/line continuation cursors; the source itself is not limited by the model-result cap.
- Reinterpreted `tools.max_read_bytes` as a 24 MiB processing buffer and added a separate 384 KiB per-tool-result ceiling so source size, processing window, and model-facing payload are distinct. Large sources skip full-file SHA-256 by default and expose a stable size/mtime/path fingerprint; callers can explicitly request a full SHA scan.
- Added task storage accounting under `temp\tasks\<task_id>` with a 54 GiB managed-storage ceiling and 3 GiB per-pass processing allowance. Reaching the processing ceiling writes a continuation ZIP and parks/suppresses the same task for explicit manual resume; resume resets only the per-pass counter.
- Added `append_task_note` for durable internal Markdown extraction/summary notes with automatic 5 MiB physical-file rotation. Aggregate logical notes remain unconstrained except by the task storage ceiling.
- Task-scoped image-analysis derivatives are now written under task temp as reproducible cache rather than long-lived workspace clutter. Verified terminal cleanup retains compact source/recipe lineage and internal notes in `.norm-task-retention` while deleting reproducible derivative bytes.
- Human-facing completed replies are capped at 384 KiB. The full durable PostgreSQL task summary remains intact, and oversized full replies are also spilled to `workspace\large-responses`.

## 0.53.6 — 2026-09-30 — recovery-state hygiene and prompt provenance

- Restored `/inject-context` end-to-end: the activity API now receives a live injection callback, injections resolve to the current active task tree, persist in PostgreSQL, and are consumed by the worker at subsequent model-call boundaries without creating a new task.
- Injected text is explicitly framed as a user-authored context interjection, while generated steps/recovery/verifier work remain Norm-internal.

- Weekly maintenance and manual `/memory-condense` now run recovery-state cleanup before background-memory condensation.
- Fully terminal task trees with verified summaries automatically drop obsolete `task_recovery_notes`; recovery notes no longer accumulate indefinitely after they have served their handoff purpose.
- Stale nonterminal recovery trees are eligible only when absent from live Redis, not suppressed, and older than the configured stale threshold. Norm summarizes task state plus recovery notes into compact `task_history`, replay-validates that summary, and only then prunes the dangling tree. Any summarize/replay failure preserves the original rows.
- Added conservative orphan archive cleanup: `task_evidence_archive` and legacy `task_step_archive` rows are deleted only when a validated compact-history record already covers the missing task; uncovered orphan archives remain preserved for inspection.
- Enforced the existing `request_type` / `prompt_origin` provenance in worker prompts. Norm-generated steps, recovery jobs, verification, and maintenance are explicitly labelled internal and cannot masquerade as user-authored prompts.
- Runtime child/recovery tasks now inherit the true `original_user_prompt`; generated child instructions are retained separately instead of overwriting the user request.
- Added `memory.recovery_stale_hours=24` and `memory.recovery_cleanup_max_trees=20` defaults. The dangling-tree cap does not limit cheap terminal-note cleanup.

## 0.53.5 — 2026-09-30 — pre-task suppression and incremental rebuild cache

- `/suppress-task` now covers the canonical DB3 ingress gap before a durable task UUID exists: it suppresses the currently dispatching prompt first, otherwise the oldest live queued prompt, and prevents uncertain retry resurrection.
- A dispatching pre-task prompt cancellation also cancels the active Ollama call so planning/generation does not continue after the operator suppressed it.
- Runtime PyInstaller work/spec state now persists under `state\build-cache\pyinstaller`; normal builds reuse analysis state and no longer pass `--clean`. `tools\build_norm.py --clean` remains available for deliberate cold rebuilds.
- Updated the cryptography lock from 46.0.4 to 50.0.2.

## 0.53.4 — 2026-09-30 — suppression/cancellation reliability

- Fixed mid-execution suppression acknowledgement to use the actual Redis Streams entry ID rather than the logical task-node UUID. This prevents `XACK` failures after a model generation is cancelled for `/suppress-task`.
- Normalized intentional Ollama response-close races into `ModelGenerationCancelled` without retaining the underlying `http.client` exception chain, so deliberate cancellation no longer surfaces misleading `NoneType.peek` transport tracebacks.
- Applied the same cancellation normalization to text generation, tool-chat generation, and vision streaming.
- Operator consoles now render intentional model cancellation as a yellow cancellation notice instead of a red Ollama error; genuine failures remain red.
- Suppression remains durable-first and preserves the queue entry in Redis after acknowledgement, matching the existing pre-execution suppression path.

## 0.53.3 — 2026-09-29 — about command and taskless manual memory condensation

- Added `/about` to both operator prompt surfaces with current project/version, runtime/executable path, source/frozen mode, Python, package, and plugin summary.
- Added `/memory-condense` for incremental background-memory snapshot refresh and `/memory-condense -full` for a full-source snapshot rebuild.
- Manual memory condensation is scheduled directly on the existing worker and runs only when that worker reaches idle; it does not enqueue a user prompt, create a new task UUID, or re-plan user work.
- Reused the checkpointed `DeepHistoryMaintainer.rebuild_background_snapshot()` path so interrupted condensation can resume safely. The manual operation records a PostgreSQL maintenance note and leaves the stronger destructive `/condense-memories` concept unimplemented.
- Updated visible `/help` output in both prompt consoles.

## 0.53.2 — 2026-09-28 — cipher/plugin separation and frozen cryptography support

- Split the optional five-machine Rotor5 message transform into its own first-party `plugins\rotor5_cipher` capability; `stegosplit_message` is again only the two-image carrier.
- Rotor5 R5E2 uses five independently derived 256-symbol rotor machines, a fresh per-message nonce, pre-rotor compression, and HMAC-SHA256 envelope authentication.
- Added optional `NORM_ROTOR5_SECRET` and `NORM_ROTOR5_PREVIOUS_SECRETS` values in the configured Norm `.env`; only those two already-redacted secret values are exported into the Norm process for the hot-loaded Rotor5 plugin.
- Added `cryptography==46.0.4`, `cffi==2.0.0`, and `pycparser==3.0` to the reproducible environment lock.
- PyInstaller now explicitly collects `cryptography`, `cffi`, and `_cffi_backend`, allowing the frozen `norm.exe` process to hydrate the ChaCha20-Poly1305 StegoSplit plugin without relying on imports from an external editable checkout.
- `stegosplit_message` 0.3.0 exposes typed native tools for text, Base64 bytes, files, password rotation, cover reconstruction, authenticated pair info, and differential stats while retaining the legacy `run(payload)` wrapper.

## 0.53.1 — 2026-09-28 — aiohttp control plane

- Replaced the hand-rolled ThreadingHTTPServer/socketserver chat and activity/control transports with aiohttp.web.
- Preserved the coordinator-facing lifecycle contract so startup/shutdown ordering remains stable.
- Blocking conversation, PostgreSQL-facing, context, and control callbacks run via asyncio.to_thread rather than blocking the HTTP event loops.
- Activity SSE is asyncio-native and treats client disconnect/reset/WinError 10053/10054 as normal stream termination.
- Pinned aiohttp==3.14.3 in runtime and installer locks.
- Retained loopback/Tailscale-only bind validation.
- Added bounded graceful AppRunner cleanup and explicit event-loop/default-executor shutdown.

# Norm release notes

## 0.53.1 — 2026-09-28 — ingress retry deduplication

- Serialized uncertain-prompt requeue by durable uncertain-record identity so duplicate retry timers cannot create multiple live Redis deliveries for one prompt.
- Requeue now atomically adds the replacement ingress entry and removes its uncertain source record.
- `/queue` and `/queue-full` now exclude acknowledged-but-preserved historical stream rows from the LIVE QUEUE view while failing open if consumer-group metadata is unavailable.
- Added regression coverage for concurrent retry contenders and live-queue historical-row filtering.

## 0.53.1 — 2026-09-27 — launcher ownership/readiness fix

- Restored the proven `cmd.exe start` path for the three operator consoles after both Norm health endpoints are ready.
- Removed `start_operator_consoles.py` from the normal `Run-Norm.bat` startup path.
- Detached service creation now returns and reports the owned `norm.exe` PID instead of discarding the process handle immediately.
- A pre-existing `norm.exe` is treated as an attach/wait condition; a process launched by the current helper is not subsequently described as a second instance.
- Startup timeout messaging now reports readiness failure without falsely claiming that the just-launched Norm process is a duplicate.


## 0.53.1 — 2026-09-27 — restore explicit operator consoles

- `norm.exe` remains detached in `--service` mode.
- `Run-Norm.bat` launches `Norm Prompt`, `Norm Runtime`, and `Norm Replies` as three explicit persistent consoles via `tools\start_operator_consoles.py`.
- Each console stays open if its Python helper exits during startup, so startup errors remain visible.
- Duplicate GUI-helper mutex detection prints which operator console is already running rather than silently exiting.

## 0.52.6 — 2026-09-27 — detached service launch + HTTP ownership hardening

- `Run-Norm.bat` no longer launches `norm.exe` in a transient/minimized helper console. It uses the shared detached service launcher instead.
- Every automatic `norm.exe` start now passes `--service`; the prior GUI fallback path could start manual-mode Norm and leave it vulnerable to console `KeyboardInterrupt` events.
- Run-Norm refuses to launch a duplicate `norm.exe` merely because HTTP is temporarily unavailable. An existing process is given the health window instead.
- Service mode now installs both Python signal handlers and a native Windows `SetConsoleCtrlHandler` guard for Ctrl+C/Ctrl+Break.
- The visible Ollama GIN server (`/api/tags`, `/api/generate`, `/api/chat`) is not Norm's `12543` chat API; Norm health is established only by the configured chat and activity `/health` endpoints.
- GUI DB3 ingress now catches per-entry dispatch/bootstrap exceptions instead of letting the background dispatcher thread die; enqueueing a later prompt also revives a dead dispatcher thread while preserving queued DB3 entries.

## 0.52.5 — 2026-09-27 — proportional plan verification

- Plan verification rejects only blocking defects: lost constraints, unauthorized/unsafe actions, materially contradictory or impossible designs, missing material verification, unbounded work, or missing final synthesis on multi-step plans.
- Clearly described tightly coupled implementation parts may remain in one bounded step; advisory organization/granularity concerns no longer veto execution.
- Redundant mechanisms are only blocking when they create a real contradiction, ambiguity, or user-constraint violation.
- The verifier no longer invents hypothetical implementation/boundary failures when the plan explicitly inspects/reuses an existing mechanism or includes execution tests for that invariant.
- Repair prompts require the smallest material fix and prohibit mechanically returning the same rejected plan.
- Runtime build tooling lock moves PyInstaller from 6.22.2 to 6.22.3.

# Norm Release Notes

## 0.52.4 — 2026-09-27 — canonical prompt ingress and startup reliability

- Local Rich-console prompts and SSH `P` prompts now use the same Redis DB3 ingress stream, consumer group, selected-thread key, default project, and core dispatcher implementation.
- Removed the second `norm:rich-console:*` durable ingress namespace. Frontends differ only in transport/UI; prompt interpretation and task creation no longer diverge.
- The canonical dispatcher creates/selects an explicit thread before `/api/chat`, so an accepted console prompt cannot be converted into a semantic-routing clarification before task creation.
- HTTP 200 is no longer sufficient to acknowledge a normal queued prompt: the response must contain a durable `task_id`. A protocol violation is parked visibly in uncertain state without automatic replay.
- Added ingress lifecycle logging for prompt/source/thread/task IDs to make queue-to-task handoff auditable.
- Put the activity API, worker, chat API, and their server threads under one startup ownership/cleanup boundary so a failed migration or later startup stage releases ports before retry.
- Removed the normal-start duplicate PostgreSQL health preflight; PostgreSQL connection/schema failures now occur inside the bounded startup retry loop.
- Added `connect_timeout=5` to generated PostgreSQL connection strings in addition to the existing lock/statement timeouts.
- Serialized plugin refresh/hydration/execution with one process-global `RLock`, covering global `sys.modules`, `sys.path`, stdout, and stderr mutations across multiple `PluginManager` instances.

This file is the concise, version-by-version record of **implemented and promoted changes** in Norm. It answers “what changed in this iteration?” rather than “what is true right now?”

Rules:
- Add an entry for every promoted version or promoted letter revision.
- List shipped behavior/configuration/architecture changes, migration notes, and release verification that materially define that version.
- Do not use this file for unfinished designs or backlog items; those belong in `FUTURE_IMPLEMENTATION_NOTES.md`.
- Do not use it as the live-state authority; current facts belong in `CURRENT_STATUS.md`.
- Detailed incidents, experiments, lessons, and superseded implementation paths remain in `DEVELOPMENT_NOTES.md`.
- Internal staging labels that were never promoted are not separate releases here.

## 0.52.3 — 2026-09-27 — Windows service signal isolation

- `Run-Norm.bat` now launches `norm.exe --service`.
- Service mode installs explicit SIGINT/SIGBREAK handlers that log and ignore stray Ctrl+C/Ctrl-Break events instead of treating them as shutdown requests.
- Canonical operator shutdown remains routed through the activity/control API; direct manual `norm.exe` launches retain normal KeyboardInterrupt behavior.
- This addresses a live 0.52.2 failure where the runtime reached ready state and then exited on unsolicited `KeyboardInterrupt` without any logged `/shutdown` or `/stop-all` request.

## 0.52.2 — 2026-09-27 — startup controls, thread navigation, help cleanup

- Activity/control API now starts before bounded PostgreSQL/schema initialization and reports an explicit initializing phase until chat/worker readiness.
- `/status/busy` remains reachable and reports busy during initialization, preventing GUI dispatch from treating schema startup as idle.
- Added queue-ordered `/new [name]`, `/thread-list`, and `/thread-resume <name|id>` controls to GUI and Rich consoles.
- Named threads are created in PostgreSQL while the switch itself remains ordered in Redis ingress, preserving the thread assignment of older queued prompts.
- Visible help now lists canonical commands only; legacy aliases continue to be accepted for backward compatibility.
- Installer 1.3.4 reuses a compatible `.venv`, allows pip to upgrade to any available release satisfying `pip>=26.1`, retains `.ssh` as protected persistent state, and tightens the GUI layout.
- `/flush-suppressed` now clears both suppressed PostgreSQL task records and the matching parked/suppressed GUI delivery records; in-flight prompt-ID tombstones remain until the HTTP dispatch is no longer active so a late connection reset cannot resurrect the submission.

## 0.52.1 — 2026-09-27 — bounded startup/schema hardening

- Added PostgreSQL schema-migration lock and statement timeouts plus serialized advisory migration locking.
- Hardened startup failure logging and retry behavior so stale PostgreSQL transactions cannot wait indefinitely.
- This release retained the 0.52.0 portable layout, plugin, queue, recovery, and installer contract.

## 0.52.0 — 2026-09-27 — portable source, native plugins, integrity recovery, reusable installation

- Standardized the source/executable directory on `core\` and removed stale `app\` assumptions from the portable package/build path.
- Added reusable package-manifest-driven installation with in-place managed sync and compatible `.venv` reuse.
- Promoted dynamic plugin folders to native hot-reloaded tools with multi-file imports and last-known-good fallback; plugin root is now `C:\Norm\plugins`.
- Added `/queue-full` support and explicit `/status/busy` GUI recognition alongside `/status`.
- Hardened UUID/dependency migration and pruning against deleted-parent lineage; added conservative memory-thread recovery plus `tools\repair_norm_state.py`.
- Moved maintained docs into `C:\Norm\docs`, Norm SSH material to `C:\Norm\.ssh`, and external generated work into `Documents\Norm\workspace` / disposable `Documents\Norm\temp`.
- Added conservative temp cleanup: verified terminal task temp can be removed immediately; old scratch is age-cleaned; unresolved recovery/SOS material is preserved.
- Added a first-party backup plugin design producing installer-compatible private full-state ZIPs with source/docs/plugins/.ssh/secrets/workspace/PostgreSQL and environment rebuild metadata while excluding `.venv`.
- Added explicit `[paths].runtime_root`; the reusable installer binds it to the actual target directory after every source/full-backup sync.
- Fixed queued-worker runtime placeholder expansion by using the shared resolved runtime config, and made image analyzer Python/script validation lazy so plain text work is independent of optional image tooling.
- Fixed emergency-stop SOS path resolution by separating the Norm runtime root from the emergency snapshot output root.
- Preserved/fixed the 0.51.5 GUI suppression safeguards: queued suppressed prompt IDs are rejected before primary dispatch, duplicate suppression checks/helpers were removed, and Redis XRANGE queue inspection never passes `COUNT 0`.
- Added self-contained first-party `stegosplit_key`, `stegosplit_message`, and `verbatim_lines` plugins. The MessageCodec no longer depends on an editable external checkout.
- Added `/backup` (portable source), `/backup full` (sensitive full state), and kept `/backup-zip` as a full-backup compatibility alias.
- This entry describes the 0.52.0 clean source/release package. Machine-specific service health must still be verified after installation.


## 0.51.5 - 2026-09-24 - offline rebuild and helper fixes

- Promoted executable SHA-256: `A3F0714DB6E281086563D8E5D9EECE95AEF449204CAFBDDC21D5CD4AA436E635`. Retained version 0.51.5; 0.51.6 was not assigned.
- Included structural tool-output/evidence/history/log redaction and protected credential-file reads.
- Graceful-stop waiting output reduced to one message; existing worker event remains the completion signal.
- External GUI dispatcher uses quiet-driven busy checks with a slow fallback; existing DB3 fixes remain.
- External runtime viewer separates control messages from partial model lines; model-buffer/SOS storage was not changed.
- Added explicit editable StegoSplit restoration instructions for environment rebuilds.
- Validation: source compilation/offline tests, packaged help/version, bundle inspection, read-only persistence audit and synthetic recovery/render replay. No service start or end-to-end runtime health claim for this revision.

## 0.51.4 — 2026-09-22 — Tailscale authority and centralized network topology

- Replaced the old `[ports]` settings model with `[network]`, including `current_machine`, `current_domain`, per-service host selectors, ports, and `require_tailscale`.
- Central runtime resolution now derives Redis, prompt/deletion/console queues, PostgreSQL, `stocks_api`, Norm HTTP, activity/control, and Ollama endpoints from the settings resolver instead of duplicating live network values in `runtime.json`.
- Moved environment-specific PostgreSQL database/user/password values to the configured external `.env` file; runtime JSON no longer carries the connection string.
- PostgreSQL runtime and `stocks_api` use `khzz.boga-dace.ts.net:25434`; Redis authority uses `khzz.boga-dace.ts.net:6379`; Norm HTTP/activity bind to KHzz’s Tailscale address; Ollama intentionally remains loopback-only.
- Added Tailscale Serve TCP forwarding for Redis so Memurai remains bound to `127.0.0.1:6379` while Norm reaches it only through the tailnet authority path.
- Added a pre-tool-call authority gate: each native Norm tool call must successfully reach/PING the configured Tailscale Redis authority endpoint before execution proceeds.
- Promoted live executable SHA-256: `0EE7EA56B866F0DC6CC3B54C04A4549A97F76DC4C9CCD968CD13D6D7137CC671`; `norm.exe --version` reports `0.51.4`.
- Post-promotion companion-script repair updated the external GUI helpers to use the same 0.51.4 endpoint/config resolver instead of the removed `[ports]` section and raw `runtime.json` connection fields; this did not require replacing the packaged executable.
- 2026-09-23 companion-script hardening kept the packaged 0.51.4 executable unchanged: Redis-backed replies tolerate malformed legacy bytes, large replies bypass monolithic Markdown rendering, `Run-Norm.bat` uses the central settings resolver/noninteractive-safe startup wait, and GUI `/stop-all now` waits for the live Redis model-buffer to settle, writes Redis task state/events + raw model buffers + PostgreSQL checkpoints to `SOS.md`, fsync/read-back verifies it, then force-terminates Norm/Ollama.
- 2026-09-23 external capability extension kept the packaged executable unchanged: `tools\norm_plugins.py` plus the workspace `plugins\` directory provide filesystem-discovered Python plugins with README/capability matching, version/SHA/UUID identity, fresh per-call loading, and `run_command` access. A one-time process restart is required only for an already-running worker to pick up the new broker instruction; plugin swaps themselves require no rebuild/restart.
- The same 2026-09-23 maintenance pass repaired `tools\norm_backup.py` to use the centralized 0.51.4 settings/runtime resolver, added optional checkpoint labels, excluded runtime `state\file-backups` from ZIPs, and removed one-off convenience/probe/write helpers. A requested `0.52.5` backup label is a snapshot/checkpoint identifier, not a promoted executable release.
- A stale PostgreSQL `idle in transaction` session was found blocking startup schema DDL. Clearing the stale transaction allowed both Tailscale-bound APIs (`12543` and `8766`) to start normally. The underlying startup-lock hardening remains a separate unresolved item, not a claimed 0.51.4 fix.

## 0.51.3 — 2026-09-21 — Nonblocking durable GUI ingress

- Normal `norm_gui.bat` prompts became durable Redis DB3 ingress instead of blocking the prompt window on `/api/chat`.
- Added a background dispatcher that submits the oldest queued GUI prompt through `12543` while activity/control and immediate stop remain independently reachable on `8766`.
- DB3 preserves GUI thread/submission/answer state and queued ingress across Norm restart; interrupted dispatches are quarantined as uncertain instead of replayed automatically.
- `/repeat-submission` and `/repeat-answer` requeue the latest applicable persisted turn verbatim.
- Rich-console ingress received a separate DB3 stream/group namespace so frontends do not steal each other’s entries.
- Promoted executable SHA-256: `2409ad004cd2c2d17937e35d37e7bd415728e7d94a3d04f714692465fe5239a2`.

## 0.51.2b — 2026-09-21 — Unified maintenance correction

- Unified regular memory cleanup, deep-history cleanup selection, and derived image-output purge behind one idle scheduler.
- PostgreSQL `runtime_state` became the durable authority for maintenance cadence; Redis retains only the active crash/restart marker.
- Interrupted maintenance resumes the same recorded mode after restart; successful completion clears the active marker only after durable PostgreSQL success state is written.
- Build tooling gained promoted letter-revision support such as `0.51.2b`.
- Promoted executable SHA-256: `f3710205b3bbcf78e68d3c72eb9d0f7c742677cec19cba77c78d46135136db37`.

## 0.51.2 — 2026-09-20 — Weekly derived-image cleanup (superseded by 0.51.2b)

- Added an independent seven-day idle cleanup pass for derived `images\analysis` output with workspace-boundary validation.
- Added crash-visible Redis cleanup markers and `/status-context` visibility for incomplete cleanup state.
- First live cleanup removed 1,359 derived files / 873,675,067 bytes while preserving source/original images.
- This scheduler design was subsequently corrected and unified in promoted revision `0.51.2b`.
- Promoted executable SHA-256: `332f38e05a31fd4740ac23c0ad91cf60895b574889490f3b76b5be9e7f009b8b`.

## 0.51.1 — 2026-09-20 — Runtime/workspace split and reproducible backup

- Moved the runtime to `C:\Norm` while keeping the model-editable workspace at `C:\Users\KHzz\Documents\Norm`; startup validates that the two roots do not overlap.
- Normal and recovery-child tools receive the configured workspace plus approved external roots, while the runtime tree itself is not model-writable.
- Added `/backup-zip` with PostgreSQL `norm_runtime`, workspace, runtime, manifest, and restore helpers while excluding disposable build/cache output.
- Removed the large rebuildable `.venv` from backups and added pinned Python/CUDA-Torch/dependency rebuild settings and documentation.
- Pruned obsolete build/staging artifacts after preserving the external rollback snapshot.
- Promoted executable SHA-256: `FB5E6E2BB7668D1C2CBD1CAEC60F356D193A50B70B208DD0E2B50C56CEF4BBF4`.

## 0.51.0 — 2026-09-20 — Versioned build, recovery scheduling, and opaque runtime identity

- Made `settings.ini` the canonical project metadata/document-pointer source and added repeatable `tools\build_norm.py` PyInstaller release tooling.
- Deferred append/follow-up planning now waits for the predecessor task to complete and pass final verification before generating the real follow-up plan.
- Oversized-recovery child replacement gained bounded retry handling without repeatedly consuming the parent retry budget.
- Added immutable task/node/dependency UUID identity (`task_uuid`, `node_id`, `edge_id`) and backfilled existing PostgreSQL runtime records while preserving readable compatibility IDs.
- Dependency/append/recovery linkage now uses opaque identities where available, with legacy lookup retained for compatibility.
- GUI companion windows were hardened for graceful runtime shutdown and real Windows Ctrl+C/Ctrl+Break behavior.
- The final promoted 0.51.0 executable after same-version fixes had SHA-256 `A72D7FC8E0268CB42A1F062BB63EFCAA4904B159330972D878779738A2DDAEC6`.

## Earlier development baseline — 2026-09-13 through 2026-09-19

Norm was already under active development before formal `0.51.x` release naming. That work established the PostgreSQL/Redis coordinator model, step-aware planning/verification, cancellation safety, temporary thinking persistence, targeted PostgreSQL pruning, shell evidence capture, busy-state reporting, `/status-context`, and the first GUI/operator shell. Detailed chronology remains in `DEVELOPMENT_NOTES.md`; these pre-version milestones are not presented as invented releases.
