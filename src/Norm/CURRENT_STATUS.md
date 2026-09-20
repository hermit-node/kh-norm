# Norm current status

Last verified: **2026-09-20 11:04 EDT (-04:00) GUI/operator documentation refresh on KHzz**

This file is the authoritative operational/development handoff for the deployed Norm runtime. Historical notes and regression-task files may intentionally describe older behavior.

## Source of truth

- Machine: `KHzz`
- Root: `C:\Users\KHzz\Documents\Norm`
- Live source: `C:\Users\KHzz\Documents\Norm\app`
- Live executable: `C:\Users\KHzz\Documents\Norm\app\norm.exe`
- Executable SHA-256 rechecked this pass: `5D52EDF2519B1951A4109DCD3D8100BEFF830AB08B018EEF91C484F500C33D47`
- Model alias/base: `norm` / `qwen3.8:27b-q4_K_M`
- Context length: `84000`
- GPU observed this pass: NVIDIA GeForce RTX 3090, 24576 MiB
- Runtime log: `C:\Users\KHzz\Documents\Norm\logs\norm-runtime.log`

Live Norm HTTP and activity `/health` both returned `{"status":"ok"}` after restart. Packaged `--check` reported Ollama, Redis, prompt queue, and PostgreSQL all `ok`.

## Configuration and services

`config\settings.ini` is the human-editable source of truth for runtime service ports:

- Ollama: `11434`
- Norm HTTP/chat: `12543`
- activity/control: `8766`

Redis/Memurai is local on `6379`. PostgreSQL is local on `25434`, database `postgres`, schema `norm_runtime`. HTTP/activity listeners are restricted to loopback/Tailscale-safe addressing by the runtime.

`config\runtime.json` carries queue, storage, tool, memory, maintenance, PostgreSQL, and coordinator settings. The live image analyzer configured there is `tools\image_analyzer_v3.py`.

The activity/control API now exposes `GET /status/busy`. Busy detection does not treat Redis as authoritative: it combines pre-queue in-flight chat/planning requests, active Ollama calls, worker state, Redis work/retry/escalation state, PostgreSQL running-task context, and two 250 ms CPU/GPU activity votes with a third tiebreaker when needed. A PostgreSQL task can therefore be reported as `open_task_idle` instead of falsely implying active work.

The activity/control API also exposes `GET /status-context`. Each request builds and persists a complete raw Markdown evidence snapshot from the maintained README/current-status/development/future notes plus recent files/logs and live Redis/PostgreSQL/runtime state. The same Ollama model used by Norm is then called directly—without creating a normal Norm task, Redis work tree, or PostgreSQL task row—to generate a concise recency-weighted handoff. `/status-context` serves the actual saved Markdown contents; if summarization fails, the raw snapshot is served instead. Raw/generated files are retained under `docs\recovery-notes\system-context` and include a deterministic source/AI-warning footer.

## GUI/operator shell

`norm_gui.bat` is the lightweight local operator shell. It opens separate prompt, runtime-stream, and reply windows while using the existing Norm APIs rather than creating a second runtime. Healthy existing Norm instances are attached immediately; an existing-but-starting `norm.exe` is given up to 60 seconds to expose its APIs before attach; no duplicate instance is spawned. If Norm is fully stopped, the launcher may start `app\norm.exe` and wait for health.

The GUI uses project `default`, so PostgreSQL remains the canonical running record of user submissions, assistant replies, thread IDs, summaries, and consolidation. On GUI startup, `/repeat-submission` and `/repeat-answer` recover the latest applicable turn from PostgreSQL and requeue it verbatim. A local UTF-8 JSONL fallback is used only when PostgreSQL message persistence is missing. Completed replies are delivered through an atomic UTF-8 staging file; the reply viewer reads the whole file only after completion, displays it once, then deletes the staging file.

`help` or `/help` (case/whitespace insensitive via `prompt.strip().lower()`) prints the full local operator command list. Ctrl+C in the prompt console calls the graceful Norm shutdown control rather than terminating `norm.exe` directly.

## Control-plane protocol v2

The internal control plane is schema-validated JSON. User-facing output remains ordinary English/Markdown.

Lifecycle:

```text
user request
-> command classification JSON
-> generated + independently checked plan
-> typed DB1 work nodes
-> bounded worker execution + immediate evidence persistence
-> durable step_result JSON
-> final_candidate JSON containing user_reply
-> final_verification JSON
-> terminal PostgreSQL user_reply
-> task-specific Redis cleanup
```

Final acceptance is `parse_json -> validate_final_verification -> validated verdict`. A valid `accept` requires complete schema/requirements, applicable artifact verification, no issues, and `repair_scope=none`. There is no magic-string acceptance path. The verifier accepting JSON internally and the caller receiving the candidate's plain-text `user_reply` is expected behavior.


