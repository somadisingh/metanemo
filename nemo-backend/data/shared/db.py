"""
Database connection factory for PostGIS.
Provides connection pooling and transaction management.
"""

import os
import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Connection pool (initialized lazily)
_connection_pool: pool.ThreadedConnectionPool | None = None


def get_connection_params() -> dict:
    """Get database connection parameters from environment variables."""
    return {
        'host': os.environ.get('POSTGRES_HOST', 'localhost'),
        'port': int(os.environ.get('POSTGRES_PORT', '5432')),
        'database': os.environ.get('POSTGRES_DB', 'metaglass'),
        'user': os.environ.get('POSTGRES_USER', 'metaglass'),
        'password': os.environ.get('POSTGRES_PASSWORD', ''),
    }


def init_pool(minconn: int = 2, maxconn: int = 10) -> pool.ThreadedConnectionPool:
    """
    Initialize the connection pool.
    
    Args:
        minconn: Minimum number of connections to maintain
        maxconn: Maximum number of connections allowed
        
    Returns:
        ThreadedConnectionPool instance
    """
    global _connection_pool
    
    if _connection_pool is None:
        params = get_connection_params()
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=minconn,
            maxconn=maxconn,
            **params
        )
        logger.info(f"Database pool initialized: {params['host']}:{params['port']}/{params['database']}")
    
    return _connection_pool


def get_pool() -> pool.ThreadedConnectionPool:
    """Get the connection pool, initializing if necessary."""
    if _connection_pool is None:
        return init_pool()
    return _connection_pool


@contextmanager
def get_connection(autocommit: bool = False) -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager for database connections.
    
    Args:
        autocommit: If True, each statement is committed immediately
        
    Yields:
        Database connection from the pool
        
    Example:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants LIMIT 10")
    """
    conn = None
    try:
        conn = get_pool().getconn()
        conn.autocommit = autocommit
        yield conn
        if not autocommit:
            conn.commit()
    except Exception as e:
        if conn and not autocommit:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            get_pool().putconn(conn)


@contextmanager
def get_cursor(dict_cursor: bool = False) -> Generator[psycopg2.extensions.cursor, None, None]:
    """
    Context manager for database cursors with automatic connection handling.
    
    Args:
        dict_cursor: If True, returns rows as dictionaries
        
    Yields:
        Database cursor
        
    Example:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT * FROM restaurants WHERE grade = 'C'")
            for row in cur:
                print(row['dba'], row['score'])
    """
    cursor_factory = RealDictCursor if dict_cursor else None
    
    with get_connection() as conn:
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur


def close_pool() -> None:
    """Close all connections in the pool."""
    global _connection_pool
    
    if _connection_pool is not None:
        _connection_pool.closeall()
        _connection_pool = None
        logger.info("Database pool closed")


def test_connection() -> bool:
    """
    Test database connectivity.
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return False
