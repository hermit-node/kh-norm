# Norm development notes

## Working pattern
- Sol/ChatGPT owns architecture, exact control flow, constraints, and acceptance criteria.
- Norm is the local coder/typist: give it one bounded method or file at a time.
- After every generated unit: save to staging, inspect, `py_compile`/test, then promote.
- Avoid giant non-streaming generations. They hide progress and are harder to recover from.
- Prefer `plan -> one bounded step -> verify -> log completion -> next step`.

## Ollama API gotchas
- For PowerShell -> `/api/generate`, make `prompt` an explicit scalar string before `ConvertTo-Json`.
- Prefer `[System.IO.File]::ReadAllText(...)` over implicit `Get-Content -Raw` serialization.
- `keep_alive` must be numeric `-1`, not string `"-1"`.
- Direct `ollama run norm` bypasses coordinator persistence. Persistent chat must go through `norm.exe`.

## Remote editing / Windows gotchas
- Large code payloads through remote write/command tools can be blocked. Work around by generating smaller units and writing them locally from Norm's response.
- Do not ask Norm for a whole project/file if a method-sized task will do; exact method contracts returned in ~10-25 seconds and were reliable.
- Never overwrite a live source file directly from an unvalidated model response. Stage first, inspect, then promote.
- Keep a source snapshot before multi-file changes.
- When assembling indented Python methods, do NOT use `.Trim()`/`.strip()` on the whole chunk; it can remove the first line's class indentation. Use trailing-only trimming.
- PowerShell text writes can add UTF-8 BOMs; pytest rejected a BOM in `pytest.ini`. Use UTF8 without BOM for source/config files.
- Nested PowerShell/Python quoting is fragile. Prefer `psql` or a small script for database inspection instead of complex one-liners.

## Validation rules
- `py_compile` proves syntax, not class structure; explicitly check expected attributes/methods after assembly.
- Run a real end-to-end smoke test after schema or persistence work, not only mocked tests.
- Keep PostgreSQL tests isolated when possible; verify temporary schemas/Redis keys are cleaned afterward.
- PyInstaller source should pass `--check` before packaging; then run the packaged EXE's `--check` too.
- PyInstaller `--onefile` may show a parent and child `norm.exe`; that is expected.

## Future coordinator migration
- Current prompt-worker maintenance/recovery behavior is intentionally temporary and should be easy to move into a dedicated coordinator later.
- Dedicated coordinator should own queue policy: chain sequencing, retries, parked chains, idle restoration, retry ceilings, escalation, and maintenance triggers.
- Python should enforce hard invariants before Redis (for example, reject blank prompts instead of queuing them).
- If Norm encounters 3 blank queue entries consecutively, trigger a hidden maintenance turn rather than another normal task.
- That maintenance turn should give Norm bounded Redis queue tools so Norm can inspect work/retry state, remove blank artifacts itself, verify cleanup, and resume normal processing.
- Keep those Redis operations as small reusable queue primitives so the future coordinator can call the same tools without rewriting queue logic.
- At retry limit, coordinator should let Norm troubleshoot/recover first; unresolved work should move to the ChatGPT/Sol escalation path rather than spin forever.
- Goal: worker executes jobs; coordinator decides what happens next. Keep those responsibilities separable now so migration is mostly rewiring rather than redesign.

## Verification architecture direction
- Keep deep reasoning verification separate from the final machine-readable acceptance gate.
- Current deployed gate is intentionally narrow: dedicated no-tools Ollama generation, `think=False`, strict JSON schema, and local retry of malformed/blank verdicts.
- This gate should not redo the task. Heavy reasoning, tool use, correction, and deterministic action checks happen before it.
- A malformed verdict envelope is a verifier-transport problem, not evidence that the underlying project task failed; retry only the verdict turn before entering task recovery.
- Observed with the current Qwen/Ollama stack: schema-constrained `think=True` generation can return a blank response, while the same schema with `think=False` returns valid structured JSON. Do not couple the strict verdict envelope to extended thinking mode.
- Future target architecture: primary coordinator/worker -> deterministic verification -> separate coordinator/verifier model with its own independent thinking pass -> strict structured acceptance gate -> correction loop if rejected.
- The future secondary coordinator/verifier should perform the real skeptical reasoning: challenge assumptions, detect omissions and contradictions, and compare the candidate against observed evidence independently of the worker.
- Preserve the strict JSON gate after that reasoning as a simple reliable protocol boundary; do not make the protocol gate itself responsible for deep reasoning.
- This separation lets the reasoning verifier model change later without destabilizing queue semantics or the acceptance/result contract.