## Queue durability and current Redis state

PostgreSQL is the durable ledger. DB0/DB1/DB2 are transient runtime state and deletion staging.

Current code behavior:
- Terminal completion/failure/cancellation is written and terminal-summary verified before task-specific Redis cleanup.
- Startup requeues pending work, preserves recoverable running work, finalizes unrecoverable running tasks into PostgreSQL, and clears Redis-only orphan tasks.
- Periodic Redis reconciliation is enabled with a 14,400-second interval.
- Graceful shutdown records unfinished state, drains, purges queued deletions, then clears runtime Redis.

Post-promotion state on 2026-09-18:
- PostgreSQL running tasks: `0`.
- DB1 `norm:prompt:work`: stream length `0`, pending count `0`.
- The failed organic-fill SPY continuation and the interrupted old SPY merge tree were deliberately cancelled; queued/retry entries were removed and durable completed work was preserved.
- The runtime is intentionally idle and ready for a clean continuation under the promoted build.

The empty DB1 stream/group shell is normal infrastructure, not queued work. Do not flush a live DB1 just to make `dbsize` literally zero.

## Persistent operating principles

The top-level `persistent_instructions` list in `config\runtime.json` is injected into planning, normal/degraded chat, every worker step, recursively spawned child tasks, final verification, and final-response repair.

Standing principles:
- Do the work rather than taking shortcuts merely to finish faster.
- Take the time needed to get the task right; correctness, completeness, and verification outrank speed.
- Use available tools proactively when they can inspect, measure, retrieve, test, or verify a material fact. Do not guesstimate what can reasonably be checked.
- Do not replace missing evidence with convenient assumptions. Gather more evidence when practical, and clearly mark uncertainty when it cannot be resolved.
- Improve efficiency through better planning, decomposition, reuse, caching, and targeted tool use—not by skipping necessary work or lowering verification rigor.
- Before finishing, verify important claims and results against observed evidence and the user's actual constraints.

Efficiency means reducing wasted work, not reducing rigor.

## Operator execution rule

For all but the simplest one-line operation, **start with `C:\Users\KHzz\Documents\Norm\verbatim_lines.py`**. Create a temporary Python script with the exact multiline code/query/prompt, run it, verify the result, then delete the temporary script. Avoid nested PowerShell escaping even if it initially appears manageable.

This is a standing operator rule, not merely a fallback after a quote-heavy command has already failed.

## File and image tool state

The model-facing executor provides bounded list/read/write/replace/delete, image analysis/vision, targeted connection checks, and an enabled bounded PowerShell `run_command` tool. Existing-file mutations require current hashes and deterministic read-back verification; deletes are staged reversibly until graceful shutdown.

`run_command` is configured with `powershell.exe`, a 180-second maximum timeout, allowed-root working directories, and bounded captured output. Results expose observed `exit_code`, `stdout`, `stderr`, timeout state, working directory, and duration so execution proof can enter durable evidence. Native file tools remain preferred for edits/deletes.

`tools\image_analyzer_v3.py` is live. It wraps v2 and adds local focused rescans across original/contrast/saturation/gamma views. The analyzer separates reference geometry from data objects and records inferred occlusion/layer relationships, but numeric price conversion still requires the separate chart-axis contract.

`app\norm_runtime\prompt_worker_legacy.py` and older analyzer files are retained history/baselines; they are not the selected live worker/analyzer path.

## Chart perception state

`docs\CHART_VISION_CHEATSHEET.md` is the generic object/layer/disagreement guide. `docs\CHART_AXIS_GATE.md` is the generic calibration contract and forbids pane boundaries, crop edges, separators, or unlabeled geometry as numeric price anchors.

For the selected SPY candle on Wed Sep 16 2026 at 15:15, TradingView's explicit OHLC is authoritative: O 751.33, H 752.12, **L 749.60**, C 751.24. The earlier geometry-only mapping that placed the low around 751-752.5 is superseded. Application OHLC tied to the crosshair/time outranks inferred wick geometry.

The axis gate requires at least two exact price↔y anchors plus slope/intercept/residual checks and local monotonic/bracket validation before geometry is converted to price. The gate is still instruction/runtime-contract level rather than a machine-enforced transform inside the analyzer.

