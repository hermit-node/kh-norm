# Norm development notes

These are current engineering contracts for **Norm 0.55.9r2**. This file intentionally contains no release chronology.

## Architectural authority

- PostgreSQL is the durable authority for tasks, history, memories, summaries, maintenance notes, and long-term state.
- Redis is hot/transient coordination state: queues, ingress, validation evidence, suppression/transition hints, and delivery state. GUI ingress keeps suppressed prompt-ID tombstones across `/flush-suppressed` to prevent a claimed but not yet submitted prompt from being resurrected by a later idle transition. They are cancellation authority, not user-visible delivery records. Future tombstone pruning must first prove the prompt has no claimed/queued/HTTP/uncertain owner; never treat a cleared queue view alone as proof.
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

Regular batch condensation is N1-gated for up to four N2 attempts. N2 ranks and rewrites; N1 judges accept/retry. A batch at or below 1,800 characters is the ideal early-stop result; 3,400 characters is the practical ceiling. `max_segments` remains generation continuation, not retry count. The final working-memory merge uses its own larger 6,000-character ideal / 9,000-character practical ceiling.

Manual force means run now, not ignore history scope.

Compact-record replay validation remains separate from the working-memory snapshot: one compact record in, no neighboring memory, no prior replay, no tools, maximum 4,800 × 4 continuation budget, authoritative source used only by grader/repair, replay text discarded after the decision.

A failed compact record may be repaired from its own authoritative task source and retried. The persisted row must be the repaired row rather than the pre-repair version.

## Scheduled maintenance contract

Scheduled maintenance uses independent clocks: **reductive** runs every `memory.scheduled_memory_interval_days` (default 7), while **destructive** runs every `memory.destructive_memory_interval_days` (default 21). A destructive run also satisfies the currently due reductive pass.

The last successful reductive and destructive timestamps are durable PostgreSQL runtime state. Transient crashes/failures may retain the Redis active marker for same-mode resume; each clock advances only after successful completion. ModelOutputTruncated is deterministic attention state, not an automatic retry signal: Norm writes durable weekly_maintenance_parked state, marks the Redis run requires_attention with auto_resume=false, and waits for explicit operator resume.

Scheduled maintenance is first-class operator-visible work. The worker idle flag is cleared while it runs; /status/busy carries a maintenance object; /queue and /queue-full display active or parked maintenance even when the prompt queue is empty. If there is no active/queued user task ahead of it, /suppress-task durably parks the maintenance run, checkpoints existing maintenance state, cancels the active model call, and disables automatic resume. /resume-task maintenance clears the durable park and explicitly resumes the saved run when the worker is idle.

Scheduled reductive uses recent incremental background-memory condensation. Scheduled destructive sweeps the complete historical archive with the full hierarchical validation/merge rules below and may omit N2-proposed useless records only after explicit N1 approval.

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

The public package does not vendor that generated native runtime. package-manifest.json pins the official upstream URL, version, and archive SHA-256. tools\fetch_weasyprint_runtime.py is the canonical fetch/repair implementation; Installer 1.6.9 calls it when the runtime is absent/stale, and tools\weasyprint.cmd calls the same helper for lazy repair. Existing valid runtime directories are persistent across source sync. Installer acceptance proves both --info discovery and actual PDF creation after ensure/repair.

## Installer/package contract

The installer is an in-place package manager. It preserves configured/persistent state, removes package-owned files no longer in the payload, migrates config through explicit precedence, and binds the payload by SHA-256.

Portable source excludes .venv, compiled norm.exe, caches, secrets, SSH private material, and machine-private topology.

## Documentation contract

Only RELEASE_NOTES.md is historical.

README.md, CURRENT_STATUS.md, DEVELOPMENT_NOTES.md, MAINTENANCE_VERIFICATION.md, FUTURE_IMPLEMENTATION_NOTES.md, and SOURCE_PACKAGE.md describe current state, current contracts, current evidence, or active backlog only.

## Optional web-browser plugin

`plugins/web_browser` is a schema-2 optional capability. Its exposed tool functions stay lightweight inside frozen `norm.exe` and delegate browser/extraction work to `C:\Norm\.venv\Scripts\python.exe -m web_runtime.worker`, with the plugin `src` directory on `PYTHONPATH`. This prevents optional Playwright/Beautiful Soup/Trafilatura dependencies from becoming mandatory PyInstaller payload.

