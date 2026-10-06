# PostgreSQL Pool

postgres_pool is Norm's controlled model-facing interface to the PostgreSQL pools already owned by the runtime.

The runtime owns one canonical process-global bounded psycopg_pool.ConnectionPool per approved logical connection name:

- norm - Norm's durable runtime/conversation database.
- stocks - the configured stocks database.

The plugin does not create a second pool and never accepts host, port, user, password, or arbitrary database-name parameters. It resolves the canonical pool through runtime_bootstrap.build_postgres_pool(), so plugin calls and core/runtime callers borrow from the same process-global pool object.

Public tools:

- postgres_pool(action, connection) - status, health, and approved connection names.
- postgres_query(query, connection, params, max_rows, timeout_seconds) - one bounded read-only SQL query through the shared pool.
- postgres_execute(action, table, values, where, returning, max_rows, timeout_seconds) - structured INSERT/UPDATE/DELETE against Norm's configured runtime schema only, always through the shared norm connection.

postgres_query forces the PostgreSQL transaction to READ ONLY, caps output to 1-1000 rows, caps statement timeouts to 1-120 seconds, and only accepts logical connection names enforced by the canonical pool.

postgres_execute is intentionally narrower than raw SQL: it has no connection or schema parameter, rejects qualified table names, allows only INSERT/UPDATE/DELETE, and requires a non-empty filter for UPDATE/DELETE. This lets Norm maintain its own runtime tables without exposing arbitrary PostgreSQL writes.

Runtime code also borrows connections from the same pool. External utilities such as pg_dump may resolve the approved connection parameters without opening a child-process pool. No runtime module or plugin should call psycopg.connect() directly.

Default limits are configured in [postgres]: minimum 2 and maximum 8 connections per named pool, 30-second checkout timeout, and at most 64 waiting borrowers. When the pool is full, callers wait for a returned connection instead of creating an unbounded socket.

plugin.json schema 2 records the plugin metadata and SHA-256 of the complete src/ tree; Norm recalculates that source hash before loading.
