# Norm

**Norm 0.51.3** is the user's local general-purpose assistant and developing coordination layer on **KHzz**. It turns a request into bounded, verified work, executes against local tools and state, preserves durable evidence, and returns ordinary English/Markdown to the user.

## Project identity

- Name: `Norm`
- Version: `0.51.3`
- Author: `KernelHermit`
- Repository: `https://github.com/hermit-node`
- Canonical metadata: `config\settings.ini` (`[project]`)
- Runtime root: `C:\Norm`
- Writable workspace: `C:\Users\KHzz\Documents\Norm`
- Deployed executable: `C:\Norm\app\norm.exe`
- Verified deployed SHA-256: `2409ad004cd2c2d17937e35d37e7bd415728e7d94a3d04f714692465fe5239a2`

KHzz is the source of truth for the deployed runtime.

## What Norm does

Norm is not just a chat wrapper. The normal runtime path is:

```text
request
-> classify command
-> generate + independently verify a bounded plan
-> queue typed work nodes
-> execute steps with tool evidence
-> persist step results and recovery state
-> build final candidate
-> structured final verification
-> persist terminal reply/summary
-> task-specific transient-state cleanup
```

Internally, the control plane is schema-validated JSON protocol v2. User-facing answers remain normal prose/Markdown; JSON is for coordination and verification, not the final interface.

## Runtime and services

- Model alias/base: `norm` / `qwen3.8:27b-q4_K_M`
- Context length: `84000`
- Ollama: `11434`
- Norm HTTP/chat: `12543`
- Activity/control: `8766`
- Redis/Memurai: local `6379`
- PostgreSQL: local `25434`, database `postgres`, schema `norm_runtime`
- Runtime log: `logs\norm-runtime.log`

`config\settings.ini` is the human-editable source for ports, documentation pointers, and release metadata. `config\runtime.json` carries queue, storage, tool, memory, maintenance, PostgreSQL, and coordinator settings.

## Durable state, identity, and queues

PostgreSQL is Norm's durable ledger for conversations, plans, steps, evidence, summaries, recovery notes, maintenance records, and memory. Redis DB0/DB1/DB2 hold runtime task/work/deletion state. Redis DB3 is the durable GUI ingress/state buffer and is intentionally excluded from normal runtime Redis cleanup so queued console prompts survive Norm restarts.

The UUID migration introduced in 0.51.0 remains deployed in 0.51.3. Immutable `task_uuid`, `node_id`, and dependency `edge_id` values are authoritative runtime identity; readable task IDs and step labels remain compatibility/display aliases. The migration audit recorded 71 tasks, 424 nodes, 364 dependency edges, and 0 orphan nodes. New Redis work linkage uses UUID-backed node/chain identities while compatibility fields remain available.

Terminal state is written and verified in PostgreSQL before task-specific Redis cleanup. Startup and periodic reconciliation compare Redis against durable task state rather than assuming an empty queue means no unfinished work.

Weekly maintenance runs only while the work queue is idle. One scheduler checks hourly for eligibility but PostgreSQL controls the durable cadence: a regular cleanup is due every 7 days, while every due run becomes the extensive deep-history cleanup when the last successful deep clean is at least 21 days old (or has never occurred). Either path purges reproducible files under the configured image-analysis output root while preserving source/original images. PostgreSQL `runtime_state` stores `deployed_version`, `last_successful_cleanup_at`, and `last_successful_deep_cleanup_at`. Redis DB0 stores only the in-progress `norm:maintenance:weekly_cleanup:active` marker; a crash or graceful restart preserves that marker so the same cleanup can resume, and the marker is removed only after the run succeeds.

## Scheduling and recovery

Two mechanisms must remain conceptually separate:

- **Deferred append planning:** a follow-up to unfinished work stores only a durable placeholder. The real follow-up plan is generated only after the predecessor task fully completes and passes structured final verification. An intermediate completed step is not enough.
- **Oversized recovery:** large or unfinished steps use bounded child/recovery handling and pointer-based handoff. Replacement attempts are bounded and do not repeatedly consume the parent retry budget; exhaustion safety-stops instead of looping.

## Running Norm

- **Headless/API:** run `app\norm.exe` directly.
- **Operator console:** run `norm_gui.bat`. It opens prompt, runtime-stream, and completed-reply windows and attaches to an existing healthy Norm. Normal prompt input is written immediately to Redis DB3 and the prompt window returns to `You>`; a background dispatcher serially submits the oldest queued prompt through `12543`. Activity/control remains on `8766`, so `/stop` or `/stop all -now` can interrupt work immediately even while `/api/chat` is blocked. If an existing process is still starting, the GUI waits for health rather than launching a duplicate.

