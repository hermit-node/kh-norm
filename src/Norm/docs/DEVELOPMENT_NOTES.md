# Norm development notes

These are current engineering contracts for **Norm 0.53.16**. This file intentionally contains no release chronology.

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

memory.regular_memory_window_days defaults to 14.

Manual force means run now, not ignore history scope.

Each replay is isolated: one compact record in, no neighboring memory, no prior replay, no tools, maximum 4,800 × 4 continuation budget, authoritative source used only by grader/repair, replay text discarded after the decision.

A failed record may be repaired from its own authoritative task source and retried. The persisted row must be the repaired row rather than the pre-repair version.

## Scheduled maintenance contract

Scheduled maintenance alternates **regular -> full -> regular -> full** using memory.scheduled_memory_interval_days (default 7).

The last successful scheduled mode is durable PostgreSQL runtime state. A failed/crashed run retains the Redis active marker and resumes the same mode; the alternation advances only after successful completion.

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

Windows PDF rendering is self-contained under tools\weasyprint. Invoke the bundled standalone executable/wrapper rather than mixing its frozen DLL set into Norm's Python process.

Installer acceptance proves both --info discovery and actual PDF creation.

## Installer/package contract

The installer is an in-place package manager. It preserves configured/persistent state, removes package-owned files no longer in the payload, migrates config through explicit precedence, and binds the payload by SHA-256.

Portable source excludes .venv, compiled norm.exe, caches, secrets, SSH private material, and machine-private topology.

## Documentation contract

Only RELEASE_NOTES.md is historical.

README.md, CURRENT_STATUS.md, DEVELOPMENT_NOTES.md, MAINTENANCE_VERIFICATION.md, FUTURE_IMPLEMENTATION_NOTES.md, and SOURCE_PACKAGE.md describe current state, current contracts, current evidence, or active backlog only.