## 2026-09-14 planner/decomposition and step-aware verification
- Conversation requests are no longer wrapped in one opaque generic `respond` step. `conversation_service.py` first generates an explicit 1–8 step plan, independently checks it, optionally repairs it once, and then creates a larger `TaskPlan` containing planning/verification meta-steps plus the generated action steps.
- The authoritative original user request is stored separately from each Norm-generated execution step. Action-job metadata carries `prompt_origin=norm_generated_step`, the original user message ID/prompt, generated step index/count, and conversation/thread provenance.
- Redis action nodes use one shared `chain_id` and stable logical node IDs (`<task_id>:step-NN`) with `previous_prompt_id`, `next_prompt_id`, and `chain_index`. `enqueue_chain()` writes them transactionally and immediately re-reads/verifies the complete link structure; bad chains are removed rather than accepted.
- Redis is the execution chain, not the long-term result store. Completed step summaries/status/verification are checkpointed in PostgreSQL `norm_runtime.task_steps`. `completed_step_context()` loads prior completed step results and injects them into later prompts as trusted durable context.
- A read-only Pogicraft review exposed an old architectural assumption in the global verifier: it rejected non-user-facing output because historically every work item was treated as the final answer. That conflicts with real intermediate read/inspect/analyze steps.
- Current fix: intermediate action nodes (`job.next_prompt_id` non-empty) return their bounded result directly after tool execution/deterministic checks and are durably checkpointed for downstream steps. They do not enter the final-answer quick-review/skeptical-verifier loop. The last action node (`next_prompt_id` empty) still receives the existing quick completion review when eligible plus the bounded skeptical verifier/correction loop before becoming the user-facing task result.
- For multi-step plans, keep the planner's final synthesis step explicit so it turns completed prior work into the direct user-facing answer. For a truly one-step request, the sole action is already final and no duplicate synthesis node should be added. Do not require intermediate steps to look like final responses.
- Planner schema-constrained generation should remain `think=False` with the current Qwen/Ollama stack; structured `think=True` previously returned a blank final JSON response.
- Step-aware build promoted on 2026-09-14: live SHA-256 `D8A420D24AEA2773C6D81DA062890BAF8D420BB7AA2361E90CDB75E3CEA57AAB`. `/health` passed on `12543`, and a fresh Pogicraft run produced an eight-node verified Redis chain with `step-01` actively claimed after restart.

## 2026-09-13 verifier/cancellation incident
- A read-only portfolio review exposed a runaway path: the final verifier emitted roughly 86 KB of malformed JSON and was retried while the logical task kept recovering.
- `/stop ollama` previously cancelled only the active generation; the worker then treated `ModelGenerationCancelled` like an ordinary failure and requeued/recovered the task.
- `ModelGenerationCancelled` now has its own terminal path: persist `cancelled`, ACK the current work item, reset task-round bookkeeping, and do not retry automatically.
- The final verdict gate is now deliberately tiny: `num_predict=384`, at most four issue strings, 240 characters per issue, and a 4096-character hard sanity ceiling.
- Three malformed/oversized verdict attempts or three unsuccessful correction cycles raise `VerifierProtocolError`, write `SOS.readme`, and safety-stop the affected task instead of entering ordinary recovery.
- `/shutdown norm` drains/checkpoints current work and exits; `/shutdown norm now` cancels the active model call and exits promptly after cancellation is persisted.
- The runaway task's stale 51-round counter was cleared after verifying there was no active/pending work.
- Keep shutdown behavior simple and user-facing: one console command should be enough to leave durable state and stop Norm cleanly.

## 2026-09-13 port restoration
- `12543` is the intended Norm HTTP/chat port.
- A staged README preserved that intent even though runtime config had drifted to `8765`.
- Runtime config, `norm_main.py` defaults, `http_api.py` defaults, `start-norm-ollama.cmd`, and current docs were realigned to `12543`.
- After rebuild, packaged `--check` passed, `/health` succeeded on `12543`, `8765` was offline, and a live chat smoke returned `PORT_OK`.
- Do not "correct" `12543` back to `8765` just because older runtime backups contain `8765`; treat `CURRENT_STATUS.md` as authoritative.

## 2026-09-15 single-source port settings — implemented
- `config\settings.ini` is now the source of truth for the three runtime service ports: Ollama, Norm HTTP/chat, and Norm activity/control.
- `norm.exe` reads that file at startup and logs the resolved three ports before connecting or binding.
- `Open-Norm-Console.ps1` and `start-norm-ollama.cmd` read the same file; neither maintains independent hard-coded values for those three ports.
- Operational hard-coded defaults for `11434`, `12543`, and `8766` were removed from the runtime path; `runtime.json` now retains hosts and other settings but not those three port numbers.
- Alternate-value tests proved `31434/32543/38766` propagated through the executable startup path, console arguments, chat-side Ollama client construction, and CMD parsing without rebuilding.
- The packaged live build passed `--check`; chat and activity health checks passed after promotion. Current configured values remain `11434/12543/8766`.
- Pre-promotion backup: `staging\pre-port-config-promotion-20260915-1912`.

## 2026-09-15 Redis lifecycle and typed queue — staged for final repush
- Do not rebuild the executable after each feature. Complete and source-test the full staged change set, then do one final PyInstaller build and post-build smoke.
- Every Redis job now carries an explicit request type: `prompt`, `internal_instruction`, `internal_direction`, `straightforward_direction`, `step`, `task`, `maintenance`, or `conclusion`.
- The worker applies a bounded processing contract from that type instead of inferring all queue traffic as the same kind of prompt. Old queue entries without the field remain backward-compatible as `step`.
- PostgreSQL is authoritative durable state. Final completion/failure/cancellation writes and verifies a terminal task summary before task-specific Redis working state is deleted.
- Redis cleanup is task-scoped, not `FLUSHDB`: work/retry/escalation/dead entries, round counters, and DB0 live state/events for that task are removed only after durable verification.
- On startup, pending work from a previous worker is requeued for processing. Running tasks with work/retry entries are preserved for execution; unrecoverable running tasks are summarized/finalized in PostgreSQL and then cleared from Redis; Redis-only tasks with no PostgreSQL ledger are recorded as maintenance notes and cleared.
- On shutdown, unfinished tasks are preserved for the next startup. PostgreSQL receives a maintenance note for each unfinished task, explicitly flagging tasks with no recoverable work/retry entry as stuck.
- A Redis reconciliation pass runs between work at most once every 14,400 seconds (4 hours), cleaning verified terminal leftovers and finalizing otherwise-unrecoverable stale running tasks.
- `runtime_maintenance_notes` in PostgreSQL records startup/shutdown/periodic maintenance decisions and is included in background-memory consolidation.
- Coordinator remains local today, but queue protocol version 1 and the explicit typed Redis envelope are the boundary a future coordinator on another machine can emit without changing worker semantics.

