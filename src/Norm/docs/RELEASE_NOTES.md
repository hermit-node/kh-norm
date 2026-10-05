# Release Notes

## 0.53.16 — 2026-10-04 — Maintenance reliability, hierarchical memory condensation, complete console teardown, and bundled WeasyPrint

- Deep-history reconstruction is isolated per sampled compact memory. Each replay gets up to **4,800 output tokens per continuation segment × 4 segments**; no neighboring compact memory, previous replay, or tool call can help the sample pass. Replay text is disposable and is not persisted as memory.
- Failed compact-memory validation may repair only that record from its own authoritative task source, then re-run the isolated reconstruction. The repaired record is the one persisted; a failed retry preserves trusted/raw state.
- /memory-condense is explicitly recent-only, using live-configurable memory.regular_memory_window_days (default **14 days**). Manual force means “run now,” not “scan all history,” and recent mode does not globally prune old conversation history.
- Scheduled memory maintenance now alternates successful **regular -> full -> regular -> full** passes on memory.scheduled_memory_interval_days (default **7 days**). Failed/interrupted scheduled work resumes the same recorded mode; alternation advances only after success. Automatic deep scheduling was removed.
- Added manual /memory-condense -deep for bounded older raw-history compaction/validation without hierarchical merging.
- Scheduled and manual /memory-condense -full sweep the complete date-ordered historical archive. Validation is partitioned into compact-row batches via deep_history_full_batch_rows (default **200**) and samples deep_history_full_samples_per_batch (default **12**) isolated rows from each batch. After QA passes, one hierarchical merge level considers chronologically neighboring windows of up to deep_history_full_merge_max_records (default **6**). A window may remain unchanged or reduce to 1..N validated replacement rows; repeated full passes can therefore progressively condense prior merged outputs.
- Within each neighboring merge window, full mode may leave unrelated rows separate or consolidate genuinely related/redundant subsets into **1..N** surviving records instead of forcing N-to-1. Every source memory must appear exactly once in the proposed partition; singletons stay unchanged; actual merged groups retain full provenance.
- Merged records must preserve reconstructable task/request, meaningful steps or itinerary, tools used, results/current state, artifact pointers, efficiency/failure lessons, reusable constraints/fixes, and concise improvement notes. Each constituent is replay-tested from the merged record alone. A failed merge falls back to its original constituent compact memories.
- Background-condensation checkpoints use unique PID/UUID temp files, flush + fsync, six bounded Windows atomic-replace retries, and a final durable in-place rewrite fallback for persistent destination-sharing denial.
- /stop-all now signals exact detached Norm operator-console hosts after the SOS snapshot is verified. Non-prompt operator windows auto-close after about **5 seconds**; Norm Prompt remains about **10 seconds**; Enter or Ctrl+C closes a countdown immediately. Generic python.exe processes are never mass-killed.
- Bundled the official **WeasyPrint 70.0** Windows onedir runtime under tools\weasyprint, including the UCRT64 native stack with **Pango 1.58.2**. No target-machine MSYS2/Pango compilation is required.
- Installer **1.6.7-unified** validates the bundled WeasyPrint runtime with --info and a real HTML-to-PDF render before installation reports success.
- Current-state documentation was reconciled against 0.53.16 source/config. README.md, CURRENT_STATUS.md, DEVELOPMENT_NOTES.md, MAINTENANCE_VERIFICATION.md, FUTURE_IMPLEMENTATION_NOTES.md, and SOURCE_PACKAGE.md contain current state/current contracts/current evidence/active backlog only; RELEASE_NOTES.md remains the sole maintained historical ledger.
## 0.53.15 — 2026-10-04 — Canonical path-policy and internal state root
- Added one canonical `state_root` setting under `[paths]`; weekly condensation checkpoints, deletion trash/file backups, voice-profile state, GUI fallback state, emergency snapshots, and full-backup state now resolve through it instead of separately spelling `runtime_root/state`.
- Added `@state` plus `internal_directories` to the central file-access policy. `@state` is internal-only by default and is not exposed to N2/native/plugin file tools.
- Core file tools and built-in file plugins now share the same `authorize_path()` boundary. Outside-root access is always rejected; the enforcement flags now only select normal-vs-HARDLOCK messaging.
- Added `[file_access_overrides]` additive per-capability roots such as `file_read.read_add = @state` without widening every tool.

## 0.53.14 — 2026-10-04 — N1/N2 tool-gate checkpoint 1

- Added a two-role runtime boundary without changing user-facing routing yet: N1 is a gatekeeper/supervisor and N2 remains the existing reasoning/worker path. User input and N2 answers continue through the current conversation flow without N1 rewriting them.
- `run norm` now initializes both logical agent clients. Both default to the existing `norm` Ollama model/endpoint for checkpoint 1, while `runtime.json:agents.n1/n2` provides separate role configuration for later model/host separation.
- Removed N2 ownership of `verification_preflight`, `verification_history`, and `verification_checkin` from the native tool loop. N2 now proposes the actual information tool with `n1_need` and `n1_target`; N1 alone checks the live Redis verification pool, decides reuse versus fresh execution, and records fresh evidence.
- Fresh tool execution results are passed to N2 unchanged. N1 verification/check-in metadata is recorded out-of-band; Redis reuse returns the already-verified fact instead of invoking the tool. Exact repeated requests within the same task/step reuse the prior raw result.
- Added a first loop/rabbit-hole guard: exact repeated N2 tool requests are suppressed/reused, while consecutive similar N2 reasoning/tool turns are passively observed and escalated to N1 for a loop judgment. A judged rabbit hole is stopped before that turn's proposed tools execute, then N2 receives a reset instruction to continue from established evidence using a materially different next action.
- Deterministic runtime read-back verification after writes remains a core safety invariant and is not routed through model approval because it is not an N2-requested information call.

## 0.53.13 — 2026-10-03 — runtime summary, prompt fidelity, and compact validation pool