Browser execution prefers installed Edge, then installed Chrome, then Playwright Chromium when already present. Browser profiles, cache, page material, and downloads live under `Documents\Norm\workspace\web`. The plugin permits only public HTTP(S) destinations and rejects loopback/private/link-local/multicast/reserved destinations. Crawl limits are explicit and the plugin does not implement CAPTCHA or anti-bot bypass.


## Norm 0.55.9r2: authoritative N1/N2 async contract

# Norm 0.55.9r2 development contracts (DRAFT)

This documents the asynchronous implementation and outstanding live-validation requirements; Celery,
GUI automation, or 0.55.9r2 has been implemented. The verified base archive
is Norm-0.55.9-portable-source.zip. Before shipping a real r2, incorporate
this contract into docs/DEVELOPMENT_NOTES.md and tests, update release
metadata/hashes together, and verify installer compatibility. Do not overwrite
C:\Norm or silently activate new workers.

## Non-negotiable N1/N2 responsibility split

N2: understands the original user request, plans work and executes ONLY the
currently assigned step, requests tools, reasons from observed results, performs
independent work while approved async operations are pending, and drafts the
conclusion. N2 cannot execute an unapproved tool or directly submit Celery work.

N1: owns the existing before_tool/after_tool boundary and all tool decisions.
Before approving any fresh observation, inspect the live Redis verification
pool for relevant already-observed evidence. Prefer replaying that result when
N1 judges it accurate, complete, applicable to the requested tool/arguments/
needed fact, and sufficiently current for its source. Age by itself does not
mean stale or reliable. Reuse preserves the original result/provenance; it does
not call the tool, fabricate evidence, or increment fresh-observation counts.
Actual source mutations must invalidate affected cached evidence.

If no sufficiently trustworthy observation exists, N1 approves execution,
applies the existing authorization, identity, guard, loop and audit rules,
and passes the approved tool request to the dispatcher. N1 receives resulting
evidence; N2 consumes the answer. Celery is NOT N1 or N2 and never decides
whether a tool is warranted. Preserve operator slash-command exceptions.
Do not allow the normal protected execution path to silently bypass N1 when
its gatekeeper is missing. Maintain N1's final-review authority.

## Generic Celery adapter for existing and future plugins

Do NOT create one Celery task per plugin, and do NOT serialize arbitrary Python
lambdas/functions/callables to workers. Use one registered task whose message
contains only an immutable dispatch identifier. Its worker looks up the
N1-approved, schema-validated request, checks approval identity, expiry,
tool permissions, tool schema, plugin/source fingerprint and task ownership,
resolves the existing verified Norm tool registry, then executes through the
authorized tool executor. Prevent a worker from consuming forged/stale requests.

Illustrative DESIGN ONLY (not implementation):

    @celery_app.task(name="norm.execute_approved_tool")
    def execute_approved_tool(dispatch_id):
        request = approved_store.claim(dispatch_id)
        validate_approval_and_registered_tool(request)
        result = authorized_executor.execute(
            request.tool_name, request.arguments
        )
        result_inbox.persist(request.dispatch_id, result)
        completion_events.publish(request.dispatch_id)

New registered plugins should need no Celery code change. Handle per-plugin
serial/parallel classification, timeouts, resource budgets, sensitive actions
and filesystem/secret policies. Avoid routing runtime-bound objects or
unserializable clients across worker processes.

Windows Celery multiprocessing/fork assumptions must not be imported. Start
with four independent Windows-compatible solo workers as an optional setup,
plus measured resource limits; do not claim four workers share an in-memory
PostgresPool. Each process has its own bounded instance of Norm's existing
connection-pool implementation, or workers call a separately managed pool
service if true central sharing is required. Four threads INSIDE one worker
process could share one pool, but Norm's current plugin manager serializes
calls behind a process-wide lock, so threads alone do not provide true
plugin-call parallelism until that mechanism is reviewed safely.

