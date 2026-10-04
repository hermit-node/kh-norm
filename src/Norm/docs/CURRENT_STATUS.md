# Norm current status

Updated 2026-10-04 for **Norm 0.53.14** with Installer **1.6.6-unified**. This file describes only the current package state. Historical changes belong in `RELEASE_NOTES.md`.

## Runtime and end-of-task durable state

- End-of-task durable summary generation is enabled. Structured summary generation is silent; after it is saved, the complete summary is published once as an `End-of-task durable summary` runtime block.
- The durable thread/runtime summary is a **current-state projection**, not a compressed transcript. It advances from `covers_through_message_id`, may shrink, and removes superseded version/status/fact chains rather than retaining them as history.
- `Superseded information`, embedded `Recent messages`, and conversation-log sections are invalid durable-summary shapes. Invalid model output is retried with an explicit prune instruction; a second invalid/failing result writes the deterministic current-state fallback and advances coverage instead of freezing an older summary.
- Durable-memory extraction runs before summary refresh so same-turn corrections/supersession are visible to the replacement summary.
- Normal Ollama thinking/answer fragments publish as they arrive rather than waiting for newline or 1024-character display buffers.
- The model-stream guard detects both severe token loops and repeated coherent 32-token phrases, so long sentence/paragraph repetition is stopped instead of streaming indefinitely.
- `memory.consolidation_batch_chars=14000` is a separate maintenance batching limit and does not control runtime-stream cadence or end-of-task summary size.

## Prompt interpretation and task fidelity

- The full user turn is stored verbatim for provenance.
- Norm still interprets intent, command class, reusable facts, corrections, preferences, decisions, constraints, assumptions, future tasks, and task-local context.
- Planning uses a primary executable request derived conservatively from the user turn. Only exact, clearly separable sidecar spans such as explicit asides/non-task parentheticals may be removed from executable wording.
- Ordinary task sentences and embedded qualifiers remain intact. A phrase such as `end of task summary` cannot be reduced to `task summary` merely because individual words look removable.
- Material marked as current-task context remains in the task even when it is also durable/reusable.
- Confident durable corrections may supersede explicitly matched active memories. Genuinely unplaced details use PostgreSQL `unresolved_bits` plus `unresolved_bit_trials`; resolved/promoted items are removed rather than kept in a resolved graveyard.

## Source and runtime layout

