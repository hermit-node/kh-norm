## 0.55.10r26 installer-ready package correction (2026-10-18)

Installer 1.6.17 validates config/settings.ini and builds norm.exe from source. This package now includes sanitized default settings, r26 manifest/version metadata, and pinned media plugin dependencies (onnxruntime-gpu and imageio-ffmpeg). FFmpeg resolves from PATH or the installed Python wheel. CUDA inference is optional at runtime, and YuNet/SFace weights still require explicit approved setup. No private credentials or compiled binaries are included.

## 0.55.10r26 — Generic Vision Media (2026-10-18)

Built directly on r25. The built-in vision_parse plugin adds YuNet/SFace face observations and embeddings, stable UUIDs, global two-pass scene detection with a hard 30-second minimum, structured per-scene and whole-video categories, and read-only cross-video identity reconciliation. Different manual aliases never auto-merge; matching aliases require strong face evidence. Editable names and database writes remain responsibilities of Media Catalog. Optional ONNX Runtime GPU setup has CPU fallback. Existing N1/N2, queue, Ollama and installer behavior is unchanged. See docs/R26_GENERIC_VISION_MEDIA.md.

## 0.55.10r25 - Cockney compiler fixes (2026-10-16)

Direct patch to verified r24 source. Voice-profile 0.4.3 replaces weak editorial/PDF style anchors with dialect-aware, source-balanced Cockney anchors, strengthens model synthesis against sanitized corporate language, prevents borrowing distinctive source lines and favors short casual replies. Other profiles and all existing r24 Nabler changes unchanged. Source-only, no restart or activation. See docs/R25_COCKNEY_VOICE_COMPILER.md.

## 0.55.10r24 - Nabler structured reporting (2026-10-16)

Based on verified r23. N2 now prefers Nabler at every structured-output opportunity; Redis retains its structured rows plus rendered view, and Runtime consoles show the formatted report rather than raw Nabler tool JSON. No Arrow, Celery, installer or changes to other runtime settings. See docs/R24_NABLER_STRUCTURED_REPORTS.md.

## 0.55.10r23 — Non-image ChatGPT attachments (2026-10-16)

Built directly on uploaded r22. Expanded structured file-ID detection and explicit reporting of missing `sandbox:` file links, preserved document bytes in ZIP and PDF companion asset folders, increased binary attachment allowance to 256 MiB while retaining the 24 MiB image guard and 512 MiB total export budget. Existing N1/N2 and no-Celery behavior unchanged. Offline regression passed; no signed-in live browser validation. See `docs/R23_CHATGPT_FILE_ATTACHMENTS.md`.

## 0.55.10r22 — Lean N2 step-boundary context (2026-10-16)

Built directly on uploaded r21, with only intermediate-step context changes. Norm already starts fresh N2 messages after N1-accepted step completion; r22 bounds generic background rehydration to 8K characters on later generated steps and prevents old steps' full Redis evidence from entering intermediate-step verification. Original user constraints, prior-step pointers, on-demand Postgres/Redis retrieval, N1 acceptance, final-task evidence, browser assets, no-Celery and installer remain intact. No additional model summarization. Offline focused test passed; no live installation. See `docs/R22_STEP_BOUNDARY_CONTEXT.md`.

## 0.55.10r21 - ChatGPT image/file collection (2026-10-16)

Direct patch to the normalized r20 no-Celery source. Added extension-style first-party file-ID resolution, a Qt native browser download fallback for HTTP 403 failures, actual local binary export, image signatures, distinct media coverage reporting, default missing-file refusal, and focused regression tests. No live signed-in Qt verification; see `docs/R21_CHATGPT_MEDIA_EXPORT.md`.

## 0.55.10r11r9 — Shared browser ChatGPT compatibility (2026-10-16)

Based on the exact uploaded r11r8 no-Celery portable source. Qt shared browser now starts on demand, supports sign-in popups, ChatGPT contenteditable input, conversation-focused scrolling, read-only UI diagnostics, and lazy legacy visual-PDF loading. Existing no-Celery recovery, installer and user-plugin preservation are unchanged. See `docs/R11R9_SHARED_BROWSER_COMPAT.md`. Source-only; live ChatGPT Qt session unverified.

## R11R7: strict recovery scope IDs (2026-10-15)
See docs/R11R7_STRICT_SCOPE_IDS.md. No running Norm process changed.

## 0.55.10r11r6 - Workspace stability (2026-10-15)
Source-only follow-up to r11r5, preserving Celery and all other changes. See docs/R11R6_WORKSPACE_VALIDATION.md for tests and limitations.

## 0.55.10r11r5 — detached Celery orphan watchdog and suppressed receipt durability (2026-10-15)

