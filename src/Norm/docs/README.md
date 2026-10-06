# Norm

Norm is a local Windows agent/runtime built around Ollama, Redis, PostgreSQL, durable task/memory state, dynamic first-party plugins, and explicit operator controls.

This document describes the current **Norm 0.53.18 / Installer 1.6.8-unified** package only. Version history belongs only in RELEASE_NOTES.md.

## Runtime layout

Default installation root: C:\Norm.

- core\ — runtime source and compiled norm.exe target.
- config\settings.ini — resolved paths, service endpoints, plugin root, file-access policy, and connection configuration.
- config\runtime.json — worker, model, maintenance, queue, storage, and memory policy.
- docs\ — maintained current-state documentation plus the release ledger.
- plugins\ — first-party and operator-added dynamic plugins.
- tools\ — operator helpers, build/test tools, bundled 7-Zip, and bundled WeasyPrint.
- .ssh\ — persistent Norm SSH material.
- state_root — trusted internal runtime state resolved from [paths].state_root.
- %USERPROFILE%\Documents\Norm\workspace — durable generated artifacts/work files.
- %USERPROFILE%\Documents\Norm\temp — disposable scratch, recovery, and task-local intermediates.

.venv, build output, caches, and compiled core\norm.exe are generated/reused locally and are not authoritative portable-source content.

## N1 / N2 execution boundary

Norm runs two logical agent roles. **N2** performs normal reasoning and work. **N1** gates model-requested tools, owns verified-result reuse/check-in, and watches for repeated reasoning/tool loops.

Normal user text and N2 user-facing answers are not rewritten by N1.

For cacheable information requests, N2 proposes the actual tool plus stable need/target metadata. N1 checks the live Redis validation pool first. A sufficiently current verified result can be reused without re-running the tool. A fresh execution result is forwarded to N2 unchanged and checked into the validation pool out-of-band.

Successful known mutations invalidate affected target and parent-directory cache state. Deterministic post-write verification is a core safety check and does not require model approval.

## Session model switching

Norm always boots with the canonical Ollama model norm. A previous interactive switch is never restored across process restart.

- /switch-model asks the running Ollama service for /api/tags and prints a deterministic numbered model list. norm / norm:latest is first; remaining installed models are alphabetical.
- /switch-model N selects by one-based list index.
- /switch-model MODEL selects an exact installed model name; an untagged base name is accepted only when it resolves uniquely.
- Switching is allowed only after startup is complete and while Norm has no active or queued work.
- The candidate is silently generation-probed before any live client pointer changes.
- Every distinct live Ollama endpoint used by N1, N2/chat/worker, context, or vision is checked and probed before commit.
- Norm checks for newly started work again after probes and before commit.
- A successful commit changes all live Ollama clients together for the current process; commit failure restores every prior client model.
- A missing model, failed probe/load, endpoint disagreement, or busy-state conflict leaves the current model unchanged.
- /shutdown ollama targets the current session model.
- Restarting Norm always returns to norm.

Model switches are session-only: /switch-model does not rewrite runtime.json, settings.ini, PostgreSQL runtime state, or another persistent boot selector.

## Operator help authority

docs\help_menu.txt is the single human-editable operator-help source. Both the GUI Prompt and Rich console read it at command time for help / /help. Editing the text file changes the visible command menu without rebuilding norm.exe.

## Validation/evidence pool

Redis is the mandatory hot validation layer for information-tool evidence.

- Shared hash: norm:validation:pool.
- Identity is target-first to avoid near-duplicates caused by wording changes.
- Fresh same-value checks increment num_checks.
- A verified value change resets the current count to 1 and preserves the prior value/change timestamp.
- Reuse alone does not increment the check count.
- Hot records become migration-eligible after about 24 hours.
- Migration runs roughly twice daily and merges eligible evidence into PostgreSQL history before deleting the Redis field.
- PostgreSQL verification history uses bounded retention.

## Prompt ingress, queue, and task provenance

Rich and SSH prompt clients share Redis DB3 ingress and the same dispatcher. Accepted normal prompts are not acknowledged until a durable task ID exists.

Prompt IDs are carried into durable task provenance so redelivery attaches to the existing root task rather than creating duplicate user work.

The full original user turn remains durable provenance. Interpretation may classify side information, but executable wording only removes clearly separable sidecar material. Material task qualifiers remain part of the request.

## Durable summaries and memory

