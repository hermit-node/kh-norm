# Norm current status

This file records the current deployed state and known limitations of the Norm runtime. It is not a backlog, a chronology, or an operator manual: active future work lives in `FUTURE_IMPLEMENTATION_NOTES.md`, history and lessons in `DEVELOPMENT_NOTES.md`, and operator-facing usage in `README.md`.

## Identity and live runtime

- Version: `0.51.3`
- Author: `KernelHermit`
- Repository: `https://github.com/hermit-node`
- Machine: `KHzz`
- Runtime root: `C:\Norm`
- Writable workspace: `C:\Users\KHzz\Documents\Norm`
- Live source: `C:\Norm\app`
- Live executable: `C:\Norm\app\norm.exe`
- Executable SHA-256: `2409ad004cd2c2d17937e35d37e7bd415728e7d94a3d04f714692465fe5239a2`
- Model alias/base: `norm` / `qwen3.8:27b-q4_K_M`
- Context length: `84000`
- GPU: NVIDIA GeForce RTX 3090, 24576 MiB
- Runtime log: `C:\Norm\logs\norm-runtime.log`

The promoted 0.51.3 executable passes packaged `--check` with Ollama, Redis, prompt queue, and PostgreSQL all `ok`. After promotion/restart both HTTP/activity health endpoints return `status=ok`; `/status/busy` reports `idle` with no active chat/model calls, no work, no pending, no retry, no escalation, and no PostgreSQL running tasks.

## Configuration and services

`config\settings.ini` is the human-editable source of truth for runtime service ports, maintained-document pointers, and project/release metadata (`Norm` / `0.51.3` / `KernelHermit` / `https://github.com/hermit-node`):

- Ollama: `11434`
- Norm HTTP/chat: `12543`
- activity/control: `8766`

Redis/Memurai is local on `6379`. PostgreSQL is local on `25434`, database `postgres`, schema `norm_runtime`. Norm HTTP/chat and activity/control bind only to KHzz's Tailscale address; Ollama binds only to loopback.

`config\runtime.json` carries queue, storage, tool, memory, maintenance, PostgreSQL, and coordinator settings. The live image analyzer configured there is `tools\image_analyzer_v3.py`.

The activity/control API exposes `GET /status/busy`. Busy detection does not treat Redis as authoritative: it combines pre-queue in-flight chat/planning requests, active Ollama calls, worker state, Redis work/retry/escalation state, PostgreSQL running-task context, and two 250 ms CPU/GPU activity votes with a third tiebreaker when needed. A PostgreSQL task can therefore be reported as `open_task_idle` instead of falsely implying active work.

The activity/control API also exposes `GET /status-context`. Each request builds and persists a complete raw Markdown evidence snapshot from the maintained README/current-status/development/future notes plus recent files/logs and live Redis/PostgreSQL/runtime state. The same Ollama model used by Norm is then called directly — without creating a normal Norm task, Redis work tree, or PostgreSQL task row — to generate a concise recency-weighted handoff. `/status-context` serves the actual saved Markdown contents; if summarization fails, the raw snapshot is served instead. Raw/generated files are retained under `docs\recovery-notes\system-context` and include a deterministic source/AI-warning footer.

## GUI/operator shell

`norm_gui.bat` is the lightweight local operator shell. It opens separate prompt, runtime-stream, and reply windows while using the existing Norm APIs rather than creating a second runtime. Healthy existing Norm instances are attached immediately; an existing-but-starting `norm.exe` is given up to 60 seconds to expose its APIs before attach; no duplicate instance is spawned. If Norm is fully stopped, the launcher may start `app\norm.exe` and wait for health.

The GUI uses project `default`, so PostgreSQL remains the canonical completed conversation record. Normal prompt input is first written to Redis DB3 (`norm:gui:ingress`) with a timestamp and prompt UUID, allowing the prompt window to return immediately. A dedicated GUI dispatcher submits one oldest prompt at a time through HTTP/chat `12543`; activity/control and immediate stop commands use `8766`. In-flight dispatch is tracked separately and an interrupted/ambiguous dispatch is quarantined as uncertain instead of being replayed automatically. DB3 is not cleared by normal runtime Redis cleanup, so queued GUI input survives Norm restart. On GUI startup, `/repeat-submission` and `/repeat-answer` recover the latest applicable turn and requeue it verbatim. A local UTF-8 JSONL fallback is used only when PostgreSQL message persistence is missing. Completed replies are delivered through an atomic UTF-8 staging file; the reply viewer reads the whole file only after completion, displays it once, then deletes the staging file.