- Retained the end-of-task durable summary instead of suppressing/removing it: structured generation stays silent, then one complete reconciled summary block is published after persistence.
- Replaced newline/1024-character runtime display buffering with immediate publication of upstream Ollama answer/thinking fragments. This is independent of the 14,000-character maintenance batch limit, which remains unchanged.
- Extended the Ollama degeneration guard to catch long coherent phrase loops (repeated sentence/paragraph blocks), not only low-entropy token loops; this covers the observed repeated-thought failure where normal-vocabulary prose could repeat indefinitely.
- Kept the 0.53.12 current-state reconciliation fix: summary refresh uses the coverage cursor, active current memories outrank stale prior text, and double model failure writes a deterministic current-state fallback rather than freezing old state.
- Hardened current-state reconciliation against the live monotonic-growth failure: summary replacements containing `Superseded information`, embedded `Recent messages`, or conversation-log sections are rejected and retried; a second invalid result advances to the deterministic current-state fallback. The thread summary row may shrink and is not required to preserve prior text.
- Tightened prompt reduction: model-derived intent/details remain useful, but an ordinary complete task sentence cannot be removed just because it was classified as durable memory. Only exact explicit-aside spans (plus non-task parentheticals) are eligible for removal from executable wording.
- `vision_parse` 0.2.1 retains the ten-page call limit and adds 1.5x/2.2x/2.9x adaptive render tiers: easy clean pages use 1.5x, moderately dense clean pages use 2.2x, and suspect/dense/split pages use 2.9x.
- Prompt interpretation remains enabled for intent, command classification, durable memories, corrections, preferences, and constraints. Executable text is now conservative: only exact, structurally separable sidecar spans may be removed; embedded task qualifiers such as `end of task summary` stay verbatim.
- Added regressions for conservative prompt reduction, immediate stream publication, complete durable-summary events, stale-summary fallback, and the unchanged `consolidation_batch_chars=14000` boundary.
- Documentation split was corrected: `CURRENT_STATUS.md`, the main README, and `SOURCE_PACKAGE.md` now describe only current behavior; historical implementation facts remain in `RELEASE_NOTES.md`. Vision history is recorded under the versions that actually shipped it: 0.53.8 = 4 pages/2.5x, 0.53.12 = 0.2.0 with 10 pages and 2.2x/2.9x, 0.53.13 = 0.2.1 with 1.5x/2.2x/2.9x.
- Reworked the live verification pool to the intended compact architecture: Redis now uses one `norm:validation:pool` hash instead of `facts` + `recent` + per-subject observation zsets/pending keys. Preflight reads Redis first and only then lets Norm choose reuse, optional PostgreSQL history, or a fresh information tool; check-in uses target-first candidate matching so minor wording changes cannot proliferate near-duplicate facts. Same-value checks increment one count, changed values reset to 1 with previous value/change time, and twice-daily migration merges eligible >24h hot records into PostgreSQL before deleting the Redis field. PostgreSQL verification retention is now about 14 days.
- Promoted the installer wrapper to **1.6.6-unified** and simplified release packaging to `Norm Installer 1.6.6.zip`; redundant outer audit/checkpoint/change-note copies were removed because authoritative project documentation already ships inside the portable source.

## 0.53.12 runtime-summary reconciliation hotfix — 2026-10-03

- Replaced the 0.53.9 rolling-summary failure mode that could preserve an old branch summary indefinitely after two output-budget hits. Summary refresh now advances from the last successfully covered message cursor instead of re-reading only the last 20 messages.
- Runtime summary semantics are current-state projection, not a supersession timeline: newer versions/values/statuses remove older ones unless the historical transition is itself operationally relevant. Completed/resolved work and stale unresolved state are pruned.
- Summary maintenance now consumes newly extracted active memories before rebuilding runtime state. Internal summary/memory JSON generation is silent and no longer dumps maintenance JSON into the normal runtime activity stream.
- If both model rebuild attempts fail, Norm advances to a deterministic current-state fallback from active durable memories plus the latest verified assistant results instead of freezing the stale prior summary.
- Turn interpretation may still classify sidecar durable details, but it may not rewrite the executable user request; the full original user wording remains authoritative.
- This hotfix does not change `memory.consolidation_batch_chars`; the unrelated maintenance batching value remains 14000.

## 0.53.12 verification/archive maintenance — 2026-10-03

- Replaced the optional/manual validation bookkeeping path with a mandatory live Redis verification protocol around information-gathering tools: preflight Redis check, explicit PostgreSQL-history yes/no decision, one evidence tool, then mandatory Redis check-in before any next evidence tool or final answer.
- Semantic identity decisions remain model-owned through stable key + who/what/where/how/why; Python only stores/compares the canonical key/value selected by Norm.
- Same-key/same-value observations increment the current generation; changed values immediately become the live Redis value with count 1. Timestamped old->new transitions remain pending in Redis until optional PostgreSQL history accepts them.
- Added twice-daily migration of Redis verification observations older than 24 hours to PostgreSQL historical batches. PostgreSQL counts are explicitly historical and are not compared to live Redis counts as contradictions. Weekly maintenance purges verification-history/change rows older than seven days.
- Added archive-aware `file_read` through a Python intermediary that prefers local 7-Zip and falls back to Python ZIP/TAR readers. Archive inspection is non-extracting and follows tree/sizes -> SHA-256 -> selective member read escalation.
- Bundled 7-Zip 26.03 x64 under `tools\\7zip` and changed archive resolution to prefer that package-managed copy after an explicit `NORM_7ZIP` override, so a target machine no longer needs a system 7-Zip installation.
- Made DB3 `prompt_id` a durable idempotency key. Any uncertain/redelivered submission first resolves the original root task by `source_prompt_id`; running work waits on that same task and terminal work replays its durable result without routing, inserting another user turn, or creating another `chat-*` task. Failed/cancelled terminal tasks also return their durable summary to the synchronous owner instead of throwing after finalization.
- Fixed the first-party `postgres_pool` tool adapter. It now resolves `runtime_bootstrap.build_postgres_pool()` instead of importing a second `_pool` module, so model-facing PostgreSQL tools borrow the exact same process-global pool object already used by the runtime. Added `postgres_query` as a bounded read-only query surface for the approved `norm`/`stocks` logical connections and structured `postgres_execute` for INSERT/UPDATE/DELETE only inside Norm's configured runtime schema through the shared `norm` connection. Full backup helpers resolve approved connection parameters without opening a pointless child-process psycopg pool before `pg_dump`; no host/user/password/arbitrary database parameters or direct `psycopg.connect()` path are exposed to the model.
- Repointed manual `/memory-condense` to the replay-validated deep-history pipeline. Manual normal mode bypasses the scheduled age/interval gate, compacts a bounded batch of new terminal task history, and replay-validates every compact record from that pass. `-full` refreshes all surviving compact/raw history newest-to-oldest in batches of up to 200 source tasks; older records receive relevant newer compact state so stale/superseded lessons can be corrected or reduced to historical context, and up to 12 stratified records per full batch are replay-checked. Both retain the SQL-backup + fail-closed replay gate before covered raw history can be removed or refreshed compact history committed. Validated compact history remains as individual searchable `norm_runtime.task_history` rows with no aggregate character cap; deep-history completion no longer forces the entire archive through one 12,000-character global merge.
- Removed the planner's fixed batch-of-eight guidance; repeated work now batches adaptively and file/archive work prefers machine-readable manifest/hash comparison first.
- Emergency `/stop-all-now` now closes helper PostgreSQL pools before process teardown and allows a brief bounded `norm.exe` shutdown window before the existing force-kill fallback.

