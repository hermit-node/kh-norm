# PostgreSQL Pool

postgres_pool is Norm's single PostgreSQL connection tool.

It owns one process-global bounded psycopg_pool.ConnectionPool per approved logical connection name:

- norm - Norm's durable runtime/conversation database.
- stocks - the configured stocks database.

Norm does not pass hosts, ports, users, passwords, or arbitrary database names to the tool. Those values are resolved once from the normal Norm settings/secrets during pool initialization. The pool is pinned for the process lifetime; configuration changes require a Norm restart.

The public tool exposes only status, health, and connections. Runtime code borrows connections from the same global pool object. No other runtime module should call psycopg.connect() directly.

Default limits are configured in [postgres]: minimum 2 and maximum 8 connections per named pool, 30-second checkout timeout, and at most 64 waiting borrowers. When the pool is full, callers wait for a returned connection instead of creating an unbounded socket.

plugin.json schema 2 records the plugin metadata and SHA-256 of the complete src/ tree; Norm recalculates that source hash before loading.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
