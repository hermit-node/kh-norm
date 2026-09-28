# Norm development notes

`RELEASE_NOTES.md` is the concise promoted-version change ledger. This file remains the detailed chronological engineering record: implementation work, incidents, experiments, validation, corrections, and lessons that explain how each release was reached.

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

## Coordinator migration design note
- Early development deliberately kept queue/recovery primitives separable so a later coordinator could own scheduling without redesigning the worker.
- The detailed unimplemented coordinator design is no longer duplicated here; the canonical backlog is `FUTURE_IMPLEMENTATION_NOTES.md`.


## Verification architecture history
- Deep reasoning verification and the strict machine-readable acceptance gate were intentionally separated; the deployed strict gate remains small and schema-constrained.
- Schema-constrained `think=True` was observed to return blank structured output on the current Qwen/Ollama stack, so the strict verdict envelope uses `think=False`.
- Remaining verifier/coordinator hardening is tracked only in `FUTURE_IMPLEMENTATION_NOTES.md`.


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
- The deferred coordinator retrieval design motivated this policy; its current canonical specification is in `FUTURE_IMPLEMENTATION_NOTES.md`.
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
- `docs\QQQ_AXIS_GATE.md` is now a regression-specific example rather than the general rule. The generic gate is not hard-wired into analyzer code; the deferred enforcement design is tracked in `FUTURE_IMPLEMENTATION_NOTES.md`.
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
- Memory housekeeping gap documented accurately: background consolidation does not by itself hard-delete every redundant curated `memory_item`; the remaining automation design is tracked in `FUTURE_IMPLEMENTATION_NOTES.md`.

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
- The missing-evidence failure mode remains an architectural limitation; the implementation plan is tracked in `FUTURE_IMPLEMENTATION_NOTES.md`.

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
- The over-decomposition lesson is retained here; the active planner-efficiency design is tracked in `FUTURE_IMPLEMENTATION_NOTES.md`.
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

## 2026-09-20 - documentation roles consolidated
- `README.md` is now operator overview only; `CURRENT_STATUS.md` is current facts/limitations; `DEVELOPMENT_NOTES.md` retains chronology/lessons; `FUTURE_IMPLEMENTATION_NOTES.md` is the single active backlog/design notebook.
- Future-design detail previously duplicated across README/current-status/development notes was collapsed into the canonical future-notes file; historical entries now retain only the lesson/rationale plus a pointer.
- Canonical future notes moved from `docs\` to the Norm root. The old `docs\FUTURE_IMPLEMENTATION_NOTES.md` path is temporarily an NTFS hard link to the same file so the currently running packaged executable's hard-coded status-context reader is not broken mid-task.
- `config\settings.ini` now has `[documentation]` pointers for `CURRENT_STATUS.md`, `DEVELOPMENT_NOTES.md`, and `FUTURE_IMPLEMENTATION_NOTES.md`. Source `settings.py`/`context_snapshot.py` was updated and `py_compile`-verified to consume those pointers on the next promoted build.
- Do not remove the compatibility hard link until a build containing the documentation-pointer source patch is promoted and `/status-context` is verified against the configured paths.

## 2026-09-20 - Norm 0.51.0 promotion and scheduler/recovery repair
- `config\settings.ini` is the canonical source for project metadata: Norm `0.51.0`, author/company `KernelHermit`, repository URL `https://github.com/hermit-node`; it also holds maintained-document pointers.
- Added `tools\build_norm.py` as the repeatable PyInstaller build path. It generates Windows version resources from `settings.ini`; Windows metadata and `norm.exe --version` report 0.51.0 / KernelHermit / the repository URL.
- Promoted executable SHA-256: `F0900495F289DABDF0A38AAA7C4014F6E60A37B19B66955CF4BD930571555DD5`; packaged dependency health passed before promotion.
- Append/follow-up requests now persist only a `deferred-plan` control node while the predecessor is unfinished. The real plan is generated only after the full predecessor task completes and passes structured final verification.
- Cancelled/failed oversized-recovery children are replaced within a bounded child retry budget without repeatedly consuming the parent retry budget.
- Worker shell instructions explicitly advertise `C:\Users\KHzz\Documents\Norm\verbatim_lines.py` for multiline/quote-heavy commands without widening native file-tool roots.
- The pointer-aware build reads maintained-document paths from `settings.ini`; after promotion, the redundant `docs\FUTURE_IMPLEMENTATION_NOTES.md` hard link was removed while preserving the root canonical file.

