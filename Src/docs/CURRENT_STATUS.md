# Norm current status

Current package: **Norm 0.54.2f** with **Installer 2.0.0**.

### Console fan-out + byte-cursor verification fix (0.54.2f)

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
- Prompt ingress uses Redis DB3; task queues use their configured Redis databases/streams.

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

Package-managed first-party plugins: backup, file_read, postgres_pool, rotor5_cipher, soft_delete, stegosplit_key, stegosplit_message, verbatim_lines, and voice_profile. Public/optional plugins `vision_parse`, `web_browser`, `jan`, `vane`, and `opencode` use install-if-missing semantics: an existing plugin directory is operator-owned and is not overwritten by normal updates.

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
