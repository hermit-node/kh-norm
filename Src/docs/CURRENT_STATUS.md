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

## Compiled StegoSplit engines inherited from the 0.55.3 CLEAN release

StegoSplit Message 0.4.3, Auth 0.1.0, and Key 0.2.0a1 now carry CPython 3.14 native binaries with public tool adapters. Installer 1.6.12 validation, compiled functional smoke tests, and 18 plugin identity checks passed. No full installation has been attempted; image-auth is not natively wired to SSH. Auth is disabled until configured.

# Norm current status

Current package: **Norm 0.55.7** portable source with compiled StegoSplit internals; verified external installer: **1.6.12-unified**.

### Changes added in 0.55.1

- Operator checkbox (with `--once`/`--keep` switches) allows task context injections to auto-retire ONLY when a structured accepted verifier marks their specific ID fulfilled and tool evidence exists. PostgreSQL state preserves the original injection; active prompts consult the live DB rather than stale queue metadata. `/injections`, `/injection-retire`, and `/injection-restore` are direct console controls.
- Queued N2 work now uses the same SHA-validated active voice prompt as direct conversations. This affects subsequent worker steps, not N1 gatekeeper or model weights.
- `voice_profile` 0.4.2 adds optional durable per-profile `style_override.txt`, `/voice-tune`, and `/voice-test` A/B prompting. The override survives a corpus rebuild but requires `/voice-activate` to refresh the currently active prompt.
- Regression tests are source-only in this environment. PostgreSQL migration, live Windows console checkbox and local Ollama output have not been end-to-end tested on KHzz.

### Lesson/code-memory plugin integration (packaged in 0.55.0)

- Built-in schema-2 `lesson_model` v0.3.0a1 captures reusable lesson candidates but requires explicit operator approval before a lesson becomes approved/training-eligible. Training and `norm:latest` promotion remain disabled pending a reviewed LoRA/QLoRA backend and regression gate.
- Source ZIP study can retain a verified wholesale content-addressed archive, walk bounded code chunks sequentially, persist dense structured project/version memory in PostgreSQL, write a separate human README, and emit a standalone interactive topology map from a canonical CodeGraph representation. The original user source ZIP is never moved, renamed, or deleted.

### Structured conclusion reporting (0.54.3)

- Built-in Nabler v0.2.0 supplies a one-call, data-only native reporting tool for verified checklist/table rows. N2 prefers it for suitable multi-item conclusions, but a rendered PASS is not evidence of executing a check.
- Python script authors may use deferred lambdas/callables or trusted multiline inline `script=` blocks; native `render_report` ignores executable source fields.
- Both dependency files pin `tabulate==0.9.0`. Schema-2 SHA-256 protects plugin source; local import registration via `plugins/nabler/install.py` is optional for standalone scripts.

### Adaptive round leases + compact continuation (0.54.2i)

- Task-round accounting remains unchanged, but 128 is no longer the only fixed outcome: N1 first reviews genuine progress at round 96 and can extend the granted budget by 36 rounds per review up to an absolute 384-round ceiling. Dense turns with many useful tool calls are not penalized; repeated checks/reasoning without durable state advancement do not earn another lease.
- A budget safety-stop keeps a durable queue/checkpoint snapshot instead of becoming unrecoverable. `/failed` lists recent failed tasks, `/resume-failed N` restores the saved queue state with a fresh active round budget while retaining lifetime telemetry, and `/flush-failed` distills one reusable failure-prevention lesson before deleting failed task execution/history state and invalidating stale background snapshots.
- A context-headroom checkpoint now marks the requeued slice as compact-resume. The next N2 request skips the bulky live working-memory/prior-step/background payloads and receives only the original/current instruction plus N1's compact continuation and evidence cursors; PostgreSQL keeps the lossless history. Existing 0.54.2h headroom checkpoints are recognized from their legacy yield markers on first resume. N1 explicitly treats a tool proposal without a following tool result as not executed.

### Single-primary-file planner discipline (0.54.2h)

