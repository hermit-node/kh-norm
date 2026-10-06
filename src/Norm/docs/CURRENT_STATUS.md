# Norm current status

Current package: **Norm 0.53.18** with **Installer 1.6.8-unified**.

This file is a current-state snapshot only.

## Core runtime

- Default runtime root: C:\Norm and relocatable through installer path configuration.
- Runtime source/executable: core\.
- Compiled target: core\norm.exe.
- Canonical configuration: config\settings.ini and config\runtime.json.
- Canonical runtime state root: [paths].state_root.
- Durable workspace: %USERPROFILE%\Documents\Norm\workspace.
- Disposable/recovery root: %USERPROFILE%\Documents\Norm\temp.
- PostgreSQL runtime schema: norm_runtime.
- Prompt ingress uses Redis DB3; task queues use their configured Redis databases/streams.

## Agent boundary

- N2 is the reasoning/execution worker.
- N1 is the model-tool gatekeeper and loop supervisor.
- User ingress and N2 user-facing output are passed through unchanged.
- N1 owns verified-result reuse/check-in for model-requested information tools.
- Exact same-step repeat requests can reuse raw results.
- Successful mutations invalidate affected validation/cache targets.
- Deterministic post-write verification remains a runtime safety invariant outside model approval.

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

Scheduled memory maintenance alternates **regular -> full -> regular -> full** on the configured interval (default 7 days). Transient failures may resume the same mode and do not advance alternation. Deterministic output truncation parks the run as requires_attention with auto-resume disabled. /status/busy, /queue, and /queue-full expose maintenance; /suppress-task can park it when no user task is ahead; /resume-task maintenance explicitly resumes it. Scheduled full sweeps the complete historical compact/raw archive. Deep is manual-only through /memory-condense -deep and performs bounded older-history compaction/validation without hierarchical merging.

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

Package-managed first-party plugins: backup, file_read, postgres_pool, rotor5_cipher, soft_delete, stegosplit_key, stegosplit_message, verbatim_lines, vision_parse, and voice_profile.

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

Installer 1.6.8 performs managed in-place source sync, preserves persistent machine state plus a valid native WeasyPrint runtime, migrates configuration, reuses a compatible .venv, fetches/repairs WeasyPrint/Pango when absent/stale, validates it, and binds one exact portable-source payload by filename and SHA-256.

## Validation boundary

Package validation proves the shipped source/package contract. It does not by itself prove a target machine's external services, credentials, CUDA, network authority, or SSH connectivity.
