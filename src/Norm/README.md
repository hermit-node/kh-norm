# Norm

Norm is the user's local general-purpose assistant and developing coordination layer on **KHzz**. KHzz is the source of truth for the deployed runtime.

> `CURRENT_STATUS.md` is the authoritative operational handoff. `DEVELOPMENT_NOTES.md` preserves implementation history. `SOS.readme` is a transient unprocessed safety-stop record. Delete it only after the originating task is durably resolved in PostgreSQL: either the task ultimately completes successfully, or it is deliberately finalized as failed with a useful terminal summary. Preserve any reusable lesson in durable docs/logs before removing the live SOS. The deployed worker now enforces this during terminal cleanup for a matching SOS/task ID; cancelled or unresolved tasks do not clear it.

## Current live runtime

- Root: `C:\Users\KHzz\Documents\Norm`
- Live source: `C:\Users\KHzz\Documents\Norm\app`
- Executable: `C:\Users\KHzz\Documents\Norm\app\norm.exe`
- Verified deployed SHA-256 (rechecked 2026-09-20): `5D52EDF2519B1951A4109DCD3D8100BEFF830AB08B018EEF91C484F500C33D47`
- Model alias: `norm`; base model: `qwen3.8:27b-q4_K_M`; context: `84000`
- GPU: NVIDIA GeForce RTX 3090, 24576 MiB
- Human-editable service ports: `config\settings.ini`
- Current ports: Ollama `11434`, Norm HTTP `12543`, activity/control `8766`
- Redis/Memurai: local `6379`; PostgreSQL: local `25434`, schema `norm_runtime`
- Runtime log: `logs\norm-runtime.log`
- When Norm launches Ollama itself, Ollama stdout/stderr are redirected to `logs\server.stdout.log` and `logs\server.stderr.log`. `%LOCALAPPDATA%\Ollama\server.log` may therefore be stale and should not be treated as the live Ollama log for the Norm-managed server.
- Rollback executable snapshot: `staging\pre-thinking-cleanup-promotion-20260918-163510`

After the 2026-09-18 promotion/restart, Norm HTTP and activity `/health` returned `{"status":"ok"}`, packaged `--check` reported Ollama/Redis/prompt-queue/PostgreSQL all OK, and the runtime was intentionally left idle with zero running tasks and zero queued/pending prompts.

## Control plane and verification

Norm uses **JSON control-plane protocol v2** internally while ordinary user-facing answers remain normal English/Markdown.

Normal path: request classification -> verified plan -> typed Redis work nodes -> step execution/evidence -> durable `step_result` checkpoints -> `final_candidate` JSON -> `final-verify` -> terminal PostgreSQL `user_reply` -> task-scoped Redis cleanup.

Per-step/final verifier control messages are schema-constrained JSON. Acceptance comes from parsed/validated fields such as `verdict`, completeness/evidence booleans, `issues`, and `repair_scope`; magic prose markers are not accepted. The final verifier consuming JSON and the user receiving ordinary prose are complementary parts of the same protocol, not an unresolved mismatch.

### GUI/operator console

- `norm_gui.bat` opens three windows: prompt entry, live runtime/event stream, and completed replies. It attaches to an already-running healthy Norm; if `norm.exe` exists but APIs are still starting, it waits up to 60 seconds and attaches rather than spawning a duplicate; if Norm is fully stopped, it may start `app\norm.exe` and wait for health.
- GUI chat uses project `default`, so normal submissions and answers participate in the same PostgreSQL conversation/thread history and regular consolidation path. PostgreSQL is canonical history.
- Final replies are atomically written to a temporary UTF-8 handoff file, displayed all at once by the reply window, then removed. A local UTF-8 JSONL record is written only when PostgreSQL message persistence is unavailable/incomplete.
- `help` and `/help` are local operator commands (`prompt.strip().lower()` matching) and list all GUI commands. `/repeat-submission` requeues the last user submission verbatim; `/repeat-answer` requeues the last completed Norm answer verbatim. Both recover the latest turn from PostgreSQL after reopening the GUI, with local fallback only if needed.
- Ctrl+C in the prompt window requests Norm's existing graceful shutdown endpoint instead of force-killing the runtime.

### Console controls