- Planning now prefers one primary mutated output file per executable file-creation/file-editing step. Reads remain unrestricted, related target files become separate executable steps, and one complete file may still be emitted through the guarded verbatim writer. This is planner guidance only: no runtime guard, verifier rule, or plugin behavior changed.
- Managed-plugin fallback intentionally remains pinned to the known-good 0.54.2g package because 0.54.2h does not alter plugin payloads.

### Package completeness + console/byte-cursor fixes (0.54.2g)

- Restored the exact SHA-verified `vision_parse`, `web_browser`, `jan`, `vane`, and `opencode` plugin payloads omitted from 0.54.2/0.54.2f. The package no longer depends on the incomplete 0.54.2 source ZIP to repair a plugin that is also absent there; its managed public fallback targets 0.54.2g.
- Norm Prompt remains the only singleton operator console. Norm Replies and Norm Runtime are read-only fan-out viewers and no longer use Windows named mutexes, so an invisible orphan viewer cannot block a usable replacement window. Multiple Replies viewers use independent Redis XREAD cursors; multiple Runtime viewers receive independent SSE subscriber queues.
- Explicit text read_file calls with start_byte are now tagged as streamed evidence even when N2 supplies the byte cursor directly. N1 records deterministic path/fingerprint/cursor metadata instead of sending the raw chunk through verification_value, eliminating the Laguna output-truncation path seen during chunked source inspection.
- Portable-source packaging now omits core/norm.exe as the manifest already promised, preventing an older compiled binary from riding inside a source-only installer payload.

### Recovery scope + action-boundary protected-literal guard (0.54.2)

