"""Nightly cleanup job for the user-profile service."""
import structlog
from psycopg2 import pool as pg_pool

log = structlog.get_logger()


def nightly_cleanup(conn_pool: pg_pool.SimpleConnectionPool, retention_days: int) -> None:
    """Delete behavioral data older than retention_days from place_visits and neighborhood_visits.

    Args:
        conn_pool: The psycopg2 connection pool.
        retention_days: Number of days to retain data.
    """
    conn = conn_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM place_visits WHERE visited_at < NOW() - INTERVAL '%s days'",
                (retention_days,)
            )
            place_deleted = cur.rowcount
            cur.execute(
                "DELETE FROM neighborhood_visits WHERE visited_date < CURRENT_DATE - INTERVAL '%s days'",
                (retention_days,)
            )
            neighborhood_deleted = cur.rowcount
        conn.commit()
        log.info("nightly_cleanup_complete",
                 place_visits_deleted=place_deleted,
                 neighborhood_visits_deleted=neighborhood_deleted,
                 retention_days=retention_days)
    except Exception as exc:
        conn.rollback()
        log.error("nightly_cleanup_failed", error=str(exc), exc_info=True)
    finally:
        conn_pool.putconn(conn)