## 2026-09-20 - failed documentation self-reconcile stopped and purged
- A documentation-maintenance task completed inspection but then wrote an incorrect README statement conflating deferred append planning with oversized-step recovery. Its verification also expected missing `docs\_readme_new.md`, causing retry/recovery.
- Norm was stopped before the misunderstanding propagated through the remaining documentation. DB1 was explicitly cleared while Norm was offline.
- PostgreSQL audit found one cancelled task, five task-step rows, 41 evidence rows, 21 thinking-segment rows, one task summary, and one dedicated maintenance-project message. No memory items existed for that project and no marker hits were found in validated task history/background memory/maintenance notes.
- Before deletion, a full `norm_runtime` SQL dump (~6.25 MB) plus targeted JSON export was written under `Norm-backups\failed-docs-cleanup-20260920-191832`. The cancelled task/cascaded working evidence and dedicated maintenance project/orphan message were then removed.
- Lesson: documentation repair should fail closed on missing temporary verification artifacts and must keep append scheduling distinct from oversized recovery. The UUID/dependency migration remains the next planned architecture cleanup.

## 2026-09-20 - opaque UUID task/node identities and dependency graph deployed
- Migration was performed only after Norm was stopped, DB1 work was cleared, the failed documentation task had been backed up/purged, and a fresh migration-specific source + full `norm_runtime` SQL backup was created at `Norm-backups\pre-uuid-gui-20260920-192743` (~6.0 MB SQL dump).
- Added immutable `task_runs.task_uuid`, `task_nodes.node_id`, and `task_dependency_edges.edge_id`. Readable task/step IDs remain compatibility/display aliases; ordinals are stored separately from node identity.
- Plans now persist task UUID, node UUID, ordinal, legacy dependencies, and `depends_on_node_ids`. Redis work `message_id`, chain identity, previous-node, and next-node references are UUID based; worker/coordinator metadata carries both opaque and legacy identities.
- PostgreSQL step/evidence/segment/thinking/recovery/archive rows are backfilled with `node_uuid`. Existing plan rows were rewritten with UUID identity metadata. Legacy direct `task_runs` inserts receive database-side `gen_random_uuid()` so transition-era tools/tests keep working.
- `task_dependency_edges` represents sequence, verification, append, child, and recovery relations. Stable plans preserve existing edge UUIDs across `ensure_schema()`; obsolete internal edges are removed only when the dependency itself changes.
- Append verification is runtime-enriched with `previous_task_uuid` and `previous_node_id`; execution gating uses opaque identity when present and falls back to legacy lookup only for compatibility. Append edges target task identity so replacing a `deferred-plan` placeholder cannot retarget the dependency.
- Live migration audit: 71 task rows / 71 distinct non-null task UUIDs; 424 task nodes; 364 dependency edges; zero null node UUIDs in `task_steps`, `task_evidence`, `task_step_segments`, `task_thinking_segments`, `task_recovery_notes`, and `task_evidence_archive`; zero dangling task-edge references; zero plan/registry mismatches. A second migration pass preserved all 364 edge UUIDs exactly.
- Isolated PostgreSQL test proved recovery-child edges, task-level append edges surviving deferred-plan replacement, evidence node linkage, task lookup by UUID, and stable internal edge IDs; temporary schema was dropped afterward.
- Existing deferred-planning, append-gate, and cancelled-child recovery regressions all passed after compatibility hardening.

## 2026-09-20 - GUI companion-window lifecycle fix deployed
- `norm_gui.bat` now uses `cmd.exe /c` for prompt, stream, and reply helpers; `/k` no longer leaves a shell open after Python exits.
- Stream/reply helpers allow startup grace, detect the running `norm.exe`, and exit normally when the runtime disappears. Reply process checks are throttled rather than spawning `tasklist` continuously.
- Python `signal`/outer-guard handling alone was insufficient on Windows while the stream blocked inside WinSock: `CTRL_BREAK_EVENT` either terminated with `0xC000013A` or remained queued until the read returned. The final implementation installs native `SetConsoleCtrlHandler` callbacks in the disposable stream/reply viewers and calls `ExitProcess(0)` for Ctrl+C/Ctrl+Break.
- Live graceful-shutdown regression: stream and reply helpers were started while Norm was down, attached after the promoted executable came online, then Norm received the same graceful shutdown endpoint used by the prompt console. Norm exited normally; stream and reply both exited code 0. A separate real Windows `CTRL_BREAK_EVENT` regression then verified both helpers exit code 0 with no traceback while the stream is connected/blocking.
- Promoted 0.51.0 executable SHA-256: `A72D7FC8E0268CB42A1F062BB63EFCAA4904B159330972D878779738A2DDAEC6`. Previous live executable backup: `staging\norm-live-before-0.51.0-20260920-194100.exe` (`F0900495...555DD5`).