## 2026-09-15 final runtime deployment result
- Final build hash: `0A207A5A991287631C9A6A8C63CB1F071B2C77D3469A180E28784D391FED1A5A`.
- Startup cleanup verified against the real Redis/PostgreSQL state: 43 terminal stale tasks cleared, one unrecoverable old running smoke summarized/finalized then cleared, leaving no DB0 task keys and no DB1 task entries.
- Post-build live worker completion verified PostgreSQL-first terminal summary followed by task-scoped Redis deletion.
- Shutdown note + next-startup summarization/cleanup was verified with a synthetic stuck task.
- The generic `/api/chat` smoke used for this deployment was rejected earlier by the pre-existing planner verifier as redundant (`Prepare exact response` vs `Synthesize final answer`). That planner behavior is separate from this lifecycle change and should be addressed in a later planner-specific edit rather than mixed into this build.

## 2026-09-15 planner/simple-request repair — deployed
- Root cause of the 500 smoke failure: planner wording required a final synthesis step even when the entire request was one bounded action; the verifier then correctly called the generated prepare+synthesize pair redundant.
- New contract: a truly simple bounded request must produce exactly one action, and that sole action is itself the user-facing final step. Separate synthesis is required only for multi-step work.
- The independent verifier is explicitly told to reject needless prepare-then-synthesize duplication while accepting valid one-step plans.
- Port/operator configuration was generalized from the initial one-purpose `ports.json` into human-editable `config\settings.ini`; `[ports]` is only the first section so future operator settings can share the same file.
- One PyInstaller build was performed after source tests; live hash `D29128AEFFE21586AD838DCA3BCF1924CAE9A69FC4D813340B62B610BE3B484C`. Packaged health passed and the exact-response `/api/chat` reproducer now returns `PLANNER_SMOKE_OK`.
- Pre-promotion backup: `staging\pre-settings-planner-promotion-20260915-2003`.

## 2026-09-15 PostgreSQL memory consolidation policy
- Manual consolidation backed up memory_items, memory_threads, and background snapshots to `staging\postgres-memory-preconsolidation-20260915.json` before cleanup.
- Policy: age alone is not a deletion criterion. Preserve correct old facts when still useful, explanatory, or preventive; remove only high-confidence redundancy, completed/stale task-state, superseded detail, smoke-test trivia, and low-value repetition.
- Cleanup removed 19 redundant/stale active memory items and sanitized one project memory so it retains the credential-location/risk without storing credential values. Active memory_items changed 105 -> 86.
- A new compact global snapshot was written: 5,967 characters versus the prior 8,785-character snapshot. Detailed project facts remain in project-scoped PostgreSQL memory/thread history rather than being copied into the global snapshot.
- The earlier delta-only weekly-consolidation idea is NOT the final housekeeping design. It is acceptable only as a cheap between-cleanups refresh. True memory housekeeping must traverse every curated/current memory record so it can detect duplicates, contradictions, newer resolutions, and stale task/problem state.
- Memory retention policy: age alone never makes a record disposable. Preserve useful old facts and lessons. If a record is genuinely superseded or redundant, permanently delete it rather than keeping a dead `superseded` copy. If an old problem contains a reusable lesson, distill that lesson into a current/historical/resolved memory first, then delete the obsolete source record.
- Future coordinator responsibility: before substantive work, traverse PostgreSQL for task-relevant context and inject only the best matches into the live prompt. Search must include curated memories plus relevant historical messages, runtime/maintenance notes, task summaries/steps, and similar prior tasks. Ranking should favor semantic similarity and same-project/thread relevance; recency is only a modest signal, not a hard filter. Resolved historical lessons remain retrievable; superseded/redundant records should no longer exist.
## 2026-09-16 immediate tool-evidence persistence — deployed
- Expensive tool evidence is now persisted immediately when each tool call returns, before any later model/tool-slice failure can discard it.
- Redis `step_evidence` now retains bounded semantic payloads such as `vision_image.answer`, file contents, paths, image counts, and analysis metadata instead of only `ok/path/hash` success markers.
- PostgreSQL now has `norm_runtime.task_evidence`; raw JSON arguments/results are written there immediately for durable recovery.
- PostgreSQL fallback context for later steps now includes persisted tool evidence, so a restart or missing Redis state does not reduce a prior vision/file step to a thin step summary.
- Verifier/reviewer evidence text now includes bounded semantic evidence, not only success metadata.
- End-to-end packaged smoke verified that a `read_file` tool result persisted the file content into `task_evidence` and still produced the expected final response. Smoke records/file were removed afterward.
- Live executable SHA-256 after promotion: `9999FBB263E2647C5E39A6D4CE42AA590AF13C39C9D6A38834F8C5816F7B8AC9`.
- Pre-promotion executable backup: `staging\pre-evidence-promotion-20260916-1143\norm.exe`.

## 2026-09-16 — JSON control-plane protocol v2

Norm's internal coordinator/worker/verifier control plane now uses schema-validated JSON envelopes while preserving ordinary natural-language content inside typed fields.

