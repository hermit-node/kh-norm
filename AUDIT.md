# Public Release Audit — Norm 0.53.11 / Installer 1.6.5

Audit date: 2026-10-02

## Scope

Reviewed the public installer source, generated portable-source ZIP, runtime source tree, public/private imprint migration, PostgreSQL pool, shared validation pool, suppression/resume behavior, plugin hot-swap identity model, and public/private configuration boundary.

## Release findings

- Public source uses topology-neutral defaults; machine-specific network-map, storage roots, hostnames, and local imprint values are excluded.
- `norm-imprint.json` inside the package is the public/default baseline used for upgrade comparisons.
- `norm-imprint.local.json` remains private and Git-ignored; `.env` and secrets are never packaged.
- Legacy PostgreSQL routing may be read from the external `.env`, while the password remains external.
- Runtime PostgreSQL access is routed through the bounded first-party `postgres_pool`; direct runtime `psycopg.connect()` calls are rejected by maintenance tests.
- Redis is the live shared semantic verification pool; PostgreSQL is the durable 12-hour checkpoint/history layer.
- Changed verification values reset the generation count to 1; same values increment it.
- Suppressed pending work is not resurrected at startup; resume restores captured queue work once.
- The nine built-in plugins use schema 2: root `plugin.json` + README, code under `src/`, declared `src/main.py` injection point, and one deterministic SHA-256 of the complete `src/` tree.
- `plugins/.registry.json` is generated from the plugins actually installed, Git-ignored, excluded from source media, and is not package authority.
- Matching plugin source SHA is treated as the same code build; metadata-only edits update generated registry metadata without reloading unchanged functions.
- Installer updates each known built-in plugin independently while preserving unrelated user plugin folders.

## Public release gate

- Source-tree public-release guard: PASS (127 files)
- Source-ZIP public-release guard: PASS (127 files)
- Installer regression suite: PASS
- Plugin maintenance/runtime suite: PASS (22 tests)
- Nine built-ins hydrate with zero errors / 42 native tools
- Real Redis/PostgreSQL validation integration: PASS
- Real Redis suppression/resume integration: PASS
- Network-map policy suite: PASS
- Public topology/credential scan: PASS
- Ingress reconciliation regression: PASS
- Frozen Installer 1.6.5 self-test and source validation: PASS