## 2026-09-20 - Norm 0.51.1 runtime/workspace split and backup tooling
- Runtime moved to `C:\Norm`; the model-editable workspace remains `C:\Users\KHzz\Documents\Norm`. `config\settings.ini` now defines `runtime_root`, `workspace_root`, and `verbatim_writer`, and startup validates that runtime/workspace do not overlap.
- Both normal conversation tools and worker/recovery-child tools inject the configured workspace root as an allowed file root. Effective roots are the Norm workspace, `\\KH-CA8D\Local1675`, and `D:\LOCAL_Share\Code Projects`; `C:\Norm` itself is not model-writable.
- `verbatim_lines.py`, runtime logs/state/tools/config/source, build tooling, and the deployed executable now live under `C:\Norm`. Maintained docs/images/context/statements remain under the writable workspace.
- Added local GUI command `/backup-zip`. It uses settings to create a timestamped ZIP containing a custom-format PostgreSQL `norm_runtime` dump, the workspace tree, and the runtime tree while excluding disposable build/staging/cache directories. The archive contains `backup-manifest.json` plus `.bat`/PowerShell restore helpers; restore refuses while Norm is running and requires explicit `RESTORE` confirmation.
- Backup validation succeeded by creating a temporary PostgreSQL dump, validating it with `pg_restore --list`, and walking 29,552 runtime files / 4,871,669,539 bytes plus 1,478 workspace files / 881,549,022 bytes.
- Obsolete PyInstaller build trees, promotion/release copies, headless-Chrome staging profiles, and superseded candidate EXEs were pruned after the external pre-migration rollback snapshot was created. `app` is now ~37 MB and staging was reduced from ~3.99 GB to ~201 MB.
- Promoted Norm `0.51.1` executable SHA-256: `FB5E6E2BB7668D1C2CBD1CAEC60F356D193A50B70B208DD0E2B50C56CEF4BBF4`. Packaged `--check` passed for Ollama, Redis, prompt queue, and PostgreSQL; live chat/activity health returned OK and `/status/busy` was idle with zero work/pending/retry/escalation/running tasks.

## 2026-09-20 - backup venv made reproducible
- `/backup-zip` no longer archives `C:\Norm\.venv`; the CUDA PyTorch environment was ~4.4 GB and is reproducible. Runtime backup validation dropped from ~4.87 GB to ~235.7 MB before ZIP compression, while the workspace remains separately archived.
- Added `[environment]` settings plus `tools\requirements-lock.txt` and `tools\ENVIRONMENT_REBUILD.md`. Restore tooling finds Python 3.14, installs it through winget or python.org if absent, recreates the venv when missing/mismatched, installs `torch==2.14.0+cu126` from the configured CUDA wheel index, installs pinned dependencies, and validates vision/runtime imports.
## 2026-09-20 - Norm 0.51.2 weekly image cleanup with crash-visible Redis state (SUPERSEDED by 0.51.2b)
- Added an independent seven-day weekly cleanup pass to the idle worker loop; it is not dependent on background-memory consolidation being due.
- Cleanup uses the configured `tools.image_output_root` and validates that the purge target is inside the configured writable workspace before deleting anything. Only derived image-analysis output is purged; source/original images remain.
- Added persistent Redis DB0 markers: `norm:maintenance:weekly_cleanup:active` is written with `status=cleanup_started` before deletion and removed only after `norm:maintenance:weekly_cleanup:last` is written. Caught failures retain `active` with `status=failed`; stale active state blocks a new pass.
- `/status-context` now surfaces incomplete/active weekly cleanup state and the most recent completed purge summary.
- Regression tests verified success-marker lifecycle, simulated purge failure retention, stale-marker blocking, outside-workspace path refusal, and status-context Redis visibility.
- First live 0.51.2 cleanup removed 1,359 files / 873,675,067 bytes from `workspace\images\analysis`, leaving the directory empty and a completed Redis report.
- Promoted executable SHA-256: `332f38e05a31fd4740ac23c0ad91cf60895b574889490f3b76b5be9e7f009b8b`. Packaged `--version`/`--check`, GUI health probe, and live `/status/busy` all passed; post-restart state was idle with zero queue/pending/retry/escalation/running tasks.


