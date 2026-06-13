"""Add user_id to alert_engagements and interest_weights for per-user isolation.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-12
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add user_id column and restructure primary keys for per-user data isolation."""
    # -- alert_engagements: add user_id column --
    op.execute("ALTER TABLE alert_engagements DROP CONSTRAINT IF EXISTS alert_engagements_pkey")
    op.execute("ALTER TABLE alert_engagements ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT '__global__'")
    op.execute("ALTER TABLE alert_engagements ADD PRIMARY KEY (user_id, alert_type)")

    # -- interest_weights: add user_id column --
    op.execute("ALTER TABLE interest_weights DROP CONSTRAINT IF EXISTS interest_weights_pkey")
    op.execute("ALTER TABLE interest_weights ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT '__global__'")
    op.execute("ALTER TABLE interest_weights ADD PRIMARY KEY (user_id, topic)")


def downgrade() -> None:
    """Remove user_id columns and revert to single-row-per-type schema."""
    # Remove rows that aren't __global__ to avoid PK conflicts
    op.execute("DELETE FROM alert_engagements WHERE user_id != '__global__'")
    op.execute("ALTER TABLE alert_engagements DROP CONSTRAINT IF EXISTS alert_engagements_pkey")
    op.execute("ALTER TABLE alert_engagements DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE alert_engagements ADD PRIMARY KEY (alert_type)")

    op.execute("DELETE FROM interest_weights WHERE user_id != '__global__'")
    op.execute("ALTER TABLE interest_weights DROP CONSTRAINT IF EXISTS interest_weights_pkey")
    op.execute("ALTER TABLE interest_weights DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE interest_weights ADD PRIMARY KEY (topic)")