## 0.53.12 control-race maintenance — suppress/flush resilience

- Fixed a race where `/suppress-task` could return before the active Ollama generation had fully unwound, then `/flush-suppressed` could delete the durable task and a second `/suppress-task` could hit a deleted task and surface as HTTP 503.
- The active suppression transition marker now survives until the worker acknowledges cancellation; duplicate suppress requests during that window are idempotent.
- Prompt control HTTP failures are displayed without terminating the Prompt console.
- The Prompt console now closes its one-shot PostgreSQL pool after recovering the latest turn, preventing `psycopg_pool` finalizer warnings on helper shutdown.

# Norm release notes

## Coverage note

The retained project history documents the formal release line from **0.51.0 through 0.53.9**, plus the reconstructed 0.53.11 baseline and the current 0.53.15 release. No standalone 0.53.0 or 0.53.10 release artifact/section was found in the retained source history, so this file does not invent changes for those version numbers. Same-version hotfixes remain under the version they actually modified.

## 0.53.12 — 2026-10-02 — voice profile, startup compatibility, and operator-console recovery

- Added first-party schema-2 `voice_profile` 0.3.0. Voice profiles can build from PDF folders through the existing `vision_parse` plugin or from already-extracted text; source quotes remain untrusted corpus data and factual corpus knowledge stays retrieval-based.
- Updated `vision_parse` to 0.2.0: one call can traverse up to ten pages; ordinary clean pages use 2.2x rendering while dense/suspect/split pages use 2.9x, dense two-column pages split into left/right crops, and very dense single-column pages split top/bottom. The shared Ollama vision client preserves `done_reason`/`eval_count` and accepts `num_predict`; full-page `done_reason=length` automatically falls back to split crops, token-repeat aborts retry only the affected page/crop once, and a bounded process-local 128-page cache avoids repeating identical page/model/mode/focus work.
- Restored the shared GUI helper API `load_runtime_config()` as a **lazy** compatibility wrapper. Prompt and Replies can load runtime/queue/PostgreSQL configuration again without making the lightweight launcher eagerly import the full runtime bootstrap.
- Added transition compatibility for the schema-2 `verbatim_lines` move. The maintained CLI remains `plugins\verbatim_lines\src\_cli.py`, while a root-level `plugins\verbatim_lines\_cli.py` shim keeps older frozen executables/configurations working during in-place upgrades. `verbatim_lines` 1.0.1 also adds a private exact replacement primitive, and core `write_file`/`replace_text` now delegate exact temporary-file content creation to that hash-verified plugin source while retaining their existing authorization, SHA guard, backup, retry, and atomic-replace logic; no unguarded public overwrite tool is exposed.
- `Run-Norm.bat` continues to use the Python launcher/service architecture; it now delegates all three operator windows to `tools\start_operator_consoles.py` instead of spawning each helper independently.
- Norm Runtime, Norm Replies, and Norm Prompt now use the persistent `operator_console_host.py` wrapper. If a helper exits, its console stays open and displays the exit code/error instead of flashing away.
- Runtime and Replies no longer close merely because `norm.exe` briefly disappears; they stay open and retry their data source until the operator closes the window.
- Early detached-service stdout/stderr remain captured in `logs\norm-bootstrap.stdout.log` and `logs\norm-bootstrap.stderr.log`, so import/frozen-startup failures that occur before normal runtime logging are visible.
- Synchronized `core\requirements.txt` with direct runtime/tool imports: NumPy, OpenCV, Kornia, prompt-toolkit, Rich and tzdata are now represented alongside Redis, psycopg/psycopg-pool, aiohttp, Pillow, cryptography and PyMuPDF. PyTorch remains intentionally installer-managed through `environment.torch_version`/`torch_index_url` so the configured CUDA build is preserved.
- Added/retained `BEHAVIOR_AUDIT_0.53.12.txt` documenting high-impact existing capabilities without silently changing their policy.
- Installer remains **1.6.5-unified**. Normal PyInstaller cache reuse remains intentional; this release does **not** switch normal builds to `--clean`.

## 0.53.11 — 2026-10-02 — schema-2 plugins, shared validation pool, PostgreSQL pool, and installer migration

- Standardized first-party plugins on schema 2: root `plugin.json` + README, executable code under `src/`, normally injected from `src/main.py`. `plugin.json.sha256` is the deterministic identity of the complete `src/` tree; generated `plugins/.registry.json` is runtime cache/state rather than package authority.
- Added the first-party bounded `postgres_pool` capability using `psycopg-pool==3.3.3`; approved named connections (`norm`, `stocks`) share process-global bounded pools instead of opening unrestricted direct connections.
- Added one shared semantic validation/reuse pool across tasks and tools. Redis owns live rolling-24-hour/current-generation counts; PostgreSQL stores compact durable snapshots/history on the 12-hour checkpoint policy. A changed value starts a new generation at count 1.
- Refreshed shared validation context before each model/tool-decision round so repeated checks can be recognized across unrelated work without treating counts as permission gates.
- Hardened suppression/resume reconciliation: durably suppressed pending work is not resurrected at startup, resume restores the captured work exactly once, and duplicate resume remains idempotent.
- Added UTC-aware validation timestamp normalization and regression coverage.
- Installer 1.6.5 migration distinguishes the old installed public `norm-imprint.json` baseline from the private local imprint. Effective precedence is manual Environment edit > existing customized value > private local imprint > new public package imprint; an absent old public field means there was no old default.
- Legacy PostgreSQL routing can be recovered from the external `%APPDATA%\Norm\.env`; passwords remain external and are not written into public/package imprints.