## 2026-09-21 - 0.51.2b unified maintenance correction
- The initial 0.51.2 image-purge scheduler was superseded rather than promoted as a new feature version. Current identity is `0.51.2b`; future patch repairs to the same feature should use patch-revision labels instead of consuming a new minor version.
- Collapsed regular memory cleanup, deep-history cleanup selection, and `images\analysis` purge behind one idle scheduler. Eligibility is checked hourly; actual regular cadence is 7 days. If the last successful deep clean is 21+ days old (or absent), that due run takes the deep path instead of the regular path.
- Added PostgreSQL `norm_runtime.runtime_state` as durable authority for `deployed_version`, `last_successful_cleanup_at`, and `last_successful_deep_cleanup_at`. The last regular-clean timestamp was seeded from the existing background snapshot at `2026-09-20T15:30:49.889203-04:00`; no prior successful deep-history note existed, so the deep timestamp remains unset until the first successful deep pass.
- Redis now holds only `norm:maintenance:weekly_cleanup:active` for crash/restart recovery. The active marker records mode/phase/run ID/resume count, survives graceful DB0 cleanup, and is removed only after PostgreSQL success state is committed. A live shutdown test verified the marker survives DB0 flush exactly.
- Interrupted maintenance resumes the same recorded mode on the next idle check. Image-output purge is idempotent and runs for both regular and deep maintenance.
- `tools\build_norm.py` now accepts letter revision labels such as `0.51.2b`; Windows fixed numeric metadata maps `b` to revision 2 while visible FileVersion/ProductVersion remain `0.51.2b`.
- Promoted executable SHA-256: `f3710205b3bbcf78e68d3c72eb9d0f7c742677cec19cba77c78d46135136db37`. Packaged `--check` passed Ollama, Redis, prompt queue, and PostgreSQL; live HTTP/activity health passed after restart.

## 2026-09-21 - Norm 0.51.3 nonblocking durable GUI ingress
- Promoted the tested 0.51.2e console/queue work as feature release `0.51.3`. Live executable SHA-256: `2409ad004cd2c2d17937e35d37e7bd415728e7d94a3d04f714692465fe5239a2`.
- The normal `norm_gui.bat` prompt helper no longer blocks on `/api/chat`. Every ordinary prompt is timestamped and written immediately to Redis DB3 `norm:gui:ingress`; the prompt window immediately returns to `You>` while a background dispatcher submits one oldest prompt at a time through HTTP/chat port `12543`.
- Activity/events, busy status, and control commands remain on port `8766`; Ollama remains on loopback `11434`. `/stop` is an immediate alias for `/stop all -now`, so Python/control can interrupt active work even while the dispatcher is blocked waiting for the chat response.
- Redis DB3 is intentionally excluded from normal runtime DB0/DB1 cleanup. GUI thread/submission/answer state and waiting ingress therefore survive graceful Norm shutdown/restart. Dispatching entries are tracked separately; abandoned in-flight entries are quarantined as uncertain instead of automatically replayed and potentially duplicating a task.
- The Rich console ingress uses a separate DB3 stream/group namespace from the normal GUI, preventing the two frontends from stealing each other's entries.
- Synthetic regression passed Redis ordering/ACK/thread-state and uncertain no-replay behavior. Live GUI regression claimed a DB3 prompt, created `chat-96351e6f-e0c2-4556-a232-c10a35da0cfa`, returned exactly `gui redis queue smoke test`, ACKed/deleted the stream entry, cleared dispatch state, and left zero uncertain entries. A live stop-now test also proved the 8766 control path remains responsive while a 12543 chat request is active.
- Two stale CA8D audit tasks were explicitly cancelled and removed from executable Redis work state before release; their PostgreSQL history remains preserved and startup recovery reports no recoverable stale tasks.

## 2026-09-22 - Norm 0.51.4 centralized network authority and Tailscale fail-closed path
- Replaced `[ports]` with `[network]` in `config\settings.ini`; topology now includes KHzz/domain identity, host selectors, service ports, and `require_tailscale`.
- `runtime_bootstrap.load_config()` resolves Redis/all queue endpoints, PostgreSQL runtime, separate `stocks_api`, Norm HTTP/activity binds, and Ollama from the central network settings. Environment-specific DB names/user/password were moved to the external secrets file configured by `[environment].secrets_file`.
- Runtime PostgreSQL and `stocks_api` now use `khzz.boga-dace.ts.net:25434`; Redis authority uses `khzz.boga-dace.ts.net:6379`; Norm HTTP/activity bind to KHzz's Tailscale address; Ollama remains intentionally loopback-only.
- Memurai itself remains `127.0.0.1:6379`; Tailscale Serve provides the tailnet TCP endpoint and forwards it to local Redis. Direct tailnet Redis PING was verified.
- `FileToolExecutor` now performs a Redis authority PING before each native tool call and refuses tool execution when the configured authority endpoint is unavailable. This is a capability-boundary check, not yet continuous mid-command cancellation.
- PyInstaller 0.51.4 was built, package-checked, promoted, and verified at `C:\Norm\app\norm.exe`; live SHA-256 is `0EE7EA56B866F0DC6CC3B54C04A4549A97F76DC4C9CCD968CD13D6D7137CC671`.
- A post-promotion startup incident exposed stale PostgreSQL sessions `idle in transaction` holding a relation lock. Fresh Norm starts blocked on `ALTER TABLE norm_runtime.task_runs ADD COLUMN IF NOT EXISTS task_uuid uuid`; activity/control `8766` had already started while chat `12543` had not. Terminating only the stale blockers allowed schema reconciliation and both APIs to become healthy.
- During the resulting maintenance prune, transient Redis DB0/DB1/DB2 execution state was cleared while DB3 was re-established as durable GUI state. The stale resurrected task was intentionally terminalized, its raw thinking was purged, bulky evidence was archived/compacted, terminal recovery notes were removed, and explicitly superseded memory rows were deleted without age-based pruning of active memory/history.
- Rereading the 0.51.3 GUI design caught a migration miss: DB3 `last-submission`, `last-answer`, and `thread-id` are convenience/durability state and must survive normal cleanup. Those pointers were restored from PostgreSQL, and GUI helper source was repaired to use the 0.51.4 central resolver rather than the removed `[ports]` section/raw runtime JSON connection fields.
- Documentation policy correction: Norm is only roughly one to two weeks old; age alone is not a deletion criterion. Deep-history destructive pruning remains constrained to eligible terminal history older than the configured 30-day retention threshold.
- Added `RELEASE_NOTES.md` as the concise one-entry-per-promoted-version delta ledger; detailed chronology stays here and current state stays in `CURRENT_STATUS.md`.


