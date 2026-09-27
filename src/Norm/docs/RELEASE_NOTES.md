# Norm Release Notes

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