## 0.53.9 — 2026-10-01 — operator network map and public configuration split

- Added `/network-map` and `/network-map --json` as synchronous operator commands before normal DB3 prompt ingress.
- Active checks come only from explicit `config/network-map.json` targets; discovered peers never widen the probe set. Honeypot/decoy matches remain observable but are never probed.
- Added `tools/norm-network-map.cmd --json` for SSH/automation.
- Public defaults are topology-neutral; Unified Installer 1.6.0 supports a local non-secret imprint.

## 0.53.8 same-version reliability hotfix — vision plugin + suppression handoff

- Moved PDF vision parsing into the actual package-managed `plugins\vision_parse` tree. The dynamic plugin exposes `plugin_vision_parse__vision_parse__vision_parse`; the old native duplicate is no longer advertised.
- Suppressing a durable task now removes its live Redis work/retry/escalation copies after the resume snapshot is safely stored in PostgreSQL, so the worker can immediately advance to unrelated queued work.
- The synchronous conversation wait path now treats `suppressed` as a terminal-for-this-delivery state and returns promptly, allowing DB3 ingress to acknowledge that prompt and dispatch the next one without requiring `/flush-suppressed`.


## 2026-10-01 - 0.53.8 same-version PDF/stream hotfix
- Added first-party `plugins\vision_parse` PDF reading: PyMuPDF supplies page text/rendering and the local Ollama vision model reconciles visible page content against the untrusted text layer. This avoids treating mojibake, bad column ordering, or OCR-like corruption as ground truth.
- `vision_parse` handles at most four pages per call, renders each page at 2.5x for vision, and returns page-numbered readings plus a continuation page so large books remain resumable.
- Added PyMuPDF 1.28.2 to the managed dependency set.
- Added an Ollama stream watchdog for severe repetitive-token degeneration and bounded partial-line flushing. A no-newline token loop can no longer make the runtime window appear silent and then dump an enormous buffered string only when `/stop-all -now` closes the model response.
- Preserved the existing 0.53.8 unresolved-bits SQL typing fix and bounded rolling-summary refresh fixes.

## 0.53.8 — 2026-09-30 — Ingrained Details and unresolved-bit routing

- Added mixed-turn interpretation before planning: Norm preserves the full user message verbatim while separating the primary executable request from meaningful side information typed as **Ingrained Details**.
- Confident details are written directly to their final durable home using existing memory types or current-task context. Explicit corrections can supersede a matched active memory; future actions/backlog items use the existing `task` memory type.
- Added PostgreSQL `unresolved_bits` as the temporary catch basin only for meaningful details whose correct home is unclear, plus `unresolved_bit_trials` for test-fit evidence while unresolved. Exact repeated unresolved content increments mention count and merges source-thread provenance.
- Added bounded test-fitting of unresolved bits against later real tasks. Plausible matches are tested immediately; otherwise Norm occasionally explores a few least-tested bits. Promotion/application deletes the temporary unresolved record and its trial history instead of maintaining a resolved graveyard.
- Added evidence-based unresolved cleanup: by default a singly-mentioned bit may be removed after 15 non-useful trials spanning at least 3 distinct task domains. Repeated user mention or any useful trial prevents that automatic deletion; recency alone does not qualify.
- Added unresolved-bit state/trial aggregates to background-memory condensation input so uncertain context is visible to compact memory maintenance while still remaining unpromoted.
- Split task provenance into the full original user turn and the actionable primary task request throughout root, child, recovery, deferred-append, verifier, and worker envelopes. Norm-generated steps remain explicitly internal.
- Carried DB3 ingress `prompt_id` through `/api/chat` into durable task-plan provenance. `/queue` now joins ingress entries to the newest matching running task/child and shows its current step rather than presenting the first characters of a mixed user turn as runtime status.
- Same-version launcher presentation refresh: automatic `norm.exe --service` startup is fully hidden (`CREATE_NO_WINDOW` + hidden startup info), and `Run-Norm.bat` prefers a dedicated Windows Terminal window for **Norm Runtime** while keeping Prompt/Replies unchanged; the classic Runtime console remains the fallback when `wt.exe` is unavailable.
- Same-version startup-cleanup hotfix: `prompt_worker.py` now imports `pathlib.Path` at module scope. The 0.53.7 task-retention/temp-cleanup paths already used `Path` during startup and terminal cleanup; the missing import caused `NameError: Path is not defined` and forced Norm to preserve temp material on every startup.
- Same-version unresolved-bit query hotfix: explicitly cast the nullable exclusion parameter to PostgreSQL `text` when selecting unresolved candidates. PostgreSQL could not infer the type of a bare `%s IS NULL` placeholder and raised `IndeterminateDatatype`, preventing unresolved-context test fitting.
- Same-version conversation-summary output-budget hotfix: rolling thread summary refresh no longer uses the legacy single 900-token `generate()` call. It now requests bounded structured output with an 8,000-character summary ceiling and a 3,200-token budget, retries once with a 3,500-character compact target after a model length stop, and preserves the prior summary with a warning instead of emitting a background ERROR if even the compact retry cannot fit.

## 0.53.7 — 2026-09-30 — large-source streaming and semantic task storage

- Removed the whole-source `read_file` size gate. Large UTF-8 sources are streamed with byte/line continuation cursors; the source itself is not limited by the model-result cap.
- Reinterpreted `tools.max_read_bytes` as a 24 MiB processing buffer and added a separate 384 KiB per-tool-result ceiling so source size, processing window, and model-facing payload are distinct. Large sources skip full-file SHA-256 by default and expose a stable size/mtime/path fingerprint; callers can explicitly request a full SHA scan.
- Added task storage accounting under `temp\tasks\<task_id>` with a 54 GiB managed-storage ceiling and 3 GiB per-pass processing allowance. Reaching the processing ceiling writes a continuation ZIP and parks/suppresses the same task for explicit manual resume; resume resets only the per-pass counter.
- Added `append_task_note` for durable internal Markdown extraction/summary notes with automatic 5 MiB physical-file rotation. Aggregate logical notes remain unconstrained except by the task storage ceiling.
- Task-scoped image-analysis derivatives are now written under task temp as reproducible cache rather than long-lived workspace clutter. Verified terminal cleanup retains compact source/recipe lineage and internal notes in `.norm-task-retention` while deleting reproducible derivative bytes.
- Human-facing completed replies are capped at 384 KiB. The full durable PostgreSQL task summary remains intact, and oversized full replies are also spilled to `workspace\large-responses`.