Oversize recovery preserves an empty/unscoped parent scope instead of manufacturing item 1, so the single-item four-subcheck limit is applied only to genuinely numbered atomic work. Protected literals are still transformed upstream exactly as before. N1 no longer watches N2's ambient reasoning for credential language: it evaluates only a concrete proposed action that plausibly tries to recover or bypass a transformed literal. Ordinary file inspection, code tracing, refactoring, and credential-usage analysis proceed normally. The first blocked recovery action receives only `The Bird says No. The Worm won't Go.`; a repeated attempt may receive a short goal-preserving workaround, including direct PostgreSQL query access when that satisfies the task. This guard state is process-local RAM only and is not persisted to Redis or PostgreSQL.

### Redis verification-pool replay (0.54.2b)

The live Redis verification pool is the only hot evidence-reuse cache. N1 judges whether relevant prior observation(s) are sufficiently applicable/current for the present N2 information request; age alone is not a staleness rule. If sufficient, Norm fetches the exact bounded replay payload from the companion Redis replay hash after N1's decision and returns that evidence to N2. If insufficient or unavailable, the source executes fresh and Redis is refreshed. Information-only repeats are not blocked by the generic loop-reset path. The former process-local raw-result cache has been removed. PostgreSQL remains the durable history/checkpoint authority.

### Adaptive chunked evidence context (0.54.2a)

Large default text `read_file` results are now delivered through a single replaceable raw-evidence slot. Norm measures the current N2 request footprint (system instructions, messages, and native tool schemas), reserves the active N2 output budget and safety margin, then targets `context_budget.raw_evidence_fraction` (default 0.20) of the remaining usable input for exactly one chunk. N2 may select one numbered piece at a time with the returned `stream_id`/`piece`; previous raw chunks are evicted after N2 commits compact retained facts. Seam markers preserve cut topology without loading neighboring chunks, and stream fingerprints prevent cross-version reconstruction.

The full raw tool result remains in durable evidence; review/verifier prompts receive chunk provenance and retained facts rather than replaying every raw chunk. Streamed file verification records deterministic file/source-fingerprint metadata in the Redis validation pool without spending N1's `verification_value` generation budget.

When Ollama reports that the active N2 prompt plus newly visible assistant payload has <=5% estimated context headroom remaining, Norm archives the recent execution slice in PostgreSQL and uses N1 to distill the task-applicable continuation. The existing queue checkpoint/requeue path restarts the same step from that compact `job.context`; it is not represented as a new user turn. PostgreSQL remains the lossless recovery/audit source while N1's checkpoint is deliberately lossy working memory. Chunk-stream cursors survive the requeue through job metadata.

This file is a current-state snapshot only.

## Core runtime

- Default runtime root: C:\Norm and relocatable through installer path configuration.
- Runtime source/executable: core\.
- Compiled target: core\norm.exe.
- Canonical core configuration: config\settings.ini and config\runtime.json.
- Plugin configuration registry: config\plugins.ini, generated/reconciled from each Schema-2 plugin.json on startup/hot refresh. Jan/Vane/OpenCode and shared-model defaults are disabled with blank endpoints, so no second-PC configuration preserves legacy Norm/Ollama behavior.
- Canonical runtime state root: [paths].state_root.
- Durable workspace: %USERPROFILE%\Documents\Norm\workspace.
- Disposable/recovery root: %USERPROFILE%\Documents\Norm\temp.
- PostgreSQL runtime schema: norm_runtime.
- Prompt ingress uses Redis DB3; task queues use their configured Redis databases/streams. A hardware-only `resource_activity` signal delays one prompt for at most 30 seconds; tracked tasks/active requests always remain a hard gate, and unknown status is fail-closed. Suppression of a claimed ingress prompt remains effective even after `/flush-suppressed` because its prompt-ID tombstone is not removed before dispatcher admission checks.

## Agent boundary

- N2 is the reasoning/execution worker, subject router, and semantic editor/ranker for memory condensation.
- N1 is the boundary critic/gatekeeper. N1 reviews the exact user turn before N2, judges N2's subject-routing proposal, gates/reuses model-requested tool evidence, watches repeated reasoning/tool loops, judges maintenance candidates, and accepts/retries the final user-facing answer.
- N1 does not rewrite the authoritative user text or final answer itself. A retry sends bounded review feedback back to N2 for repair/resubmission.
- User-ingress, routing, final-answer, and maintenance N1 reviews use operator-configurable model budgets/timeouts from `agents.n1`; maintenance defaults remain 5000 tokens / 720 seconds.
- N1 owns verified-result reuse/check-in for model-requested information tools. Relevant prior Redis observations may be replayed when N1 judges them sufficient; otherwise the source is queried fresh.
- Successful mutations invalidate affected validation/cache targets. Deterministic post-write verification remains a runtime safety invariant outside model approval.

## Subject-bound conversation routing

- N2 decomposes mixed user turns into materially distinct subjects and proposes thread placement; N1 reviews the proposal before any new thread is created.
- Existing-thread reuse requires confidence >= **0.68**. A weaker match is not attached to the existing thread; N2 must create a better-named subject thread or request clarification.
- The authoritative user turn remains intact in `messages.content` after the existing ingress whitespace normalization. `message_threads.projected_content` stores only the accepted subject-relevant projection seen by each thread, preventing a mixed turn from copying unrelated sidenotes into every matched thread.
- Assistant replies are projected back into the accepted subject threads using only verbatim excerpts from the approved reply. If projection fails, only the primary thread receives the full answer rather than contaminating side threads.
- Durable memory extraction and runtime-summary refresh run per subject thread after a mixed turn so side-topic state remains subject-local.

## Scheduled memory maintenance

- Reductive background maintenance runs on an independent 7-day clock. N2 emphasizes important/current material and compresses less-important context; N1 reviews each candidate.
- Each condensation gets at most four N2 attempts. N1 may accept early when the result is adequate and <=1,800 characters; 3,400 characters is the practical batch ceiling, not a fatal failure threshold.
- Destructive long-cycle maintenance runs on an independent 21-day clock. N2 may propose that obsolete/useless compact history be discarded entirely; N1 must approve each destruction.
- A destructive run also satisfies that week's reductive clock, preventing an immediate second maintenance run.
- N1 maintenance judgment counters are exposed in maintenance result details/logging.
- N1 maintenance review allowance and timeout are runtime-configurable under `agents.n1`: `maintenance_num_predict` defaults to 5000 and `maintenance_review_timeout_seconds` defaults to 720 seconds.

## Session model and operator help

- Norm process startup always selects norm; interactive model selection is never persisted across restart.
- /switch-model lists installed Ollama models through the live Ollama API and supports numbered or exact-name selection.
- A switch is idle-only and transactional: every distinct live Ollama endpoint is probed first, then all live clients change together; any pre-commit failure leaves the current model unchanged.
- The model used for Ollama shutdown follows the active session selection.
- docs\help_menu.txt is the single operator-help authority for both Prompt frontends and is read on demand by help / /help.

## Validation pool

- Redis hot store: norm:validation:pool.
- Identity is target-first.
- Fresh same value increments the current count.
- Verified change stores the prior value and resets current checks to 1.
- Reuse alone does not increment.
- Hot evidence becomes migration-eligible after about 24 hours.
- PostgreSQL retains bounded historical verification state.

## Durable summaries and memory

- End-of-task summaries are current-state projections.
- Summary coverage advances from the durable message cursor.
- Same-turn durable-memory changes are applied before refresh.
- Invalid transcript/supersession-ledger shapes are rejected.
- Repeated generation failure falls back to deterministic current-state reconstruction.
- Unplaced meaningful details use unresolved_bits / unresolved_bit_trials until promoted/applied.

## Memory maintenance

Regular background maintenance and manual /memory-condense are recent-only. Default recent window: **14 days**.

The scheduled regular background snapshot is a small working-memory layer, not a second archive. Defaults are 14,000 input chars per slice, about 1,800 output chars per slice, and about 6,000 chars for the final snapshot. It aggressively synthesizes repeated state and drops routine chatter, examples, transient levels/shorthand, smoke narration, superseded state, and source-by-source restatement.

Manual recent compact-record validation is separate: every compact record reconstruction is isolated and has a maximum generation budget of **4,800 tokens × 4 continuation segments**. Replays are disposable and never carry into the next record.

Scheduled memory maintenance uses independent clocks: **reductive every 7 days** and **destructive every 21 days**. When both are due, destructive runs and also satisfies that week's reductive clock. Transient failures resume the same recorded mode. Deterministic output truncation parks the run as requires_attention with auto-resume disabled. /status/busy, /queue, and /queue-full expose maintenance; /suppress-task can park it when no user task is ahead; /resume-task maintenance explicitly resumes it. The destructive pass sweeps the complete historical compact/raw archive; N2 may propose destroying useless history, but N1 must approve each destructive omission. Deep remains manual-only through /memory-condense -deep and performs bounded older-history compaction/validation without hierarchical merging.

Full historical condensation defaults:

    deep_history_full_batch_rows = 200
    deep_history_full_samples_per_batch = 12
    deep_history_full_merge_max_records = 6

Each full pass sweeps the entire date-ordered compact-history set. For every 200 compact rows it validates up to 12 chronologically distributed samples in isolation, then performs one hierarchical merge level over neighboring windows of up to 6 compact rows. A window may remain 6 rows or safely reduce to 1–5; only validated merged replacements supersede originals.

A merge cluster may remain unchanged or reduce to any smaller number. Every actual merged constituent must reconstruct from the merged record alone. Failed merges fall back to the original memories.

Memory-maintenance sizing and the recent window are reloaded when a pass starts.

## Checkpoint writing

Condensation checkpoints use unique temp files, flush + fsync, bounded atomic replacement retries, and durable in-place fallback for persistent Windows destination-sharing denial.

## Emergency stop

/stop-all now creates/verifies the SOS snapshot, stops Norm/Ollama, signals exact registered Norm operator consoles, leaves non-prompt windows visible about 5 seconds and Prompt about 10 seconds, and lets Enter/Ctrl+C close a countdown immediately.

It does not target generic Python processes.

## File/state authority

- @state is internal-only.
- Internal runtime maintenance can use state directly.
- N2/native/plugin tools do not inherit state access unless a capability-specific override grants it.
- Core and built-in file tools share the same path-authorization policy.

## Plugins

Package-managed first-party plugins: backup, file_read, lesson_model, postgres_pool, rotor5_cipher, soft_delete, stegosplit_key, stegosplit_message, verbatim_lines, voice_profile, codex_reasoner, and nabler. Public/optional plugins `vision_parse`, `web_browser`, `jan`, `vane`, and `opencode` use install-if-missing semantics: an existing plugin directory is operator-owned and is not overwritten by normal updates.

Schema-2 package plugins use plugin.json, README.md, and src\ with deterministic source-tree identity.

## PostgreSQL

One process-global bounded pool is canonical. postgres_pool borrows approved connections from that pool. Model-facing DB access is constrained to approved logical connections/schemas and bounded operations.

## PDF, archive, and document tools

- Package-local 7-Zip is the preferred archive backend.
- vision_parse handles up to 10 PDF pages per call and uses rendered pages as authority over corrupt/untrusted text.
- PDF rendering uses adaptive 1.5× / 2.2× / 2.9× tiers with crop fallback for dense/truncated pages.
- WeasyPrint 70.0 / Pango 1.58.2 is a package-pinned verified external runtime. Public source carries the fetch/validation helper, not the frozen native tree.
- Installer/runtime repair verifies the pinned official archive SHA-256 and requires a real HTML-to-PDF render.

## Installer

Installer 1.6.9 performs managed in-place source sync, preserves persistent machine state, verifies managed optional-plugin src before writing, leaves matching src untouched, repairs mismatched Norm src, preserves a valid native WeasyPrint runtime, migrates configuration, reuses a compatible .venv, installs missing public/optional plugins from verified public source, fetches/repairs WeasyPrint/Pango when absent/stale, validates it, and binds one exact portable-source payload by filename and SHA-256.

## Validation boundary

Package validation proves the shipped source/package contract. It does not by itself prove a target machine's external services, credentials, CUDA, network authority, or SSH connectivity.

## Optional web-browser plugin

This 0.53.20 package includes `plugins\web_browser` v1.0.0. It exposes real-browser page opening/text extraction, CSS-selected link discovery, bounded crawling, Bing/Bing News result discovery, and file-link download into the Norm workspace. Browser state persists under `Documents\Norm\workspace\web\sessions`. The plugin runs its Playwright/Beautiful Soup/Soup Sieve/Trafilatura worker through the installed Norm virtual environment rather than embedding those libraries into `norm.exe`.
### Runtime profiles and Codex reasoning (0.54.1a)

`config/profiles.json` owns operator/user/N1/N2 persona and voice assignments. Direct persona is operator-authored; inferred persona/voice artifacts come from `voice_profile` and remain subordinate to runtime/tool rules. The `codex_reasoner` plugin can call Codex CLI for ephemeral read-only reasoning while Norm retains all execution/mutation responsibility.

### Runtime-owned model policy (0.54.1a)

Model context size and agent-persistent instructions are owned by `config/runtime.json`, not by `settings.ini`, `config/norm.json`, or the Ollama Modelfile. N1 and N2 have separate persistent-instruction sets that follow their runtime clients across model switches. Context overflow is routed into bounded oversize decomposition instead of identical retry.

### Plugin settings registry (0.53.22)

Exact duplicate `plugins.ini` backup states are SHA-256-deduplicated and do not consume the 12-slot retention limit; plugin-setting backups live under `backups/plugins-ini` and legacy `config/backups/plugins-ini` is migrated automatically.

Schema-2 plugins declare non-secret defaults in `plugin.json` under `settings`. `PluginManager.refresh()` materializes/reconciles `config/plugins.ini` on startup and hot-rescan: declared operator values are preserved, missing/invalid values get defaults, and dangling keys/sections are removed after a rotating backup. Secrets stay in the configured external `.env`. Plugin/config/file/SQL/archive backup classes retain at most 12 newest backup sets.



## Maintenance condensation model

Memory maintenance can use a model independent of N2. Configure `memory.condensation_model` and `memory.condensation_backup_model` in `config/runtime.json`. Each setting is optional; a blank value resolves to `norm`. Friendly names are resolved against Ollama's installed-model inventory at maintenance start using deterministic exact/base/starts-with/substring/fuzzy matching; ambiguous names are never guessed and fall through to the backup. The effective failover order is primary → one backup → `norm`. Settings are re-read when a maintenance pass begins.
