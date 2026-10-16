## 0.55.10r11r2 — Shared PostgreSQL boundary and python-sql

- All Norm-owned PostgreSQL connections through `postgres_pool` use one per-connection JSON/JSONB serialization policy (`configure` callback). No process-global psycopg adapter, extra pool, new database, or new connection credentials are introduced.
- `pg_json_safety` normalizes database-bound JSON/JSONB only: NUL and unpaired surrogates are literal-escaped, binary is represented as Base64, and changes are logged with metadata rather than content. The executed tool parameters/results remain unmodified; `record_evidence` continues storing explicit per-row persistence notices. Non-JSON text and arbitrary SQL arguments are deliberately *not* silently changed.
- `python-sql==1.8.1` is pinned in both `core/requirements.txt` and the installer-facing `tools/requirements-lock.txt`; new runtime-state read queries use its parameterized builder. Existing PostgreSQL-specific DDL and static SQL remain psycopg 3 statements. Context/shutdown snapshot queries use quoted schema identifiers.
- The common pool now classifies/logs SQLSTATE failures consistently without changing commit/rollback boundaries or automatically retrying operations. N1/N2 recovery logic, pool sizing, plugin interfaces, credentials, and installer/executable workflow are preserved. No executable recompile required.
- Offline tests verify JSON adaptation, audit behavior, pool registration, query-builder wiring and browser regressions. **A full live PostgreSQL test and Python 3.14 dependency-install/Windows validation have not been performed**; this package is a test candidate.

## 0.55.10r11r1 — Export compatibility + PostgreSQL evidence recovery

Source-only correction to r11. ChatGPT export now tries paginated conversation data before the legacy graph API, while the existing visual PDF capture searches more than one page/scroll layout. PostgreSQL `task_evidence` inserts now normalize NUL/unpaired-surrogate/binary values **only in the DB-bound evidence copy**, annotate any changes, and leave the executed commands and live tool results unmodified. This prevents otherwise successful file operations from entering retry solely because their result contained PostgreSQL-incompatible Unicode. The user must still verify whether previously attempted renames already happened before retrying. Tests are offline; no live Windows or PostgreSQL integration test has occurred.

## 0.55.10r11 — Norm browser cooperation candidate

Adds a small left action rail, bottom contextual **Ask Norm** prompt, persistent domain-scoped JavaScript and a selectable **convo_export** extension to the existing Qt shared browser. Selection supports user/assistant/media/code categories and local PDF, Markdown, JSON and attachment ZIP output. Existing cache/profile, Playwright, MSS, terminal launcher and control semantics remain. See [implementation/limitations](docs/NORM_BROWSER_COOPERATION.md). This release is source-only, offline-tested, **not yet live-validated on KHzz**.

# Norm 0.55.10r9 — batch-verified condensation

The full destructive history-maintenance pass uses frozen PostgreSQL worklists, **20 source memories per validation/commit/deletion checkpoint**. N1 must approve each keep/destroy; surviving memories must recreate their important prior context without raw source history. If any retained record fails reconstruction, nothing from that batch is deleted. Source fingerprints/timestamps are checked before pruning, with the verified output, deletion and PostgreSQL checkbox in one transaction. Failed/rebooted runs resume the exact unfinished batch. The regular/weekly condensation checkpoint also freezes its source, preventing changed background-history fingerprints from repeating approved batches. This remains a source-only package; it has NOT been installed or run against KHzz live PostgreSQL.

Norm 0.55.10r8 staged source: Fix QtWebEngine structured JavaScript callback return values in Visual PDF capture. Captured scrolling and real Qt screenshots were verified in an interactive KHzz test; the stitched PNG and selectable-text PDF were verified on a controlled page. No modifications to N2 self-check or N1 review from r7. PyMuPDF is a runtime dependency, not part of this portable source ZIP.

Norm 0.55.10r7 staged source: N2 self-verification unchanged; N1 independently reviews completed parent steps with private pre-execution interpretation persisted in PostgreSQL.

The shared-browser PDF pipeline now formats `/pdf-chat` output as a more readable chat transcript while preserving native vector text, code blocks, images, bookmarks and citation links.

## Norm 0.55.10r6 — Chat-style rich dialogue PDF formatting