Built on the user-supplied r11r4 portable source. **Offline source candidate only: no running Norm installation was modified.** Changes are limited to the service heartbeat, solo-worker self-monitor, N1 result interpretation, and the existing suppression snapshot/restore path.

- Existing idempotent pool remains four workers by default. A solo worker exits when Norm has been absent long enough (75-second expiring service lease plus 180-second grace), or a dispatch reaches its one-hour ceiling. Worker processes also fence old service generations. An early startup allowance is 300 seconds. A completed Celery result stays available; a watchdog-stopped dispatch is reported to N1 as unresolved, not assumed to have failed harmlessly.
- Suppression snapshots now retain outstanding Celery receipt identity across Redis TTL expiration. Resumption restores those receipts with the waiting jobs; N1 evaluates the actual result or suppression cancellation and does not silently re-dispatch the previous operation.
- The original task-parking infrastructure, user permissions, N1/N2 division, Redis DB allocation, PostgreSQL pool design, and `read_file`/mutation synchronous paths are unchanged. **No live Windows integration test yet.** See `docs/R11R5_VALIDATION.md` for 27 passing focused tests and remaining limits (including a GIL-held native execution).

## 0.55.10r11r4 — N1 Celery wait decisions and task-scoped suppression (2026-10-15)

Incremental changes on the supplied r11r2 baseline plus r11r3 recovery/resume patch. This is a **source-only offline candidate**, not a change deployed to KHzz.

- **N1 owns Celery observation at 15 minutes:** At the first outstanding 15-minute poll, N1 chooses `stop` or `continue` using observed worker/execution state. Continuing schedules the next check (30–900s); stopping produces an explicit `timed_out`/`async_unresolved` result and preserves the no-duplicate-dispatch guard. The existing one-hour hard deadline remains in place. Results flow to N1 before N2. A timeout is not proof of execution failure, and stopping observation does not cancel Celery work.
- **Suppress task = cancel task-owned Celery dispatches:** After durable suppression and before Redis queue cleanup, Norm finds signed Celery approvals belonging to the suppressed task tree, revokes queued jobs, and sets cancellation markers. Running Windows solo workers are terminated only after verifying a Norm-owned PID and the current Redis `active:<pid>` assignment matches the exact dispatch; unrelated workers remain alive. Existing worker launcher is requested to replenish only a terminated slot, under its identity lock. Existing N1 receipt records survive suppression for resume; the resumed step gets an explicit cancellation result rather than redispatching automatically.
- No change to generic task parking, Redis/PostgreSQL schema, plugin interfaces, core read/write execution routing or independent N2 analysis. PostgreSQL inspection plugins remain eligible for the existing N1-approved Celery path; `read_file` streamed reads and mutations remain synchronous because blindly moving side-effecting calls would break dependent-step ordering. N2 can continue independent work within its current step; making cross-step independent completion safe requires a separate durable result mailbox and was **not** implemented here.

Focused verification: `tools/test_celery_n1_suppression_r11r4.py` (6 cases) plus the r11r3 regression suites (7+3 cases), compilation, and selected historical tests. Windows PID termination/replenishment and Redis/Celery/Postgres concurrency require live testing before promotion. See `docs/R11R4_VALIDATION.md`.

## 0.55.10r11r4 — N1 owns Celery non-response; targeted suppression hard-stop (2026-10-15)

Based on the r11r3 candidate, retaining the user's r11r2 source baseline. **No redesign of Norm's existing task-parking or step-planning architecture.**

- After 15 minutes without a Celery response, N1 receives execution-phase/worker status, chooses `continue` (bounded polling) or `stop` (an explicit unresolved timeout). The original 60-minute hard observation deadline stays. Neither decision automatically kills or repeats a Celery job.
- `/suppress-task` persists the original task-tree snapshot and fences outstanding approved Celery dispatches, revokes queued jobs and verifies both dispatch-to-PID ownership and the actual Norm worker process before hard-stopping only a worker executing that suppressed task. Other jobs/workers stay untouched. A killed slot is replenished via the existing idempotent launcher. No global Celery shutdown or new queue system.
- Cancellation receipts remain available for N1 when the suppressed task resumes; no automatic redispatch. `tools/test_celery_n1_suppression_r11r4.py` and `tools/test_celery_suppress_n1_r11r3.py` cover offline ownership/cancellation and N1 non-response.
- Retains the r11r3 permission-step hold, context-injection wake, recovery N1 scope exception, and suppression/resume fixes. Celery continues to use the existing N1-approved registered tool executor; no expansion of mutation privileges.

**Boundary:** N2 can continue independent tool calls within its current step while N1 awaits Celery. Cross-step advancement with a pending independent receipt requires additional dependency bookkeeping and is **not** claimed as implemented here. No live Windows, Redis, PostgreSQL, Ollama or Celery service test; this source package is a candidate, not a deployed hotfix.

