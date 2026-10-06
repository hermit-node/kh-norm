# Norm development notes

These are current engineering contracts for **Norm 0.53.19**. This file intentionally contains no release chronology.

## Architectural authority

- PostgreSQL is the durable authority for tasks, history, memories, summaries, maintenance notes, and long-term state.
- Redis is hot/transient coordination state: queues, ingress, validation evidence, suppression/transition hints, and delivery state.
- config\settings.ini is canonical resolved path/service configuration.
- config\runtime.json is policy/configuration, not a durable state database.
- state_root is trusted Norm-owned runtime state and is not automatically a model filesystem root.

## N1/N2 contract

N2 reasons and requests actual tools. N1 decides whether a model-requested information call should reuse verified evidence or execute fresh.

N1 must never fabricate a tool result, rewrite a fresh executor result before N2 sees it, or increment evidence counts merely because cached evidence was reused.

Mutation invalidation must happen before a later read can reuse stale data.

Loop supervision is passive until repeated behavior crosses configured thresholds. A loop decision blocks the repeated turn before its proposed tools execute.

## Session model-switch contract

norm is the immutable process boot model. Interactive switching is intentionally ephemeral and must not mutate persistent configuration/state.

Model discovery uses Ollama /api/tags, not direct model-store directory parsing. The displayed list is deterministic: canonical norm first, then remaining installed names alphabetically.

A model switch is fail-closed:

1. Startup must be complete and tracked work must be idle.
2. Resolve the numbered/exact selector against installed models.
3. Enumerate every distinct Ollama endpoint used by live Norm model clients.
4. Verify the candidate exists/resolves and produces nonblank probe output on every endpoint.
5. Re-check tracked work after probe completion.
6. Gather the live client set again.
7. Update all client model values together and update the in-process active-model label.
8. On commit exception, restore every previous client model and active-model label.

The probe has no activity sink, so candidate verification does not appear as user/model output. A failed probe may cause Ollama itself to load or evict native model memory, but Norm's logical active model remains unchanged unless commit succeeds.

## Operator help contract

docs\help_menu.txt is the only operator-facing command-list authority. Prompt frontends read it when help / /help is invoked. Do not add a second hardcoded menu in GUI/Rich Python code; if the file is missing or unreadable, surface that error explicitly.

## Public-web contract

Internet access is an explicit native capability, not a relaxation of internal-network policy.

FileToolExecutor advertises web_search/web_fetch only when tools.public_web.enabled is true. Both normal conversation tools and worker/slice tools receive the same public-web configuration.

Network boundary:

- schemes: HTTP/HTTPS only;
- ports: 80/443 only;
- URL credentials are rejected;
- localhost/private/loopback/link-local/reserved/non-global IPs are rejected;
- Tailscale CGNAT 100.64.0.0/10 and *.ts.net are rejected;
- DNS answers are checked before a request and redirects are revalidated;
- model-visible web data is marked external_content_trust=untrusted.

Search uses structured RSS feeds rather than arbitrary HTML scraping. web mode uses Bing RSS; news mode uses Bing News RSS and extracts publisher URLs from Bing redirect metadata.

Fetch behavior is direct-first. HTML/plain text is locally extracted into bounded character chunks. If the direct result is absent or below reader_min_chars, configured reader fallback may retrieve readable Markdown from the already-validated public URL. URLs with token/auth/secret/signature-like query keys are not proxied to the reader service.

Fetched/search text is untrusted evidence and must never be interpreted as runtime/operator/system instruction. Both web tools are information tools and therefore require verification preflight/check-in through the Redis validation pool.

## Prompt/task fidelity

The verbatim user prompt is permanent provenance. Interpretation may classify side information, but task execution preserves embedded qualifiers and constraints. Only exact, clearly separable sidecar spans may be excluded from executable wording.

Prompt-ID idempotency is required across ingress/redelivery.

## Durable summary contract

Thread/runtime summaries represent the best current state and are allowed to shrink.

Do not preserve stale facts merely to show how state evolved. Version history belongs only in RELEASE_NOTES.md.

Repeated invalid/failing summary generation writes deterministic current-state fallback and advances coverage rather than freezing the previous summary.

## Compact-memory content

Compact task history preserves reconstructability, not narration volume.

A durable record should retain task/request, meaningful steps/itinerary, final state/results, artifact/data pointers, tools that mattered, efficiency lessons/failure causes, reusable constraints/fixes, and concise improvement guidance.

Routine retries, verifier chatter, duplicate smokes, and execution narration are disposable after their useful lesson is preserved.

## Regular condensation contract

Regular scheduled background condensation and manual /memory-condense are recent-only.

memory.regular_memory_window_days defaults to 14. Regular background snapshot tuning defaults to consolidation_batch_chars=14000, consolidation_batch_target_chars=1800, and consolidation_snapshot_target_chars=6000.

The regular background snapshot is working memory, not archival memory. PostgreSQL task_history retains reconstructable detail. Batch/final prompts must aggressively synthesize repeated project updates, preserve only current state/durable decisions/stable preferences/unresolved obligations/reusable lessons/still-useful pointers, and explicitly reject one-output-item-per-source behavior. Transient examples, temporary shorthand, routine successful checks, superseded states, and detail recoverable from task_history are disposable.

Regular batch condensation uses a bounded 900 × 2 generation budget; the final working-memory merge uses 1800 × 2. If either result exceeds its configured character target tolerance, Norm asks the model to re-condense the candidate rather than blindly continuing it.

Manual force means run now, not ignore history scope.

Compact-record replay validation remains separate from the working-memory snapshot: one compact record in, no neighboring memory, no prior replay, no tools, maximum 4,800 × 4 continuation budget, authoritative source used only by grader/repair, replay text discarded after the decision.