## 0.53.6 — 2026-09-30 — recovery-state hygiene and prompt provenance

- Restored `/inject-context` end-to-end: the activity API now receives a live injection callback, injections resolve to the current active task tree, persist in PostgreSQL, and are consumed by the worker at subsequent model-call boundaries without creating a new task.
- Injected text is explicitly framed as a user-authored context interjection, while generated steps/recovery/verifier work remain Norm-internal.

- Weekly maintenance and manual `/memory-condense` now run recovery-state cleanup before background-memory condensation.
- Fully terminal task trees with verified summaries automatically drop obsolete `task_recovery_notes`; recovery notes no longer accumulate indefinitely after they have served their handoff purpose.
- Stale nonterminal recovery trees are eligible only when absent from live Redis, not suppressed, and older than the configured stale threshold. Norm summarizes task state plus recovery notes into compact `task_history`, replay-validates that summary, and only then prunes the dangling tree. Any summarize/replay failure preserves the original rows.
- Added conservative orphan archive cleanup: `task_evidence_archive` and legacy `task_step_archive` rows are deleted only when a validated compact-history record already covers the missing task; uncovered orphan archives remain preserved for inspection.
- Enforced the existing `request_type` / `prompt_origin` provenance in worker prompts. Norm-generated steps, recovery jobs, verification, and maintenance are explicitly labelled internal and cannot masquerade as user-authored prompts.
- Runtime child/recovery tasks now inherit the true `original_user_prompt`; generated child instructions are retained separately instead of overwriting the user request.
- Added `memory.recovery_stale_hours=24` and `memory.recovery_cleanup_max_trees=20` defaults. The dangling-tree cap does not limit cheap terminal-note cleanup.

## 0.53.5 — 2026-09-30 — pre-task suppression and incremental rebuild cache

- `/suppress-task` now covers the canonical DB3 ingress gap before a durable task UUID exists: it suppresses the currently dispatching prompt first, otherwise the oldest live queued prompt, and prevents uncertain retry resurrection.
- A dispatching pre-task prompt cancellation also cancels the active Ollama call so planning/generation does not continue after the operator suppressed it.
- Runtime PyInstaller work/spec state now persists under `state\build-cache\pyinstaller`; normal builds reuse analysis state and no longer pass `--clean`. `tools\build_norm.py --clean` remains available for deliberate cold rebuilds.
- Updated the cryptography lock from 46.0.4 to 50.0.2.

## 0.53.4 — 2026-09-30 — suppression/cancellation reliability

- Fixed mid-execution suppression acknowledgement to use the actual Redis Streams entry ID rather than the logical task-node UUID. This prevents `XACK` failures after a model generation is cancelled for `/suppress-task`.
- Normalized intentional Ollama response-close races into `ModelGenerationCancelled` without retaining the underlying `http.client` exception chain, so deliberate cancellation no longer surfaces misleading `NoneType.peek` transport tracebacks.
- Applied the same cancellation normalization to text generation, tool-chat generation, and vision streaming.
- Operator consoles now render intentional model cancellation as a yellow cancellation notice instead of a red Ollama error; genuine failures remain red.
- Suppression remains durable-first and preserves the queue entry in Redis after acknowledgement, matching the existing pre-execution suppression path.

## 0.53.3 — 2026-09-29 — about command and taskless manual memory condensation

- Added `/about` to both operator prompt surfaces with current project/version, runtime/executable path, source/frozen mode, Python, package, and plugin summary.
- Added `/memory-condense` for incremental background-memory snapshot refresh and `/memory-condense -full` for a full-source snapshot rebuild.
- Manual memory condensation is scheduled directly on the existing worker and runs only when that worker reaches idle; it does not enqueue a user prompt, create a new task UUID, or re-plan user work.
- Reused the checkpointed `DeepHistoryMaintainer.rebuild_background_snapshot()` path so interrupted condensation can resume safely. The manual operation records a PostgreSQL maintenance note and leaves the stronger destructive `/condense-memories` concept unimplemented.
- Updated visible `/help` output in both prompt consoles.

## 0.53.2 — 2026-09-28 — cipher/plugin separation and frozen cryptography support

- Split the optional five-machine Rotor5 message transform into its own first-party `plugins\rotor5_cipher` capability; `stegosplit_message` is again only the two-image carrier.
- Rotor5 R5E2 uses five independently derived 256-symbol rotor machines, a fresh per-message nonce, pre-rotor compression, and HMAC-SHA256 envelope authentication.
- Added optional `NORM_ROTOR5_SECRET` and `NORM_ROTOR5_PREVIOUS_SECRETS` values in the configured Norm `.env`; only those two already-redacted secret values are exported into the Norm process for the hot-loaded Rotor5 plugin.
- Added `cryptography==46.0.4`, `cffi==2.0.0`, and `pycparser==3.0` to the reproducible environment lock.
- PyInstaller now explicitly collects `cryptography`, `cffi`, and `_cffi_backend`, allowing the frozen `norm.exe` process to hydrate the ChaCha20-Poly1305 StegoSplit plugin without relying on imports from an external editable checkout.
- `stegosplit_message` 0.3.0 exposes typed native tools for text, Base64 bytes, files, password rotation, cover reconstruction, authenticated pair info, and differential stats while retaining the legacy `run(payload)` wrapper.

## 0.53.1 — 2026-09-28 — aiohttp control plane

- Replaced the hand-rolled ThreadingHTTPServer/socketserver chat and activity/control transports with aiohttp.web.
- Preserved the coordinator-facing lifecycle contract so startup/shutdown ordering remains stable.
- Blocking conversation, PostgreSQL-facing, context, and control callbacks run via asyncio.to_thread rather than blocking the HTTP event loops.
- Activity SSE is asyncio-native and treats client disconnect/reset/WinError 10053/10054 as normal stream termination.
- Pinned aiohttp==3.14.3 in runtime and installer locks.
- Retained loopback/Tailscale-only bind validation.
- Added bounded graceful AppRunner cleanup and explicit event-loop/default-executor shutdown.