- User requests are normalized into a `command` envelope with category, intent, mutation/research/media flags, and expected-output type/format.
- Supported command categories include `straightforward_direction`, `simple_task`, `maintenance`, `information_retrieval`, `task`, and `task_step`.
- Worker inputs are JSON `work_request` envelopes; durable worker outputs are JSON `step_result` envelopes.
- The plan contains a dedicated `final-verify` node. The final action creates a validated `final_candidate` JSON object and puts it back on the Redis work queue.
- `final-verify` validates candidate structure deterministically, then uses a schema-constrained semantic verifier for completeness and artifact/evidence support.
- Accepted final verification is a typed JSON verdict, not a magic prose marker. Narrow response/requirements revisions are requeued from preserved state instead of redoing completed acquisition or mutation work.
- Artifact outputs require observed write evidence plus matching read-back verification before final acceptance.
- Per-step verification also uses a schema-constrained JSON verdict rather than `VERDICT:` line parsing.
- Runtime queue protocol version is 2.

Pre-change rollback backup: `C:\Users\KHzz\Documents\Norm-backups\pre-json-protocol-20260916-162126.zip`; it includes a plain-SQL `pg_dump` of the full `norm_runtime` PostgreSQL schema/data as `norm_runtime_postgresql_memory.sql`.

## 2026-09-16 — CA8D primary storage failover — deployed
- Primary relative-file root is now `\\KH-CA8D\Local1675`; fallback is `C:\Users\KHzz\Documents\Norm\docs`. Existing explicit allowed roots remain available.
- Storage probes are bounded (2s, 30s cache). Relative reads/writes select CA8D when responsive and KHzz docs when CA8D is unavailable. Writes are never mirrored.
- Every file-tool result includes `storage_context` JSON. Worker `work_request` and durable `step_result` JSON include `context.resource_status.local_storage` / `resource_status.local_storage`, including exact source name, responsiveness, active root, degraded flag, error, and a note that generation continued without the unavailable source.
- Current live state during deployment: `ca8d_smb` unavailable to Norm because SMB credentials are not yet cached in Norm's Windows session; `khzz_docs` is active fallback. Once SMB auth works, CA8D becomes primary automatically after the status cache expires; no rebuild/restart is required.
- Source `py_compile`, source `--check`, packaged `--check`, live `/health`, and a packaged end-to-end JSON smoke all passed. Smoke task `chat-2adb56d2-bc94-4647-af9c-94c1bc63a181` recorded the expected degraded storage status.
- Pre-source backup: `staging\pre-ca8d-storage-20260916-180640`. Pre-promotion executable backup: `staging\pre-ca8d-storage-promotion-20260916-181151`. Live executable SHA-256: `8B4450BF882057B03546A84AF857FCA14A028E6A6F974F7B6AA0A0D298D4F68B`.

## 2026-09-16 — demand-driven connection checks and impaired-context reporting

- Connection availability is no longer treated as something to probe globally on every task. Optional resources should be queried only when the current operation actually needs them, or when the user explicitly invokes maintenance.
- `check_connection` is a targeted maintenance tool for exactly one named target: `ca8d`, `khzz_docs`, `postgres`, `redis`, `prompt_queue`, `ollama`, `tailscale`, or `dropbox`.
- An explicit file path under an already-known allowed root does not trigger unrelated storage checks. This was live-validated with the `goBackend` README task, which stayed entirely under `D:\LOCAL_Share\Code Projects\goBackend` and never touched CA8D.
- Relative local-file paths still use the configured CA8D-primary/KHzz-docs-fallback selector. The selector checks only what is needed to resolve that relative operation; writes are never mirrored.
- Required connection failures are represented through shared `resource_status` JSON: `context_state`, the fixed message `generating with impaired context`, and deduplicated `{connection, reason}` records.
- PostgreSQL entry/context reads now have degraded paths. If PostgreSQL fails during initial conversation setup, Norm can answer from the current request plus explicitly needed available tools while clearly reporting impaired context. This is not equivalent to normal durable execution and must not be documented as such.
- Dropbox remains unconfigured in the native runtime; the maintenance check reports that fact without pretending to query an unavailable connector.

## 2026-09-16 — current live validation after impaired-context build

- Current live executable SHA-256: `F9779CFE1DF00EE3F7457755BC06F663E4E1AE2C4A3339FB9F810AF6F4A96014`.
- Live KHzz Tailscale IPv4 observed during the documentation refresh: `100.95.94.29`; `http://100.95.94.29:12543/health` returned `{"status":"ok"}`.
- The earlier CA8D deployment note in this file records the state at that deployment point. Later demand-driven connection and PostgreSQL-degraded work supersedes any implication there that CA8D should be probed for every file task.
- `chat-a75c8e0b-516d-4e77-932e-0ee779c79575` created a grounded README for `D:\LOCAL_Share\Code Projects\goBackend` and passed structured final verification without a CA8D query.
- `chat-69010cdb-40f5-4df4-a446-98e9a2b3598b` finished the previously deferred synthesis of all 32 SPY/QQQ hypotheses. Final artifact: `docs\SPY_QQQ_20260916_32_hypotheses_synthesized.md`; size 12,758 bytes; SHA-256 `B0D71CC8754E528217EB94D223841DC02C85E298B109107ACB14131A537C504A`.
- The raw 32-path artifact remains preserved as `docs\SPY_QQQ_20260916_32_hypotheticals_unsynthesized_corrected.md` rather than being overwritten by synthesis.
- Documentation refresh backup: `staging\pre-doc-refresh-20260916-194654`.