End-of-task durable summaries are **current-state projections**, not transcripts or supersession ledgers.

Summary refresh advances from the durable coverage cursor, sees same-turn durable-memory changes first, may shrink when stale state is removed, rejects transcript-like shapes, retries once with stronger pruning, and falls back to deterministic current-state reconstruction rather than freezing stale text.

Confident details go directly to their durable home. Genuinely unplaced details use norm_runtime.unresolved_bits and unresolved_bit_trials until promoted/applied.

## Memory condensation modes

Norm separates recent maintenance from full historical consolidation.

### Regular background maintenance

Scheduled regular background-memory condensation is shallow and deliberately aggressive. It only considers the configurable recent window. Defaults:

    "regular_memory_window_days": 14
    "consolidation_batch_chars": 14000
    "consolidation_batch_target_chars": 1800
    "consolidation_snapshot_target_chars": 6000

The background snapshot is working memory, not an archive. Detailed reconstructable task history remains in PostgreSQL task_history. Regular condensation merges repeated project updates into current state plus reusable lessons, and drops routine chatter, smoke-test narration, one-off examples, transient market levels, temporary shorthand, superseded states, and source-by-source repetition.

### /memory-condense

Manual /memory-condense is also recent-only. By default it considers the last 14 days, compacts eligible recent terminal work, and replay-validates **every** compact record created in that pass.

Each validation is isolated:

1. One compact memory is supplied.
2. Norm reconstructs the task from that memory alone.
3. Reconstruction gets up to 4,800 output tokens per continuation segment and up to 4 segments.
4. Authoritative source state grades the reconstruction.
5. Replay text is discarded.
6. A failed compact record may be repaired from that task's own authoritative source and retried once.
7. No neighboring memory or prior replay carries into the next sample.

Recent-only condensation does not globally prune old conversation history.

### Scheduled maintenance

Scheduled memory maintenance alternates successful passes:

    regular -> full -> regular -> full -> ...

The interval is controlled by memory.scheduled_memory_interval_days (default 7). A transient crash/failure can retain the same recorded mode for resume; alternation advances only after success. A deterministic ModelOutputTruncated failure is different: Norm durably parks the maintenance run as requires_attention and disables automatic retry until the operator explicitly resumes it.

Scheduled maintenance is visible through /status/busy, /queue, and /queue-full. When no user task is ahead of it, /suppress-task parks the active maintenance run and cancels its model call. /resume-task maintenance explicitly resumes a suppressed/requires_attention maintenance checkpoint.

Scheduled regular uses the recent-window background condensation path. Scheduled full sweeps the complete historical archive, validates every configured date-ordered row batch by sampling, then performs one conservative hierarchical merge level over neighboring compact rows.

Deep history is not scheduled automatically. Use /memory-condense -deep when you want a bounded older raw-history compaction/validation pass without hierarchical merging.

### /memory-condense -full

Full mode processes the complete surviving compact/raw historical archive under a fail-closed SQL backup. The archive is sorted by date and divided into QA batches; the batch size controls validation grouping, not pass scope. Repeated full passes remain useful because each invocation performs only one hierarchical merge level, so prior merged outputs can be condensed again on a later full pass.

Defaults in config\runtime.json:

    "deep_history_full_batch_rows": 200,
    "deep_history_full_samples_per_batch": 12,
    "deep_history_full_merge_max_records": 6

For each date-ordered compact-row batch, up to the configured sample count is reconstructed independently with the same 4,800 × 4 replay ceiling.

After all QA batches validate, full mode performs one hierarchical merge level over chronologically neighboring windows. Each window contains at most the configured number of compact rows and is **not required to collapse to one record**. Unrelated neighboring rows stay separate; genuinely related/redundant subsets may safely reduce six rows to five, four, three, two, or one.

A surviving compact record must preserve enough meaning to reconstruct the task/request, meaningful steps or full itinerary, tools actually used, final results/current state, artifact pointers, efficiency/failure lessons, reusable technical constraints, and improvement guidance.

Every input compact memory must appear exactly once in a proposed partition. Actual multi-memory merges are validated by reconstructing each constituent task from the merged memory alone. A failed merge falls back to the original constituent memories.

The sizing keys are re-read when a maintenance pass starts, so changes apply without restarting Norm. Example:

    "deep_history_full_batch_rows": 50,
    "deep_history_full_samples_per_batch": 6,
    "deep_history_full_merge_max_records": 3