## 0.53.1 — 2026-09-28 — ingress retry deduplication

- Serialized uncertain-prompt requeue by durable uncertain-record identity so duplicate retry timers cannot create multiple live Redis deliveries for one prompt.
- Requeue now atomically adds the replacement ingress entry and removes its uncertain source record.
- `/queue` and `/queue-full` now exclude acknowledged-but-preserved historical stream rows from the LIVE QUEUE view while failing open if consumer-group metadata is unavailable.
- Added regression coverage for concurrent retry contenders and live-queue historical-row filtering.

## 0.53.1 — 2026-09-27 — launcher ownership/readiness fix

- Restored the proven `cmd.exe start` path for the three operator consoles after both Norm health endpoints are ready.
- Removed `start_operator_consoles.py` from the normal `Run-Norm.bat` startup path.
- Detached service creation now returns and reports the owned `norm.exe` PID instead of discarding the process handle immediately.
- A pre-existing `norm.exe` is treated as an attach/wait condition; a process launched by the current helper is not subsequently described as a second instance.
- Startup timeout messaging now reports readiness failure without falsely claiming that the just-launched Norm process is a duplicate.


## 0.53.1 — 2026-09-27 — restore explicit operator consoles

- `norm.exe` remains detached in `--service` mode.
- `Run-Norm.bat` launches `Norm Prompt`, `Norm Runtime`, and `Norm Replies` as three explicit persistent consoles via `tools\start_operator_consoles.py`.
- Each console stays open if its Python helper exits during startup, so startup errors remain visible.
- Duplicate GUI-helper mutex detection prints which operator console is already running rather than silently exiting.

## 0.52.6 — 2026-09-27 — detached service launch + HTTP ownership hardening

- `Run-Norm.bat` no longer launches `norm.exe` in a transient/minimized helper console. It uses the shared detached service launcher instead.
- Every automatic `norm.exe` start now passes `--service`; the prior GUI fallback path could start manual-mode Norm and leave it vulnerable to console `KeyboardInterrupt` events.
- Run-Norm refuses to launch a duplicate `norm.exe` merely because HTTP is temporarily unavailable. An existing process is given the health window instead.
- Service mode now installs both Python signal handlers and a native Windows `SetConsoleCtrlHandler` guard for Ctrl+C/Ctrl+Break.
- The visible Ollama GIN server (`/api/tags`, `/api/generate`, `/api/chat`) is not Norm's `12543` chat API; Norm health is established only by the configured chat and activity `/health` endpoints.
- GUI DB3 ingress now catches per-entry dispatch/bootstrap exceptions instead of letting the background dispatcher thread die; enqueueing a later prompt also revives a dead dispatcher thread while preserving queued DB3 entries.

## 0.52.5 — 2026-09-27 — proportional plan verification

- Plan verification rejects only blocking defects: lost constraints, unauthorized/unsafe actions, materially contradictory or impossible designs, missing material verification, unbounded work, or missing final synthesis on multi-step plans.
- Clearly described tightly coupled implementation parts may remain in one bounded step; advisory organization/granularity concerns no longer veto execution.
- Redundant mechanisms are only blocking when they create a real contradiction, ambiguity, or user-constraint violation.
- The verifier no longer invents hypothetical implementation/boundary failures when the plan explicitly inspects/reuses an existing mechanism or includes execution tests for that invariant.
- Repair prompts require the smallest material fix and prohibit mechanically returning the same rejected plan.
- Runtime build tooling lock moves PyInstaller from 6.22.2 to 6.22.3.

## 0.52.4 — 2026-09-27 — canonical prompt ingress and startup reliability

- Local Rich-console prompts and SSH `P` prompts now use the same Redis DB3 ingress stream, consumer group, selected-thread key, default project, and core dispatcher implementation.
- Removed the second `norm:rich-console:*` durable ingress namespace. Frontends differ only in transport/UI; prompt interpretation and task creation no longer diverge.
- The canonical dispatcher creates/selects an explicit thread before `/api/chat`, so an accepted console prompt cannot be converted into a semantic-routing clarification before task creation.
- HTTP 200 is no longer sufficient to acknowledge a normal queued prompt: the response must contain a durable `task_id`. A protocol violation is parked visibly in uncertain state without automatic replay.
- Added ingress lifecycle logging for prompt/source/thread/task IDs to make queue-to-task handoff auditable.
- Put the activity API, worker, chat API, and their server threads under one startup ownership/cleanup boundary so a failed migration or later startup stage releases ports before retry.
- Removed the normal-start duplicate PostgreSQL health preflight; PostgreSQL connection/schema failures now occur inside the bounded startup retry loop.
- Added `connect_timeout=5` to generated PostgreSQL connection strings in addition to the existing lock/statement timeouts.
- Serialized plugin refresh/hydration/execution with one process-global `RLock`, covering global `sys.modules`, `sys.path`, stdout, and stderr mutations across multiple `PluginManager` instances.

This file is the concise, version-by-version record of **implemented and promoted changes** in Norm. It answers “what changed in this iteration?” rather than “what is true right now?”

Rules:
- Add an entry for every promoted version or promoted letter revision.
- List shipped behavior, configuration, architecture changes, migration notes, and release verification that materially define that version.
- Keep unfinished designs and backlog items in `FUTURE_IMPLEMENTATION_NOTES.md`.
- Keep current runtime facts in `README.md` / `CURRENT_STATUS.md` and current engineering contracts in `DEVELOPMENT_NOTES.md`.
- Historical incidents or superseded implementation details belong here only when they materially explain a shipped release.
- Internal staging labels that were never promoted are not separate releases here.

## 0.52.3 — 2026-09-27 — Windows service signal isolation

- `Run-Norm.bat` now launches `norm.exe --service`.
- Service mode installs explicit SIGINT/SIGBREAK handlers that log and ignore stray Ctrl+C/Ctrl-Break events instead of treating them as shutdown requests.
- Canonical operator shutdown remains routed through the activity/control API; direct manual `norm.exe` launches retain normal KeyboardInterrupt behavior.
- This addresses a live 0.52.2 failure where the runtime reached ready state and then exited on unsolicited `KeyboardInterrupt` without any logged `/shutdown` or `/stop-all` request.