## 2026-09-16 — Layered/N-dimensional image analysis v3

- Live image analyzer now points to `tools\image_analyzer_v3.py`; v3 wraps v2 so the prior analyzer remains available as a baseline/recovery path.
- Chart analysis now distinguishes reference geometry (grid/pane coordinate system) from variable data objects (wicks, fills/volume, averages, indicators) and annotation/UI.
- Occlusion is represented as an inferred render-order relationship between data objects rather than as a flat pixel classification.
- Suspicious thin traces are detected using color family, local darkness, thinness, vertical continuity, edge orientation, pane membership, and broad-fill overlap.
- A multiplicative trace-strength score prevents one strong feature from averaging away weak evidence in other dimensions.
- Ambiguous/high-impact regions receive focused rescans across multiple non-geometric views (original, contrast up/down, saturation, gamma bright/dark), preserving coordinate alignment.
- Focus confidence is calibrated against the entire primary price pane. Features are promoted only when cross-view evidence converges; otherwise they remain uncertain.
- Focus artifacts include 8x crops, whole-pane percentile, cross-view agreement, refined endpoint, and scene-graph metadata.
- Positive regression: `SPY_20260916_2037_failure_review.jpg` recovers buried red trace `x≈390, y≈440→492` with 100% cross-view agreement, ~99.923 percentile, and ~0.9997 focus confidence.
- Negative regression: `SPY_layered_vision_gentle_test_20260916.jpg` returns zero hidden-wick candidates under the same light settings.
- Runtime config backup before v3 switch: `staging\runtime-before-image-v3-20260916-230015.json`.
- Norm restarted successfully after the switch; live `/health` returned `status=ok`.

## 2026-09-17 — verifier, chart-axis, and remote-editing lessons

- Verified the deployed acceptance path no longer depends on magic prose such as `verify: accept` or `VERDICT: ACCEPT`. Final acceptance is `parse_json -> validate_final_verification -> verification["verdict"] == "accept"`, with deterministic consistency checks on completeness/evidence/issues/repair scope.
- A normal user-facing final reply may remain Markdown/text; only the internal verifier contract must stay structured JSON.
- Corrective QQQ audit demonstrated the axis failure mode clearly: pane-bottom geometry must never become a price anchor, and volume bars must remain separate from price-pane wick geometry. The corrected QQQ result passed structured final verification.
- SPY recast task `chat-f66a1cea-8a23-4ee8-9b80-b68f30b128fa` completed analysis but safety-stopped at final verification because the final candidate was truncated and omitted the explicit Phase-2 error-classification section. `SOS.readme` was the incident record at that point; the later 2026-09-17 maintenance pass verified the task terminal state and removed the live SOS.
- The same SPY run showed that endpoint detection and price conversion can fail independently: geometry found the important wick near `y≈492`, while a bad y->price transform converted it inconsistently with the visible 750 line.
- Added `docs\CHART_AXIS_GATE.md` as the generic chart price-axis contract: visible numeric anchors only, explicit label-to-grid association, residual/spacing checks, pane identity, monotonic ordering, and local bracket checks before price conversion.
- `docs\QQQ_AXIS_GATE.md` is now a regression-specific example rather than the general rule. The generic gate is not yet hard-wired into analyzer code; that remains a pending runtime hardening choice.
- Remote Windows rule: stop escalating nested PowerShell/Python/SQL quoting once it becomes fragile. Prefer a small script/file. `C:\Users\KHzz\Documents\Norm\verbatim_lines.py` is the newline-safe helper for verbatim append/insert work.
- Documentation refresh on 2026-09-17 trimmed deployment-history detail from the operator README, updated `CURRENT_STATUS.md`, and preserved `SOS.readme` unchanged as forensic evidence.

- Redis inspection reminder: use Norm's `.venv` and Python Redis client with `config\runtime.json`; do not assume `redis-cli` is available. For active-work checks, use consumer-group pending/task state rather than treating stream `XLEN` as the number of live jobs.

## 2026-09-17 - code-first documentation/Redis maintenance pass
- Re-audited the live source tree rather than trusting handoff prose. Current worker is `app\norm_runtime\prompt_worker.py`; `prompt_worker_legacy.py` is retained history. Runtime config selects `tools\image_analyzer_v3.py`, which wraps v2.
- Reconfirmed protocol-v2 behavior from source: the final verifier consumes/returns structured control JSON while the accepted `final_candidate.user_reply` remains normal user-facing text/Markdown. This is expected architecture, not an unresolved verifier issue.
- Standing remote execution rule strengthened: use `C:\Users\KHzz\Documents\Norm\verbatim_lines.py` for essentially all operations beyond the simplest one-liners. Prefer creating a temporary Python script, running it, verifying results, then deleting it.
- Direct Redis audit found DB0 empty; DB1 work/retry/escalation/dead streams empty with zero consumer-group pending entries; DB2 empty. The sole DB1 key is the empty `norm:prompt:work` stream/group shell. Do not `FLUSHDB` a live DB1 merely to erase that infrastructure key.
- Processed the 08:18 SPY `SOS.readme`: later final verification succeeded at 09:37:41 and live `/health` was OK. The incident was preserved in the maintenance backup and the live SOS was deleted. Standing policy is now to treat SOS as transient task-resolution state: retain it until the originating task is durably completed or deliberately finalized failed in PostgreSQL, then remove it after preserving reusable lessons.
- Generalized `docs\CHART_VISION_CHEATSHEET.md`; removed QQQ-only anchors/candidate coordinates. QQQ-specific details remain in regression files. The generic axis gate remains instruction-level and is not yet an analyzer-enforced numeric transform.
- Memory housekeeping gap documented accurately: weekly background consolidation compacts summaries but does not hard-delete all redundant/superseded `memory_items`; full curated-row housekeeping remains manual/future coordinator work.