SPY path work is currently stopped, not running. Compact salvage material remains under `docs\recovery-notes\spy-salvage-20260918\`; the next continuation should reuse those durable notes rather than regenerate settled work.

## SOS state

The 2026-09-17 08:18 SPY `final-verify` SOS has been processed during this maintenance pass: the task-specific Redis state was already cleaned, a later task passed `final-verify` at 09:37:41, and current `/health` is OK. Its original contents are preserved in the pre-maintenance backup, and the live `SOS.readme` has been deleted.

Standing rule: `SOS.readme` is a transient unresolved-task incident record. Keep it while the originating task still needs recovery/decision. Remove it only after PostgreSQL shows a durable terminal resolution: successful completion, or an intentional failed terminal summary. Preserve reusable lessons elsewhere first. The deployed worker now performs this automatically during terminal Redis cleanup only when the SOS task ID matches, PostgreSQL status is `completed` or `failed`, terminal summary verification passes, and the latest summary is nonblank.

## Persistence / memory state

Current PostgreSQL memory/runtime infrastructure includes messages, thread summaries, active memory items, task steps/evidence, oversized step segments, compact recovery notes, temporary thinking segments, maintenance notes, and consolidated background snapshots. Thread summaries are single-current-version: saving a new summary deletes older versions for that thread. Default trim/consolidation policy is semantic compression plus deletion of true redundancy; repeated instances of the same state keep the initial authoritative occurrence and later recurrences become compact timestamped 'happened again' notes. All timestamps must be timezone-aware; human-facing/default summary timezone is `America/New_York` (EST/EDT as applicable), while timezone-aware UTC is acceptable for internal storage.

`task_thinking_segments` is temporary working storage. Each model slice can persist raw thinking plus a condensed continuation note. On terminal cleanup, Norm requires a durable terminal summary and (for non-literal tasks) an effectiveness note, then blanks `raw_content`, stamps `purged_at`, and keeps only the condensed note/metadata. This prevents token-limit loss during work without retaining raw scratch reasoning after the task is done.

Recovery uses pointer-first handoff. Child scopes may stay equal or shrink but cannot widen beyond the parent scope; sequential recovery units advance one at a time; compact PostgreSQL/HDD notes are passed instead of bulk parent evidence; oversized evidence can be moved to `task_evidence_archive` while compact metadata stays live.

Deep history maintenance remains replay-validated and destructive only after backup/validation. Explicitly superseded memory rows are deleted rather than retained indefinitely.

Manual prune on 2026-09-18 used verified backup `C:\Users\KHzz\Documents\Norm-backups\postgres-prune-20260918-1720\norm_runtime_pre_prune.sql`. Counts changed: task runs 162 -> 45, task evidence 1002 -> 87, evidence archive 143 -> 0, runtime maintenance notes 137 -> 56. Messages stayed 153 and active memory items stayed 127. Two validated compact `task_history` records preserve the deleted SPY-recovery and deployment-smoke episodes.

## Connections and impaired context

Connection checks are demand-driven. Query CA8D, PostgreSQL, Redis, Ollama, Tailscale, etc. only when the current task requires that resource or maintenance explicitly asks for it. Do not turn an old connectivity observation into a current claim without a fresh targeted check.

Relative file paths are configured to prefer `\\KH-CA8D\Local1675` with KHzz `docs` fallback. Dropbox is not a native Norm runtime connection; screenshot transfer remains an external/manual-pull workflow.

If PostgreSQL context is unavailable at chat entry, Norm can generate from the current request and explicitly available tools with `resource_status` marked impaired. That path is intentionally not equivalent to normal durable coordinated execution.

## Open work

- Run a clean end-to-end `run_command` task under the promoted executable (the existing `Universal\ports.py` utility is a good regression) and confirm shell evidence satisfies both step and final verification.
- Restart SPY continuation from the compact salvage notes under the new runtime. Do not reuse the cancelled organic-fill retry tree.
- Harden verification structurally: a step that explicitly requires execution/runtime proof must not become `completed` without that evidence, and a final rejection for missing evidence should reopen the exact offending step/tool requirement rather than only rewriting final prose.
- Add a generic context budget/compaction policy to the normal worker path; one normal prior-step call still uses `completed_step_context(..., max_chars=None)` outside the oversize pointer-recovery path.
- Decide whether to encode `CHART_AXIS_GATE.md` directly into analyzer/runtime logic.
- Tighten semantic consolidation of redundant active memory rows.
- Build the dedicated second-machine coordinator and its independent per-step transition verification.
- Add persistent local-source indexing/retrieval for approved HDD reference files.

No open item should describe the already-understood JSON-internal/plain-English-user-output final-verification behavior as a bug.

## Documentation roles

- `README.md`: operator overview.
- `CURRENT_STATUS.md`: authoritative current state.
- `DEVELOPMENT_NOTES.md`: chronological history/lessons; old entries may be superseded.
- `FUTURE_IMPLEMENTATION_NOTES.md`: deferred or partially implemented architecture.
- `SOS.readme`: transient unresolved-task incident record; delete it only after the originating task is durably terminal in PostgreSQL (completed, or intentionally failed with a useful summary).
- `context\current.md`: deprecated placeholder; PostgreSQL is the active persistence layer.
