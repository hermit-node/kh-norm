# Norm 0.54.3 portable source package

## Resource-only busy and suppression runway

Resource-only busy signals yield for up to 30 seconds; actively tracked Norm work remains a hard gate. Suppressed in-flight ingress is rechecked at HTTP admission, with cancellation tombstones retained through `/flush-suppressed`.

This directory is the portable source payload for **Norm 0.54.3**, derived from the user-supplied 0.54.2i + SSE source ZIP.

The portable source package excludes generated or machine-private material such as .venv, compiled core\norm.exe, build output, Python caches, secrets, and private runtime state.

## 0.54.3 Nabler structured reporting

- Adds one unified `plugins/nabler` schema-2 plugin with original native tool name and merged inline-script Python authoring API. No redundant `nabler_inline` plugin is shipped.
- Adds `tabulate==0.9.0` to both source dependency manifests, N2 default preference, conclusion directive, regression tests and source package inventory.
- Pure source package: contains no compiled executable, no .venv, no installer EXE, no secrets or user-specific local config. The existing managed optional-plugin repair fallback remains pinned to verified 0.54.2g.

## 0.54.2i progress leases, failed-task controls, and compact continuation

- N1 reviews task progress at round 96 and grants 36-round extensions only when durable forward progress is real, with an absolute 384-round cap. Existing per-turn tool behavior is unchanged.
- Budget failures preserve resumable queue/checkpoint state. `/failed`, `/resume-failed`, and `/flush-failed` expose operator recovery/cleanup; flush retains one generalized lesson and removes failed execution/history debris plus stale background snapshots.
- Context-headroom continuation uses a fresh compact slice: the resumed N2 call skips already-consumed bulk task/prior/background context and relies on N1's distilled continuation plus durable PostgreSQL fallback. Existing 0.54.2h headroom checkpoints are recognized from their saved yield markers on first resume after upgrade. Tool proposals without results are explicitly treated as unexecuted during distillation.
- Managed-plugin fallback remains pinned to the known-good 0.54.2g package because plugin payloads are unchanged.

## 0.54.2h planner-only file mutation discipline

- File-creation/file-editing plans should use one primary output file per executable step while allowing unrestricted source/context reads.
- This patch does not add runtime enforcement and does not modify the verbatim-lines plugin or plan verifier.
- Managed-plugin fallback remains pinned to the known-good 0.54.2g package because plugin payloads are unchanged.

## 0.54.2g packaged-plugin repair + 0.54.2f runtime fixes

- Restored the exact verified `vision_parse`, `web_browser`, `jan`, `vane`, and `opencode` Schema-2 plugin payloads that were accidentally omitted from the 0.54.2/0.54.2f portable-source archives. Their `src/` trees match the SHA-256 identities already declared by this package.
- The managed-plugin public fallback now points to the matching 0.54.2g source package instead of the incomplete 0.54.2 archive.
- Norm Prompt remains singleton; Norm Replies and Norm Runtime are read-only fan-out viewers and do not use named mutexes.
- Explicit text byte-cursor reads are marked as streamed evidence so N1 records deterministic metadata instead of running the raw chunk through verification_value.
- Portable-source generation excludes core/norm.exe; the installer builds and validates the executable from source.
- The 0.54.2 action-boundary protected-literal/Bird behavior and oversize-recovery fixes remain intact.

## 0.53.20 optional remote-agent/plugin installation