## 2026-09-17 - automatic processed-SOS cleanup deployed
- `prompt_worker._clear_terminal_redis()` now calls `_clear_processed_sos(task_id)`. A matching `SOS.readme` is removed only when the originating PostgreSQL task is `completed` or `failed`, terminal-summary verification passes, and the latest summary is nonblank. Cancelled/unresolved tasks do not clear SOS.
- Source policy tests covered completed, failed, cancelled, missing-summary, and mismatched-task cases. Source `--check` and packaged candidate `--check` both passed against live Ollama/Redis/PostgreSQL.
- Graceful promotion occurred with no queued/pending Redis work. Post-restart Redis: DB0 empty, DB2 empty, DB1 only the empty work stream/group with `XLEN=0`, pending=0, lag=0.
- Live executable SHA-256: `4B42B5C5AAC39CF7AB47F4783F19798D34047360D35FCEEE6F71A87CFCD4B99C`. Pre-source backup: `staging\pre-sos-cleanup-20260917-095711`. Pre-promotion executable backup: `staging\pre-sos-cleanup-promotion-20260917-100411`.
## 2026-09-17 aggressive PostgreSQL consolidation
- Fresh pre-prune backup verified at `Norm-backups\Norm-current-post-maintenance-20260917-1042.zip`; this one includes a PostgreSQL export because a destructive memory/history prune followed. Routine code backups do not need PostgreSQL exports.
- Permanently deleted 6 `memory_items` already marked superseded after verifying every replacement still existed and was active; 6 `memory_threads` links cascaded. Superseded-memory count is now 0.
- Consolidated repeated same-day one-off/retry work by authoritative request rather than title. Deleted 14 redundant task runs, cascading 41 task-step rows, 22 evidence rows, and 14 task-summary rows.
- Replaced those retry/smoke chains with 4 compact `history_consolidation` maintenance notes. Retained the useful completed Pogicraft review as the representative record; discarded failed/cancelled/retry-only copies.
- Replaced 3 stale global background snapshots (17,642 combined characters) with one 4,364-character current cross-task snapshot; old smoke trivia and pre-protocol-v2 assumptions no longer enter default background context.
- Post-prune audit: 60 task runs, 301 task steps, 106 evidence rows, 60 task summaries, 113 active memory items; no same-day duplicate task groups remain by authoritative user request.
- Long-term retention principle: raw chats/tasks/evidence are temporary working history. Once a denser verified summary preserves the useful facts, lessons, decisions, artifacts, and unresolved items, lower-level records may be permanently deleted. Summaries themselves may later be consolidated and deleted.


## 2026-09-17 - replay-validated deep PostgreSQL history consolidation deployed
- Added searchable `norm_runtime.task_history` plus nullable `task_runs.effectiveness_note`. Every non-literal terminal task attempts to store one concise future-effectiveness note; literal arithmetic/exact-string smoke tasks skip it.
- Idle deep maintenance targets terminal task history older than 30 days, on a 7-day cadence, up to 500 source tasks per pass. Exact same-day retries of the same authoritative request collapse into one history record; unrelated tasks in the same thread do not.
- Destructive maintenance is fail-closed: a real plain-SQL `pg_dump` of `norm_runtime` must succeed first. Compact records retain task ID/date, original request, final outcome, reusable lessons/new data, and what to do next time.
- Before raw deletion, Norm retrieves the new compact history with the old prompt and replays up to three sampled requests using only those compact records. Structured replay verification requires correct prior task/date identification, request understanding, lesson reuse, and sufficient context. Replay failure preserves raw history and keeps the SQL backup.
- Replay success validates the archive, hard-deletes covered task runs (cascading steps/evidence/summaries), deletes old conversation messages only when all attached threads have later covering summaries, removes older thread-summary versions and explicitly superseded memories, rebuilds the global background snapshot, and deletes the temporary SQL backup last.
- Normal chat now retrieves relevant validated `task_history` records by PostgreSQL full-text search so a repeat request can reuse prior work and cite the earlier task/date instead of reconstructing raw history.
- Isolated temporary-schema end-to-end test proved archive -> retrieval/replay -> validation -> raw task deletion -> background rebuild -> SQL backup deletion. Separate note test proved a non-trivial task gets an effectiveness note while `what's 2+2?` does not.
- Source and packaged `--check` passed; live health passed after graceful promotion. Live executable SHA-256: `C7AAFB48F25C23E91EC93FA963C992A359A1FDA7F9E54E55D3D0C535BDCA8BF8`. Pre-promotion rollback: `staging\pre-deep-history-promotion-20260917-112754`.

## 2026-09-17 - persistent rigor/tool-use instructions deployed
- Added top-level `config\runtime.json:persistent_instructions` as editable global operating principles.
- Principles explicitly require doing the work, taking adequate time, using available tools for material facts instead of guesstimating, avoiding convenient assumptions, and improving efficiency through planning/reuse rather than reduced rigor.
- Instructions are injected into planner, plan verification, normal/degraded chat, every worker work-request (therefore recursively spawned child tasks), final verification, and final-response repair.
- Final verifier now rejects avoidable shortcuts, material assumptions that available tools/evidence could reasonably verify, and skipped necessary verification.
- Source compile and source `--check` passed; packaged candidate `--check` passed. Deterministic injection test proved planner + worker + final verifier receive the instructions.
- Live smoke read `docs\CHART_AXIS_GATE.md` through the file tool and passed structured final verification on cycle 1.
- Live executable SHA-256: `C81946902D99DAA153BF8339A920E770F4938B01AC62871C701DA3C8043CB740`. Rollback/source backup: `staging\pre-persistent-instructions-20260917-183920`.

