"""Create behavior tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-11
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all five behavior tables with indexes and seed data."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS place_visits (
            id          BIGSERIAL        PRIMARY KEY,
            place_id    TEXT             NOT NULL,
            visited_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
            latitude    DOUBLE PRECISION NOT NULL,
            longitude   DOUBLE PRECISION NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS place_visits_place_id_idx ON place_visits (place_id)")
    op.execute("CREATE INDEX IF NOT EXISTS place_visits_visited_at_idx ON place_visits (visited_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS transit_patterns (
            route_id     TEXT     NOT NULL,
            hour_of_day  SMALLINT NOT NULL CHECK (hour_of_day BETWEEN 0 AND 23),
            day_of_week  SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
            visit_count  INTEGER  NOT NULL DEFAULT 1,
            PRIMARY KEY (route_id, hour_of_day, day_of_week)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS alert_engagements (
            alert_type      TEXT    PRIMARY KEY,
            positive_count  INTEGER NOT NULL DEFAULT 0,
            negative_count  INTEGER NOT NULL DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS neighborhood_visits (
            h3_cell      TEXT NOT NULL,
            visited_date DATE NOT NULL,
            PRIMARY KEY (h3_cell, visited_date)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS neighborhood_visits_date_idx ON neighborhood_visits (visited_date)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS interest_weights (
            topic   TEXT             PRIMARY KEY,
            weight  DOUBLE PRECISION NOT NULL DEFAULT 0.5
        )
    """)

    op.execute("""
        INSERT INTO alert_engagements (alert_type, positive_count, negative_count)
        VALUES ('restaurant',0,0),('transit',0,0),('safety',0,0),('discovery',0,0)
        ON CONFLICT DO NOTHING
    """)

    op.execute("""
        INSERT INTO interest_weights (topic, weight)
        VALUES ('food',0.5),('transit',0.5),('safety',0.5)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    """Drop all behavior tables."""
    op.execute("DROP TABLE IF EXISTS interest_weights")
    op.execute("DROP TABLE IF EXISTS neighborhood_visits")
    op.execute("DROP TABLE IF EXISTS alert_engagements")
    op.execute("DROP TABLE IF EXISTS transit_patterns")
    op.execute("DROP TABLE IF EXISTS place_visits")