## 2026-09-22 - Unreleased 0.51.5 work: suppressible tasks and Docker JIT E2E sandbox
- Source-only work after 0.51.4 added a durable `suppressed` task state, `/suppress-task`, `/flush-suppressed`, and suppressed-task matching/resume scaffolding. This work is **not promoted** and must not be described as deployed 0.51.5 behavior yet.
- Live synthetic suppression testing proved that active generation can be cancelled at a capability boundary, exact queued job payloads can be preserved in PostgreSQL, executable Redis state can be cleared, and the worker returns idle. A semantic defect was then found: when execution is inside an internal child task, no-ID `/suppress-task` can select that child instead of the user-facing root task. Root-tree suppression/resume still needs repair and regression coverage before promotion.
- Added `C:\Norm\tools\norm_e2e.py` as an **opt-in** staging/runner for isolated E2E tests. It copies current Norm source into a unique CA8D run directory and generates Linux sandbox settings/secrets/runtime config; no live Norm state is mounted.
- Added a separate CA8D Compose project under `e2e/norm`. Each run uses the already-cached `python:3.14-slim` image but creates a brand-new venv, plus fresh PostgreSQL 16, Redis, Apache, Python fixture, and scriptable Ollama-mock sidecars. Compose volumes are project-scoped and removed with `down -v` after an executed run.
- The default JIT smoke scenario drives the actual Norm pipeline through planning, native `write_file`/`read_file` calls, final verification, PostgreSQL task persistence, Redis connectivity, Apache shared-file serving, Python HTTP fixture messaging, and a disposable `stocks_api` fixture.
- JIT convention is `WORKING` (exit 0), `DEAD` (exit 1), `DNE` (exit 3). Stage-only/Compose validation is explicitly setup evidence, never proof that a behavior works.
- Python syntax, scenario JSON, generated sandbox configuration, source staging, and `docker compose config` all passed. CA8D execution has **not** yet run because the available Docker endpoint on port 2376 requires client TLS credentials and the prior CA8D SSH management key is not present on KHzz.
- Added workspace `e2e-jit/README.md` and a persistent runtime instruction so Norm may author a disposable JIT test/scenario when explicitly asked. E2E remains opt-in rather than automatic release behavior.

## 2026-09-23 - GUI UTF transport and emergency stop correction
- Re-read the maintained README/current-status/development/release/future docs before modifying the GUI. The DB3 durable ingress/reply architecture and PostgreSQL canonical history were preserved rather than replaced.
- Reply transport remains UTF-8 canonical. The reply viewer now reads raw Redis bytes, decodes UTF-8 first, falls back to CP1252 for legacy Windows text, and finally replacement decoding for malformed bytes so one bad value cannot wedge the viewer.
- Large completed replies above 250,000 characters are displayed completely in bounded plain-text chunks instead of one Rich Markdown parse. An 8,411,136-byte Unicode Redis round-trip and large-render reconstruction test passed.
- `Run-Norm.bat` now resolves ports through the 0.51.4 settings resolver and uses a noninteractive-safe health-wait loop; a stopped-runtime `--service-only` launch reached healthy chat/activity endpoints without the former input-redirection failure.
- Corrected `/stop-all now` semantics in the external GUI: `tools\norm_emergency_stop.py` first requests immediate cancellation, materializes current Redis/PostgreSQL recovery state into `C:\Norm\SOS.md`, then force-terminates `llama-server.exe`/Ollama and the `norm.exe` process tree. This path intentionally skips graceful drain/cleanup.
- End-to-end emergency-stop test produced a fresh `SOS.md` with `Stop mode: stop-all-now-emergency`, then both Norm HTTP and Ollama were offline. Norm was subsequently restarted healthy.
- Follow-up correction made the Redis recovery buffer explicit in the emergency path. Active model thinking/answer/tool-call chunks are already streamed into Redis `model-buffer` streams at a 4096-character / 0.5-second threshold with a final flush on model-call exit; SOS generation now also prints every live Redis task state and working-memory event stream before the raw model buffers.
- Emergency stop now waits for the Redis/model-buffer signature to settle after cancellation (bounded to 5 seconds), then writes `SOS.md` with `flush()` + `os.fsync()`, reopens/fsyncs it, and read-back verifies UTF-8/header/size/SHA-256 before any `taskkill`. A snapshot failure returns without force-killing Norm. Synthetic regression preserved task state, working buffer, Unicode model chunks, and the raw Redis model-buffer key; live emergency regression logged the verified SOS hash before killing Ollama/Norm.
- No new `norm.exe` was built for this correction because `C:\Norm\app` contains unrelated unreleased source drift; promoting that source would have mixed the GUI fix with unfinished runtime work.