## 0.55.10r11r3 — Permission, recovery, suppressed resume, and Celery diagnostics (2026-10-15)

**Base:** the exact user-supplied `Norm-0.55.10r11r2-Browser-Postgres-Unified-Portable-Source(1).zip`. Source-only candidate; no KHzz runtime or database was modified.

- Added explicit N2 `request_user_approval` native action. On use, the running step and all later chain steps park in a Redis approval-wait sorted set; the step stays RUNNING rather than falsely completing. The chat request returns the actual approval question and `/inject-context --task TASK_ID` instruction. The injection is durably stored before atomic restoration of the paused step, without assuming that a reply means yes. The model must interpret the actual user's approval/denial. An injection arriving just before the park also wakes it, and approval-wait jobs are included in suppression snapshots.
- Recovery children can call `request_scope_reconsideration` to ask N1 about an evidence-backed, previously incomplete predecessor step. N1 returns a bounded, deduplicated approve/deny receipt. This **does not** overwrite predecessor checkpoints or bypass per-tool authorization; the current recovery step may perform only the specifically authorized missing work.
- `/resume-task` now waits for cancellation to unwind before restoring Redis jobs and acknowledges queue restoration immediately rather than blocking for eventual task completion. Includes async Celery delayed jobs and paused approval steps in the durable suppression snapshot.
- Celery solo workers reuse a process-local tool executor, emit submitted/bootstrap/execute/result markers, and only announce worker readiness after `worker_ready`. Norm skips speculative N1 ETAs for never-started jobs and attaches diagnostic phase to unresolved 60-minute observations. **The one-hour deadline remains unchanged; this is not a guarantee every underlying tool will finish.**
- Offline regression suites: `tools/test_recovery_suppress_celery_r11r3.py` and `tools/test_permission_gate_r11r3.py`; see `docs/R11R3_VALIDATION.md`.

**Limitations:** Source compilation and isolated regression tests only. No live Redis/PostgreSQL/Celery/Ollama/Windows integration run. Existing N2 model behavior that asks for permission in plain prose instead of invoking `request_user_approval` may still fail to park; the system prompt now explicitly instructs native gating. Verify on an expendable task before using this for destructive or long-running operations. No runtime redesigns, plugin changes, or database migrations were performed.

## 0.55.10r11r2 — Shared PostgreSQL boundary and python-sql

- All Norm-owned PostgreSQL connections through `postgres_pool` use one per-connection JSON/JSONB serialization policy (`configure` callback). No process-global psycopg adapter, extra pool, new database, or new connection credentials are introduced.
- `pg_json_safety` normalizes database-bound JSON/JSONB only: NUL and unpaired surrogates are literal-escaped, binary is represented as Base64, and changes are logged with metadata rather than content. The executed tool parameters/results remain unmodified; `record_evidence` continues storing explicit per-row persistence notices. Non-JSON text and arbitrary SQL arguments are deliberately *not* silently changed.
- `python-sql==1.8.1` is pinned in both `core/requirements.txt` and the installer-facing `tools/requirements-lock.txt`; new runtime-state read queries use its parameterized builder. Existing PostgreSQL-specific DDL and static SQL remain psycopg 3 statements. Context/shutdown snapshot queries use quoted schema identifiers.
- The common pool now classifies/logs SQLSTATE failures consistently without changing commit/rollback boundaries or automatically retrying operations. N1/N2 recovery logic, pool sizing, plugin interfaces, credentials, and installer/executable workflow are preserved. No executable recompile required.
- Offline tests verify JSON adaptation, audit behavior, pool registration, query-builder wiring and browser regressions. **A full live PostgreSQL test and Python 3.14 dependency-install/Windows validation have not been performed**; this package is a test candidate.

## r11r1 addendum — Browser API/scroll compatibility and evidence persistence

Built on r11 Browser Cooperation source, retaining the existing installer, lightweight launcher, profile, cache and memory settings. Browser updates: API pagination fallback and multi-layout visual scroll discovery. Task-runner updates: in `PostgresTaskLog.record_evidence()`, PostgreSQL-bound copies of arguments/results encode unsupported Unicode safely; any conversion is explicitly marked in the persisted evidence. Original tool execution, arguments and in-memory result are not changed. Added `tools/test_pg_evidence_unicode_r11r1.py`. A task previously parked for this fault may already have renamed its files; verify source/destination state before resuming. Live database and Qt verification remain outstanding.

## r11 addendum — Norm Browser Cooperation

From unchanged r10 source baseline; new `norm_browser` plugin and Qt-browser UI additions. All code changes are documented in `docs/NORM_BROWSER_COOPERATION.md`; original r10 launcher and cache allocations are retained. Source-only candidate; live Qt browser verification is outstanding.