## 0.52.2 — 2026-09-27 — startup controls, thread navigation, help cleanup

- Activity/control API now starts before bounded PostgreSQL/schema initialization and reports an explicit initializing phase until chat/worker readiness.
- `/status/busy` remains reachable and reports busy during initialization, preventing GUI dispatch from treating schema startup as idle.
- Added queue-ordered `/new [name]`, `/thread-list`, and `/thread-resume <name|id>` controls to GUI and Rich consoles.
- Named threads are created in PostgreSQL while the switch itself remains ordered in Redis ingress, preserving the thread assignment of older queued prompts.
- Visible help now lists canonical commands only; legacy aliases continue to be accepted for backward compatibility.
- Installer 1.3.4 reuses a compatible `.venv`, allows pip to upgrade to any available release satisfying `pip>=26.1`, retains `.ssh` as protected persistent state, and tightens the GUI layout.
- `/flush-suppressed` now clears both suppressed PostgreSQL task records and the matching parked/suppressed GUI delivery records; in-flight prompt-ID tombstones remain until the HTTP dispatch is no longer active so a late connection reset cannot resurrect the submission.

## 0.52.1 — 2026-09-27 — bounded startup/schema hardening

- Added PostgreSQL schema-migration lock and statement timeouts plus serialized advisory migration locking.
- Hardened startup failure logging and retry behavior so stale PostgreSQL transactions cannot wait indefinitely.
- This release retained the 0.52.0 portable layout, plugin, queue, recovery, and installer contract.

## 0.52.0 — 2026-09-27 — portable source, native plugins, integrity recovery, reusable installation

