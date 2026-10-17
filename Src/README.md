# Norm — Revision History

Each entry describes the changes introduced by that revision relative to its recorded source baseline. The entries are historical, not a description of the currently installed system. Some builds follow different source branches; adjacent version numbers do not necessarily represent direct ancestry.

Detailed implementation notes and release-specific testing are recorded in [SOURCE_PACKAGE.md](SOURCE_PACKAGE.md), [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md), and the linked validation documents.

## 0.55.10

### 0.55.10r25 — Cockney voice compiler

- Strengthen voice-profile v0.4.3 Cockney anchor selection using dialect-aware, source-balanced examples.
- Prevent sanitized corporate phrasing and copying distinctive corpus lines into generated responses.
- Favor short, conversational replies while preserving r24 Nabler reporting.
- **Validation:** Source-only update; profile rebuilding and live style outcomes are separately verified. [Details](docs/R25_COCKNEY_VOICE_COMPILER.md).

### 0.55.10r24 — Nabler structured reporting

- Prefer Nabler for structured N2 intermediate and final output instead of plain-text tables.
- Retain underlying rows and the rendered report together in Redis for later N1 and console use.
- Display successful Nabler reports directly in runtime consoles, rather than raw tool JSON.
- **Validation:** Source-level tests; a full live N1/N2 reporting cycle has not been verified by this archival update. [Details](docs/R24_NABLER_STRUCTURED_REPORTS.md).

### 0.55.10r23 — Non-image ChatGPT attachments

- Recognize non-image file IDs from message parts, metadata, and nested structured payloads, alongside existing image references.
- Save original PDF, ZIP, DOCX, XLSX, and other attachment bytes within ZIP exports or in an accompanying PDF `_assets` folder.
- Record unresolved `sandbox:` links and rejected HTML/JSON download errors as missing assets, rather than reporting a complete export.
- Retain the 24 MiB image limit; increase individual non-image file limits to 256 MiB, with a 512 MiB total export budget.
- **Validation:** Offline regression tests passed; no live authenticated ChatGPT/Qt export test. [Details](docs/R23_CHATGPT_FILE_ATTACHMENTS.md).

### 0.55.10r22 — Leaner N2 step context

- Cap generic background-memory injection after the first planned N2 step at 8,000 characters by default.
- Filter intermediate-step verification to current-step evidence instead of reintroducing the full Redis task history.
- Preserve complete Redis/PostgreSQL evidence, prior-step pointers, original constraints, and full-task final verification.
- Add fresh-step context-size diagnostics without adding another summarization model call.
- **Validation:** Source tests passed; not deployed. [Details](docs/R22_STEP_BOUNDARY_CONTEXT.md).

### 0.55.10r21 — ChatGPT image and media export

- Resolve first-party ChatGPT file references and signed asset URLs; deduplicate repeated image references.
- Add a Qt-native authenticated download fallback when direct HTTPS transfer fails.
- Export actual asset bytes and distinguish history coverage from successful media retrieval.
- Refuse incomplete exports by default, allowing an explicitly selected partial export.
- **Validation:** Offline and isolated Qt transfer tests passed; signed-in end-to-end export remained unverified. [Details](docs/R21_CHATGPT_MEDIA_EXPORT.md).

### 0.55.10r11r2 — PostgreSQL JSON safety

- Normalize NUL, unpaired surrogate, and binary values only in database-bound JSON/JSONB copies, preserving original tool arguments and results.
- Centralize that policy in the existing `postgres_pool`; do not add connections or a process-global psycopg adapter.
- Pin `python-sql==1.8.1` and use its parameterized builder in new runtime-state read queries.
- Add consistent SQLSTATE diagnostics without introducing silent retries or changing transaction boundaries.
- **Validation:** Offline coverage passed; live PostgreSQL and Python 3.14/Windows integration remained outstanding.

### 0.55.10r11r1 — Export fallback and evidence persistence

- Try paginated ChatGPT conversation data before the legacy graph API and support additional visual-PDF scroll layouts.
- Escape PostgreSQL-incompatible characters in persisted task evidence without changing tool execution.
- Annotate evidence conversions so successful operations are not incorrectly retried because evidence insertion failed.
- **Recovery caution:** Check whether previously attempted file renames already occurred before resuming. Offline tested only.

### 0.55.10r11 — Cooperative Qt browser

- Introduce a left action rail, contextual **Ask Norm** prompt, and persistent domain-scoped JavaScript in the existing shared browser.
- Add selectable conversation export for user, assistant, media, and code content, with PDF, Markdown, JSON, and attachment ZIP formats.
- Preserve the browser profile/cache, Playwright separation, and previous terminal/control behavior.
- **Validation:** Source-only candidate; no authenticated KHzz browser acceptance test. [Details](docs/NORM_BROWSER_COOPERATION.md).