# Norm 0.55.10r10 - terminal-only launcher restored

Only the launch route and package metadata changed from r9. See r9 details below.

---

# Norm 0.55.10r9 — source-only full memory batch commits

This revision extends the exact r8 StegoSplit/StegoServe portable-source baseline with **only** the maintenance-source changes and associated configuration/tests/docs. Full destructive maintenance now inventories/fixes source data into PostgreSQL, groups up to 20 memories, explicitly N1-reviews and reconstructs every retained memory, then atomically commits the condensed output and deletes originals before advancing. New records wait for later inventory. Pending input is stored until committed, then removed. Weekly incremental JSON checkpoints are also source-frozen, avoiding the previously observed fingerprint restart loop. No live migration, flash, database delete, installation or service restart was attempted.

# Norm 0.55.10r8-stego2 source-only release — 2026-10-14

Based on the user-provided **Norm 0.55.10r8 visual-capture callback-fix portable source**.

This release changes only the bundled Stego plugin layout, its declared plugin settings, source package metadata, and documentation. All other Norm runtime source files are copied byte-for-byte from r8. **No Norm executable or installer has been rebuilt. No files on KHzz or live Norm are overwritten by this archive.**

* **`stegosplit`** absorbs `stegosplit_message`: general V3 encrypted pair text/file/credential APIs and a new deterministic geometric (not SVG) 12-color raster everyday-object base-image generator.
* **`stegoserve`** replaces the old specialized key/auth plugins as Norm's assignment/issuance management interface to the **separately built** standalone v0.6 engine; includes password-free company/user planning, local interactive batch issuance/rotor-round rotation and signed destination provisioning, plus signed-route preflight.
* Old `stegosplit_message`, `stegosplit_key`, and `stegosplit_auth` plugin folders and original plugins.ini are preserved outside the live plugins folder under `legacy_stegosplit_plugins_r8/` for recovery, NOT loaded by default.
* The 128 independent formulas are provided by the **external compiled v0.6 credential engine**, **not** the original generic V3 0.4.3 message API. Avoid conflating the two image envelopes. This archive by itself is **not** a full v0.6 AuthGate binary distribution.
* The standalone `authgate_server.py` must be deployed separately for live endpoint gating. A blocking port alone never intercepts an existing SSH port; bind/firewall restrict direct access after independent verification.

## Validate

Check both `plugins/{stegosplit,stegoserve}/plugin.json` checksums against the actual `src/` trees, test deterministic polygon PNGs and password-free plan validation, and run Norm PluginManager refresh on Windows x64 CPython 3.14. Native compiled Windows plugins cannot be dynamically executed on Linux. See `docs/STEGO_TWO_PLUGIN_INTEGRATION.md`.

Norm 0.55.10r8: Visual PDF callback transport fix; source-only package. Requires the PyMuPDF dependency listed in core/requirements.txt. See docs/RELEASE_NOTES.md.

Norm 0.55.10r7: independent N1 parent-step review; source-only, not installed. See docs/RELEASE_NOTES.md.

# Norm 0.55.10r6 — chat-style rich dialogue PDF

Updates the rich-dialogue formatter used by `/pdf-chat` so the output reads like a back-and-forth conversation instead of a compact article. User and assistant turns are visually separated, continuation labels survive page breaks, and citation links remain live.

# Norm 0.55.10 - Integrated Workspace Source

Continuing 0.55.9r13 browser-PDF with native three-terminal workspace, persistent fonts/layout and locked startup/shutdown. See docs/WORKSPACE_05510.md.

# Norm 0.55.9r13 - Shared-browser visual-PDF source

Based on Norm 0.55.9r12 source, preserving core and installer-facing settings. Adds Qt Chromium viewport stitching, speaker-aware PDF pagination, naming from recent user messages, and native shared-browser tool bindings. See docs/RELEASE_NOTES.md and docs/SHARED_BROWSER.md.

# Norm 0.55.9r4 - integrated reversible folder trash and quiet workers

This build preserves N1 verification-pool/tool authorization, N2 reasoning
and final step behavior. The existing Redis trash now supports entire
folders and inventory detects held historical directory orphans. Operator
soft-delete works through either console's direct /soft-delete command.
Celery subprocesses use pythonw.exe to avoid desktop console windows.
The native desktop GUI plugin/bridge is read-only at this stage (UI input
is disabled). No live install or Redis mutations were performed here.
Run the installer candidate self-test before deploying.

# Norm 0.55.9r3 - Frozen Celery import repair