- Standardized the source/executable directory on `core\` and removed stale `app\` assumptions from the portable package/build path.
- Added reusable package-manifest-driven installation with in-place managed sync and compatible `.venv` reuse.
- Promoted dynamic plugin folders to native hot-reloaded tools with multi-file imports and last-known-good fallback; plugin root is now `C:\Norm\plugins`.
- Added `/queue-full` support and explicit `/status/busy` GUI recognition alongside `/status`.
- Hardened UUID/dependency migration and pruning against deleted-parent lineage; added conservative memory-thread recovery plus `tools\repair_norm_state.py`.
- Moved maintained docs into `C:\Norm\docs`, Norm SSH material to `C:\Norm\.ssh`, and external generated work into `Documents\Norm\workspace` / disposable `Documents\Norm\temp`.
- Added conservative temp cleanup: verified terminal task temp can be removed immediately; old scratch is age-cleaned; unresolved recovery/SOS material is preserved.
- Added a first-party backup plugin design producing installer-compatible private full-state ZIPs with source/docs/plugins/.ssh/secrets/workspace/PostgreSQL and environment rebuild metadata while excluding `.venv`.
- Added explicit `[paths].runtime_root`; the reusable installer binds it to the actual target directory after every source/full-backup sync.
- Fixed queued-worker runtime placeholder expansion by using the shared resolved runtime config, and made image analyzer Python/script validation lazy so plain text work is independent of optional image tooling.
- Fixed emergency-stop SOS path resolution by separating the Norm runtime root from the emergency snapshot output root.
- Preserved/fixed the 0.51.5 GUI suppression safeguards: queued suppressed prompt IDs are rejected before primary dispatch, duplicate suppression checks/helpers were removed, and Redis XRANGE queue inspection never passes `COUNT 0`.
- Added self-contained first-party `stegosplit_key`, `stegosplit_message`, and `verbatim_lines` plugins. The MessageCodec no longer depends on an editable external checkout.
- Added `/backup` (portable source), `/backup full` (sensitive full state), and kept `/backup-zip` as a full-backup compatibility alias.
- This entry describes the 0.52.0 clean source/release package. Machine-specific service health must still be verified after installation.


## 0.51.5 - 2026-09-24 - offline rebuild and helper fixes

- Promoted executable SHA-256: `A3F0714DB6E281086563D8E5D9EECE95AEF449204CAFBDDC21D5CD4AA436E635`. Retained version 0.51.5; 0.51.6 was not assigned.
- Included structural tool-output/evidence/history/log redaction and protected credential-file reads.
- Graceful-stop waiting output reduced to one message; existing worker event remains the completion signal.
- External GUI dispatcher uses quiet-driven busy checks with a slow fallback; existing DB3 fixes remain.
- External runtime viewer separates control messages from partial model lines; model-buffer/SOS storage was not changed.
- Added explicit editable StegoSplit restoration instructions for environment rebuilds.
- Validation: source compilation/offline tests, packaged help/version, bundle inspection, read-only persistence audit and synthetic recovery/render replay. No service start or end-to-end runtime health claim for this revision.

## 0.51.4 — 2026-09-22 — Tailscale authority and centralized network topology

- Replaced the old `[ports]` settings model with `[network]`, including `current_machine`, `current_domain`, per-service host selectors, ports, and `require_tailscale`.
- Central runtime resolution now derives Redis, prompt/deletion/console queues, PostgreSQL, `stocks_api`, Norm HTTP, activity/control, and Ollama endpoints from the settings resolver instead of duplicating live network values in `runtime.json`.
- Moved environment-specific PostgreSQL database/user/password values to the configured external `.env` file; runtime JSON no longer carries the connection string.
- PostgreSQL runtime and `stocks_api` use `norm-host.example.invalid:25434`; Redis authority uses `norm-host.example.invalid:6379`; Norm HTTP/activity bind to NORM-HOST’s Tailscale address; Ollama intentionally remains loopback-only.
- Added Tailscale Serve TCP forwarding for Redis so Memurai remains bound to `127.0.0.1:6379` while Norm reaches it only through the tailnet authority path.
- Added a pre-tool-call authority gate: each native Norm tool call must successfully reach/PING the configured Tailscale Redis authority endpoint before execution proceeds.
- Promoted live executable SHA-256: `0EE7EA56B866F0DC6CC3B54C04A4549A97F76DC4C9CCD968CD13D6D7137CC671`; `norm.exe --version` reports `0.51.4`.
- Post-promotion companion-script repair updated the external GUI helpers to use the same 0.51.4 endpoint/config resolver instead of the removed `[ports]` section and raw `runtime.json` connection fields; this did not require replacing the packaged executable.
- 2026-09-23 companion-script hardening kept the packaged 0.51.4 executable unchanged: Redis-backed replies tolerate malformed legacy bytes, large replies bypass monolithic Markdown rendering, `Run-Norm.bat` uses the central settings resolver/noninteractive-safe startup wait, and GUI `/stop-all now` waits for the live Redis model-buffer to settle, writes Redis task state/events + raw model buffers + PostgreSQL checkpoints to `SOS.md`, fsync/read-back verifies it, then force-terminates Norm/Ollama.
- 2026-09-23 external capability extension kept the packaged executable unchanged: `tools\norm_plugins.py` plus the workspace `plugins\` directory provide filesystem-discovered Python plugins with README/capability matching, version/SHA/UUID identity, fresh per-call loading, and `run_command` access. A one-time process restart is required only for an already-running worker to pick up the new broker instruction; plugin swaps themselves require no rebuild/restart.
- The same 2026-09-23 maintenance pass repaired `tools\norm_backup.py` to use the centralized 0.51.4 settings/runtime resolver, added optional checkpoint labels, excluded runtime `state\file-backups` from ZIPs, and removed one-off convenience/probe/write helpers. A requested `0.52.5` backup label is a snapshot/checkpoint identifier, not a promoted executable release.
- A stale PostgreSQL `idle in transaction` session was found blocking startup schema DDL. Clearing the stale transaction allowed both Tailscale-bound APIs (`12543` and `8766`) to start normally. The underlying startup-lock hardening remains a separate unresolved item, not a claimed 0.51.4 fix.

## 0.51.3 — 2026-09-21 — Nonblocking durable GUI ingress

- Normal `norm_gui.bat` prompts became durable Redis DB3 ingress instead of blocking the prompt window on `/api/chat`.
- Added a background dispatcher that submits the oldest queued GUI prompt through `12543` while activity/control and immediate stop remain independently reachable on `8766`.
- DB3 preserves GUI thread/submission/answer state and queued ingress across Norm restart; interrupted dispatches are quarantined as uncertain instead of replayed automatically.
- `/repeat-submission` and `/repeat-answer` requeue the latest applicable persisted turn verbatim.
- Rich-console ingress received a separate DB3 stream/group namespace so frontends do not steal each other’s entries.
- Promoted executable SHA-256: `2409ad004cd2c2d17937e35d37e7bd415728e7d94a3d04f714692465fe5239a2`.

## 0.51.2b — 2026-09-21 — Unified maintenance correction

- Unified regular memory cleanup, deep-history cleanup selection, and derived image-output purge behind one idle scheduler.
- PostgreSQL `runtime_state` became the durable authority for maintenance cadence; Redis retains only the active crash/restart marker.
- Interrupted maintenance resumes the same recorded mode after restart; successful completion clears the active marker only after durable PostgreSQL success state is written.
- Build tooling gained promoted letter-revision support such as `0.51.2b`.
- Promoted executable SHA-256: `f3710205b3bbcf78e68d3c72eb9d0f7c742677cec19cba77c78d46135136db37`.

## 0.51.2 — 2026-09-20 — Weekly derived-image cleanup (superseded by 0.51.2b)

- Added an independent seven-day idle cleanup pass for derived `images\analysis` output with workspace-boundary validation.
- Added crash-visible Redis cleanup markers and `/status-context` visibility for incomplete cleanup state.
- First live cleanup removed 1,359 derived files / 873,675,067 bytes while preserving source/original images.
- This scheduler design was subsequently corrected and unified in promoted revision `0.51.2b`.
- Promoted executable SHA-256: `332f38e05a31fd4740ac23c0ad91cf60895b574889490f3b76b5be9e7f009b8b`.

## 0.51.1 — 2026-09-20 — Runtime/workspace split and reproducible backup

- Moved the runtime to `C:\Norm` while keeping the model-editable workspace at `%USERPROFILE%\Documents\Norm`; startup validates that the two roots do not overlap.
- Normal and recovery-child tools receive the configured workspace plus approved external roots, while the runtime tree itself is not model-writable.
- Added `/backup-zip` with PostgreSQL `norm_runtime`, workspace, runtime, manifest, and restore helpers while excluding disposable build/cache output.
- Removed the large rebuildable `.venv` from backups and added pinned Python/CUDA-Torch/dependency rebuild settings and documentation.
- Pruned obsolete build/staging artifacts after preserving the external rollback snapshot.
- Promoted executable SHA-256: `FB5E6E2BB7668D1C2CBD1CAEC60F356D193A50B70B208DD0E2B50C56CEF4BBF4`.

## 0.51.0 — 2026-09-20 — Versioned build, recovery scheduling, and opaque runtime identity

- Made `settings.ini` the canonical project metadata/document-pointer source and added repeatable `tools\build_norm.py` PyInstaller release tooling.
- Deferred append/follow-up planning now waits for the predecessor task to complete and pass final verification before generating the real follow-up plan.
- Oversized-recovery child replacement gained bounded retry handling without repeatedly consuming the parent retry budget.
- Added immutable task/node/dependency UUID identity (`task_uuid`, `node_id`, `edge_id`) and backfilled existing PostgreSQL runtime records while preserving readable compatibility IDs.
- Dependency/append/recovery linkage now uses opaque identities where available, with legacy lookup retained for compatibility.
- GUI companion windows were hardened for graceful runtime shutdown and real Windows Ctrl+C/Ctrl+Break behavior.
- The final promoted 0.51.0 executable after same-version fixes had SHA-256 `A72D7FC8E0268CB42A1F062BB63EFCAA4904B159330972D878779738A2DDAEC6`.

## Earlier development baseline — 2026-09-13 through 2026-09-19

Norm was already under active development before formal `0.51.x` release naming. That work established the PostgreSQL/Redis coordinator model, step-aware planning/verification, cancellation safety, temporary thinking persistence, targeted PostgreSQL pruning, shell evidence capture, busy-state reporting, `/status-context`, and the first GUI/operator shell. Detailed chronology remains in `DEVELOPMENT_NOTES.md`; these pre-version milestones are not presented as invented releases.

