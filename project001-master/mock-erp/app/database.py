"""
Database access for the mock ERP service.

This service is the ONLY one allowed to read the `erp` schema.
Everyone else (agent, UI) must go through the HTTP API.
"""

import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def build_conninfo() -> str:
    """Build the connection string from environment variables (never hard-coded)."""
    return (
        f"host={os.environ['POSTGRES_HOST']} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"dbname={os.environ['POSTGRES_DB']} "
        f"user={os.environ['POSTGRES_USER']} "
        f"password={os.environ['POSTGRES_PASSWORD']}"
    )


# One small pool for the whole service. `open=False` so we can open it on startup
# and fail loudly if the database is not reachable.
pool = ConnectionPool(
    conninfo=build_conninfo(),
    min_size=1,
    max_size=5,
    open=False,
    kwargs={"row_factory": dict_row},
)


def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()
