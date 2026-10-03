# Norm 0.53.11 maintenance source checkpoint ? 2026-10-02

This archive is the tested source staging tree only. It does not represent a live promotion.

Base source:
- maintenance commit c063d9839b9ee8a1665ddf60d71c6ebfca354639
- includes the earlier advisory validation and no-op recovery fixes

Additional staged work:
- first-party postgres_pool plugin/tool
- one process-global bounded psycopg_pool ConnectionPool object per approved connection name
- approved connection names: norm and stocks
- default pool limits: min 2, max 8, checkout timeout 30s, max waiting 64
- core/runtime PostgreSQL callers route through the pool instead of psycopg.connect()
- postgres credentials are resolved inside pool initialization instead of being exposed as ordinary tool parameters
- psycopg-pool 3.3.3 added to managed requirements
- plugin count is now 9; native tool count is 42
- docs/plugin index updated for postgres_pool
- validation integration test explicitly closes the pool

Verification:
- 17 maintenance regression tests pass
- isolated real Redis/PostgreSQL validation-service test passes and cleans its test namespace/schema
- all Python source compiles
- all 9 first-party plugin identities verify
- no direct psycopg.connect( call remains in core/tools/plugins
- explicit saturation test held 8 norm connections and confirmed the 9th checkout timed out while pool_size stayed 8

Preserved behavior:
- compact recovery handoff remains unchanged
- advisory 24-hour validation/generation behavior remains intact
- no-op recovery units remain accepted when evidence supports no edit
- deterministic read-after-write verification remains enforced for actual writes

Not performed:
- no live C:\Norm promotion
- no executable rebuild
- no installer payload rebuild
- no GitHub push

Use this archive as the source checkpoint for the next build/update step.
