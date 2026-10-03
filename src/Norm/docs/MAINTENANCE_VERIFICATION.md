# Maintenance source verification — 2026-10-02

Baseline: runtime `9b6bb13`, plus the preserved optional-entrypoint change. Norm remains 0.53.11; Installer remains 1.6.5-unified.

## Automated evidence

- `python tools/test_maintenance.py`: 17 regression tests covering recovery no-op/root-artifact contracts, read-after-last-write ordering, missing/mismatched hashes, plugin mutation evidence, context budgeting, unknown durable recent counts, all nine identities, corrupt/import-failing candidate retention, identity removal, cold invalid plugins, unsafe checksum paths, missing coverage, and legacy plugin support.
- `python tools/test_validation_services.py --services-root C:/Norm`: actual Redis/PostgreSQL tests in unique test-only namespaces. Covers rolling-window expiry, unchanged/changed generations, unrelated-task sharing, 40 concurrent increments, history, stale-snapshot rejection, expected snapshot lag and due checkpoints. Also checks completed-generation retention through a PostgreSQL history outage and successful retry. Verifies Redis and PostgreSQL cleanup. The PostgreSQL pool gate also proves an eight-connection cap: with all eight checked out, the ninth checkout times out instead of opening another connection.
- Source tests do not certify frozen executable behavior, GUI/installer interaction, live deployment or remote publication.

## Preservation and remaining work

Compact recovery handoff implementation is unchanged from the baseline. Original installer/public Git state is preserved; no installer version change. Current source status and operator semantics are in [CURRENT_STATUS.md](CURRENT_STATUS.md) and [README.md](README.md). Deferred items are in [FUTURE_IMPLEMENTATION_NOTES.md](FUTURE_IMPLEMENTATION_NOTES.md).

## Additional checks and limits

All nine first-party plugins hydrate successfully (42 native tools) with the installed runtime dependencies, and the diagnostic scan verifies all identities. All 91 Python files compile. AST comparison confirms the three handoff note/persistence/locator methods are unchanged. Git whitespace checking passes.

The optional Akinator strict documentation checker reports three path findings: generated runtime paths (such as the built executable and SOS note) and historical paths in DEVELOPMENT_NOTES are not files in this source-only checkout. Those references were retained as operational/history documentation; this checker is not reported as passing. A first hydration attempt with system Python lacked Pillow; the installed runtime interpreter provides it and all nine hydrate there. Initial temporary-directory permission and snapshot-timezone failures were corrected before the passing test run.