The existing Shared Browser plugin now owns text, visual and rich-dialogue PDFs.
Use `/pdf` in Norm Prompt to list shortcuts, `/pdf-chat "original.pdf" --name "Topic"`
for a source-faithful bookmarked dialogue, or `/pdf-visual` to capture the open tab.
`/pdf-name`, `/pdf-verify`, `/pdf-text`, `/pdf-status`, and dry-run `/pdf-cleanup`
are also available. Direct commands run locally without N2 queuing.
Original source documents and previously saved exports are never overwritten.
This is a source-only update of the uploaded 0.55.10r4 package.

## Norm 0.55.10r4 — Qt Focus restored as the default

The default workspace is **the original October 11 NormWorkspace-Qt-Focus**, written in PySide6 and using Win32 to host real terminal HWNDs. The original app.py is preserved as tools/workspace/qt_focus_original_source.py; the modified production source is tools/workspace/app.py.

**Run-Norm.bat** opens Qt Focus immediately. Qt Focus starts Norm using tools/workspace/start_roles.py, which calls Run-Norm.bat --service-only, then starts only missing operator sessions. The three existing terminal panes and focus handling remain intact. **Quit Norm** runs the existing verified shutdown procedure, preserving reversible trash. The new Fonts button uses a native Qt dialog; role font settings apply to newly created terminals.

The older C++ workspace code is kept only as reference and is **not** the default. r3 Visual PDF hybrid and OCR are retained, but have not been tested against a live authenticated ChatGPT conversation.

## Visual PDF (0.55.10r3)

Visual PDF now scrolls toward the oldest history, samples moving loading icons, then repeatedly scrolls and captures overlapping screenshots downward. The final PDF is HYBRID: normal words become visible, selectable OCR text; photos, code, math, diagrams and tables remain pixel-preserved image cutouts. **No full-page screenshot with another text layer on top.** A standalone stitched PNG is saved for reference. A synthetic comparison showed a 61.9% smaller hybrid PDF; actual results vary. Unknown images are not guaranteed to be separable from OCR, and live ChatGPT history completeness has not been verified. The authenticated browser never needs a reload or hidden window for capture.

## Native one-click Norm workspace (0.55.10r2)

Normal Run-Norm.bat now starts the native Win32/C++ workspace immediately.
The workspace launches the existing Norm backend/roles in the background and attaches three native terminal windows. Run-Norm.bat --service-only remains the nonrecursive backend startup.
The native Fonts button uses Windows font selection; changes to terminal fonts take effect on new operator windows. Quit Norm uses verified shutdown and preserves the soft-delete bin. Native preferences persist in %LOCALAPPDATA%/Norm/workspace/NormWorkspace-Native.json.
Use tools/workspace/native/NormWorkspace.exe --root C:\Norm to start directly. The previous PySide6 UI remains in source for rollback only.
Visual PDF was hardened to preserve the browser session and avoid the fragile bottom-first scan; the live ChatGPT conversation export still needs user validation.

# Norm 0.55.9r12 - Shared browser visibility and control

This revised r11 also fixes the Windows Desktop GUI helper PowerShell
brace-template error and Celery --id worker routing during graceful
shutdown. Both /stop-all and /stop-all now retain their common cleanup
path. Reinstall the refreshed r11 ZIP if using the earlier r11 build.


r11 also repairs two traced verifier/recovery failures: inconsistent
structured verifier ACCEPT results become actionable REVISE results,
and the N1 recovery approval prompt excludes mere sentence polishing.
A failed recovery handoff retains SOS, Redis evidence, checkpoints and
temporary files rather than purging them. N1 approval stays mandatory.


To reveal the hidden browser from Windows Explorer, double-click
C:\Norm\Show Shared Browser.cmd after installation. It reuses the
existing Chromium process; or ask Norm to run browser_shared_show().

Show and Hide affect only window visibility. Take Control pauses Norm so
you can use the browser manually. Return Control to Norm leaves the
window visible so you can watch Norm navigate. Closing the window hides
it without discarding cookies, page state or the control owner.

The browser uses the configured Documents workspace for its profile/cache
and JSONL/PDF exports, refuses empty or mixed-conversation PDFs, and
does not claim entire-chat completeness from a valid PDF file alone.

Includes r10 recovery/context-GC and r9 Celery/shutdown. See docs.

# Norm 0.55.9r10 ? Recovery and context GC corrections

All r9 shared Chromium browser, Celery/N1 adaptive waits, and safe-shutdown
changes remain. r10 repairs rigid artifact verification for independently
verifiable plugin-produced files, assigns recovery generation to N2 and
approval to N1, and prevents massive Redis task history/older checkpoints
from being carried into the next model step.