### 0.55.10r9 — Batch-verified memory condensation

- Freeze PostgreSQL source worklists into 20-memory destructive-maintenance batches.
- Require N1 approval of each keep/delete decision and successful reconstruction of important source context before deletion.
- Recheck source fingerprints and timestamps; commit retained records, deletion, and checkpoint together.
- Resume only unfinished batches after interruptions; prevent weekly condensation from repeating already approved work.
- **Validation:** Not executed against live KHzz PostgreSQL.

### 0.55.10r8 — Visual-PDF Qt callback repair

- Repair QtWebEngine structured JavaScript callback-result handling.
- Verify interactive scrolling, Qt screenshots, stitched PNG, and selectable-text PDF generation on a controlled KHzz page.
- Keep r7 N1 review and N2 self-verification unchanged; PyMuPDF remains an external runtime dependency.

### 0.55.10r7 — Independent N1 parent-step review

- Add independent N1 acceptance review for completed parent steps against a private pre-execution interpretation persisted in PostgreSQL.
- Keep the existing N2 self-verification path intact.

### 0.55.10r6 — Dialogue PDF formatting

- Consolidate text, visual, and rich-dialogue PDF formatting in the shared-browser plugin.
- Add `/pdf-chat` for dialogue PDFs retaining vector text, code, images, bookmarks, and citation links.
- Expose `/pdf`, `/pdf-visual`, `/pdf-name`, `/pdf-verify`, `/pdf-text`, `/pdf-status`, and dry-run `/pdf-cleanup` as direct local commands.
- Leave original documents and previously saved exports untouched. Source-only update to r4.

### 0.55.10r4 — Qt Focus workspace restored

- Restore the October 11 PySide6/Win32 Qt Focus workspace as the default, superseding the C++ workspace introduced in r2.
- Preserve the original at `tools/workspace/qt_focus_original_source.py`; run the adapted source through `tools/workspace/app.py`.
- Launch services without recursion through `start_roles.py` and `Run-Norm.bat --service-only`, attaching only missing terminal sessions.
- Retain three panes, focus controls, verified shutdown, reversible trash, and native font selection for new terminals.
- Keep the C++ workspace as reference; live ChatGPT validation of the inherited visual-PDF pipeline remained outstanding.

### 0.55.10r3 — Hybrid visual PDF

- Scroll toward oldest accessible history and capture overlapping screenshots downward, preserving the authenticated browser session.
- Build a hybrid PDF with selectable OCR words and image-preserved photos, code, math, diagrams, and tables.
- Save a stitched PNG; synthetic testing showed 61.9% smaller PDF output in one comparison.
- **Limitation:** Image segmentation and whole-history completeness were not guaranteed.

### 0.55.10r2 — Native Windows workspace

- Introduce a one-click Win32/C++ workspace attaching three terminal windows to the existing backend.
- Add `Run-Norm.bat --service-only` to prevent recursive service startup and Windows terminal font preferences.
- Preserve verified shutdown and soft-delete state; store native preferences at `%LOCALAPPDATA%/Norm/workspace/NormWorkspace-Native.json`.
- Retain the earlier PySide6 implementation for fallback. **The default workspace was subsequently changed in r4.**

## 0.55.9

### 0.55.9r12 — Browser visibility and recovery fixes

- Repair the Windows GUI helper's PowerShell brace-template error and Celery worker-ID routing during shutdown.
- Convert inconsistent structured-verifier `ACCEPT` results into actionable revisions; exclude cosmetic wording from recovery approval.
- Preserve SOS, Redis evidence, checkpoints, and temporary files after failed recovery handoff.
- Add `Show Shared Browser.cmd` to reveal an existing authenticated session; **Take Control** pauses Norm and **Return Control** gives Norm browser control without hiding the window.
- Require export coverage checks instead of treating a valid PDF file as proof of a complete conversation.

### 0.55.9r10 — Recovery and context garbage collection

- Accept independently verifiable plugin-produced artifacts during recovery.
- Assign recovery generation to N2 and final approval to N1.
- Stop injecting large Redis histories, old checkpoints, and previous lookup transcripts into every model step.
- Add bounded `read_task_record` access to earlier plan indexes, step results, context slices, and ledgers; N1 records receipts and blocks repeated identical lookups after the third request.
- Retain the original prompt and complete durable evidence without confusing artifact existence with export completeness.

### 0.55.9r9 — Shared browser and asynchronous workers