Redis DB5 is a proposed Celery transport segregated from Norm's DB0 state.
Use bounded queues and explicit worker health/readiness checks. Only the
interactive Windows-session-1 desktop helper can operate GUI controls.
GUI input remains serialized irrespective of how many Celery workers exist.
GUI reads/screenshots can be queued independently when safe.

## Nonblocking execution contract

A tool proposal always enters N1 first. If adequate cached evidence exists,
N1 returns it immediately to N2 with source/provenance. Otherwise N1 checks
whether an equivalent approved tool job is already pending; attach a waiter
rather than dispatching duplicates. For a new approved async call, return
a small pending receipt (dispatch_id, originating task_id, step_id, tool,
dependency identifiers and expected completion behavior), not a fake result.
N2 can progress with independent work, but must not pretend the missing fact
is known or finish a dependent step without it.

Celery publishes completion/failure to N1 through a durable result inbox.
N1 records fresh evidence and verification provenance, then delivers it once
to the SAME N2 task at a safe model/tool boundary, preserving task identity,
pending dependencies and completed work. If all remaining work depends on
outstanding results, park/yield the same task instead of spending model turns
polling or fabricating a new task. Support timeout, cancellation/suppression,
duplicates, stale approvals, retries and restart/recovery without double
side effects. Never redo external mutations on an ambiguous retry.

PostgreSQL stores authoritative durable job/task relationships and the outbox;
Redis handles live notifications and ephemeral transport. Preserve control
envelopes with typed status: pending/completed/failed/cancelled and distinguish
a pool replay from a fresh observation. Ensure no N1/N2 responsibilities move
into the Celery task.

## HIGH PRIORITY: context, resumption, and conclusion-step regression

This has failed on several earlier Norm versions: after many completed steps,
especially at step 7/7 or final user-facing conclusion, Norm loses or confuses
context and starts the original task over, repeats previous completed actions,
invents a new task, or overwrites earlier verified work. Treat this as an
active regression risk, not an already-proven live fix.

The immutable verbatim original_user_prompt supplies user intent/constraints,
NOT a new executable assignment. The current scoped work_request and
active_assignment identify what must be performed NOW. Carry both separately
through every JSON envelope, N1/N2 model call, child task, recovery record,
chunk/context condensation, and final-conclusion processing. Never present
the original root request alone as the new execution instruction at conclusion.

Persist completed step results, their evidence/provenance, current step scope,
outstanding job IDs/results, pending dependencies and original constraints.
Do not generate a new task ID when simply resuming the same active conclusion
after a transient failure. Preserve PASSED_TO only for genuine terminal
recovery according to its existing contract; do not substitute it for an
ordinary continuation. N1 verifies the final candidate against completed
work and user intent without ordering N2 to repeat the root task.

Acceptance tests MUST include:
1. N1 pool hit: exact observed payload reused, original executor not called.
2. N1 pool miss: one authorized Celery dispatch; N2 receives pending receipt.
3. Two concurrent equivalent proposals: one execution, two valid waiters.
4. New future plugin: registered tool resolves without Celery task changes.
5. Rejected/altered/stale approval: zero tool invocations.
6. Several independent tool calls: results arrive out of order and match
   correct task/step; N2 continues unrelated work.
7. N2 awaits only required dependencies; no busy-loop model polling.
8. Worker/process restart: pending jobs survive; no duplicate mutations.
9. Desktop: screenshots work in session 1; mouse and keyboard actions serial.
10. At conclusion step 7/7, root user prompt remains provenance only;
    previously completed steps are not re-executed, no new task ID appears,
    and verified earlier results are preserved.
11. N1 rejection/revision at final review resumes ONLY the affected final
    candidate/relevant scoped step, not the whole root workflow.
12. Context condensation, large outputs, shutdown and recoverable tool
    errors preserve pending receipts and complete-step evidence.

No live Norm files are modified by this draft. Only package tested, verified
and deliberately installed code as completed functionality.

## Initial r2 rollout safety

Celery initially handles only N1-recognized information/inspection tools
(excluding the special raw read_file chunk stream). Mutating or unknown tools
remain on the existing synchronous N1-gated path. Generic Celery worker
dispatch code supports registry-based tools, but asynchronous mutation should
NOT be enabled until cancellation, at-most-once side-effects, plugin identity
checks and full live recovery tests pass.
