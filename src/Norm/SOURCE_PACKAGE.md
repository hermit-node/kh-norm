# Norm 0.53.19 portable source package

This directory is the exact source payload intended for **Norm 0.53.19 / Installer 1.6.8-unified**.

The portable source package excludes generated or machine-private material such as .venv, compiled core\norm.exe, build output, Python caches, secrets, and private runtime state.

## Included capabilities

- N1/N2 tool gating and loop supervision.
- Session-only transactional /switch-model discovery/switching through the live Ollama API; every restart boots norm.
- Single-source operator help from docs\help_menu.txt, loaded on demand by both prompt frontends.
- Public web/news search plus bounded readable page extraction with public-network-only SSRF controls, direct publisher fetch, and optional verified reader fallback.
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

Installer 1.6.8 binds the portable-source ZIP by exact filename and SHA-256, migrates/preserves machine configuration/state, preserves a valid native WeasyPrint runtime or fetches/repairs it from the pinned official archive, validates its SHA-256 and real PDF render, and recreates/reuses generated environment/build output as appropriate.
