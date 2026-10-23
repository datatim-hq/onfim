"""Postgres connection, migration runner and startup checks (D-4, P10)."""
from __future__ import annotations

import contextlib
import logging
import os
import re
from contextvars import ContextVar
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("mem.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


# Protection item 6: namespace scope of the current request ('*' means all, for sleep and service jobs).
ns_scope: ContextVar[str] = ContextVar("mem_ns_scope", default="")
APP_ROLE = os.environ.get("MEM_DB_APP_ROLE", "mem_app")


@contextlib.contextmanager
def scope(value: str):
    token = ns_scope.set(value)
    try:
        yield
    finally:
        ns_scope.reset(token)


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)
    if APP_ROLE:                      # the service runs under a role that does not bypass RLS (created by migration 0004)
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (APP_ROLE,)).fetchone():
            conn.execute(f'SET ROLE "{APP_ROLE}"')
    conn.commit()


class ScopedPool(ConnectionPool):
    """Every pooled connection gets the namespace scope of the current request (mem.ns), used by the RLS policies of 0004."""

    @contextlib.contextmanager
    def connection(self, *a, **kw):
        with super().connection(*a, **kw) as conn:
            conn.execute("SELECT set_config('mem.ns', %s, false)", (ns_scope.get(),))
            conn.commit()
            yield conn


def make_pool(url: str, min_size: int = 1, max_size: int = 8) -> ConnectionPool:
    pool = ScopedPool(
        url, min_size=min_size, max_size=max_size, open=True,
        kwargs={"row_factory": dict_row, "autocommit": False}, configure=_configure,
    )
    pool.wait(timeout=60)
    return pool


def migration_files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if re.match(r"^\d{4}_", p.name))


def migrate(url: str, embed_dim: int, model_id: str) -> list[str]:
    """Applies the missing migrations. Returns the list applied now."""
    applied_now: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(424242)")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
            for f in migration_files():
                if f.name in done:
                    continue
                sql = f.read_text(encoding="utf-8").replace("{{EMBED_DIM}}", str(embed_dim))
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
                    if f.name.startswith("0001_"):
                        conn.execute("INSERT INTO meta(key, value) VALUES ('embed_dim', %s), ('model_id', %s)",
                                     (str(embed_dim), model_id))
                applied_now.append(f.name)
                log.info("migration applied: %s", f.name)
            if APP_ROLE and conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (APP_ROLE,)).fetchone():
                # the service role gets rights on all tables, new ones included (no TRUNCATE: it bypasses RLS)
                conn.execute(f'GRANT USAGE ON SCHEMA public TO "{APP_ROLE}"')
                conn.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"')
                conn.execute(f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "{APP_ROLE}"')
                conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{APP_ROLE}"')
        finally:
            conn.execute("SELECT pg_advisory_unlock(424242)")
    return applied_now


def check_schema(url: str, embed_dim: int, model_id: str) -> None:
    """The service refuses to start if the schema does not match the code (a lesson from the previous memory service)
    or the vector dimension does not match the model (D-4)."""
    with psycopg.connect(url) as conn:
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        missing = [f.name for f in migration_files() if f.name not in done]
        if missing:
            raise SystemExit(f"migrations not applied: {missing}")
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if int(meta.get("embed_dim", -1)) != embed_dim:
            raise SystemExit(
                f"vector dimension in the database {meta.get('embed_dim')} != model {model_id} ({embed_dim}). "
                "The model can change only through a migration (04)."
            )
        if meta.get("model_id") != model_id:
            raise SystemExit(f"the database holds vectors of model {meta.get('model_id')}, but the service runs {model_id}")
        col = conn.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'memories'::regclass AND attname = 'embedding'"
        ).fetchone()[0]
        if col != f"vector({embed_dim})":
            raise SystemExit(f"column embedding: {col}, expected vector({embed_dim})")