## Maintenance checkpoint reliability

Condensation checkpoints use unique temporary files, flush and fsync, bounded atomic replace retries, and a final in-place durable rewrite fallback when Windows temporarily denies replacement of the destination checkpoint.

Checkpoint files are resumable maintenance state; PostgreSQL remains the durable authority for history.

## Operator consoles and emergency shutdown

Run-Norm.bat starts the headless service plus Prompt, Replies, and Runtime operator surfaces.

/stop-all now writes and verifies the SOS snapshot, begins Norm/Ollama teardown immediately, and signals only registered Norm console hosts.

- Non-prompt operator windows auto-close after about 5 seconds.
- Norm Prompt remains about 10 seconds so the shutdown result is readable.
- Enter or Ctrl+C closes a countdown immediately.
- Shutdown never mass-kills generic python.exe processes.

## Filesystem policy and state root

@state is internal runtime state. It is not exposed to N2/native/plugin file tools unless a specific capability receives an additive override. Internal maintenance can use trusted state without widening general model filesystem authority.

Core file tools and built-in file plugins share the canonical path-authorization policy.

## Dynamic plugins

Package-managed first-party plugins:

- backup
- file_read
- postgres_pool
- rotor5_cipher
- soft_delete
- stegosplit_key
- stegosplit_message
- verbatim_lines
- vision_parse
- voice_profile

Schema-2 plugins use plugin.json + README.md + src\. plugin.json declares the entrypoint and deterministic source-tree SHA-256. Public entrypoint functions become model-facing tools; helper modules remain private implementation.

Hydration, schema refresh, and execution are serialized by one process-global reentrant lock. A candidate that fails verification/import does not replace the last-known-good loaded capability.

## PostgreSQL pool

Norm owns one process-global bounded PostgreSQL pool. The postgres_pool plugin borrows approved logical connections from that pool rather than opening unrestricted direct connections.

## Archive handling

file_read prefers package-local 7-Zip and falls back to standard-library ZIP/TAR support. Archive work should escalate from tree/sizes to hashes to selective member reads rather than extracting everything.

## PDF/document reading

vision_parse handles up to 10 pages per call, treats native text as an untrusted hint, uses the rendered page as authority, supports adaptive 1.5× / 2.2× / 2.9× rendering, dense-layout splitting, completion-length crop fallback, repeat-loop retry, and bounded in-process page reuse.

## WeasyPrint / Pango

Windows HTML/CSS-to-PDF rendering is self-contained.

Norm uses the official **WeasyPrint 70.0** Windows onedir runtime with the UCRT64 native stack including **Pango 1.58.2**. The public source does not vendor that frozen native tree. Instead package-manifest.json pins the official upstream URL and archive SHA-256.

Entry points:

- tools\weasyprint.cmd
- tools\fetch_weasyprint_runtime.py
- tools\weasyprint\runtime\weasyprint.exe (generated/downloaded locally)
- tools\weasyprint_smoke.py

Installer 1.6.8 preserves an already-valid local runtime. If it is missing or stale, the installer invokes the source helper, verifies the pinned upstream archive SHA-256, extracts it, then runs --info and a real HTML-to-PDF render. The command wrapper uses the same helper for lazy repair. The target machine does not need MSYS2 or locally compiled Pango.

## Backups

/backup creates a portable source backup without private machine state.

/backup full creates a sensitive recovery package including source, plugins, configured secrets, .ssh, workspace, selected state/log/recovery material, PostgreSQL dump, and environment rebuild metadata. Rebuildable .venv and ordinary build products are excluded.

## Installer 1.6.8

Installer 1.6.8 performs managed in-place updates, preserves persistent machine state and the verified WeasyPrint runtime, migrates existing configuration, reuses a compatible virtual environment, validates packaged source/tools, downloads/repairs WeasyPrint/Pango from the package-pinned official archive when needed, verifies its SHA-256 and PDF rendering, and binds the exact portable-source payload by filename and SHA-256.

## Documentation

- README.md — operator/runtime overview.
- CURRENT_STATUS.md — concise current package facts.
- DEVELOPMENT_NOTES.md — current engineering invariants and implementation contracts.
- FUTURE_IMPLEMENTATION_NOTES.md — active unimplemented backlog only.
- MAINTENANCE_VERIFICATION.md — current package validation evidence.
- RELEASE_NOTES.md — the only maintained historical/version ledger.