- Introduce a persistent Qt WebEngine browser with show/hide controls, HTML/DOM reading, BeautifulSoup/SoupSieve inspection, and JSONL/PDF exports.
- Keep Playwright in a separate plugin because direct Playwright-to-QtWebEngine CDP was incompatible; the Qt JavaScript bridge was tested.
- Request a 12 GiB JS heap ceiling, roughly 2 GiB browser HTTP cache, and 32 GiB archive budget without claiming full-history guarantees.
- Expand shutdown to drain Norm-owned Celery workers and PostgreSQL pools, close operator consoles/bridges, and avoid terminating unrelated Ollama sessions.
- **Historical:** Later source revisions removed Celery.

### 0.55.9r8 — Adaptive Celery completion checks

- Poll N1-approved asynchronous tools at 30 seconds, 60 seconds, 4, 7, 10, 13, and 15 minutes, followed by N1-adjusted intervals up to one hour.
- Send tool results to N1 before N2; avoid burning N2 reasoning rounds while waiting.
- Keep synchronous tools on their existing execution path. **Historical Celery design.**

### 0.55.9r7 — Redis evidence corroboration

- Add a task-local Redis record of observed tool returns alongside existing step evidence.
- Reconcile the failing parent's returns, executed steps, and recorded status before N1 plans only unfinished recovery work.
- Hand approved remaining work and the verbatim user prompt to recovery children, not the full parent ledger.
- Do not alter N1 authorization, normal checkpointing, or PostgreSQL task-history writes.

### 0.55.9r4 — Reversible folder deletion

- Extend Redis-backed reversible trash to entire folders and detect held historical-directory orphans.
- Allow `/soft-delete` from either operator console.
- Launch historical Celery subprocesses with `pythonw.exe` to avoid extra desktop windows.
- Keep the GUI bridge read-only at this stage; installer validation remained required.

### 0.55.9r3 — Frozen Celery dependency fix

- Explicitly collect Celery/Kombu runtime dependencies when building the frozen executable.
- Require `--self-test-celery` before installer promotion; reject failing candidates.
- Preserve r2's N1/N2 execution roles and Redis configuration. A real frozen executable was tested on KHzz.

### 0.55.9r2 — N1-approved asynchronous execution

- Introduce optional Celery execution for approved tools, using four configurable Windows solo workers and Redis DB4, with synchronous fallback.
- Keep N1 as gatekeeper, N2 as worker, and final responses outside Celery.
- **Status:** Source candidate for testing; this execution path was later removed.

### 0.55.9 — Public plugin identity correction

- Correct the `web_browser` manifest checksum without changing the plugin implementation.

## 0.55.8

### 0.55.8r10c — Reference and assignment separation

- Keep the original user request separate from child-step assignments inside `TaskPlan`.
- Have N2 execute only the current step while N1 checks evidence against the original constraints without restarting completed steps.

### 0.55.8r10b — Active-step-first execution

- Preserve the original request through internal N1/N2 handoffs without treating it as a command to restart the root task.
- Protect completed work while verifying only the active assignment.

### 0.55.8r10a — Original-prompt handoff

- Introduce `PromptJob.original_user_prompt` and durable-root restoration before N2 execution or delegation.
- Preserve the r10 portable-source baseline and existing PDF/web import corrections.

### 0.55.8r10 — Recovery contract

- Integrate the recovery objects boundary into verified r9 portable source.
- Retain corrected web imports and unchanged PDF-reader source; add failed-task console controls and retire redundant automatic injection.
- **Validation:** Source-only; no change to the live `C:\Norm` installation.

### 0.55.8r9 — N2 final-answer ownership

- Let N2 write and self-verify its final candidate with native voice/tools, subject to explicit N1 approval.
- Move terminal failures into dedicated recovery tasks with compact evidence context.
- Remove Celery from this lineage; use the `norm` Ollama model with its Qwen-based Modelfile.

### 0.55.8 continuity r6 — Original intent across step yields

- Preserve the original operator request in generated N2 handoffs while separating planning from active-step execution.
- Include r5 token-budget and Redis/Markdown overflow fixes; keep the no-tool N2 context at 16K.
- **Validation:** Source-only; not a rebuilt Windows executable.

### 0.55.8 continuity r4 — Compact handoff safeguards

- Load `completed_step_context()` independently of Redis `working_memory()` and cap injected PostgreSQL prior-step text at 36,000 characters.
- Clear stale compact-only markers after the immediate resumed slice, preserving durable history.
- Keep direct user prompts in the user role; route generated work, reviewers, and recovery children through typed internal payloads.
- **Validation:** Separate source-only patch with regression tests.

### 0.55.8 continuity/supervision update — Yield control