## 2026-09-23 - runtime convenience-code cleanup and 0.52.6 backup checkpoint
- Re-audited the live runtime tree after the GUI/UTF/emergency-stop work and removed 143 one-off convenience/probe/write helpers (207,481 bytes) from `C:\Norm\tools` and top-level `C:\Norm\state`. Removed categories included `_tmp_*`, StegoSplit patch/write helpers, decompiled probe artifacts, the superseded `verbatim_append.py`, and old top-level state maintenance scripts. Core runtime tools, `verbatim_lines.py`, GUI helpers, backup/restore tools, image analyzers, `state\file-backups`, and `state\deletion-trash` were preserved.
- Repaired `tools\norm_backup.py` for the 0.51.4 centralized configuration model: runtime/workspace paths now come from `load_path_settings()`, PostgreSQL connection/schema from `runtime_bootstrap.load_config()`, and an optional `--label` is recorded in the filename/manifest. Validation succeeded against the live PostgreSQL schema and measured the cleaned runtime/workspace before ZIP creation.
- Updated backup policy to exclude `state/file-backups`, avoiding recursive backup-of-backup growth while preserving those local mutation backups in place.
- The user-requested `0.52.6` identifier is being used for this full backup checkpoint only. The deployed packaged runtime remains Norm `0.51.4`; no unfinished post-0.51.4 source drift was promoted or rebuilt as part of this maintenance pass.
- Created and verified `C:\Users\KHzz\Documents\Norm-backups\Norm-backup-0.52.6-20260923-123324-0400.zip` (SHA-256 `7b0edd732c1540d6bf9bd98b1e7e5801250aec92bd12dbdfa154fe5b3987343e`). Archive `testzip`, `.sha256` match, checkpoint-label manifest check, nested-file-backup exclusion, and `pg_restore --list` on the embedded `norm_runtime` dump all passed. The manifest intentionally records project version `0.51.4` alongside checkpoint label `0.52.6`.

## 2026-09-23 - external hot-swappable plugin broker
- Added `C:\Norm\tools\norm_plugins.py` and configured `documents_root\plugins` as the local plugin root without rebuilding the packaged executable.
- Each active plugin is a direct subfolder with literal-metadata `init.py`, a human/Norm-readable `README.md`, and Python implementation files. Discovery parses `init.py` with `ast.literal_eval` and does not import plugin code during scanning.
- Every scan records README SHA-256, aggregate plugin SHA-256, deterministic plugin/version UUIDs, and per-script SHA-256/script/content UUIDs. Matching uses capability/description/README text; execution fresh-loads the selected entry file so changed code is visible on the next helper invocation.
- Added `scan`, `list`, `match`, `describe`, `run`, and `scaffold` helper commands plus JSON-file payload support to avoid fragile shell quoting.
- Hot-swap regression used a disposable `selftest_plugin`: version 0.1.0 UUID `f31d2cb0-f59b-54b1-9683-93a8425f087e` ran successfully, then code/version were changed in place to 0.1.1 and the next invocation immediately returned the changed behavior under UUID `64c1d6d4-4e46-5952-b7ae-d486bd31f689`, with no Norm rebuild or restart.
- Added a persistent runtime instruction telling Norm to query the plugin broker through existing `run_command` for specialized capabilities. The currently running process is intentionally not interrupted; because `PromptWorker` caches `runtime.json`, that one process will learn the new instruction on its next restart. Plugin edits after that are discovered per call and need no restart.

## 2026-09-24 - offline rebuild, buffer audit, renderer correction and documentation refresh