`norm_gui.bat` launches its helper shells with `cmd.exe /c`, so stream/reply windows close when Norm exits. On Windows those disposable viewers use native `SetConsoleCtrlHandler` handling and `ExitProcess(0)` for Ctrl+C/Ctrl+Break; real `CTRL_BREAK_EVENT` regression tests exited code 0 with no Python traceback even while the stream was blocked in WinSock.

The prompt window's Ctrl+C uses Norm's graceful shutdown control instead of directly killing `norm.exe`.

## Useful operator controls

- `help` / `/help` - list local GUI commands.
- `/stop norm` / `/start norm` - pause and resume acceptance of new console submissions.
- `/shutdown norm` - graceful checkpoint/drain/shutdown.
- `/stop ollama` - cancel the currently active model generation without shutting down Norm.
- `/shutdown ollama` - unload/stop the Norm-owned Ollama runtime.
- `/stop all` - finish the current step, preserve unfinished state, unload the model, and stop cleanly.
- `/stop all -now` - cancel active generation, checkpoint partial work, preserve the recovery buffer, and stop promptly.
- `/backup-zip` - create a settings-driven ZIP of PostgreSQL `norm_runtime`, the writable workspace, and the runtime tree. Rebuildable `.venv`/build/staging content and derived `workspace\images\analysis` output are excluded; restore helpers recreate the Python environment from pinned settings/dependencies when needed.
- `/repeat-submission` / `/repeat-answer` - requeue the latest applicable persisted turn verbatim.

Use `GET /status/busy` for authoritative live workload state; it combines pre-queue requests, model activity, worker state, Redis state, PostgreSQL running tasks, and CPU/GPU sampling instead of treating Redis alone as truth.

## Safe local execution

For anything beyond the simplest one-liner, prefer `C:\Norm\verbatim_lines.py` rather than nesting PowerShell/Python/SQL/JSON quoting. The preferred pattern is: write the exact temporary script through `verbatim_lines.py`, run it, verify observed output/exit state, then remove the temporary helper when appropriate.

Norm's bounded command/file tooling records observed exit code, stdout, stderr, timeout state, working directory, hashes, and read-back evidence so verification can rely on what actually happened rather than on claimed execution.

## Build and release

`tools\build_norm.py` is the stable PyInstaller release path. It reads `[project]` metadata from `config\settings.ini`, generates Windows version information, builds a versioned candidate, and reports SHA-256 before promotion. Substantial runtime changes should be staged, source-tested, packaged-tested, and backed up before replacing `app\norm.exe`.

## Key project areas

- `app\norm_runtime\` - planning, queue, execution, persistence, verification, recovery, and protocol code.
- `tools\` - build, GUI helpers, image analysis, and maintenance utilities.
- `config\settings.ini` - ports, documentation pointers, project/release metadata.
- `config\runtime.json` - runtime, storage, queue, tool, memory, and coordinator configuration.
- `logs\norm-runtime.log` - primary runtime log.
- `verbatim_lines.py` - safe exact-line writer for quote-heavy/multiline operations.

## Documentation map

- `README.md` - project/operator overview: enough context to understand, run, and navigate Norm without duplicating the full implementation ledger.
- `CURRENT_STATUS.md` - authoritative deployed facts, architecture state, and current limitations.
- `DEVELOPMENT_NOTES.md` - chronological implementation history and lessons learned.
- `FUTURE_IMPLEMENTATION_NOTES.md` - single active backlog/design notebook for genuinely future or incomplete work.
- `SOS.readme` - transient unresolved-task incident record; keep it until the originating task has a durable terminal resolution.

`config\settings.ini` (`[documentation]`) is the canonical source for maintained-document pointers.

## Current limitations

The README intentionally keeps limitations brief; exact current details belong in `CURRENT_STATUS.md`. Important deployed limitations include one normal completed-step context path without routine bounded compaction, incomplete structural enforcement of every intermediate evidence requirement, and the chart-axis calibration gate remaining a runtime/documented contract rather than a machine-enforced numeric analyzer transform.

For exact live state, trust observable runtime/database/queue evidence over stale prose, and use `CURRENT_STATUS.md` plus `/status/busy` rather than copying transient counts into this README.

