# Norm

Norm is a local Windows agent/runtime built around Ollama, Redis, PostgreSQL, durable task/memory state, dynamic first-party plugins, and explicit operator controls.

This document describes the current **Norm 0.54.2h / Installer 2.0.0** package only. Version history belongs only in RELEASE_NOTES.md.

### Single-primary-file planner discipline (0.54.2h)

Norm 0.54.2h adds a planner-only execution guideline: file-creation/file-editing steps should mutate one primary output file per executable step, while remaining free to inspect any number of source/context files. Related target files are planned separately, and a complete primary file may still be emitted in one guarded verbatim write. No runtime guard, verifier rule, or plugin behavior changed. Managed-plugin fallback remains on the known-good 0.54.2g package because plugin payloads are unchanged.

### Package completeness + console/byte-cursor fixes (0.54.2g)

Norm 0.54.2g restores the exact SHA-verified `vision_parse`, `web_browser`, `jan`, `vane`, and `opencode` payloads accidentally omitted from the 0.54.2/0.54.2f source archives, and the managed-plugin public fallback now targets the matching 0.54.2g package. Norm Prompt remains singleton because duplicate input frontends can submit duplicate work. Norm Replies and Norm Runtime are read-only viewers and may coexist locally or remotely; they no longer acquire named Windows mutexes. Explicit byte-cursor text reads are treated as streamed evidence even when N2 supplies start_byte, so N1 checks in deterministic file/fingerprint/cursor metadata without invoking the verification_value model on the raw chunk. Portable-source archives also exclude core/norm.exe.

### Recovery scope + action-boundary protected-literal guard (0.54.2)