- `/stop ollama` cancels the currently active Ollama generation through Norm's `/control/cancel-ollama` endpoint. It does **not** pause Norm, mute output, unload the model, or stop the Ollama server.
- `/shutdown ollama` cancels active generation, unloads the configured model from VRAM, sends a graceful Ctrl+Break/Ctrl+C-equivalent signal to the Norm-owned `ollama serve` process, waits for exit, and verifies that no Ollama/llama-server processes remain.
- `/stop norm` pauses new console submissions after the current one; `/start norm` resumes them.
- `/shutdown norm` performs Norm's graceful checkpoint/drain/shutdown path; `/shutdown norm now` cancels active Ollama work and exits promptly.
- `/stop all` stops accepting new work, lets the current step finish, writes the preserved Redis model buffer to `SOS.md`, unloads `norm`, gracefully stops `ollama serve`, and exits Norm without clearing unfinished Redis work.
- `/stop all -now` cancels the active generation immediately, flushes partial thinking/answer/tool-call output to Redis, checkpoints the interrupted step as RUNNING/yielded rather than cancelled, writes `SOS.md`, unloads the model, gracefully stops Ollama, and exits Norm. Restart resumes from PostgreSQL/Redis state.
- `GET /status/busy` on the activity API reports logical workload and sampled resource activity separately. A chat request is counted before command classification/planning, so pre-queue planning cannot look idle merely because Redis and PostgreSQL task rows are still empty. Two CPU/GPU samples are taken 250 ms apart; a third is used when the first two activity votes disagree.
- `GET /status-context` builds a fresh system handoff from maintained Markdown docs plus recent files/logs and live Redis/PostgreSQL/runtime state. It always persists a complete raw Markdown evidence snapshot, then uses the same Ollama model as Norm directly (without creating a normal Norm task/Redis work tree/PostgreSQL task row) to produce a concise recency-weighted Markdown handoff. The endpoint serves the actual generated file contents; if model summarization fails, it serves the raw snapshot instead. Saved raw/generated handoffs live under `docs\recovery-notes\system-context` and carry a deterministic source/AI-warning footer.


## Durable state and queues

- PostgreSQL is durable history for conversations, plans, steps, tool evidence, oversized answer segments, compact recovery notes, terminal results, maintenance notes, and memory.
- `task_thinking_segments` temporarily stores raw model thinking plus a condensed continuation note so a token-limit hit does not discard all unfinished work.
- Raw thinking is working state only: after a task is terminal, its durable summary exists, and its effectiveness note is written (except literal throwaways), raw thinking is blanked and `purged_at` is stamped; condensed notes remain.
- Bulky evidence can be archived into `task_evidence_archive` while compact metadata stays in live context.
- Redis DB0 is live task state/events; DB1 is work/retry/escalation/dead-letter state; DB2 is the reversible deletion queue.
- Terminal state is written and verified in PostgreSQL before task-specific Redis state is removed.
- Startup and periodic reconciliation compare Redis to PostgreSQL; graceful shutdown records unfinished state, drains work, purges staged deletions, and clears runtime Redis.

Post-promotion audit on 2026-09-18 found zero running PostgreSQL tasks, DB1 work stream length/pending count both zero, and no queued retry work from the retired SPY branches.

A targeted PostgreSQL prune later on 2026-09-18 compacted redundant SPY recovery/retry trees and throwaway deployment smokes into two validated `task_history` records, then removed 117 task rows and 915 live evidence rows. Curated memory/messages and the authoritative completed SPY/QQQ tasks were retained.

## Persistent operating principles

The top-level `persistent_instructions` list in `config\runtime.json` is injected into planning, normal/degraded chat, every worker step, recursively spawned child tasks, final verification, and final-response repair.

Standing principles:
- Do the work rather than taking shortcuts merely to finish faster.
- Take the time needed to get the task right; correctness, completeness, and verification outrank speed.
- Use available tools proactively when they can inspect, measure, retrieve, test, or verify a material fact. Do not guesstimate what can reasonably be checked.
- Do not replace missing evidence with convenient assumptions. Gather more evidence when practical, and clearly mark uncertainty when it cannot be resolved.
- Improve efficiency through better planning, decomposition, reuse, caching, and targeted tool use?not by skipping necessary work or lowering verification rigor.
- Before finishing, verify important claims and results against observed evidence and the user's actual constraints.
- Trim/consolidation should produce a strong durable summary and delete true redundancy; repeated instances of the same state retain the initial authoritative occurrence and later recurrences become compact timestamped 'happened again' notes.
- All timestamps are timezone-aware. Default human-facing timestamps and memory-summary timestamps to `America/New_York` (EST/EDT as applicable); internal UTC storage is acceptable when timezone-aware.

Efficiency means reducing wasted work, not reducing rigor.