When a following step needs an older detail, N2 can request it via
read_task_record(scope=current, kind=plan_index/step_result/context_slice/
result_ledger, step_id=..., slice_index=..., message_index=..., start_char=...).
The history stays in its original storage, and only the requested bounded
record window is temporarily presented. N1 retains the lookup receipt and
blocks repeated identical lookups after a third request. Previous step
summaries and old lookup transcripts are NOT automatically reinjected.

The original user prompt remains verbatim. Full evidence stays in Redis/
PostgreSQL, retrievable by reference. Hash-verified PDF file existence
does not guarantee long-chat export completeness. See RELEASE_NOTES.md.

# Norm 0.55.9r9 - Shared Browser + Norm-local Celery workers

r9 includes the r8 asynchronous N1/Celery check improvements and adds a
**working Qt WebEngine shared browser prototype**. Start it in your logged-in
Windows session from tools/shared_browser/Start Hidden Browser.cmd, or open it
visibly using Launch Browser.cmd. Norm exposes N1-approved browser tools to
show it for your login/input, read DOM/HTML with CSS and BeautifulSoup/
SoupSieve, and export loaded conversation turns to JSONL and searchable PDF.
The page and login remain the same session when hidden/shown.

The browser requests a 12 GiB JS heap ceiling, supports the largest Qt
Chromium disk HTTP cache accepted by this build (about 2 GiB), and budgets
32 GiB for persistent conversation archives. Render only needed content;
extra memory cannot guarantee very long chats won't crash.

Playwright remains in the separate existing web_browser plugin; direct
Playwright-to-QtWebEngine CDP is INCOMPATIBLE with this Chromium build.
Qt's own JavaScript/DOM bridge works and was tested. Whole-conversation
completeness isn't yet guaranteed, and the PDF result clearly says so.

r9 complete shutdown: Safe-Shutdown-Norm.bat now waits until norm.exe
exits, ensures Norm-owned Celery processes are closed, releases the main
and worker psycopg_pool connections, signals the three operator consoles,
and stops the optional browser/GUI bridges. Ordinary shutdown closes a
Norm-owned Ollama server but does not terminate an unrelated Ollama instance.
The BAT refuses to force-kill a service still performing work.

Celery now validates it uses the Norm installation's own .venv and stops
Norm-owned workers when the service exits after N2 checkpointing. See
docs/SHARED_BROWSER.md and docs/RELEASE_NOTES.md.

# Norm 0.55.9r8 - separate asynchronous tool queue and adaptive N1 completion checks

N1-approved Celery informational tools now use Redis-delayed checks at 30s,
60s, 4m, 7m, 10m, 13m, 15m. From 15m, N1 estimates each subsequent check
time (reviewing again if the operation remains pending), up to a strict
one-hour deadline from the original dispatch. Sync tools retain the old path.
N1 reviews tool results before N2 sees them. No user interaction is needed
during waits, and N2 does not burn model rounds merely to poll. queue-full
shows waiting Celery steps. See docs/RELEASE_NOTES.md.

# Norm 0.55.9r7 - Redis corroboration for step and recovery continuity

r7 adds a compact, task-local Redis record of actually observed tool returns
alongside the existing step evidence stream. N2 can consult returned paths and
values without rediscovering them, and the **failing parent** reconciles those
returns with the Redis step-execution list and recorded status before N1 builds
a new plan containing only the remaining work. A recovery successor gets the
approved work list and the verbatim original prompt, never the parent's ledger.
N1 tool authorization, normal checkpointing, stop/resume semantics, and existing
database task-history writes are not redesigned. The supplemental ledger adds
no PostgreSQL writes. See docs/RELEASE_NOTES.md for limits and tests.

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

> **0.55.8r9:** N2 writes and self-verifies final responses with native voice/tools; N1 explicitly approves. Celery removed. Terminal failures hand off to a dedicated recovery task with compact context. See `docs/RELEASE_NOTES.md`. Runtime Ollama model: `norm` (Qwen is its underlying Modelfile base).

> **0.55.8 continuity r6 source-only:** Preserves the original user request as an Ollama `user` message on generated N2 steps, while explicitly separating planning from **active-step execution** (no re-planning or redoing completed work). Includes all r5 token-budget and Redis/Markdown output-overflow changes. N2 without tools remains at 16k. See `docs/RELEASE_NOTES.md`. This ZIP is not a rebuilt Windows executable.

### 0.55.8 continuity r4 — protect task handoffs (2026-10-09; source-only)