- Package version: **0.53.14**.
- Runtime root: `C:\Norm` by default and relocatable through Installer 1.6.6; the resolved installation path is stored in `config\settings.ini` `[paths].runtime_root`.
- Runtime source/executable directory: `core\`; compiled executable target: `core\norm.exe`.
- Maintained docs: `C:\Norm\docs`.
- Dynamic plugins: `C:\Norm\plugins`.
- Norm SSH material: `C:\Norm\.ssh`.
- External documents root: `%USERPROFILE%\Documents\Norm`.
- Durable generated workspace: `%USERPROFILE%\Documents\Norm\workspace`.
- Disposable/recovery root: `%USERPROFILE%\Documents\Norm\temp`.
- `.venv` is generated/reusable and is omitted from portable source and full-backup ZIP payloads.

## Operator and service behavior

- `Run-Norm.bat` launches the service and operator surfaces; `norm.exe --service` runs headlessly while Prompt/Replies/Runtime helpers remain explicit operator clients.
- Runtime and Replies reconnect across temporary service loss. Startup exceptions remain visible in the operator console path, and early detached-service stdout/stderr is captured in bootstrap logs.
- Chat/API readiness remains health-gated; the activity/control service reports initialization/busy state before the chat service is considered ready.
- `/status/busy` is the authoritative busy probe.
- `/new [name]`, `/thread-list`, `/thread-resume <name|id>`, suppression/resume controls, graceful shutdown, and emergency-stop controls remain supported.
- `/suppress-task` keeps the active cancellation transition marker until worker acknowledgement. Repeated suppression while cancellation is unwinding is idempotent. `/flush-suppressed` can remove durable suppressed rows without erasing the active transition hint.
- Prompt-side control HTTP failures are non-fatal to the Prompt console.
- `/inject-context` targets the live worker's current task first, then the oldest queued task only as a between-step fallback; injected context is persisted for the next model boundary without creating a new task.

## Queue, ingress, and task provenance

- Rich/SSH prompt clients share the Redis DB3 ingress namespace and the same dispatcher. Project `default` is canonical unless explicitly changed.
- DB3 ingress `prompt_id` is carried through `/api/chat` into durable task-plan provenance. Redelivery of the same prompt ID attaches to the existing root task rather than creating duplicate user work.
- `/queue` and `/queue-full` join ingress entries to matching durable running tasks/children and show the actual current task/step when available.
- A normal queued prompt is acknowledged only after a durable task exists; uncertain delivery remains visible rather than being silently replayed.
- `request_type` / `prompt_origin` provenance distinguishes user-authored work from Norm-generated child/recovery steps, while the original user prompt remains separately preserved.

## Task storage, files, and cleanup

- `read_file` streams arbitrarily large UTF-8 sources through a 24 MiB processing buffer and returns bounded tool-result chunks with continuation metadata.
- Large-source work uses independent processed-data, task-storage, and internal-note limits. Source files referenced outside task temp are not counted as copied task storage.
- Terminal cleanup is lineage-aware. Reproducible analysis/cache artifacts may be removed after verified task completion; user-requested durable artifacts outside task temp are preserved.
- Human-facing replies are bounded independently from durable task records; oversized replies are written to the durable workspace rather than forcing the task summary to carry the full payload.
- `write_file` and `replace_text` retain path authorization, SHA guards, backup, retry, and atomic replacement while delegating exact UTF-8 temporary-file content creation to the hash-verified private `verbatim_lines` writer. No unguarded public overwrite plugin tool is exposed.
- `file_read` is archive-aware through `archive_adapter.py`. Package-local 7-Zip is preferred, with ZIP/TAR standard-library fallback. Archive comparison is tree/sizes first, SHA second, and selective member reads last.

## Built-in plugins

Package-managed first-party plugin trees are:

- `backup`
- `file_read`
- `postgres_pool`
- `rotor5_cipher`
- `soft_delete`
- `stegosplit_key`
- `stegosplit_message`
- `verbatim_lines`
- `vision_parse`

User-added plugin folders remain persistent across normal managed updates. Plugin hydration is checksum/identity verified; failed candidate reloads retain the last-known-good loaded capability.

### `vision_parse` 0.2.1

- Handles **up to 10 PDF pages per call** and returns continuation metadata for larger documents.
- Pages are processed independently through local Ollama vision while the native PDF text layer remains an untrusted reconciliation hint.
- Clean sparse/normal pages use **1.5×** rendering.
- Moderately dense clean full pages use **2.2×** rendering.
- Suspect, dense, and split-crop pages use **2.9×** rendering.
- Dense two-column pages split left/right; very dense single-column pages can split top/bottom.
- Ollama `done_reason` and `eval_count` are preserved; dynamic `num_predict` ceilings are used per page/crop.
- A full-page `done_reason=length` falls back to split crops instead of silently returning an incomplete page.
- `token repeat limit reached` retries only the affected page/crop once with an anti-loop instruction.
- A bounded 128-page in-process LRU cache reuses identical source-page/model/mode/focus reads without creating a disk cache.

### PostgreSQL pool

- One process-global bounded PostgreSQL pool is the canonical runtime connection authority.
- `postgres_pool` is the controlled model-facing adapter to that pool; it does not create a second pool.
- Approved read-only queries and structured writes use sanctioned logical connections and schema restrictions rather than direct credential fishing.
- Backup tooling may resolve connection parameters without opening/configuring the process pool before launching `pg_dump`.

## Memory and history maintenance

- PostgreSQL `norm_runtime.task_history` is the durable compact long-term task archive. It is row-based and has **no aggregate character cap**.
- Prompt-time history retrieval is bounded per request; durable archive size is not bounded by the old monolithic-summary target.
- `/memory-condense` runs replay-validated deep-history consolidation on a bounded set of terminal raw history and replay-checks every compact record created in that normal pass before covered raw state can be pruned.
- `/memory-condense -full` refreshes the surviving compact/raw history newest-to-oldest in batches of up to **200 source tasks**, reconciles older lessons against relevant newer state, and replay-checks up to **12 stratified compact records per batch**.
- Deep-history pruning is fail-closed: an SQL backup is created before destructive work, failed validation preserves raw history and retains the backup, and only validated compact history may cover/delete raw task state.
- Deep-history completion does not re-merge the entire task archive into a single bounded background-memory string.
- Recovery cleanup runs before memory condensation. Dangling nonterminal task trees are eligible only when no live Redis membership/suppression exists and the configured stale threshold is met; salvage is replay-validated before raw rows are removed.
- Temporary recovery/SOS material is preserved when state is uncertain rather than guessed disposable.

## N1/N2 tool gate and validation/evidence pool

- `Run-Norm.bat` starts one runtime with two logical agent clients. **N2** is the existing reasoning/worker path; **N1** is the tool gatekeeper/supervisor. User input and N2 output are not rewritten by N1 at this checkpoint.
- N2 does not own verification tools anymore. Cacheable information-tool schemas require `n1_need` and `n1_target`; N1 checks Redis, chooses reuse versus fresh execution, and performs fresh-result check-in.
- Fresh executor results are delivered to N2 unchanged. N1 metadata/check-in is out-of-band. Exact repeated information requests in the same task/step reuse the prior raw result instead of firing the tool again.
- N1 passively observes consecutive N2 turns. Exact repeated tool requests are caught deterministically; broader similar reasoning/tool turns are sent to N1 for a loop judgment. When N1 judges a rabbit hole, that repeated turn is stopped before its proposed tools execute and N2 receives a reset instruction to continue from existing evidence with a materially different next action.
- Successful known mutations invalidate matching N1 raw-cache entries and live Redis verification records before later reads. Core deterministic post-write read-back remains outside model approval because it is a runtime safety invariant, not an N2 tool request.
- Redis uses one shared hash: **`norm:validation:pool`**. A fact record contains the tool used, stable target, factual description, current value, `num_checks`, previous value, and verified change timestamp. Internal pool-age bookkeeping is not exposed as another Redis key.
- Record identity is target-first rather than wording-key-first: same-target candidates are returned and ranked so small description changes can reuse an existing record instead of creating another Redis fact.
- A fresh same value increments `num_checks`. A verified different value stores the prior value, resets `num_checks` to **1**, and records the change timestamp. Reuse alone does not increment the count.
- Redis records become migration-eligible after their current hot epoch is at least **24 hours** old (`redis_min_age_seconds=86400`). Migration runs roughly twice daily (`migration_interval_seconds=43200`), so an eligible record may naturally remain in Redis longer than 24 hours until the next sweep.
- A successful migration merges the Redis count into PostgreSQL's longer-term record and then deletes that Redis hash field. Same-value waves are combined; a changed value starts a new current epoch. PostgreSQL verification history is retained for roughly **14 days** (`history_retention_seconds=1209600`).

## Backups

- `/backup` creates a portable source backup.
- `/backup full` creates a sensitive full backup suitable for Installer 1.6.6 restore, including package/source files, maintained docs, plugins, `.ssh`, configured secrets, selected runtime/recovery state, workspace material, PostgreSQL `norm_runtime`, and environment rebuild metadata.
- `.venv`, PyInstaller build/staging output, Python caches, and routine disposable scratch are excluded.
- `/backup-zip` remains a compatibility alias for `/backup full`.

## Installer behavior

- Installer **1.6.6-unified** performs an in-place managed sync: changed package files are replaced and removed package-owned files are deleted while persistent local state survives normal updates.
- Existing compatible virtual environments are reused.
- The installer binds one exact source payload by filename/SHA-256 and can build a derived payload using locked or newest eligible stable/RC dependencies; alpha/beta/dev releases are excluded.
- Existing configuration is snapshotted and migrated. Secrets stay in the configured secrets file and masked installer fields; non-secret settings are carried forward according to migration rules.
- The retired validation-pool default `600` seconds is promoted to the packaged `86400`-second default only when the old value is exactly that retired default; other operator-selected values remain preserved.
- Environment connection checks use only configured endpoints.

## Current validation boundary

Source-level syntax, configuration, plugin-integrity, package, and installer-payload validation are part of release packaging. Those checks do not by themselves prove that an arbitrary target machine's Ollama, Redis, PostgreSQL, Tailscale/network authority, secrets, SSH material, CUDA, or Python environment is healthy. Live deployment remains a separate operation.
