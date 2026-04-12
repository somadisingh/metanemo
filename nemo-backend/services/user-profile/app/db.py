"""Database connection pool for the user-profile service."""
import os
import sys
import time
from typing import Optional
import psycopg2
from psycopg2 import pool
import structlog

log = structlog.get_logger()

_pool: Optional[pool.SimpleConnectionPool] = None
BEHAVIOR_RETENTION_DAYS: int = 90


def init_pool() -> None:
    """Initialize the psycopg2 connection pool from DATABASE_URL env var.

    Retries 3 times with 2-second backoff. Exits with code 1 if DATABASE_URL
    is missing or all connection attempts fail.
    """
    global _pool, BEHAVIOR_RETENTION_DAYS

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("missing_env_var", variable="DATABASE_URL")
        sys.exit(1)

    retention_str = os.environ.get("BEHAVIOR_RETENTION_DAYS")
    if retention_str is None:
        log.warning("missing_env_var_using_default", variable="BEHAVIOR_RETENTION_DAYS", default=90)
        BEHAVIOR_RETENTION_DAYS = 90
    else:
        BEHAVIOR_RETENTION_DAYS = int(retention_str)

    for attempt in range(1, 4):
        try:
            _pool = pool.SimpleConnectionPool(1, 10, dsn=db_url)
            log.info("db_pool_initialized", attempt=attempt)
            return
        except psycopg2.Error as exc:
            log.error("db_connection_failed", attempt=attempt, error=str(exc))
            if attempt < 3:
                time.sleep(2)

    log.error("db_connection_exhausted")
    sys.exit(1)


def get_conn() -> psycopg2.extensions.connection:
    """Get a connection from the pool.

    Returns:
        A psycopg2 connection object.
    """
    assert _pool is not None, "Pool not initialized"
    return _pool.getconn()


def put_conn(conn: psycopg2.extensions.connection) -> None:
    """Return a connection to the pool.

    Args:
        conn: The psycopg2 connection to return.
    """
    assert _pool is not None, "Pool not initialized"
    _pool.putconn(conn)


def ping(conn: psycopg2.extensions.connection) -> bool:
    """Check if the database connection is alive.

    Args:
        conn: The psycopg2 connection to test.

    Returns:
        True if the connection is healthy, False otherwise.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except psycopg2.Error:
        return False


def close_pool() -> None:
    """Close all connections in the pool."""
    if _pool is not None:
        _pool.closeall()
        log.info("db_pool_closed")