## Default remote execution rule

For **everything beyond the simplest one-liner**, use `C:\Users\KHzz\Documents\Norm\verbatim_lines.py` first instead of constructing nested PowerShell/Python/SQL/JSON quoting.

Preferred pattern: use `verbatim_lines.py` to create a temporary Python script containing the exact operation, run that script, verify the result, then delete the temporary script. Quote-heavy PowerShell is the exception, not the default.

## File, image, and chart-analysis tools

Norm's model tool layer has bounded file tools, hash-checked mutation/read-back verification, reversible deletion, targeted connection checks, deterministic image preprocessing/local vision, and an enabled bounded PowerShell `run_command` tool. Shell execution is intended for running code, tests, and inspection; native file tools remain preferred for edits/deletes. Command results include observed `exit_code`, `stdout`, `stderr`, timeout state, working directory, and duration for verifier evidence.

Live image analysis is `tools\image_analyzer_v3.py`; v3 wraps v2 and adds focused multi-view rescans. `tools\image_analyzer.py`, v2, and `prompt_worker_legacy.py` are retained baseline/legacy code, not the deployed selection where runtime config points elsewhere.

`docs\CHART_VISION_CHEATSHEET.md` is the generic object/layer guide. `docs\CHART_AXIS_GATE.md` is the generic y-to-price calibration contract. `docs\QQQ_AXIS_GATE.md` is a regression-specific example, not a reusable source of anchors.

For the Sep-16 SPY selected candle, explicit TradingView OHLC is authoritative: O 751.33, H 752.12, **L 749.60**, C 751.24 at 15:15. Application OHLC tied to the selected candle/time outranks inferred wick geometry. The axis gate still requires at least two exact price/y anchors plus slope/intercept/residual checks before geometry is converted to price.

## Connections and degraded operation

Connection checks are demand-driven: query only the resource required by the current task or explicitly requested for maintenance. CA8D is configured as the preferred relative-file root with KHzz `docs` fallback; do not present an old connectivity observation as current without rechecking it. Dropbox is not a native Norm runtime connector; phone screenshots are currently transferred separately/manual-pull as needed.

PostgreSQL has an impaired-context fallback for answering from the current request and explicitly available tools when durable context is unavailable. That mode must be reported as impaired and does not provide normal persistence guarantees.

## Current maintenance gaps / future architecture

- Oversized-task recovery is now bounded by immutable scope: child scopes may stay equal or shrink but never widen; recovery units run sequentially; compact PostgreSQL/HDD notes are passed by pointer instead of bulk parent cargo; bulky evidence is compacted between units.
- A remaining generic prompt-budget issue exists in normal step execution: one path still requests `completed_step_context(..., max_chars=None)`. Add a routine context budget/compaction policy outside oversize recovery.
- Final-verifier architecture still needs structural hardening for missing-evidence failures. A verifier rejection caused by absent runtime proof should reopen the exact offending step/tool requirement, not repeatedly rewrite only the final prose. Intermediate completion should also be structurally gated on the step's actual verification evidence.
- The live bounded shell tool is deployed. A clean end-to-end Norm task should still be run to prove `run_command` evidence flows through the worker, step verification, and final verification under this exact promoted executable.
- Deep history maintenance remains destructive-by-design for validated terminal history older than 30 days, with SQL backup and replay validation before deletion.
- The generic chart-axis gate is an analysis contract, not yet a machine-enforced analyzer transform.
- A dedicated second-machine coordinator remains future work: normalize/classify input, retrieve bounded relevant PostgreSQL/local-source context, submit work, monitor Redis, independently verify transitions, deliver the final response, confirm transient cleanup, and persist the durable summary.
- Persistent local-file source indexing remains future work.

Develop against KHzz, stage substantial runtime changes, back up before promotion, test source and packaged behavior, and trust observable code/database/queue state over stale prose.

## Documentation roles

- `README.md` - concise operator overview.
- `CURRENT_STATUS.md` - authoritative current deployed-state handoff.
- `DEVELOPMENT_NOTES.md` - chronological history/lessons; old sections can describe behavior later superseded.
- `FUTURE_IMPLEMENTATION_NOTES.md` - deferred or only partially implemented architecture.
- `SOS.readme` - transient pathological safety-stop record. Remove it only after the originating task has a verified terminal PostgreSQL record: completed successfully, or finalized failed with a useful summary. Preserve reusable lessons elsewhere first.
- `context\current.md` - deprecated placeholder; PostgreSQL is the live persistence system.