## 2026-09-18 - bounded oversize recovery / pointer handoff
- Runaway recovery-tree behavior was traced to child scopes widening beyond their assigned item ranges and bulk parent context being copied into descendants.
- Recovery children now carry immutable `scope_items`; a child may keep or shrink its parent scope but cannot add items outside it. Overlapping sibling recovery units are rejected.
- Oversize recovery now advances sequentially instead of sibling fanout. Completed child work is compacted to recovery notes and pointer locators before the next unit starts.
- `norm_runtime.task_recovery_notes`, `task_step_segments`, and `task_evidence_archive` provide durable handoff/raw fallback without forcing bulky evidence back into every prompt.
- Synthetic scope/pointer/cursor tests passed before promotion of the predecessor recovery build.

## 2026-09-18 - shell tool and verifier diagnosis
- Added bounded `run_command(command, cwd?, timeout_seconds?)` to the model tool executor. Runtime config enables PowerShell with a 180-second cap and 20,000-character stdout/stderr capture.
- Direct source test ran `D:\LOCAL_Share\Code Projects\Universal\ports.py` successfully with exit code 0 and a bindable returned port. The all-busy utility behavior had already been independently verified at 235 unique probes then `False`.
- The earlier `ports.py` task exposed a pipeline flaw rather than a script flaw: the planner required execution proof, the old worker lacked a shell, intermediate verification still accepted substitute static reasoning, and final verification correctly rejected the missing runtime evidence. Prose-only final repair cannot manufacture missing execution evidence.
- Remaining verifier hardening: structurally gate step completion on required evidence and route final missing-evidence rejection back to the exact step/tool requirement.

## 2026-09-18 - temporary thinking persistence and cleanup
- Ollama thinking and answer content are separate. Previously, a slice could spend ~16K tokens thinking, emit almost no answer, hit the limit, and lose most of the useful unfinished work because only answer content was durable.
- Added `norm_runtime.task_thinking_segments`: raw thinking is saved per task/step/slice while work is active; a compact condensed note preserves calculations, branches explored, unresolved questions, and next work for continuation.
- Oversize recovery can use the condensed note instead of reconstructing work from scratch. Raw thinking remains available only while the task is active.
- Terminal cleanup now requires a durable summary and, for non-literal work, an `effectiveness_note`; then `purge_raw_thinking()` blanks raw content and stamps `purged_at` while retaining the condensed note/metadata.
- Persistence and purge tests passed: raw scratch content was stored/read, then blanked while the compact note remained.

## 2026-09-18 - promotion/restart current live state
- Promoted combined shell + thinking-persistence + terminal-purge build to `app\norm.exe`.
- Live SHA-256: `84C065AAB84D79A792A9B420535C18CB09BF02829361F4F92870F1B589EA18B9`.
- Rollback snapshot: `staging\pre-thinking-cleanup-promotion-20260918-163510`.
- Restart verification: Norm HTTP `/health` OK, activity `/health` OK, packaged `--check` reported Ollama/Redis/prompt_queue/PostgreSQL all OK.
- The bad organic-fill SPY continuation was deliberately cancelled and six queued retry entries were removed. The older SPY merge tree was also cancelled after restart because one recovery child was interrupted and the parent immediately began retrying stale recovery state.
- Post-cleanup state: zero PostgreSQL tasks in `running`, DB1 work stream length 0, pending count 0. Durable completed SPY work remains preserved for a clean future continuation.


## 2026-09-18 - targeted PostgreSQL prune
- Created and verified a full pre-prune SQL backup at `C:\Users\KHzz\Documents\Norm-backups\postgres-prune-20260918-1720\norm_runtime_pre_prune.sql` (~13.9 MB) before deleting anything.
- Compacted redundant Sep-16-18 SPY retry/recovery trees into one validated `task_history` record preserving the 749.60 ground truth, salvage-note location, bounded-scope recovery lesson, and organic-survivor rule.
- Compacted exact-response/storage/protocol/lifecycle/planner deployment smokes into a second validated `task_history` record.
- Deleted 117 terminal task rows (including descendants), 463 task-step rows by cascade, 915 live evidence rows by cascade (~6.31M rendered chars), and 145 orphan-style archive rows.
- Removed 83 exact duplicate maintenance-note rows while keeping the latest identical phase/note/details copy.
- Post-prune counts: task_runs 45, task_steps 245, task_evidence 87, task_evidence_archive 0, task_summaries 45, task_history 2, runtime_maintenance_notes 56. Messages (153), active memory_items (127), and the single background snapshot were intentionally untouched.
- Retained authoritative completed tasks including corrected SPY 32-to-16 work, SPY axis-gate audit, corrective QQQ audit, and the synthesized 32-path artifact task.
- Ran `VACUUM (ANALYZE)` on affected tables and rechecked both live health endpoints; Norm remained healthy and idle with zero running tasks.