The Norm 0.55.9r2 native tool agent split, N1 verification-pool-first
gate, N2 step/conclusion logic and Celery's configured Redis database
are unchanged. The corrected installer collects Celery/Kombu and related
runtime modules explicitly when building norm.exe and rejects compiled
candidates that fail --self-test-celery BEFORE the installer promotes
them. Tested a real frozen executable on KHzz before this release.
See docs/RELEASE_NOTES.md for details.

# Norm 0.55.9r2 - N1-approved asynchronous tool execution

This is a source release for live testing. N1 remains the tool and
verification-pool gatekeeper; N2 remains the worker. Optional Celery
uses config/settings.ini ([celery]), with four Windows solo workers,
configurable Redis DB4, and a synchronous fallback. No Celery
final-answer model exists. See docs/DEVELOPMENT_NOTES.md.

# Norm 0.55.9 - verified public-plugin source identity

Corrected web_browser manifest checksum without altering plugin source.

# Norm 0.55.8r10c - verified reference/assignment separation

TaskPlan preserves verbatim user origin separately from child assignments. N2 executes only the active step and N1 checks current evidence against the original constraints without restarting completed work.

# Norm 0.55.8r10b - active-step-first portable source

All internal N1/N2 handoffs preserve the original user request for constraints and provenance. N2 executes only the current assigned step and preserves completed work; N1 checks that step against evidence and original-user intent without reissuing the root task.

# Norm 0.55.8r10a original-prompt handoff hotfix

Includes explicit PromptJob.original_user_prompt and durable-root restoration before N2 execution and delegation. Built from the r10 portable source staging tree, preserving the same live-derived PDF/web plugin fixes. See release notes.

# Norm 0.55.8r10 portable source - recovery contract integration

Built from the verified r9 source, not the live installation. Adds the r10 objects boundary, retained live web import fixes, unchanged PDF reader source, failed-task console controls and verifier-confirmed automatic injection retirement. The live C:\Norm installation is unchanged. See `docs/RELEASE_NOTES.md` for scope and validation limitations.

# Norm 0.55.8r9 — restore native N2 conclusion and N1 final acceptance

**Source release (not a precompiled EXE), based on the user-supplied 0.55.8r8 ZIP.**
Celery workers and the DB5 terminal pipeline have been removed. All AI-generated
user-facing output uses the normal N2 worker with its active voice, native tools,
Nabler, context and N1 tool gate. N2 self-checks, submits the final candidate,
and N1 must explicitly accept; unavailable or rejected N1 review cannot be
silently substituted with an unrestricted model call or an automatic acceptance.

N2 execution keeps the unchanged original request in the Ollama `user` role as
*intent/constraints reference*, never as a second instruction to restart the
original task. Only the currently approved N2 step is executable.

A genuinely terminal failure retains SOS, Redis/PG checkpoints and the prior
history, then attempts a **`task_kind=recovery`** successor with a stable new task
ID and parent pointer. Recovery proceeds through N2's check planning, N1
step review, N2 evidence verification, N1 review, N2 remaining-work planning,
execution, and a fresh N2→N1 final. Compact task metadata is carried forward;
full archived evidence is fetched selectively. A recoverable normal round yield
continues on the existing ID, without manufacturing a recovery task.

Celery for approved CPU-side disk/DB tools is deferred in
`docs/FUTURE_IMPLEMENTATION_NOTES.md`. The original compiled StegoSplit
modules are unchanged. Tests are offline and cannot prove Windows/Redis/Ollama
integration until installed and smoke-tested. No live `C:\Norm` was modified.

---

> 0.55.8r8: Celery DB5 terminal workers (five Windows solo processes); deterministic file/hash/text checks without Ollama; paragraph-level verified response; successor ID only for proven missing work. See docs/TERMINAL_CONCLUSION_R8.md. Source-only ZIP; Installer 1.6.12 builds norm.exe.

> 0.55.8 continuity r7 terminal source: single final-step Redis verification, successor handoff only if FALSE, restartable verified paragraphs. Based on installed r6 source. See docs/TERMINAL_CONCLUSION_R7.md. No Windows EXE rebuilt.

> **0.55.8 continuity r6 source-only:** Preserves the original user request as an Ollama `user` message on generated N2 steps, while explicitly separating planning from **active-step execution** (no re-planning or redoing completed work). Includes all r5 token-budget and Redis/Markdown output-overflow changes. N2 without tools remains at 16k. See `docs/RELEASE_NOTES.md`. This ZIP is not a rebuilt Windows executable.

### 0.55.8 continuity/supervision update (2026-10-09; source-only, not live-validated)