`norm_gui.bat` launches all three helper shells with `cmd.exe /c`; stream/reply helpers wait through startup, then exit when Norm exits, so those companion windows close with the runtime. On Windows the stream/reply viewers install a native `SetConsoleCtrlHandler`; Ctrl+C/CTRL_BREAK exits the disposable viewer through `ExitProcess(0)`, avoiding Python traceback/reconnect behavior even while the SSE socket is blocked. Live graceful-shutdown and actual `CTRL_BREAK_EVENT` regressions both verified stream/reply exit code 0 with no traceback.

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

## Opaque task/node identity and dependency graph (DEPLOYED)

The UUID migration is deployed, not future work:

- Every current `task_run` has an immutable `task_uuid`; readable `task_id` remains a compatibility/display alias.
- `task_nodes` gives every planning, action, verification, child/recovery plan node an immutable `node_id` UUID while preserving legacy labels and a separate ordinal.
- `task_dependency_edges` gives every sequencing, verification, append, child, and recovery relation its own immutable `edge_id` UUID. Stable-plan reinitialization preserves existing edge UUIDs rather than recreating them.
- Plans persist `task_uuid`, `node_id`, ordinal, legacy `depends_on`, and opaque `depends_on_node_ids`. Redis work-node `message_id`, chain identity, previous-node, and next-node linkage use UUID-backed node/chain identities for new work; legacy task/step labels remain alongside them for logging/backward lookup.
- Append commands are runtime-enriched with `previous_task_uuid` / `previous_node_id`; the execution gate prefers those opaque references while retaining legacy fallback. Deferred append edges target the prerequisite/follow-up task identity, not an order-dependent placeholder label.
- Existing PostgreSQL rows were backfilled under an offline migration. Audit result: 71 unique task UUIDs, 424 node UUIDs, 364 dependency edges, zero null task UUIDs, zero null node UUIDs in all step-bearing working tables, zero dangling task-edge references, and zero plan/registry mismatches. A second schema pass preserved all 364 edge UUIDs exactly.
- Legacy direct `task_runs` inserts receive `gen_random_uuid()` automatically; old task/step lookup remains supported during transition.

## Queue durability

PostgreSQL is the durable task/conversation ledger. DB0/DB1/DB2 are transient runtime state and deletion staging; DB3 is the separate durable GUI ingress/state buffer and survives normal runtime Redis cleanup.

Current code behavior:
- Terminal completion/failure/cancellation is written and terminal-summary verified before task-specific Redis cleanup.
- Startup requeues pending work, preserves recoverable running work, finalizes unrecoverable running tasks into PostgreSQL, and clears Redis-only orphan tasks.
- Periodic Redis reconciliation is enabled with a 14,400-second interval.
- Graceful shutdown records unfinished state, drains, purges queued deletions, then clears runtime Redis.

No static queue/task count is maintained in this file because it becomes stale immediately. Use `GET /status/busy` for authoritative live workload state. The empty DB1 stream/group shell is normal infrastructure, not queued work; do not flush a live DB1 just to make `dbsize` literally zero.

## Scheduler/recovery mechanisms

Deferred append planning and oversized recovery are separate mechanisms and must not be conflated:

- **Deferred append planning:** append/follow-up requests persist only a `deferred-plan` placeholder while the predecessor is unfinished; the real follow-up plan is generated only after the predecessor task fully completes and passes structured final verification. A completed intermediate prerequisite step does not authorize an append while its predecessor task is still running.
- **Oversized recovery:** bounded child/recovery handling for large or unfinished steps. Cancelled/failed analysis or work children receive bounded replacement attempts without repeatedly consuming the parent retry budget; exhaustion safety-stops.

Other deployed behaviors:
- Worker shell guidance explicitly names `C:\Norm\verbatim_lines.py` for multiline/quote-heavy operations without widening native file-tool roots.
- `tools\build_norm.py` reads `[project]` metadata from `settings.ini`, generates the Windows PyInstaller version resource, and stages the versioned candidate before promotion.

## Persistent operating principles

The top-level `persistent_instructions` list in `config\runtime.json` is injected into planning, normal/degraded chat, every worker step, recursively spawned child tasks, final verification, and final-response repair.

