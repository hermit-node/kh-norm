# Norm 0.53.20 portable source package

This directory is the exact source payload intended for **Norm 0.53.20 / Installer 1.6.9-unified**.

The portable source package excludes generated or machine-private material such as .venv, compiled core\norm.exe, build output, Python caches, secrets, and private runtime state.

## 0.53.20 optional remote-agent/plugin installation

Installer 1.6.9 adds shared chat/embedding provider fields plus explicit Jan, Vane, and OpenCode Tailscale endpoints on the Environment page. Managed public/optional plugins are reconciled checksum-first. Existing `src\` trees are hashed before any write; an exact target SHA is left untouched. A mismatch repairs only Norm-owned `src\` from the packaged release and re-hashes it; the pinned public 0.53.20 source is used only if that repair still fails. Missing plugins prefer the exact public 0.53.20 source and use the verified packaged adapter only as an offline fallback. Non-src plugin state/cache survives repair.


## Included capabilities

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
