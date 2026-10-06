# Norm 0.53.16 portable source package

This directory is the exact source payload intended for **Norm 0.53.16 / Installer 1.6.7-unified**.

The portable source package excludes generated or machine-private material such as .venv, compiled core\norm.exe, build output, Python caches, secrets, and private runtime state.

## Included capabilities

- N1/N2 tool gating and loop supervision.
- Durable PostgreSQL task/memory/history state.
- Redis prompt queues and hot validation pool.
- Recent-only manual/background memory condensation.
- Alternating scheduled regular/full maintenance on a configurable interval.
- Manual /memory-condense -deep for bounded older-history compaction/validation without hierarchical merging.
- Full-history condensation with configurable row-batch/sample sizing and conservative hierarchical merging of neighboring compact rows.
- Canonical internal state_root and capability-specific file-access overrides.
- Package-local 7-Zip archive support.
- First-party PDF vision parsing.
- Bundled WeasyPrint 70.0 / Pango 1.58.2 Windows runtime.
- Managed dynamic plugins and shared PostgreSQL pool.
- Operator consoles and emergency shutdown coordination.

## Current memory tuning

config\runtime.json defaults:

    scheduled_memory_interval_days = 7
    regular_memory_window_days = 14
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

Installer 1.6.7 binds the portable-source ZIP by exact filename and SHA-256, migrates/preserves machine configuration/state, validates bundled WeasyPrint/Pango with a real render, and recreates/reuses generated environment/build output as appropriate.
