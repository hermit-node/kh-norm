# Release

## Norm 0.53.11 / Unified Installer 1.6.5

- Added the first-party bounded PostgreSQL connection pool (`psycopg-pool==3.3.3`) and routed core/runtime PostgreSQL callers through it.
- Added one shared semantic verification pool across tasks/tools: Redis owns live rolling-24h and generation counts; PostgreSQL stores durable checkpoints/history.
- Changed-value verification starts a new generation at count 1; same-value verification increments the current generation.
- Refreshes the shared Redis verification checklist before each model/tool-decision round so repeated checks can be avoided immediately.
- Durable validation snapshots/checkpoints run on a 12-hour interval while Redis remains the hot-path authority.
- Hardened suppression/resume so suppressed pending work is not resurrected and resume restores a captured job exactly once.
- Added UTC-aware validation timestamp normalization.
- Installer migration now preserves customized installed values, uses the old installed public `norm-imprint.json` as the old-default baseline, and uses the new package public imprint as the new default. If no old public imprint exists, the old default is absent.
- Legacy PostgreSQL routing fields can be recovered from the external `.env`; passwords remain external and are not packaged.
- Public source carries only generic defaults; `norm-imprint.local.json` stays private/Git-ignored.
- Standardized the nine built-in plugins on schema 2: root `plugin.json` + README with executable code under `src/`, normally injected from `src/main.py`.
- Each schema-2 plugin records one deterministic SHA-256 of its complete `src/` tree. A matching SHA is the same code build; metadata-only edits update the generated registry without reloading unchanged code.
- `plugins/.registry.json` is generated from the plugin folders actually present at startup, refreshed for hot swaps, ignored by Git, and excluded from source packages.
- Installer manages each of the nine built-ins independently while leaving unrelated user plugin folders untouched.
- Rebuilt and validated Windows Installer 1.6.5 against the 0.53.11 source package.
