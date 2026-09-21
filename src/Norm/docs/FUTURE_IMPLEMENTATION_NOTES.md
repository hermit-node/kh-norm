# Norm Future Implementation Notes

This is the **single canonical backlog/design notebook** for behavior that is not fully implemented or not yet promoted. `README.md` describes operation, `CURRENT_STATUS.md` describes deployed/current facts, and `DEVELOPMENT_NOTES.md` preserves engineering history and lessons. Those files may point here but should not duplicate active future designs.

## Dedicated coordinator and transition verification

Target architecture:
- A separate coordinator process/machine normalizes and classifies user input, retrieves bounded relevant PostgreSQL/local-source context, submits complete work to Norm, watches Redis/PostgreSQL, independently verifies transitions, delivers the final response, confirms transient cleanup, and persists a compact durable summary.
- The worker executes bounded jobs; the coordinator owns chain sequencing, parked dependencies, retries, retry ceilings, escalation, maintenance triggers, and final delivery policy.
- Python/runtime invariants should reject malformed or blank queue entries before Redis. If repeated blank/corrupt entries appear, run a bounded maintenance action rather than creating another ordinary task.
- At retry exhaustion, perform one bounded troubleshooting/recovery pass and then escalate rather than spinning indefinitely.
- Keep queue primitives reusable so the future coordinator can call the same operations rather than duplicating worker internals.

Independent step-result verification:
- Compare each returned step result against the step's explicit scope, verification requirement, and observed evidence before allowing dependent work to trust it.
- Store a compact pass/fail verdict and explanation; a non-empty model response is not sufficient proof of completion.
- On failure, reopen/repair the exact offending step rather than advancing the chain or rewriting only final prose.
- Preserve failed results and verifier explanations for debugging/training, but do not inject them as trusted downstream context.
- Prefer a fast verifier path; it should judge the bounded step rather than redo the task.

## Structural evidence enforcement and repair routing

- If a step explicitly requires execution/runtime/file evidence, do not mark it completed until the required evidence exists and satisfies the contract.
- A final-verifier rejection caused by missing evidence should reopen the exact originating step/tool requirement with the verifier's reason and preserved context.
- Final response repair should be reserved for response/requirements defects that can actually be fixed from already-verified evidence.
- Preserve the current separation between deep reasoning verification and the small strict JSON acceptance gate.

## Normal context budgeting / compaction

- Add a routine size budget to normal completed-step context; one path still calls `completed_step_context(..., max_chars=None)` outside oversize recovery.
- Compact by relevance and preserve durable pointers to omitted detail rather than copying every prior result into each prompt.
- Reuse the existing oversize/pointer philosophy without recursively spawning recovery merely to trim normal context.

## Planner decomposition proportional to task complexity

- Keep explicit planning and evidence coverage, but collapse adjacent low-risk inspection work when one adaptive step can safely do it.
- Do not reduce verification rigor just to reduce step count.
- Simple bounded tasks should usually need only the minimum inspect/mutate/verify structure justified by the request; extra decomposition should be driven by uncertainty, destructive side effects, or genuine dependency boundaries.

## Persistent local-file knowledge references

Add a durable local knowledge-source layer so PostgreSQL can reference approved files on local storage without copying the full source text into memory.

Target behavior:
- Persist stable source identity, canonical/current path, SHA-256, modified time, title/type/domain, tags/aliases, priority, summary, and status.
- Maintain a section/table-of-contents index with headings, line ranges, summaries, keywords/entities, and retrieval data so only relevant sections are read at answer time.
- Filesystem remains source of truth; PostgreSQL stores durable map/index and retrieval associations.
- Support priorities such as `local_authoritative`, `local_preferred`, and `reference`; matching preferred sources should be consulted before general web search.
- Detect changes and re-index. Mark missing files as missing rather than silently deleting their records; use stable identity/hash to survive relocation.
- Reinforce successful source/task associations so related future work retrieves the right local section efficiently.

## Memory housekeeping and retrieval

- Background condensation should remain non-recursive: run directly against the low-level model client, not through the normal Norm task/Redis/history path, and checkpoint maintenance continuation state separately.
- Periodically traverse curated/current memory rows to detect true duplicates, contradictions, resolved/superseded problem state, and stale task residue; age alone is never a deletion criterion.
- Permanently delete genuinely redundant/superseded rows after preserving any reusable lesson in a current compact record.
- Future coordinator retrieval should search curated memories plus relevant historical messages, maintenance/runtime notes, task summaries/steps, and validated `task_history`; rank semantic/project/thread relevance above simple recency.

## Evolving user communication / semantic profile

Create a PostgreSQL-backed model of the user's own communication patterns to improve normalization and ambiguity recovery without overriding explicit current instructions.

Desired behavior:
- Store raw/derived observations for recurring shorthand, phrasing, semantics, spelling substitutions, argument structure, tone/context modes, and interpretation corrections.
- Track stable versus context-specific patterns and retain provenance, first/last seen, repetition, confidence, and examples.
- Treat corrections as strong learning signals; track expectation/deviation pairs where Norm repeatedly misreads the user's intended semantics.
- Update gradually with recency/repetition weighting instead of rewriting the profile around one anomalous message.
- Inject only the relevant compact profile slice into the live prompt.

Possible schema: `user_input_observations`, `communication_profile_items`, `interpretation_corrections`, and `profile_deviations` with version/confidence history.

## Chart-axis enforcement

- Encode the generic `CHART_AXIS_GATE.md` contract into analyzer/runtime logic rather than relying only on prompt discipline.
- Require at least two exact visible numeric anchors, label-to-grid association, slope/intercept/residual checks, pane identity, monotonic/bracket validation, and rejection of pane boundaries/crop edges/unlabeled geometry as price anchors.
- Preserve regression-specific examples separately; do not hard-code QQQ/SPY coordinates into the generic transform.

## Validation debt

- Run one clean end-to-end task under the promoted shell-enabled executable proving `run_command` evidence flows through worker execution, step verification, final verification, and durable evidence under the exact deployed build.
- Validation debt is not the same as missing functionality; once proved, remove this item rather than leaving a permanent TODO.

## Documentation/configuration rule

- `README.md` is the concise operator overview and should not contain an active backlog.
- `CURRENT_STATUS.md` records deployed/current facts and factual current limitations; it should point here for proposed solutions.
- `DEVELOPMENT_NOTES.md` preserves chronological engineering history and lessons; historical proposals should point here rather than remain as competing active specifications.
- Paths for the maintained status/history/future files belong in `config/settings.ini` so future relocation does not require scattering hard-coded paths through the runtime.

## Low-priority status reporting cleanup

- `/status/busy.retry_count` currently reflects the whole Redis retry/holding stream length, so deferred or dependency-blocked `attempt=0` entries can inflate the displayed number even when no execution retry occurred. Future cleanup should separate actual retry attempts from blocked/deferred/holding entries; this is a status-labeling/reporting issue, not evidence of four failed retries.