- **Verified earlier steps:** Load PostgreSQL `completed_step_context()` independently of the Redis `working_memory()` JSON envelope. Limit injected PostgreSQL prior-step text to 36,000 characters; preserve the full durable records.
- **Non-sticky compaction:** A context-headroom distilled checkpoint is used for its immediate resumed slice; the next ordinary yield clears stale compact-only metadata. Legacy headroom markers and explicit manual-failure compact resumes are still supported.
- **Step scope:** Only direct operator prompt jobs send the original prompt in Ollama's user role. Generated steps, reviewer work, and recovery children retain the original user request inside the typed system work payload for semantic fidelity, without presenting a four-hour-old request as a new user command on every requeue. The current step/verification/continuation is explicitly authoritative for immediate execution.
- No changes to tool schemas, batching limits, plugin or slash-command wiring, model selection, installer, private code, or database schema. This branch is **not** installed or live-validated. It is built upon the separate 0.55.8 continuity r3 source patch.
- Regression tests: `tools/test_continuity_handoff_0558_r4.py` and extended provenance-role tests.

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

# Norm 0.55.4 — Installer-ready portable source

Norm is a Windows local Ollama/Redis/PostgreSQL agent runtime with persistent task queues, operator controls, history maintenance, and native plugins. The core Norm code remains portable Python source; StegoSplit internals are compiled.

## Install or upgrade

Use the working **Norm Installer 1.6.12** with **Norm-0.55.3-portable-source.zip** alongside its matching `.sha256` file. The installer reads `package-manifest.json` (package schema 1, type `portable-source`), installs/manages the source tree, and builds `core/norm.exe` itself if compilation is selected. This ZIP is not a prebuilt Norm EXE. Package discovery and non-destructive installer validation pass; a complete live installation/upgrade has not been performed.

## Compiled StegoSplit plugins

| Plugin | Version | Native code | Public adapter |
|---|---|---|---|
| StegoSplit Message | 0.4.3 | 12 Windows CPython 3.14 x64 .pyd modules, including V3, paired identifiers, and 256-bit credentials | `plugins/stegosplit_message/src/main.py` |
| StegoSplit Auth | 0.1.0 | 11 Windows CPython 3.14 x64 .pyd modules for fixed-master signed client PNG authentication and local GUI/admin utilities | `plugins/stegosplit_auth/src/main.py` |
| StegoSplit Key | 0.2.0a1 | 3 Windows CPython 3.14 x64 .pyd modules | `plugins/stegosplit_key/src/main.py` |

All proprietary StegoSplit implementation modules are compiled. Their corresponding Python implementations are **not** included. The adapter files remain human-readable so Norm can discover its tool endpoint names, argument types, and return values. Each plugin has a public README and help.txt. The private developer source remains separately available and must not be committed to a public repository.

### How to use

**Message:** Create two lossless PNG shares from a cover image and a password. Both images and the password are required to decode. `create_unlock_pair` writes a randomized 256-bit application credential across the two PNGs; `unlock_image_pair` authenticates/recovers it in one call. `embed_identity_pair` and `unlock_identity_pair` handle paired SSH public-key / SHA-256 / MD5 identifier matching. These are not directly OpenSSH authentication.

**Auth:** A locally trusted administrator generates a private master PNG and issues individually signed client PNG bearer credentials. Present one client PNG to the server-side verification adapter to check it against the master, permissions, expiration, revocation, and optional password. It supports five failed attempts in five minutes followed by administrator unlock. Its TXT-filtered file chooser can accept a manually typed PNG filename. The plugin is disabled by default until locally configured; no network listener, remote upload endpoint, or OpenSSH authentication module is supplied.

**Key:** Existing paired-image key recovery, cover reconstruction, and password rotation remain available through the compiled key engine.

### Security and compatibility

Lossless PNG is required. Filename-derived passwords expose their secret to anyone who obtains the filename; a copy of an unprotected client PNG is a transferable bearer credential. The master issuer seed is stored in protected PNG metadata; its secrecy depends on local filesystem permissions. Never commit actual credential images, authentication SQLite databases, `.env` files, or issuer key material.

The 1.6.12 package validator accepts this ZIP, and the compiled StegoSplit adapter smoke tests and all 18 bundled plugin identity checks passed on KHzz. No full installer-upgrade test or adversarial cryptographic audit has been performed.

See `docs/README.md`, `docs/CURRENT_STATUS.md`, `docs/RELEASE_NOTES.md`, `SOURCE_PACKAGE.md`, and the individual plugin README/help files.