Standing principles:
- Do the work rather than taking shortcuts merely to finish faster.
- Take the time needed to get the task right; correctness, completeness, and verification outrank speed.
- Use available tools proactively when they can inspect, measure, retrieve, test, or verify a material fact. Do not guesstimate what can reasonably be checked.
- Do not replace missing evidence with convenient assumptions. Gather more evidence when practical, and clearly mark uncertainty when it cannot be resolved.
- Improve efficiency through better planning, decomposition, reuse, caching, and targeted tool use — not by skipping necessary work or lowering verification rigor.
- Before finishing, verify important claims and results against observed evidence and the user's actual constraints.

Efficiency means reducing wasted work, not reducing rigor.

## File and image tool state

The model-facing executor provides bounded list/read/write/replace/delete, image analysis/vision, targeted connection checks, and an enabled bounded PowerShell `run_command` tool. Existing-file mutations require current hashes and deterministic read-back verification; deletes are staged reversibly until graceful shutdown.

`run_command` is configured with `powershell.exe`, a 180-second maximum timeout, allowed-root working directories, and bounded captured output. Results expose observed `exit_code`, `stdout`, `stderr`, timeout state, working directory, and duration so execution proof can enter durable evidence. Native file tools remain preferred for edits/deletes.

`tools\image_analyzer_v3.py` is live. It wraps v2 and adds local focused rescans across original/contrast/saturation/gamma views. The analyzer separates reference geometry from data objects and records inferred occlusion/layer relationships, but numeric price conversion still requires the separate chart-axis contract.

`app\norm_runtime\prompt_worker_legacy.py` and older analyzer files are retained history/baselines; they are not the selected live worker/analyzer path.

## Chart perception state

`docs\CHART_VISION_CHEATSHEET.md` is the generic object/layer/disagreement guide. `docs\CHART_AXIS_GATE.md` is the generic calibration contract and forbids pane boundaries, crop edges, separators, or unlabeled geometry as numeric price anchors.

The axis gate requires at least two exact price-y anchors plus slope/intercept/residual checks and local monotonic/bracket validation before geometry is converted to price. The gate is still instruction/runtime-contract level rather than a machine-enforced transform inside the analyzer.

## SOS state

Standing rule: `SOS.readme` is a transient unresolved-task incident record. Keep it while the originating task still needs recovery/decision. Remove it only after PostgreSQL shows a durable terminal resolution: successful completion, or an intentional failed terminal summary. Preserve reusable lessons elsewhere first. The deployed worker performs this automatically during terminal Redis cleanup only when the SOS task ID matches, PostgreSQL status is `completed` or `failed`, terminal summary verification passes, and the latest summary is nonblank.

## Persistence / memory state

Current PostgreSQL memory/runtime infrastructure includes messages, thread summaries, active memory items, task steps/evidence, oversized step segments, compact recovery notes, temporary thinking segments, maintenance notes, and consolidated background snapshots. Thread summaries are single-current-version: saving a new summary deletes older versions for that thread. Default trim/consolidation policy is semantic compression plus deletion of true redundancy; repeated instances of the same state keep the initial authoritative occurrence and later recurrences become compact timestamped 'happened again' notes. All timestamps must be timezone-aware; human-facing/default summary timezone is `America/New_York` (EST/EDT as applicable), while timezone-aware UTC is acceptable for internal storage.

`task_thinking_segments` is temporary working storage. Each model slice can persist raw thinking plus a condensed continuation note. On terminal cleanup, Norm requires a durable terminal summary and (for non-literal tasks) an effectiveness note, then blanks `raw_content`, stamps `purged_at`, and keeps only the condensed note/metadata. This prevents token-limit loss during work without retaining raw scratch reasoning after the task is done.

Recovery uses pointer-first handoff. Child scopes may stay equal or shrink but cannot widen beyond the parent scope; sequential recovery units advance one at a time; compact PostgreSQL/HDD notes are passed instead of bulk parent evidence; oversized evidence can be moved to `task_evidence_archive` while compact metadata stays live.

Deep history maintenance remains replay-validated and destructive only after backup/validation. Explicitly superseded memory rows are deleted rather than retained indefinitely.

## Backup and environment recovery

`/backup-zip` archives the PostgreSQL `norm_runtime` schema, the writable workspace, and the runtime tree. Rebuildable `.venv`, staging, build, dist, cache, and derived `workspace\images\analysis` content are excluded. `[environment]` in `config\settings.ini` pins Python `3.14.0`, CUDA Torch `2.14.0+cu126`, its PyTorch wheel index, the venv path, and `tools\requirements-lock.txt`. The restore BAT/PowerShell helper finds or installs compatible Python, recreates/repairs the venv only when needed, installs CUDA Torch first, installs pinned dependencies, validates runtime/vision imports, then restores PostgreSQL.