The current executable is `C:\Norm\app\norm.exe`, rebuilt and promoted on
2026-09-24 while retaining version **0.51.5**. **0.51.6 has not been assigned.**
The service was not started during this update. Packaged `--help`/`--version`,
source compilation and offline regression checks passed; post-start HTTP/GUI
health and an end-to-end task remain unverified for this build.

Implemented: quiet-driven GUI busy checks (~1 second quiet, 30-second fallback;
no owned item means no probe), structural secret redaction at tool/evidence/log
boundaries, and one shutdown waiting message followed by an event wait. The GUI
renderer now places control messages on separate lines and resumes model text on
a clean line. GUI helpers are external Python files and need no executable rebuild.

Existing Memurai/PostgreSQL records were inspected read-only: no shutdown waiting
messages were found in the available model buffer or searched durable text. A
synthetic replay confirmed that control logs stay out of model-buffer/SOS storage
and reproduced the GUI-only interleaving defect before the renderer fix.
See `CURRENT_STATUS.md` for counts, limitations and remaining checks.

Executable SHA-256: `A3F0714DB6E281086563D8E5D9EECE95AEF449204CAFBDDC21D5CD4AA436E635`. Rollback: `C:\Norm\staging\pre-offline-promotion-20260924-011756\norm.exe`.

The pre-fix synthetic renderer replay reproduced a control line appended to a
partial model line. Real read-only Redis/PostgreSQL/SOS checks found no shutdown
message contamination in the searched data. Fixed only the external renderer;
raw recovery/storage code was left intact. Renderer tests preserve contiguous
model fragments and ignore filtered log noise without injecting blank lines.

Resolved maintained-document paths through the actual settings loader: all five
point to `C:\Users\KHzz\Documents\Norm`. Prior docs still declared 0.51.4 and
an old executable hash, despite the current executable being 0.51.5. Refreshed
current documentation; retained release/development history as historical records.
No PostgreSQL memory write, task creation or service start was performed.