## 2026-09-18 - planner efficiency without weakening verification
- The `simpleTables` README trial was intentionally easy but the planner expanded it into six execution steps. That was more decomposition than the task needed.
- Preserve the good behavior: requirements were repeatedly checked against observed files, the worker did not simply guess the project purpose, and the final artifact still requires write/read-back verification.
- Future improvement: make decomposition proportional to task complexity. For a bounded README inspection, one adaptive inspect/read step, one write step, and one verification step should usually be enough.
- Do not optimize by reducing evidence. Collapse adjacent inspection work into fewer steps while retaining the same evidence coverage and explicit checks against skipped work or unsupported assumptions.
- Treat extra verification as valuable when uncertainty, destructive actions, external side effects, or conflicting evidence justify it; avoid mechanically triple-checking every low-risk substep.
## 2026-09-19 - teach Norm through its own learning path first
- When the user says to "teach Norm" or "have Norm learn" a lesson, submit that lesson to Norm naturally first and observe what Norm itself persists or changes.
- Do not automatically convert learned behavior into `runtime.json` instructions, README guidance, CURRENT_STATUS notes, or other static sticky-note rules.
- Norm's own durable memory/retrieval path should be the default home for reusable learned corrections when it can handle them appropriately.
- Only modify runtime code, configuration, or global instructions if the natural learning path demonstrably fails or the behavior truly requires a system-level invariant.
- This keeps runtime instructions reserved for genuine global constraints and avoids bypassing the architecture by hard-coding every correction externally.


## 2026-09-19 19:42 EDT (-04:00) — busy-state endpoint deployed
- Added activity API `GET /status/busy`; valid chat requests are counted before classification/planning so empty Redis cannot hide pre-queue work.
- Status combines in-flight chat, Ollama active-call count, worker/queue state, PostgreSQL running-task context, and 250 ms CPU/GPU sampling with a third-vote tiebreaker.
- `open_task_idle` distinguishes a durable running task record from actual active work. Timezone-aware output defaults to `America/New_York`; internal aware UTC remains valid.
- Live executable SHA-256: `A8036D0E169D175D1A5D32C81577142A33BCC631097BC58D6EE346DC292F0A30`.

## 2026-09-19 20:19 EDT (-04:00) -- live status-context handoff deployed
- Added activity API `GET /status-context` for a fresh cross-resource Norm handoff that can be called directly from a shell or a new chat.
- Each request first builds a deterministic raw Markdown snapshot from `README.md`, `CURRENT_STATUS.md`, `DEVELOPMENT_NOTES.md`, `FUTURE_IMPLEMENTATION_NOTES.md`, recent source/config/docs files, runtime logs, Redis queue state, PostgreSQL durable task/memory state, live busy state, and deployed-EXE/source-drift evidence.
- The raw snapshot is always persisted under `docs\recovery-notes\system-context`; it does not depend on model output or a token budget.
- The same Ollama model used by Norm is then called directly, without creating a normal Norm task/Redis work tree/PostgreSQL task row, to produce a concise recency-weighted Markdown handoff. If that generation fails, `/status-context` serves the complete raw snapshot instead.
- Stable architecture/history come primarily from maintained docs; live probes/filesystem/Redis/PostgreSQL/logs are authoritative for current operational facts. Conflicts are flagged as documentation drift instead of silently trusting stale docs.
- Both raw and concise generated files receive a deterministic footer naming the Markdown/docs, Redis queue, and PostgreSQL durable memory/task sources and warning that AI-generated summaries may contain mistakes.
- End-to-end live test showed `/status/busy` as `ollama_generation` while the summarizer ran, with Redis work/pending/retry/escalation all zero and no PostgreSQL running task. The endpoint returned full Markdown contents and the expected footer.
- Promoted live executable SHA-256: `5D52EDF2519B1951A4109DCD3D8100BEFF830AB08B018EEF91C484F500C33D47`.

## 2026-09-20 11:04 EDT (-04:00) — lightweight GUI/operator shell
- Added root `norm_gui.bat` plus `tools\norm_gui_prompt.py`, `norm_gui_stream.py`, `norm_gui_reply.py`, and shared helpers. The launcher exposes separate prompt, runtime/event, and completed-reply consoles without modifying the deployed Norm runtime.
- Attach behavior is duplicate-safe: attach immediately to healthy Norm; if `norm.exe` exists but APIs are still starting, wait up to 60 seconds and attach; if Norm is absent, start the live executable and wait for health.
- GUI conversation traffic uses project `default`; PostgreSQL is canonical chat history and participates in the existing thread-summary/consolidation path. The GUI reloads the latest submission, answer, and thread ID from PostgreSQL after restart.
- Completed replies are atomically staged as UTF-8 and displayed whole, avoiding Windows CP1252 failures such as Unicode `→`; the transient handoff file is deleted after display. A UTF-8 JSONL fallback is written only when normal PostgreSQL message IDs are missing.
- Local commands include `help`/`/help`, `/status`, `/new`, `/multi` with `::send`/`::cancel`, `/repeat-submission`, `/repeat-answer`, `/exit`, and `/shutdown`. Repeat commands requeue the prior content verbatim rather than merely redisplaying it.
- Ctrl+C in the prompt console requests the existing graceful `/control/shutdown-norm` path. `help` matching is explicitly case/whitespace insensitive through `prompt.strip().lower()`.
- Validation: Python helpers compile; PostgreSQL history recovery returned a prior submission/answer/thread; UTF-8 round-trip succeeded with `CPI → hike → SPY ✓`; literal `HELP` was intercepted locally; live chat/activity health both returned OK. Deployed EXE remained unchanged at SHA-256 `5D52EDF2519B1951A4109DCD3D8100BEFF830AB08B018EEF91C484F500C33D47`.