A failed compact record may be repaired from its own authoritative task source and retried. The persisted row must be the repaired row rather than the pre-repair version.

## Scheduled maintenance contract

Scheduled maintenance alternates **regular -> full -> regular -> full** using memory.scheduled_memory_interval_days (default 7).

The last successful scheduled mode is durable PostgreSQL runtime state. Transient crashes/failures may retain the Redis active marker for same-mode resume; alternation advances only after successful completion. ModelOutputTruncated is deterministic attention state, not an automatic retry signal: Norm writes durable weekly_maintenance_parked state, marks the Redis run requires_attention with auto_resume=false, and waits for explicit operator resume.

Scheduled maintenance is first-class operator-visible work. The worker idle flag is cleared while it runs; /status/busy carries a maintenance object; /queue and /queue-full display active or parked maintenance even when the prompt queue is empty. If there is no active/queued user task ahead of it, /suppress-task durably parks the maintenance run, checkpoints existing maintenance state, cancels the active model call, and disables automatic resume. /resume-task maintenance clears the durable park and explicitly resumes the saved run when the worker is idle.

Scheduled regular uses recent incremental background-memory condensation. Scheduled full sweeps the complete historical archive with the full hierarchical validation/merge rules below.

Deep history is manual-only through /memory-condense -deep. Manual deep processes older terminal raw history, bounded by deep_history_max_tasks_per_pass, sorts resulting compact rows by date, groups them by deep_history_full_batch_rows, and validates up to deep_history_full_samples_per_batch isolated compact memories from each batch. It does not perform hierarchical multi-memory merging.

## Full condensation contract

/memory-condense -full and scheduled full sweep the complete date-ordered historical archive each invocation. The row-batch setting controls validation grouping only; it does not cap pass scope. Each full pass performs one hierarchical merge level, so repeated full passes can progressively condense the already-compacted archive further.

Live-configurable keys:

- deep_history_full_batch_rows — date-ordered compact rows per validation batch; default 200.
- deep_history_full_samples_per_batch — stratified isolated samples per batch; default 12.
- deep_history_full_merge_max_records — maximum chronologically neighboring compact rows per merge window; default 6.

Memory-maintenance config reloads when a pass starts.

Merging is optional and variable-cardinality:

- N inputs may produce 1..N outputs.
- Every input primary ID appears exactly once in the proposal partition.
- Singletons remain their original record.
- Multi-memory records carry full source provenance.
- Newer non-superseded state wins when facts changed.
- Older chronology remains only when it explains evolution or prevents repeated mistakes.
- Each constituent of a merged record reconstructs successfully from that merged record alone.
- Failed proposed merges fall back to their original memories.

## Maintenance checkpoint contract

Checkpoint files are resumable convenience state, not authoritative history.

Writes use unique temp names, flush/fsync, bounded Path.replace retries, and final durable in-place fallback for Windows sharing/rename denial.

## Destructive history safety

Destructive deep/full maintenance requires a verified SQL backup first. Raw task deletion is allowed only when validated durable compact history covers the source IDs.

Failure before validation leaves trusted source state in place and retains the safety backup.

## Console process contract

Operator windows are detached for usability, so shutdown coordinates them explicitly.

/stop-all now signals registered Norm console hosts through internal state. It never searches for and kills generic Python processes.

Prompt has the longer post-shutdown visibility window. All countdowns permit immediate Enter/Ctrl+C close.

## File policy contract

Every first-party filesystem capability uses canonical authorization.

@state is internal-only unless a capability-specific file_access_overrides rule grants access.

Do not widen N2 filesystem authority merely to fix an internal maintenance path.

## Plugin contract

Package-managed schema-2 plugins use plugin.json, README.md, and src\. plugin.json:sha256 covers the deterministic src tree. Public functions in the declared entrypoint are tool candidates; helper code remains private.

Hydration, schema refresh, and execution are serialized. Invalid candidates do not replace active last-known-good capability code.

## PostgreSQL pool contract

One process-global pool is authoritative. Model tools borrow approved logical connections. Do not add independent credential-driven direct connections to model-facing tools.

## Archive contract

Prefer tree/size inspection first, then hashes, then selective reads. Package-local 7-Zip is the preferred native archive engine; standard ZIP/TAR readers are fallback.

## PDF vision contract

Rendered page evidence outranks an untrusted/corrupt text layer. Large documents remain resumable. Full-page truncation falls back to crops instead of presenting incomplete output as complete.

## WeasyPrint contract

Windows PDF rendering uses the official standalone WeasyPrint onedir runtime under tools\weasyprint\runtime. Do not mix its frozen DLL set into Norm's Python process.

The public package does not vendor that generated native runtime. package-manifest.json pins the official upstream URL, version, and archive SHA-256. tools\fetch_weasyprint_runtime.py is the canonical fetch/repair implementation; Installer 1.6.8 calls it when the runtime is absent/stale, and tools\weasyprint.cmd calls the same helper for lazy repair. Existing valid runtime directories are persistent across source sync. Installer acceptance proves both --info discovery and actual PDF creation after ensure/repair.

## Installer/package contract

The installer is an in-place package manager. It preserves configured/persistent state, removes package-owned files no longer in the payload, migrates config through explicit precedence, and binds the payload by SHA-256.

Portable source excludes .venv, compiled norm.exe, caches, secrets, SSH private material, and machine-private topology.

## Documentation contract

Only RELEASE_NOTES.md is historical.

README.md, CURRENT_STATUS.md, DEVELOPMENT_NOTES.md, MAINTENANCE_VERIFICATION.md, FUTURE_IMPLEMENTATION_NOTES.md, and SOURCE_PACKAGE.md describe current state, current contracts, current evidence, or active backlog only.
