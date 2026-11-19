"""
PostgreSQL connection pools for the agent.

Two pools:
  * audit_pool      - our own reads/writes on the `audit` schema
  * checkpoint_pool - used by the LangGraph PostgresSaver; its search_path
                      points at the `langgraph` schema so the checkpointer
                      creates its tables there.

The agent NEVER queries the `erp` schema - ERP data comes from mock-erp over HTTP.
"""

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.settings import postgres_conninfo

audit_pool = ConnectionPool(
    conninfo=postgres_conninfo(),
    min_size=1,
    max_size=8,
    open=False,
    kwargs={"row_factory": dict_row},
)

checkpoint_pool = ConnectionPool(
    conninfo=postgres_conninfo(),
    min_size=1,
    max_size=5,
    open=False,
    kwargs={
        "autocommit": True,              # required by PostgresSaver
        "prepare_threshold": 0,          # required by PostgresSaver
        "row_factory": dict_row,
        "options": "-c search_path=langgraph",
    },
)


def open_pools() -> None:
    """Open both pools. Raises if PostgreSQL is not reachable."""
    audit_pool.open(wait=True, timeout=30)
    checkpoint_pool.open(wait=True, timeout=30)


def close_pools() -> None:
    audit_pool.close()
    checkpoint_pool.close()


@contextmanager
def get_connection():
    """Borrow a connection; commits on success, rolls back on error."""
    with audit_pool.connection() as conn:
        yield conn