- Ordinary 16-round step yields now use the existing PostgreSQL context-slice archive and N1 compact continuation handoff, preserving the previous cumulative checkpoint and negative findings across successive slices. The original task stays authoritative and raw execution remains recoverable.
- Formal plan verification routes through the existing N1 Ollama client (same loaded model, N1 agent policy). N2 still drafts and performs its bounded pre-submission plan check.
- Repeated identical file/directory reads and equivalent simple `git log` investigations are bounded by N1 per task/step; distinct file offsets and other Git queries remain usable. Repeated tool observations are reported separately from distinct evidence during task-budget reviews.
- Repeated ordinary step-round yields prompt a bounded N1 stalled-step check. Only a positive substantive decomposition decision invokes the existing oversize-recovery child-task machinery; the default remains simple continuation.
- **Important diagnostic caveat:** the observed ineffective run used Laguna, whereas restarting Norm returns the canonical `norm` model. Model quality and reset working state were uncontrolled variables. The observed loop does NOT independently prove these orchestrator changes are needed or effective. Run the same task on both models under controlled conditions before attributing causality.
- Does not change installed configuration, model selection, installer, plug-in files, or compiled StegoSplit. Tests are offline/mock tests, not a live Windows end-to-end validation.

### 0.55.8 review-retry checkpoint-handoff revision (2026-10-09)

- The N1 step-alignment retry now archives original request, primary task, step acceptance criteria, **unchanged N2-verified candidate**, full observed evidence, job metadata/working context, and previously completed-step progress in the existing PostgreSQL `task_context_slices` store.
- The existing `checkpoint_step()` records its archive pointer and first failure before the retry. The review reconstructs its inputs from the saved PostgreSQL slice and receives a compact, deterministic handoff (including progress and archive reference). No additional summarizer-model call or second Python agent is started.
- N1's actual accept/retry decision remains independent; after a second *processing* failure, pass-through retains the `n1_step_review_status=unavailable_after_2_attempts` marker. Failure to persist/read back the checkpoint is a checkpoint failure, **not** grounds for silently reporting N1 approval.
- This is a synchronous fresh N1 model invocation using a persisted/reloaded snapshot, not an independent crash-proof review-only queue consumer. A hard process crash during this retry still relies on the broader existing queue recovery path; that path is not modified here.
- `agents.n1.task_budget_review.num_predict` controls N1's progress-extension review (fallback 900); `agents.n1.step_review.num_predict` controls step alignment (default 6000, capped at 10000); the 900-token command classifier is a distinct hardcoded call. No existing runtime config budgets were modified for this revision.

## Norm 0.55.8 — N1 bounded-review recovery, queue-full visibility, and richer idle detection (2026-10-09)

- Based solely on the user-supplied 0.55.7 CLEAN portable source. The original compiled StegoSplit/Auth/Key binaries, their plugin manifests, and plugin catalog identities remain unchanged.
- N1 step alignment remains `think=true`; default generation is 6,000 tokens with a 10,000-token hard cap and 300-second timeout cap, even if a preserved local configuration requests 86,000 output tokens. These are output/time bounds, NOT a change to the Ollama context window.
- Only AFTER the original N2 structured step verifier accepts a candidate: N1 processing failure checkpoints the exact candidate and evidence in existing PostgreSQL task-step storage, retries N1 once, then passes through the original N2-verified candidate if N1 remains unavailable. This is a synchronous review restart following the durable checkpoint, not a second worker or process. The step verification record indicates N1 was unavailable; a real N1 rejection still returns specific repairs to N2.
- N1 final verification now requeues the SAME candidate for its one existing processing-failure retry instead of asking N2 to rewrite it merely to make the failed review easier. The pre-existing deterministic artifact-safety precondition for review-failure pass-through remains intact.
- `/queue-full` now merges GUI ingress and uncertain deliveries with an explicit worker/Redis + PostgreSQL read-only inventory (live/pending, parked retries, escalations, dead letters, running, failed and suppressed; flags resumability and partial probe failures). New endpoint: `/status/queue-full` on Norm's activity API.
- Idle/resource observation now uses three intervals and measures CPU, GPU compute and GPU memory-controller activity; tracked HTTP/model/worker/queue signals retain priority. Pure external resource activity retains its existing 30-second grace rule and suppression checks.
- Preserved the shared-model N1/N2 setup, first accepted answer, existing recovery framework, voice profile and all compiled plugins. No new Python verifier/agent, no installer changes.
- Tests: focused isolated source tests. NO live Windows/Ollama/PostgreSQL/Redis execution, no rebuilt Norm EXE or Installer EXE; do not describe this as live-validated. A sudden process crash during the synchronous N1 retry is handled by existing queue recovery, not a new N1-only replay-after-process-loss guarantee.

## Norm 0.55.7 — explicit N1/N2 invoke-source provenance (2026-10-08)