Unnumbered oversized work remains genuinely unscoped during recovery; Norm no longer invents `scope_items=[1]`, so atomic-item decomposition limits cannot be triggered by a synthetic scope. Sensitive literals continue to be transformed before N1/N2 see evidence. N1 does not police N2's general reasoning about passwords, credentials, or surrounding code. Only a concrete proposed action that plausibly attempts to recover or bypass a transformed literal is sent through the semantic guard. The first denied attempt receives only `The Bird says No. The Worm won't Go.`; repeated attempts may receive a short safe workaround, such as using Norm's direct PostgreSQL query capability instead of constructing a credential-bearing connection. The guard's counters/history are in-process only and reset on restart.

### Redis verification-pool evidence replay (0.54.2b)

Redis's existing live verification pool is the sole hot reusable-evidence cache. For each information request, N1 receives only bounded candidate metadata/canonical values and decides whether the relevant previous observation or observations are sufficiently applicable/current for N2's present need. Age is evidence, not a TTL: static documents may remain sufficient while live chat/process/latest-state requests may justify a refresh even when recent; known mutations invalidate affected targets immediately.

When N1 judges prior evidence sufficient, Norm fetches the selected private replay payload from the companion Redis replay hash only after that decision and supplies the actual evidence to N2. N2 is not told to "use prior evidence" and does not need to know whether the source was replayed or queried again. If evidence is incomplete, stale, contradictory, structurally mismatched, unavailable, or otherwise insufficient, the source executes fresh and the verification pool is refreshed. The former separate in-process raw-result cache was removed. Large `read_file` evidence still obeys the one-raw-chunk-per-N2-turn invariant. PostgreSQL remains the durable history/checkpoint layer; Redis replay payloads are hot evidence only.

### Adaptive chunked evidence context (0.54.2a)

Large default text reads use a context-budgeted evidence stream instead of accumulating raw tool results in N2 history. The runtime reserves N2 output + safety first, assigns about one fifth of the remaining usable context to one raw chunk, and never delivers more than one raw evidence chunk in an N2 turn. A stream returns `stream_id`, numbered `piece`, byte range, estimated piece count, and seam markers. N2 can jump directly to any one piece. Before a later tool call, N2 can place compact facts in the read's `retain` field; the consumed raw chunk is then replaced in active context by compact provenance/facts while the original tool evidence remains durable.

`config/runtime.json:[context_budget]` owns this policy. The active stream's byte map remains stable for that source fingerprint; a file change invalidates the stream.

Context headroom uses Ollama's own `prompt_eval_count` rather than rescanning the full prompt each turn. At the default 5% remaining threshold, Norm archives the recent execution slice to PostgreSQL, N1 distills only what is applicable to finishing the same step, and the existing checkpoint/requeue path restarts N2 from that compact continuation. Requeued slices remain Norm-internal continuations, never synthetic user prompts.

### Runtime profiles and Codex reasoning (0.54.1a)

`config/profiles.json` owns operator/user/N1/N2 persona and voice assignments. Direct persona is operator-authored; inferred persona/voice artifacts come from `voice_profile` and remain subordinate to runtime/tool rules. The `codex_reasoner` plugin can call Codex CLI for ephemeral read-only reasoning while Norm retains all execution/mutation responsibility.

### Runtime-owned model policy (0.54.1a)

Model context size and agent-persistent instructions are owned by `config/runtime.json`, not by `settings.ini`, `config/norm.json`, or the Ollama Modelfile. N1 and N2 have separate persistent-instruction sets that follow their runtime clients across model switches. Context overflow is routed into bounded oversize decomposition instead of identical retry.

### N1-gated memory maintenance (0.54.0 Beta baseline)

Scheduled memory has two independent modes. The weekly 7-day pass is reductive: N2 ranks and rewrites, preserving important/current information and shrinking less-important context. N1 judges each N2 candidate and decides accept/retry. Up to four attempts are allowed; <=1,800 characters is the ideal early-stop size and 3,400 characters is the practical batch ceiling. The 21-day long-cycle pass is destructive: N2 may propose removal of obsolete/useless memory, but N1 must approve destruction before the record is omitted.

N1 maintenance review limits are operator-configurable in `config/runtime.json`: `agents.n1.maintenance_num_predict` controls the per-review output allowance (default 5000) and `agents.n1.maintenance_review_timeout_seconds` controls the per-review wall-clock timeout (default 720 seconds / 12 minutes). These are runtime settings, not compile-time constants.


### Portable remote SSE viewer

Run `make_sse.bat` from the installed Norm root to build `Norm-SSE.exe` from that machine's current network configuration. The output is a single read-only Windows SSE viewer that can be moved to another tailnet-connected Windows PC. It connects directly to the source machine's activity `/events` endpoint and embeds no Redis/PostgreSQL passwords or other Norm secret values. Re-run `make_sse.bat` after changing the source machine name/domain/activity port.


## Runtime layout

Default installation root: C:\Norm.

- core\ — runtime source and compiled norm.exe target.
- config\settings.ini — core Norm paths, services, plugin root, file-access policy, and connection configuration.
- config\plugins.ini — generated/reconciled plugin settings registry populated from Schema-2 plugin manifests; disabled integrations remain inert.
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

Norm runs two logical agent roles. **N2** performs normal reasoning/work, subject decomposition, and memory rewriting. **N1** is the boundary critic/gatekeeper.

The normal control loop is: **user -> N1 ingress review -> N2 -> N1 tool gate/reuse -> N2 -> N1 final-answer review -> user**. N1 never rewrites the exact user text or accepted final answer. When N1 finds a material defect it sends bounded feedback back to N2 for repair/resubmission. If N1 cannot process a final candidate, N2 gets one formatting/structure/wording cleanup resubmission before the existing safe review-failure policy applies.

For cacheable information requests, N2 proposes the actual tool plus stable need/target metadata. N1 checks the live Redis validation pool first. A sufficiently current verified result can be reused without re-running the tool. A fresh execution result is forwarded to N2 unchanged and checked into the validation pool out-of-band.

Successful known mutations invalidate affected target and parent-directory cache state. Deterministic post-write verification is a core safety check and does not require model approval.

### Subject-bound thread routing

N2 now decomposes a mixed user turn into materially distinct subjects instead of attaching the entire turn wholesale to every semantically related thread. N1 reviews that routing proposal before new threads are created. Existing-thread reuse requires confidence >= **0.68**; weaker matches create a better subject thread or ask for clarification.

The authoritative user turn remains unchanged in the shared `messages` record for provenance after the existing ingress whitespace normalization. Each thread association can carry `projected_content`, so thread history, summaries, and later routing see only the accepted subject-relevant portion. Multi-subject assistant replies are projected back using verified verbatim excerpts from the accepted reply; a projection failure falls back to the primary thread only rather than spraying the whole answer across side threads.

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

Scheduled memory maintenance uses two independent clocks:

    reductive every 7 days
    destructive every 21 days

The reductive interval is controlled by memory.scheduled_memory_interval_days (default 7); the destructive interval is controlled by memory.destructive_memory_interval_days (default 21). When both are due, destructive runs and also satisfies that week's reductive pass. A transient crash/failure retains the same recorded mode for resume. A deterministic ModelOutputTruncated failure is different: Norm durably parks the maintenance run as requires_attention and disables automatic retry until the operator explicitly resumes it.

Scheduled maintenance is visible through /status/busy, /queue, and /queue-full. When no user task is ahead of it, /suppress-task parks the active maintenance run and cancels its model call. /resume-task maintenance explicitly resumes a suppressed/requires_attention maintenance checkpoint.

Scheduled reductive uses the recent-window background condensation path. Scheduled destructive sweeps the complete historical archive, lets N2 propose useless records for deletion, requires N1 approval for each destructive omission, validates the surviving configured date-ordered row batches by sampling, then performs one conservative hierarchical merge level over neighboring compact rows.

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

Installer 1.6.9 preserves an already-valid local runtime. If it is missing or stale, the installer invokes the source helper, verifies the pinned upstream archive SHA-256, extracts it, then runs --info and a real HTML-to-PDF render. The command wrapper uses the same helper for lazy repair. The target machine does not need MSYS2 or locally compiled Pango.

## Backups

/backup creates a portable source backup without private machine state.

/backup full creates a sensitive recovery package including source, plugins, configured secrets, .ssh, workspace, selected state/log/recovery material, PostgreSQL dump, and environment rebuild metadata. Rebuildable .venv and ordinary build products are excluded.

## Installer 1.6.9

Installer 1.6.9 performs managed in-place updates, preserves persistent machine state, hashes managed public/optional plugin src before mutation, leaves matching src untouched, repairs only mismatched Norm src, installs missing plugins from verified public source, preserves the verified WeasyPrint runtime, migrates existing configuration, reuses a compatible virtual environment, validates packaged source/tools, downloads/repairs WeasyPrint/Pango from the package-pinned official archive when needed, verifies its SHA-256 and PDF rendering, and binds the exact portable-source payload by filename and SHA-256.

## Documentation

- README.md — operator/runtime overview.
- CURRENT_STATUS.md — concise current package facts.
- DEVELOPMENT_NOTES.md — current engineering invariants and implementation contracts.
- FUTURE_IMPLEMENTATION_NOTES.md — active unimplemented backlog only.
- MAINTENANCE_VERIFICATION.md — current package validation evidence.
- RELEASE_NOTES.md — the only maintained historical/version ledger.
### Plugin settings registry (0.53.22)

Exact duplicate `plugins.ini` backup states are SHA-256-deduplicated and do not consume the 12-slot retention limit; plugin-setting backups live under `backups/plugins-ini` and legacy `config/backups/plugins-ini` is migrated automatically.

Schema-2 plugins declare non-secret defaults in `plugin.json` under `settings`. `PluginManager.refresh()` materializes/reconciles `config/plugins.ini` on startup and hot-rescan: declared operator values are preserved, missing/invalid values get defaults, and dangling keys/sections are removed after a rotating backup. Secrets stay in the configured external `.env`. Plugin/config/file/SQL/archive backup classes retain at most 12 newest backup sets.



## Maintenance condensation model

Memory maintenance can use a model independent of N2. Configure `memory.condensation_model` and `memory.condensation_backup_model` in `config/runtime.json`. Each setting is optional; a blank value resolves to `norm`. Friendly names are resolved against Ollama's installed-model inventory at maintenance start using deterministic exact/base/starts-with/substring/fuzzy matching; ambiguous names are never guessed and fall through to the backup. The effective failover order is primary → one backup → `norm`. Settings are re-read when a maintenance pass begins.