## Weekly cleanup / crash marker

Norm has one combined idle maintenance scheduler. It polls eligibility no more than once per hour; the hourly poll is only a cheap check, not an hourly cleanup. PostgreSQL `norm_runtime.runtime_state` is authoritative for cadence and currently stores `deployed_version`, `last_successful_cleanup_at`, and `last_successful_deep_cleanup_at`.

A cleanup is due when `last_successful_cleanup_at` is at least 7 days old. At that due run, if `last_successful_deep_cleanup_at` is at least 21 days old or absent, Norm runs the extensive replay-validated deep-history cleanup; otherwise it runs the regular background-memory cleanup. A successful deep run updates both cleanup timestamps; a successful regular run updates only `last_successful_cleanup_at`. Deep-history pruning still only considers eligible terminal history older than the configured 30-day retention threshold.

Both regular and deep runs purge the configured `tools.image_output_root` (`C:\Users\KHzz\Documents\Norm\images\analysis`) after verifying it is inside the writable workspace; source/original images are outside that derived-output directory and are preserved. The initial derived-output purge removed 1,359 files / 873,675,067 bytes.

Before any maintenance work, Redis DB0 receives persistent `norm:maintenance:weekly_cleanup:active` JSON containing the run UUID, mode (`regular` or `deep`), phase, start time, host, consumer, and resume count. Redis does not hold the durable last-success timestamps. If Norm crashes or is gracefully restarted during cleanup, the active marker survives and the same mode resumes on the next idle maintenance opportunity. The marker is deleted only after PostgreSQL success state and a maintenance note are written. `/status-context` reports the active marker plus the PostgreSQL runtime-state timestamps.

The deployed revision is `0.51.2b`; Windows fixed version metadata encodes the letter revision numerically as `0.51.2.2` while FileVersion/ProductVersion and `--version` display `0.51.2b`.

## Connections and impaired context

Connection checks are demand-driven. Query CA8D, PostgreSQL, Redis, Ollama, Tailscale, etc. only when the current task requires that resource or maintenance explicitly asks for it. Do not turn an old connectivity observation into a current claim without a fresh targeted check.

Relative file paths are configured to prefer `\\KH-CA8D\Local1675` with KHzz `docs` fallback. Dropbox is not a native Norm runtime connection; screenshot transfer remains an external/manual-pull workflow.

If PostgreSQL context is unavailable at chat entry, Norm can generate from the current request and explicitly available tools with `resource_status` marked impaired. That path is intentionally not equivalent to normal durable coordinated execution.

## Current limitations

These are factual limitations of the deployed/runtime architecture, not a second backlog. Designs and implementation work are tracked only in `FUTURE_IMPLEMENTATION_NOTES.md`.

- Readable task/step aliases are retained for compatibility, but opaque UUIDs are authoritative for current runtime identity and dependency linkage.
- Normal completed-step context still has one path without a routine bounded compaction limit.
- Intermediate completion is not yet structurally gated on every verification-evidence requirement.
- The chart-axis gate is not yet enforced as a numeric analyzer transform.
- There is no deployed second-machine coordinator, persistent HDD-source index, or evolving communication-profile subsystem.

Live workload counts are intentionally not maintained here because they age immediately; use `GET /status/busy` for current running tasks, queues, retries, escalations, and resource activity.

## Documentation roles

- `README.md`: operator overview.
- `CURRENT_STATUS.md`: authoritative current state and known limitations (this file).
- `DEVELOPMENT_NOTES.md`: chronological history/lessons; old entries may be superseded.
- `FUTURE_IMPLEMENTATION_NOTES.md`: the single active backlog/design notebook.
- `SOS.readme`: transient unresolved-task incident record; delete it only after the originating task is durably terminal in PostgreSQL (completed, or intentionally failed with a useful summary).
- `context\current.md`: deprecated placeholder; PostgreSQL is the active persistence layer.

`config\settings.ini` names the three maintained documents besides README (current status, development notes, future implementation notes); the promoted 0.51.1 source/runtime resolves those paths through `settings.py`, and `context_snapshot.py` uses the configured document-path loader rather than hard-coding the legacy future-notes path.