- Every typed work-request JSON includes `invoke_source` and `invoke_target`; only genuine operator-ingress prompts are attributed to `user`. Internal generated steps remain `n1`, `n2`, or `system`, regardless of quoted text.
- N2 step-result and final-candidate JSON are tagged `invoke_source=n2`, `invoke_target=n1`; final-candidate normalization retains the tags for durable checkpoint/resume.
- N2 plan self-reviews, plan submissions to N1, N1 review directions, targeted repairs, and N1 loop interventions include a small JSON attribution header. The original operator request remains a separately authoritative field, not overwritten by agent follow-ups.
- This is metadata/routing provenance only: existing N1/N2 review criteria, shared voice profile, context-injection lifecycle, first-accepted-answer preservation, and native Ollama roles remain intact.
- All compiled StegoSplit/Auth/Key plugin binaries remain unchanged, as does installer 1.6.12. Select the adjacent 0.55.7 source; the prebuilt installer EXE embeds an older fallback.

## Norm 0.55.6 — planning self-audit, shared N1/N2 voice and strict step review (2026-10-08)

- N2 reviews its first proposed plan for omitted original requirements, missing prerequisites, sequencing and verification weaknesses, redundancies, inefficient effort, and cases where splitting a step into bounded child-task candidates improves outcome quality.
- The self-review returns a structured `improve` verdict, issue list and a 0–1 confidence assessment. A `no improvement required` verdict is accepted by N2 only at **>= 0.50 confidence**. A lower score causes one additional scrutiny pass. After a second uncertain pass, N2 records the uncertainty for an independent verifier rather than falsely declaring the plan complete.
- Exactly one targeted plan rewrite is allowed if a concrete actionable deficiency is identified. Self-review cannot rewrite an already satisfactory plan for cosmetics. A split becomes explicit planned execution steps, not automatically spawned nested runtime subtasks.
- N1 independently checks every N2 step submitted for acceptance against the original user prompt, primary task request, current internal step, verification requirement and evidence. N1 sends material deficiencies back for targeted correction rather than rewriting satisfactory work.
- N1, the N2 worker, and structured planning calls read the same validated active voice profile as a style prior. The persona cannot override safety, the original task, evidence or strict JSON schemas.
- The prior 0.55.5 fix preserving first satisfactory answers and its structured verifier stay intact. N1 final review remains active.
- All prior CLEAN compiled StegoSplit/Auth/Key modules remain binary-identical. No plugin source or trusted plugin catalog is changed.
- Windows Installer 1.6.12 EXE is unchanged with its older embedded fallback; select the adjacent 0.55.6 portable ZIP, or rebuild from the included builder kit on Windows.

## Norm 0.55.6 — N2 plan self-review without automatic rewrites (2026-10-08)

- Restored one deliberate **N2 planning-stage self-audit** before the independent plan verifier, looking for missing prerequisites, redundant work, improper ordering, unbounded steps, avoidable tool/model costs, and user-constraint or verification gaps.
- Self-audit uses a strict structured yes/no verdict and up to four concrete issues. A clean plan stays unchanged; only a material identified issue permits one targeted refinement pass. Never regenerates a plan simply for style or polish.
- Existing independent plan verification/repair, structured step verification, N1 final approval, and 0.55.5 first-final-answer preservation remain intact. Failed self-audits do not bypass independent verification.
- The verified-plan audit record now includes `plan_self_review.status` (checked_no_change, targeted_revision, proposed_no_change, unavailable) and issues, preserving provenance without turning internal instructions into user messages.
- The CLEAN compiled StegoSplit/Auth/Key plugins and their source identities are unchanged.
- The installer EXE is still the prior prebuilt 1.6.12 executable with older embedded fallback; use the adjacent 0.55.6 source or build the installer kit on Windows.

## Norm 0.55.5 — Preserve first satisfactory N2 answer (2026-10-08)

- Based on the operator-supplied 0.55.3 CLEAN compiled source, incorporating all 0.55.4 voice/provenance fixes.
- Removed the **unconditional, free-form completion-review regeneration** after N2's first answer in both tool and no-tool paths. Previously, a second answer could silently replace the first before any structured verdict.
- Preserved the independent structured step verifier. On acceptance, the original answer is retained **verbatim**; on rejection with specific issues, the existing targeted correction flow remains available.
- Preserved the structured final verifier and N1's final acceptance/revision gate. N1 may still request corrections for identified defects.
- Preserved compiled StegoSplit modules and plugin catalog identities; no plugin source or metadata changed.
- The included Installer 1.6.12 Windows EXE is reused, not recompiled; it must select the adjacent 0.55.5 portable source instead of relying on its older embedded fallback.
- Local Python checks are not a live Windows/Ollama integration test.

## Norm 0.55.4 — provenance/voice fix rebased on CLEAN compiled release (2026-10-08)

