from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

_ALLOWED_CONNECTIONS = ("norm", "stocks")


class PostgresPool:
    """Process-global bounded PostgreSQL pools for Norm's approved connections."""

    _lock = RLock()
    _pools: dict[str, ConnectionPool] = {}
    _conninfo: dict[str, str] = {}
    _config_key: tuple | None = None
    _settings: dict[str, int | float] = {}

    @classmethod
    def _name(cls, name: str) -> str:
        value = str(name or "").strip().lower()
        if value not in _ALLOWED_CONNECTIONS:
            raise ValueError(f"unsupported PostgreSQL connection: {value!r}")
        return value

    @classmethod
    def configured(cls) -> bool:
        with cls._lock:
            return bool(cls._pools)

    @classmethod
    def configure_from_runtime(cls, runtime_root: str | Path) -> dict:
        from norm_runtime.settings import (
            load_network_settings,
            load_ports,
            load_postgres_settings,
            load_secrets,
            load_settings,
            resolve_network_host,
        )

        root = Path(runtime_root).expanduser().resolve()
        parser = load_settings(root)
        network = load_network_settings(root)
        ports = load_ports(root)
        postgres = load_postgres_settings(root)
        secrets = load_secrets(root)
        password = str(secrets.get("NORM_POSTGRES_PASSWORD") or "").strip()
        if not password:
            raise ValueError("NORM_POSTGRES_PASSWORD is required")

        host = resolve_network_host(network, "postgres_host")
        user = str(postgres["user"])
        min_size = max(1, parser.getint("postgres", "pool_min_size", fallback=2))
        max_size = max(min_size, parser.getint("postgres", "pool_max_size", fallback=8))
        timeout = max(1.0, parser.getfloat("postgres", "pool_timeout_seconds", fallback=30.0))
        max_waiting = max(1, parser.getint("postgres", "pool_max_waiting", fallback=64))
        max_idle = max(30.0, parser.getfloat("postgres", "pool_max_idle_seconds", fallback=600.0))
        max_lifetime = max(max_idle, parser.getfloat("postgres", "pool_max_lifetime_seconds", fallback=3600.0))

        specs = {
            "norm": str(postgres["database"]),
            "stocks": str(postgres["stocks_database"]),
        }
        conninfo = {
            name: make_conninfo(
                host=host,
                port=int(ports["postgres"]),
                dbname=database,
                user=user,
                password=password,
                connect_timeout=5,
                application_name=f"norm-{name}",
            )
            for name, database in specs.items()
        }
        config_key = (
            root,
            tuple(sorted(conninfo.items())),
            min_size,
            max_size,
            timeout,
            max_waiting,
            max_idle,
            max_lifetime,
        )

        with cls._lock:
            if cls._pools:
                if cls._config_key != config_key:
                    raise RuntimeError(
                        "PostgreSQL pool configuration is pinned for this process; restart Norm to apply changes"
                    )
                return cls.status()

            opened: dict[str, ConnectionPool] = {}
            try:
                for name in _ALLOWED_CONNECTIONS:
                    pool = ConnectionPool(
                        conninfo[name],
                        min_size=min_size,
                        max_size=max_size,
                        open=False,
                        timeout=timeout,
                        max_waiting=max_waiting,
                        max_idle=max_idle,
                        max_lifetime=max_lifetime,
                        check=ConnectionPool.check_connection,
                        name=f"norm-{name}",
                    )
                    pool.open(wait=True, timeout=timeout)
                    opened[name] = pool
            except Exception:
                for pool in opened.values():
                    pool.close(timeout=5.0)
                raise

            cls._pools = opened
            cls._conninfo = conninfo
            cls._config_key = config_key
            cls._settings = {
                "min_size": min_size,
                "max_size": max_size,
                "timeout_seconds": timeout,
                "max_waiting": max_waiting,
                "max_idle_seconds": max_idle,
                "max_lifetime_seconds": max_lifetime,
            }
            return cls.status()

    @classmethod
    @contextmanager
    def connection(cls, name: str = "norm", *, timeout: float | None = None) -> Iterator[psycopg.Connection]:
        key = cls._name(name)
        with cls._lock:
            pool = cls._pools.get(key)
            default_timeout = float(cls._settings.get("timeout_seconds", 30.0))
        if pool is None:
            raise RuntimeError("PostgreSQL pool is not configured")
        with pool.connection(timeout=default_timeout if timeout is None else float(timeout)) as conn:
            yield conn

    @classmethod
    def dsn(cls, name: str = "norm") -> str:
        key = cls._name(name)
        with cls._lock:
            value = cls._conninfo.get(key)
        if not value:
            raise RuntimeError("PostgreSQL pool is not configured")
        return value

    @classmethod
    def connection_parameters(cls, name: str = "norm", *, include_password: bool = False) -> dict[str, str]:
        parts = {str(k): str(v) for k, v in conninfo_to_dict(cls.dsn(name)).items() if v is not None}
        if not include_password:
            parts.pop("password", None)
        return parts

    @classmethod
    def health(cls, name: str = "norm") -> dict:
        key = cls._name(name)
        started = time.perf_counter()
        with cls.connection(key) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            value = cur.fetchone()
        return {
            "connection": key,
            "responsive": bool(value and int(value[0]) == 1),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    @classmethod
    def status(cls, name: str | None = None) -> dict:
        with cls._lock:
            configured = bool(cls._pools)
            settings = dict(cls._settings)
            names = [cls._name(name)] if name else list(_ALLOWED_CONNECTIONS)
            pools = {key: cls._pools.get(key) for key in names}
        return {
            "configured": configured,
            "allowed_connections": list(_ALLOWED_CONNECTIONS),
            "limits": settings,
            "connections": {
                key: ({"configured": False} if pool is None else {"configured": True, **pool.get_stats()})
                for key, pool in pools.items()
            },
        }

    @classmethod
    def close(cls) -> None:
        with cls._lock:
            pools = list(cls._pools.values())
            cls._pools = {}
            cls._conninfo = {}
            cls._config_key = None
            cls._settings = {}
        for pool in pools:
            pool.close(timeout=5.0)