## 2026-09-27 - 0.52.0 clean-source/install-layout consolidation
- Canonical portable source uses `core\` rather than historical `app\`; source ZIPs contain no `.venv`, compiled executable, caches, or private credentials.
- Maintained docs moved into runtime `docs\`; plugins moved into runtime `plugins\`; Norm-specific SSH material is `C:\Norm\.ssh`.
- External writable generated state is split into `%USERPROFILE%\Documents\Norm\workspace` (durable artifacts/work) and `temp` (disposable scratch/recovery).
- Plugin hydration is native: public plugin functions are sent as actual tool schemas, multi-file imports work, changed plugins reload before schema/dispatch use, and last-known-good remains active after a bad edit.
- UUID migration/prune recovery now repairs surviving lineage conservatively and memory recovery preserves intact/source-linked context before clustering true orphans.
- Emergency SOS/status-context recovery material now belongs under the external temp recovery tree rather than package-managed docs/source.
- Reusable installer updates package-owned files in place and reuses a compatible `.venv`; persistent `.ssh`, local plugins, logs and state are not wiped by normal base updates.
- First-party backup capability is implemented as installer-compatible source and private full-state packages, excluding the reproducible `.venv` but including environment recreation metadata.


## 2026-09-27 - 0.52.0 compatibility/plugin/operator correction
- Rechecked the 0.52.0 source against the prior 0.51.5 read-only audit rather than reverting to the older tree. Intentional task-tree suppression/resume, DB3 durable ingress, NOGROUP recovery, quiet busy probes, strict UTF transport, redaction, protocol-v2/UUID identity, PostgreSQL-first terminal cleanup, bounded recovery, temporary-thinking purge, Tailscale authority, and emergency model-buffer capture remain present.
- Closed the prior audit's remaining GUI suppression hole: `_dispatch_prompt()` now checks the durable GUI suppressed-prompt set before starting HTTP dispatch. Duplicate `_prompt_retry_suppressed` and repeated retry/defer suppression blocks were removed. `inspect_queue()` clamps Redis XRANGE COUNT to at least one.
- Fixed worker/host configuration drift: queued worker tool construction now calls the shared `runtime_bootstrap.load_config()` resolver instead of reading raw `runtime.json`. This expands `{runtime_root}` consistently.
- Image analysis is now lazy-validated. Constructing the normal tool executor does not require `image_python` or the analyzer script; only `analyze_image` validates those paths. This prevents optional image configuration from killing simple text work.
- `settings.ini` now carries `[paths].runtime_root`; portable source uses `.` and the installer writes the actual installed absolute root after sync. The exact-text writer moved from root `verbatim_lines.py` into first-party `plugins\verbatim_lines`; `[paths].verbatim_writer` points to its private stdin CLI.
- Emergency SOS writing now receives the real runtime root separately from an optional output directory, so `state\emergency-stop` is no longer interpreted as a runtime containing `config\settings.ini`.
- Bundled the uploaded StegoSplit key-pair prototype and MessageCodec as self-contained private-source plugins. Unicode message embed/extract and deterministic key create/recover round trips passed through both native hydration and the legacy plugin broker. The stale prototype key CLI was intentionally not bundled because its call signature omitted the current required map key.
- Backup tooling now supports `source` and `full` package modes. `/backup` produces source/docs/built-in-plugin installer media without private state; `/backup full` includes all plugins, `.ssh`, secrets, workspace/recovery/log/state and PostgreSQL while still omitting `.venv`; `/backup-zip` remains the legacy full-backup alias.
- `/condense-memories` was deliberately not added. The documented intended command is stronger than rebuilding the global snapshot: it must non-recursively traverse curated/current memories, reconcile real duplicates/contradictions/superseded state, preserve reusable lessons, never delete by age alone, and fail safely. Current primitives do not yet implement that full operator action.


## 2026-09-27 - 0.52.2 startup visibility and named thread navigation
- Activity/control now binds before PostgreSQL/schema migration and reports an explicit initializing phase until the runtime is fully ready; chat remains gated until durable initialization succeeds.
- Busy status is forced busy during startup initialization so the durable GUI dispatcher cannot interpret a migration wait as an idle dispatch window.
- Added PostgreSQL-backed thread listing/creation API support plus queue-ordered GUI/Rich-console controls `/new [name]`, `/thread-list`, and `/thread-resume <name|id>`. Thread switch/reset controls remain in the same Redis ingress order as prompts.

- Follow-up operator regression: `/flush-suppressed` previously deleted only PostgreSQL `task_runs(status=suppressed)` rows, leaving GUI DB3 uncertain records visible after a `ConnectionResetError`. 0.52.2 now flushes the matching suppressed delivery records/stream entries too, but retains a prompt-ID retry tombstone while an HTTP dispatch with that ID is still active.
- Installer 1.3.4 changed pip maintenance from an exact pin to `pip>=26.1` with `--upgrade`, allowing newer pip releases without changing Norm's runtime requirements lock.
- Cleaned visible help to canonical command forms while retaining historical aliases in parsers for compatibility.
- Installer 1.3.4 skips a pip reinstall when the requested pip is already present and keeps `.ssh`, `.venv`, user plugins, logs, and state protected during normal in-place updates.

## 2026-09-27 - 0.52.4 canonical console/SSH prompt ingress
- Reconciled the local Rich console and SSH prompt GUI onto one DB3 ingress stream/group/thread key and one shared core dispatcher implementation.
- Both frontends now submit to project `default`; the old `norm:rich-console:*` queue namespace is removed from active configuration.
- The dispatcher establishes an explicit current thread before chat submission, then requires a durable `task_id` in the successful response before acknowledging/deleting the ingress item.
- A 200 response without task creation is retained as a non-auto-retrying protocol violation rather than being silently treated as completed work.
## 2026-09-27 - 0.52.4 startup ownership / PostgreSQL retry / plugin serialization
- Folded every `run_host()`-owned server/worker into one `try/finally` ownership boundary. A failure after activity port 8766 is bound now shuts that server down before the outer startup retry, preventing a recoverable PostgreSQL error from becoming `WinError 10048`.
- Removed the duplicate normal-start `healthcheck()` before the retry loop. `build_runtime(..., ensure_schema=True)` is the authoritative PostgreSQL startup path, so psycopg connection/schema failures are covered by the existing bounded three-attempt retry. `--check` retains its explicit one-shot health probe.
- Generated PostgreSQL and stocks conninfo now include `connect_timeout=5`; lock and statement timeouts continue to govern established schema-migration sessions.
- Added one process-global reentrant plugin lock. This deliberately serializes hot hydration and execution across all `PluginManager` instances because `sys.modules`, `sys.path`, `redirect_stdout`, and `redirect_stderr` are process-global. Synthetic concurrent execution kept each call's stdout isolated.
- Deferred rather than mixed into this reliability patch: transactional whole-install rollback, explicit plugin export declarations, and routing `verbatim_lines` through the same allowed-root policy as native file tools.



## 2026-09-27 - 0.52.6 proportional plan verifier

The plan verifier was tightened around blocking execution correctness while reducing false-positive rejection. Cohesive bounded steps may cover multiple tightly coupled modules/functions when their responsibilities and checks are explicit. Advisory decomposition/style concerns are non-blocking. Existing inspected/reused mechanisms and execution-time invariant tests count as evidence, preventing speculative objections such as boundary-value cases already eliminated by the inherited implementation. Repair cycles are instructed to make the smallest material correction rather than mechanically split steps or repeat an unchanged rejected plan.