Installer 1.6.9 adds shared chat/embedding provider fields plus explicit Jan, Vane, and OpenCode Tailscale endpoints on the Environment page. Managed public/optional plugins are reconciled checksum-first. Existing `src\` trees are hashed before any write; an exact target SHA is left untouched. A mismatch repairs only Norm-owned `src\` from the packaged release and re-hashes it; the pinned public 0.53.20 source is used only if that repair still fails. Missing plugins prefer the exact public 0.53.20 source and use the verified packaged adapter only as an offline fallback. Non-src plugin state/cache survives repair.



## Portable remote SSE viewer

The installed root includes `make_sse.bat`. Running it uses the installed `.venv` and current `config\settings.ini` to build a single `Norm-SSE.exe` beside the batch file. The generated viewer embeds the current Norm machine's tailnet activity `/events` endpoint, contains no Redis/PostgreSQL passwords or other Norm secrets, and can be copied to another Windows machine on the same tailnet. `Norm-SSE.exe` is read-only and reconnects automatically when the source Norm instance is offline or restarting.

## Included capabilities

- Adaptive single-chunk evidence streaming for large text reads: context-budgeted piece size, explicit seam markers, compact retained facts, and raw-payload eviction between N2 turns.
- N1/N2 tool gating and loop supervision.
- Session-only transactional /switch-model discovery/switching through the live Ollama API; every restart boots norm.
- Single-source operator help from docs\help_menu.txt, loaded on demand by both prompt frontends.
- Durable PostgreSQL task/memory/history state.
- Redis prompt queues and hot validation pool.
- Recent-only manual/background memory condensation.
- Alternating scheduled regular/full maintenance on a configurable interval.
- Manual /memory-condense -deep for bounded older-history compaction/validation without hierarchical merging.
- Full-history condensation with configurable row-batch/sample sizing and conservative hierarchical merging of neighboring compact rows.
- Canonical internal state_root and capability-specific file-access overrides.
- Package-local 7-Zip archive support.
- First-party PDF vision parsing.
- Verified-download WeasyPrint 70.0 / Pango 1.58.2 runtime with package-pinned upstream URL/SHA and lazy repair helper.
- Managed dynamic plugins and shared PostgreSQL pool.
- Operator consoles and emergency shutdown coordination.

## Current memory tuning

config\runtime.json defaults:

    scheduled_memory_interval_days = 7
    regular_memory_window_days = 14
    consolidation_batch_chars = 14000
    consolidation_batch_target_chars = 1800
    consolidation_snapshot_target_chars = 6000
    deep_history_full_batch_rows = 200
    deep_history_full_samples_per_batch = 12
    deep_history_full_merge_max_records = 6

These settings are re-read when a maintenance pass starts.

## Documentation

- docs\README.md — current operator/runtime overview.
- docs\CURRENT_STATUS.md — concise current facts.
- docs\DEVELOPMENT_NOTES.md — current implementation contracts.
- docs\FUTURE_IMPLEMENTATION_NOTES.md — active backlog only.
- docs\MAINTENANCE_VERIFICATION.md — current package evidence.
- docs\RELEASE_NOTES.md — release/version history.

## Package contract

package-manifest.json is the installer-facing source contract.

Installer 1.6.9 binds the portable-source ZIP by exact filename and SHA-256, migrates/preserves machine configuration/state, hashes existing managed plugin src before mutation, repairs only mismatched Norm src, installs missing optional plugins from verified public source, preserves a valid native WeasyPrint runtime or fetches/repairs it from the pinned official archive, validates its SHA-256 and real PDF render, and recreates/reuses generated environment/build output as appropriate.

## 0.53.20 optional web plugin

This package includes schema-2 `plugins\web_browser` v1.0.0 and exact direct pins for Playwright 1.63.0, Beautiful Soup 4.15.0, Soup Sieve 2.10, and Trafilatura 2.3.0. The plugin delegates its third-party web runtime to the installed `.venv`, so the portable plugin remains independently removable and does not require those libraries to be frozen into `norm.exe`.

## 0.54.2 recovery scope and action-boundary protected-literal guard

- Oversize recovery no longer invents `scope_items=[1]` for ordinary unnumbered work. Atomic-item limits apply only when a real numbered scope exists.
- The existing upstream protected-literal transformation remains unchanged. N1 checks only concrete proposed actions, not N2's ambient reasoning.
- The first denied action returns only `The Bird says No. The Worm won't Go.`; a later retry may receive a short safe workaround. Existing PostgreSQL pool/query/execute capabilities remain fully allowed, and guard state resets on restart.
- Existing PostgreSQL pool/query/execute capabilities remain fully allowed; ordinary code analysis of credential usage is not blocked.

## 0.54.2b Redis verification-pool replay

Redis's existing validation pool is the sole hot reusable-evidence cache. N1 decides whether relevant cached observations are sufficiently applicable/current for N2's request; when they are, the selected exact replay payload is fetched from the companion Redis replay hash and delivered to N2. Otherwise the source runs again and the pool is refreshed. Information-only repeats are not blocked by the generic loop reset, the redundant process-local raw-result cache is removed, and large reads still permit only one raw chunk in an N2 turn. PostgreSQL remains the durable history/checkpoint layer.

## 0.54.2a continuation context

Large evidence streams use one replaceable raw chunk at a time. Ollama token counters trigger the existing checkpoint/requeue path at 5% remaining context headroom without a separate full-context rescan. The recent execution slice is retained losslessly in PostgreSQL; N1 distills the small task-applicable continuation that N2 receives on the same requeued step.