- **Base:** operator-supplied Norm 0.55.3 CLEAN compiled portable-source and Installer 1.6.12 kit, **not** the earlier uncompiled StegoSplit source.
- **Reinstated prior 0.55.3 N2 fixes removed from CLEAN:** the active voice profile is a dedicated per-request system instruction (including thinking/review/tool rounds), and internal planning, N1 interventions, retries, and completion reviews are not misidentified as new user-role prompts. The separate original user request retains its user-role provenance.
- **Preserved:** Message 0.4.3, Auth 0.1.0, Key 0.2.0a1 compiled binaries and their exact plugin SHA identities, the help structure, the context-injection lifecycle and all other clean-release code/plugins.
- **Packaging:** release 0.55.4, source ZIP and checksum, Installer 1.6.12 builder kit updated to embed this source. The previously compiled Windows Installer 1.6.12 executable is not rebuilt in this environment; if supplied as a companion, its embedded fallback remains 0.55.3. When using that EXE, the adjacent 0.55.4 portable-source ZIP must be selected/verified.
- **Verification boundary:** source/code regression testing is not an end-to-end Windows/Ollama/Redis/PostgreSQL install. Optional image-based Auth is not an SSH/PAM/TLS authentication integration.

## Norm 0.55.4 clean compiled portable source

Compatible with Norm Installer 1.6.12, Windows Python 3.14 x64. Norm core is portable Python source that the installer compiles into norm.exe. Proprietary StegoSplit internals are precompiled native .pyd modules, accessed through readable plugin src/main.py adapters. StegoSplit Auth reuses Message's native V3 modules instead of duplicating them. Each plugin includes a developer README.md and a user-facing help.txt available with /help stegosplit-message, /help stegosplit-auth or /help stegosplit-key. Master and credential images are NEVER distributed in this package. Filename-derived passwords and hidden PNG file selection are convenience/obscurity only. Native SSH or a network-facing authentication endpoint are NOT provided.

## 0.55.8 continuity r4 hotfix — source-only, not installed

Builds upon the separately prepared 0.55.8 continuity r3 portable-source archive. Repairs three N2 context routing problems in `core/norm_runtime/prompt_worker.py`: independent bounded PostgreSQL prior-step retrieval even when Redis returns its standard JSON envelope; clearing stale compact-only metadata on non-headroom yields; and treating original user text as a reference inside generated step/recovery system instructions rather than repeating it as a fresh user-role request. Direct operator prompts continue to use the user role. No changes to plugin binaries or interfaces. Offline focused tests pass; not validated with live Ollama/Redis/PostgreSQL or Windows installer.

# Norm 0.55.4 — Compiled StegoSplit portable-source package

This archive is compatible with Norm Installer **1.6.12-unified**, with package schema 1, `package_type: portable-source`, the installer-owned `norm.exe` build (no legacy build script required), and a matching companion SHA-256 file. Norm's core is distributed as source to be built by the installer. The three StegoSplit plugins distribute their private implementations only as Windows CPython 3.14 x64 native `.pyd` modules; readable `src/main.py` adapters expose the stable Norm tool interface.

- `stegosplit_message` **0.4.3**: V3 paired-message carrier, paired SSH/SHA/MD5 identifier matching, and random 256-bit credential creation/unlock.
- `stegosplit_auth` **0.1.0**: distinct fixed-master/many-client signed image bearer authentication. Optional side plugin; disabled until enrolled/configured.
- `stegosplit_key` **0.2.0a1**: legacy paired key, rotation and cover reconstruction methods.
- The existing compiled V3 carrier engine remains **0.4.0** internally; its native module bytes were not modified.
- `package-manifest.json` and every plugin `plugin.json` carry the matching current versions and schema-2 hashes of the exact shipped source/native trees.
- `plugins/.registry.json` is runtime-generated and deliberately excluded. No private authentication Python implementation, real master image, private client images, SQLite authentication state, or configured secrets are included.

**Verification:** The exact 1.6.12 installer accepted the compiled ZIP in `--validate-only` mode. Windows CPython 3.14 smoke tests passed for Message paired secret unlock, Auth issuance/verify/revoke, Key imports, and 18 plugin identities. Full deployment and SSH-service integration remain untested/not implemented.

**Help:** `README.md` contains operator installation and user procedures. `plugins/stegosplit_message/README.md`, `plugins/stegosplit_auth/README.md`, and `plugins/stegosplit_key/README.md` explain interfaces for Norm; each plugin's `help.txt` summarizes user-facing operations. Complete historical development detail is preserved in `docs/RELEASE_NOTES.md`.

**Security note:** A signed client PNG is a copyable bearer credential. A filename used as a password is exposed with the file. The master issuer seed is in server-master PNG metadata, so secure the master and never publish operational credentials. Auth is not native OpenSSH.