- Retain cumulative PostgreSQL context slices and N1 continuation instructions through successive 16-round step yields.
- Route formal plan review to N1 while retaining N2's bounded pre-submission check.
- Limit repeated identical file/directory and simple Git-log inspections; track repeated observations separately from new evidence.
- Add N1 stalled-step checks, splitting tasks only after a substantive decision.
- **Caveat:** The problem run used Laguna, not the canonical `norm` model, so the observed loop did not independently establish these changes as necessary or effective.

### 0.55.8 review-retry checkpoint update — Durable N1 review

- Archive original constraints, acceptance criteria, unchanged N2 candidate, observed evidence, and completed progress in PostgreSQL before retrying N1.
- Rebuild the review request from that persisted slice rather than asking N2 to rewrite an accepted candidate.
- Distinguish checkpoint failures from approval; after two processing failures, use `n1_step_review_status=unavailable_after_2_attempts` if pass-through is permitted.
- Separate output budgets for task-progress review, step alignment, and command classification. Hard crashes still use the existing queue recovery path.

### 0.55.8 — Bounded review, queue visibility, idle detection

- Bound N1 step alignment to a 6,000-token default, 10,000-token ceiling, and 300-second timeout without changing the model context window.
- Retry N1 once against the same durable N2 candidate after processing failure; preserve actual N1 rejections and targeted repairs.
- Expand `/queue-full` and `/status/queue-full` across GUI ingress, Redis, and PostgreSQL, including retries, dead letters, failed, and suppressed tasks.
- Improve CPU, GPU-compute, GPU-memory-controller, and service/queue-based idle detection.
- **Validation:** Isolated source tests only; no complete Windows/Redis/PostgreSQL/Ollama validation.

## 0.55.7

### 0.55.7 — Invocation provenance

- Add `invoke_source` and `invoke_target` fields to typed work requests; reserve `user` for actual operator ingress.
- Attribute N2 results and final candidates as N2-to-N1 through checkpoint/resume.
- Add compact provenance headers to planning, review, repair, and loop intervention without changing the original-user constraints.
- Preserve the existing review logic, voice profile, and compiled StegoSplit plugin identities.

## 0.55.6

### 0.55.6 — N2 plan self-audit and N1 review

- Add a bounded N2 self-audit for missing prerequisites, sequencing, inefficient work, verification gaps, and suitable step splits.
- Permit one targeted plan rewrite only when the audit identifies an actionable deficiency; do not rewrite sound plans for style.
- Record audit outcomes, issues, and confidence. A `no improvement required` verdict below 0.50 triggers another scrutiny pass rather than unconditional acceptance.
- Have N1 independently check submitted steps against original intent, acceptance criteria, and evidence.
- Apply one validated voice profile across N1, N2, and structured planning without overriding safety or schemas.
- Preserve first-satisfactory-answer behavior, plugin binaries, and installer identity. This combines two overlapping 0.55.6 entries in the prior README.

## 0.55.5

### 0.55.5 — Preserve first satisfactory response

- Remove unconditional free-form regeneration after N2's first answer on both tool and no-tool paths.
- Keep accepted answers verbatim following structured verification; permit targeted revision only for identified defects.
- Preserve the independent step/final verifiers and N1 acceptance gate.
- **Validation:** Local Python checks only; prebuilt Installer 1.6.12 still had an older embedded fallback.

## 0.55.4

### 0.55.4 — Voice/provenance correction and compiled plugins

- Restore the active voice profile as a dedicated system instruction for thinking, review, and tool rounds; stop misclassifying internal N1/N2 requests as fresh user prompts.
- Rebase on the 0.55.3 CLEAN compiled portable source while preserving exact StegoSplit binaries and plugin manifest identities.
- Ship portable Python 3.14 x64 core source alongside readable StegoSplit adapters and compiled native `.pyd` modules:
  - **Message 0.4.3:** Lossless paired PNG shares, credential recovery, and paired identifier operations.
  - **Auth 0.1.0:** Local signed client-PNG credentials, expiry/revocation, optional passwords, and administration; no network service or SSH authentication.
  - **Key 0.2.0a1:** Paired-image key recovery, reconstruction, and password rotation.
- Keep proprietary implementation source and credential images outside the public package.
- **Security limits:** PNGs must remain lossless; filename-derived passwords and unprotected bearer credential images are not reliable independent access controls.
- **Packaging at the time:** Installer 1.6.12 could use adjacent portable source, but its previously compiled EXE contained an older fallback. Plugin smoke/identity checks passed; full installer-upgrade tests and cryptographic audit were not claimed.

---

Historical worker, UI, and installer behavior may have been superseded by later revisions. Consult [SOURCE_PACKAGE.md](SOURCE_PACKAGE.md) for the specific package baseline and [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md) for detailed validation.
